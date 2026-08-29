"""H-024 phase 1: ``insider_transactions`` store + backfill row-building tests.

In-memory DuckDB throughout; never touches the real ``analytics.db``.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from analytics.insider.form4 import Form4Filing, Form4Transaction, ParseOutcome
from analytics.store.insider import (
    get_insider_transactions,
    upsert_insider_transactions,
)
from analytics.store.schema import init_schema
from tools.insider_backfill import build_rows, collect_filings


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def _row(**over: Any) -> dict[str, Any]:
    base = {
        "symbol": "FIXT",
        "issuer_cik": "0000000001",
        "owner_cik": "0000000901",
        "owner_name": "Example Insider A",
        "is_officer": True,
        "is_director": False,
        "transaction_date": "2026-08-25",
        "transaction_code": "S",
        "shares": 1500.0,
        "price_per_share": 232.45,
        "acquired_disposed": "D",
        "accession": "0000000000-26-000001",
        "filing_date": "2026-08-27",
        "acceptance_ts": "2026-08-27 22:30:30",
        "seq": 0,
    }
    base.update(over)
    return base


def test_roundtrip_preserves_every_column(conn: duckdb.DuckDBPyConnection) -> None:
    upsert_insider_transactions(conn, [_row()])
    df = get_insider_transactions(conn)
    assert len(df) == 1
    got = df.iloc[0]
    assert got["symbol"] == "FIXT"
    assert got["owner_cik"] == "0000000901"
    assert bool(got["is_officer"]) is True
    assert got["shares"] == 1500.0
    assert got["price_per_share"] == 232.45
    assert got["seq"] == 0


def test_empty_upsert_is_a_noop(conn: duckdb.DuckDBPyConnection) -> None:
    upsert_insider_transactions(conn, [])
    assert get_insider_transactions(conn).empty


def test_key_separates_owners_and_transactions_in_one_filing(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # (accession, owner_cik, seq): a joint filing with two transactions is four
    # distinct rows. If any one part were dropped from the key these would
    # collapse and a joint filer's history would silently shrink.
    rows = [
        _row(owner_cik=cik, seq=seq)
        for cik in ("0000000901", "0000000902")
        for seq in (0, 1)
    ]
    upsert_insider_transactions(conn, rows)
    assert len(get_insider_transactions(conn)) == 4


def test_reupsert_replaces_rather_than_duplicates(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    upsert_insider_transactions(conn, [_row(shares=1500.0)])
    upsert_insider_transactions(conn, [_row(shares=1600.0)])
    df = get_insider_transactions(conn)
    assert len(df) == 1
    assert df.iloc[0]["shares"] == 1600.0


def test_code_filter_defaults_to_open_market_pair(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    upsert_insider_transactions(
        conn,
        [
            _row(accession="A", transaction_code="P"),
            _row(accession="B", transaction_code="S"),
            _row(accession="C", transaction_code="M"),  # option exercise
            _row(accession="D", transaction_code="G"),  # gift
        ],
    )
    default = get_insider_transactions(conn)
    assert sorted(default["transaction_code"]) == ["P", "S"]
    # ...but the excluded codes are STORED, so what the study dropped stays
    # auditable from the table instead of being unrecoverable.
    every = get_insider_transactions(conn, codes=None)
    assert sorted(every["transaction_code"]) == ["G", "M", "P", "S"]


def test_ordering_is_by_the_outsider_observable_clock(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # Acceptance time, not transaction date: an outsider can only act once the
    # filing lands, and post-SOX the two can differ by up to two business days.
    upsert_insider_transactions(
        conn,
        [
            _row(
                accession="LATE-TRADE-EARLY-FILE",
                transaction_date="2026-08-20",
                acceptance_ts="2026-08-21 22:00:00",
            ),
            _row(
                accession="EARLY-TRADE-LATE-FILE",
                transaction_date="2026-08-19",
                acceptance_ts="2026-08-24 22:00:00",
            ),
        ],
    )
    order = list(get_insider_transactions(conn)["accession"])
    assert order == ["LATE-TRADE-EARLY-FILE", "EARLY-TRADE-LATE-FILE"]


def test_build_rows_flattens_a_parsed_filing() -> None:
    filing = Form4Filing(
        accession="0000000000-26-000009",
        primary_document="xslF345X06/form4.xml",
        filing_date="2026-08-27",
        acceptance="2026-08-27T22:30:30.000Z",
    )
    outcome = ParseOutcome(
        [
            Form4Transaction(
                owner_cik="0000000901",
                owner_name="Example Insider A",
                is_officer=True,
                is_director=False,
                transaction_date="2026-08-25",
                transaction_code="S",
                shares=10.0,
                price_per_share=1.5,
                acquired_disposed="D",
                seq=0,
            )
        ],
        [],
    )
    rows = build_rows(outcome, filing, "FIXT", "0000000001")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "FIXT"
    assert rows[0]["accession"] == "0000000000-26-000009"
    assert rows[0]["acceptance_ts"] == "2026-08-27T22:30:30.000Z"


def test_build_rows_of_an_empty_outcome_is_empty() -> None:
    filing = Form4Filing("A", "form4.xml", "2026-01-01", "2026-01-01T00:00:00Z")
    assert build_rows(ParseOutcome([], ["boom"]), filing, "FIXT", "1") == []


def _subs_with_shard(shard_to: str) -> dict[str, Any]:
    return {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["0000000000-26-000001"],
                "primaryDocument": ["xslF345X06/form4.xml"],
                "filingDate": ["2026-01-02"],
                "acceptanceDateTime": ["2026-01-02T22:00:00.000Z"],
            },
            "files": [
                {"name": "CIK0000000001-submissions-001.json", "filingTo": shard_to}
            ],
        }
    }


def test_collect_filings_skips_a_shard_that_ends_before_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _boom(name: str) -> dict[str, Any]:
        calls.append(name)
        return {}

    monkeypatch.setattr("tools.insider_backfill.fetch_submissions_shard", _boom)
    filings = collect_filings(_subs_with_shard("2010-01-01"), "2015-01-01")
    assert [f.accession for f in filings] == ["0000000000-26-000001"]
    assert calls == []  # the pre-window shard was never fetched


def test_collect_filings_walks_a_shard_that_reaches_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The positive control for the test above: same code path, a shard whose
    # filingTo lands inside the window, and its filings MUST appear. Without
    # this, "skips the shard" would also pass if the walk were deleted entirely.
    def _shard(name: str) -> dict[str, Any]:
        return {
            "form": ["4"],
            "accessionNumber": ["SHARD-1"],
            "primaryDocument": ["xslF345X06/form4.xml"],
            "filingDate": ["2016-05-05"],
            "acceptanceDateTime": ["2016-05-05T22:00:00.000Z"],
        }

    monkeypatch.setattr("tools.insider_backfill.fetch_submissions_shard", _shard)
    filings = collect_filings(_subs_with_shard("2017-01-01"), "2015-01-01")
    assert sorted(f.accession for f in filings) == ["0000000000-26-000001", "SHARD-1"]
