"""Wifey CLI entry — assembles argparse tree, dispatches to subcommand handlers."""

from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv

from cli import analytics, backtest, digest, param, recalibrate, signal, web


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Buibui Wifey Wall Street CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    signal.add_signal_subparser(subparsers)
    analytics.add_analytics_subparser(subparsers)
    backtest.add_backtest_subparser(subparsers)
    digest.add_digest_subparser(subparsers)
    param.add_param_sweep_subparser(subparsers)
    param.add_param_audit_subparser(subparsers)
    recalibrate.add_recalibrate_subparser(subparsers)
    web.add_web_subparser(subparsers)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
