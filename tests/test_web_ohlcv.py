"""Tests for the OHLCV web endpoint."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient


def test_health_no_auth(web_client: TestClient) -> None:
    """Health endpoint requires no auth."""
    resp = web_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_missing_token_returns_403_or_401(web_client: TestClient) -> None:
    """Requests without Bearer token are rejected."""
    from web.api.deps import require_token
    from web.api.main import app

    app.dependency_overrides.pop(require_token, None)
    try:
        resp = web_client.get("/api/ohlcv?symbol=AAPL&timeframe=1h&start_ms=0&end_ms=1")
        assert resp.status_code in (401, 403)
    finally:
        from web.api.deps import require_token as rt  # re-import to restore

        app.dependency_overrides[rt] = lambda: None


def test_ohlcv_returns_candles(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OHLCV endpoint returns candle data from mocked DB."""
    sample = pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "open": [30000.0],
            "high": [30500.0],
            "low": [29500.0],
            "close": [30200.0],
            "volume": [100.0],
        }
    )
    monkeypatch.setattr("web.api.routers.ohlcv.get_ohlcv", lambda *a, **kw: sample)
    resp = web_client.get(
        "/api/ohlcv?symbol=AAPL&timeframe=1h&start_ms=0&end_ms=9999999999999"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candles"]) == 1
    assert data["candles"][0]["open_time"] == 1_700_000_000_000
    assert data["candles"][0]["open"] == 30000.0
