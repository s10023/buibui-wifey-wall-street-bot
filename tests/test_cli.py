"""Tests for wifey.py CLI argument parsing and dispatch."""

from typing import Any
from unittest.mock import patch

import pytest


class TestCLIParsing:
    """Tests for CLI argument parsing in wifey.py."""

    def test_missing_subcommand_exits(self) -> None:
        """Missing subcommand causes SystemExit."""
        with patch("sys.argv", ["wifey.py"]):
            from wifey import main

            with pytest.raises(SystemExit):
                main()


class TestBacktestMinSlPctFlag:
    """`--min-sl-pct` reaches both backtest modes.

    The flag did not exist on the `backtest` parser, while the single-combo
    call site read it behind a `hasattr` guard that could therefore never be
    satisfied — so `min_sl_pct` was pinned at 0.0 on that path no matter what
    the operator asked for. The default cases are the positive control: they
    fail if the flag ever starts overriding a value nobody set.
    """

    @staticmethod
    def _parse(argv: list[str]) -> Any:
        import argparse

        from cli.backtest import add_backtest_subparser

        parser = argparse.ArgumentParser()
        add_backtest_subparser(parser.add_subparsers(dest="command"))
        return parser.parse_args(["backtest", *argv])

    def test_single_combo_honours_the_flag(self) -> None:
        from analytics.strategies import KNOWN_STRATEGIES
        from cli.backtest import run_backtest

        args = self._parse(
            [
                "--symbol",
                "AAPL",
                "--strategy",
                KNOWN_STRATEGIES[0],
                "--min-sl-pct",
                "0.005",
            ]
        )
        with patch("analytics.backtest_runner.run_backtest_cmd") as cmd:
            run_backtest(args)
        assert cmd.call_args.kwargs["min_sl_pct"] == 0.005

    def test_single_combo_defaults_to_disabled(self) -> None:
        from analytics.strategies import KNOWN_STRATEGIES
        from cli.backtest import run_backtest

        args = self._parse(["--symbol", "AAPL", "--strategy", KNOWN_STRATEGIES[0]])
        with patch("analytics.backtest_runner.run_backtest_cmd") as cmd:
            run_backtest(args)
        assert cmd.call_args.kwargs["min_sl_pct"] == 0.0

    def test_sweep_honours_the_flag(self) -> None:
        from cli.backtest import run_backtest

        args = self._parse(["--symbols", "AAPL", "--min-sl-pct", "0.005"])
        with patch("analytics.backtest_runner.run_backtest_sweep") as sweep:
            run_backtest(args)
        assert sweep.call_args.args[0].min_sl_pct == 0.005

    def test_sweep_leaves_the_config_value_alone_when_unset(self) -> None:
        from analytics.backtest_config import BacktestSweepConfig
        from cli.backtest import run_backtest

        args = self._parse(["--symbols", "AAPL"])
        with patch("analytics.backtest_runner.run_backtest_sweep") as sweep:
            run_backtest(args)
        assert sweep.call_args.args[0].min_sl_pct == BacktestSweepConfig().min_sl_pct
