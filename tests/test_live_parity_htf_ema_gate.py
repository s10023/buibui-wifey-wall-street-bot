"""Tests for T6 live-parity F8 HTF EMA gate (PR-3).

Covers the engine helper `_resolve_series_at` and the adapter
`_apply_htf_ema_gate_to_signals`, plus wire-up inside `run_backtest()`.
The gate must reuse live's `_apply_htf_ema_gate` verbatim — per-signal HTF
slope lookup via `_resolve_series_at` (mirrors `_resolve_regime_at`'s
last-fully-closed semantics).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from analytics.backtest.engine import (
    _apply_htf_ema_gate_to_signals,
    _resolve_series_at,
    run_backtest,
)
from analytics.backtest.live_parity_config import LiveParityConfig
from analytics.signal_config import BiasConfig, HtfEmaAnchor

# ---------------------------------------------------------------------------
# _resolve_series_at — last-closed numeric lookup
# ---------------------------------------------------------------------------


class TestResolveSeriesAt:
    def test_empty_series_returns_none(self) -> None:
        series = pd.Series([], dtype="float64")
        assert _resolve_series_at(1_000, series) is None

    def test_single_candle_no_prior_returns_none(self) -> None:
        series = pd.Series([0.05], index=pd.Index([1_000], dtype="int64"))
        assert _resolve_series_at(2_000, series) is None
        assert _resolve_series_at(1_000, series) is None

    def test_returns_value_of_candle_before_current(self) -> None:
        series = pd.Series(
            [0.01, 0.02, 0.03, 0.04],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        # Signal at 3_500: current = 3_000, prior = 2_000 → 0.02.
        assert _resolve_series_at(3_500, series) == pytest.approx(0.02)
        # Boundary: side='right' makes 3_000 the current.
        assert _resolve_series_at(3_000, series) == pytest.approx(0.02)
        # Signal at 4_500 → current = 4_000 → prior = 3_000 = 0.03.
        assert _resolve_series_at(4_500, series) == pytest.approx(0.03)

    def test_signal_before_series_returns_none(self) -> None:
        series = pd.Series([0.01, 0.02], index=pd.Index([2_000, 3_000], dtype="int64"))
        assert _resolve_series_at(500, series) is None

    def test_nan_value_returned_as_none(self) -> None:
        series = pd.Series(
            [np.nan, 0.02, 0.03],
            index=pd.Index([1_000, 2_000, 3_000], dtype="int64"),
        )
        # Signal at 2_500 → current = 2_000, prior = 1_000 (NaN warmup) → None.
        assert _resolve_series_at(2_500, series) is None


# ---------------------------------------------------------------------------
# _apply_htf_ema_gate_to_signals — group + reuse live gate
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


def _bias_with_anchor(
    *,
    mode: str = "hard",
    tf: str = "4h",
    period: int = 50,
    slope_lookback: int = 10,
    deadband_pct: float = 0.003,
) -> BiasConfig:
    return BiasConfig(
        htf_ema_enabled=True,
        htf_ema_mode=mode,
        htf_ema_default_tf=tf,
        htf_ema_default_period=period,
        htf_ema_default_slope_lookback=slope_lookback,
        htf_ema_deadband_pct=deadband_pct,
    )


class TestApplyHtfEmaGateToSignals:
    def test_disabled_returns_signals_unchanged(self) -> None:
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        bias = BiasConfig(htf_ema_enabled=False)
        out = _apply_htf_ema_gate_to_signals(signals, "AAPL", "4h", "bos", bias, None)
        assert out is signals

    def test_empty_signals_returns_empty(self) -> None:
        signals = _toy_signals([], [])
        out = _apply_htf_ema_gate_to_signals(
            signals, "AAPL", "4h", "bos", _bias_with_anchor(), None
        )
        assert out is signals

    def test_no_slope_series_falls_open(self) -> None:
        signals = _toy_signals([1_000, 2_000], ["long", "short"])
        out = _apply_htf_ema_gate_to_signals(
            signals, "AAPL", "4h", "bos", _bias_with_anchor(), None
        )
        # None → fall open (matches live cache-miss).
        assert out is signals

    def test_hard_mode_drops_opposing_longs(self) -> None:
        bias = _bias_with_anchor(mode="hard")
        anchor_key = ("4h", 50, 10)
        # Negative slope → drops longs in hard mode.
        slope_series = pd.Series(
            [-0.05, -0.05, -0.05, -0.05],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        signals = _toy_signals([3_500, 4_500], ["long", "short"])
        out = _apply_htf_ema_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "bos",
            bias,
            {anchor_key: slope_series},
        )
        # long opposes negative slope → drop; short aligns → keep.
        assert list(out["open_time"]) == [4_500]

    def test_hard_mode_drops_opposing_shorts(self) -> None:
        bias = _bias_with_anchor(mode="hard")
        anchor_key = ("4h", 50, 10)
        slope_series = pd.Series(
            [0.05, 0.05, 0.05, 0.05],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        signals = _toy_signals([3_500, 4_500], ["long", "short"])
        out = _apply_htf_ema_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "bos",
            bias,
            {anchor_key: slope_series},
        )
        # short opposes positive slope → drop; long aligns → keep.
        assert list(out["open_time"]) == [3_500]

    def test_soft_mode_keeps_all_signals_with_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bias = _bias_with_anchor(mode="soft")
        anchor_key = ("4h", 50, 10)
        slope_series = pd.Series(
            [0.05, 0.05, 0.05, 0.05],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        signals = _toy_signals([3_500, 4_500], ["long", "short"])
        with caplog.at_level(logging.INFO, logger="analytics.signal.gates"):
            out = _apply_htf_ema_gate_to_signals(
                signals,
                "AAPL",
                "4h",
                "bos",
                bias,
                {anchor_key: slope_series},
            )
        assert sorted(out["open_time"].tolist()) == [3_500, 4_500]
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "soft-flagged" in joined

    def test_deadband_lets_both_directions_through(self) -> None:
        bias = _bias_with_anchor(mode="hard", deadband_pct=0.01)
        anchor_key = ("4h", 50, 10)
        # |slope| = 0.001 < deadband 0.01 → gate has no opinion.
        slope_series = pd.Series(
            [0.001, 0.001, 0.001, 0.001],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        signals = _toy_signals([3_500, 4_500], ["long", "short"])
        out = _apply_htf_ema_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "bos",
            bias,
            {anchor_key: slope_series},
        )
        assert sorted(out["open_time"].tolist()) == [3_500, 4_500]

    def test_warmup_nan_slope_falls_open(self) -> None:
        bias = _bias_with_anchor(mode="hard")
        anchor_key = ("4h", 50, 10)
        # Warmup at the front, then valid slope only at index 3.
        slope_series = pd.Series(
            [np.nan, np.nan, np.nan, -0.05],
            index=pd.Index([1_000, 2_000, 3_000, 4_000], dtype="int64"),
        )
        # Signal at 2_500 → prior is 1_000 (NaN) → falls open (resolve returns None).
        # Signal at 4_500 → prior is 3_000 (NaN) → also falls open.
        signals = _toy_signals([2_500, 4_500], ["long", "long"])
        out = _apply_htf_ema_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "bos",
            bias,
            {anchor_key: slope_series},
        )
        assert sorted(out["open_time"].tolist()) == [2_500, 4_500]

    def test_per_strategy_anchor_routing(self) -> None:
        # Two different strategies route to two different anchors; their slopes
        # at the same signal time may disagree.
        bias = BiasConfig(
            htf_ema_enabled=True,
            htf_ema_mode="hard",
            htf_ema_default_tf="4h",
            htf_ema_default_period=50,
            htf_ema_default_slope_lookback=10,
            htf_ema_per_strategy={
                "bos": HtfEmaAnchor(tf="1d", period=50, slope_lookback=10),
            },
        )
        # Same signal_time but two different slope readings for the two anchors.
        default_series = pd.Series(
            [0.05, 0.05, 0.05],
            index=pd.Index([1_000, 2_000, 3_000], dtype="int64"),
        )
        bos_series = pd.Series(
            [-0.05, -0.05, -0.05],
            index=pd.Index([1_000, 2_000, 3_000], dtype="int64"),
        )
        signals = _toy_signals([2_500], ["long"])
        # `bos` event uses 1d anchor → negative slope → long opposes → drop.
        out_bos = _apply_htf_ema_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "bos",
            bias,
            {
                ("4h", 50, 10): default_series,
                ("1d", 50, 10): bos_series,
            },
        )
        assert list(out_bos["open_time"]) == []
        # An engulfing event (no per-strategy override) uses the default 4h
        # anchor → positive slope → long aligns → keep.
        out_eng = _apply_htf_ema_gate_to_signals(
            signals,
            "AAPL",
            "4h",
            "engulfing",
            bias,
            {
                ("4h", 50, 10): default_series,
                ("1d", 50, 10): bos_series,
            },
        )
        assert list(out_eng["open_time"]) == [2_500]


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


class TestRunBacktestHtfEmaGate:
    def test_gate_on_but_no_bias_cfg_is_no_op(self) -> None:
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "long"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "long"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(f8_htf_ema=True),
            bias_cfg=None,
        )
        assert len(baseline.trades) == len(gated.trades)

    def test_gate_on_bias_disabled_is_no_op(self) -> None:
        bias = BiasConfig(htf_ema_enabled=False)
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "long"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([2_000, 5_000], ["long", "long"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(f8_htf_ema=True),
            bias_cfg=bias,
        )
        assert len(baseline.trades) == len(gated.trades)

    def test_gate_filters_opposing_long(self) -> None:
        bias = _bias_with_anchor(mode="hard")
        anchor_key = ("4h", 50, 10)
        # Negative slope at every prior candle → all longs opposed.
        slope_series = pd.Series(
            [-0.05] * 10,
            index=pd.Index(list(range(0, 10_000, 1_000)), dtype="int64"),
        )
        baseline = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([3_000, 5_000], ["long", "long"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _toy_ohlcv(),
            _toy_engine_signals([3_000, 5_000], ["long", "long"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(f8_htf_ema=True),
            bias_cfg=bias,
            htf_slope_series_by_anchor={anchor_key: slope_series},
        )
        assert len(baseline.trades) == 2
        assert len(gated.trades) == 0
