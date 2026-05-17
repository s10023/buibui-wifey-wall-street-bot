"""Tests for the signals web endpoint."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient


def _make_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "open": [30000.0],
            "high": [30500.0],
            "low": [29500.0],
            "close": [30200.0],
            "volume": [100.0],
        }
    )


def _make_signals_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "direction": ["long"],
            "reason": ["FVG fill"],
            "sl_price": [29000.0],
            "context": ["bullish"],
        }
    )


def test_signals_returns_list(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signals endpoint returns detected signals."""
    monkeypatch.setattr(
        "web.api.routers.signals.get_ohlcv", lambda *a, **kw: _make_ohlcv()
    )
    monkeypatch.setattr(
        "web.api.routers.signals.detect_signals_for_strategy",
        lambda *a, **kw: _make_signals_df(),
    )
    resp = web_client.post(
        "/api/signals",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "start_ms": 0,
            "end_ms": 9_999_999_999_999,
            "strategies": ["fvg"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "signals" in data
    assert len(data["signals"]) == 1
    assert data["signals"][0]["direction"] == "long"
    assert data["signals"][0]["strategy"] == "fvg"


def test_signals_unknown_strategy_returns_422(
    web_client: TestClient,
) -> None:
    """Unknown strategy in signals list returns 422."""
    resp = web_client.post(
        "/api/signals",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "start_ms": 0,
            "end_ms": 9_999_999_999_999,
            "strategies": ["not_real"],
        },
    )
    assert resp.status_code == 422


def test_signals_no_ohlcv_returns_404(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty OHLCV returns 404."""
    monkeypatch.setattr(
        "web.api.routers.signals.get_ohlcv", lambda *a, **kw: pd.DataFrame()
    )
    resp = web_client.post(
        "/api/signals",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "start_ms": 0,
            "end_ms": 9_999_999_999_999,
            "strategies": ["fvg"],
        },
    )
    assert resp.status_code == 404


def test_signals_empty_result(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No signals detected returns empty list (not an error)."""
    monkeypatch.setattr(
        "web.api.routers.signals.get_ohlcv", lambda *a, **kw: _make_ohlcv()
    )
    monkeypatch.setattr(
        "web.api.routers.signals.detect_signals_for_strategy",
        lambda *a, **kw: pd.DataFrame(),
    )
    resp = web_client.post(
        "/api/signals",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "start_ms": 0,
            "end_ms": 9_999_999_999_999,
            "strategies": ["fvg"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["signals"] == []
