"""backtest_runs + backtest_trades upserts/queries."""

import hashlib
import time
from typing import Any

import duckdb
import pandas as pd


def _backtest_run_id(
    symbol: str,
    timeframe: str,
    strategy: str,
    days: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
    adr_suppress_threshold: float | None = None,
    volume_suppress: bool | None = None,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    volume_spike_boost_long: bool | None = None,
    volume_spike_boost_short: bool | None = None,
    adr_exempt: bool = False,
    atr_sl_floor: bool = False,
    cost_model: str | None = None,
    origin: str = "sweep",
) -> str:
    """Return a deterministic 16-char hex ID for a backtest param combination.

    Optional suffixes are appended only when set so existing run_ids are
    unchanged (None = flag not applied, same hash as before these columns).

    ``origin`` identifies the *writer*, not the parameters. It exists because a
    param combination does not uniquely identify a measurement: the sweep
    (`_collect_sweep_results`) and the live EV gate (`signal.scanner`) can
    produce the same `symbol|timeframe|strategy|days|sl_pct|tp_r|fee_pct|
    day_filter` tuple while measuring completely different things — the sweep
    runs every configured strategy over the config window, the live gate runs
    one strategy over the scan window. Sharing a run_id made
    `INSERT OR REPLACE` *overwrite* one writer's row with the other's instead
    of accumulating both, silently deleting sweep measurements that
    `confidence_ratings` is built from. `"sweep"` is the legacy value and adds
    no suffix, so every historical sweep run_id is unchanged.
    """
    key = (
        f"{symbol}|{timeframe}|{strategy}|{days}|{sl_pct}|{tp_r}|{fee_pct}|{day_filter}"
    )
    if adr_suppress_threshold is not None:
        key += f"|adr:{adr_suppress_threshold}"
    if volume_suppress:
        key += "|vol_suppress"
    if min_sl_pct > 0.0:
        key += f"|min_sl:{min_sl_pct}"
    if atr_sl_multiplier is not None:
        key += f"|atr_sl:{atr_sl_multiplier}"
    if tp_r_long is not None:
        key += f"|tp_long:{tp_r_long}"
    if tp_r_short is not None:
        key += f"|tp_short:{tp_r_short}"
    if volume_suppress_long:
        key += "|vol_sup_l"
    if volume_suppress_short:
        key += "|vol_sup_s"
    if volume_spike_boost_long:
        key += "|spike_l"
    if volume_spike_boost_short:
        key += "|spike_s"
    if adr_exempt:
        key += "|adr_exempt"
    if atr_sl_floor:
        key += "|atr_floor"
    if cost_model is not None:
        key += f"|cost:{cost_model}"
    if origin != "sweep":
        key += f"|origin:{origin}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def upsert_backtest_run(
    conn: duckdb.DuckDBPyConnection,
    result: Any,
    days: int,
    data_start_ms: int,
    data_end_ms: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
    sweep_id: str | None = None,
    volume_suppress: bool | None = None,
    universe_policy: str | None = None,
    cost_model: str | None = None,
    *,
    origin: str,
    adr_suppress_threshold: float | None,
) -> str:
    """Insert or replace a backtest aggregate result row.

    result must be a BacktestResult instance.
    Returns the run_id so the caller can link backtest_trades rows.

    ``origin`` is a REQUIRED keyword so mypy forces every call site to say
    which writer it is. `backtest_runs` has four writers and the row key is
    derived from parameters alone, so two writers measuring different things
    over different windows used to collide and overwrite each other — see
    `_backtest_run_id`. Making this required (rather than defaulting it) is the
    same enforcement `adr_gate_applies(timeframe)` uses: a new call site cannot
    silently inherit another writer's identity.

    ``adr_suppress_threshold`` is a REQUIRED keyword for the same reason, and it
    is the threshold that was **executed**, not the one the config declared —
    pass `effective_adr_threshold(...)`, never `cfg.adr_suppress_threshold`. It
    defaulted to None until 2026-08-11, which let the two writers that do wire
    the gate hand it their raw config value: 2,091 of 3,246 rows then recorded a
    threshold for a cell the gate never touched (1,974 of them on `1d`/`1wk`,
    where the gate is undefined since #142). A defaulted argument cannot
    distinguish "this path does not run the gate" from "nobody thought about
    it"; a required one forces each writer to answer.
    """
    run_id = _backtest_run_id(
        result.symbol,
        result.timeframe,
        result.strategy,
        days,
        sl_pct,
        tp_r,
        fee_pct,
        day_filter,
        adr_suppress_threshold,
        volume_suppress,
        cost_model=cost_model,
        origin=origin,
    )
    row: dict[str, Any] = {
        "run_id": run_id,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "strategy": result.strategy,
        "data_start_ms": data_start_ms,
        "data_end_ms": data_end_ms,
        "days": days,
        "sl_pct": sl_pct,
        "tp_r": tp_r,
        "fee_pct": fee_pct,
        "day_filter": day_filter,
        "total_signals": len(result.trades),
        "closed_trades": len(result.closed_trades),
        "win_count": result.win_count,
        "loss_count": result.loss_count,
        "win_rate": result.win_rate,
        "avg_r": result.avg_r,
        "total_r": result.total_r,
        "max_drawdown_r": result.max_drawdown_r,
        "run_at_ms": int(time.time() * 1000),
        "sweep_id": sweep_id,
        "adr_suppress_threshold": adr_suppress_threshold,
        "long_closed_trades": len(result.long_closed_trades),
        "long_win_count": result.long_win_count,
        "long_win_rate": result.long_win_rate,
        "long_avg_r": result.long_avg_r,
        "short_closed_trades": len(result.short_closed_trades),
        "short_win_count": result.short_win_count,
        "short_win_rate": result.short_win_rate,
        "short_avg_r": result.short_avg_r,
        "long_total_r": result.long_total_r,
        "short_total_r": result.short_total_r,
        "recovery_factor": result.recovery_factor,
        "volume_suppress": volume_suppress,
        "universe_policy": universe_policy,
        "cost_model": cost_model,
    }
    df = pd.DataFrame([row])
    conn.register("_bt_run_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_runs SELECT "
            "run_id, symbol, timeframe, strategy, data_start_ms, data_end_ms, "
            "days, sl_pct, tp_r, fee_pct, day_filter, "
            "total_signals, closed_trades, win_count, loss_count, "
            "win_rate, avg_r, total_r, max_drawdown_r, run_at_ms, sweep_id, "
            "long_closed_trades, long_win_count, long_win_rate, long_avg_r, "
            "short_closed_trades, short_win_count, short_win_rate, short_avg_r, "
            "long_total_r, short_total_r, volume_suppress, adr_suppress_threshold, "
            "recovery_factor, universe_policy, cost_model "
            "FROM _bt_run_upsert_df"
        )
    finally:
        conn.unregister("_bt_run_upsert_df")
    return run_id


