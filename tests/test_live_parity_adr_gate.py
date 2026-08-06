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
from analytics.signal.gates import _filter_signals_by_adr, adr_gate_applies
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


# ---------------------------------------------------------------------------
# Timeframe applicability — the gate needs >1 bar per calendar day to mean
# anything. Audit: docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md
# ---------------------------------------------------------------------------


def _one_bar_per_day_ohlcv() -> pd.DataFrame:
    """Daily bars on distinct calendar dates — one bar per day.

    Every bar's "cumulative intraday range up to this candle" is its own full
    range, so consumed_ratio measures range-vs-ADR14 rather than exhaustion,
    and every bar closing up reports move_up=True. This is the shape `1d` and
    `1wk` production data actually has; the gate was designed for the
    multi-bar-per-day shape `_chasing_ohlcv()` builds.
    """
    day = 86_400_000
    return pd.DataFrame(
        {
            "open_time": [i * day for i in range(9)],
            "open": [100.0] * 9,
            # Days 0-6 narrow (1% range), day 7 wide (10%), day 8 narrow again so
            # a signal on day 7 has a following candle to enter on. Every bar
            # closes in the upper half of its own range → move_up=True for all.
            "high": [101.0] * 7 + [110.0, 101.0],
            "low": [100.0] * 9,
            "close": [100.9] * 7 + [109.5, 100.9],
            "volume": [1000.0] * 9,
        }
    )


class TestAdrGateApplies:
    def test_intraday_timeframes_apply(self) -> None:
        for tf in ("1m", "5m", "15m", "30m", "1h", "2h", "4h"):
            assert adr_gate_applies(tf), tf

    def test_daily_and_slower_do_not_apply(self) -> None:
        for tf in ("1d", "1wk", "1mo"):
            assert not adr_gate_applies(tf), tf

    def test_unknown_timeframe_defaults_to_not_applicable(self) -> None:
        # A gate that silently means something other than its docs is worse
        # than one that is off, so unknown timeframes must fall closed.
        assert not adr_gate_applies("3d")
        assert not adr_gate_applies("")


class TestAdrGateTimeframeDegeneracy:
    """The fix must be observable: identical data + signals, different tf."""

    def test_same_data_gated_on_4h_but_not_on_1d(self) -> None:
        ohlcv = _one_bar_per_day_ohlcv()
        # The final bar: wide range, closes in the upper half → a chasing long.
        signals = _signals([7 * 86_400_000], ["long"])

        on_4h = _filter_signals_by_adr(ohlcv, signals, 0.50, "4h")
        on_1d = _filter_signals_by_adr(ohlcv, signals, 0.50, "1d")

        # Falsification: if the timeframe argument were ignored these would match.
        assert len(on_4h) == 0, "gate must still bite on an applicable timeframe"
        assert len(on_1d) == 1, "gate must no-op where a day holds one bar"

    def test_weekly_is_a_no_op_too(self) -> None:
        ohlcv = _one_bar_per_day_ohlcv()
        signals = _signals([7 * 86_400_000], ["long"])
        assert len(_filter_signals_by_adr(ohlcv, signals, 0.50, "1wk")) == 1

    def test_direction_guard_is_degenerate_on_one_bar_days(self) -> None:
        """Why the no-op is needed, not just that it happens.

        On a one-bar day `move_up` reduces to "close in the upper half of its
        own bar" — the same quantity close-derived detectors read direction
        from. So every long on an up-closing bar is 'chasing' by construction
        and the direction guard spares nothing. Asserted on `4h` to show the
        degeneracy is a property of the DATA shape, not of the timeframe label.

        The ratio degenerates in the same step: with one bar per day, a run of
        equal-range bars gives consumed_ratio == range/ADR14 == 1.0 for every
        one of them (production median on `1d` is 0.925 against a 0.80
        threshold). So the gate stops discriminating on exhaustion entirely and
        collapses into a pure direction filter — 100% of one side, 0% of the
        other, which is not what any of its documentation claims.
        """
        ohlcv = _one_bar_per_day_ohlcv()
        times = [i * 86_400_000 for i in range(9)]
        # 0.40, not the 0.50 used elsewhere: the final bar's ratio is exactly
        # 0.01/0.02 = 0.50, so a 0.50 threshold sits on a float boundary and the
        # bar's fate would turn on representation error, not on the mechanism.
        kept_long = _filter_signals_by_adr(
            ohlcv, _signals(times, ["long"] * 9), 0.40, "4h"
        )
        kept_short = _filter_signals_by_adr(
            ohlcv, _signals(times, ["short"] * 9), 0.40, "4h"
        )
        assert len(kept_short) == 9, "no short is ever chasing on up-closing bars"
        assert len(kept_long) == 0, "every long is chasing by construction"


class TestAdrGateTimeframeThreadedThroughEngine:
    def test_run_backtest_1d_does_not_apply_adr_gate(self) -> None:
        """The engine adapter must pass its timeframe down, not hardcode one."""
        ohlcv = _one_bar_per_day_ohlcv()
        signals = _toy_engine_signals([7 * 86_400_000], ["long"])
        gated_1d = run_backtest(
            ohlcv,
            signals,
            "AAPL",
            "1d",
            "bos",
            live_parity=LiveParityConfig(adr_bias=True),
            bias_cfg=_bias(0.50),
        )
        gated_4h = run_backtest(
            ohlcv,
            signals,
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(adr_bias=True),
            bias_cfg=_bias(0.50),
        )
        assert len(gated_1d.trades) == 1, "1d must reach simulation ungated"
        assert len(gated_4h.trades) == 0, "4h must still be gated"
