"""Wifey CLI — `signal` subcommand (watch + test)."""

from __future__ import annotations

import argparse
import datetime
import pathlib

from analytics import signal_runner
from cli._common import parse_since_to_ms, parse_smt_pairs


def run_signal_test(args: argparse.Namespace) -> None:
    from analytics.signal_config import SignalWatchConfig, load_signal_config
    from analytics.signal_test_runner import run_signal_test as _run
    from analytics.strategies import KNOWN_STRATEGIES

    cfg = SignalWatchConfig()
    if getattr(args, "config", None):
        cfg = load_signal_config(args.config)

    from utils.binance_client import load_coins_config

    coins_config = load_coins_config()

    # CLI flags narrow down; config provides defaults; daemon-matching fallbacks.
    # Symbols: CLI → config → coins.json (mirrors signal_runner.py behaviour).
    symbols = args.symbol or cfg.symbols or list(coins_config.keys())
    timeframes = args.timeframe or cfg.timeframes or ["4h"]
    strategies = (
        args.strategy
        or cfg.strategies
        or [s for s in KNOWN_STRATEGIES if s != "seasonality"]
    )

    if not symbols:
        raise SystemExit(
            "error: no symbols found — pass --symbol or add symbols to your config/coins.json"
        )

    tp_r = args.tp_r if args.tp_r is not None else cfg.tp_r
    min_sl_pct = args.min_sl_pct if args.min_sl_pct is not None else cfg.min_sl_pct

    at_ms: int | None = None
    if args.at:
        try:
            dt = datetime.datetime.fromisoformat(args.at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.UTC)
            at_ms = int(dt.timestamp() * 1000)
        except ValueError:
            try:
                at_ms = int(args.at)
            except ValueError:
                raise SystemExit(
                    f"error: --at '{args.at}' is not a valid ISO datetime or Unix ms timestamp"
                ) from None

    # Build secondary_map from coins.json (same as signal_runner.py).
    secondary_map: dict[str, str] = {
        sym: coins_config[sym]["smt_secondary"]
        for sym in symbols
        if sym in coins_config and "smt_secondary" in coins_config[sym]
    }

    # Resolve since_ms: explicit --since > config's backtest.since (when --lookback
    # is still at its default, meaning the user hasn't explicitly overridden it).
    since_ms: int | None = None
    if getattr(args, "since", None):
        since_ms = parse_since_to_ms(args.since)
    elif args.lookback == 200 and cfg.backtest and cfg.backtest.since:
        since_ms = parse_since_to_ms(cfg.backtest.since)

    kwargs: dict[str, object] = {
        "symbols": symbols,
        "timeframes": timeframes,
        "strategies": strategies,
        "at_ms": at_ms,
        "lookback": args.lookback,
        "since_ms": since_ms,
        "tp_r": tp_r,
        "sl_pct": cfg.sl_pct,
        "min_sl_pct": min_sl_pct,
        "direction_filter": args.direction,
        "send_telegram": args.telegram,
        "backtest_cfg": cfg.backtest,
        "day_filter": cfg.day_filter,
        "secondary_map": secondary_map or None,
        "strategy_params": cfg.strategy_params or None,
        "bias_cfg": cfg.bias if cfg.bias.adr_suppress_threshold is not None else None,
        "atr_sl_multiplier": cfg.atr_sl_multiplier,
        "atr_sl_floor": cfg.atr_sl_floor,
    }
    if getattr(args, "db_path", None):
        kwargs["db_path"] = pathlib.Path(args.db_path)
    _run(**kwargs)  # type: ignore[arg-type]


def run_signal_watch(args: argparse.Namespace) -> None:
    from analytics.signal_config import SignalWatchConfig, load_signal_config

    cfg = SignalWatchConfig()
    if getattr(args, "config", None):
        cfg = load_signal_config(args.config)

    # CLI flags override config file values; None means "not provided by user"
    symbols = args.symbols if args.symbols is not None else cfg.symbols
    timeframes = args.timeframes if args.timeframes is not None else cfg.timeframes
    strategies = args.strategies if args.strategies is not None else cfg.strategies
    tp_r = args.tp_r if args.tp_r is not None else cfg.tp_r
    min_sl_pct = args.min_sl_pct if args.min_sl_pct is not None else cfg.min_sl_pct
    telegram = args.telegram or cfg.telegram  # either source can enable
    state_file = (
        args.state_file if args.state_file != "signal_state.json" else cfg.state_file
    )

    cli_smt_pairs: dict[str, str] | None = getattr(args, "smt_pairs", None)
    # CLI --smt-pairs overrides config [smt_pairs] table entirely when provided
    smt_pairs = cli_smt_pairs if cli_smt_pairs is not None else (cfg.smt_pairs or None)

    config_name = (
        pathlib.Path(args.config).stem if getattr(args, "config", None) else None
    )
    signal_runner.run_signal_watch(
        symbols=symbols,
        timeframes=timeframes,
        strategies=strategies,
        tp_r=tp_r,
        sl_pct=cfg.sl_pct,
        min_sl_pct=min_sl_pct,
        send_telegram=telegram,
        state_file=state_file,
        secondary_symbol=args.secondary_symbol,
        smt_pairs=smt_pairs,
        backtest_cfg=cfg.backtest,
        day_filter=cfg.day_filter,
        smt_trend_filter=cfg.smt_trend_filter,
        strategy_timeframes=cfg.strategy_timeframes or None,
        strategy_params=cfg.strategy_params or None,
        atr_sl_multiplier=cfg.atr_sl_multiplier,
        atr_sl_floor=cfg.atr_sl_floor,
        config_name=config_name,
        bias_cfg=cfg.bias,
        combo_cfg=cfg.combo,
    )


