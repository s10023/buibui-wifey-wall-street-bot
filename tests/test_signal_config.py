"""Tests for analytics/signal_config.py — pure config loader."""

import tomllib
from pathlib import Path

import pytest

from analytics.backtest.cost_model import CostModel
from analytics.signal_config import (
    SignalWatchConfig,
    StrategyOverride,
    SymbolOverride,
    _day_filter_to_weekdays,
    _deep_merge,
    dead_timeframes,
    load_signal_config,
    voided_volume_gates,
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
        # "1wk" dropped 2026-08-06 — day_filter="tue_thu" discarded every weekly
        # bar (all stamped Monday), so it scanned and dispatched nothing. See
        # TestDeadTimeframes; load_signal_config now refuses the pairing outright.
        assert cfg.timeframes == ["4h", "1d"]
        # Telegram is off by default in the committed config; the --telegram CLI
        # flag (TELEGRAM=1 / `make go-live`) is the single master switch.
        assert cfg.telegram is False
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
        # engulfing live-parity re-derive (2026-06-03): 4h combined 4.0 (was 3.0; n=75);
        # 1d combined 5.0 (was 2.5; n=24, long_1d dropped n=18<20); 1wk combined 4.0 kept
        # (n<10) with long_1wk/short_1wk dropped (raw n=24/37 inflated).
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h") == 4.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "1wk") == 4.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "1wk", direction="long") == 4.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "1wk", direction="short") == 4.0
        # engulfing 4h directional: short_4h dropped (live short n=42→4.0 = combined);
        # both directions fall back to combined 4.0.
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h", direction="short") == 4.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h", direction="long") == 4.0
        # engulfing 1d directional: long_1d dropped (live long n=18<20); both fall to combined 5.0.
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d", direction="long") == 5.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d", direction="short") == 5.0
        # trend_day Phase 2 resweep (under ATR floor): 1wk combined 3.0 NEW (was fallback 2.0)
        # + long 4.5 (was 4.0; ATR floor pulled winner 0.5 step wider) + short 1.5 (confirmed).
        assert cfg.effective_tp_r("trend_day", "AAPL", "1wk") == 3.0
        assert cfg.effective_tp_r("trend_day", "AAPL", "1wk", direction="long") == 4.5
        assert cfg.effective_tp_r("trend_day", "AAPL", "1wk", direction="short") == 1.5
        # trend_day 4h combined 3.0 (confirms Task E); tp_r_short_4h dropped — short
        # winner = combined → falls back to tp_r_4h.
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h") == 3.0
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h", direction="short") == 3.0
        # trend_day 1d Phase 2 resweep: combined 3.5 (was 5.0; long-driven so combined
        # tightens) + tp_r_long_1d=5.0 NEW; tp_r_short_1d dropped (short no_edge under floor).
        assert cfg.effective_tp_r("trend_day", "AAPL", "1d") == 3.5
        assert cfg.effective_tp_r("trend_day", "AAPL", "1d", direction="long") == 5.0
        assert cfg.effective_tp_r("trend_day", "AAPL", "1d", direction="short") == 3.5
        # morning_evening_star Phase 2 resweep: 1d combined 2.0 (confirmed); long 3.5
        # (was 3.0; ATR floor pulled winner 0.5 step wider); short 1.5 (confirmed).
        assert cfg.effective_tp_r("morning_evening_star", "AAPL", "1d") == 2.0
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1d", direction="long")
            == 3.5
        )
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1d", direction="short")
            == 1.5
        )
        # morning_evening_star 1wk Phase 2 resweep: combined 1.0 NEW (was no commit);
        # long-only 3.5 kept.
        assert cfg.effective_tp_r("morning_evening_star", "AAPL", "1wk") == 1.0
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1wk", direction="long")
            == 3.5
        )
        # morning_evening_star 4h Phase 2 resweep: combined 1.5 (was 3.0; ATR floor
        # inverts edge — tightest tp_r wins); short 1.0 (was 3.5; same direction).
        assert cfg.effective_tp_r("morning_evening_star", "AAPL", "4h") == 1.5
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "4h", direction="short")
            == 1.0
        )
        # ema live-parity re-derive (2026-06-03): 1d combined 4.5 (was 4.0; n=20),
        # long_1d/short_1d dropped (live long n=15<20, short n<10) → fall to 4.5.
        assert cfg.effective_tp_r("ema", "AAPL", "1d") == 4.5
        assert cfg.effective_tp_r("ema", "AAPL", "1d", direction="long") == 4.5
        assert cfg.effective_tp_r("ema", "AAPL", "1d", direction="short") == 4.5
        # ema 4h combined 3.0 (was 1.5; n=45) + tp_r_long_4h=3.5 (n=20) + tp_r_short_4h=1.0 (n=25).
        assert cfg.effective_tp_r("ema", "AAPL", "4h") == 3.0
        assert cfg.effective_tp_r("ema", "AAPL", "4h", direction="long") == 3.5
        assert cfg.effective_tp_r("ema", "AAPL", "4h", direction="short") == 1.0
        # hammer_hanging_man Phase 2 resweep: 4h combined 3.5 (was 3.0); 1d combined 2.0
        # NEW (was fallback 4.0) + tp_r_long_1d=2.5 NEW.
        assert cfg.effective_tp_r("hammer_hanging_man", "AAPL", "4h") == 3.5
        assert cfg.effective_tp_r("hammer_hanging_man", "AAPL", "1d") == 2.0
        assert (
            cfg.effective_tp_r("hammer_hanging_man", "AAPL", "1d", direction="long")
            == 2.5
        )
        # bos Phase 2 resweep: 1d combined 2.5 kept (no_edge under floor) + tp_r_long_1d=4.0 NEW.
        assert cfg.effective_tp_r("bos", "AAPL", "1d") == 2.5
        assert cfg.effective_tp_r("bos", "AAPL", "1d", direction="long") == 4.0
        # orb 4h live-parity re-derive (2026-06-03): combined 3.0 (was 2.5; n=54),
        # long_4h dropped (live long n=29→3.0 = combined), short_4h 2.0 (was 1.5; n=25).
        assert cfg.effective_tp_r("orb", "AAPL", "4h") == 3.0
        assert cfg.effective_tp_r("orb", "AAPL", "4h", direction="long") == 3.0
        assert cfg.effective_tp_r("orb", "AAPL", "4h", direction="short") == 2.0
        # doji live-parity re-derive (2026-06-03): 4h combined 4.5 NEW (raw was no_edge,
        # live n=30); 1d 5.0 kept (live n=13<20, flagged); 1wk 4.0 kept (live n<10).
        assert cfg.effective_tp_r("doji", "AAPL", "4h") == 4.5
        assert cfg.effective_tp_r("doji", "AAPL", "1d") == 5.0
        assert cfg.effective_tp_r("doji", "AAPL", "1wk") == 4.0
        # eqh_eql Phase 2 resweep: 4h tp_r_short_4h=5.0 NEW (combined no_edge stays at 2.0).
        assert cfg.effective_tp_r("eqh_eql", "AAPL", "4h", direction="short") == 5.0
        # order_block Phase 2 resweep: 1d combined 3.0 confirmed; tp_r_short_1d=2.5 NEW;
        # tp_r_short_4h=1.5 NEW (combined no_edge stays at fallback 2.0).
        assert cfg.effective_tp_r("order_block", "AAPL", "1d") == 3.0
        assert cfg.effective_tp_r("order_block", "AAPL", "1d", direction="short") == 2.5
        assert cfg.effective_tp_r("order_block", "AAPL", "4h", direction="short") == 1.5
        # order_block 1wk: tp_r_long_1wk=2.5 NEW (combined no_edge under floor).
        assert cfg.effective_tp_r("order_block", "AAPL", "1wk", direction="long") == 2.5
        # inside_bar candle-resweep 13-sym: 4h combined 3.0 (confirms inside_bar audit;
        # long no_edge, short=combined); 1d combined 2.5 (was 2.0; long winner=combined
        # → drop tp_r_long_1d, short no_edge → falls to combined); 1wk combined 1.5
        # (was 4.5; ATR floor 2.5× pulled SL much wider → tighter tp_r wins; long
        # winner=combined → drop tp_r_long_1wk, short no_edge).
        assert cfg.effective_tp_r("inside_bar", "AAPL", "4h") == 3.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "4h", direction="long") == 3.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "4h", direction="short") == 3.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1d") == 2.5
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1d", direction="long") == 2.5
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1d", direction="short") == 2.5
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1wk") == 1.5
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1wk", direction="long") == 1.5
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1wk", direction="short") == 1.5
        # pin_bar candle-resweep 13-sym: 4h combined 2.0 (was 3.5; ATR floor 1.5×
        # pulled SL wider → tighter tp_r wins) + tp_r_long_4h=3.0 kept (differs from
        # combined 2.0); 1d combined 2.5 (confirms; long winner=combined → drop
        # tp_r_long_1d); 1wk combined 4.5 kept (combined no_edge) + tp_r_long_1wk=3.5
        # (was 3.0; thin sample n=10 +0.800R under ATR floor); tp_r_short_1wk dropped
        # (was 5.0; short no_edge under ATR floor).
        assert cfg.effective_tp_r("pin_bar", "AAPL", "4h") == 2.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "4h", direction="long") == 3.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "4h", direction="short") == 2.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1d") == 2.5
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1d", direction="long") == 2.5
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1d", direction="short") == 2.5
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1wk") == 4.5
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1wk", direction="long") == 3.5
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1wk", direction="short") == 4.5
        # strategy not in params falls back to global.
        assert cfg.effective_tp_r("seasonality", "AAPL", "1d") == cfg.tp_r

    def test_signal_watch_toml_strategy_params_parsed(self) -> None:
        """signal_watch.toml (tue_thu) strategy_params must be applied (equity surface)."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # engulfing live-parity re-derive (2026-06-03): 4h combined 4.0 (was 2.5; n=44)
        # + tp_r_short_4h=3.0 (n=26, differs); 1d combined 2.5 kept (live n=16<20),
        # long_1d/short_1d dropped (raw n=59/77 inflated).
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h") == 4.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d") == 2.5
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h", direction="short") == 3.0
        assert cfg.effective_tp_r("engulfing", "AAPL", "4h", direction="long") == 4.0
        # engulfing 1d directionals dropped → both fall to combined 2.5.
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d", direction="long") == 2.5
        assert cfg.effective_tp_r("engulfing", "AAPL", "1d", direction="short") == 2.5
        # pin_bar candle-resweep 13-sym: 4h combined 2.0 (was 3.5; ATR floor 1.5×
        # pulled SL wider → tighter tp_r wins; long no_edge, short=combined → no
        # directional override); 1d combined 2.5 (was 3.5) + tp_r_long_1d=5.0 kept
        # (short winner=combined → drop tp_r_short_1d); 1wk falls back to
        # strategy-wide tp_r=3.0 (n<10 — Fri close × tue_thu suppression).
        assert cfg.effective_tp_r("pin_bar", "AAPL", "4h") == 2.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "4h", direction="long") == 2.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "4h", direction="short") == 2.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1d") == 2.5
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1d", direction="long") == 5.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1d", direction="short") == 2.5
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1wk") == 3.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1wk", direction="long") == 3.0
        assert cfg.effective_tp_r("pin_bar", "AAPL", "1wk", direction="short") == 3.0
        # hammer_hanging_man Phase 2 resweep: 4h combined 3.5 (was 4.0; ATR floor
        # 1.5× pulled SL wider → tighter tp_r wins); 1d combined 2.0 (was 2.5);
        # tp_r_long_1d=2.5 NEW + tp_r_short_1d=1.5 NEW (directional split).
        assert cfg.effective_tp_r("hammer_hanging_man", "AAPL", "4h") == 3.5
        assert cfg.effective_tp_r("hammer_hanging_man", "AAPL", "1d") == 2.0
        assert (
            cfg.effective_tp_r("hammer_hanging_man", "AAPL", "1d", direction="long")
            == 2.5
        )
        assert (
            cfg.effective_tp_r("hammer_hanging_man", "AAPL", "1d", direction="short")
            == 1.5
        )
        # trend_day Phase 2 resweep: 4h combined 4.5 (confirms Task E); long=5.0;
        # short=3.0 (was 2.0; ATR floor pulled winner 1.0 step wider). 1d combined
        # 5.0 NEW (long-driven edge dominates); tp_r_long_1d dropped (=combined);
        # tp_r_short_1d=1.0 (was 3.0; ATR floor pulled winner 2.0 steps tighter).
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h") == 4.5
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h", direction="long") == 5.0
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h", direction="short") == 3.0
        assert cfg.effective_tp_r("trend_day", "AAPL", "1d") == 5.0
        assert cfg.effective_tp_r("trend_day", "AAPL", "1d", direction="long") == 5.0
        assert cfg.effective_tp_r("trend_day", "AAPL", "1d", direction="short") == 1.0
        # morning_evening_star Phase 2 resweep: 4h combined 2.5 (confirms Task E);
        # long=4.5 (was 5.0; ATR floor pulled winner 0.5 step shorter); short=
        # combined (tp_r_short_4h dropped). 1d combined 2.0 (was 2.5); long=5.0
        # (kept); short=1.5 (was 2.0; ATR floor pulled winner 0.5 step shorter).
        assert cfg.effective_tp_r("morning_evening_star", "AAPL", "4h") == 2.5
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "4h", direction="long")
            == 4.5
        )
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "4h", direction="short")
            == 2.5
        )
        assert cfg.effective_tp_r("morning_evening_star", "AAPL", "1d") == 2.0
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1d", direction="long")
            == 5.0
        )
        assert (
            cfg.effective_tp_r("morning_evening_star", "AAPL", "1d", direction="short")
            == 1.5
        )
        # ema live-parity re-derive (2026-06-03): 4h combined 1.5 (was 3.5; n=30),
        # long_4h/short_4h dropped (live n=13/17<20) → fall to 1.5; 1d combined 4.5 kept
        # (live n=13<20), long_1d/short_1d dropped → fall to 4.5.
        assert cfg.effective_tp_r("ema", "AAPL", "4h") == 1.5
        assert cfg.effective_tp_r("ema", "AAPL", "4h", direction="long") == 1.5
        assert cfg.effective_tp_r("ema", "AAPL", "4h", direction="short") == 1.5
        assert cfg.effective_tp_r("ema", "AAPL", "1d") == 4.5
        assert cfg.effective_tp_r("ema", "AAPL", "1d", direction="long") == 4.5
        assert cfg.effective_tp_r("ema", "AAPL", "1d", direction="short") == 4.5
        # orb 4h live-parity re-derive (2026-06-03): combined 5.0 (was 3.5; n=37),
        # long_4h/short_4h dropped (live long=5.0=combined, short n=17<20) → fall to 5.0.
        assert cfg.effective_tp_r("orb", "AAPL", "4h") == 5.0
        assert cfg.effective_tp_r("orb", "AAPL", "4h", direction="long") == 5.0
        assert cfg.effective_tp_r("orb", "AAPL", "4h", direction="short") == 5.0
        # bos Phase 2 resweep: 4h tp_r_short_4h=1.5 NEW (long no_edge, combined
        # net-neg); 1d combined 2.5 NEW; tp_r_long_1d=4.0 NEW.
        assert cfg.effective_tp_r("bos", "AAPL", "4h", direction="short") == 1.5
        assert cfg.effective_tp_r("bos", "AAPL", "1d") == 2.5
        assert cfg.effective_tp_r("bos", "AAPL", "1d", direction="long") == 4.0
        # doji live-parity re-derive (2026-06-03): 1d combined 3.5 kept (live n=11<20),
        # long_1d dropped (live long n<10) → falls to 3.5.
        assert cfg.effective_tp_r("doji", "AAPL", "1d") == 3.5
        assert cfg.effective_tp_r("doji", "AAPL", "1d", direction="long") == 3.5
        # eqh_eql Phase 2 resweep: 4h combined 4.0 NEW (near-zero edge); 1d combined
        # 3.0 NEW; tp_r_long_1d=5.0 NEW (thin n=15 but strong edge).
        assert cfg.effective_tp_r("eqh_eql", "AAPL", "4h") == 4.0
        assert cfg.effective_tp_r("eqh_eql", "AAPL", "1d") == 3.0
        assert cfg.effective_tp_r("eqh_eql", "AAPL", "1d", direction="long") == 5.0
        # order_block Phase 2 resweep: 4h combined 2.5 NEW; tp_r_short_4h=4.5 NEW;
        # 1d combined 5.0 kept (sub-noise uplift over 4.5 winner).
        assert cfg.effective_tp_r("order_block", "AAPL", "4h") == 2.5
        assert cfg.effective_tp_r("order_block", "AAPL", "4h", direction="short") == 4.5
        assert cfg.effective_tp_r("order_block", "AAPL", "1d") == 5.0
        # inside_bar candle-resweep 13-sym: 4h combined 3.0 (confirms inside_bar audit;
        # long no_edge, short=combined); 1d combined 5.0 (was 2.0; ATR floor inverts
        # edge — long-driven now wins outright; long winner=combined → drop
        # tp_r_long_1d; short no_edge → falls to combined); 1wk insufficient sample
        # (n<10) — falls back to strategy-wide tp_r=3.0.
        assert cfg.effective_tp_r("inside_bar", "AAPL", "4h") == 3.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "4h", direction="long") == 3.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "4h", direction="short") == 3.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1d") == 5.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1d", direction="long") == 5.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1d", direction="short") == 5.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1wk") == 3.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1wk", direction="long") == 3.0
        assert cfg.effective_tp_r("inside_bar", "AAPL", "1wk", direction="short") == 3.0
        # strategy not in params (fvg dropped from signal_watch.toml) falls back to global
        assert cfg.effective_tp_r("fvg", "AAPL", "4h") == cfg.tp_r

    def test_signal_watch_toml_atr_overrides_parsed(self) -> None:
        """Task C-followup 13-sym: per-strategy atr_sl_floor + atr_sl_multiplier_<tf>."""
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # Top-level floor stays off (Task C precedent — no other strategy affected).
        assert cfg.atr_sl_floor is False
        # All 12 active strategies carry per-strategy atr_sl_floor=true
        # (liquidity_sweep removed 2026-05-21 — no_edge on 4h/1d both configs).
        active = [
            "bos",
            "doji",
            "ema",
            "engulfing",
            "eqh_eql",
            "hammer_hanging_man",
            "inside_bar",
            "morning_evening_star",
            "orb",
            "order_block",
            "pin_bar",
            "trend_day",
        ]
        for s in active:
            assert cfg.effective_atr_sl_floor(s, "AAPL", "4h") is True, s
        # Per-strategy × TF multipliers (Task C-followup winners, tue_thu).
        assert cfg.effective_atr_sl_multiplier("bos", "AAPL", "4h") == 2.0
        assert cfg.effective_atr_sl_multiplier("bos", "AAPL", "1d") == 2.0
        assert cfg.effective_atr_sl_multiplier("doji", "AAPL", "4h") == 0.5
        assert cfg.effective_atr_sl_multiplier("doji", "AAPL", "1d") == 2.5
        assert cfg.effective_atr_sl_multiplier("ema", "AAPL", "4h") == 2.5
        assert cfg.effective_atr_sl_multiplier("ema", "AAPL", "1d") == 0.5
        assert cfg.effective_atr_sl_multiplier("engulfing", "AAPL", "4h") == 0.5
        assert cfg.effective_atr_sl_multiplier("engulfing", "AAPL", "1d") == 1.0
        assert cfg.effective_atr_sl_multiplier("eqh_eql", "AAPL", "4h") == 2.5
        assert cfg.effective_atr_sl_multiplier("eqh_eql", "AAPL", "1d") == 2.5
        assert (
            cfg.effective_atr_sl_multiplier("hammer_hanging_man", "AAPL", "4h") == 1.5
        )
        assert (
            cfg.effective_atr_sl_multiplier("hammer_hanging_man", "AAPL", "1d") == 1.0
        )
        assert cfg.effective_atr_sl_multiplier("inside_bar", "AAPL", "4h") == 0.5
        assert cfg.effective_atr_sl_multiplier("inside_bar", "AAPL", "1d") == 1.0
        assert (
            cfg.effective_atr_sl_multiplier("morning_evening_star", "AAPL", "4h") == 1.0
        )
        assert (
            cfg.effective_atr_sl_multiplier("morning_evening_star", "AAPL", "1d") == 1.0
        )
        assert cfg.effective_atr_sl_multiplier("orb", "AAPL", "4h") == 1.5
        assert cfg.effective_atr_sl_multiplier("order_block", "AAPL", "4h") == 0.5
        assert cfg.effective_atr_sl_multiplier("order_block", "AAPL", "1d") == 2.5
        assert cfg.effective_atr_sl_multiplier("pin_bar", "AAPL", "4h") == 1.5
        assert cfg.effective_atr_sl_multiplier("pin_bar", "AAPL", "1d") == 0.5
        assert cfg.effective_atr_sl_multiplier("trend_day", "AAPL", "4h") == 0.5
        assert cfg.effective_atr_sl_multiplier("trend_day", "AAPL", "1d") == 1.0

    def test_signal_watch_weekdays_toml_atr_overrides_parsed(self) -> None:
        """Task C-followup 13-sym: per-strategy ATR overrides on weekdays config (1wk has data)."""
        cfg_path = (
            Path(__file__).parent.parent / "config" / "signal_watch_weekdays.toml"
        )
        cfg = load_signal_config(cfg_path)
        assert cfg.atr_sl_floor is False  # top-level off
        # liquidity_sweep removed 2026-05-21 — was the only positive 1wk cell on
        # this config but 4h/1d net-neg; full retirement.
        active = [
            "bos",
            "doji",
            "ema",
            "engulfing",
            "eqh_eql",
            "hammer_hanging_man",
            "inside_bar",
            "morning_evening_star",
            "orb",
            "order_block",
            "pin_bar",
            "trend_day",
        ]
        for s in active:
            assert cfg.effective_atr_sl_floor(s, "AAPL", "4h") is True, s
        # 4h multipliers
        assert cfg.effective_atr_sl_multiplier("bos", "AAPL", "4h") == 2.0
        assert cfg.effective_atr_sl_multiplier("doji", "AAPL", "4h") == 0.5
        assert cfg.effective_atr_sl_multiplier("ema", "AAPL", "4h") == 2.5
        assert cfg.effective_atr_sl_multiplier("engulfing", "AAPL", "4h") == 0.5
        assert cfg.effective_atr_sl_multiplier("eqh_eql", "AAPL", "4h") == 1.5
        assert (
            cfg.effective_atr_sl_multiplier("hammer_hanging_man", "AAPL", "4h") == 1.0
        )
        assert cfg.effective_atr_sl_multiplier("inside_bar", "AAPL", "4h") == 0.5
        assert (
            cfg.effective_atr_sl_multiplier("morning_evening_star", "AAPL", "4h") == 2.5
        )
        assert cfg.effective_atr_sl_multiplier("orb", "AAPL", "4h") == 2.5
        assert cfg.effective_atr_sl_multiplier("order_block", "AAPL", "4h") == 2.5
        assert cfg.effective_atr_sl_multiplier("pin_bar", "AAPL", "4h") == 1.5
        assert cfg.effective_atr_sl_multiplier("trend_day", "AAPL", "4h") == 1.0
        # 1d multipliers
        assert cfg.effective_atr_sl_multiplier("bos", "AAPL", "1d") == 2.0
        assert cfg.effective_atr_sl_multiplier("doji", "AAPL", "1d") == 2.5
        assert cfg.effective_atr_sl_multiplier("ema", "AAPL", "1d") == 1.0
        assert cfg.effective_atr_sl_multiplier("engulfing", "AAPL", "1d") == 0.5
        assert cfg.effective_atr_sl_multiplier("eqh_eql", "AAPL", "1d") == 1.5
        assert (
            cfg.effective_atr_sl_multiplier("hammer_hanging_man", "AAPL", "1d") == 1.0
        )
        assert cfg.effective_atr_sl_multiplier("inside_bar", "AAPL", "1d") == 1.0
        assert (
            cfg.effective_atr_sl_multiplier("morning_evening_star", "AAPL", "1d") == 1.0
        )
        assert cfg.effective_atr_sl_multiplier("order_block", "AAPL", "1d") == 0.5
        assert cfg.effective_atr_sl_multiplier("pin_bar", "AAPL", "1d") == 0.5
        assert cfg.effective_atr_sl_multiplier("trend_day", "AAPL", "1d") == 2.5
        # 1wk multipliers — first sweep cohort to cover 1wk (Task C tt had no 1wk data).
        assert cfg.effective_atr_sl_multiplier("bos", "AAPL", "1wk") == 2.5
        assert cfg.effective_atr_sl_multiplier("ema", "AAPL", "1wk") == 2.0
        assert cfg.effective_atr_sl_multiplier("engulfing", "AAPL", "1wk") == 0.5
        assert (
            cfg.effective_atr_sl_multiplier("hammer_hanging_man", "AAPL", "1wk") == 1.0
        )
        assert cfg.effective_atr_sl_multiplier("inside_bar", "AAPL", "1wk") == 2.5
        assert (
            cfg.effective_atr_sl_multiplier("morning_evening_star", "AAPL", "1wk")
            == 1.0
        )
        assert cfg.effective_atr_sl_multiplier("order_block", "AAPL", "1wk") == 2.0
        assert cfg.effective_atr_sl_multiplier("pin_bar", "AAPL", "1wk") == 2.5
        assert cfg.effective_atr_sl_multiplier("trend_day", "AAPL", "1wk") == 1.0


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
        # merged: base per-strategy flag + child tp_r. Was `volume_suppress` until
        # 2026-08-06; `adr_exempt` replaced it as the demonstrator when that flag was
        # removed, and it MOVED into the base in the same change precisely so both
        # configs inherit it (living in signal_watch.toml alone had voided bos on
        # weekdays). Same merge shape: base contributes the flag, child the tp_r.
        assert cfg.strategy_params["bos"].adr_exempt is True
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
        # T2c direction filter (soft mode) inherited from base — the gate stays
        # enabled so suppress_long / suppress_short still fire for any strategy
        # that declares them. NO strategy declares one as of 2026-08-06 (PR #143):
        # bos's `suppress_long` was the only instance and it was a crypto-era flag
        # whose claim inverts on equities. The gate mechanism is unchanged and is
        # covered by tests/test_live_parity_direction_filter_gate.py, which builds
        # its own StrategyOverride(suppress_long=True) rather than reading a config.
        assert cfg.bias.direction_filter_enabled is True
        assert cfg.bias.direction_filter_mode == "soft"
        bos_override = cfg.strategy_params.get("bos")
        assert bos_override is not None
        assert bos_override.suppress_long is False
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
        """signal_watch.toml volume_suppress flags must be parsed correctly.

        Updated 2026-08-06: this test previously asserted `orb` and `doji` were
        True and passed for months while both cells produced ~zero signals — it
        checked that a flag PARSED, never that the flag left anything alive. That
        is the #140 blind spot ("right value for the input I imagined" cannot see
        "this surface produces nothing"); `TestVoidedVolumeGates` is the
        output-oriented counterpart that can.
        """
        cfg_path = Path(__file__).parent.parent / "config" / "signal_watch.toml"
        cfg = load_signal_config(cfg_path)
        # All four crypto-era A14b volume flags were removed 2026-08-06:
        # doji / orb / engulfing-long were VOIDED by conjunction with the ADR gate;
        # bos was not voided (it is adr_exempt) but its claim does not replicate on
        # equities — volume failed to predict R on every cell (all p >= 0.113, wrong
        # sign on 3 of 5) while discarding 90-94% of its signals.
        assert cfg.effective_volume_suppress("bos") is False
        assert cfg.effective_volume_suppress("orb") is False
        assert cfg.effective_volume_suppress("doji") is False
        assert cfg.effective_volume_suppress_long("engulfing") is not True
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


class TestCostModelConfig:
    def test_absent_block_is_none(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, 'timeframes = ["4h"]\n')
        assert load_signal_config(p).backtest.cost_model is None

    def test_disabled_block_is_none(self, tmp_path: Path) -> None:
        content = """
