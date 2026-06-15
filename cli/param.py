"""Wifey CLI — `param-sweep` and `param-audit` subcommands (top-level commands)."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from analytics.strategies import KNOWN_STRATEGIES
from cli._common import parse_since_to_ms

if TYPE_CHECKING:
    from analytics.backtest.cv_splits import CvConfig


def _cv_from_args(args: argparse.Namespace) -> CvConfig | None:
    """Build a CvConfig from --cv-* flags; None when mode is contiguous (legacy)."""
    if args.cv_mode != "purged":
        return None
    from analytics.backtest.cv_splits import CvConfig

    return CvConfig(
        mode="purged",
        n_folds=args.cv_folds,
        purge_bars=args.cv_purge_bars,
        embargo_bars=args.cv_embargo_bars,
    )


def run_param_sweep(args: argparse.Namespace) -> None:
    import duckdb

    from analytics.data_store import DEFAULT_DB_PATH
    from analytics.param_sweep import (
        ParamRange,
        _parse_param_spec,
        format_sweep_results,
    )
    from analytics.param_sweep import run_param_sweep as _run

    param_ranges: list[ParamRange] | None = None
    if args.params:
        try:
            param_ranges = [_parse_param_spec(s) for s in args.params]
        except ValueError as e:
            raise SystemExit(f"error: {e}") from e

    if param_ranges is None:
        from analytics.param_sweep import _default_param_ranges

        param_ranges = _default_param_ranges(args.strategy)

    _tf_defaults = {"15m": 20, "1h": 12, "4h": 5, "1d": 2}
    min_trades = (
        args.min_trades if args.min_trades else _tf_defaults.get(args.timeframe, 8)
    )

    grid_size = 1
    for r in param_ranges:
        grid_size *= len(r.values)

    _window = f"since {args.since}" if args.since else f"{args.days}d"
    print(f"\nParam sweep  {args.strategy} / {args.symbol} / {args.timeframe}")
    if args.cv_mode == "purged":
        print(f"Window: {_window}  CV: purged {args.cv_folds}-fold + embargo")
    else:
        print(
            f"Window: {_window}  WFO split: {args.wfo_split:.0%} IS / {1 - args.wfo_split:.0%} OOS"
        )
    print(f"Grid: {grid_size} combos  Min trades: {min_trades}  Top-N: {args.top_n}")
    print(f"Params: {', '.join(r.name for r in param_ranges)}")

    if grid_size > 5000:
        print(f"\n  WARNING: Grid has {grid_size} combos — this may take a while.")

    from analytics.perf_timer import timed

    db_path = args.db or DEFAULT_DB_PATH
    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
        with timed("param-sweep total"):
            report = _run(
                conn=conn,
                strategy=args.strategy,
                symbol=args.symbol,
                timeframe=args.timeframe,
                days=args.days,
                param_ranges=param_ranges,
                wfo_split=args.wfo_split,
                min_trades=min_trades,
                fee_pct=args.fee_pct,
                top_n=args.top_n,
                adr_suppress_threshold=args.adr_suppress_threshold,
                since_ms=parse_since_to_ms(args.since) if args.since else None,
                day_filter=args.day_filter,
                atr_sl_multiplier=args.atr_sl_multiplier,
                atr_sl_floor=args.atr_sl_floor,
                cv=_cv_from_args(args),
            )
    finally:
        conn.close()

    print(
        format_sweep_results(
            report.rows,
            args.strategy,
            args.symbol,
            args.timeframe,
            gate=report.gate,
        )
    )


def run_param_audit(args: argparse.Namespace) -> None:
    import duckdb

    from analytics.data_store import DEFAULT_DB_PATH
    from analytics.param_sweep import (
        format_audit_results,
        run_strategy_audit,
    )

    strategies = (
        args.strategies
        if args.strategies
        else [s for s in KNOWN_STRATEGIES if s != "seasonality"]
    )
    _tf_defaults = {"15m": 20, "1h": 12, "4h": 5, "1d": 2}
    min_trades = (
        args.min_trades if args.min_trades else _tf_defaults.get(args.timeframe, 8)
    )

    _window = f"since {args.since}" if args.since else f"{args.days}d"
    print(f"\nStrategy audit  {args.symbol} / {args.timeframe} / {_window}")
    print(f"Strategies: {len(strategies)}  WFO split: {args.wfo_split:.0%} IS")

    from analytics.perf_timer import timed

    db_path = args.db or DEFAULT_DB_PATH
    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
        with timed("param-audit total"):
            rows = run_strategy_audit(
                conn=conn,
                symbol=args.symbol,
                timeframe=args.timeframe,
                days=args.days,
                strategies=strategies,
                wfo_split=args.wfo_split,
                min_trades=min_trades,
                fee_pct=args.fee_pct,
                adr_suppress_threshold=args.adr_suppress_threshold,
                since_ms=parse_since_to_ms(args.since) if args.since else None,
                day_filter=args.day_filter,
                atr_sl_multiplier=args.atr_sl_multiplier,
                atr_sl_floor=args.atr_sl_floor,
            )
    finally:
        conn.close()

    print(
        format_audit_results(
            rows, args.symbol, args.timeframe, args.days, window=_window
        )
    )


def add_param_sweep_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    param_sweep_parser = subparsers.add_parser(
        "param-sweep",
        help="WFO parameter sweep: grid search + walk-forward validation for one strategy",
    )
    param_sweep_parser.add_argument(
        "--strategy",
        required=True,
        choices=KNOWN_STRATEGIES,
        help="Strategy to sweep: " + ", ".join(KNOWN_STRATEGIES),
    )
    param_sweep_parser.add_argument(
        "--symbol",
        required=True,
        help="Symbol (e.g. BTCUSDT)",
    )
    param_sweep_parser.add_argument(
        "--timeframe",
        required=True,
        help="Timeframe (e.g. 1h)",
    )
    param_sweep_parser.add_argument(
        "--param",
        action="append",
        dest="params",
        metavar="NAME=MIN:MAX:STEP",
        help="Param range override. Repeatable. E.g. --param tp_r=1.0:5.0:0.5",
    )
    param_sweep_parser.add_argument(
        "--wfo-split",
        type=float,
        default=0.7,
        dest="wfo_split",
        help="In-sample fraction for walk-forward split (default: 0.7)",
    )
    param_sweep_parser.add_argument(
        "--min-trades",
        type=int,
        default=0,
        dest="min_trades",
        help="Min closed trades in IS to score a config (default: auto by TF)",
    )
    param_sweep_parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        dest="top_n",
        help="Number of top configs to display (default: 10)",
    )
    param_sweep_parser.add_argument(
        "--days",
        type=int,
        default=180,
        help="Days of history to load (default: 180)",
    )
    param_sweep_parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Anchor start date for stable runs (e.g. 2025-09-12). Overrides --days when set.",
    )
    param_sweep_parser.add_argument(
        "--fee-pct",
        type=float,
        default=0.0005,
        dest="fee_pct",
        help="Taker fee fraction (default: 0.0005 = 0.05%%)",
    )
    param_sweep_parser.add_argument(
        "--adr-suppress-threshold",
        type=float,
        default=None,
        dest="adr_suppress_threshold",
        help="ADR suppress threshold (e.g. 0.80) — filter signals when today's range >= N × ADR-14",
    )
    param_sweep_parser.add_argument(
        "--day-filter",
        type=str,
        default="off",
        dest="day_filter",
        choices=["off", "weekdays", "tue_thu"],
        help="Restrict signals to allowed weekdays before WFO split (default: off)",
    )
    param_sweep_parser.add_argument(
        "--cv-mode",
        type=str,
        default="contiguous",
        dest="cv_mode",
        choices=["contiguous", "purged"],
        help="CV geometry: contiguous = legacy single IS/OOS split (default); "
        "purged = purged+embargoed K-fold CV (Phase 0.3c, LdP AFML ch. 7)",
    )
    param_sweep_parser.add_argument(
        "--cv-folds",
        type=int,
        default=5,
        dest="cv_folds",
        help="Fold count for --cv-mode purged (default: 5)",
    )
    param_sweep_parser.add_argument(
        "--cv-purge-bars",
        type=int,
        default=0,
        dest="cv_purge_bars",
        help="Bars purged from the end of each pre-test train segment (default: 0 — "
        "the engine already censors boundary-straddling trades as open)",
    )
    param_sweep_parser.add_argument(
        "--cv-embargo-bars",
        type=int,
        default=None,
        dest="cv_embargo_bars",
        help="Bars embargoed after each test fold (default: ~1%% of candles)",
    )
    param_sweep_parser.add_argument(
        "--atr-sl-multiplier",
        type=float,
        default=None,
        dest="atr_sl_multiplier",
        help="ATR-14 SL multiplier (e.g. 2.0). Used either as the SL itself (when "
        "sl_price is absent) or, with --atr-sl-floor, as a lower bound on structural SL distance.",
    )
    param_sweep_parser.add_argument(
        "--atr-sl-floor",
        action="store_true",
        default=False,
        dest="atr_sl_floor",
        help="F9 floor: widen structural SL to max(structural_dist, atr_mult × ATR14). "
        "No-op without --atr-sl-multiplier.",
    )
    param_sweep_parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to DuckDB database (default: analytics.db)",
    )
    param_sweep_parser.set_defaults(func=run_param_sweep)


def add_param_audit_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    param_audit_parser = subparsers.add_parser(
        "param-audit",
        help="Quick tp_r sweep across all strategies — verdict table showing which have edge",
    )
    param_audit_parser.add_argument(
        "--symbol", required=True, help="Symbol (e.g. BTCUSDT)"
    )
    param_audit_parser.add_argument(
        "--timeframe", required=True, help="Timeframe (e.g. 1h)"
    )
    param_audit_parser.add_argument(
        "--strategies",
        nargs="+",
        default=None,
        choices=KNOWN_STRATEGIES,
        help="Strategies to audit (default: all except seasonality)",
    )
    param_audit_parser.add_argument(
        "--days", type=int, default=180, help="Days of history (default: 180)"
    )
    param_audit_parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Anchor start date for stable runs (e.g. 2025-09-12). Overrides --days when set.",
    )
    param_audit_parser.add_argument(
        "--wfo-split",
        type=float,
        default=0.7,
        dest="wfo_split",
        help="In-sample fraction (default: 0.7)",
    )
    param_audit_parser.add_argument(
        "--min-trades",
        type=int,
        default=0,
        dest="min_trades",
        help="Min IS trades to score (default: auto by TF)",
    )
    param_audit_parser.add_argument(
        "--fee-pct",
        type=float,
        default=0.0005,
        dest="fee_pct",
        help="Taker fee fraction (default: 0.0005)",
    )
    param_audit_parser.add_argument(
        "--adr-suppress-threshold",
        type=float,
        default=None,
        dest="adr_suppress_threshold",
        help="ADR suppress threshold (e.g. 0.80) — filter signals when today's range >= N × ADR-14",
    )
    param_audit_parser.add_argument(
        "--day-filter",
        type=str,
        default="off",
        dest="day_filter",
        choices=["off", "weekdays", "tue_thu"],
        help="Restrict signals to allowed weekdays before WFO split (default: off)",
    )
    param_audit_parser.add_argument(
        "--atr-sl-multiplier",
        type=float,
        default=None,
        dest="atr_sl_multiplier",
        help="ATR-14 SL multiplier (e.g. 2.0). Used either as the SL itself (when "
        "sl_price is absent) or, with --atr-sl-floor, as a lower bound on structural SL distance.",
    )
    param_audit_parser.add_argument(
        "--atr-sl-floor",
        action="store_true",
        default=False,
        dest="atr_sl_floor",
        help="F9 floor: widen structural SL to max(structural_dist, atr_mult × ATR14). "
        "No-op without --atr-sl-multiplier.",
    )
    param_audit_parser.add_argument(
        "--db", type=str, default=None, help="DuckDB path (default: analytics.db)"
    )
    param_audit_parser.set_defaults(func=run_param_audit)
