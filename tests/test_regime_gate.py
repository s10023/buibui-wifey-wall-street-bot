"""Unit tests for the v2 Phase 2 regime gate (`_apply_regime_gate`).

The gate is pure — it consumes a pre-computed regime cache, so these tests
do not need any DB or OHLCV setup.  classify_series itself is covered in
`tests/test_regime.py`.
"""

from analytics.regime import Regime
from analytics.signal.gates import _apply_regime_gate
from analytics.signal.types import SignalEvent
from analytics.signal_config import BiasConfig


def _evt(strategy: str, direction: str = "long") -> SignalEvent:
    return SignalEvent(
        symbol="BTCUSDT",
        timeframe="1h",
        strategy=strategy,
        direction=direction,
        reason="test",
        open_time=1_700_000_000_000,
        price=100.0,
    )


def _bias(
    *,
    enabled: bool = True,
    mode: str = "hard",
    enabled_regimes: dict[str, list[str]] | None = None,
    per_strategy: dict[str, list[str]] | None = None,
) -> BiasConfig:
    # Default enablement matches the v1 bridge mapping in
    # docs/redesign/buibui-redesign-phase2-plan.md.
    default_enabled: dict[str, list[str]] = {
        "trend": ["trend"],
        "fib": ["trend"],
        "flow": ["trend", "range", "high_vol"],
        "structural": ["trend", "range", "high_vol"],
        "price_action": ["trend", "range", "high_vol"],
        "candlestick": ["trend", "range", "high_vol"],
        "session": ["trend", "range", "high_vol"],
    }
    return BiasConfig(
        regime_enabled=enabled,
        regime_mode=mode,
        regime_htf_tf="4h",
        regime_enabled_regimes=enabled_regimes
        if enabled_regimes is not None
        else default_enabled,
        regime_per_strategy=per_strategy
        if per_strategy is not None
        else {"bos": ["trend"]},
    )


class TestRegimeGate:
    def test_disabled_is_noop(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "range"}
        # ema is "trend" type → not enabled in range, but gate is disabled.
        events = [_evt("ema", "long")]
        out = _apply_regime_gate(events, _bias(enabled=False), cache, "BTCUSDT", "1h")
        assert len(out) == 1

    def test_hard_mode_drops_continuation_in_range(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "range"}
        # ema (trend) and ote_entry (fib) are continuation → dropped in range.
        events = [_evt("ema", "long"), _evt("ote_entry", "short")]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert out == []

    def test_hard_mode_drops_continuation_in_high_vol(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "high_vol"}
        events = [_evt("ema", "long"), _evt("ote_entry", "short")]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert out == []

    def test_continuation_passes_in_trend(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "trend"}
        events = [_evt("ema", "long"), _evt("ote_entry", "short")]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 2

    def test_reversion_passes_in_range(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "range"}
        # fvg + eqh_eql (structural), pin_bar (candlestick),
        # wick_fills (price_action) all enabled in range.
        events = [
            _evt("fvg", "long"),
            _evt("eqh_eql", "short"),
            _evt("pin_bar", "long"),
            _evt("wick_fills", "short"),
        ]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 4

    def test_session_passes_in_all_regimes(self) -> None:
        events = [_evt("orb", "long")]
        for regime in ("trend", "range", "high_vol"):
            cache: dict[str, Regime] = {"BTCUSDT": regime}
            out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
            assert len(out) == 1, f"orb dropped in {regime} regime"

    def test_unknown_regime_falls_open(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "unknown"}
        # ema (trend) would normally be dropped in non-trend, but unknown → allow.
        events = [_evt("ema", "long"), _evt("fvg", "short")]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 2

    def test_cache_miss_falls_open(self) -> None:
        cache: dict[str, Regime] = {}
        events = [_evt("ema", "long")]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 1

    def test_soft_mode_keeps_all(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "range"}
        # ema would be dropped in hard mode; soft only logs.
        events = [_evt("ema", "long"), _evt("ote_entry", "short")]
        out = _apply_regime_gate(events, _bias(mode="soft"), cache, "BTCUSDT", "1h")
        assert len(out) == 2

    def test_per_strategy_override_bos_continuation(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "range"}
        # bos is structural by type (would pass in range), but per_strategy
        # override pins it to ["trend"] only → dropped in range.
        events = [_evt("bos", "long")]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert out == []

    def test_per_strategy_override_bos_passes_in_trend(self) -> None:
        cache: dict[str, Regime] = {"BTCUSDT": "trend"}
        events = [_evt("bos", "short")]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 1

    def test_unknown_strategy_type_falls_open(self) -> None:
        # Defensive: a strategy not in any enablement list (e.g. a newly added
        # detector before TOML is updated) should fall open, not silently drop.
        cache: dict[str, Regime] = {"BTCUSDT": "range"}
        events = [_evt("brand_new_strategy", "long")]
        out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 1

    def test_empty_event_list_returns_empty(self) -> None:
        out = _apply_regime_gate(
            [], _bias(mode="hard"), {"BTCUSDT": "range"}, "BTCUSDT", "1h"
        )
        assert out == []

    def test_fib_group_dropped_in_range_and_high_vol(self) -> None:
        # ote_entry is BOS-anchored continuation (`fib` type).
        for regime in ("range", "high_vol"):
            cache: dict[str, Regime] = {"BTCUSDT": regime}
            events = [
                _evt("ote_entry", "short"),
            ]
            out = _apply_regime_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
            assert out == [], f"fib group leaked in {regime}"
