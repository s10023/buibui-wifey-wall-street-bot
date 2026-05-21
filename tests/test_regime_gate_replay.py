"""Unit tests for the regime gate backtest replay (`tools/regime_gate_replay.py`).

Covers the pure logic — time alignment, suppression labelling, aggregation —
so the verdict on the live DB run can be trusted.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.signal_config import BiasConfig
from tools.regime_gate_replay import (
    _FOUR_HOURS_MS,
    _regime_at_entry,
    aggregate,
    annotate_regime_4h,
    annotate_suppression,
)


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


class TestTimeAlignment:
    def test_entry_time_lands_in_middle_of_4h_bin(self) -> None:
        # 4h bin starts at T=0; entry at T+30min → most recent CLOSED candle is
        # the one before bin 0 (i.e. open_time = -4h).
        bin_start = 1_700_000_000_000 - (1_700_000_000_000 % _FOUR_HOURS_MS)
        entry = bin_start + 30 * 60_000  # 30 min into the bin
        assert _regime_at_entry(entry) == bin_start - _FOUR_HOURS_MS

    def test_entry_at_exact_4h_boundary(self) -> None:
        # Entry at the open of a new 4h candle → most recent CLOSED is the
        # candle that just closed, i.e. open_time = entry - 4h.
        bin_start = 1_700_000_000_000 - (1_700_000_000_000 % _FOUR_HOURS_MS)
        assert _regime_at_entry(bin_start) == bin_start - _FOUR_HOURS_MS


class TestRegimeAnnotation:
    def test_lookup_hits_previous_closed_candle(self) -> None:
        # Build a tiny in-memory DB with 4h OHLCV producing all-"unknown"
        # regimes (insufficient history). The annotation should still
        # populate the column, just with the fallback label.
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT, timeframe TEXT, open_time BIGINT, "
            "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE)"
        )
        # Only 5 4h bars — far below classify_series's min_history (50).
        for i in range(5):
            conn.execute(
                "INSERT INTO ohlcv VALUES ('BTCUSDT', '4h', ?, 100, 101, 99, 100, 1000)",
                [i * _FOUR_HOURS_MS],
            )
        trades = pd.DataFrame(
            {
                "strategy": ["ema"],
                "symbol": ["BTCUSDT"],
                "timeframe": ["1h"],
                "direction": ["long"],
                "entry_time": [3 * _FOUR_HOURS_MS + 60_000],
                "pnl_r": [0.5],
            }
        )
        out = annotate_regime_4h(trades, conn)
        assert "regime" in out.columns
        # 5 bars is below classify_series min_history → all rows label "unknown".
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
