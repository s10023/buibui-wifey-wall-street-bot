"""Tests for analytics/signal_config.py — pure config loader."""

import tomllib
from pathlib import Path

import pytest

from analytics.signal_config import (
    SignalWatchConfig,
    StrategyOverride,
    SymbolOverride,
    _day_filter_to_weekdays,
    _deep_merge,
    load_signal_config,
)


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "signal_watch.toml"
    p.write_text(content)
    return p


class TestSignalWatchConfigDefaults:
    def test_defaults(self) -> None:
        cfg = SignalWatchConfig()
        assert cfg.symbols is None
        assert cfg.timeframes == ["4h"]
        assert cfg.strategies is None
        assert cfg.telegram is False
        assert cfg.min_sl_pct == 0.0
        assert cfg.tp_r == 2.0
        assert cfg.sl_pct == 0.02
        assert cfg.state_file == "signal_state.json"
        assert cfg.day_filter == "off"


class TestDayFilterToWeekdays:
    def test_off_returns_none(self) -> None:
        assert _day_filter_to_weekdays("off") is None

    def test_weekdays_returns_mon_fri(self) -> None:
        assert _day_filter_to_weekdays("weekdays") == [0, 1, 2, 3, 4]

    def test_tue_thu_returns_tue_wed_thu(self) -> None:
        assert _day_filter_to_weekdays("tue_thu") == [1, 2, 3]

    def test_unknown_string_returns_none(self) -> None:
        assert _day_filter_to_weekdays("unknown") is None


