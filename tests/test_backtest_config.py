"""Tests for analytics/backtest_config.py."""

import tomllib
from pathlib import Path

import pytest

from analytics.backtest_config import (
    BacktestSweepConfig,
    StrategyOverride,
    SymbolOverride,
    load_backtest_config,
)

_MINIMAL_TOML = """\
symbols    = ["BTCUSDT", "ETHUSDT"]
timeframes = ["1h", "4h"]
strategies = ["fvg", "bos"]
days       = 60
sl_pct     = 0.015
tp_r       = 3.0
min_trades = 10
"""

_PARTIAL_TOML = """\
symbols = ["BTCUSDT"]
"""


class TestBacktestSweepConfigDefaults:
    def test_load_defaults(self) -> None:
        cfg = BacktestSweepConfig()
        assert cfg.symbols is None
        assert cfg.timeframes == ["4h"]
        assert cfg.strategies is None
        assert cfg.days == 90
        assert cfg.sl_pct == 0.02
        assert cfg.tp_r == 2.0
        assert cfg.min_trades == 20
        assert cfg.day_filter == "off"


class TestLoadBacktestConfig:
    def test_load_from_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(_MINIMAL_TOML, encoding="utf-8")
        cfg = load_backtest_config(p)
        assert cfg.symbols == ["BTCUSDT", "ETHUSDT"]
        assert cfg.timeframes == ["1h", "4h"]
        assert cfg.strategies == ["fvg", "bos"]
        assert cfg.days == 60
        assert cfg.sl_pct == 0.015
        assert cfg.tp_r == 3.0
        assert cfg.min_trades == 10

    def test_load_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_backtest_config("/nonexistent/path/cfg.toml")

    def test_load_invalid_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.toml"
        p.write_text("symbols = [unclosed", encoding="utf-8")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_backtest_config(p)

    def test_load_partial_toml_uses_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "partial.toml"
        p.write_text(_PARTIAL_TOML, encoding="utf-8")
        cfg = load_backtest_config(p)
        assert cfg.symbols == ["BTCUSDT"]
        assert cfg.timeframes == ["4h"]
        assert cfg.strategies is None
        assert cfg.days == 90
        assert cfg.min_trades == 20
        assert cfg.day_filter == "off"

    def test_load_day_filter_string_modes(self, tmp_path: Path) -> None:
        for mode in ("off", "weekdays", "tue_thu"):
            p = tmp_path / f"cfg_{mode}.toml"
            p.write_text(f'symbols = []\nday_filter = "{mode}"\n', encoding="utf-8")
            cfg = load_backtest_config(p)
            assert cfg.day_filter == mode

    def test_atr_sl_floor_default(self) -> None:
        cfg = BacktestSweepConfig()
        assert cfg.atr_sl_floor is False

    def test_load_atr_sl_floor_top_level(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text("symbols = []\natr_sl_floor = true\n", encoding="utf-8")
        cfg = load_backtest_config(p)
        assert cfg.atr_sl_floor is True

    def test_load_atr_sl_floor_backtest_section(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(
            "symbols = []\n[backtest]\natr_sl_floor = true\n", encoding="utf-8"
        )
        cfg = load_backtest_config(p)
        assert cfg.atr_sl_floor is True

    def test_load_per_tf_min_trades(self, tmp_path: Path) -> None:
        content = "min_trades = 20\nmin_trades_15m = 30\nmin_trades_4h = 10\nmin_trades_1d = 5\n"
        p = tmp_path / "cfg.toml"
        p.write_text(content, encoding="utf-8")
        cfg = load_backtest_config(p)
        assert cfg.min_trades_per_tf == {"15m": 30, "4h": 10, "1d": 5}
        assert cfg.effective_min_trades("15m") == 30
        assert cfg.effective_min_trades("4h") == 10
        assert cfg.effective_min_trades("1d") == 5
        assert cfg.effective_min_trades("1h") == 20  # falls back to global

    def test_effective_min_trades_no_overrides(self) -> None:
        cfg = BacktestSweepConfig(min_trades=15)
        assert cfg.effective_min_trades("15m") == 15
        assert cfg.effective_min_trades("4h") == 15


class TestStrategyOverrideBacktestConfig:
    def test_effective_tp_r_tf_specific(self) -> None:
        cfg = BacktestSweepConfig(
            tp_r=2.0,
            strategy_params={"bos": StrategyOverride(tp_r_per_tf={"4h": 2.5})},
        )
        assert cfg.effective_tp_r("bos", "BTCUSDT", "4h") == 2.5
        assert cfg.effective_tp_r("bos", "BTCUSDT", "1h") == 2.0  # global fallback

    def test_effective_tp_r_strategy_wide(self) -> None:
        cfg = BacktestSweepConfig(
            tp_r=2.0,
            strategy_params={"engulfing": StrategyOverride(tp_r=3.0)},
        )
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "1h") == 3.0
        assert cfg.effective_tp_r("engulfing", "BTCUSDT", "4h") == 3.0
        assert cfg.effective_tp_r("fvg", "BTCUSDT", "1h") == 2.0  # not in params

    def test_effective_tp_r_tf_beats_strategy_wide(self) -> None:
        cfg = BacktestSweepConfig(
            tp_r=2.0,
            strategy_params={
                "pin_bar": StrategyOverride(tp_r=3.0, tp_r_per_tf={"4h": 2.5})
            },
        )
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "4h") == 2.5
        assert cfg.effective_tp_r("pin_bar", "BTCUSDT", "1h") == 3.0

    def test_effective_sl_pct_override(self) -> None:
        cfg = BacktestSweepConfig(
            sl_pct=0.02,
            strategy_params={"orb": StrategyOverride(sl_pct=0.015)},
        )
        assert cfg.effective_sl_pct("orb", "BTCUSDT", "1h") == 0.015
        assert cfg.effective_sl_pct("fvg", "BTCUSDT", "1h") == 0.02

    def test_load_strategy_params_sub_table(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.engulfing]
