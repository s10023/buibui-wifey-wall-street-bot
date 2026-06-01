"""Tests for T6 live-parity ADR bias gate (PR-4).

Covers the engine adapter `_apply_adr_bias_gate_to_signals` and the wire-up
inside `run_backtest()`. The gate must reuse live's `_filter_signals_by_adr`
and `_is_adr_exempt` verbatim. Wifey has no per-direction exemption fields
(parent's PR #380 `adr_exempt_long`/`adr_exempt_short` were not ported), so
only strategy-wide `adr_exempt=True` is exercised here.
"""

from __future__ import annotations

import pandas as pd

from analytics.backtest.engine import (
    _apply_adr_bias_gate_to_signals,
    run_backtest,
)
from analytics.backtest.live_parity_config import LiveParityConfig
from analytics.signal_config import BiasConfig, StrategyOverride

# ---------------------------------------------------------------------------
# Fixtures — build a one-day OHLCV that triggers the ADR consumed-ratio logic
# ---------------------------------------------------------------------------


def _chasing_ohlcv() -> pd.DataFrame:
    """Single 24h day where the move is sharply UP — designed so any signal
    fired late in the session reports adr_consumed >= threshold and the close
    sits in the upper half of the range (move_up=True).

    Day open is 100.0; day high climbs to 110.0; close near 109. Daily range
    fraction = 10% which feeds the 14-day rolling ADR (single day → ADR=10%).
    A threshold of 0.50 means: by the time today's range covers >=5% of open,
    the gate suppresses LONGs (chasing the move).
    """
    return pd.DataFrame(
        {
            "open_time": [
                0,  # 00:00 UTC — day open
                3_600_000,
                7_200_000,
                10_800_000,
                14_400_000,
                18_000_000,  # range now spans 100→107 (7% of open)
                21_600_000,
                25_200_000,
            ],
            "open": [100.0, 102.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0],
            "high": [102.0, 104.0, 105.0, 106.0, 107.0, 108.5, 109.5, 110.0],
            "low": [99.5, 101.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0],
            "close": [101.5, 103.5, 104.5, 105.5, 106.5, 108.0, 109.0, 109.5],
            "volume": [1000.0] * 8,
        }
    )


def _signals(open_times: list[int], directions: list[str]) -> pd.DataFrame:
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


def _bias(threshold: float | None = 0.50) -> BiasConfig:
    return BiasConfig(adr_suppress_threshold=threshold)


# ---------------------------------------------------------------------------
# _apply_adr_bias_gate_to_signals — adapter parity with live
# ---------------------------------------------------------------------------