class TestLoadSignalConfig:
    def test_minimal_config(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, 'timeframes = ["15m", "1h"]\ntelegram = true\n')
        cfg = load_signal_config(p)
        assert cfg.timeframes == ["15m", "1h"]
        assert cfg.telegram is True
        # unset fields fall back to defaults
        assert cfg.symbols is None
        assert cfg.min_sl_pct == 0.0

    def test_full_config(self, tmp_path: Path) -> None:
        content = """
symbols = ["BTCUSDT", "ETHUSDT"]
timeframes = ["15m", "1h", "4h", "1d"]
strategies = ["fvg", "bos"]
telegram = true
min_sl_pct = 0.01
tp_r = 3.0
sl_pct = 0.015
state_file = "my_state.json"
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.symbols == ["BTCUSDT", "ETHUSDT"]
        assert cfg.timeframes == ["15m", "1h", "4h", "1d"]
        assert cfg.strategies == ["fvg", "bos"]
        assert cfg.telegram is True
        assert cfg.min_sl_pct == 0.01
        assert cfg.tp_r == 3.0
        assert cfg.sl_pct == 0.015
        assert cfg.state_file == "my_state.json"

    def test_full_config_day_filter_string_modes(self, tmp_path: Path) -> None:
        for mode in ("off", "weekdays", "tue_thu"):
            content = f'day_filter = "{mode}"\n'
            p = _write_toml(tmp_path, content)
            cfg = load_signal_config(p)
            assert cfg.day_filter == mode

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_signal_config(tmp_path / "nonexistent.toml")

    def test_invalid_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.toml"
        p.write_text("timeframes = [broken")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_signal_config(p)

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, "telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.telegram is True

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, "telegram = false\n")
        cfg = load_signal_config(str(p))
        assert cfg.telegram is False

    def test_default_config_file_is_valid(self) -> None:
        """The committed config/signal_watch.toml must parse without errors."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        assert cfg.timeframes == ["4h", "1d", "1wk"]
        assert cfg.telegram is True
        assert cfg.min_sl_pct == 0.005

    def test_strategy_timeframes_parsed(self, tmp_path: Path) -> None:
        content = '[strategy_timeframes]\ntrend_day = ["4h", "1d"]\nmarubozu = ["1d"]\n'
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.strategy_timeframes == {
            "trend_day": ["4h", "1d"],
            "marubozu": ["1d"],
        }

    def test_strategy_timeframes_defaults_to_empty(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, "telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.strategy_timeframes == {}

    def test_invalid_strategy_timeframes_not_table(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, 'strategy_timeframes = "not a table"\n')
        with pytest.raises(
            ValueError, match="strategy_timeframes must be a TOML table"
        ):
            load_signal_config(p)

    def test_signal_watch_toml_orb_4h_only(self) -> None:
        """signal_watch.toml must restrict ORB to 4h on equity (mechanical: 1d sessions hold one candle)."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        assert "orb" in cfg.strategy_timeframes
        assert cfg.strategy_timeframes["orb"] == ["4h"]


class TestBacktestFilterConfigPerTf:
    def test_effective_min_trades_uses_per_tf_override(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = BacktestFilterConfig(min_trades=20, min_trades_per_tf={"4h": 10, "1d": 5})
        assert cfg.effective_min_trades("4h") == 10
        assert cfg.effective_min_trades("1d") == 5

    def test_effective_min_trades_falls_back_to_global(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = BacktestFilterConfig(min_trades=20, min_trades_per_tf={"4h": 10})
        assert cfg.effective_min_trades("1h") == 20

    def test_effective_min_trades_empty_per_tf(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = BacktestFilterConfig(min_trades=15)
        assert cfg.effective_min_trades("15m") == 15
        assert cfg.effective_min_trades("1d") == 15

    def test_load_signal_config_parses_per_tf_keys(self, tmp_path: Path) -> None:
        content = """
[backtest]
mode = "soft"
min_trades = 20
min_trades_15m = 30
min_trades_4h = 10
min_trades_1d = 5
"""
        p = tmp_path / "w.toml"
        p.write_text(content)
        cfg = load_signal_config(p)
        assert cfg.backtest.min_trades_per_tf == {"15m": 30, "4h": 10, "1d": 5}
        assert cfg.backtest.effective_min_trades("15m") == 30
        assert cfg.backtest.effective_min_trades("4h") == 10
        assert cfg.backtest.effective_min_trades("1d") == 5
        assert cfg.backtest.effective_min_trades("1h") == 20  # falls back to global

    def test_signal_watch_toml_has_per_tf_min_trades(self) -> None:
        """The committed signal_watch.toml must define per-TF min_trades overrides."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # [backtest] section uses directional counts (longs or shorts only).
        # T13 (2026-05-17): equity TFs only — 4h/1d/1wk. T14 may recalibrate.
        assert cfg.backtest.effective_min_trades("4h") == 5
        assert cfg.backtest.effective_min_trades("1d") == 2
        assert cfg.backtest.effective_min_trades("1wk") == 1


class TestStrategyOverride:
    def test_effective_tp_r_tf_specific(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "bos": StrategyOverride(tp_r=2.5, tp_r_per_tf={"4h": 2.5, "1h": 2.0}),
            },
        )
        assert cfg.effective_tp_r("bos", "BTCUSDT", "4h") == 2.5
        assert cfg.effective_tp_r("bos", "BTCUSDT", "1h") == 2.0

    def test_effective_tp_r_strategy_wide(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={"engulfing": StrategyOverride(tp_r=3.0)},
        )
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "1h") == 3.0
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "4h") == 3.0

    def test_effective_tp_r_falls_back_to_global(self) -> None:
        cfg = SignalWatchConfig(tp_r=2.0, strategy_params={})
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "1h") == 2.0

    def test_effective_tp_r_tf_specific_overrides_strategy_wide(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "pin_bar": StrategyOverride(tp_r=3.0, tp_r_per_tf={"4h": 2.5})
            },
        )
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "4h") == 2.5  # TF-specific wins
        assert (
            cfg.effective_tp_r("pin_bar", "BTCUSDT", "1h") == 3.0
        )  # strategy-wide fallback
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "1d") == 3.0

    def test_effective_sl_pct_tf_specific(self) -> None:
        cfg = SignalWatchConfig(
            sl_pct=0.02,
            strategy_params={
                "orb": StrategyOverride(sl_pct_per_tf={"1h": 0.015}),
            },
        )
        assert cfg.effective_sl_pct("orb", "BTCUSDT", "1h") == 0.015
        assert cfg.effective_sl_pct("orb", "BTCUSDT", "4h") == 0.02  # global fallback

    def test_effective_sl_pct_no_override(self) -> None:
        cfg = SignalWatchConfig(sl_pct=0.025)
        assert cfg.effective_sl_pct("fvg", "BTCUSDT", "1h") == 0.025

    def test_load_strategy_params_sub_table(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.engulfing]
tp_r = 3.0

