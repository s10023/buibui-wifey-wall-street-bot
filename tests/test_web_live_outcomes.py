"""Tests for the live-outcomes web endpoint (GET /api/live-outcomes).

Self-contained client setup (not the shared ``web_client`` fixture): the fixture
patches ``web.api.main.duckdb.connect`` globally, which would clobber the real
``duckdb.connect`` that builds the in-memory seed connection. We create the
seed conn first, then enter the patched TestClient context.
"""

import time
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from fastapi.testclient import TestClient

from analytics.data_store import init_schema

_NOW_MS = int(time.time() * 1000)


def _client_for(conn: duckdb.DuckDBPyConnection) -> Generator[TestClient]:
    """Yield a TestClient whose get_db dependency returns ``conn``."""
    from web.api.deps import get_db, require_token
    from web.api.main import app

    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[require_token] = lambda: None
    with (
        patch("web.api.main.duckdb.connect", return_value=MagicMock()),
        patch("web.api.main.init_schema"),
        TestClient(app) as client,
    ):
        yield client
    app.dependency_overrides.clear()


def _seed_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO signal_alert_outcomes
            (signal_id, symbol, tf, strategy, direction, fired_at_ms,
             entry_price, sl_price, tp_price, rr_ratio, outcome, outcome_r)
        VALUES
            ('a','AAPL','4h','bos','short',?,100,95,105,1.0,'win',1.5),
            ('b','AAPL','4h','bos','short',?,100,95,105,1.0,'loss',-1.0),
            ('c','MSTR','1d','ema','long',?,100,95,NULL,1.0,NULL,NULL)
        """,
        (_NOW_MS, _NOW_MS, _NOW_MS),
    )
    return conn


def test_live_outcomes_endpoint() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes?days=0&min_n=1")
        assert resp.status_code == 200
        data = resp.json()

        assert data["rollup"]["total_rows"] == 3
        assert data["rollup"]["resolved"] == 2
        assert data["rollup"]["open"] == 1
        assert data["rollup"]["open_no_tp"] == 1

        cells = data["cells"]
        assert len(cells) == 1
        assert cells[0]["strategy"] == "bos"
        assert cells[0]["win_rate"] == 0.5

        assert [s["strategy"] for s in data["by_strategy"]] == ["bos"]
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_live_outcomes_empty() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rollup"]["total_rows"] == 0
        assert data["cells"] == []
        assert data["by_strategy"] == []
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_live_outcomes_includes_symbol_chip_list() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes?days=0&min_n=1")
        assert resp.status_code == 200
        data = resp.json()
        assert [(s["symbol"], s["n"]) for s in data["symbols"]] == [
            ("AAPL", 2),
            ("MSTR", 1),
        ]
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_live_outcomes_symbol_param_slices() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes?days=0&min_n=1&symbol=AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rollup"]["total_rows"] == 2
        assert data["rollup"]["resolved"] == 2
        # Chips stay global under the filter.
        assert [s["symbol"] for s in data["symbols"]] == ["AAPL", "MSTR"]
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_by_strategy_exposes_outcome_counts() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes?days=0&min_n=1")
        assert resp.status_code == 200
        row = resp.json()["by_strategy"][0]
        assert row["strategy"] == "bos"
        assert row["wins"] == 1
        assert row["losses"] == 1
        assert row["expired"] == 0
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def _seed_ohlcv(
    conn: duckdb.DuckDBPyConnection, symbol: str, closes: list[float]
) -> None:
    """Insert daily bars; the LAST close is the newest (the mark source)."""
    for i, close in enumerate(closes):
        conn.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, 100.0)",
            (
                symbol,
                _NOW_MS - (len(closes) - 1 - i) * 86_400_000,
                close,
                close,
                close,
                close,
            ),
        )


def test_open_endpoint_marks_positions() -> None:
    conn = _seed_conn()
    # MSTR has the open row ('c': long, entry 100, sl 95). Newest close 105
    # → unrealized_r = (105-100)/5 = +1.0, dist_sl_pct = 10/105.
    _seed_ohlcv(conn, "MSTR", [98.0, 105.0])
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes/open")
        assert resp.status_code == 200
        data = resp.json()
        assert data["marks_ok"] is True
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["signal_id"] == "c"
        assert pos["mark"] == 105.0
        assert abs(pos["unrealized_r"] - 1.0) < 1e-9
        assert abs(pos["dist_sl_pct"] - 10.0 / 105.0) < 1e-9
        assert pos["dist_tp_pct"] is None  # no tp on the seeded open row
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_open_endpoint_missing_price_yields_null_columns() -> None:
    # No OHLCV for MSTR at all — the row still returns with null marks and
    # the endpoint never 5xxs (mark source is the DB, best-effort by design).
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes/open")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["mark"] is None
        assert pos["unrealized_r"] is None
        assert pos["dist_sl_pct"] is None
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_open_endpoint_symbol_filter() -> None:
    conn = _seed_conn()
    conn.execute(
        """
        INSERT INTO signal_alert_outcomes
            (signal_id, symbol, tf, strategy, direction, fired_at_ms,
             entry_price, sl_price, tp_price, rr_ratio, outcome, outcome_r)
        VALUES ('d','SPY','1d','fvg','long',?,500,490,520,2.0,NULL,NULL)
        """,
        (_NOW_MS,),
    )
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes/open?symbol=SPY")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "SPY"
        assert [p["signal_id"] for p in data["positions"]] == ["d"]
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)