class TestApplyAdrBiasGateToSignals:
    def test_empty_signals_returns_unchanged(self) -> None:
        signals = _signals([], [])
        out = _apply_adr_bias_gate_to_signals(
            signals, _chasing_ohlcv(), "AAPL", "4h", "bos", _bias(), None
        )
        assert out is signals

    def test_no_threshold_returns_unchanged(self) -> None:
        signals = _signals([18_000_000], ["long"])
        bias = BiasConfig(adr_suppress_threshold=None)
        out = _apply_adr_bias_gate_to_signals(
            signals, _chasing_ohlcv(), "AAPL", "4h", "bos", bias, None
        )
        assert out is signals

    def test_chasing_long_dropped_when_consumed_above_threshold(self) -> None:
        # 5th candle (open_time=14_400_000) range so far = 100→107 = 7% of 100.
        # ADR (single day) ≈ 10.5%. Consumed ≈ 0.67 > 0.50 → LONG dropped.
        signals = _signals([14_400_000], ["long"])
        out = _apply_adr_bias_gate_to_signals(
            signals, _chasing_ohlcv(), "AAPL", "4h", "bos", _bias(0.50), None
        )
        assert out.empty

    def test_non_chasing_short_passes_through(self) -> None:
        # Move is UP today; a SHORT entry is the contrarian side and is allowed
        # by live `_filter_signals_by_adr` (only the chasing direction is cut).
        signals = _signals([14_400_000], ["short"])
        out = _apply_adr_bias_gate_to_signals(
            signals, _chasing_ohlcv(), "AAPL", "4h", "bos", _bias(0.50), None
        )
        assert len(out) == 1
        assert out.iloc[0]["direction"] == "short"

    def test_strategy_wide_exempt_lets_chase_through(self) -> None:
        # adr_exempt=True (strategy-wide) means both directions are exempt —
        # the chasing LONG passes through.
        signals = _signals([14_400_000], ["long"])
        params = {"bos": StrategyOverride(adr_exempt=True)}
        out = _apply_adr_bias_gate_to_signals(
            signals, _chasing_ohlcv(), "AAPL", "4h", "bos", _bias(0.50), params
        )
        assert len(out) == 1

    def test_unrelated_strategy_override_does_not_exempt_bos(self) -> None:
        # adr_exempt only on a different strategy → bos is not exempt → drops.
        signals = _signals([14_400_000], ["long"])
        params = {"engulfing": StrategyOverride(adr_exempt=True)}
        out = _apply_adr_bias_gate_to_signals(
            signals, _chasing_ohlcv(), "AAPL", "4h", "bos", _bias(0.50), params
        )
        assert out.empty

    def test_exempt_strategy_byte_identical(self) -> None:
        # With strategy-wide adr_exempt=True both directions skip the filter →
        # frame returned unchanged (same identity to assert no copy/sort drift).
        signals = _signals(
            [3_600_000, 14_400_000, 7_200_000],
            ["short", "long", "short"],
        )
        params = {"bos": StrategyOverride(adr_exempt=True)}
        out = _apply_adr_bias_gate_to_signals(
            signals, _chasing_ohlcv(), "AAPL", "4h", "bos", _bias(0.50), params
        )
        assert out is signals


# ---------------------------------------------------------------------------
# run_backtest integration — default no-op + on/off comparison
# ---------------------------------------------------------------------------


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


class TestRunBacktestAdrBiasGate:
    def test_default_off_path_byte_identical(self) -> None:
        baseline = run_backtest(
            _chasing_ohlcv(),
            _toy_engine_signals([14_400_000], ["long"]),
            "AAPL",
            "4h",
            "bos",
        )
        with_none = run_backtest(
            _chasing_ohlcv(),
            _toy_engine_signals([14_400_000], ["long"]),
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
            _chasing_ohlcv(),
            _toy_engine_signals([14_400_000], ["long"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _chasing_ohlcv(),
            _toy_engine_signals([14_400_000], ["long"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(adr_bias=True),
            bias_cfg=None,
        )
        assert len(baseline.trades) == len(gated.trades)

    def test_gate_on_no_threshold_is_no_op(self) -> None:
        bias = BiasConfig(adr_suppress_threshold=None)
        baseline = run_backtest(
            _chasing_ohlcv(),
            _toy_engine_signals([14_400_000], ["long"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _chasing_ohlcv(),
            _toy_engine_signals([14_400_000], ["long"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(adr_bias=True),
            bias_cfg=bias,
        )
        assert len(baseline.trades) == len(gated.trades)

    def test_gate_on_drops_chasing_long(self) -> None:
        baseline = run_backtest(
            _chasing_ohlcv(),
            _toy_engine_signals([14_400_000], ["long"]),
            "AAPL",
            "4h",
            "bos",
        )
        gated = run_backtest(
            _chasing_ohlcv(),
            _toy_engine_signals([14_400_000], ["long"]),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(adr_bias=True),
            bias_cfg=_bias(0.50),
        )
        # The chasing long was the only signal; gated path must drop it.
        assert len(baseline.trades) >= len(gated.trades)
        assert len(gated.trades) == 0

    def test_strategy_wide_exempt_lets_signal_through(self) -> None:
        signals = _toy_engine_signals([14_400_000], ["long"])
        params = {"bos": StrategyOverride(adr_exempt=True)}
        gated = run_backtest(
            _chasing_ohlcv(),
            signals,
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(adr_bias=True),
            bias_cfg=_bias(0.50),
            strategy_params=params,
        )
        # adr_exempt=True → chasing long passes through into simulation.
        assert len(gated.trades) >= 1