[strategy_params.bos]
tp_r_4h = 2.5
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert "engulfing" in cfg.strategy_params
        assert cfg.strategy_params["engulfing"].tp_r == 3.0
        assert cfg.strategy_params["engulfing"].tp_r_per_tf == {}
        assert "bos" in cfg.strategy_params
        assert cfg.strategy_params["bos"].tp_r is None
        assert cfg.strategy_params["bos"].tp_r_per_tf == {"4h": 2.5}

    def test_load_strategy_params_inline_table(self, tmp_path: Path) -> None:
        content = "[strategy_params]\nengulfing = {tp_r = 3.0}\n"
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.strategy_params["engulfing"].tp_r == 3.0

    def test_load_strategy_params_defaults_to_empty(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, "telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.strategy_params == {}

    def test_signal_watch_weekdays_toml_strategy_params_parsed(self) -> None:
        """signal_watch_weekdays.toml strategy_params must be applied (equity surface)."""
        cfg_path = (
            Path(__file__).parent.parent / "config" / "signal_watch_weekdays.toml"
        )
        cfg = load_signal_config(cfg_path)
        # engulfing: 4h combined 3.0 (Task A 4-sym n=87 supersedes T14 AAPL n=21);
        # 1wk falls back to strategy-wide 3.5.
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h") == 3.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "1wk") == 3.5
        # engulfing 1d directional split (Task A Δ=0.70R): long=3.0, short=2.5.
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d", direction="long") == 3.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d", direction="short") == 2.5
        # trend_day 1wk long directional override; short falls back to combined 3.0.
        assert cfg.effective_tp_r("trend_day", "AAPL", "1wk", direction="long") == 2.5
        assert (
            cfg.effective_tp_r("trend_day", "AAPL", "1wk", direction="short")
            == cfg.tp_r
        )
        # morning_evening_star: stale crypto tp_r_long=4.0 retired (now falls
        # through to per-TF combined 3.5 for 1d longs).
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1d", direction="long")
            == 3.5
        )
        # ema 1d long directional override (Task A: +0.920R, n=25).
        assert cfg.effective_tp_r("ema", "AAPL", "1d", direction="long") == 5.0
        # strategy not in params falls back to global.
        assert cfg.effective_tp_r("seasonality", "AAPL", "1d") == cfg.tp_r

    def test_signal_watch_toml_strategy_params_parsed(self) -> None:
        """signal_watch.toml (tue_thu) strategy_params must be applied (equity surface)."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # engulfing: 4h override = 3.0; 1d falls back to strategy-wide 3.0 (no direction).
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h") == 3.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d") == 3.0
        # engulfing 4h directional: short override 3.5, long falls back to combined 3.0.
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h", direction="short") == 3.5
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h", direction="long") == 3.0
        # engulfing 1d directional split (Task A Δ=0.78R): long=3.5, short=2.5.
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d", direction="long") == 3.5
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d", direction="short") == 2.5
        # pin_bar: 4h=5.0, 1d=3.5 (T-A 4-sym WFO)
        assert cfg.effective_tp_r("pin_bar", "AAPL", "4h") == 5.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1d") == 3.5
        # hammer_hanging_man: strategy-wide 4.0; 1d override 2.5 (T-A 4-sym WFO)
        assert cfg.effective_tp_r("hammer_hanging_man", "AAPL", "4h") == 4.0
        assert cfg.effective_tp_r("hammer_hanging_man", "AAPL", "1d") == 2.5
        # trend_day: 4h=4.5 (T-A 4-sym confirmed by Task A); 1d directional split.
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h") == 4.5
        assert cfg.effective_tp_r("trend_day", "AAPL", "1d", direction="long") == 5.0
        assert cfg.effective_tp_r("trend_day", "AAPL", "1d", direction="short") == 3.5
        # morning_evening_star: 4h updated 2.0 → 3.5 (Task A 4-sym n=76 supersedes T14).
        assert cfg.effective_tp_r("morning_evening_star", "AAPL", "4h") == 3.5
        # morning_evening_star 1d long directional; short falls back to strategy-wide 3.0.
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1d", direction="long")
            == 5.0
        )
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1d", direction="short")
            == 3.0
        )
        # ema 1d long directional (Task A: +1.769R, n=13).
        assert cfg.effective_tp_r("ema", "AAPL", "1d", direction="long") == 5.0
        # orb: 4h override 3.5 (T-A 4-sym WFO, was 5.0)
        assert cfg.effective_tp_r("orb", "AAPL", "4h") == 3.5
        # strategy not in params (fvg dropped from signal_watch.toml) falls back to global
        assert cfg.effective_tp_r("fvg", "AAPL", "4h") == cfg.tp_r


class TestEffectiveTpRPerSymbol:
    def test_symbol_tf_override_wins_over_strategy_tf(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "doji": StrategyOverride(
                    tp_r_per_tf={"15m": 4.0},
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r_per_tf={"15m": 4.5}),
                    },
                )
            },
        )
        # ETHUSDT symbol+TF override (4.5) wins over strategy-level TF override (4.0)
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        # BTCUSDT has no symbol override — falls back to strategy+TF level
        assert cfg.effective_tp_r("doji", "BTCUSDT", "15m") == 4.0

    def test_symbol_wide_override_wins_over_strategy_tf(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "hammer_hanging_man": StrategyOverride(
                    tp_r=4.0,
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r=5.0),
                    },
                )
            },
        )
        # ETHUSDT symbol-wide (5.0) wins
        assert cfg.effective_tp_r("hammer_hanging_man", "ETHUSDT", "1h") == 5.0
        assert cfg.effective_tp_r("hammer_hanging_man", "ETHUSDT", "15m") == 5.0
        # BTCUSDT falls back to strategy-wide
        assert cfg.effective_tp_r("hammer_hanging_man", "BTCUSDT", "1h") == 4.0

    def test_symbol_tf_wins_over_symbol_wide(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "doji": StrategyOverride(
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r=4.0, tp_r_per_tf={"15m": 4.5}),
                    },
                )
            },
        )
        # symbol+TF (4.5) wins over symbol-wide (4.0)
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        # Other TF falls back to symbol-wide
        assert cfg.effective_tp_r("doji", "ETHUSDT", "1h") == 4.0

    def test_unknown_symbol_falls_through_to_strategy_level(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "doji": StrategyOverride(
                    tp_r=3.0,
                    tp_r_per_tf={"15m": 4.0},
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r_per_tf={"15m": 4.5}),
                    },
                )
            },
        )
        # Unknown symbol SOLUSDT — no symbol override → falls to TF-level (4.0)
        assert cfg.effective_tp_r("doji", "SOLUSDT", "15m") == 4.0
        # Unknown symbol, unknown TF → strategy-wide (3.0)
        assert cfg.effective_tp_r("doji", "SOLUSDT", "1h") == 3.0

    def test_toml_round_trip_per_symbol(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.doji]
tp_r = 3.0
tp_r_15m = 4.0

[strategy_params.doji.ETHUSDT]
tp_r_15m = 4.5

[strategy_params.doji.BTCUSDT]
tp_r_15m = 3.5
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        # ETHUSDT symbol+TF override
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        # BTCUSDT symbol+TF override
        assert cfg.effective_tp_r("doji", "BTCUSDT", "15m") == 3.5
        # SOLUSDT — no symbol override → TF-level fallback
        assert cfg.effective_tp_r("doji", "SOLUSDT", "15m") == 4.0
        # ETHUSDT 1h — no symbol TF override, no symbol-wide → strategy-wide (3.0)
        assert cfg.effective_tp_r("doji", "ETHUSDT", "1h") == 3.0

    def test_signal_watch_toml_has_no_per_symbol_overrides(self) -> None:
        """T13 (2026-05-17) stripped all per-symbol overrides; T14 may reintroduce."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        for strat, override in cfg.strategy_params.items():
            assert override.per_symbol == {}, (
                f"strategy_params.{strat} must not declare per-symbol overrides on equity"
            )


