"""``earnings_facts`` table accessors (edge-hunt #4, PEAD-lite).

The one DB write the PEAD sleeve makes — populated once by the EDGAR backfill,
read-only thereafter. Uses the sealed ``_upsert`` register/unregister convention.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd

from analytics.store._common import _upsert

_COLUMNS = (
    "symbol, cik, fy, fp, period_end, eps_diluted, "
    "announce_date, filed_date, accn, source"
)


def upsert_earnings_facts(
    conn: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]] | pd.DataFrame
) -> None:
    """Insert or replace earnings facts. Conflicts on (symbol, fy, fp) are replaced.

    ``rows`` may be a list of dicts or a DataFrame; either way it must carry the
    ``earnings_facts`` columns (``period_end`` / ``announce_date`` / ``filed_date``
    as ISO strings or dates).
    """
    df = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
    if df.empty:
        return
    _upsert(conn, df, "earnings_facts", _COLUMNS)


def get_earnings_facts(
    conn: duckdb.DuckDBPyConnection, symbols: list[str] | None = None
) -> pd.DataFrame:
    """Return earnings facts (optionally filtered to ``symbols``), ordered causally.

    Ordered by (symbol, announce_date, fy, fp) so downstream SUE construction can
    walk each name's announcements in disclosure order.
    """
    sql = (
        "SELECT symbol, cik, fy, fp, period_end, eps_diluted, "
        "announce_date, filed_date, accn, source FROM earnings_facts"
    )
    params: list[Any] = []
    if symbols:
        placeholders = ", ".join("?" for _ in symbols)
        sql += f" WHERE symbol IN ({placeholders})"
        params.extend(symbols)
    sql += " ORDER BY symbol, announce_date, fy, fp"
    return conn.execute(sql, params).df()
