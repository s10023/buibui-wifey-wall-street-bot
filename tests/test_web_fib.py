"""Tests for the Fibonacci retracement web endpoint."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient


def _make_candles(n: int = 30) -> pd.DataFrame:
    """Generate synthetic OHLCV data with clear swing high and low."""
    rows = []
    base = 100_000.0
    interval = 3_600_000  # 1 h in ms
    t0 = 1_700_000_000_000
    for i in range(n):
        # Simple sine-like wave to produce real swing pivots
        import math

        mid = base + 5_000 * math.sin(i * 0.4)
        rows.append(
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "open_time": t0 + i * interval,
                "open": mid - 100,
                "high": mid + 300,
                "low": mid - 300,
                "close": mid + 100,
                "volume": 100.0,
            }
        )
    return pd.DataFrame(rows)


def test_fib_returns_levels(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fib endpoint returns 7 levels including golden zone."""
    monkeypatch.setattr(
        "web.api.routers.fib.get_ohlcv", lambda *a, **kw: _make_candles(30)
    )
    resp = web_client.get(
        "/api/fib?symbol=BTCUSDT&timeframe=1h&start_ms=0&end_ms=9999999999999"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "swing_low" in data
    assert "swing_high" in data
    levels = data["levels"]
    assert len(levels) == 6
    labels = [lv["label"] for lv in levels]
    assert "0.0" in labels
    assert "0.5" in labels
    assert "0.618" in labels
    assert "1.0" in labels

    golden = [lv for lv in levels if lv["golden"]]
    assert len(golden) == 2
    golden_labels = {lv["label"] for lv in golden}
    assert golden_labels == {"0.5", "0.618"}


def test_fib_level_prices_in_range(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All Fib level prices fall between swing_low and swing_high."""
    monkeypatch.setattr(
        "web.api.routers.fib.get_ohlcv", lambda *a, **kw: _make_candles(30)
    )
    resp = web_client.get(
        "/api/fib?symbol=BTCUSDT&timeframe=1h&start_ms=0&end_ms=9999999999999"
    )
    assert resp.status_code == 200
    data = resp.json()
    low = data["swing_low"]
    high = data["swing_high"]
    for lv in data["levels"]:
        assert low <= lv["price"] <= high, (
            f"Level {lv['label']} price {lv['price']} outside [{low}, {high}]"
        )


def test_fib_not_enough_candles(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returns 422 when fewer than 4 candles are available."""
    monkeypatch.setattr(
        "web.api.routers.fib.get_ohlcv", lambda *a, **kw: _make_candles(2)
    )
    resp = web_client.get(
        "/api/fib?symbol=BTCUSDT&timeframe=1h&start_ms=0&end_ms=9999999999999"
    )
    assert resp.status_code == 422


def test_fib_swing_not_found(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returns 422 when no swing pivots can be detected (monotone data)."""
    # Monotonically increasing candles — no 3-bar pivots
    rows = []
    t0 = 1_700_000_000_000
    interval = 3_600_000
    for i in range(10):
        price = 100_000.0 + i * 100
        rows.append(
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "open_time": t0 + i * interval,
                "open": price,
                "high": price + 50,
                "low": price - 50,
                "close": price,
                "volume": 100.0,
            }
        )
    mono_df = pd.DataFrame(rows)
    monkeypatch.setattr("web.api.routers.fib.get_ohlcv", lambda *a, **kw: mono_df)
    resp = web_client.get(
        "/api/fib?symbol=BTCUSDT&timeframe=1h&start_ms=0&end_ms=9999999999999"
    )
    assert resp.status_code == 422