class TestDeepMerge:
    def test_scalar_override_wins(self) -> None:
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_base_key_preserved_when_not_overridden(self) -> None:
        assert _deep_merge({"a": 1, "b": 2}, {"a": 9}) == {"a": 9, "b": 2}

    def test_new_key_added_by_override(self) -> None:
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_list_override_replaces_entirely(self) -> None:
        assert _deep_merge({"x": [1, 2]}, {"x": [3]}) == {"x": [3]}

    def test_nested_dict_merged_key_by_key(self) -> None:
        base = {"s": {"tp_r": 2.0, "adr_exempt": True}}
        override = {"s": {"tp_r": 3.0}}
        result = _deep_merge(base, override)
        assert result == {"s": {"tp_r": 3.0, "adr_exempt": True}}

    def test_deeply_nested_merge(self) -> None:
        base = {"strategy_params": {"bos": {"volume_suppress": True, "tp_r": 3.0}}}
        override = {"strategy_params": {"bos": {"tp_r": 4.0}}}
        result = _deep_merge(base, override)
        assert result["strategy_params"]["bos"] == {
            "volume_suppress": True,
            "tp_r": 4.0,
        }

    def test_empty_override_returns_base(self) -> None:
        base = {"a": 1, "b": 2}
        assert _deep_merge(base, {}) == base

    def test_empty_base_returns_override(self) -> None:
        assert _deep_merge({}, {"a": 1}) == {"a": 1}


