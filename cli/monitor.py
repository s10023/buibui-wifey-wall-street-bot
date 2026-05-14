"""Wifey CLI — `monitor` subcommand (price + position)."""

from __future__ import annotations

import argparse

from monitor import position_monitor, price_monitor


def run_price_monitor(args: argparse.Namespace) -> None:
    price_monitor.main(live=args.live, telegram=args.telegram, sort=args.sort)


def run_position_monitor(args: argparse.Namespace) -> None:
    position_monitor.main(
        sort=args.sort,
        telegram=args.telegram,
        hide_empty=args.hide_empty,
        compact=args.compact,
        live=args.live,
    )


def add_monitor_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    monitor_parser = subparsers.add_parser("monitor", help="Monitoring tools")
    monitor_subparsers = monitor_parser.add_subparsers(
        dest="monitor_command", required=True
    )

    # 'price' subcommand
    price_parser = monitor_subparsers.add_parser("price", help="Run price monitor")
    price_parser.add_argument("--live", action="store_true", help="Live refresh mode")
    price_parser.add_argument(
        "--sort",
        default="default",
        help="Sort table by column[:asc|desc]. Options: change_15m, change_1h, change_4h, change_asia, change_24h. Example: --sort change_15m:desc",
    )
    price_parser.add_argument(
        "--telegram", action="store_true", help="Send output to Telegram"
    )
    price_parser.set_defaults(func=run_price_monitor)

    # 'position' subcommand
    position_parser = monitor_subparsers.add_parser(
        "position", help="Run position monitor"
    )
    position_parser.add_argument(
        "--live", action="store_true", help="Live refresh mode"
    )
    position_parser.add_argument("--sort", default="default", help="Sort order")
    position_parser.add_argument(
        "--telegram", action="store_true", help="Send output to Telegram"
    )
    position_parser.add_argument(
        "--hide-empty", action="store_true", help="Hide symbols with no open positions"
    )
    position_parser.add_argument(
        "--compact", action="store_true", help="Show compact information"
    )
    position_parser.set_defaults(func=run_position_monitor)
