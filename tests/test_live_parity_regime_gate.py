"""Tests for T6 live-parity regime gate (PR-2).

Covers the engine helpers `_resolve_regime_at` and `_apply_regime_gate_to_signals`,
plus the wire-up inside `run_backtest()`. The gate must reuse live's
`_apply_regime_gate` verbatim — these tests verify the per-signal time resolution
and that the default-off path stays byte-identical to the baseline (regression
goldens contract).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from analytics.backtest.engine import (
    _apply_regime_gate_to_signals,
    _resolve_regime_at,
    run_backtest,
)
from analytics.backtest.live_parity_config import LiveParityConfig
from analytics.signal_config import BiasConfig

# ---------------------------------------------------------------------------
# _resolve_regime_at — last-closed semantics
# ---------------------------------------------------------------------------


class TestResolveRegimeAt:
    def test_empty_series_returns_none(self) -> None:
        series = pd.Series([], dtype="object")
        assert _resolve_regime_at(1_000, series) is None

    def test_single_candle_no_prior_closed_returns_none(self) -> None:
        # Only one HTF row → there is no candle "before the current one".
        series = pd.Series(["trend"], index=pd.Index([1_000], dtype="int64"))
        assert _resolve_regime_at(2_000, series) is None
        # Signal exactly at the only candle's open_time → still no prior.
        assert _resolve_regime_at(1_000, series) is None

    def test_returns_regime_of_candle_before_current(self) -> None:
        # Live uses iloc[-2]: the candle BEFORE the still-open one.
        # For backtest at signal_time T, find largest open_time <= T, then -1.
        series = pd.Series(
            ["range", "trend", "high_vol", "range"],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        # Signal at 3_500: current candle is the one at 3_000 (high_vol);
        # the candle BEFORE it is at 2_000 (trend).
        assert _resolve_regime_at(3_500, series) == "trend"
        # Signal exactly on a boundary: side='right' makes 3_000 the current,
        # so still trend.
        assert _resolve_regime_at(3_000, series) == "trend"
        # Signal at 4_500: current is 4_000 (range), before is 3_000 (high_vol).
        assert _resolve_regime_at(4_500, series) == "high_vol"

    def test_signal_before_series_returns_none(self) -> None:
        series = pd.Series(
            ["trend", "range"], index=pd.Index([2_000, 3_000], dtype="int64")
        )
        # Signal time 500 < first candle → no current, no prior.
        assert _resolve_regime_at(500, series) is None

    def test_nan_value_returned_as_none(self) -> None:
        series = pd.Series(
            [np.nan, "trend", "range"],
            index=pd.Index([1_000, 2_000, 3_000], dtype="int64"),
        )
        # Signal at 2_500 → current is 2_000, before is 1_000 (NaN).
        assert _resolve_regime_at(2_500, series) is None


# ---------------------------------------------------------------------------
# _apply_regime_gate_to_signals — group + reuse live gate
# ---------------------------------------------------------------------------


def _toy_signals(open_times: list[int], directions: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": open_times,
            "direction": directions,
            "reason": ["t"] * len(open_times),
            "sl_price": [99.0] * len(open_times),
            "context": ["c"] * len(open_times),
            "low_volume": [False] * len(open_times),
            "tp_price": [104.0] * len(open_times),
        }
    )


def _trend_only_bias() -> BiasConfig:
    """Strategy type 'continuation' allowed only in trend regime."""
    return BiasConfig(
        regime_enabled=True,
        regime_mode="hard",
        regime_htf_tf="4h",
        regime_enabled_regimes={"continuation": ["trend"]},
    )


class TestApplyRegimeGateToSignals:
    def test_disabled_returns_signals_unchanged(self) -> None:
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        bias = BiasConfig(regime_enabled=False)
        out = _apply_regime_gate_to_signals(signals, "AAPL", "4h", "bos", bias, None)
        # Same identity (early return when gate off).
        assert out is signals

    def test_empty_signals_returns_empty(self) -> None:
        signals = _toy_signals([], [])
        bias = _trend_only_bias()
        out = _apply_regime_gate_to_signals(signals, "AAPL", "4h", "bos", bias, None)
        assert out is signals  # empty short-circuit

    def test_no_regime_series_falls_open(self) -> None:
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        bias = _trend_only_bias()
        out = _apply_regime_gate_to_signals(signals, "AAPL", "4h", "bos", bias, None)
        # No HTF data → match live cache-miss behaviour (fall open).
        assert out is signals

    def test_drops_signals_in_disallowed_regime_hard_mode(self) -> None:
        # `bos` strategy_type is "structural", per-strategy mapping pins it to
        # "continuation" (T2a memo). We use the per-strategy override here so
        # the test does not depend on registry type strings.
        bias = BiasConfig(
            regime_enabled=True,
            regime_mode="hard",
            regime_htf_tf="4h",
            regime_per_strategy={"bos": ["trend"]},
        )
        # 4 HTF candles, alternating regimes. _resolve_regime_at uses iloc-2:
        # signal at 3_500 → current is 3_000 → prior is 2_000.
        # signal at 4_500 → current is 4_000 → prior is 3_000.
        regime_series = pd.Series(
            ["range", "trend", "range", "trend"],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        # Signal at 3_500 → prior regime = trend (allowed for bos) → keep.
        # Signal at 4_500 → prior regime = range (NOT allowed for bos) → drop.
        signals = _toy_signals([3_500, 4_500], ["long", "long"])
        out = _apply_regime_gate_to_signals(
            signals, "AAPL", "4h", "bos", bias, regime_series
        )
        assert list(out["open_time"]) == [3_500]

    def test_soft_mode_keeps_all_signals(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bias = BiasConfig(
            regime_enabled=True,
            regime_mode="soft",
            regime_htf_tf="4h",
            regime_per_strategy={"bos": ["trend"]},
        )
        regime_series = pd.Series(
            ["range", "trend", "range", "trend"],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        signals = _toy_signals([3_500, 4_500], ["long", "long"])
        with caplog.at_level(logging.INFO, logger="analytics.signal.gates"):
            out = _apply_regime_gate_to_signals(
                signals, "AAPL", "4h", "bos", bias, regime_series
            )
        # Soft mode keeps everything but logs the would-be drop.
        assert sorted(out["open_time"].tolist()) == [3_500, 4_500]
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "soft-flagged" in joined

    def test_unknown_regime_falls_open(self) -> None:
        bias = BiasConfig(
            regime_enabled=True,
            regime_mode="hard",
            regime_htf_tf="4h",
            regime_per_strategy={"bos": ["trend"]},
        )
        # Prior candle's regime is "unknown" → live gate falls open.
        regime_series = pd.Series(
            ["unknown", "range", "range"],
            index=pd.Index([1_000, 2_000, 3_000], dtype="int64"),
        )
        signals = _toy_signals([2_500], ["long"])
        out = _apply_regime_gate_to_signals(
            signals, "AAPL", "4h", "bos", bias, regime_series
        )
        # Prior of 2_500 is 1_000 ("unknown") → live gate returns events unchanged.
        assert list(out["open_time"]) == [2_500]

    def test_per_signal_regime_resolution(self) -> None:
        # Two signals at different times resolve to different regimes — proves
        # per-signal resolution rather than a single snapshot.
        bias = BiasConfig(
            regime_enabled=True,
            regime_mode="hard",
            regime_htf_tf="4h",
            regime_per_strategy={"bos": ["trend"]},
        )
        regime_series = pd.Series(
            ["trend", "range", "trend"],
            index=pd.Index([1_000, 2_000, 3_000], dtype="int64"),
        )
        # Signal 1 at 2_500 → prior is 1_000 (trend) → keep.
        # Signal 2 at 3_500 → prior is 2_000 (range) → drop.
        signals = _toy_signals([2_500, 3_500], ["long", "long"])
        out = _apply_regime_gate_to_signals(
            signals, "AAPL", "4h", "bos", bias, regime_series
        )
        assert list(out["open_time"]) == [2_500]


# ---------------------------------------------------------------------------
# run_backtest integration — default no-op + on/off comparison
# ---------------------------------------------------------------------------


def _toy_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": list(range(0, 30_000, 1_000)),
            "open": [100.0] * 30,
            "high": [101.0] * 30,
            "low": [99.0] * 30,
            "close": [100.0] * 30,
            "volume": [1000.0] * 30,
        }
    )


def _toy_engine_signals(open_times: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": open_times,
            "direction": ["long"] * len(open_times),
            "reason": ["t"] * len(open_times),
            "sl_price": [98.0] * len(open_times),
            "context": ["c"] * len(open_times),
            "low_volume": [False] * len(open_times),
            "tp_price": [104.0] * len(open_times),
        }
    )


class TestRunBacktestRegimeGate:
    def test_default_off_path_byte_identical(self) -> None:
        # PR-1 contract: default args + live_parity=None ≡ pre-T6 behaviour.
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000]),
            "AAPL",
            "4h",
            "bos",
        )
        # Same with all the new kwargs explicitly None.
        with_none = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000]),
            "AAPL",
            "4h",
            "bos",
            live_parity=None,
            bias_cfg=None,
            regime_series=None,
        )
        assert len(baseline.trades) == len(with_none.trades)
        assert baseline.total_r == with_none.total_r

    def test_gate_on_but_no_bias_cfg_is_no_op(self) -> None:
        # live_parity.regime is on, but bias_cfg=None → engine short-circuits.
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(regime=True),
            bias_cfg=None,
        )
        assert len(baseline.trades) == len(gated.trades)

    def test_gate_on_bias_disabled_is_no_op(self) -> None:
        bias = BiasConfig(regime_enabled=False)
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(regime=True),
            bias_cfg=bias,
        )
        assert len(baseline.trades) == len(gated.trades)

    def test_gate_filters_signals_with_regime_series(self) -> None:
        bias = BiasConfig(
            regime_enabled=True,
            regime_mode="hard",
            regime_htf_tf="4h",
            regime_per_strategy={"bos": ["trend"]},
        )
        # 4 HTF candles in increasing open_time. Two backtest signals:
        #   sig at 3_000 → searchsorted right=3, pos=2 (current=3_000),
        #     target=1 → regime at 2_000 = "trend" → allowed for bos → keep.
        #   sig at 5_000 → searchsorted right=4, pos=3 (current=4_000),
        #     target=2 → regime at 3_000 = "range" → NOT allowed → drop.
        regime_series = pd.Series(
            ["range", "trend", "range", "trend"],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([3_000, 5_000]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([3_000, 5_000]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(regime=True),
            bias_cfg=bias,
            regime_series=regime_series,
        )
        assert len(baseline.trades) == 2
        assert len(gated.trades) == 1
        assert gated.trades[0].signal_time == 3_000