tp_r = 3.0

[strategy_params.bos]
tp_r_4h = 2.5
"""
        p = tmp_path / "cfg.toml"
        p.write_text(content, encoding="utf-8")
        cfg = load_backtest_config(p)
        assert cfg.strategy_params["engulfing"].tp_r == 3.0
        assert cfg.strategy_params["bos"].tp_r_per_tf == {"4h": 2.5}

    def test_load_strategy_params_defaults_to_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text("symbols = ['BTCUSDT']\n", encoding="utf-8")
        cfg = load_backtest_config(p)
        assert cfg.strategy_params == {}

    def test_effective_tp_r_per_tf_directional_beats_per_tf_combined(self) -> None:
        """tp_r_long_4h / tp_r_short_4h beat tp_r_4h on the same TF."""
        cfg = BacktestSweepConfig(
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
        # No direction → falls through to per-TF combined.
        assert cfg.effective_tp_r("trend_day", "AAPL", "4h") == 4.0

    def test_effective_tp_r_per_tf_directional_does_not_leak_to_other_tfs(
        self,
    ) -> None:
        """tp_r_long_4h must NOT affect 1d / 1wk trades."""
        cfg = BacktestSweepConfig(
            tp_r=2.0,
            strategy_params={
                "ema": StrategyOverride(
                    tp_r=3.0,
                    tp_r_long_per_tf={"4h": 2.0},
                )
            },
        )
        assert cfg.effective_tp_r("ema", "AAPL", "4h", direction="long") == 2.0
        assert cfg.effective_tp_r("ema", "AAPL", "1d", direction="long") == 3.0
        assert cfg.effective_tp_r("ema", "AAPL", "1wk", direction="long") == 3.0

    def test_load_tp_r_long_per_tf_from_toml(self, tmp_path: Path) -> None:
        content = """\
[strategy_params.trend_day]
tp_r_4h = 4.0
tp_r_long_4h = 3.5
tp_r_short_4h = 5.0
tp_r_long_1wk = 2.0
"""
        p = tmp_path / "cfg.toml"
        p.write_text(content, encoding="utf-8")
        cfg = load_backtest_config(p)
        override = cfg.strategy_params["trend_day"]
        assert override.tp_r_per_tf == {"4h": 4.0}
        assert override.tp_r_long_per_tf == {"4h": 3.5, "1wk": 2.0}
        assert override.tp_r_short_per_tf == {"4h": 5.0}
        # Guard against the silent mis-parse bug.
        assert "long_4h" not in override.tp_r_per_tf
        assert "short_4h" not in override.tp_r_per_tf
        assert "long_1wk" not in override.tp_r_per_tf


class TestEffectiveTpRPerSymbolBacktestConfig:
    def test_symbol_tf_override_wins_over_strategy_tf(self) -> None:
        cfg = BacktestSweepConfig(
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
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        assert cfg.effective_tp_r("doji", "BTCUSDT", "15m") == 4.0

    def test_symbol_wide_override_wins_over_strategy_wide(self) -> None:
        cfg = BacktestSweepConfig(
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
        assert cfg.effective_tp_r("hammer_hanging_man", "ETHUSDT", "1h") == 5.0
        assert cfg.effective_tp_r("hammer_hanging_man", "BTCUSDT", "1h") == 4.0

    def test_symbol_tf_wins_over_symbol_wide(self) -> None:
        cfg = BacktestSweepConfig(
            tp_r=2.0,
            strategy_params={
                "doji": StrategyOverride(
                    per_symbol={
                        "ETHUSDT": SymbolOverride(tp_r=4.0, tp_r_per_tf={"15m": 4.5}),
                    },
                )
            },
        )
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        assert cfg.effective_tp_r("doji", "ETHUSDT", "1h") == 4.0

    def test_unknown_symbol_falls_through(self) -> None:
        cfg = BacktestSweepConfig(
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
        assert cfg.effective_tp_r("doji", "SOLUSDT", "15m") == 4.0
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
        p = tmp_path / "cfg.toml"
        p.write_text(content, encoding="utf-8")
        cfg = load_backtest_config(p)
        assert cfg.effective_tp_r("doji", "ETHUSDT", "15m") == 4.5
        assert cfg.effective_tp_r("doji", "BTCUSDT", "15m") == 3.5
        assert cfg.effective_tp_r("doji", "SOLUSDT", "15m") == 4.0
        assert cfg.effective_tp_r("doji", "ETHUSDT", "1h") == 3.0


class TestCostModelBacktestConfig:
    def test_defaults_to_none(self) -> None:
        assert BacktestSweepConfig().cost_model is None

    def test_loads_enabled_block(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(
            'symbols = ["SPY"]\n'
            "\n"
            "[backtest.cost_model]\n"
            "enabled = true\n"
            "notional_usd = 25000.0\n",
            encoding="utf-8",
        )
        cfg = load_backtest_config(p)
        assert cfg.cost_model is not None
        assert cfg.cost_model.notional_usd == 25_000.0

    def test_disabled_block_is_none(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(
            'symbols = ["SPY"]\n\n[backtest.cost_model]\nenabled = false\n',
            encoding="utf-8",
        )
        assert load_backtest_config(p).cost_model is None