timeframes = ["4h"]

[backtest.cost_model]
enabled = false
"""
        p = _write_toml(tmp_path, content)
        assert load_signal_config(p).backtest.cost_model is None

    def test_enabled_block_parses_overrides(self, tmp_path: Path) -> None:
        content = """
timeframes = ["4h"]

[backtest.cost_model]
enabled = true
impact_coef = 0.5
borrow_rate_annual = 0.02
"""
        p = _write_toml(tmp_path, content)
        model = load_signal_config(p).backtest.cost_model
        assert model is not None
        assert model.impact_coef == 0.5
        assert model.borrow_rate_annual == 0.02
        # Unset keys keep the conservative defaults.
        assert model.half_spread_bps == CostModel().half_spread_bps


class TestDeadTimeframes:
    """A day_filter × fixed-open-weekday timeframe pairing is a blackout, not a filter.

    `config/signal_watch.toml` paired `tue_thu` with `1wk` from PR #22 until
    2026-08-06. Weekly bars are stamped Monday, so 100% of their signals were
    discarded: zero alerts dispatched, zero closed trades in `backtest_runs`,
    and therefore no `confidence_ratings` rows that any `make db-update` could
    ever create.
    """

    def test_tue_thu_kills_weekly(self) -> None:
        assert dead_timeframes("tue_thu", ["4h", "1d", "1wk"]) == ["1wk"]

    def test_no_monfi_kills_weekly(self) -> None:
        # Tue/Wed/Thu/Sat/Sun — Monday excluded, so weekly bars cannot pass.
        assert dead_timeframes("no_monfi", ["1wk"]) == ["1wk"]

    def test_weekdays_allows_weekly(self) -> None:
        # Mon–Fri includes Monday — this is why signal_watch_weekdays has 1wk ratings.
        assert dead_timeframes("weekdays", ["4h", "1d", "1wk"]) == []

    def test_off_allows_everything(self) -> None:
        assert dead_timeframes("off", ["1wk"]) == []

    def test_intraday_and_daily_never_dead(self) -> None:
        # Only fixed-open-weekday timeframes can black out; 4h/1d span every weekday.
        assert dead_timeframes("tue_thu", ["4h", "1d"]) == []

    def test_duplicates_reported_once(self) -> None:
        assert dead_timeframes("tue_thu", ["1wk", "1wk"]) == ["1wk"]

    def test_load_rejects_blackout_pairing(self, tmp_path: Path) -> None:
        content = """
