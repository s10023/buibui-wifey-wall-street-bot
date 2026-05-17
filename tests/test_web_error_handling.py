"""Tests for web API error-handling behaviour."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def _no_auth_client() -> Generator[TestClient]:
    """TestClient with auth bypassed but get_db NOT overridden.

    This lets get_db run its real logic so we can test its error handling.
    Lifespan is still patched to avoid touching the real DB.
    """
    from web.api.deps import require_token
    from web.api.main import app

    mock_conn = MagicMock(spec=duckdb.DuckDBPyConnection)

    app.dependency_overrides[require_token] = lambda: None

    with (
        patch("web.api.main.duckdb.connect", return_value=mock_conn),
        patch("web.api.main.init_schema"),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        yield client

    app.dependency_overrides.clear()


def test_get_db_busy_returns_503(_no_auth_client: TestClient) -> None:
    """When DuckDB is locked (signal-watch writing), get_db raises 503."""
    with patch(
        "web.api.deps.duckdb.connect",
        side_effect=duckdb.IOException("Could not set lock on file"),
    ):
        resp = _no_auth_client.get(
            "/api/ohlcv",
            params={
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "start_ms": 0,
                "end_ms": 1,
            },
        )

    assert resp.status_code == 503
    assert "busy" in resp.json()["detail"].lower()
