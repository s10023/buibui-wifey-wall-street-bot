"""``insider_transactions`` table accessors (H-024, routine vs opportunistic).

The one DB write the insider sleeve makes — populated by the EDGAR Form 4
backfill, read-only thereafter. Uses the sealed ``_upsert`` register/unregister
convention.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd

from analytics.store._common import _upsert

_COLUMNS = (
    "symbol, issuer_cik, owner_cik, owner_name, is_officer, is_director, "
    "transaction_date, transaction_code, shares, price_per_share, "
    "acquired_disposed, accession, filing_date, acceptance_ts, seq"
)


def upsert_insider_transactions(
    conn: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]] | pd.DataFrame
) -> None:
    """Insert or replace Form 4 rows; conflicts on (accession, owner_cik, seq).

    ``rows`` may be a list of dicts or a DataFrame; either way it must carry the
    ``insider_transactions`` columns (dates as ISO strings or dates).
    """
    df = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
    if df.empty:
        return
    _upsert(conn, df, "insider_transactions", _COLUMNS)


def get_insider_transactions(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str] | None = None,
    codes: tuple[str, ...] | None = ("P", "S"),
) -> pd.DataFrame:
    """Return insider transactions, ordered causally by disclosure.

    ``codes`` defaults to the pre-registered open-market pair (P/S) because that
    is the study population; pass ``None`` to read every stored code. The filter
    lives here, not in ingestion, so what was excluded stays auditable from the
    table itself rather than being unrecoverable.

    Ordered by (symbol, acceptance_ts, owner_cik, seq): acceptance time is the
    outsider-observable clock, so walking this order is what an outsider could
    actually have acted on — transaction date is when the insider traded, which
    is knowable to the market only once the filing lands.
    """
    sql = (
        "SELECT symbol, issuer_cik, owner_cik, owner_name, is_officer, "
        "is_director, transaction_date, transaction_code, shares, "
        "price_per_share, acquired_disposed, accession, filing_date, "
        "acceptance_ts, seq FROM insider_transactions"
    )
    where: list[str] = []
    params: list[Any] = []
    if symbols:
        where.append(f"symbol IN ({', '.join('?' for _ in symbols)})")
        params.extend(symbols)
    if codes:
        where.append(f"transaction_code IN ({', '.join('?' for _ in codes)})")
        params.extend(codes)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY symbol, acceptance_ts, owner_cik, seq"
    return conn.execute(sql, params).df()