timeframes = ["4h", "1d", "1wk"]
day_filter = "tue_thu"
"""
        p = _write_toml(tmp_path, content)
        with pytest.raises(ValueError, match="discards every bar"):
            load_signal_config(p)

    def test_load_rejects_blackout_via_strategy_timeframes(
        self, tmp_path: Path
    ) -> None:
        # A per-strategy override can smuggle in a dead TF the top-level list omits.
        content = """
timeframes = ["4h"]
day_filter = "tue_thu"

[strategy_timeframes]
pin_bar = ["1wk"]
"""
        p = _write_toml(tmp_path, content)
        with pytest.raises(ValueError, match="discards every bar"):
            load_signal_config(p)

    def test_shipped_configs_have_no_blackout(self) -> None:
        # Binds the guard to the real configs so neither can regress into a blackout.
        for path in ("config/signal_watch.toml", "config/signal_watch_weekdays.toml"):
            cfg = load_signal_config(path)
            assert dead_timeframes(cfg.day_filter, cfg.timeframes) == [], path


class TestVoidedVolumeGates:
    """`volume_suppress*` + a live ADR gate select for opposite bars.

    The ADR gate keeps candles that have consumed little of their typical daily
    range; `volume_suppress` keeps candles with >=1.5x mean volume. Range and
    volume correlate at ~+0.65, so the conjunction is nearly the empty set —
    measured P(pass both) = 0.0046 vs 0.036 under independence.

    Shipped state before 2026-08-06: `doji` x 1d produced exactly 0 signals from
    1,247 raw detector fires, `orb` x 4h ran on n=1, and `engulfing` x 1d /
    `bos` x 1d had their measured avg_r sign inverted.
    """

    def test_flags_symmetric_suppress_without_exemption(self) -> None:
        params = {"doji": StrategyOverride(volume_suppress=True)}
        assert voided_volume_gates(params, 0.80) == ["doji"]

    def test_flags_directional_suppress(self) -> None:
        # engulfing carried only the LONG-side flag and was voided just the same.
        params = {"engulfing": StrategyOverride(volume_suppress_long=True)}
        assert voided_volume_gates(params, 0.80) == ["engulfing"]
        params = {"x": StrategyOverride(volume_suppress_short=True)}
        assert voided_volume_gates(params, 0.80) == ["x"]

    def test_adr_exempt_clears_the_pairing(self) -> None:
        # bos opts out of the ADR gate by entry geometry, so its volume flag acts alone.
        params = {"bos": StrategyOverride(volume_suppress=True, adr_exempt=True)}
        assert voided_volume_gates(params, 0.80) == []

    def test_disabled_adr_gate_clears_the_pairing(self) -> None:
        params = {"doji": StrategyOverride(volume_suppress=True)}
        assert voided_volume_gates(params, None) == []

    def test_explicit_false_is_not_a_conjunction(self) -> None:
        # `volume_suppress = false` is the safe majority in strategy_params.toml.
        params = {"pin_bar": StrategyOverride(volume_suppress=False)}
        assert voided_volume_gates(params, 0.80) == []

    def test_none_inherits_and_is_not_flagged(self) -> None:
        # None means "inherit global"; only an explicit True pins the conjunction.
        params = {"trend_day": StrategyOverride()}
        assert voided_volume_gates(params, 0.80) == []

    def test_load_rejects_the_conjunction(self, tmp_path: Path) -> None:
        content = """
