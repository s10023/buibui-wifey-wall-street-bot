"""Orchestration logic for backfill and incremental sync.

yfinance returns the full available history per call (capped by interval —
2y for 1h/4h, unlimited for 1d/1wk), so backfill is a single ``fetch_bars``
call rather than a paginated loop.
"""

import logging

import duckdb

from analytics.data_fetcher import fetch_bars
from analytics.data_store import (
    get_latest_open_time,
    upsert_ohlcv,
)


def backfill(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start_ms: int,
) -> int:
    """Fetch OHLCV history from ``start_ms`` to now and store it.

    Single yfinance call — the underlying ``fetch_history`` returns the full
    configured period in one shot. Returns total rows upserted.
    """
    df = fetch_bars(symbol, timeframe, start_ms)
    if df.empty:
        return 0
    upsert_ohlcv(conn, df)
    logging.info("backfill %s %s: stored %d rows", symbol, timeframe, len(df))
    return len(df)


def sync(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
) -> int:
    """Fetch candles from the latest stored open_time onwards (inclusive).

    Starting from ``latest`` (not ``latest + 1``) re-fetches the most recent
    stored candle so its OHLCV values are overwritten with the final values
    once the bar closes — otherwise a candle stored mid-formation would keep
    its stale close forever.

    Raises ``ValueError`` if no data exists for (symbol, timeframe) — run
    backfill first. Returns total rows upserted.
    """
    latest = get_latest_open_time(conn, symbol, timeframe)
    if latest is None:
        raise ValueError(f"No data found for {symbol}/{timeframe}. Run backfill first.")
    return backfill(conn, symbol, timeframe, latest)
