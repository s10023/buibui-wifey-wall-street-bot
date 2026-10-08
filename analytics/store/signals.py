"""signals + signal_alert_outcomes table accessors."""

from typing import Any

import duckdb
import pandas as pd

_OUTCOME_COLUMNS = [
    "signal_id",
    "symbol",
    "tf",
    "strategy",
    "direction",
    "fired_at_ms",
    "candle_ts_ms",
    "entry_price",
    "sl_price",
    "tp_price",
    "rr_ratio",
    "confidence_at_fire",
    "tags",
    "outcome",
    "outcome_r",
    "outcome_filled_at_ms",
    "outcome_cost_r",
]


def upsert_signals(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or ignore signal rows (conflicts on PK are silently skipped).

    df must have columns: symbol, timeframe, strategy, open_time, direction,
    entry_price, sl_price, reason, confidence, fired_at.
    Conflicts on (symbol, timeframe, strategy, open_time, direction) are ignored
    so that re-runs of the same scan cycle do not overwrite previously persisted
    signals with potentially different metadata.
    """
    if df.empty:
        return
    # Explicit register/unregister in try/finally — see _upsert docstring for why.
    conn.register("_signals_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO signals "
            "SELECT symbol, timeframe, strategy, open_time, direction, "
            "entry_price, sl_price, reason, confidence, fired_at "
            "FROM _signals_upsert_df"
        )
    finally:
        conn.unregister("_signals_upsert_df")


def get_signals_history(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Return persisted signal rows for (symbol, timeframe) in [start_ms, end_ms].

    Results are ordered by open_time descending (most recent first).
    """
    return conn.execute(
        "SELECT symbol, timeframe, strategy, open_time, direction, "
        "entry_price, sl_price, reason, confidence, fired_at "
        "FROM signals "
        "WHERE symbol = ? AND timeframe = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time DESC",
        [symbol, timeframe, start_ms, end_ms],
    ).df()


def upsert_signal_outcome(conn: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> None:
    """Insert a signal outcome row, or refresh an existing one in place.

    The row dict must contain at minimum: signal_id, symbol, tf, strategy,
    direction, fired_at_ms.  All other fields are optional and default to NULL
    on a fresh insert.

    **A conflict does not blank the resolution.** `outcome` / `outcome_r` /
    `outcome_filled_at_ms` / `outcome_cost_r` are `COALESCE`d against the stored
    row, so an
    omitted (NULL) value keeps whatever is already there and a supplied value
    still wins. This is not defensive coding — it is the live shape.
    `scanner.py` re-writes the alert row on **every** scan that still detects
    the signal (the write is unconditional; only *dispatch* is watermarked)
    and passes none of the three keys, so an `INSERT OR REPLACE`
    would set all three back to NULL on every re-detection. Measured on
    the live ledger 2026-08-11: 13 rows re-stamped in a single cycle, the
    oldest a signal from 7 weeks earlier already booked as a loss. It survived
    only because `backfill_outcomes` re-derives the label downstream in the
    same cycle — so the exposure was a crash, or that symbol hitting the
    backfill's `no_ohlcv` path, silently reverting a resolved row to `open`.

    `backfill_outcomes` does not route through here (it issues a direct
    `UPDATE`), so preserving on NULL costs the resolver nothing.
    """
    values = [row.get(col) for col in _OUTCOME_COLUMNS]
    conn.execute(
        "INSERT INTO signal_alert_outcomes "
        "(signal_id, symbol, tf, strategy, direction, fired_at_ms, "
        "candle_ts_ms, entry_price, sl_price, tp_price, rr_ratio, "
        "confidence_at_fire, tags, outcome, outcome_r, outcome_filled_at_ms, "
        "outcome_cost_r) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (signal_id) DO UPDATE SET "
        "symbol = excluded.symbol, tf = excluded.tf, "
        "strategy = excluded.strategy, direction = excluded.direction, "
        "fired_at_ms = excluded.fired_at_ms, "
        "candle_ts_ms = excluded.candle_ts_ms, "
        "entry_price = excluded.entry_price, sl_price = excluded.sl_price, "
        "tp_price = excluded.tp_price, rr_ratio = excluded.rr_ratio, "
        "confidence_at_fire = excluded.confidence_at_fire, "
        "tags = excluded.tags, "
        "outcome = COALESCE(excluded.outcome, signal_alert_outcomes.outcome), "
        "outcome_r = COALESCE(excluded.outcome_r, "
        "signal_alert_outcomes.outcome_r), "
        "outcome_filled_at_ms = COALESCE(excluded.outcome_filled_at_ms, "
        "signal_alert_outcomes.outcome_filled_at_ms), "
        "outcome_cost_r = COALESCE(excluded.outcome_cost_r, "
        "signal_alert_outcomes.outcome_cost_r)",
        values,
    )
