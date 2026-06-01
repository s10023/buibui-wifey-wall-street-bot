"""Tests for T6 live-parity foundation (PR-1).

Covers the `LiveParityConfig` dataclass, the `[backtest.live_parity]` TOML
loader, the CLI flag wiring (master + per-gate `--with`/`--without`), the
`_df_to_events` / `_events_to_df` engine adapters, and that
`run_backtest()` is unaffected when `live_parity` is left at its default.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
from pathlib import Path

import pandas as pd
import pytest

from analytics.backtest.engine import (
    _df_to_events,
    _events_to_df,
    run_backtest,
)
from analytics.backtest.live_parity_config import LiveParityConfig
from analytics.backtest_config import BacktestSweepConfig, load_backtest_config
from cli.backtest import (
    _LIVE_PARITY_GATES,
    _resolve_live_parity,
    add_backtest_subparser,
)


class TestLiveParityConfigDataclass:
    def test_defaults_are_all_off(self) -> None:
        cfg = LiveParityConfig()
        assert cfg.enabled is False
        for gate in _LIVE_PARITY_GATES:
            assert getattr(cfg, gate) is False
        assert cfg.cooldown_bars_per_tf is None

    def test_is_on_reads_field_directly(self) -> None:
        cfg = LiveParityConfig(regime=True)
        assert cfg.is_on("regime") is True
        assert cfg.is_on("direction_filter") is False

    def test_master_alone_does_not_imply_gate_fields(self) -> None:
        # `enabled` is a resolver-time convenience; the dataclass itself does
        # not infer per-gate truth from it. The CLI/TOML resolver is the layer
        # that expands master into per-gate True values.
        cfg = LiveParityConfig(enabled=True)
        for gate in _LIVE_PARITY_GATES:
            assert cfg.is_on(gate) is False

    def test_without_gate_overrides_master_after_resolve(self) -> None:
        # Mirrors how `_resolve_live_parity` shapes the dataclass when the
        # user passes `--live-parity --without-cooldown`.
        cfg = LiveParityConfig(
            enabled=True,
            regime=True,
            direction_filter=True,
            f8_htf_ema=True,
            adr_bias=True,
            conflict_resolver=True,
            cooldown=False,
        )
        assert cfg.is_on("regime") is True
        assert cfg.is_on("cooldown") is False

    def test_frozen_dataclass(self) -> None:
        cfg = LiveParityConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.enabled = True  # type: ignore[misc]

    def test_replace_returns_new_instance(self) -> None:
        base = LiveParityConfig(regime=True)
        child = dataclasses.replace(base, cooldown=True)
        assert base.cooldown is False
        assert child.regime is True
        assert child.cooldown is True

    def test_cooldown_bars_per_tf_passthrough(self) -> None:
        # Equity-only TFs (wifey trades 4h/1d/1wk).
        bars = {"4h": 2, "1d": 1, "1wk": 1}
        cfg = LiveParityConfig(cooldown=True, cooldown_bars_per_tf=bars)
        assert cfg.cooldown_bars_per_tf == bars


class TestLiveParityTomlLoader:
    def test_missing_block_defaults_to_no_op(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text('symbols = ["AAPL"]\n')
        cfg = load_backtest_config(p)
        assert isinstance(cfg.live_parity, LiveParityConfig)
        assert cfg.live_parity == LiveParityConfig()

    def test_block_is_parsed(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(
            'symbols = ["AAPL"]\n'
            "\n"
            "[backtest.live_parity]\n"
            "enabled = false\n"
            "regime = true\n"
            "direction_filter = true\n"
            "cooldown = true\n"
            "\n"
            "[backtest.live_parity.cooldown_bars]\n"
            '"4h" = 2\n'
            '"1d" = 1\n'
        )
        cfg = load_backtest_config(p)
        assert cfg.live_parity.enabled is False
        assert cfg.live_parity.regime is True
        assert cfg.live_parity.direction_filter is True
        assert cfg.live_parity.cooldown is True
        assert cfg.live_parity.cooldown_bars_per_tf == {"4h": 2, "1d": 1}

    def test_extends_inheritance(self, tmp_path: Path) -> None:
        base = tmp_path / "base.toml"
        base.write_text(
            'symbols = ["AAPL"]\n'
            "\n"
            "[backtest.live_parity]\n"
            "regime = true\n"
            "direction_filter = true\n"
        )
        child = tmp_path / "child.toml"
        child.write_text(
            'extends = "base.toml"\n'
            "\n"
            "[backtest.live_parity]\n"
            "direction_filter = false\n"
            "cooldown = true\n"
        )
        cfg = load_backtest_config(child)
        assert cfg.live_parity.regime is True  # inherited
        assert cfg.live_parity.direction_filter is False  # overridden
        assert cfg.live_parity.cooldown is True  # added

    def test_invalid_block_type_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text('symbols = ["AAPL"]\n\n[backtest]\nlive_parity = "yes"\n')
        with pytest.raises(ValueError, match="backtest.live_parity must be"):
            load_backtest_config(p)

    def test_invalid_cooldown_bars_type_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(
            'symbols = ["AAPL"]\n\n[backtest.live_parity]\ncooldown_bars = "nope"\n'
        )
        with pytest.raises(ValueError, match="cooldown_bars must be"):
            load_backtest_config(p)


def _build_subparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    add_backtest_subparser(subparsers)
    return parser


class TestCliLiveParityFlags:
    def test_no_flags_default(self) -> None:
        parser = _build_subparser()
        args = parser.parse_args(["backtest", "--symbols", "AAPL"])
        assert args.live_parity_enabled is False
        for gate in _LIVE_PARITY_GATES:
            assert getattr(args, f"live_parity_{gate}") is None

    def test_master_flag(self) -> None:
        parser = _build_subparser()
        args = parser.parse_args(["backtest", "--symbols", "AAPL", "--live-parity"])
        assert args.live_parity_enabled is True
        resolved = _resolve_live_parity(args, LiveParityConfig())
        assert resolved.enabled is True
        for gate in _LIVE_PARITY_GATES:
            assert resolved.is_on(gate) is True  # master lights everything

    def test_with_single_gate(self) -> None:
        parser = _build_subparser()
        args = parser.parse_args(["backtest", "--symbols", "AAPL", "--with-regime"])
        resolved = _resolve_live_parity(args, LiveParityConfig())
        assert resolved.enabled is False
        assert resolved.regime is True
        assert resolved.is_on("regime") is True
        assert resolved.is_on("direction_filter") is False

    def test_without_negates_master(self) -> None:
        parser = _build_subparser()
        args = parser.parse_args(
            [
                "backtest",
                "--symbols",
                "AAPL",
                "--live-parity",
                "--without-cooldown",
            ]
        )
        resolved = _resolve_live_parity(args, LiveParityConfig())
        assert resolved.enabled is True
        # Every other gate stays on via master expansion.
        for gate in _LIVE_PARITY_GATES:
            if gate == "cooldown":
                continue
            assert resolved.is_on(gate) is True
        # Acceptance contract: `--without-cooldown` defeats the master switch
        # for this one gate — log line will print `cooldown=off`.
        assert resolved.cooldown is False
        assert resolved.is_on("cooldown") is False

    def test_with_then_without_same_gate_resolves_last(self) -> None:
        parser = _build_subparser()
        args = parser.parse_args(
            [
                "backtest",
                "--symbols",
                "AAPL",
                "--with-regime",
                "--without-regime",
            ]
        )
        assert args.live_parity_regime is False  # argparse: last wins
        resolved = _resolve_live_parity(args, LiveParityConfig())
        assert resolved.regime is False

    def test_resolve_inherits_unset_fields_from_base(self) -> None:
        parser = _build_subparser()
        args = parser.parse_args(["backtest", "--symbols", "AAPL"])
        base = LiveParityConfig(regime=True, cooldown=True)
        resolved = _resolve_live_parity(args, base)
        assert resolved is base  # no CLI overrides → identity

    def test_resolve_cli_wins_over_toml(self) -> None:
        parser = _build_subparser()
        args = parser.parse_args(["backtest", "--symbols", "AAPL", "--without-regime"])
        base = LiveParityConfig(regime=True)
        resolved = _resolve_live_parity(args, base)
        assert resolved.regime is False


def _toy_signals_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "open_time": 1_000,
                "direction": "long",
                "reason": "test",
                "sl_price": 99.0,
                "context": "ctx",
                "low_volume": False,
                "tp_price": 110.0,
            },
            {
                "open_time": 2_000,
                "direction": "short",
                "reason": "test2",
                "sl_price": 101.0,
                "context": "ctx2",
                "low_volume": True,
                "tp_price": 90.0,
            },
        ]
    )


class TestEngineAdapters:
    def test_df_to_events_roundtrip(self) -> None:
        df = _toy_signals_df()
        events = _df_to_events(df, "AAPL", "4h", "bos")
        assert [e.open_time for e in events] == [1_000, 2_000]
        assert events[0].symbol == "AAPL"
        assert events[0].timeframe == "4h"
        assert events[0].strategy == "bos"
        assert events[0].direction == "long"
        assert events[0].sl_price == pytest.approx(99.0)
        assert events[1].direction == "short"
        assert events[1].low_volume is True
        # round-trip back to a frame preserves the rows.
        roundtripped = _events_to_df(events, df)
        pd.testing.assert_frame_equal(roundtripped, df)

    def test_df_to_events_empty(self) -> None:
        empty = _toy_signals_df().iloc[0:0]
        assert _df_to_events(empty, "AAPL", "4h", "bos") == []

    def test_events_to_df_empty_returns_empty_frame(self) -> None:
        df = _toy_signals_df()
        out = _events_to_df([], df)
        assert out.empty
        # original columns preserved on the empty frame
        assert list(out.columns) == list(df.columns)

    def test_events_to_df_filters_to_kept_open_times(self) -> None:
        df = _toy_signals_df()
        events = _df_to_events(df, "AAPL", "4h", "bos")
        kept = [events[0]]  # drop the second event
        out = _events_to_df(kept, df)
        assert list(out["open_time"]) == [1_000]


def _toy_ohlcv() -> pd.DataFrame:
    # 30 synthetic bars so ATR14 + simulation have room to run. Wifey uses
    # equity TFs (4h/1d/1wk); the bar spacing here is purely synthetic.
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


def _toy_signals_for_engine() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [2_000],
            "direction": ["long"],
            "reason": ["t"],
            "sl_price": [98.0],
            "context": ["c"],
            "low_volume": [False],
            "tp_price": [104.0],
        }
    )


class TestRunBacktestAcceptsLiveParity:
    def test_default_none_unchanged(self) -> None:
        baseline = run_backtest(
            _toy_ohlcv(), _toy_signals_for_engine(), "AAPL", "4h", "bos"
        )
        with_default = run_backtest(
            _toy_ohlcv(),
            _toy_signals_for_engine(),
            "AAPL",
            "4h",
            "bos",
            live_parity=LiveParityConfig(),
        )
        assert len(baseline.trades) == len(with_default.trades)
        assert baseline.total_r == with_default.total_r

    def test_log_line_emitted_when_any_gate_on(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="analytics.backtest.engine"):
            run_backtest(
                _toy_ohlcv(),
                _toy_signals_for_engine(),
                "AAPL",
                "4h",
                "bos",
                live_parity=LiveParityConfig(regime=True),
            )
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "live_parity: regime=on" in joined
        assert "direction_filter=off" in joined

    def test_no_log_line_when_all_gates_off(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="analytics.backtest.engine"):
            run_backtest(
                _toy_ohlcv(),
                _toy_signals_for_engine(),
                "AAPL",
                "4h",
                "bos",
                live_parity=LiveParityConfig(),
            )
        for r in caplog.records:
            assert "live_parity" not in r.getMessage()


class TestBacktestSweepConfigField:
    def test_default_live_parity_is_no_op(self) -> None:
        cfg = BacktestSweepConfig()
        assert isinstance(cfg.live_parity, LiveParityConfig)
        assert cfg.live_parity == LiveParityConfig()