class TestLoadWithExtends:
    def test_child_inherits_base_scalar(self, tmp_path: Path) -> None:
        base = tmp_path / "base.toml"
        base.write_text("telegram = true\nfee_pct = 0.0005\n")
        child = tmp_path / "child.toml"
        child.write_text('extends = "base.toml"\ntimeframes = ["1h"]\n')
        cfg = load_signal_config(child)
        assert cfg.telegram is True
        assert cfg.timeframes == ["1h"]

    def test_child_overrides_base_scalar(self, tmp_path: Path) -> None:
        base = tmp_path / "base.toml"
        base.write_text('telegram = false\ntimeframes = ["4h"]\n')
        child = tmp_path / "child.toml"
        child.write_text('extends = "base.toml"\ntelegram = true\n')
        cfg = load_signal_config(child)
        assert cfg.telegram is True
        assert cfg.timeframes == ["4h"]  # inherited from base

    def test_child_strategy_params_merged_with_base(self, tmp_path: Path) -> None:
        base = tmp_path / "base.toml"
        base.write_text("[strategy_params.bos]\nvolume_suppress = true\n")
        child = tmp_path / "child.toml"
        child.write_text('extends = "base.toml"\n[strategy_params.bos]\ntp_r = 3.0\n')
        cfg = load_signal_config(child)
        assert cfg.effective_volume_suppress("bos") is True  # from base
        assert cfg.effective_tp_r("bos", "BTCUSDT", "1h") == 3.0  # from child

    def test_child_list_replaces_base_list(self, tmp_path: Path) -> None:
        base = tmp_path / "base.toml"
        base.write_text('strategies = ["fvg", "bos"]\n')
        child = tmp_path / "child.toml"
        child.write_text('extends = "base.toml"\nstrategies = ["engulfing"]\n')
        cfg = load_signal_config(child)
        assert cfg.strategies == ["engulfing"]

    def test_extends_key_not_in_parsed_result(self, tmp_path: Path) -> None:
        base = tmp_path / "base.toml"
        base.write_text("telegram = true\n")
        child = tmp_path / "child.toml"
        child.write_text('extends = "base.toml"\n')
        # should not raise — 'extends' key must be consumed before parsing
        cfg = load_signal_config(child)
        assert cfg.telegram is True

    def test_no_extends_loads_normally(self, tmp_path: Path) -> None:
        child = tmp_path / "child.toml"
        child.write_text('telegram = true\ntimeframes = ["15m"]\n')
        cfg = load_signal_config(child)
        assert cfg.telegram is True
        assert cfg.timeframes == ["15m"]

    def test_signal_watch_toml_extends_base(self) -> None:
        """signal_watch.toml must load correctly via the extends mechanism."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # inherited from base
        assert cfg.bias.adr_suppress_threshold == 0.80
        assert cfg.backtest.effective_min_trades("4h") == 5
        assert cfg.backtest.effective_min_trades("1wk") == 1
        # merged: base volume_suppress + child tp_r
        assert cfg.effective_volume_suppress("bos") is True
        assert cfg.effective_tp_r("bos", "AAPL", "4h") == 3.0
        # F8 HTF EMA gate inherited from base — enabled in hard mode after the
        # 2026-05-06 soft-mode validation; per-strategy overrides loaded for the
        # strategies that prefer 1d EMA-50.
        assert cfg.bias.htf_ema_enabled is True
        assert cfg.bias.htf_ema_mode == "hard"
        assert cfg.bias.htf_ema_default_tf == "4h"
        assert cfg.bias.htf_ema_default_period == 50
        assert cfg.bias.htf_ema_deadband_pct == 0.003
        # default anchor for non-overridden strategy
        anchor_default = cfg.bias.htf_ema_anchor("bos")
        assert anchor_default.tf == "4h" and anchor_default.period == 50
        # override anchor for ema (1d EMA-50)
        anchor_ema = cfg.bias.htf_ema_anchor("ema")
        assert anchor_ema.tf == "1d" and anchor_ema.period == 50
        for strat in ("orb", "eqh_eql", "marubozu"):
            assert cfg.bias.htf_ema_anchor(strat).tf == "1d", (
                f"{strat} should override to 1d anchor"
            )
        # T2c direction filter (soft mode) inherited from base — gate enabled
        # so suppress_long / suppress_short on per-strategy blocks fires.
        assert cfg.bias.direction_filter_enabled is True
        assert cfg.bias.direction_filter_mode == "soft"
        # bos long-side suppress flag carried through the parser.
        bos_override = cfg.strategy_params.get("bos")
        assert bos_override is not None
        assert bos_override.suppress_long is True
        assert bos_override.suppress_short is False


class TestDirectionFilterParsing:
    def test_toml_round_trip_direction_filter(self, tmp_path: Path) -> None:
        content = """\
