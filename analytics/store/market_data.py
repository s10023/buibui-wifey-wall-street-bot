"""OHLCV table accessors."""

import duckdb
import pandas as pd

from analytics.store._common import _upsert


def upsert_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace OHLCV rows.

    df must have columns: symbol, timeframe, open_time, open, high, low, close, volume.
    Conflicts on (symbol, timeframe, open_time) are replaced.
    """
    _upsert(
        conn,
        df,
        "ohlcv",
        "symbol, timeframe, open_time, open, high, low, close, volume",
    )


def get_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Return OHLCV rows for (symbol, timeframe) between start and end (Unix ms, inclusive)."""
    return conn.execute(
        "SELECT symbol, timeframe, open_time, open, high, low, close, volume "
        "FROM ohlcv "
        "WHERE symbol = ? AND timeframe = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time",
        [symbol, timeframe, start, end],
    ).df()


def get_latest_open_time(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
) -> int | None:
    """Return the maximum open_time stored for (symbol, timeframe), or None if no rows."""
    # ORDER BY ... LIMIT 1 instead of MAX() to avoid a DuckDB statistics
    # optimizer bug (InternalException on aggregate after multiple inserts).
    result = conn.execute(
        "SELECT open_time FROM ohlcv WHERE symbol = ? AND timeframe = ?"
        " ORDER BY open_time DESC LIMIT 1",
        [symbol, timeframe],
    ).fetchone()
    if result is None:
        return None
    return int(result[0])


def get_earliest_open_time(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
) -> int | None:
    """Return the minimum open_time stored for (symbol, timeframe), or None if no rows.

    Mirrors ``get_latest_open_time``'s ORDER BY ... LIMIT 1 shape for the same
    reason: MAX()/MIN() after multiple inserts can trip a DuckDB statistics
    optimizer bug.
    """
    result = conn.execute(
        "SELECT open_time FROM ohlcv WHERE symbol = ? AND timeframe = ?"
        " ORDER BY open_time ASC LIMIT 1",
        [symbol, timeframe],
    ).fetchone()
    if result is None:
        return None
    return int(result[0])


def get_close_at(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    open_time: int,
) -> float | None:
    """Return the stored close for one bar, or None if that bar is absent."""
    result = conn.execute(
        "SELECT close FROM ohlcv WHERE symbol = ? AND timeframe = ? AND open_time = ?",
        [symbol, timeframe, open_time],
    ).fetchone()
    if result is None:
        return None
    return float(result[0])


def get_bar_before(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    open_time: int,
) -> tuple[int, float] | None:
    """Return ``(open_time, close)`` of the newest stored bar strictly before
    ``open_time``, or None when nothing precedes it."""
    result = conn.execute(
        "SELECT open_time, close FROM ohlcv"
        " WHERE symbol = ? AND timeframe = ? AND open_time < ?"
        " ORDER BY open_time DESC LIMIT 1",
        [symbol, timeframe, open_time],
    ).fetchone()
    if result is None:
        return None
    return int(result[0]), float(result[1])
