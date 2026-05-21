"""Backtest runner — thin wrapper: opens DB, loads data, calls strategy + backtest libs."""

import datetime
import itertools
import logging
import os
import sys
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import duckdb
import pandas as pd

from analytics.backtest_config import BacktestSweepConfig
from analytics.backtest_lib import (
    BacktestResult,
    ComboBacktestResult,
    CrossTfComboBacktestResult,
    filter_signals_by_day,
    format_atr_sl_sweep_table,
    format_combo_table,
    format_cross_tf_combo_table,
    format_directional_volume_split,
    format_duration_table,
    format_result,
    format_seasonality,
    format_sweep_table,
    format_tp_sweep_table,
    format_volume_split,
    run_backtest,
    run_combo_backtest,
    run_cross_tf_combo_backtest,
)
from analytics.data_store import (
    DEFAULT_DB_PATH,
    get_ohlcv,
    init_schema,
    upsert_backtest_run,
    upsert_backtest_trades,
    upsert_combo_run,
    upsert_cross_tf_combo_run,
)
from analytics.digest_lib import run_digest
from analytics.perf_timer import timed
from analytics.signal_lib import _filter_signals_by_adr
from analytics.strategies import (
    DETECTOR_REGISTRY,
    KNOWN_STRATEGIES,
    detect_liquidity_sweep,
    seasonality_stats,
)
from utils.config_validation import load_stocks_config

_SIMPLE_DETECTORS = DETECTOR_REGISTRY

_SWEEP_STRATEGIES: list[str] = [s for s in KNOWN_STRATEGIES if s != "seasonality"]


def detect_signals_for_strategy(
    conn: duckdb.DuckDBPyConnection,
    ohlcv: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    start_ms: int,
    end_ms: int,
    liq_sweep_use_fib: bool = True,
    liq_sweep_fib_range_close: bool = False,
) -> pd.DataFrame | None:
    """Return signals DataFrame.

    ohlcv must already be fetched and non-empty by the caller.
    """
    if strategy == "liquidity_sweep":
        return detect_liquidity_sweep(
            ohlcv,
            use_fib_extension=liq_sweep_use_fib,
            fib_require_range_close=liq_sweep_fib_range_close,
        )

    return _SIMPLE_DETECTORS[strategy](ohlcv)


def _collect_signals_map(
    conn: duckdb.DuckDBPyConnection,
    cfg: BacktestSweepConfig,
    symbols: list[str],
    strategies: list[str],
    start_ms: int,
    end_ms: int,
) -> tuple[dict[tuple[str, str, str], tuple[pd.DataFrame, pd.DataFrame]], list[str]]:
    """Detect signals once for all symbol × TF × strategy combos.

    Returns a map keyed by (symbol, timeframe, strategy) →
    (ohlcv, filtered_signals) plus a skipped list.
    Used by tp_r sweep mode to avoid re-running detection for each tp_r value.
    """
    from analytics.signal_config import _day_filter_to_weekdays

    allowed_days = _day_filter_to_weekdays(cfg.day_filter)
    signals_map: dict[tuple[str, str, str], tuple[pd.DataFrame, pd.DataFrame]] = {}
    skipped: list[str] = []
    ohlcv_cache: dict[tuple[str, str], pd.DataFrame] = {}

    for symbol, timeframe, strategy in itertools.product(
        symbols, cfg.timeframes, strategies
    ):
        if strategy == "seasonality":
            continue

        ohlcv_key = (symbol, timeframe)
        if ohlcv_key not in ohlcv_cache:
            ohlcv_cache[ohlcv_key] = get_ohlcv(
                conn, symbol, timeframe, start_ms, end_ms
            )
        ohlcv = ohlcv_cache[ohlcv_key]
        if ohlcv.empty:
            skipped.append(f"{symbol}/{timeframe}/{strategy} (no data)")
            continue

        signals = detect_signals_for_strategy(
            conn,
            ohlcv,
            symbol,
            timeframe,
            strategy,
            start_ms,
            end_ms,
            liq_sweep_use_fib=cfg.liq_sweep_use_fib,
            liq_sweep_fib_range_close=cfg.liq_sweep_fib_range_close,
        )
        if signals is None:
            skipped.append(f"{symbol}/{timeframe}/{strategy} (no data)")
            continue

        if allowed_days is not None:
            signals = filter_signals_by_day(signals, allowed_days)

        signals_map[(symbol, timeframe, strategy)] = (ohlcv, signals)

    return signals_map, skipped


