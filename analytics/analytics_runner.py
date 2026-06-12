"""Analytics runner — thin wrapper that opens the DB and delegates to data_sync."""

import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.data_sync import backfill, sync
from utils.config_validation import load_stocks_config, load_universe_policy


def _resolve_symbols(symbols: list[str] | None) -> list[str]:
    if symbols:
        return symbols
    try:
        resolved = list(load_stocks_config().keys())
    except Exception as e:
        logging.error("Failed to load stocks config: %s", e)
        sys.exit(1)
    policy = load_universe_policy()
    logging.info("Universe: %s (%d symbols)", policy.summary(), len(resolved))
    return resolved


@contextmanager
def _open_session(db_path: Path) -> Generator[duckdb.DuckDBPyConnection]:
    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path))
    try:
        init_schema(conn)
        yield conn
    finally:
        conn.close()


def run_backfill(
    symbols: list[str] | None,
    timeframes: list[str],
    since_ms: int,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    resolved = _resolve_symbols(symbols)
    with _open_session(db_path) as conn:
        for symbol in resolved:
            for timeframe in timeframes:
                logging.info("Backfilling %s %s ...", symbol, timeframe)
                total = backfill(conn, symbol, timeframe, since_ms)
                logging.info(
                    "Backfill complete: %s %s — %d rows", symbol, timeframe, total
                )


def run_sync(
    symbols: list[str] | None,
    timeframes: list[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    resolved = _resolve_symbols(symbols)
    with _open_session(db_path) as conn:
        for symbol in resolved:
            for timeframe in timeframes:
                logging.info("Syncing %s %s ...", symbol, timeframe)
                try:
                    total = sync(conn, symbol, timeframe)
                    logging.info(
                        "Sync complete: %s %s — %d new rows", symbol, timeframe, total
                    )
                except ValueError as e:
                    logging.warning("%s — skipping (run backfill first)", e)
