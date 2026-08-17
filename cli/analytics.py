"""Wifey CLI — `analytics` subcommand (backfill + sync)."""

from __future__ import annotations

import argparse

from analytics import analytics_runner
from cli._common import parse_since_to_ms


def run_analytics_backfill(args: argparse.Namespace) -> None:
    analytics_runner.run_backfill(
        symbols=args.symbols,
        timeframes=args.timeframes,
        since_ms=parse_since_to_ms(args.since),
        use_universe=args.universe,
        use_pundit=args.pundit,
    )


def run_analytics_sync(args: argparse.Namespace) -> None:
    analytics_runner.run_sync(
        symbols=args.symbols,
        timeframes=args.timeframes,
        use_universe=args.universe,
        use_pundit=args.pundit,
    )


def _add_universe_flags(parser: argparse.ArgumentParser, verb: str) -> None:
    """Attach the two alternate symbol-source flags; at most one may be given.

    Mutually exclusive at PARSE time rather than resolved by precedence inside
    ``_resolve_symbols``: under a silent precedence order ``--universe --pundit``
    would act on one set and ignore the other, and the only evidence of the wrong
    thing happening would be a log line nobody reads. argparse rejects it outright.
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--universe",
        action="store_true",
        help="Resolve symbols from config/universe.json (research breadth "
        "universe) instead of the stocks.json live watchlist",
    )
    group.add_argument(
        "--pundit",
        action="store_true",
        help=f"Resolve symbols from the pundit call ledger "
        f"(docs/plans/pundit-calls.jsonl) instead of the stocks.json live "
        f"watchlist. The ledger records index and futures UNDERLYINGS "
        f"(^GSPC, GC=F) that no watchlist carries, so nothing else {verb}s them.",
    )


def add_analytics_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    analytics_parser = subparsers.add_parser("analytics", help="Analytics data tools")
    analytics_subparsers = analytics_parser.add_subparsers(
        dest="analytics_command", required=True
    )

    # 'backfill' subcommand
    backfill_parser = analytics_subparsers.add_parser(
        "backfill", help="Full history backfill from yfinance"
    )
    backfill_parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to backfill (default: all from stocks.json)",
    )
    backfill_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1h", "4h"],
        help="Timeframes to backfill (default: 1h 4h)",
    )
    backfill_parser.add_argument(
        "--since",
        default="2023-01-01",
        help="Start date in YYYY-MM-DD format (default: 2023-01-01)",
    )
    _add_universe_flags(backfill_parser, "backfill")
    backfill_parser.set_defaults(func=run_analytics_backfill)

    # 'sync' subcommand
    sync_parser = analytics_subparsers.add_parser(
        "sync", help="Incremental sync since last stored candle"
    )
    sync_parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to sync (default: all from stocks.json)",
    )
    sync_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1h", "4h"],
        help="Timeframes to sync (default: 1h 4h)",
    )
    _add_universe_flags(sync_parser, "sync")
    sync_parser.set_defaults(func=run_analytics_sync)
