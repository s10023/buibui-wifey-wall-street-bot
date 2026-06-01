"""Tests for T6 live-parity direction filter gate (PR-3).

Covers the engine adapter `_apply_direction_filter_gate_to_signals` and the
wire-up inside `run_backtest()`. The gate must reuse live's
`_apply_direction_filter_gate` verbatim — pure per-event flag check, no HTF
data, no time-series. These tests verify the empty / disabled short-circuits,
hard vs soft mode behaviour, and that the default-off path stays byte-identical
(regression goldens contract).
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from analytics.backtest.engine import (
    _apply_direction_filter_gate_to_signals,
    run_backtest,
)
from analytics.backtest.live_parity_config import LiveParityConfig
from analytics.signal_config import BiasConfig, StrategyOverride


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


def _hard_bias() -> BiasConfig:
    return BiasConfig(direction_filter_enabled=True, direction_filter_mode="hard")


def _soft_bias() -> BiasConfig:
    return BiasConfig(direction_filter_enabled=True, direction_filter_mode="soft")


# ---------------------------------------------------------------------------
# _apply_direction_filter_gate_to_signals — adapter parity
# ---------------------------------------------------------------------------


class TestApplyDirectionFilterGateToSignals:
    def test_disabled_returns_signals_unchanged(self) -> None:
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        bias = BiasConfig(direction_filter_enabled=False)
        out = _apply_direction_filter_gate_to_signals(
            signals, "AAPL", "4h", "bos", bias, {"bos": StrategyOverride()}
        )
        assert out is signals

    def test_empty_signals_returns_empty(self) -> None:
        signals = _toy_signals([], [])
        out = _apply_direction_filter_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "bos",
            _hard_bias(),
            {"bos": StrategyOverride()},
        )
        assert out is signals

    def test_no_strategy_params_falls_open(self) -> None:
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        out = _apply_direction_filter_gate_to_signals(
            signals, "AAPL", "4h", "bos", _hard_bias(), None
        )
        assert out is signals

    def test_empty_strategy_params_falls_open(self) -> None:
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        out = _apply_direction_filter_gate_to_signals(
            signals, "AAPL", "4h", "bos", _hard_bias(), {}
        )
        assert out is signals

    def test_hard_mode_drops_suppressed_long(self) -> None:
        signals = _toy_signals([1_000, 2_000, 3_000], ["long", "short", "long"])
        out = _apply_direction_filter_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "bos",
            _hard_bias(),
            {"bos": StrategyOverride(suppress_long=True)},
        )
        assert list(out["open_time"]) == [2_000]

    def test_hard_mode_drops_suppressed_short(self) -> None:
        signals = _toy_signals([1_000, 2_000, 3_000], ["long", "short", "long"])
        out = _apply_direction_filter_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "bos",
            _hard_bias(),
            {"bos": StrategyOverride(suppress_short=True)},
        )
        assert list(out["open_time"]) == [1_000, 3_000]

    def test_soft_mode_keeps_all_signals_with_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        with caplog.at_level(logging.INFO, logger="analytics.signal.gates"):
            out = _apply_direction_filter_gate_to_signals(
                signals,
                "AAPL",
                "4h",
                "bos",
                _soft_bias(),
                {"bos": StrategyOverride(suppress_long=True)},
            )
        # Soft mode keeps everything but logs the would-be drop.
        assert sorted(out["open_time"].tolist()) == [1_000, 2_000]
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "soft-flagged" in joined

    def test_unknown_strategy_falls_open(self) -> None:
        # Strategy missing from strategy_params dict → live gate keeps event
        # (defensive: a freshly added detector should not be silently dropped).
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        out = _apply_direction_filter_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "newstrat",
            _hard_bias(),
            {"bos": StrategyOverride(suppress_long=True)},
        )
        assert list(out["open_time"]) == [1_000, 2_000]


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


def _toy_engine_signals(open_times: list[int], directions: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": open_times,
            "direction": directions,
            "reason": ["t"] * len(open_times),
            "sl_price": [98.0] * len(open_times),
            "context": ["c"] * len(open_times),
            "low_volume": [False] * len(open_times),
            "tp_price": [104.0] * len(open_times),
        }
    )


class TestRunBacktestDirectionFilterGate:
    def test_default_off_path_byte_identical(self) -> None:
        # PR-1 contract: default kwargs ≡ pre-T6 behaviour, including this PR's
        # additions (strategy_params + htf_slope_series_by_anchor default None).
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "short"]),
            "AAPL",
            "4h",
            "bos",
        )
        with_none = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "short"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=None,
            bias_cfg=None,
            regime_series=None,
            strategy_params=None,
            htf_slope_series_by_anchor=None,
        )
        assert len(baseline.trades) == len(with_none.trades)
        assert baseline.total_r == with_none.total_r

    def test_gate_on_but_no_bias_cfg_is_no_op(self) -> None:
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "short"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "short"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(direction_filter=True),
            bias_cfg=None,
            strategy_params={"bos": StrategyOverride(suppress_long=True)},
        )
        assert len(baseline.trades) == len(gated.trades)

    def test_gate_on_bias_disabled_is_no_op(self) -> None:
        bias = BiasConfig(direction_filter_enabled=False)
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "short"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "short"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(direction_filter=True),
            bias_cfg=bias,
            strategy_params={"bos": StrategyOverride(suppress_long=True)},
        )
        assert len(baseline.trades) == len(gated.trades)

    def test_gate_filters_suppressed_direction(self) -> None:
        bias = _hard_bias()
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000, 8_000], ["long", "short", "long"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000, 8_000], ["long", "short", "long"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(direction_filter=True),
            bias_cfg=bias,
            strategy_params={"bos": StrategyOverride(suppress_long=True)},
        )
        assert len(baseline.trades) == 3
        assert len(gated.trades) == 1
        assert gated.trades[0].direction == "short"
        assert gated.trades[0].signal_time == 5_000
