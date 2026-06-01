"""Wifey CLI — `backtest` subcommand (single-combo / sweep / combo / cross-TF modes)."""

from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Any

from analytics import backtest_runner
from analytics.backtest.live_parity_config import LiveParityConfig
from analytics.backtest_config import BacktestSweepConfig, load_backtest_config
from analytics.strategies import KNOWN_STRATEGIES
from cli._common import parse_since_to_ms

_LIVE_PARITY_GATES: tuple[str, ...] = (
    "regime",
    "direction_filter",
    "f8_htf_ema",
    "adr_bias",
    "conflict_resolver",
    "cooldown",
)


def _resolve_live_parity(
    args: argparse.Namespace, base: LiveParityConfig
) -> LiveParityConfig:
    """Layer CLI overrides on top of a TOML-loaded `LiveParityConfig`.

    `--live-parity` expands at resolve time into per-gate True values so an
    explicit `--without-<gate>` can disable a single gate while the master
    switch stays on. Per-gate `--with-<gate>` forces True; `--without-<gate>`
    forces False. Unspecified CLI flags inherit the base value.
    """
    master = bool(getattr(args, "live_parity_enabled", False))
    has_gate_override = any(
        getattr(args, f"live_parity_{gate}", None) is not None
        for gate in _LIVE_PARITY_GATES
    )
    if not master and not has_gate_override:
        return base

    overrides: dict[str, Any] = {}
    if master:
        overrides["enabled"] = True
        for gate in _LIVE_PARITY_GATES:
            overrides[gate] = True
    for gate in _LIVE_PARITY_GATES:
        val = getattr(args, f"live_parity_{gate}", None)
        if val is not None:
            overrides[gate] = bool(val)
    return replace(base, **overrides)


def run_backtest(args: argparse.Namespace) -> None:
    # Cross-TF co-firing mode: --cross-tf
    if getattr(args, "cross_tf", False):
        since_ms = parse_since_to_ms(args.since) if args.since else None
        htf_ltf_pairs: list[tuple[str, str]] | None = None
        if getattr(args, "htf_ltf", None):
            htf_ltf_pairs = []
            for pair_str in args.htf_ltf:
                parts = pair_str.split(":")
                if len(parts) == 2:
                    htf_ltf_pairs.append((parts[0].strip(), parts[1].strip()))
        backtest_runner.run_cross_tf_combo_backtest_cmd(
            symbols=args.symbols or [],
            htf_ltf_pairs=htf_ltf_pairs,
            days=args.days,
            window_hours=getattr(args, "window_hours", 4.0),
            sl_pct=args.sl_pct,
            tp_r=args.tp_r,
            fee_pct=args.fee_pct,
            min_trades=args.min_trades if args.min_trades is not None else 3,
            day_filter="tue_thu" if args.day_filter else "off",
            save_results=args.save,
            since_ms=since_ms,
            config_path=args.config,
            workers=getattr(args, "workers", None),
        )
        return

    # Co-firing confluence mode: --combo
    if getattr(args, "combo", False):
        since_ms = parse_since_to_ms(args.since) if args.since else None
        backtest_runner.run_combo_backtest_cmd(
            symbols=args.symbols or [],
            timeframes=args.timeframes or [],
            days=args.days,
            window=args.window,
            sl_pct=args.sl_pct,
            tp_r=args.tp_r,
            fee_pct=args.fee_pct,
            min_trades=args.min_trades if args.min_trades is not None else 3,
            day_filter="tue_thu" if args.day_filter else "off",
            save_results=args.save,
            since_ms=since_ms,
            config_path=args.config,
            workers=getattr(args, "workers", None),
        )
        return

    # Sweep mode: --config or --symbols (or --timeframes / --strategies alone)
    if args.config or args.symbols or args.timeframes or args.strategies:
        cfg = (
            load_backtest_config(args.config) if args.config else BacktestSweepConfig()
        )
        # CLI flags override TOML values
        if args.symbols:
            cfg.symbols = args.symbols
        if args.timeframes:
            cfg.timeframes = args.timeframes
        if args.strategies:
            cfg.strategies = args.strategies
        if args.days != 90:
            cfg.days = args.days
        if args.since:
            cfg.since = args.since
        if args.sl_pct != 0.02:
            cfg.sl_pct = args.sl_pct
        if args.tp_r != 2.0:
            cfg.tp_r = args.tp_r
        if args.fee_pct != 0.0:
            cfg.fee_pct = args.fee_pct
        if args.min_trades is not None:
            cfg.min_trades = args.min_trades
        if args.day_filter:
            cfg.day_filter = "tue_thu"
        if args.save:
            cfg.save_results = True
        if args.atr_sl_multiplier is not None:
            cfg.atr_sl_multiplier = args.atr_sl_multiplier
        if args.atr_sl_multiplier_values:
            cfg.atr_sl_multiplier_values = args.atr_sl_multiplier_values
        if getattr(args, "atr_sl_floor", False):
            cfg.atr_sl_floor = True
        cfg.live_parity = _resolve_live_parity(args, cfg.live_parity)
        backtest_runner.run_backtest_sweep(cfg)
        return

    # Single-combo mode: --symbol + --strategy (backward-compatible)
    if not args.symbol or not args.strategy:
        raise SystemExit(
            "error: --symbol and --strategy are required in single-combo mode"
        )
    backtest_runner.run_backtest_cmd(
        symbol=args.symbol,
        strategy=args.strategy,
        timeframe=args.interval,
        days=args.days,
        sl_pct=args.sl_pct,
        tp_r=args.tp_r,
        fee_pct=args.fee_pct,
        min_sl_pct=args.min_sl_pct
        if hasattr(args, "min_sl_pct") and args.min_sl_pct is not None
        else 0.0,
        atr_sl_multiplier=args.atr_sl_multiplier,
        atr_sl_floor=getattr(args, "atr_sl_floor", False),
        save_results=args.save,
        since_ms=parse_since_to_ms(args.since) if args.since else None,
        live_parity=_resolve_live_parity(args, LiveParityConfig()),
    )