def _collect_sweep_results(
    conn: duckdb.DuckDBPyConnection,
    cfg: BacktestSweepConfig,
    tp_r: float,
    symbols: list[str],
    strategies: list[str],
    start_ms: int,
    end_ms: int,
    sweep_id: str | None = None,
) -> tuple[list[BacktestResult], list[str]]:
    """Run one full symbol × TF × strategy grid for a given tp_r value."""
    from analytics.signal_config import _day_filter_to_weekdays

    allowed_days = _day_filter_to_weekdays(cfg.day_filter)
    results: list[BacktestResult] = []
    skipped: list[str] = []
    ohlcv_cache: dict[tuple[str, str], pd.DataFrame] = {}

    for symbol, timeframe, strategy in itertools.product(
        symbols, cfg.timeframes, strategies
    ):
        if strategy == "seasonality":
            continue

        ohlcv_key = (symbol, timeframe)
        if ohlcv_key not in ohlcv_cache:
            ohlcv_cache[ohlcv_key] = get_ohlcv(
                conn, symbol, timeframe, start_ms, end_ms
            )
        ohlcv = ohlcv_cache[ohlcv_key]
        if ohlcv.empty:
            skipped.append(f"{symbol}/{timeframe}/{strategy} (no data)")
            continue

        signals = detect_signals_for_strategy(
            conn,
            ohlcv,
            symbol,
            timeframe,
            strategy,
            start_ms,
            end_ms,
            liq_sweep_use_fib=cfg.liq_sweep_use_fib,
            liq_sweep_fib_range_close=cfg.liq_sweep_fib_range_close,
        )
        if signals is None:
            skipped.append(f"{symbol}/{timeframe}/{strategy} (no data)")
            continue

        if allowed_days is not None:
            signals = filter_signals_by_day(signals, allowed_days)

        if (
            cfg.adr_suppress_threshold is not None
            and not cfg.is_adr_exempt(strategy)
            and not signals.empty
        ):
            signals = _filter_signals_by_adr(ohlcv, signals, cfg.adr_suppress_threshold)

        eff_tp_r = cfg.effective_tp_r(strategy, symbol, timeframe)
        eff_sl_pct = cfg.effective_sl_pct(strategy, symbol, timeframe)
        eff_atr_sl = cfg.effective_atr_sl_multiplier(strategy, symbol, timeframe)
        bt = run_backtest(
            ohlcv,
            signals,
            symbol,
            timeframe,
            strategy,
            eff_sl_pct,
            eff_tp_r,
            cfg.fee_pct,
            min_sl_pct=cfg.min_sl_pct,
            atr_sl_multiplier=eff_atr_sl,
            atr_sl_floor=cfg.atr_sl_floor,
            volume_suppress=cfg.effective_volume_suppress(strategy),
            volume_spike_boost=cfg.effective_volume_spike_boost(strategy),
            volume_suppress_long=cfg.effective_volume_suppress_long(strategy),
            volume_suppress_short=cfg.effective_volume_suppress_short(strategy),
            volume_spike_boost_long=cfg.effective_volume_spike_boost_long(strategy),
            volume_spike_boost_short=cfg.effective_volume_spike_boost_short(strategy),
        )
        results.append(bt)

        if cfg.save_results and sweep_id is not None:
            run_id = upsert_backtest_run(
                conn,
                bt,
                days=cfg.days,
                data_start_ms=start_ms,
                data_end_ms=end_ms,
                sl_pct=eff_sl_pct,
                tp_r=eff_tp_r,
                fee_pct=cfg.fee_pct,
                day_filter=cfg.day_filter,
                sweep_id=sweep_id,
                adr_suppress_threshold=cfg.adr_suppress_threshold,
                volume_suppress=cfg.effective_volume_suppress(strategy) or None,
            )
            upsert_backtest_trades(conn, bt, run_id)

    return results, skipped


