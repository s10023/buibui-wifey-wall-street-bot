"""Edge-hunt #4 (PEAD-lite): earnings_facts store round-trip (in-memory DuckDB)."""

from __future__ import annotations

import duckdb

from analytics.store.earnings import get_earnings_facts, upsert_earnings_facts
from analytics.store.schema import init_schema


def _rows() -> list[dict[str, object]]:
    return [
        {
            "symbol": "AAPL",
            "cik": "0000320193",
            "fy": 2023,
            "fp": "Q1",
            "period_end": "2022-12-31",
            "eps_diluted": 1.88,
            "announce_date": "2023-02-02",
            "filed_date": "2023-02-03",
            "accn": "x",
            "source": "8k",
        },
        {
            "symbol": "MSFT",
            "cik": "0000789019",
            "fy": 2023,
            "fp": "Q1",
            "period_end": "2022-12-31",
            "eps_diluted": 2.32,
            "announce_date": "2023-01-24",
            "filed_date": "2023-01-25",
            "accn": "y",
            "source": "8k",
        },
    ]


def test_upsert_then_get_roundtrip() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_earnings_facts(conn, _rows())
    got = get_earnings_facts(conn, symbols=["AAPL"])
    assert len(got) == 1
    assert got.iloc[0]["fp"] == "Q1"
    assert abs(float(got.iloc[0]["eps_diluted"]) - 1.88) < 1e-9


def test_get_all_symbols_when_unfiltered() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_earnings_facts(conn, _rows())
    assert len(get_earnings_facts(conn)) == 2


def test_upsert_is_idempotent_on_pk() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_earnings_facts(conn, _rows())
    upsert_earnings_facts(conn, _rows())  # re-upsert same PKs
    assert len(get_earnings_facts(conn, symbols=["AAPL"])) == 1


def test_empty_upsert_is_a_noop() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_earnings_facts(conn, [])
    assert len(get_earnings_facts(conn)) == 0
