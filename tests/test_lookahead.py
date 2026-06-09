"""Lookahead / leakage harness (Phase 0.2).

Proves the signal pipeline is causal: a signal emitted at bar open_time t must
not depend on any bar after t. We verify this by feeding each detector a series
truncated at t and asserting the set of signals at t is identical to the
full-series run.

Fixtures: the committed real-AAPL OHLCV parquets in tests/fixtures. The 4h frame
fires all 16 detectors; 1d fires 15 (orb needs the intraday session anchor);
1wk fires 14 (orb + ote_entry too rare).

These are characterization guards. Expected result: green (current code is
causal). A failure is a real leak — fix it and flag any golden move per §4.2.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from analytics.backtest.engine import run_backtest
from analytics.strategies._registry import DETECTOR_REGISTRY
from tests.conftest import _candle, _make_ohlcv

_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_MAX_TRUNC_POINTS = 25
_FLOAT_DECIMALS = 8

Detector = Callable[[pd.DataFrame], pd.DataFrame]


def _load_fixture(tf: str) -> pd.DataFrame:
    """Load a committed AAPL OHLCV fixture with a clean RangeIndex."""
    df = pd.read_parquet(_FIXTURE_DIR / f"aapl_{tf}.parquet")
    return df.reset_index(drop=True)


def _normalize_signals_at(
    sig: pd.DataFrame, t: int
) -> list[tuple[tuple[str, object], ...]]:
    """Order-independent, float-stable view of the signals whose open_time == t."""
    if sig.empty or "open_time" not in sig.columns:
        return []
    at = sig[sig["open_time"].astype("int64") == t]
    rows: list[tuple[tuple[str, object], ...]] = []
    for _, r in at.iterrows():
        cells: list[tuple[str, object]] = []
        for col in sorted(sig.columns):
            v = r[col]
            if isinstance(v, float):
                v = round(v, _FLOAT_DECIMALS)
            cells.append((col, v))
        rows.append(tuple(cells))
    return sorted(rows)


def _truncation_points(open_times: list[int]) -> list[int]:
    """Up to _MAX_TRUNC_POINTS evenly-spread points; always first + last."""
    uniq = sorted(set(open_times))
    if len(uniq) <= _MAX_TRUNC_POINTS:
        return uniq
    step = (len(uniq) - 1) / (_MAX_TRUNC_POINTS - 1)
    idxs = sorted({round(i * step) for i in range(_MAX_TRUNC_POINTS)})
    return [uniq[i] for i in idxs]


def _first_lookahead_violation(detector: Detector, df: pd.DataFrame) -> int | None:
    """Return the first open_time whose signals change under truncation, else None.

    None also means "no signals" (vacuously causal) — callers skip that case.
    """
    full = detector(df)
    if full.empty:
        return None
    for t in _truncation_points(full["open_time"].astype("int64").tolist()):
        trunc = df[df["open_time"] <= t].reset_index(drop=True)
        if _normalize_signals_at(detector(trunc), t) != _normalize_signals_at(full, t):
            return t
    return None


def test_harness_catches_injected_lookahead() -> None:
    """A detector that reads the NEXT bar must be flagged — proves the harness bites."""
    df = _load_fixture("1d")

    def peeking(d: pd.DataFrame) -> pd.DataFrame:
        # Emit a long signal at every bar i<n-1 with sl_price taken from the
        # FUTURE bar's close — a textbook lookahead.
        n = len(d)
        ot = d["open_time"].to_numpy()
        cl = d["close"].to_numpy(dtype=float)
        rows = [
            {
                "open_time": int(ot[i]),
                "direction": "long",
                "reason": "peek",
                "sl_price": float(cl[i + 1]),
            }
            for i in range(n - 1)
        ]
        return pd.DataFrame(
            rows, columns=["open_time", "direction", "reason", "sl_price"]
        )

    assert _first_lookahead_violation(peeking, df) is not None


# Detectors with a confirmed, tracked lookahead leak. xfail(strict) so the
# follow-up fix PR is forced to remove the entry when it makes the cell causal.
# Currently empty: the only known leak (`bos`, centered swing window) was fixed
# by shifting the signal open_time to the confirmation bar (row_idx +
# swing_lookback) — see docs/redesign/phase0-lookahead-audit.md.
_KNOWN_LOOKAHEAD_DETECTORS: set[str] = set()


@pytest.mark.parametrize("tf", ["4h", "1d", "1wk"])
@pytest.mark.parametrize("detector_name", sorted(DETECTOR_REGISTRY))
def test_detector_has_no_lookahead(
    detector_name: str, tf: str, request: pytest.FixtureRequest
) -> None:
    if detector_name in _KNOWN_LOOKAHEAD_DETECTORS:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=(
                    f"Phase 0.2 finding: {detector_name} reads future bars "
                    "(centered swing window); fix tracked in a follow-up PR — "
                    "see docs/redesign/phase0-lookahead-audit.md."
                ),
            )
        )
    detector = DETECTOR_REGISTRY[detector_name]
    df = _load_fixture(tf)
    if detector(df).empty:
        pytest.skip(f"{detector_name} emits no signals on the {tf} fixture")
    violation = _first_lookahead_violation(detector, df)
    assert violation is None, (
        f"{detector_name} on {tf}: signals at open_time={violation} differ between "
        f"the full-series and truncated-at-t runs — the detector reads future bars."
    )


# --- Backtest entry-path causality (Task 2) ---------------------------------

_T = 1_700_000_000_000


def _long_signal(open_time: int) -> pd.DataFrame:
    return pd.DataFrame(
        [{"open_time": open_time, "direction": "long", "reason": "test"}],
        columns=["open_time", "direction", "reason"],
    )


def test_backtest_entry_is_strictly_next_bar_open() -> None:
    """Entry fills at the NEXT bar's open — never the signal bar's close, never a future bar."""
    ohlcv = _make_ohlcv(
        [
            _candle(_T + 0, 100, 105, 95, 102),  # idx 0: signal candle (close=102)
            _candle(_T + 1, 100, 103, 99, 101),  # idx 1: entry candle (open=100)
            _candle(_T + 2, 101, 106, 99, 105),  # idx 2: resolves
        ]
    )
    res = run_backtest(
        ohlcv, _long_signal(_T + 0), "AAPL", "4h", "fvg", sl_pct=0.02, tp_r=2.0
    )
    assert len(res.trades) == 1
    tr = res.trades[0]
    assert tr.signal_time == _T + 0
    assert tr.entry_time == _T + 1  # strictly the next bar
    assert tr.entry_price == pytest.approx(
        100.0
    )  # that bar's OPEN, not the signal close (102)


def test_backtest_entry_independent_of_future_bars() -> None:
    """Truncating the series right after the entry bar leaves entry price/time unchanged."""
    candles = [
        _candle(_T + 0, 100, 105, 95, 102),  # signal
        _candle(_T + 1, 100, 103, 99, 101),  # entry (no SL/TP hit here)
        _candle(_T + 2, 101, 106, 99, 105),  # would resolve the trade in the full run
        _candle(_T + 3, 105, 130, 104, 129),  # large future move
    ]
    sig = _long_signal(_T + 0)
    full = run_backtest(
        _make_ohlcv(candles), sig, "AAPL", "4h", "fvg", sl_pct=0.02, tp_r=2.0
    )
    trunc = run_backtest(
        _make_ohlcv(candles[:2]), sig, "AAPL", "4h", "fvg", sl_pct=0.02, tp_r=2.0
    )
    assert len(full.trades) == 1 and len(trunc.trades) == 1
    assert trunc.trades[0].entry_time == full.trades[0].entry_time == _T + 1
    assert trunc.trades[0].entry_price == pytest.approx(full.trades[0].entry_price)
    assert trunc.trades[0].entry_price == pytest.approx(100.0)