[bias.direction_filter]
enabled = true
mode = "hard"

[strategy_params.bos]
suppress_long = true

[strategy_params.engulfing]
suppress_short = true

[strategy_params.pin_bar]
suppress_long = true
suppress_short = true
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.bias.direction_filter_enabled is True
        assert cfg.bias.direction_filter_mode == "hard"
        assert cfg.strategy_params["bos"].suppress_long is True
        assert cfg.strategy_params["bos"].suppress_short is False
        assert cfg.strategy_params["engulfing"].suppress_long is False
        assert cfg.strategy_params["engulfing"].suppress_short is True
        assert cfg.strategy_params["pin_bar"].suppress_long is True
        assert cfg.strategy_params["pin_bar"].suppress_short is True

    def test_default_is_off_when_block_missing(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.bos]
tp_r = 3.0
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.bias.direction_filter_enabled is False
        assert cfg.bias.direction_filter_mode == "soft"
        assert cfg.strategy_params["bos"].suppress_long is False
        assert cfg.strategy_params["bos"].suppress_short is False


class TestEffectiveVolumeSuppress:
    def test_per_strategy_true_overrides_global_false(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = SignalWatchConfig(
            backtest=BacktestFilterConfig(volume_suppress=False),
            strategy_params={"bos": StrategyOverride(volume_suppress=True)},
        )
        assert cfg.effective_volume_suppress("bos") is True

    def test_per_strategy_false_overrides_global_true(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = SignalWatchConfig(
            backtest=BacktestFilterConfig(volume_suppress=True),
            strategy_params={"pin_bar": StrategyOverride(volume_suppress=False)},
        )
        assert cfg.effective_volume_suppress("pin_bar") is False

    def test_none_falls_back_to_global_true(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = SignalWatchConfig(
            backtest=BacktestFilterConfig(volume_suppress=True),
            strategy_params={"engulfing": StrategyOverride(volume_suppress=None)},
        )
        assert cfg.effective_volume_suppress("engulfing") is True

    def test_missing_strategy_falls_back_to_global(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = SignalWatchConfig(
            backtest=BacktestFilterConfig(volume_suppress=True),
            strategy_params={},
        )
        assert cfg.effective_volume_suppress("orb") is True

    def test_global_false_no_override_returns_false(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = SignalWatchConfig(
            backtest=BacktestFilterConfig(volume_suppress=False),
            strategy_params={},
        )
        assert cfg.effective_volume_suppress("marubozu") is False

    def test_toml_round_trip_volume_suppress(self, tmp_path: Path) -> None:
        content = """\
[backtest]
volume_suppress = false

[strategy_params.bos]
tp_r = 3.0
volume_suppress = true

[strategy_params.pin_bar]
tp_r = 3.0
volume_suppress = false

[strategy_params.doji]
tp_r = 3.0
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        # per-strategy true overrides global false
        assert cfg.effective_volume_suppress("bos") is True
        # per-strategy false (explicit)
        assert cfg.effective_volume_suppress("pin_bar") is False
        # no volume_suppress in block → falls back to global false
        assert cfg.effective_volume_suppress("doji") is False
        # strategy not in params → falls back to global false
        assert cfg.effective_volume_suppress("orb") is False

    def test_signal_watch_toml_volume_suppress_flags(self) -> None:
        """signal_watch.toml A14b volume_suppress flags must be parsed correctly."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # suppress = true: strategies where normal-vol signals outperform
        assert cfg.effective_volume_suppress("bos") is True
        assert cfg.effective_volume_suppress("orb") is True
        assert cfg.effective_volume_suppress("doji") is True
        assert cfg.effective_volume_suppress("liquidity_sweep") is True
        assert cfg.effective_volume_suppress("fib_golden_zone") is True
        # suppress = false: strategies where low-vol signals have edge
        assert cfg.effective_volume_suppress("pin_bar") is False
        assert cfg.effective_volume_suppress("hammer_hanging_man") is False
        assert cfg.effective_volume_suppress("marubozu") is False
        assert cfg.effective_volume_suppress("morning_evening_star") is False
        # neutral strategies (no flag) → fall back to global default (false)
        assert cfg.effective_volume_suppress("engulfing") is False
        assert cfg.effective_volume_suppress("eqh_eql") is False


class TestDirectionalTpR:
    """Gate 3: direction-split tp_r and min_avg_r."""

    def test_effective_tp_r_directional_long(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.5,
            strategy_params={
                "bos": StrategyOverride(tp_r=2.5, tp_r_long=1.8, tp_r_short=2.5)
            },
        )
        assert cfg.effective_tp_r("bos", "BTCUSDT", "1h", direction="long") == 1.8
        assert cfg.effective_tp_r("bos", "BTCUSDT", "1h", direction="short") == 2.5
        # no direction → strategy-wide tp_r
        assert cfg.effective_tp_r("bos", "BTCUSDT", "1h") == 2.5

    def test_effective_tp_r_directional_only_long_set(self) -> None:
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={"engulfing": StrategyOverride(tp_r=3.0, tp_r_long=2.0)},
        )
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "1h", direction="long") == 2.0
        # short not set → falls back to strategy-wide tp_r
        assert (
            cfg.effective_tp_r("engulfing", "BTCUSDT", "1h", direction="short") == 3.0
        )

    def test_effective_tp_r_tf_specific_beats_directional(self) -> None:
        """TF-specific override takes priority over directional."""
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "bos": StrategyOverride(
                    tp_r=2.5, tp_r_long=1.8, tp_r_per_tf={"1h": 3.0}
                )
            },
        )
        # TF-specific wins even when direction="long"
        assert cfg.effective_tp_r("bos", "BTCUSDT", "1h", direction="long") == 3.0
        # No TF override on 4h → directional applies
        assert cfg.effective_tp_r("bos", "BTCUSDT", "4h", direction="long") == 1.8

    def test_effective_tp_r_no_override_direction_falls_back_to_global(self) -> None:
        cfg = SignalWatchConfig(tp_r=2.0, strategy_params={})
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "1h", direction="long") == 2.0
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "1h", direction="short") == 2.0

    def test_load_tp_r_long_short_from_toml(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.bos]
