"""Analytics runner — thin wrapper that opens the DB and delegates to data_sync."""

import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.data_sync import backfill, sync
from analytics.db_retry import connect_with_retry
from analytics.overlay.live import CORE_SYMBOL
from utils.config_validation import (
    load_pundit_ledger_symbols,
    load_research_universe,
    load_stocks_config,
    load_universe_policy,
)


def _resolve_symbols(
    symbols: list[str] | None,
    *,
    use_universe: bool = False,
    use_pundit: bool = False,
    use_core: bool = False,
) -> list[str]:
    if symbols:
        return symbols
    if use_core:
        logging.info("Universe: survival-core signal index (%s)", CORE_SYMBOL)
        return [CORE_SYMBOL]
    if use_universe:
        try:
            universe = load_research_universe()
        except Exception as e:
            logging.error("Failed to load research universe: %s", e)
            sys.exit(1)
        resolved = universe.active_symbols()
        logging.info("%s", universe.describe())
        return resolved
    if use_pundit:
        try:
            resolved = load_pundit_ledger_symbols()
        except Exception as e:
            logging.error("Failed to load pundit ledger: %s", e)
            sys.exit(1)
        # Loud on empty: an empty ledger and an unreadable one both otherwise
        # produce a no-op sync that logs nothing and exits 0, which is the
        # "reports success while refreshing nothing" shape this flag exists to fix.
        if not resolved:
            logging.error("Pundit ledger resolved 0 symbols — nothing to sync.")
            sys.exit(1)
        logging.info("Universe: pundit ledger (%d symbols)", len(resolved))
        return resolved
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
    # Waits out a concurrent writer rather than dying on it. `make go-live`
    # runs `wifey-analytics-sync` before the scan, so this is the first thing
    # to hit the lock when a web UI or a db-update happens to be running.
    conn: duckdb.DuckDBPyConnection = connect_with_retry(db_path)
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
    *,
    use_universe: bool = False,
    use_pundit: bool = False,
    use_core: bool = False,
) -> None:
    resolved = _resolve_symbols(
        symbols, use_universe=use_universe, use_pundit=use_pundit, use_core=use_core
    )
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
    *,
    use_universe: bool = False,
    use_pundit: bool = False,
    use_core: bool = False,
) -> None:
    resolved = _resolve_symbols(
        symbols, use_universe=use_universe, use_pundit=use_pundit, use_core=use_core
    )
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
