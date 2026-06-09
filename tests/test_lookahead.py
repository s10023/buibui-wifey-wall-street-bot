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

from analytics.strategies._registry import DETECTOR_REGISTRY

_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_MAX_TRUNC_POINTS = 25
_FLOAT_DECIMALS = 8

Detector = Callable[[pd.DataFrame], pd.DataFrame]


def _load_fixture(tf: str) -> pd.DataFrame:
    """Load a committed AAPL OHLCV fixture with a clean RangeIndex."""
    df = pd.read_parquet(_FIXTURE_DIR / f"aapl_{tf}.parquet")
    return df.reset_index(drop=True)


def _normalize_signals_at(sig: pd.DataFrame, t: int) -> list[tuple[tuple[str, object], ...]]:
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
            {"open_time": int(ot[i]), "direction": "long",
             "reason": "peek", "sl_price": float(cl[i + 1])}
            for i in range(n - 1)
        ]
        return pd.DataFrame(rows, columns=["open_time", "direction", "reason", "sl_price"])

    assert _first_lookahead_violation(peeking, df) is not None


# Detectors with a confirmed, tracked lookahead leak. xfail(strict) so the
# follow-up fix PR is forced to remove the entry when it makes the cell causal.
_KNOWN_LOOKAHEAD_DETECTORS = {
    # bos stamps the signal at the swing bar, but the centered rolling window
    # (2*swing_lookback+1, center=True) confirms the swing using swing_lookback
    # FUTURE bars — so the emission decision at open_time t depends on bars after
    # t. Fix = shift the signal open_time to the confirmation bar
    # (i + swing_lookback). Deferred to a dedicated PR with full backtest
    # re-validation; see docs/redesign/phase0-lookahead-audit.md.
    "bos",
}


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
