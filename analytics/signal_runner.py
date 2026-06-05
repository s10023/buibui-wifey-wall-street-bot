"""Signal daemon runner — thin wrapper over signal_lib.

Opens a short-lived DuckDB connection per scan cycle, syncs data via yfinance,
scans for signals, then closes the connection before sleeping.

Each cycle: open conn → sync → scan → upsert signals → close conn → sleep.
This releases the write lock during the sleep window so the web API's read-only
connections can access the DB between cycles.
"""

import logging
import signal
import time
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from analytics.data_store import (
    DEFAULT_DB_PATH,
    get_combo_lookup,
    get_confidence_ratings,
    get_cross_tf_combo_lookup,
    get_directional_confidence_ratings,
    get_ohlcv,
    init_schema,
    prune_backtest_cache,
)
from analytics.data_sync import backfill, sync
from analytics.signal.outcome_backfill import backfill_outcomes
from analytics.signal_config import (
    BacktestFilterConfig,
    BiasConfig,
    ComboConfig,
    StrategyOverride,
)
from analytics.signal_lib import (
    _parse_htf_ltf_pairs,
    run_scan_cycle,
    secs_until_next_boundary,
)
from signals.cooldown_store import CooldownStore
from utils.config_validation import load_stocks_config

logger = logging.getLogger(__name__)

_MYT = ZoneInfo("Asia/Kuala_Lumpur")
_DEFAULT_BACKFILL_DAYS = 90

# Maximum rows returned by the inclusive re-fetch before the cache is invalidated.
# Normal cycle: 2 rows (re-fetched+finalised last row + new partial candle).
# >2 = daemon missed a cycle or a gap fill happened — rebuild from scratch.
_CACHE_INVALIDATE_THRESHOLD = 2


def _update_ohlcv_cache(
    conn: duckdb.DuckDBPyConnection,
    cache: dict[tuple[str, str], pd.DataFrame],
    symbol: str,
    tf: str,
    start_ms: int,
    now_ms: int,
) -> None:
    """Incrementally refresh the in-memory OHLCV cache for one (symbol, tf) pair.

    Hot path (normal cycle): queries from the last cached row's open_time (inclusive)
    so the partially-formed candle stored in the previous cycle is always replaced with
    its finalised values — mirroring data_sync.sync() which also starts from `latest`
    (not latest+1) for the same reason.  Typically 2 rows per cycle: the finalised last
    candle + the newly opened partial candle.

    Invalidation: if >2 rows arrive (missed a cycle / gap fill) the cache is discarded
    and rebuilt from a full DB read so no candles are skipped.
    """
    key = (symbol, tf)
    if key in cache and not cache[key].empty:
        cached_max_ts = int(cache[key]["open_time"].max())
        # Fetch from cached_max_ts inclusive — the last cached row was a partial candle
        # that has since been finalised in the DB by sync().  Replacing it ensures
        # detectors and the volume gate always see the correct final OHLCV values.
        new_rows = get_ohlcv(conn, symbol, tf, cached_max_ts, now_ms)
        if len(new_rows) > _CACHE_INVALIDATE_THRESHOLD:
            logger.info(
                "OHLCV cache invalidated for %s/%s — %d new rows (backfill/gap)",
                symbol,
                tf,
                len(new_rows),
            )
            cache[key] = get_ohlcv(conn, symbol, tf, start_ms, now_ms)
        elif not new_rows.empty:
            cache[key] = pd.concat([cache[key].iloc[:-1], new_rows], ignore_index=True)
        # else: DB has no rows at or after cached_max_ts (shouldn't happen) — keep as-is
    else:
        cache[key] = get_ohlcv(conn, symbol, tf, start_ms, now_ms)


