"""Wifey CLI — `web` subcommand (FastAPI server entry)."""

from __future__ import annotations

import argparse


def run_web_server(args: argparse.Namespace) -> None:
    import os

    import uvicorn

    if getattr(args, "config", None):
        os.environ["WIFEY_CONFIG"] = args.config

    uvicorn.run(
        "web.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def add_web_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    web_parser = subparsers.add_parser("web", help="Run FastAPI web backend")
    web_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    web_parser.add_argument(
        "--port", type=int, default=8000, help="Bind port (default: 8000)"
    )
    web_parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload (dev mode)"
    )
    web_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="Path to signal-watch TOML config (e.g. config/signal_watch.toml). "
        "Exposes config defaults to the UI via GET /api/active-config.",
    )
    web_parser.set_defaults(func=run_web_server)
