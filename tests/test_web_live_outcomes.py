"""Tests for the live-outcomes web endpoint (GET /api/live-outcomes).

Self-contained client setup (not the shared ``web_client`` fixture): the fixture
patches ``web.api.main.duckdb.connect`` globally, which would clobber the real
``duckdb.connect`` used to build the in-memory seed connection. We create the
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