def run_signal_watch(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    strategies: list[str] | None = None,
    tp_r: float = 2.0,
    sl_pct: float = 0.02,
    min_sl_pct: float = 0.0,
    send_telegram: bool = False,
    state_file: str = "signal_state.json",
    db_path: Path = DEFAULT_DB_PATH,
    backtest_cfg: BacktestFilterConfig | None = None,
    day_filter: str = "off",
    strategy_timeframes: dict[str, list[str]] | None = None,
    strategy_params: dict[str, StrategyOverride] | None = None,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    config_name: str | None = None,
    bias_cfg: BiasConfig | None = None,
    combo_cfg: ComboConfig | None = None,
    once: bool = False,
) -> None:
    """Run the signal detection daemon loop.

    On each cycle: syncs new candles via yfinance, scans for signals,
    sends Telegram alerts if enabled, then sleeps until the next candle
    boundary across all watched timeframes.

    When ``once`` is True, runs exactly one scan cycle (sync → scan → alert →
    outcome backfill) and exits without sleeping — for cron / once-a-day use.
    """
    from analytics.strategies import KNOWN_STRATEGIES

    stocks_config = load_stocks_config()

    resolved_symbols = symbols or list(stocks_config.keys())
    resolved_timeframes = timeframes or ["4h"]
    resolved_strategies = strategies or [
        s for s in KNOWN_STRATEGIES if s != "seasonality"
    ]

    store = CooldownStore(state_file)

    logger.info("Signal daemon starting")
    logger.info("  symbols    = %s", resolved_symbols)
    logger.info("  timeframes = %s", resolved_timeframes)
    logger.info("  strategies = %s", resolved_strategies)

    # Graceful shutdown: first Ctrl+C sets the flag; second Ctrl+C forces exit.
    # Using a flag instead of letting KeyboardInterrupt propagate prevents DuckDB's
    # native heap from being corrupted when conn.close() is called mid-operation.
    shutdown_requested = [False]

    def _handle_sigint(signum: int, frame: types.FrameType | None) -> None:
        if shutdown_requested[0]:
            raise KeyboardInterrupt  # second Ctrl+C: force exit
        shutdown_requested[0] = True
        logger.info(
            "Shutdown requested — finishing current operation and exiting cleanly"
        )

    prev_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        with duckdb.connect(str(db_path)) as init_conn:
            init_schema(init_conn)

        with duckdb.connect(str(db_path)) as prune_conn:
            prune_backtest_cache(prune_conn)

        # Load same-TF co-firing combo lookup from DB once at startup (D10 step 3).
        # combo_lookup is keyed by (symbol, tf, frozenset({a, b})) → best avg_r row.
        # Empty dict disables the co-fire check (no combo runs saved yet).
        with duckdb.connect(str(db_path)) as cl_conn:
            combo_lookup = get_combo_lookup(cl_conn)
        if combo_lookup:
            logger.info("Loaded combo lookup: %d pairs", len(combo_lookup))
        else:
            logger.info("No combo runs found — same-TF co-fire tagging disabled")

        # Load cross-TF combo lookup from DB once at startup (D10 step 4).
        # cross_tf_lookup is keyed by (symbol, tf_htf, tf_ltf, strat_htf, strat_ltf).
        with duckdb.connect(str(db_path)) as ct_conn:
            cross_tf_lookup = get_cross_tf_combo_lookup(ct_conn)
        if cross_tf_lookup:
            logger.info("Loaded cross-TF lookup: %d pairs", len(cross_tf_lookup))
        else:
            logger.info("No cross-TF runs found — cross-TF co-fire tagging disabled")

        # Load per-config confidence ratings from DB once at startup.
        # Falls back to indicators_lib.py defaults when empty (no recalibrate run yet).
        confidence_override: dict[str, dict[str, int]] = {}
        directional_confidence_override: dict[str, dict[str, dict[str, int]]] = {}
        if config_name:
            with duckdb.connect(str(db_path)) as cr_conn:
                confidence_override = get_confidence_ratings(cr_conn, config_name)
                directional_confidence_override = get_directional_confidence_ratings(
                    cr_conn, config_name
                )
            if confidence_override:
                logger.info(
                    "Loaded confidence ratings for config '%s' (%d strategies, %d directional)",
                    config_name,
                    len(confidence_override),
                    len(directional_confidence_override),
                )
            else:
                logger.info(
                    "No confidence ratings found for config '%s' — using indicators_lib defaults",
                    config_name,
                )

        _cycle_count = 0
        _COMBO_REFRESH_CYCLES = 10  # reload combo_lookup every N cycles
        # In-memory OHLCV cache: (symbol, tf) → DataFrame.
        # Warm after first cycle; subsequent cycles append 0–1 new rows instead of
        # re-reading the full 4000–7000 row history from DuckDB. (P6 fix)
        ohlcv_cache: dict[tuple[str, str], pd.DataFrame] = {}

        while not shutdown_requested[0]:
            _cycle_count += 1
            logger.info("--- scan cycle start ---")

            # Reload combo lookups periodically so newly saved backtest runs are
            # picked up without requiring a daemon restart.
            if _cycle_count > 1 and _cycle_count % _COMBO_REFRESH_CYCLES == 0:
                with duckdb.connect(str(db_path)) as _cl_conn:
                    fresh = get_combo_lookup(_cl_conn)
                if len(fresh) != len(combo_lookup):
                    logger.info(
                        "Combo lookup refreshed: %d → %d pairs",
                        len(combo_lookup),
                        len(fresh),
                    )
                    combo_lookup = fresh
                with duckdb.connect(str(db_path)) as _ct_conn:
                    fresh_ct = get_cross_tf_combo_lookup(_ct_conn)
                if len(fresh_ct) != len(cross_tf_lookup):
                    logger.info(
                        "Cross-TF lookup refreshed: %d → %d pairs",
                        len(cross_tf_lookup),
                        len(fresh_ct),
                    )
                    cross_tf_lookup = fresh_ct

            # Open a short-lived connection for this cycle only.
            # Closing before the sleep window releases the write lock so the
            # web API's read-only connections can access the DB between cycles.
            with duckdb.connect(str(db_path)) as conn:
                # Sync each symbol+timeframe; fall back to backfill for new symbols
                now_ms = int(time.time() * 1000)
                backfill_start_ms = now_ms - _DEFAULT_BACKFILL_DAYS * 24 * 3600 * 1000
                # OHLCV cache window: match backtest_cfg.since so _compute_backtest
                # sees the same data range the alert labels (e.g. "since 2025-09-12").
                # Falls back to 90d when no since is configured.
                if backtest_cfg and backtest_cfg.since:
                    import datetime as _dt

                    cache_start_ms = int(
                        _dt.datetime.strptime(backtest_cfg.since, "%Y-%m-%d")
                        .replace(tzinfo=_dt.UTC)
                        .timestamp()
                        * 1000
                    )
                else:
                    cache_start_ms = backfill_start_ms
                for symbol in resolved_symbols:
                    for tf in resolved_timeframes:
                        try:
                            sync(conn, symbol, tf)
                        except ValueError:
                            logger.info(
                                "No data for %s/%s — running initial backfill",
                                symbol,
                                tf,
                            )
                            backfill(conn, symbol, tf, backfill_start_ms)
                            ohlcv_cache.pop((symbol, tf), None)  # force cold read
                        except duckdb.IOException as exc:
                            logger.warning(
                                "DB sync failed for %s/%s (will retry): %s",
                                symbol,
                                tf,
                                exc,
                            )

                # Incrementally refresh OHLCV cache for all primary symbols.
                for symbol in resolved_symbols:
                    for tf in resolved_timeframes:
                        _update_ohlcv_cache(
                            conn, ohlcv_cache, symbol, tf, cache_start_ms, now_ms
                        )

                alerts = run_scan_cycle(
                    conn=conn,
                    symbols=resolved_symbols,
                    timeframes=resolved_timeframes,
                    strategies=resolved_strategies,
                    store=store,
                    tp_r=tp_r,
                    sl_pct=sl_pct,
                    min_sl_pct=min_sl_pct,
                    send_telegram=send_telegram,
                    backtest_cfg=backtest_cfg,
                    day_filter=day_filter,
                    strategy_timeframes=strategy_timeframes,
                    strategy_params=strategy_params,
                    atr_sl_multiplier=atr_sl_multiplier,
                    atr_sl_floor=atr_sl_floor,
                    confidence_override=confidence_override or None,
                    directional_confidence_override=directional_confidence_override
                    or None,
                    bias_cfg=bias_cfg,
                    combo_lookup=combo_lookup or None,
                    combo_window=combo_cfg.window if combo_cfg else 5,
                    combo_min_avg_r=combo_cfg.min_avg_r if combo_cfg else 1.0,
                    cross_tf_lookup=cross_tf_lookup or None,
                    cross_tf_pairs=_parse_htf_ltf_pairs(combo_cfg.cross_tf_pairs)
                    if combo_cfg
                    else None,
                    cross_tf_window_hours=combo_cfg.cross_tf_window_hours
                    if combo_cfg
                    else 4.0,
                    cross_tf_min_avg_r=combo_cfg.cross_tf_min_avg_r
                    if combo_cfg
                    else 1.0,
                    ohlcv_cache=ohlcv_cache,
                )

                # T2 P2: walk OHLCV forward to resolve outstanding outcome rows.
                # Reuses the same write conn; cheap (single SELECT + per-TF
                # OHLCV reads). Failure is logged but never blocks the cycle.
                try:
                    backfill_outcomes(conn, now_ms=now_ms)
                except Exception:
                    logger.exception("Outcome backfill failed this cycle")
            # Connection is now closed — web API can read the DB during the sleep.

            if alerts:
                logger.info("%d alert(s) sent this cycle", len(alerts))
            else:
                logger.info("No new signals this cycle")

            if once:
                logger.info("--- single-cycle (--once) complete; exiting ---")
                break

            sleep_secs, wake_ts = secs_until_next_boundary(resolved_timeframes)
            next_dt = datetime.fromtimestamp(wake_ts, tz=_MYT).strftime("%H:%M:%S MYT")
            logger.info("--- sleeping %.0fs until %s ---", sleep_secs, next_dt)

            # Interruptible sleep: 1s chunks so Ctrl+C exits within ~1s
            elapsed = 0.0
            while elapsed < sleep_secs and not shutdown_requested[0]:
                chunk = min(1.0, sleep_secs - elapsed)
                time.sleep(chunk)
                elapsed += chunk
    finally:
        signal.signal(signal.SIGINT, prev_handler)
