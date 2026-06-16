from typing import Any

import duckdb
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from tools import universe_coverage
from utils.config_validation import (
    ResearchUniverse,
    UniverseMember,
    UniversePolicy,
)

_TF_MS = 4 * 60 * 60 * 1000  # 4h in ms


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, tf: str, n: int) -> None:
    rows: list[dict[str, Any]] = []
    for i in range(n):
        rows.append(
            {
                "symbol": symbol,
                "timeframe": tf,
                "open_time": i * _TF_MS,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
            }
        )
    upsert_ohlcv(conn, pd.DataFrame(rows))


def _universe() -> ResearchUniverse:
    return ResearchUniverse(
        policy=UniversePolicy(
            scope="liquid_large_cap_breadth",
            as_of="today",
            survivorship_note="bounded to survivors",
        ),
        membership_as_of="2026-06-16",
        members=(
            UniverseMember(
                symbol="AAPL",
                sector="Information Technology",
                kind="stock",
                delisted=False,
            ),
            UniverseMember(
                symbol="MSFT",
                sector="Information Technology",
                kind="stock",
                delisted=False,
            ),
        ),
    )


def test_build_coverage_rows_counts_bars_and_flags_missing() -> None:
    conn = _conn()
    _seed(conn, "AAPL", "4h", 10)
    # MSFT has no 4h bars -> should show 0 / missing
    rows = universe_coverage.build_coverage_rows(conn, _universe(), ["4h"])
    by_sym = {r.symbol: r for r in rows}
    assert by_sym["AAPL"].bars == 10
    assert by_sym["AAPL"].present is True
    assert by_sym["MSFT"].bars == 0
    assert by_sym["MSFT"].present is False


def test_summarize_reports_missing_symbols() -> None:
    conn = _conn()
    _seed(conn, "AAPL", "4h", 5)
    rows = universe_coverage.build_coverage_rows(conn, _universe(), ["4h"])
    summary = universe_coverage.summarize(rows, ["4h"])
    assert summary.n_symbols == 2
    assert "MSFT" in summary.missing_symbols
    assert "AAPL" not in summary.missing_symbols
