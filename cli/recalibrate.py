"""Wifey CLI — `recalibrate` subcommand."""

from __future__ import annotations

import argparse

from analytics import recalibrate_runner


def run_recalibrate(args: argparse.Namespace) -> None:
    recalibrate_runner.run(args)


def add_recalibrate_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    recalibrate_parser = subparsers.add_parser(
        "recalibrate",
        help="Recalibrate strategy confidence star ratings from backtest_runs data",
    )
    recalibrate_parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply the new ratings to STRATEGY_REGISTRY (default: dry-run only)",
    )
    recalibrate_parser.add_argument(
        "--min-trades",
        type=int,
        default=10,
        dest="min_trades",
        help="Minimum total closed trades required to recalibrate a strategy (default: 10)",
    )
    recalibrate_parser.add_argument(
        "--config",
        type=str,
        default=None,
        dest="config",
        help="TOML config path to calibrate for (derives day_filter + config_name from file; "
        "--apply writes to confidence_ratings DB table instead of indicators_lib.py)",
    )
    recalibrate_parser.add_argument(
        "--day-filter",
        type=str,
        default=None,
        dest="day_filter",
        help="Only use backtest runs saved with this day_filter value (e.g. tue_thu); "
        "overridden by --config when both are provided",
    )
    recalibrate_parser.set_defaults(func=run_recalibrate)
