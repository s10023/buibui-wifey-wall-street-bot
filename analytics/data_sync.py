"""Orchestration logic for backfill and incremental sync.

yfinance returns the full available history per call (capped by interval —
2y for 1h/4h, unlimited for 1d/1wk), but ``fetch_bars`` keeps only the first
``BARS_MAX_LIMIT`` bars at or after its ``start_ms``. A deep start therefore
cannot be served in one call: asking for ``1d`` bars since 1927 would store
1927–1947 and silently leave every later bar missing, which reads exactly like
a symbol with no recent history. ``backfill`` pages instead, advancing past the
last bar it stored until a short page proves the history is exhausted.
"""

import logging

import duckdb
import pandas as pd

from analytics.data_fetcher import BARS_MAX_LIMIT, fetch_bars
from analytics.data_quality import check_ohlcv, quarantine
from analytics.data_store import (
    get_latest_open_time,
    upsert_ohlcv,
)
from analytics.trading_calendar import check_session_gaps


def backfill(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start_ms: int,
) -> int:
    """Fetch OHLCV history from ``start_ms`` to now and store it.

    Pages through ``fetch_bars`` — each call yields at most ``BARS_MAX_LIMIT``
    bars — until a page comes back short. Returns total rows upserted.
    """
    total = 0
    cursor = start_ms
    while True:
        df = fetch_bars(symbol, timeframe, cursor)
        if df.empty:
            return total
        page_rows = len(df)
        last_open_time = int(df["open_time"].max())
        total += _store_page(conn, symbol, timeframe, df)
        if page_rows < BARS_MAX_LIMIT:
            return total
        # Strictly increasing: fetch_bars only returns bars at or after cursor.
        cursor = last_open_time + 1


def _store_page(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
) -> int:
    """Quality-check, quarantine and store one page. Returns rows upserted."""
    report = check_ohlcv(df)
    if not report.is_clean:
        logging.warning("data-quality %s %s: %s", symbol, timeframe, report.summary())
    clean, dropped = quarantine(df, report)
    if clean.empty:
        logging.warning(
            "data-quality %s %s: all %d rows quarantined", symbol, timeframe, len(df)
        )
        return 0

    if len(clean) >= 2:
        gap_report = check_session_gaps(clean, timeframe)
        if gap_report.has_gaps:
            logging.warning(
                "session gap %s %s: %s", symbol, timeframe, gap_report.summary()
            )

    upsert_ohlcv(conn, clean)
    logging.info(
        "backfill %s %s: stored %d rows (%d quarantined)",
        symbol,
        timeframe,
        len(clean),
        len(dropped),
    )
    return len(clean)


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