tp_r = 2.5
tp_r_long = 1.8
tp_r_short = 2.5
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        override = cfg.strategy_params["bos"]
        assert override.tp_r == 2.5
        assert override.tp_r_long == 1.8
        assert override.tp_r_short == 2.5
        # tp_r_long/short must NOT appear in tp_r_per_tf
        assert "long" not in override.tp_r_per_tf
        assert "short" not in override.tp_r_per_tf

    def test_load_tp_r_long_short_not_in_tp_r_per_tf(self, tmp_path: Path) -> None:
        """tp_r_long and tp_r_short must be excluded from tp_r_per_tf dict."""
        content = """\
[strategy_params.engulfing]
tp_r = 3.0
tp_r_long = 2.5
tp_r_short = 3.5
tp_r_4h = 4.0
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        override = cfg.strategy_params["engulfing"]
        assert override.tp_r_per_tf == {"4h": 4.0}
        assert override.tp_r_long == 2.5
        assert override.tp_r_short == 3.5

    def test_effective_tp_r_per_tf_directional_beats_per_tf_combined(self) -> None:
        """tp_r_long_4h / tp_r_short_4h beat tp_r_4h on the same TF."""
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "trend_day": StrategyOverride(
                    tp_r_per_tf={"4h": 4.0},
                    tp_r_long_per_tf={"4h": 3.5},
                    tp_r_short_per_tf={"4h": 5.0},
                )
            },
        )
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h", direction="long") == 3.5
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h", direction="short") == 5.0
        # No direction → falls through per-TF directional to per-TF combined.
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h") == 4.0

    def test_effective_tp_r_per_tf_directional_does_not_leak_to_other_tfs(
        self,
    ) -> None:
        """tp_r_long_4h must NOT affect 1d / 1wk trades."""
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "ema": StrategyOverride(
                    tp_r=3.0,
                    tp_r_long_per_tf={"4h": 2.0},
                )
            },
        )
        # 4h long uses the per-TF directional override.
        assert cfg.effective_tp_r("ema", "AAPL", "4h", direction="long") == 2.0
        # 1d long has no per-TF directional → falls back to strategy-wide tp_r.
        assert cfg.effective_tp_r("ema", "AAPL", "1d", direction="long") == 3.0
        # 1wk same.
        assert cfg.effective_tp_r("ema", "AAPL", "1wk", direction="long") == 3.0

    def test_effective_tp_r_per_tf_directional_supports_1wk(self) -> None:
        """Per-TF directional must work for 1wk (Task C: 1wk under coverage)."""
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "engulfing": StrategyOverride(
                    tp_r=3.0,
                    tp_r_short_per_tf={"1wk": 4.5},
                )
            },
        )
        assert cfg.effective_tp_r("engulfing", "AAPL", "1wk", direction="short") == 4.5
        # Long on 1wk falls back to strategy-wide.
        assert cfg.effective_tp_r("engulfing", "AAPL", "1wk", direction="long") == 3.0

    def test_effective_tp_r_per_tf_directional_falls_back_to_flat_directional(
        self,
    ) -> None:
        """When per-TF directional is absent, flat tp_r_long / tp_r_short still applies."""
        cfg = SignalWatchConfig(
            tp_r=2.0,
            strategy_params={
                "morning_evening_star": StrategyOverride(
                    tp_r=3.0,
                    tp_r_long=4.0,
                    tp_r_long_per_tf={"4h": 2.5},
                )
            },
        )
        # 4h long: per-TF directional wins.
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "4h", direction="long")
            == 2.5
        )
        # 1d long: no per-TF directional → flat tp_r_long applies.
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1d", direction="long")
            == 4.0
        )

    def test_load_tp_r_long_4h_from_toml(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.trend_day]
tp_r_4h = 4.0
tp_r_long_4h = 3.5
tp_r_short_4h = 5.0
tp_r_long_1wk = 2.0
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        override = cfg.strategy_params["trend_day"]
        assert override.tp_r_per_tf == {"4h": 4.0}
        assert override.tp_r_long_per_tf == {"4h": 3.5, "1wk": 2.0}
        assert override.tp_r_short_per_tf == {"4h": 5.0}

    def test_load_tp_r_long_4h_not_in_tp_r_per_tf(self, tmp_path: Path) -> None:
        """tp_r_long_<tf> / tp_r_short_<tf> must be excluded from tp_r_per_tf
        (else they'd be mis-parsed as tp_r_per_tf['long_4h']).
        """
        content = """\
[strategy_params.engulfing]
tp_r_4h = 3.5
tp_r_long_4h = 2.0
tp_r_short_4h = 4.5
tp_r_long_1wk = 1.5
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        override = cfg.strategy_params["engulfing"]
        # tp_r_per_tf must NOT carry "long_4h" / "short_4h" / "long_1wk".
        assert override.tp_r_per_tf == {"4h": 3.5}
        assert "long_4h" not in override.tp_r_per_tf
        assert "short_4h" not in override.tp_r_per_tf
        assert "long_1wk" not in override.tp_r_per_tf
        # And the directional per-TF dicts are correctly populated.
        assert override.tp_r_long_per_tf == {"4h": 2.0, "1wk": 1.5}
        assert override.tp_r_short_per_tf == {"4h": 4.5}

    def test_min_avg_r_directional_parsed_from_toml(self, tmp_path: Path) -> None:
        content = """\
[backtest]
mode = "hard"
min_avg_r = 0.0
min_avg_r_long = -0.1
min_avg_r_short = 0.2
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.backtest.min_avg_r == 0.0
        assert cfg.backtest.min_avg_r_long == -0.1
        assert cfg.backtest.min_avg_r_short == 0.2

    def test_min_avg_r_directional_defaults_to_none(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        cfg = BacktestFilterConfig()
        assert cfg.min_avg_r_long is None
        assert cfg.min_avg_r_short is None

    def test_cache_enabled_defaults_true(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        assert BacktestFilterConfig().cache_enabled is True

    def test_cache_enabled_can_be_disabled(self) -> None:
        from analytics.signal_config import BacktestFilterConfig

        assert BacktestFilterConfig(cache_enabled=False).cache_enabled is False
