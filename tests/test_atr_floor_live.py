"""Tests for the live ATR-as-min-SL floor (F9 live path).

Covers `analytics.signal.atr_floor._apply_atr_floor` plus the resolver and
TOML-loading surface that feeds it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.signal.atr_floor import _apply_atr_floor
from analytics.signal.resolvers import _resolve_atr_sl_floor
from analytics.signal.types import SignalEvent
from analytics.signal_config import (
    SignalWatchConfig,
    StrategyOverride,
    SymbolOverride,
    load_signal_config,
)


def _make_ohlcv(n: int = 30, base: float = 100.0, atr: float = 1.0) -> pd.DataFrame:
    """Build a synthetic OHLCV frame with a known per-candle range of `atr`.

    Each candle has high - low == atr exactly, prev_close == close so the
    True Range == high - low; this makes ATR14 == `atr` regardless of length.
    """
    rng = np.arange(n, dtype=np.float64)
    closes = np.full(n, base, dtype=np.float64)
    highs = closes + atr / 2.0
    lows = closes - atr / 2.0
    opens = closes.copy()
    return pd.DataFrame(
        {
            "open_time": (rng * 3_600_000).astype(np.int64),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.full(n, 1.0, dtype=np.float64),
        }
    )


def _event(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    strategy: str = "bos",
    symbol: str = "BTCUSDT",
    tf: str = "1h",
) -> SignalEvent:
    return SignalEvent(
        symbol=symbol,
        timeframe=tf,
        strategy=strategy,
        direction=direction,
        reason="test",
        open_time=0,
        price=entry,
        sl_price=sl,
        tp_price=tp,
        context="",
        confidence=3,
    )


class TestApplyAtrFloorNoOp:
    """Cases where the helper must leave events untouched."""

    def test_empty_events_returns_unchanged(self) -> None:
        df = _make_ohlcv()
        assert _apply_atr_floor([], df, "BTCUSDT", "1h", None, 2.0, 2.5, True) == []

    def test_flag_off_globally_is_noop(self) -> None:
        df = _make_ohlcv(atr=2.0)
        ev = _event("long", entry=100.0, sl=99.5, tp=101.0)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", None, 2.0, 2.5, False)
        assert ev.sl_price == 99.5
        assert ev.tp_price == 101.0

    def test_no_multiplier_is_noop_even_if_floor_on(self) -> None:
        df = _make_ohlcv(atr=2.0)
        ev = _event("long", entry=100.0, sl=99.5, tp=101.0)
        # Floor on globally, but no multiplier configured anywhere → no-op.
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", None, 2.0, None, True)
        assert ev.sl_price == 99.5

    def test_zero_sl_price_is_skipped(self) -> None:
        """sl_price == 0 means the detector emitted no structural SL."""
        df = _make_ohlcv(atr=2.0)
        ev = _event("long", entry=100.0, sl=0.0, tp=0.0)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", None, 2.0, 2.5, True)
        assert ev.sl_price == 0.0
        assert ev.tp_price == 0.0

    def test_structural_sl_already_wider_than_atr_is_untouched(self) -> None:
        df = _make_ohlcv(atr=1.0)  # ATR14 == 1.0; 2.5× = 2.5
        # Structural distance = 3.0 > 2.5 atr_dist → no widening.
        ev = _event("long", entry=100.0, sl=97.0, tp=109.0)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", None, 2.0, 2.5, True)
        assert ev.sl_price == 97.0
        assert ev.tp_price == 109.0

    def test_insufficient_history_falls_open(self) -> None:
        """ATR14 needs at least 1 prior close — single-candle df returns None."""
        df = _make_ohlcv(n=1, atr=2.0)
        ev = _event("long", entry=100.0, sl=99.9, tp=100.5)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", None, 2.0, 2.5, True)
        assert ev.sl_price == 99.9
        assert ev.tp_price == 100.5


class TestApplyAtrFloorWidens:
    """Cases where the floor must widen the SL and recompute the TP."""

    def test_long_widens_sl_and_recomputes_tp(self) -> None:
        df = _make_ohlcv(atr=2.0)  # ATR14 == 2.0
        # Multiplier 2.5 → atr_dist = 5.0. Structural dist = 0.5. → widen.
        # New SL = entry − 5.0 = 95.0; tp_r=2.0 → TP = entry + 2×5 = 110.0.
        ev = _event("long", entry=100.0, sl=99.5, tp=101.0)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", None, 2.0, 2.5, True)
        assert ev.sl_price == pytest.approx(95.0)
        assert ev.tp_price == pytest.approx(110.0)

    def test_short_widens_sl_and_recomputes_tp(self) -> None:
        df = _make_ohlcv(atr=2.0)
        # Short: SL above entry. Structural 0.5 vs atr_dist 5.0 → widen.
        # New SL = entry + 5.0 = 105.0; TP = entry − 2×5 = 90.0.
        ev = _event("short", entry=100.0, sl=100.5, tp=99.0)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", None, 2.0, 2.5, True)
        assert ev.sl_price == pytest.approx(105.0)
        assert ev.tp_price == pytest.approx(90.0)

    def test_direction_aware_tp_r_used(self) -> None:
        """When tp_r_long differs from global tp_r, TP must use the directional value."""
        df = _make_ohlcv(atr=2.0)
        # tp_r_long=3.0 overrides global tp_r=2.0 for long events.
        params = {"bos": StrategyOverride(tp_r_long=3.0)}
        ev = _event("long", entry=100.0, sl=99.5, tp=101.0)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", params, 2.0, 2.5, True)
        assert ev.sl_price == pytest.approx(95.0)
        # tp_r=3 × atr_dist=5 = 15 → TP = 115.0
        assert ev.tp_price == pytest.approx(115.0)

    def test_per_strategy_floor_override_on(self) -> None:
        """Strategy-level atr_sl_floor=True wins over global False."""
        df = _make_ohlcv(atr=2.0)
        params = {"bos": StrategyOverride(atr_sl_floor=True, atr_sl_multiplier=2.5)}
        ev = _event("long", entry=100.0, sl=99.5, tp=101.0)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", params, 2.0, None, False)
        assert ev.sl_price == pytest.approx(95.0)
        assert ev.tp_price == pytest.approx(110.0)

    def test_per_strategy_floor_override_off_beats_global_on(self) -> None:
        """Strategy-level atr_sl_floor=False wins over global True."""
        df = _make_ohlcv(atr=2.0)
        params = {"bos": StrategyOverride(atr_sl_floor=False)}
        ev = _event("long", entry=100.0, sl=99.5, tp=101.0)
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", params, 2.0, 2.5, True)
        assert ev.sl_price == 99.5  # unchanged
        assert ev.tp_price == 101.0

    def test_per_strategy_per_tf_floor_override(self) -> None:
        df = _make_ohlcv(atr=2.0)
        # Floor on for 1h only; 4h request still uses global (off).
        params = {
            "bos": StrategyOverride(
                atr_sl_floor_per_tf={"1h": True}, atr_sl_multiplier=2.5
            )
        }
        ev_1h = _event("long", entry=100.0, sl=99.5, tp=101.0, tf="1h")
        ev_4h = _event("long", entry=100.0, sl=99.5, tp=101.0, tf="4h")
        _apply_atr_floor([ev_1h], df, "BTCUSDT", "1h", params, 2.0, None, False)
        _apply_atr_floor([ev_4h], df, "BTCUSDT", "4h", params, 2.0, None, False)
        assert ev_1h.sl_price == pytest.approx(95.0)
        assert ev_4h.sl_price == 99.5  # unchanged

    def test_per_symbol_floor_override_wins(self) -> None:
        df = _make_ohlcv(atr=2.0)
        params = {
            "bos": StrategyOverride(
                atr_sl_multiplier=2.5,
                atr_sl_floor=False,
                per_symbol={"BTCUSDT": SymbolOverride(atr_sl_floor=True)},
            )
        }
        ev = _event("long", entry=100.0, sl=99.5, tp=101.0, symbol="BTCUSDT")
        _apply_atr_floor([ev], df, "BTCUSDT", "1h", params, 2.0, None, False)
        assert ev.sl_price == pytest.approx(95.0)


class TestResolveAtrSlFloor:
    """Direct resolver tests — mirrors the per_symbol/per_tf hierarchy."""

    def test_global_default(self) -> None:
        assert _resolve_atr_sl_floor(None, "bos", "BTCUSDT", "1h", False) is False
        assert _resolve_atr_sl_floor(None, "bos", "BTCUSDT", "1h", True) is True

    def test_strategy_override_wins(self) -> None:
        params = {"bos": StrategyOverride(atr_sl_floor=True)}
        assert _resolve_atr_sl_floor(params, "bos", "BTCUSDT", "1h", False) is True

    def test_per_tf_wins_over_strategy_wide(self) -> None:
        params = {
            "bos": StrategyOverride(
                atr_sl_floor=False, atr_sl_floor_per_tf={"1h": True}
            )
        }
        assert _resolve_atr_sl_floor(params, "bos", "BTCUSDT", "1h", False) is True
        assert _resolve_atr_sl_floor(params, "bos", "BTCUSDT", "4h", False) is False

    def test_symbol_tf_wins_over_symbol_wide(self) -> None:
        params = {
            "bos": StrategyOverride(
                per_symbol={
                    "BTCUSDT": SymbolOverride(
                        atr_sl_floor=False, atr_sl_floor_per_tf={"1h": True}
                    )
                }
            )
        }
        assert _resolve_atr_sl_floor(params, "bos", "BTCUSDT", "1h", False) is True
        assert _resolve_atr_sl_floor(params, "bos", "BTCUSDT", "4h", False) is False


class TestLoadSignalConfigAtrSlFloor:
    """TOML-loading round-trips for `atr_sl_floor`."""

    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "cfg.toml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_global_floor_parsed(self, tmp_path: Path) -> None:
        p = self._write(tmp_path, "atr_sl_floor = true\n")
        cfg = load_signal_config(p)
        assert cfg.atr_sl_floor is True

    def test_global_floor_defaults_off(self, tmp_path: Path) -> None:
        p = self._write(tmp_path, "telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.atr_sl_floor is False

    def test_strategy_level_floor_parsed(self, tmp_path: Path) -> None:
        p = self._write(
            tmp_path,
            """\
