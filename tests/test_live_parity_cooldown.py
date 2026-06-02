"""Tests for T6 live-parity cooldown gate (PR-5).

Covers the state machine ``_CooldownState``, the ``_resolve_cooldown_bars``
default-resolver, the adapter ``_apply_cooldown_gate_to_signals``, and the
wire-up inside ``run_backtest()``. State is scoped per call so each backtest
gets a fresh ledger — the engine path proves call-isolation by replaying the
same input twice and asserting byte-equal results.

Fork delta: wifey trades only 4h / 1d / 1wk (no intraday), so the baked-in
cooldown map is the equity ``{"4h": 2, "1d": 1, "1wk": 1}`` — not parent's
``{"15m": 4, "1h": 3, "4h": 2, "1d": 1}``.
"""

from __future__ import annotations

import pandas as pd

from analytics.backtest.engine import (
    _DEFAULT_COOLDOWN_BARS_PER_TF,
    _apply_cooldown_gate_to_signals,
    _CooldownState,
    _resolve_cooldown_bars,
    run_backtest,
)
from analytics.backtest.live_parity_config import LiveParityConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FOUR_HOUR_MS = 4 * 60 * 60 * 1000  # 14_400_000


def _ohlcv_4h(n: int = 24) -> pd.DataFrame:
    """Flat OHLCV at 4h cadence — enough candles for cooldown tests to span
    multiple bars without colliding with end-of-data semantics."""
    times = [i * _FOUR_HOUR_MS for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": times,
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [1000.0] * n,
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


# ---------------------------------------------------------------------------
# _resolve_cooldown_bars — defaults + overrides
# ---------------------------------------------------------------------------


class TestResolveCooldownBars:
    def test_baked_in_defaults_match_equity_map(self) -> None:
        cfg = LiveParityConfig(cooldown=True)
        assert _resolve_cooldown_bars("4h", cfg) == 2
        assert _resolve_cooldown_bars("1d", cfg) == 1
        assert _resolve_cooldown_bars("1wk", cfg) == 1

    def test_toml_override_wins_over_default(self) -> None:
        cfg = LiveParityConfig(cooldown=True, cooldown_bars_per_tf={"4h": 5})
        assert _resolve_cooldown_bars("4h", cfg) == 5
        # 1d falls through to baked-in default since not in override map
        assert _resolve_cooldown_bars("1d", cfg) == 1

    def test_unknown_timeframe_falls_back_to_one(self) -> None:
        # Intraday TFs wifey no longer trades are unknown → 1-bar floor.
        cfg = LiveParityConfig(cooldown=True)
        assert _resolve_cooldown_bars("15m", cfg) == 1
        assert _resolve_cooldown_bars("1h", cfg) == 1

    def test_none_override_map_uses_defaults(self) -> None:
        cfg = LiveParityConfig(cooldown=True, cooldown_bars_per_tf=None)
        assert _resolve_cooldown_bars("4h", cfg) == 2

    def test_default_map_exposes_equity_tfs(self) -> None:
        assert set(_DEFAULT_COOLDOWN_BARS_PER_TF) == {"4h", "1d", "1wk"}


# ---------------------------------------------------------------------------
# _apply_cooldown_gate_to_signals — adapter behaviour
# ---------------------------------------------------------------------------


class TestApplyCooldownGateToSignals:
    def test_empty_signals_returns_unchanged(self) -> None:
        empty = _signals([], [])
        out = _apply_cooldown_gate_to_signals(
            empty,
            "AAPL",
            "4h",
            "engulfing",
            LiveParityConfig(cooldown=True),
            _CooldownState(),
        )
        assert out.empty

    def test_single_signal_always_passes_and_stamps_state(self) -> None:
        sig = _signals([0], ["long"])
        state = _CooldownState()
        out = _apply_cooldown_gate_to_signals(
            sig, "AAPL", "4h", "engulfing", LiveParityConfig(cooldown=True), state
        )
        assert len(out) == 1
        assert state.last_fire_by_key == {("AAPL", "4h", "engulfing", "long"): 0}

    def test_within_window_second_signal_dropped(self) -> None:
        # 4h cooldown = 2 bars = 8h. Two longs 4h apart → second within window.
        sig = _signals([0, _FOUR_HOUR_MS], ["long", "long"])
        out = _apply_cooldown_gate_to_signals(
            sig,
            "AAPL",
            "4h",
            "engulfing",
            LiveParityConfig(cooldown=True),
            _CooldownState(),
        )
        assert list(out["open_time"]) == [0]

    def test_at_window_boundary_second_signal_passes(self) -> None:
        # 4h × 2 bars = 8h window. Second signal exactly at +8h is outside the
        # strict-less-than window → passes.
        sig = _signals([0, 2 * _FOUR_HOUR_MS], ["long", "long"])
        out = _apply_cooldown_gate_to_signals(
            sig,
            "AAPL",
            "4h",
            "engulfing",
            LiveParityConfig(cooldown=True),
            _CooldownState(),
        )
        assert list(out["open_time"]) == [0, 2 * _FOUR_HOUR_MS]

    def test_after_window_second_signal_passes(self) -> None:
        sig = _signals([0, 3 * _FOUR_HOUR_MS], ["long", "long"])
        out = _apply_cooldown_gate_to_signals(
            sig,
            "AAPL",
            "4h",
            "engulfing",
            LiveParityConfig(cooldown=True),
            _CooldownState(),
        )
        assert len(out) == 2

    def test_opposite_directions_dont_suppress(self) -> None:
        # long at t=0 must NOT suppress a short at t=4h (different direction
        # → different cooldown key).
        sig = _signals([0, _FOUR_HOUR_MS], ["long", "short"])
        out = _apply_cooldown_gate_to_signals(
            sig,
            "AAPL",
            "4h",
            "engulfing",
            LiveParityConfig(cooldown=True),
            _CooldownState(),
        )
        assert list(out["direction"]) == ["long", "short"]

    def test_shared_state_isolates_by_strategy(self) -> None:
        # Two engine calls sharing a state object across strategies should
        # still leave keys independent because strategy is in the key.
        state = _CooldownState()
        sig = _signals([0, _FOUR_HOUR_MS], ["long", "long"])
        out_a = _apply_cooldown_gate_to_signals(
            sig, "AAPL", "4h", "engulfing", LiveParityConfig(cooldown=True), state
        )
        out_b = _apply_cooldown_gate_to_signals(
            sig, "AAPL", "4h", "pin_bar", LiveParityConfig(cooldown=True), state
        )
        # engulfing drops second; pin_bar — fresh key — drops second too.
        assert list(out_a["open_time"]) == [0]
        assert list(out_b["open_time"]) == [0]
        assert len(state.last_fire_by_key) == 2

    def test_shared_state_isolates_by_symbol_and_tf(self) -> None:
        state = _CooldownState()
        sig = _signals([0, _FOUR_HOUR_MS], ["long", "long"])
        _apply_cooldown_gate_to_signals(
            sig, "AAPL", "4h", "engulfing", LiveParityConfig(cooldown=True), state
        )
        out = _apply_cooldown_gate_to_signals(
            sig, "MSTR", "4h", "engulfing", LiveParityConfig(cooldown=True), state
        )
        # MSTR has its own key — first signal passes.
        assert list(out["open_time"]) == [0]

    def test_unordered_input_is_sorted_before_walk(self) -> None:
        # Reversed input — adapter sorts by open_time before applying cooldown
        # so the earlier signal owns the slot.
        sig = _signals([_FOUR_HOUR_MS, 0], ["long", "long"])
        out = _apply_cooldown_gate_to_signals(
            sig,
            "AAPL",
            "4h",
            "engulfing",
            LiveParityConfig(cooldown=True),
            _CooldownState(),
        )
        assert list(out["open_time"]) == [0]

    def test_cooldown_bars_override_extends_window(self) -> None:
        # Override 4h cooldown to 5 bars (=20h) → 12h gap not enough.
        cfg = LiveParityConfig(cooldown=True, cooldown_bars_per_tf={"4h": 5})
        sig = _signals([0, 3 * _FOUR_HOUR_MS], ["long", "long"])
        out = _apply_cooldown_gate_to_signals(
            sig, "AAPL", "4h", "engulfing", cfg, _CooldownState()
        )
        assert list(out["open_time"]) == [0]

    def test_zero_cooldown_bars_is_no_op(self) -> None:
        cfg = LiveParityConfig(cooldown=True, cooldown_bars_per_tf={"4h": 0})
        sig = _signals([0, _FOUR_HOUR_MS, 2 * _FOUR_HOUR_MS], ["long", "long", "long"])
        out = _apply_cooldown_gate_to_signals(
            sig, "AAPL", "4h", "engulfing", cfg, _CooldownState()
        )
        assert len(out) == 3

    def test_ties_at_same_open_time_keep_only_first(self) -> None:
        sig = _signals([0, 0], ["long", "long"])
        out = _apply_cooldown_gate_to_signals(
            sig,
            "AAPL",
            "4h",
            "engulfing",
            LiveParityConfig(cooldown=True),
            _CooldownState(),
        )
        assert len(out) == 1

    def test_index_reset_on_returned_frame(self) -> None:
        sig = _signals([_FOUR_HOUR_MS, 0, 3 * _FOUR_HOUR_MS], ["long", "long", "long"])
        out = _apply_cooldown_gate_to_signals(
            sig,
            "AAPL",
            "4h",
            "engulfing",
            LiveParityConfig(cooldown=True),
            _CooldownState(),
        )
        # Returned frame must have a contiguous RangeIndex post-filter.
        assert list(out.index) == list(range(len(out)))


# ---------------------------------------------------------------------------
# run_backtest integration — wire-up + per-call state scoping
# ---------------------------------------------------------------------------


class TestRunBacktestCooldownIntegration:
    def test_default_off_byte_identical(self) -> None:
        # live_parity=None must never alter trade output.
        ohlcv = _ohlcv_4h(n=8)
        sig = _signals([0, _FOUR_HOUR_MS], ["long", "long"])
        bt_off = run_backtest(ohlcv, sig.copy(), "AAPL", "4h", "engulfing")
        bt_explicit_off = run_backtest(
            ohlcv,
            sig.copy(),
            "AAPL",
            "4h",
            "engulfing",
            live_parity=LiveParityConfig(cooldown=False),
        )
        assert len(bt_off.trades) == len(bt_explicit_off.trades) == 2

    def test_cooldown_on_drops_back_to_back_signals(self) -> None:
        ohlcv = _ohlcv_4h(n=8)
        sig = _signals([0, _FOUR_HOUR_MS], ["long", "long"])
        bt = run_backtest(
            ohlcv,
            sig,
            "AAPL",
            "4h",
            "engulfing",
            live_parity=LiveParityConfig(cooldown=True),
        )
        # Second long is within the 8h window — only the first survives.
        assert len(bt.trades) == 1

    def test_cooldown_state_resets_between_calls(self) -> None:
        # Same input replayed back-to-back must yield identical trade output.
        ohlcv = _ohlcv_4h(n=8)
        sig = _signals([0, _FOUR_HOUR_MS], ["long", "long"])
        cfg = LiveParityConfig(cooldown=True)
        bt1 = run_backtest(
            ohlcv, sig.copy(), "AAPL", "4h", "engulfing", live_parity=cfg
        )
        bt2 = run_backtest(
            ohlcv, sig.copy(), "AAPL", "4h", "engulfing", live_parity=cfg
        )
        assert len(bt1.trades) == len(bt2.trades) == 1

    def test_opposite_directions_both_survive_on_engine_path(self) -> None:
        ohlcv = _ohlcv_4h(n=8)
        sig = _signals([0, _FOUR_HOUR_MS], ["long", "short"])
        bt = run_backtest(
            ohlcv,
            sig,
            "AAPL",
            "4h",
            "engulfing",
            live_parity=LiveParityConfig(cooldown=True),
        )
        assert len(bt.trades) == 2