def add_backtest_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    backtest_parser = subparsers.add_parser(
        "backtest", help="Backtest a trading strategy on historical data"
    )
    # Single-combo flags
    backtest_parser.add_argument(
        "--symbol",
        default=None,
        help="Primary symbol for single-combo mode (e.g., BTCUSDT)",
    )
    backtest_parser.add_argument(
        "--strategy",
        default=None,
        choices=KNOWN_STRATEGIES,
        help="Strategy for single-combo mode: " + ", ".join(KNOWN_STRATEGIES),
    )
    backtest_parser.add_argument(
        "--interval",
        default="4h",
        help="Candle timeframe for single-combo mode (default: 4h)",
    )
    # Shared / sweep flags
    backtest_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="TOML config file for sweep mode",
    )
    backtest_parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to sweep (overrides --config)",
    )
    backtest_parser.add_argument(
        "--strategies",
        nargs="+",
        default=None,
        choices=KNOWN_STRATEGIES,
        help="Strategies to sweep (overrides --config)",
    )
    backtest_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help="Timeframes to sweep (overrides --config)",
    )
    backtest_parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Lookback period in days (default: 90)",
    )
    backtest_parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Anchor start date for stable runs (e.g. 2025-09-12). Overrides --days when set.",
    )
    backtest_parser.add_argument(
        "--sl-pct",
        type=float,
        default=0.02,
        dest="sl_pct",
        help="Stop loss as a decimal fraction (default: 0.02 = 2%%)",
    )
    backtest_parser.add_argument(
        "--tp-r",
        type=float,
        default=2.0,
        dest="tp_r",
        help="Take profit in R multiples (default: 2.0)",
    )
    backtest_parser.add_argument(
        "--fee-pct",
        type=float,
        default=0.0,
        dest="fee_pct",
        help="Taker fee as a decimal fraction applied on entry+exit (default: 0.0; e.g. 0.0005 for 0.05%%)",
    )
    backtest_parser.add_argument(
        "--day-filter",
        action="store_true",
        default=False,
        dest="day_filter",
        help="Suppress Monday and Friday signals (ICT weekly cycle) before backtesting",
    )
    backtest_parser.add_argument(
        "--save",
        action="store_true",
        default=False,
        help="Persist aggregate results to backtest_runs table in DB",
    )
    backtest_parser.add_argument(
        "--atr-sl-multiplier",
        type=float,
        default=None,
        dest="atr_sl_multiplier",
        help="ATR-based SL multiplier: SL = N × ATR14 (overrides --sl-pct when set)",
    )
    backtest_parser.add_argument(
        "--atr-sl-values",
        nargs="+",
        type=float,
        default=None,
        dest="atr_sl_multiplier_values",
        help="ATR SL multiplier sweep: comparison table across values (e.g. 0.5 1.0 1.5 2.0 2.5)",
    )
    backtest_parser.add_argument(
        "--atr-sl-floor",
        action="store_true",
        default=False,
        dest="atr_sl_floor",
        help=(
            "F9: use atr_sl_multiplier × ATR14 as a minimum on top of structural sl_price "
            "(max of distances). Required to make the ATR multiplier bite on strategies "
            "that emit a structural sl_price."
        ),
    )
    backtest_parser.add_argument(
        "--min-trades",
        type=int,
        default=None,
        dest="min_trades",
        help="Hide combos below this trade count in sweep table (default: 20)",
    )
    backtest_parser.add_argument(
        "--combo",
        action="store_true",
        default=False,
        help="Run co-firing confluence backtests across all strategy pairs",
    )
    backtest_parser.add_argument(
        "--window",
        type=int,
        default=5,
        dest="window",
        help="Co-firing window: ±N candles for strategy pair detection (default: 5)",
    )
    backtest_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        dest="workers",
        help=(
            "Parallel workers for combo backtest (default: min(4, cpu_count-1)). "
            "Pass 1 to run serially."
        ),
    )
    backtest_parser.add_argument(
        "--cross-tf",
        action="store_true",
        default=False,
        dest="cross_tf",
        help="Run cross-TF co-firing backtests (HTF context + LTF entry)",
    )
    backtest_parser.add_argument(
        "--htf-ltf",
        nargs="+",
        default=None,
        dest="htf_ltf",
        help=(
            "HTF:LTF pairs for cross-TF sweep, e.g. '4h:15m 4h:1h 1h:15m'. "
            "Defaults to all 5 canonical pairs when omitted."
        ),
    )
    backtest_parser.add_argument(
        "--window-hours",
        type=float,
        default=4.0,
        dest="window_hours",
        help=(
            "Cross-TF lookback: how many hours back to search for an HTF signal "
            "(default: 4.0). Run the sweep across multiple values to find the optimum."
        ),
    )
    # T6 live-parity toggles (PR-1 plumbing; gate logic ships in PRs 2-5).
    backtest_parser.add_argument(
        "--live-parity",
        action="store_true",
        default=False,
        dest="live_parity_enabled",
        help="Master switch: enable every live-only gate (cancel individual ones with --without-<gate>)",
    )
    for _gate in _LIVE_PARITY_GATES:
        _dest = f"live_parity_{_gate}"
        _flag = _gate.replace("_", "-")
        backtest_parser.add_argument(
            f"--with-{_flag}",
            action="store_true",
            default=None,
            dest=_dest,
            help=f"Enable live-parity {_gate} gate (additive on top of --live-parity)",
        )
        backtest_parser.add_argument(
            f"--without-{_flag}",
            action="store_false",
            default=None,
            dest=_dest,
            help=f"Disable live-parity {_gate} gate (overrides --live-parity for this gate)",
        )

    backtest_parser.set_defaults(func=run_backtest)