def add_signal_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    signal_parser = subparsers.add_parser("signal", help="Signal detection tools")
    signal_subparsers = signal_parser.add_subparsers(
        dest="signal_command", required=True
    )

    # 'watch' subcommand
    watch_parser = signal_subparsers.add_parser(
        "watch", help="Run 24/7 signal detection daemon"
    )
    watch_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="Path to TOML config file (e.g. config/signal_watch.toml). CLI flags override file values.",
    )
    watch_parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to scan (default: all from coins.json)",
    )
    watch_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help="Timeframes to scan (default: 4h, or from --config)",
    )
    watch_parser.add_argument(
        "--strategies",
        nargs="+",
        default=None,
        help="Strategies to run (default: all except seasonality)",
    )
    watch_parser.add_argument(
        "--tp-r",
        type=float,
        default=None,
        dest="tp_r",
        help="Take-profit risk:reward ratio for alert formatting (default: 2.0, or from --config)",
    )
    watch_parser.add_argument(
        "--telegram",
        action="store_true",
        help="Send alerts via Telegram",
    )
    watch_parser.add_argument(
        "--state-file",
        default="signal_state.json",
        dest="state_file",
        help="Path to candle watermark state file (default: signal_state.json)",
    )
    watch_parser.add_argument(
        "--secondary-symbol",
        default=None,
        dest="secondary_symbol",
        help="Secondary symbol for SMT divergence strategy (e.g. ETHUSDT) (deprecated — use --smt-pairs)",
    )
    watch_parser.add_argument(
        "--smt-pairs",
        default=None,
        dest="smt_pairs",
        type=parse_smt_pairs,
        help=(
            "Per-symbol SMT secondary mappings as comma-separated PRIMARY:SECONDARY tokens "
            "(e.g. BTCUSDT:ETHUSDT,ETHUSDT:BTCUSDT). Overrides smt_secondary in coins.json."
        ),
    )
    watch_parser.add_argument(
        "--min-sl-pct",
        type=float,
        default=None,
        dest="min_sl_pct",
        help="Minimum SL distance as a fraction of price (e.g. 0.005 = 0.5%%; default: 0 = disabled, or from --config)",
    )
    watch_parser.set_defaults(func=run_signal_watch)

    # 'test' subcommand
    test_parser = signal_subparsers.add_parser(
        "test",
        help="Fire a detector against historical data and print/send the formatted alert",
    )
    test_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="TOML config to inherit symbol/TF/tp_r/sl_pct defaults from.",
    )
    test_parser.add_argument(
        "--symbol", nargs="+", default=None, help="Symbol(s), e.g. BTCUSDT ETHUSDT"
    )
    test_parser.add_argument(
        "--timeframe", nargs="+", default=None, help="Timeframe(s), e.g. 1h 4h"
    )
    test_parser.add_argument(
        "--strategy",
        nargs="+",
        default=None,
        help="Strategy/strategies to test, e.g. bos fvg (default: all from --config or all known)",
    )
    test_parser.add_argument(
        "--at",
        default=None,
        metavar="TIMESTAMP",
        help=(
            "Pin to a specific candle. Accepts ISO datetime (e.g. 2026-04-07T02:00:00, "
            "treated as UTC unless offset given) or Unix ms integer. "
            "Defaults to latest available candle."
        ),
    )
    test_parser.add_argument(
        "--lookback",
        type=int,
        default=200,
        help="Number of candles to load (default: 200). Ignored when --since is set.",
    )
    test_parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Load all candles from this date up to --at (or now). Overrides --lookback. "
            "When --config is provided, defaults to [backtest].since from the TOML so "
            "results match the live daemon's history window."
        ),
    )
    test_parser.add_argument(
        "--direction",
        default=None,
        choices=["long", "short"],
        help="Only show signals in this direction.",
    )
    test_parser.add_argument(
        "--tp-r",
        type=float,
        default=None,
        dest="tp_r",
        help="TP risk:reward for alert formatting (default: 2.0 or from --config).",
    )
    test_parser.add_argument(
        "--min-sl-pct",
        type=float,
        default=None,
        dest="min_sl_pct",
        help="Minimum SL distance as fraction of price (default: 0 or from --config).",
    )
    test_parser.add_argument(
        "--telegram",
        action="store_true",
        help="Send the alert via Telegram in addition to printing it.",
    )
    test_parser.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        help="Path to analytics.db (default: project default).",
    )
    test_parser.set_defaults(func=run_signal_test)
