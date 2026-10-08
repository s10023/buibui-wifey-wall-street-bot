"""GET /api/core-state must return exactly what the digest's ``core_state`` call does.

The DB is an in-memory seed of ``^GSPC`` ``1d`` bars on real NYSE sessions, and
the router's clock is pinned so the session boundary is deterministic.
"""

import dataclasses
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from analytics.data_store import init_schema
from analytics.overlay.frame import load_gspc_close
from analytics.overlay.live import CoreState, completed_closes, core_state
from analytics.trading_calendar import nyse_sessions

PRE_OPEN = datetime(2026, 10, 8, 9, 15, tzinfo=UTC)  # Thursday, before the bell
AFTER_CLOSE = datetime(2026, 10, 8, 21, 30, tzinfo=UTC)
LAST_CLOSED = date(2026, 10, 7)


def _seed(sessions: list[date], seed: int = 3) -> duckdb.DuckDBPyConnection:
    """``^GSPC`` ``1d`` bars stamped 04:00 UTC on each session date."""
    rng = np.random.default_rng(seed)
    closes = 5000.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(sessions))))
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    for d, c in zip(sessions, closes, strict=True):
        ms = int(pd.Timestamp(d, tz="UTC").value // 1_000_000) + 4 * 3_600_000
        conn.execute(
            "INSERT INTO ohlcv (symbol, timeframe, open_time, open, high, low, close,"
            " volume) VALUES ('^GSPC', '1d', ?, ?, ?, ?, ?, 0)",
            (ms, c, c, c, c),
        )
    return conn


def _sessions(end: date = LAST_CLOSED, n: int = 300) -> list[date]:
    return nyse_sessions(date(2025, 1, 1), end)[-n:]


Client = Callable[[duckdb.DuckDBPyConnection, datetime], TestClient]


@pytest.fixture
def client_for() -> Iterator[Client]:
    """A client whose DB is ``conn`` and whose clock reads ``now``.

    Used without ``with``, so the app's lifespan (which opens ``analytics.db``)
    never runs.
    """
    from web.api.deps import get_db, require_token
    from web.api.main import app

    clock = patch("web.api.routers.core._now")

    def make(conn: duckdb.DuckDBPyConnection, now: datetime) -> TestClient:
        app.dependency_overrides[get_db] = lambda: conn
        app.dependency_overrides[require_token] = lambda: None
        mocked.return_value = now
        return TestClient(app)

    mocked = clock.start()
    try:
        yield make
    finally:
        clock.stop()
        app.dependency_overrides.clear()


class TestSameCallAsTheDigest:
    def test_every_field_equals_core_state(self, client_for: Client) -> None:
        conn = _seed(_sessions())
        expected = core_state(completed_closes(load_gspc_close(conn), PRE_OPEN))

        data = client_for(conn, PRE_OPEN).get("/api/core-state").json()

        for f in dataclasses.fields(CoreState):
            want = getattr(expected, f.name)
            got = data[f.name]
            assert (date.fromisoformat(got) if f.name == "as_of" else got) == want
        for prop in ("exposure", "sma_distance", "flip_distance"):
            assert data[prop] == pytest.approx(getattr(expected, prop))

    def test_field_set_tracks_the_dataclass(self, client_for: Client) -> None:
        """A new ``CoreState`` field must reach the endpoint, or this fails."""
        data = client_for(_seed(_sessions()), PRE_OPEN).get("/api/core-state").json()
        dataclass_fields = {f.name for f in dataclasses.fields(CoreState)}
        properties = {n for n, v in vars(CoreState).items() if isinstance(v, property)}
        assert set(data) == dataclass_fields | properties | {"missing_sessions"}


class TestSessionBoundary:
    def test_a_forming_bar_is_excluded_until_the_close(
        self, client_for: Client
    ) -> None:
        conn = _seed(_sessions(end=date(2026, 10, 8)))

        pre = client_for(conn, PRE_OPEN).get("/api/core-state").json()
        post = client_for(conn, AFTER_CLOSE).get("/api/core-state").json()

        assert pre["as_of"] == "2026-10-07"
        assert post["as_of"] == "2026-10-08"  # positive control: the bar was there

    def test_fresh_owes_nothing(self, client_for: Client) -> None:
        data = client_for(_seed(_sessions()), PRE_OPEN).get("/api/core-state").json()
        assert data["missing_sessions"] == 0

    def test_missed_sessions_are_counted(self, client_for: Client) -> None:
        conn = _seed(_sessions()[:-2])
        data = client_for(conn, PRE_OPEN).get("/api/core-state").json()
        assert data["missing_sessions"] == 2


def test_short_history_is_404_naming_the_fix(client_for: Client) -> None:
    resp = client_for(_seed(_sessions(n=50)), PRE_OPEN).get("/api/core-state")
    assert resp.status_code == 404
    assert "make core-sync" in resp.json()["detail"]
