"""Wifey CLI — `digest` subcommand (pre-canned analytics queries)."""

from __future__ import annotations

import argparse

from analytics import backtest_runner
from analytics.digest_lib import QUERY_NAMES


def run_digest_cmd(args: argparse.Namespace) -> None:
    backtest_runner.run_digest_cmd(args.query, args.min_trades, args.top_n)


def add_digest_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    digest_parser = subparsers.add_parser(
        "digest",
        help="Aggregated backtest analysis: leaderboards, A/B comparisons, breadth stats",
    )
    digest_parser.add_argument(
        "--query",
        default="strategy",
        choices=QUERY_NAMES,
        help=(
            "Which analysis to run (default: strategy). "
            "Options: " + ", ".join(QUERY_NAMES)
        ),
    )
    digest_parser.add_argument(
        "--min-trades",
        type=int,
        default=None,
        dest="min_trades",
        help="Minimum closed trades to include (default: 5, or 3 for co_firing)",
    )
    digest_parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        dest="top_n",
        help="Max rows returned for combos query (default: 20)",
    )
    digest_parser.set_defaults(func=run_digest_cmd)