[strategy_params.bos]
atr_sl_multiplier = 2.5
atr_sl_floor = true
atr_sl_floor_1h = true
""",
        )
        cfg = load_signal_config(p)
        ov = cfg.strategy_params["bos"]
        assert ov.atr_sl_floor is True
        assert ov.atr_sl_floor_per_tf == {"1h": True}
        assert ov.atr_sl_multiplier == 2.5

    def test_per_symbol_floor_parsed(self, tmp_path: Path) -> None:
        p = self._write(
            tmp_path,
            """\
[strategy_params.bos.BTCUSDT]
atr_sl_floor = true
atr_sl_floor_1h = true
""",
        )
        cfg = load_signal_config(p)
        sym = cfg.strategy_params["bos"].per_symbol["BTCUSDT"]
        assert sym.atr_sl_floor is True
        assert sym.atr_sl_floor_per_tf == {"1h": True}

    def test_effective_atr_sl_floor_method(self) -> None:
        cfg = SignalWatchConfig(
            atr_sl_floor=False,
            strategy_params={
                "bos": StrategyOverride(
                    atr_sl_floor_per_tf={"1h": True},
                    per_symbol={"ETHUSDT": SymbolOverride(atr_sl_floor=False)},
                )
            },
        )
        # 1h TF override wins for BTC
        assert cfg.effective_atr_sl_floor("bos", "BTCUSDT", "1h") is True
        # 4h falls back to global (False)
        assert cfg.effective_atr_sl_floor("bos", "BTCUSDT", "4h") is False
        # ETH per-symbol override beats the strategy-level per-tf
        assert cfg.effective_atr_sl_floor("bos", "ETHUSDT", "1h") is False
