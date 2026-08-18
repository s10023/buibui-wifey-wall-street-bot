"""Unit tests for the regime gate backtest replay (`tools/regime_gate_replay.py`).

Covers the pure logic — time alignment, suppression labelling, aggregation —
so the verdict on the live DB run can be trusted.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.signal_config import BiasConfig
from tools.regime_gate_replay import (
    _regimes_at_entries,
    aggregate,
    annotate_regime_4h,
    annotate_suppression,
)

# An RTH equity 4h tape: two bars a day, stamped 13:30 and 17:30 UTC. NONE of
# these is a multiple of 4h from UTC midnight — 0 of 105,708 such bars in the
# live DB are — which is precisely what the old modulo floor assumed.
_MIDNIGHT_2026_01_01 = 1_767_225_600_000
_DAY_MS = 86_400_000
_RTH_OFFSETS_MS = (48_600_000, 63_000_000)  # 13:30, 17:30
_FOUR_HOURS_MS = 4 * 60 * 60 * 1000  # crypto tape, for the non-regression case


def _rth_open_times(n_bars: int) -> list[int]:
    out: list[int] = []
    day = 0
    while len(out) < n_bars:
        for off in _RTH_OFFSETS_MS:
            out.append(_MIDNIGHT_2026_01_01 + day * _DAY_MS + off)
        day += 1
    return out[:n_bars]


def _bias() -> BiasConfig:
    return BiasConfig(
        regime_enabled=True,
        regime_mode="hard",
        regime_htf_tf="4h",
        regime_enabled_regimes={
            "trend": ["trend"],
            "fib": ["trend"],
            "structural": ["trend", "range", "high_vol"],
        },
        regime_per_strategy={"bos": ["trend"]},
    )


class TestRegimesAtEntries:
    """The bar-alignment contract. Every case here is an RTH tape unless named
    otherwise, because the tape this repo actually reads is never UTC-aligned."""

    def _labels(self, n: int) -> pd.Series:
        return pd.Series([f"r{i}" for i in range(n)], dtype=object)

    def test_rth_entry_resolves_to_previous_closed_bar(self) -> None:
        opens = _rth_open_times(6)
        entry = opens[3] + 42 * 60_000  # 42 min into bar 3
        got = _regimes_at_entries(pd.Series([entry]), pd.Series(opens), self._labels(6))
        assert got.iloc[0] == "r2"  # bar 3 is in progress → last CLOSED is bar 2

    def test_rth_entry_does_NOT_fall_open(self) -> None:
        """Positive control for the defect this replaces.

        The modulo floor returned a key no RTH bar has, so every lookup missed
        and `fillna` produced "unknown" — a fall-open indistinguishable from a
        genuine cache miss. Asserting "not unknown" is the only assertion that
        fails on the old implementation, so it is the one that must exist.
        """
        opens = _rth_open_times(8)
        entries = pd.Series([o + 60_000 for o in opens[2:]])
        got = _regimes_at_entries(entries, pd.Series(opens), self._labels(8))
        assert (got != "unknown").all()

    def test_entry_at_exact_bar_open_uses_the_prior_bar(self) -> None:
        opens = _rth_open_times(5)
        got = _regimes_at_entries(
            pd.Series([opens[4]]), pd.Series(opens), self._labels(5)
        )
        assert got.iloc[0] == "r3"

    def test_entry_before_any_closed_bar_is_unknown(self) -> None:
        opens = _rth_open_times(4)
        got = _regimes_at_entries(
            pd.Series([opens[0] - 1]), pd.Series(opens), self._labels(4)
        )
        assert got.iloc[0] == "unknown"

    def test_empty_bar_series_is_unknown(self) -> None:
        got = _regimes_at_entries(
            pd.Series([_MIDNIGHT_2026_01_01]),
            pd.Series([], dtype="int64"),
            pd.Series([], dtype=object),
        )
        assert got.iloc[0] == "unknown"

    def test_utc_aligned_crypto_tape_still_resolves(self) -> None:
        """Non-regression for the 24/7 shape the old floor was written for."""
        opens = [i * _FOUR_HOURS_MS for i in range(6)]
        entry = opens[3] + 30 * 60_000
        got = _regimes_at_entries(pd.Series([entry]), pd.Series(opens), self._labels(6))
        assert got.iloc[0] == "r2"


class TestRegimeAnnotation:
    def _conn_with_rth_bars(self, n_bars: int) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT, timeframe TEXT, open_time BIGINT, "
            "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE)"
        )
        for i, ot in enumerate(_rth_open_times(n_bars)):
            close = 100.0 + i  # steady uptrend so the classifier commits a label
            conn.execute(
                "INSERT INTO ohlcv VALUES ('AAPL', '4h', ?, ?, ?, ?, ?, 1000)",
                [ot, close, close + 1.0, close - 1.0, close],
            )
        return conn

    def test_rth_trade_resolves_to_a_real_regime(self) -> None:
        """End-to-end positive control: the replay must SEE a regime.

        The predecessor asserted "unknown" on 5 bars — below the classifier's
        minimum — so it passed both before and after the bug, and the tool
        shipped reporting 0 suppressed of 2,849 live trades.
        """
        n = 400
        conn = self._conn_with_rth_bars(n)
        entry = _rth_open_times(n)[-1] + 60_000
        trades = pd.DataFrame(
            {
                "strategy": ["ema"],
                "symbol": ["AAPL"],
                "timeframe": ["4h"],
                "direction": ["long"],
                "entry_time": [entry],
                "pnl_r": [0.5],
            }
        )
        out = annotate_regime_4h(trades, conn)
        assert out["regime"].iloc[0] != "unknown"

    def test_missing_ohlcv_falls_open(self) -> None:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT, timeframe TEXT, open_time BIGINT, "
            "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE)"
        )
        trades = pd.DataFrame(
            {
                "strategy": ["ema"],
                "symbol": ["NOSUCH"],
                "timeframe": ["4h"],
                "direction": ["long"],
                "entry_time": [_MIDNIGHT_2026_01_01],
                "pnl_r": [0.5],
            }
        )
        out = annotate_regime_4h(trades, conn)
        assert out["regime"].iloc[0] == "unknown"


class TestSuppressionLabelling:
    def _row(self, strategy: str, regime: str) -> pd.DataFrame:
        return pd.DataFrame(
            {"strategy": [strategy], "regime": [regime], "pnl_r": [0.0]}
        )

    def test_continuation_in_range_is_suppressed(self) -> None:
        # ema (type=trend) → only allowed in trend → suppressed in range.
        out = annotate_suppression(self._row("ema", "range"), _bias().regime_allowed)
        assert out["suppressed"].iloc[0] is True or out["suppressed"].iloc[0] == True  # noqa: E712

    def test_reversion_in_range_is_kept(self) -> None:
        # fvg (type=structural) → enabled in range.
        out = annotate_suppression(self._row("fvg", "range"), _bias().regime_allowed)
        assert not out["suppressed"].iloc[0]

    def test_unknown_regime_falls_open(self) -> None:
        out = annotate_suppression(self._row("ema", "unknown"), _bias().regime_allowed)
        assert not out["suppressed"].iloc[0]

    def test_per_strategy_override_bos(self) -> None:
        # bos overridden to ["trend"] — suppressed in range despite type=structural.
        out = annotate_suppression(self._row("bos", "range"), _bias().regime_allowed)
        assert out["suppressed"].iloc[0]


class TestAggregate:
    def test_aggregate_groups_by_strategy_regime_suppressed(self) -> None:
        trades = pd.DataFrame(
            {
                "strategy": ["ema", "ema", "ema", "ema"],
                "regime": ["range", "range", "trend", "trend"],
                "suppressed": [True, True, False, False],
                "pnl_r": [-0.5, -0.3, 1.0, 0.5],
            }
        )
        agg = aggregate(trades)
        assert len(agg) == 2  # (ema, range, suppressed) + (ema, trend, kept)
        suppressed_row = agg[agg["suppressed"]].iloc[0]
        kept_row = agg[~agg["suppressed"]].iloc[0]
        assert suppressed_row["n"] == 2
        assert suppressed_row["avg_r"] == -0.4
        assert kept_row["n"] == 2
        assert kept_row["avg_r"] == 0.75