timeframes = ["4h"]
day_filter = "off"

[bias]
adr_suppress_threshold = 0.80

[strategy_params.doji]
volume_suppress = true
"""
        p = _write_toml(tmp_path, content)
        with pytest.raises(ValueError, match="without adr_exempt"):
            load_signal_config(p)

    def test_load_accepts_the_conjunction_when_exempt(self, tmp_path: Path) -> None:
        content = """
timeframes = ["4h"]
day_filter = "off"

[bias]
adr_suppress_threshold = 0.80

[strategy_params.bos]
volume_suppress = true
adr_exempt = true
"""
        p = _write_toml(tmp_path, content)
        cfg = load_signal_config(p)
        assert cfg.effective_volume_suppress("bos") is True

    def test_shipped_configs_carry_no_voided_gate(self) -> None:
        # Binds the guard to the real configs. This assertion FAILS on the
        # pre-2026-08-06 tree: signal_watch flagged [engulfing, orb, doji] and
        # signal_watch_weekdays additionally flagged bos.
        for path in ("config/signal_watch.toml", "config/signal_watch_weekdays.toml"):
            cfg = load_signal_config(path)
            assert (
                voided_volume_gates(
                    cfg.strategy_params, cfg.bias.adr_suppress_threshold
                )
                == []
            ), path

    def test_no_shipped_strategy_sets_volume_suppress(self) -> None:
        """All four crypto-era A14b volume flags were removed on 2026-08-06.

        Not merely a restatement of the guard above: a strategy could set the flag
        legitimately by also setting `adr_exempt` (that is `bos`'s old shape, which
        the guard permits). This pins the stronger, current fact — none of the four
        replicated on equities, so none is set. Re-adding one is allowed, but it
        must break this test and be re-measured first, not slip in.
        """
        for path in ("config/signal_watch.toml", "config/signal_watch_weekdays.toml"):
            cfg = load_signal_config(path)
            for name in cfg.strategy_params:
                assert cfg.effective_volume_suppress(name) is not True, f"{path} {name}"
                assert cfg.effective_volume_suppress_long(name) is not True, name
                assert cfg.effective_volume_suppress_short(name) is not True, name

    def test_no_shipped_strategy_sets_direction_suppress(self) -> None:
        """Direct sibling of the volume-flag guard above, for the DIRECTION flags.

        `bos.suppress_long` was the only instance and it was removed 2026-08-06
        (PR #143): it arrived from the crypto parent one day before the fork
        (parent PR #367, justified by an n=72,643 Binance-trade audit) and its
        claim INVERTS on equities — long is bos's better leg on both timeframes.
        #141's sweep missed it because it greps as a direction flag, not a volume
        flag, which is exactly why this needs its own assertion.

        This flag class is latent rather than loud: `[bias.direction_filter]` ships
        as mode="soft", so a wrong flag logs and keeps, costing nothing until
        someone follows that block's own instruction to flip it to hard. Re-adding
        one is allowed, but it must break this test and be re-measured on equity
        data first.
        """
        for path in ("config/signal_watch.toml", "config/signal_watch_weekdays.toml"):
            cfg = load_signal_config(path)
            for name, override in cfg.strategy_params.items():
                assert override.suppress_long is not True, f"{path} {name}"
                assert override.suppress_short is not True, f"{path} {name}"