def run_backtest_sweep(
    cfg: BacktestSweepConfig,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Run all symbol × timeframe × strategy combos and print a ranked table."""
    end_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    if cfg.since is not None:
        _since_dt = datetime.datetime.strptime(cfg.since, "%Y-%m-%d").replace(
            tzinfo=datetime.UTC
        )
        start_ms = int(_since_dt.timestamp() * 1000)
    else:
        start_ms = end_ms - cfg.days * 24 * 3_600 * 1_000

    symbols = cfg.symbols
    if symbols is None:
        symbols = list(load_stocks_config().keys())

    strategies = cfg.strategies if cfg.strategies is not None else _SWEEP_STRATEGIES

    n_strats = len(strategies)
    strat_word = "strategy" if n_strats == 1 else "strategies"
    n_tfs = len(cfg.timeframes)
    tf_word = "timeframe" if n_tfs == 1 else "timeframes"
    n_syms = len(symbols)
    sym_word = "symbol" if n_syms == 1 else "symbols"

    tp_sweep_mode = bool(cfg.tp_r_values)
    atr_sweep_mode = bool(cfg.atr_sl_multiplier_values)
    window_label = f"since {cfg.since}" if cfg.since is not None else f"{cfg.days}d"

    if tp_sweep_mode:
        print(
            f"Backtest TP Sweep — {n_syms} {sym_word} × "
            f"{n_tfs} {tf_word} × "
            f"{n_strats} {strat_word} ({window_label}) "
            f"× tp_r {cfg.tp_r_values}"
        )
    elif atr_sweep_mode:
        print(
            f"Backtest ATR SL Sweep — {n_syms} {sym_word} × "
            f"{n_tfs} {tf_word} × "
            f"{n_strats} {strat_word} ({window_label}) "
            f"× atr_sl_multiplier {cfg.atr_sl_multiplier_values}"
        )
    else:
        print(
            f"Backtest Sweep — {n_syms} {sym_word} × "
            f"{n_tfs} {tf_word} × "
            f"{n_strats} {strat_word} ({window_label})"
        )

    single_run_mode = not tp_sweep_mode and not atr_sweep_mode
    sweep_id = str(uuid.uuid4()) if cfg.save_results and single_run_mode else None
    conn: duckdb.DuckDBPyConnection = duckdb.connect(
        str(db_path), read_only=not (cfg.save_results and single_run_mode)
    )

    try:
        if tp_sweep_mode:
            # Detect signals once — tp_r does not affect signal detection
            signals_map, skipped = _collect_signals_map(
                conn, cfg, symbols, strategies, start_ms, end_ms
            )
            results_by_tp: dict[float, list[BacktestResult]] = {}
            for tp_r in cfg.tp_r_values:
                tp_results: list[BacktestResult] = []
                for (sym, tf, strat), (ohlcv, sigs) in signals_map.items():
                    # tp_r is swept globally; per-strategy sl_pct/atr_sl overrides still apply.
                    bt = run_backtest(
                        ohlcv,
                        sigs,
                        sym,
                        tf,
                        strat,
                        cfg.effective_sl_pct(strat, sym, tf),
                        tp_r,
                        cfg.fee_pct,
                        min_sl_pct=cfg.min_sl_pct,
                        atr_sl_multiplier=cfg.effective_atr_sl_multiplier(
                            strat, sym, tf
                        ),
                        atr_sl_floor=cfg.atr_sl_floor,
                        volume_suppress=cfg.effective_volume_suppress(strat),
                        volume_spike_boost=cfg.effective_volume_spike_boost(strat),
                        volume_suppress_long=cfg.effective_volume_suppress_long(strat),
                        volume_suppress_short=cfg.effective_volume_suppress_short(
                            strat
                        ),
                        volume_spike_boost_long=cfg.effective_volume_spike_boost_long(
                            strat
                        ),
                        volume_spike_boost_short=cfg.effective_volume_spike_boost_short(
                            strat
                        ),
                    )
                    tp_results.append(bt)
                results_by_tp[tp_r] = tp_results
            # Duration uses results from default tp_r (or first value)
            duration_results = results_by_tp.get(
                cfg.tp_r, next(iter(results_by_tp.values()))
            )
            print(format_tp_sweep_table(results_by_tp))
            print(format_duration_table(duration_results))
        elif atr_sweep_mode:
            # Detect signals once — ATR multiplier affects only SL placement, not detection
            signals_map, skipped = _collect_signals_map(
                conn, cfg, symbols, strategies, start_ms, end_ms
            )
            results_by_atr: dict[float, list[BacktestResult]] = {}
            for atr_mult in cfg.atr_sl_multiplier_values:
                atr_results: list[BacktestResult] = []
                for (sym, tf, strat), (ohlcv, sigs) in signals_map.items():
                    # atr_sl_multiplier is swept globally; per-strategy tp_r overrides apply.
                    bt = run_backtest(
                        ohlcv,
                        sigs,
                        sym,
                        tf,
                        strat,
                        cfg.effective_sl_pct(strat, sym, tf),
                        cfg.effective_tp_r(strat, sym, tf),
                        cfg.fee_pct,
                        min_sl_pct=cfg.min_sl_pct,
                        atr_sl_multiplier=atr_mult,
                        atr_sl_floor=cfg.atr_sl_floor,
                        volume_suppress=cfg.effective_volume_suppress(strat),
                        volume_spike_boost=cfg.effective_volume_spike_boost(strat),
                        volume_suppress_long=cfg.effective_volume_suppress_long(strat),
                        volume_suppress_short=cfg.effective_volume_suppress_short(
                            strat
                        ),
                        volume_spike_boost_long=cfg.effective_volume_spike_boost_long(
                            strat
                        ),
                        volume_spike_boost_short=cfg.effective_volume_spike_boost_short(
                            strat
                        ),
                    )
                    atr_results.append(bt)
                results_by_atr[atr_mult] = atr_results
            duration_results_atr = results_by_atr.get(
                cfg.atr_sl_multiplier_values[0],
                next(iter(results_by_atr.values())),
            )
            print(format_atr_sl_sweep_table(results_by_atr))
            print(format_duration_table(duration_results_atr))
        else:
            with timed("backtest sweep"):
                results, skipped = _collect_sweep_results(
                    conn, cfg, cfg.tp_r, symbols, strategies, start_ms, end_ms, sweep_id
                )
            print(
                format_sweep_table(
                    results, cfg.min_trades, cfg.min_trades_per_tf or None
                )
            )
            print(format_volume_split(results))
            print(format_directional_volume_split(results))
            print(format_duration_table(results))
            if cfg.save_results:
                print(f"\n  Results saved to DB (sweep_id={sweep_id})")

    finally:
        conn.close()

    if skipped:
        print(f"\n  Skipped {len(skipped)} combo(s):")
        for s in skipped:
            print(f"    • {s}")


def run_backtest_cmd(
    symbol: str,
    strategy: str,
    timeframe: str,
    days: int,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    save_results: bool = False,
    since_ms: int | None = None,
) -> None:
    """Open DB, load OHLCV, detect signals, run backtest, print results."""
    if strategy not in KNOWN_STRATEGIES:
        logging.error(
            "Unknown strategy '%s'. Choose from: %s",
            strategy,
            ", ".join(KNOWN_STRATEGIES),
        )
        sys.exit(1)

    end_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    start_ms = since_ms if since_ms is not None else end_ms - days * 24 * 3_600 * 1_000

    conn: duckdb.DuckDBPyConnection = duckdb.connect(
        str(db_path), read_only=not save_results
    )
    try:
        ohlcv = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
        if ohlcv.empty:
            logging.error(
                "No OHLCV data for %s %s. Run 'analytics backfill' first.",
                symbol,
                timeframe,
            )
            sys.exit(1)

        if strategy == "seasonality":
            stats = seasonality_stats(ohlcv)
            print(format_seasonality(stats))
            return

        signals = detect_signals_for_strategy(
            conn, ohlcv, symbol, timeframe, strategy, start_ms, end_ms
        )
        if signals is None:
            sys.exit(1)

        bt_result = run_backtest(
            ohlcv,
            signals,
            symbol,
            timeframe,
            strategy,
            sl_pct,
            tp_r,
            fee_pct,
            min_sl_pct=min_sl_pct,
            atr_sl_multiplier=atr_sl_multiplier,
            atr_sl_floor=atr_sl_floor,
        )
        print(format_result(bt_result))

        if save_results:
            run_id = upsert_backtest_run(
                conn,
                bt_result,
                days=days,
                data_start_ms=start_ms,
                data_end_ms=end_ms,
                sl_pct=sl_pct,
                tp_r=tp_r,
                fee_pct=fee_pct,
                day_filter="off",
            )
            upsert_backtest_trades(conn, bt_result, run_id)
            print(f"\n  Results saved to DB (run_id={run_id})")

    finally:
        conn.close()


def _combo_worker(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    allowed_days: list[int] | None,
    window: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    min_sl_pct: float,
    min_signals: int,
    db_path: Path,
) -> tuple[list[ComboBacktestResult], list[str]]:
    """Per-(symbol, TF) worker: opens its own DB connection, detects all strategies,
    runs all valid pair combos, and returns results + skipped messages.

    Must be a top-level function so ProcessPoolExecutor can pickle it.
    """
    from analytics.strategies import INCOMPATIBLE_PAIRS, KNOWN_STRATEGIES

    _non_seasonal = [s for s in KNOWN_STRATEGIES if s != "seasonality"]
    combo_results: list[ComboBacktestResult] = []
    skipped: list[str] = []

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
        ohlcv = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
        if ohlcv.empty:
            skipped.append(f"{symbol}/{timeframe} (no data)")
            return combo_results, skipped

        signals_cache: dict[str, pd.DataFrame] = {}
        for strategy in _non_seasonal:
            sigs = detect_signals_for_strategy(
                conn, ohlcv, symbol, timeframe, strategy, start_ms, end_ms
            )
            if sigs is None:
                continue
            if allowed_days is not None:
                sigs = filter_signals_by_day(sigs, allowed_days)
            if len(sigs) >= min_signals:
                signals_cache[strategy] = sigs

        if not signals_cache:
            return combo_results, skipped

        strategies_with_signals = list(signals_cache.keys())
        for i, strat_a in enumerate(strategies_with_signals):
            for strat_b in strategies_with_signals[i + 1 :]:
                pair = frozenset({strat_a, strat_b})
                if pair in INCOMPATIBLE_PAIRS:
                    skipped.append(
                        f"{symbol}/{timeframe}: {strat_a}+{strat_b} (incompatible)"
                    )
                    continue
                c = run_combo_backtest(
                    ohlcv,
                    signals_cache[strat_a],
                    signals_cache[strat_b],
                    symbol,
                    timeframe,
                    strat_a,
                    strat_b,
                    window=window,
                    sl_pct=sl_pct,
                    tp_r=tp_r,
                    fee_pct=fee_pct,
                    min_sl_pct=min_sl_pct,
                    min_signals=min_signals,
                )
                combo_results.append(c)
    finally:
        conn.close()

    return combo_results, skipped


def run_combo_backtest_cmd(
    symbols: list[str],
    timeframes: list[str],
    days: int,
    window: int = 5,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    min_sl_pct: float = 0.0,
    min_signals: int = 3,
    min_trades: int = 3,
    day_filter: str = "off",
    save_results: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    since_ms: int | None = None,
    config_path: Path | None = None,
    workers: int | None = None,
) -> None:
    """Run all valid strategy-pair co-firing backtests and print a ranked table.

    Loads a TOML config when config_path is set; otherwise uses the provided
    symbols/timeframes directly.  Incompatible pairs (e.g. bos + ote_entry)
    are skipped automatically.

    workers controls parallelism over symbol×TF chunks.  Defaults to
    min(4, cpu_count - 1).  Pass workers=1 to run serially (no subprocess
    overhead — useful when other heavy processes are running).
    """
    from analytics.signal_config import _day_filter_to_weekdays

    if config_path is not None:
        from analytics.backtest_config import load_backtest_config

        cfg = load_backtest_config(config_path)
        symbols = symbols or cfg.symbols or []
        if not symbols:
            symbols = list(load_stocks_config().keys())
        timeframes = timeframes or cfg.timeframes or []
        if day_filter == "off":
            day_filter = cfg.day_filter
        if not sl_pct or sl_pct == 0.02:
            sl_pct = cfg.sl_pct
        if tp_r == 2.0:
            tp_r = cfg.tp_r
        if not fee_pct:
            fee_pct = cfg.fee_pct
        if cfg.since:
            since_ms = int(
                datetime.datetime.fromisoformat(cfg.since)
                .replace(tzinfo=datetime.UTC)
                .timestamp()
                * 1000
            )

    end_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    start_ms = since_ms if since_ms is not None else end_ms - days * 24 * 3_600 * 1_000
    allowed_days = _day_filter_to_weekdays(day_filter)

    _max_workers = (
        workers if workers is not None else min(4, max(1, (os.cpu_count() or 1) - 1))
    )

    chunks = [(sym, tf) for sym in symbols for tf in timeframes]
    combo_results: list[ComboBacktestResult] = []
    skipped: list[str] = []

    worker_kwargs = {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "allowed_days": allowed_days,
        "window": window,
        "sl_pct": sl_pct,
        "tp_r": tp_r,
        "fee_pct": fee_pct,
        "min_sl_pct": min_sl_pct,
        "min_signals": min_signals,
        "db_path": db_path,
    }

    if _max_workers == 1:
        # Serial path — no subprocess overhead, easier to debug.
        for sym, tf in chunks:
            results, skips = _combo_worker(sym, tf, **worker_kwargs)  # type: ignore[arg-type]
            combo_results.extend(results)
            skipped.extend(skips)
    else:
        with ProcessPoolExecutor(max_workers=_max_workers) as pool:
            futures = {
                pool.submit(_combo_worker, sym, tf, **worker_kwargs): (sym, tf)  # type: ignore[arg-type]
                for sym, tf in chunks
            }
            for fut in as_completed(futures):
                results, skips = fut.result()
                combo_results.extend(results)
                skipped.extend(skips)

    # DB writes happen in the main process — avoids concurrent write contention.
    if save_results and combo_results:
        conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=False)
        try:
            for c in combo_results:
                if len(c.result.closed_trades) >= min_trades:
                    upsert_combo_run(
                        conn,
                        c,
                        days=days,
                        data_start_ms=start_ms,
                        data_end_ms=end_ms,
                        sl_pct=sl_pct,
                        tp_r=tp_r,
                        fee_pct=fee_pct,
                        day_filter=day_filter,
                    )
        finally:
            conn.close()

    print(format_combo_table(combo_results, min_trades=min_trades))
    if save_results:
        saved = sum(
            1 for c in combo_results if len(c.result.closed_trades) >= min_trades
        )
        print(f"\n  {saved} combo(s) saved to DB.")
    if skipped:
        print(f"\n  Skipped {len(skipped)} combo(s):")
        for s in skipped[:10]:
            print(f"    • {s}")
        if len(skipped) > 10:
            print(f"    … and {len(skipped) - 10} more")


def _cross_tf_combo_worker(
    symbol: str,
    tf_htf: str,
    tf_ltf: str,
    start_ms: int,
    end_ms: int,
    allowed_days: list[int] | None,
    window_hours: float,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    min_sl_pct: float,
    min_signals: int,
    db_path: Path,
) -> tuple[list[CrossTfComboBacktestResult], list[str]]:
    """Per-(symbol, tf_htf, tf_ltf) worker for cross-TF combo sweep.

    Opens its own DB connection, detects all strategies on both TFs, runs all
    ordered strategy_htf × strategy_ltf pairs, returns results + skipped messages.

    Must be a top-level function so ProcessPoolExecutor can pickle it.
    """
    from analytics.strategies import INCOMPATIBLE_PAIRS, KNOWN_STRATEGIES

    _non_seasonal = [s for s in KNOWN_STRATEGIES if s != "seasonality"]
    results: list[CrossTfComboBacktestResult] = []
    skipped: list[str] = []

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
        ohlcv_htf = get_ohlcv(conn, symbol, tf_htf, start_ms, end_ms)
        ohlcv_ltf = get_ohlcv(conn, symbol, tf_ltf, start_ms, end_ms)
        if ohlcv_htf.empty:
            skipped.append(f"{symbol}/{tf_htf} (no HTF data)")
            return results, skipped
        if ohlcv_ltf.empty:
            skipped.append(f"{symbol}/{tf_ltf} (no LTF data)")
            return results, skipped

        # Detect signals on each TF once, cache for all pair combinations.
        htf_signals_cache: dict[str, pd.DataFrame] = {}
        ltf_signals_cache: dict[str, pd.DataFrame] = {}

        for strategy in _non_seasonal:
            sigs_htf = detect_signals_for_strategy(
                conn, ohlcv_htf, symbol, tf_htf, strategy, start_ms, end_ms
            )
            if sigs_htf is not None:
                if allowed_days is not None:
                    sigs_htf = filter_signals_by_day(sigs_htf, allowed_days)
                if len(sigs_htf) >= min_signals:
                    htf_signals_cache[strategy] = sigs_htf

            sigs_ltf = detect_signals_for_strategy(
                conn, ohlcv_ltf, symbol, tf_ltf, strategy, start_ms, end_ms
            )
            if sigs_ltf is not None:
                if allowed_days is not None:
                    sigs_ltf = filter_signals_by_day(sigs_ltf, allowed_days)
                if len(sigs_ltf) >= min_signals:
                    ltf_signals_cache[strategy] = sigs_ltf

        if not htf_signals_cache or not ltf_signals_cache:
            return results, skipped

        # Run all ordered strategy_htf × strategy_ltf pairs.
        for strat_htf, sigs_htf in htf_signals_cache.items():
            for strat_ltf, sigs_ltf in ltf_signals_cache.items():
                pair = frozenset({strat_htf, strat_ltf})
                if pair in INCOMPATIBLE_PAIRS:
                    skipped.append(
                        f"{symbol}/{tf_htf}+{tf_ltf}: "
                        f"{strat_htf}+{strat_ltf} (incompatible)"
                    )
                    continue
                c = run_cross_tf_combo_backtest(
                    ohlcv_ltf,
                    sigs_htf,
                    sigs_ltf,
                    symbol,
                    tf_htf,
                    tf_ltf,
                    strat_htf,
                    strat_ltf,
                    window_hours=window_hours,
                    sl_pct=sl_pct,
                    tp_r=tp_r,
                    fee_pct=fee_pct,
                    min_sl_pct=min_sl_pct,
                    min_signals=min_signals,
                )
                results.append(c)
    finally:
        conn.close()

    return results, skipped


# Default HTF/LTF pairs for cross-TF sweep.
_DEFAULT_HTF_LTF_PAIRS = [
    ("4h", "15m"),
    ("4h", "1h"),
    ("1h", "15m"),
    ("1d", "4h"),
    ("1d", "1h"),
]


def run_cross_tf_combo_backtest_cmd(
    symbols: list[str],
    htf_ltf_pairs: list[tuple[str, str]] | None = None,
    days: int = 200,
    window_hours: float = 4.0,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    min_sl_pct: float = 0.0,
    min_signals: int = 3,
    min_trades: int = 3,
    day_filter: str = "off",
    save_results: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    since_ms: int | None = None,
    config_path: Path | None = None,
    workers: int | None = None,
) -> None:
    """Sweep all cross-TF strategy-pair co-firing backtests and print a ranked table.

    For each (symbol, tf_htf, tf_ltf) chunk, detects signals on both TFs and
    runs all ordered strategy_htf × strategy_ltf combinations where a same-direction
    HTF signal fired within window_hours before the LTF signal.

    workers controls parallelism over symbol × TF-pair chunks. Defaults to
    min(4, cpu_count - 1). Pass workers=1 to run serially.
    """
    from analytics.signal_config import _day_filter_to_weekdays

    if htf_ltf_pairs is None:
        htf_ltf_pairs = _DEFAULT_HTF_LTF_PAIRS

    if config_path is not None:
        from analytics.backtest_config import load_backtest_config

        cfg = load_backtest_config(config_path)
        if not symbols:
            symbols = cfg.symbols or []
        if not symbols:
            symbols = list(load_stocks_config().keys())
        if day_filter == "off":
            day_filter = cfg.day_filter
        if not sl_pct or sl_pct == 0.02:
            sl_pct = cfg.sl_pct
        if tp_r == 2.0:
            tp_r = cfg.tp_r
        if not fee_pct:
            fee_pct = cfg.fee_pct
        if cfg.since:
            since_ms = int(
                datetime.datetime.fromisoformat(cfg.since)
                .replace(tzinfo=datetime.UTC)
                .timestamp()
                * 1000
            )

    end_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    start_ms = since_ms if since_ms is not None else end_ms - days * 24 * 3_600 * 1_000
    allowed_days = _day_filter_to_weekdays(day_filter)

    _max_workers = (
        workers if workers is not None else min(4, max(1, (os.cpu_count() or 1) - 1))
    )

    # Chunks: one worker per (symbol, htf, ltf) tuple.
    chunks = [(sym, htf, ltf) for sym in symbols for htf, ltf in htf_ltf_pairs]
    combo_results: list[CrossTfComboBacktestResult] = []
    skipped: list[str] = []

    worker_kwargs = {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "allowed_days": allowed_days,
        "window_hours": window_hours,
        "sl_pct": sl_pct,
        "tp_r": tp_r,
        "fee_pct": fee_pct,
        "min_sl_pct": min_sl_pct,
        "min_signals": min_signals,
        "db_path": db_path,
    }

    if _max_workers == 1:
        for sym, htf, ltf in chunks:
            r, s = _cross_tf_combo_worker(sym, htf, ltf, **worker_kwargs)  # type: ignore[arg-type]
            combo_results.extend(r)
            skipped.extend(s)
    else:
        with ProcessPoolExecutor(max_workers=_max_workers) as pool:
            futures = {
                pool.submit(_cross_tf_combo_worker, sym, htf, ltf, **worker_kwargs): (  # type: ignore[arg-type]
                    sym,
                    htf,
                    ltf,
                )
                for sym, htf, ltf in chunks
            }
            for fut in as_completed(futures):
                r, s = fut.result()
                combo_results.extend(r)
                skipped.extend(s)

    # DB writes in main process — avoid concurrent write contention.
    if save_results and combo_results:
        conn_w: duckdb.DuckDBPyConnection = duckdb.connect(
            str(db_path), read_only=False
        )
        init_schema(conn_w)
        try:
            for c in combo_results:
                if len(c.result.closed_trades) >= min_trades:
                    upsert_cross_tf_combo_run(
                        conn_w,
                        c,
                        days=days,
                        data_start_ms=start_ms,
                        data_end_ms=end_ms,
                        sl_pct=sl_pct,
                        tp_r=tp_r,
                        fee_pct=fee_pct,
                        day_filter=day_filter,
                    )
        finally:
            conn_w.close()

    print(format_cross_tf_combo_table(combo_results, min_trades=min_trades))
    if save_results:
        saved = sum(
            1 for c in combo_results if len(c.result.closed_trades) >= min_trades
        )
        print(f"\n  {saved} cross-TF combo(s) saved to DB.")
    if skipped:
        print(f"\n  Skipped {len(skipped)} combo(s):")
        for msg in skipped[:10]:
            print(f"    • {msg}")
        if len(skipped) > 10:
            print(f"    … and {len(skipped) - 10} more")


def run_digest_cmd(
    query: str = "strategy",
    min_trades: int | None = None,
    top_n: int = 20,
    db_path: Path | None = None,
) -> None:
    """Open DB, run a digest query, and print a tabular result to stdout."""
    from analytics.digest_lib import _DEFAULT_MIN_TRADES, _QUERY_MIN_TRADES

    try:
        from tabulate import tabulate as _tab

        _tabulate = _tab
    except ImportError:
        _tabulate = None

    path = db_path or DEFAULT_DB_PATH
    conn = duckdb.connect(str(path), read_only=True)
    try:
        result = run_digest(conn, query, min_trades=min_trades, top_n=top_n)
    finally:
        conn.close()

    columns = result["columns"]
    rows = result["rows"]

    effective_min = (
        min_trades
        if min_trades is not None
        else _QUERY_MIN_TRADES.get(query, _DEFAULT_MIN_TRADES)
    )
    print(f"\n=== Backtest digest: {query} (min_trades={effective_min}) ===\n")
    if not rows:
        print("  No data — run `wifey backtest --save` first.")
        return

    if _tabulate is not None:
        print(_tabulate(rows, headers=columns, tablefmt="simple", floatfmt=".3f"))
    else:
        widths = [
            max(len(str(c)), max((len(str(r[i])) for r in rows), default=0))
            for i, c in enumerate(columns)
        ]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        print(fmt.format(*columns))
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print(fmt.format(*[str(v) if v is not None else "\u2014" for v in row]))
    print()