def upsert_backtest_trades(
    conn: duckdb.DuckDBPyConnection,
    result: Any,
    run_id: str,
) -> None:
    """Insert or replace per-trade rows for a backtest run.

    result must be a BacktestResult instance.
    Skips if result.trades is empty.
    """
    if not result.trades:
        return
    rows = [
        {
            "trade_id": f"{run_id}:{t.signal_time}",
            "run_id": run_id,
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "strategy": result.strategy,
            "direction": t.direction,
            "signal_time": t.signal_time,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "sl_price": t.sl_price,
            "tp_price": t.tp_price,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "outcome": t.outcome,
            "pnl_r": t.pnl_r,
        }
        for t in result.trades
    ]
    df = pd.DataFrame(rows)
    conn.register("_bt_trades_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_trades SELECT "
            "trade_id, run_id, symbol, timeframe, strategy, direction, "
            "signal_time, entry_time, entry_price, sl_price, tp_price, "
            "exit_time, exit_price, outcome, pnl_r "
            "FROM _bt_trades_upsert_df"
        )
    finally:
        conn.unregister("_bt_trades_upsert_df")


def list_backtest_runs(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return the latest backtest_run per (symbol, timeframe, strategy, day_filter), newest first.

    Attaches calibrated star ratings from confidence_ratings by matching on
    (strategy, timeframe, day_filter) so each row shows the correct per-config stars.
    """
    return conn.execute(
        "SELECT b.run_id, b.symbol, b.timeframe, b.strategy, b.days, b.sl_pct, b.tp_r, "
        "b.fee_pct, b.day_filter, b.closed_trades, b.win_count, b.loss_count, b.win_rate, "
        "b.avg_r, b.total_r, b.max_drawdown_r, b.recovery_factor, b.sweep_id, b.run_at_ms, "
        "b.long_closed_trades, b.long_win_count, b.long_win_rate, b.long_avg_r, b.long_total_r, "
        "b.short_closed_trades, b.short_win_count, b.short_win_rate, b.short_avg_r, b.short_total_r, "
        "b.adr_suppress_threshold, cr.stars, cr_long.long_stars, cr_short.short_stars "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY symbol, timeframe, strategy, day_filter, adr_suppress_threshold "
        "    ORDER BY run_at_ms DESC"
        "  ) AS rn FROM backtest_runs"
        ") b "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'combined' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr ON cr.strategy = b.strategy AND cr.tf = b.timeframe AND cr.day_filter = b.day_filter "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS long_stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'long' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr_long ON cr_long.strategy = b.strategy AND cr_long.tf = b.timeframe "
        "  AND cr_long.day_filter = b.day_filter "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS short_stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'short' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr_short ON cr_short.strategy = b.strategy AND cr_short.tf = b.timeframe "
        "  AND cr_short.day_filter = b.day_filter "
        "WHERE b.rn = 1 "
        "ORDER BY b.run_at_ms DESC"
    ).df()


def get_win_rate_by_strategy(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return win rate aggregated per strategy across all saved backtest runs.

    Only includes combos with at least 20 closed trades (same gate as sweep table).
    Ordered by win_rate_pct descending.
    """
    return conn.execute("""
        SELECT
            strategy,
            SUM(closed_trades)                                                  AS total_closed,
            SUM(win_count)                                                      AS total_wins,
            ROUND(SUM(win_count) * 100.0 / NULLIF(SUM(closed_trades), 0), 1)   AS win_rate_pct,
            ROUND(AVG(avg_r), 3)                                                AS mean_avg_r,
            COUNT(*)                                                            AS combos_run
        FROM backtest_runs
        WHERE closed_trades >= 20
          AND adr_suppress_threshold IS NULL
        GROUP BY strategy
        ORDER BY win_rate_pct DESC
    """).df()
