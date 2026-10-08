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
        # `connect_with_retry`, not `duckdb.connect` — the lifespan stopped
        # calling the latter when the retry landed, and an unpatched lifespan
        # opens the REAL analytics.db from the test suite.
        patch("web.api.main.connect_with_retry", return_value=mock_conn),
        patch("web.api.main.init_schema"),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        yield client

    app.dependency_overrides.clear()


# The real message DuckDB 1.5.5 raises, captured in this repo 2026-08-12.
#
# A fixture saying only "Could not set lock on file" would be an invented
# truncation. `is_lock_conflict` matches on "Conflicting lock", the substring
# that actually discriminates, so the shortened wording would sail through this test
# while failing the guard. That is the failure mode a substring match always
# has: a test that writes its own version of an external system's message can
# pass while the code under test never fires.
#
# Matching the longer "Could not set lock" instead would be wrong, not just
# looser: a permissions failure produces that prefix too, and retrying it six
# times buys nothing.
LOCK_MSG = (
    'IO Error: Could not set lock on file "analytics.db": '
    "Conflicting lock is held in /usr/bin/python3.13 (PID 3364232) by user kng."
)


def test_get_db_busy_returns_503(_no_auth_client: TestClient) -> None:
    """A genuine lock conflict is transient, so get_db answers 503."""
    with patch(
        "web.api.deps.duckdb.connect",
        side_effect=duckdb.IOException(LOCK_MSG),
    ):
        resp = _no_auth_client.get(
            "/api/ohlcv",
            params={
                "symbol": "SPY",
                "timeframe": "1h",
                "start_ms": 0,
                "end_ms": 1,
            },
        )

    assert resp.status_code == 503
    assert "busy" in resp.json()["detail"].lower()


def test_get_db_does_not_report_a_missing_db_as_busy(
    _no_auth_client: TestClient,
) -> None:
    """A non-lock IOException must NOT be dressed up as a transient 503.

    DuckDB raises one exception class for every I/O failure, so the old blanket
    `except duckdb.IOException` told a user whose database was missing or
    corrupt to "try again in a few seconds" — advice that can never come true.
    This is the control that keeps the handler narrow.
    """
    with patch(
        "web.api.deps.duckdb.connect",
        side_effect=duckdb.IOException("IO Error: No such file or directory"),
    ):
        resp = _no_auth_client.get(
            "/api/ohlcv",
            params={
                "symbol": "SPY",
                "timeframe": "1h",
                "start_ms": 0,
                "end_ms": 1,
            },
        )

    assert resp.status_code == 500
    assert resp.status_code != 503
