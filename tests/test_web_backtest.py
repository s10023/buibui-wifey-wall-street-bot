"""Tests for the backtest web endpoint."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from analytics.backtest_lib import BacktestResult, Trade


def _make_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [1_700_000_000_000, 1_700_003_600_000],
            "open": [30000.0, 30100.0],
            "high": [30500.0, 31000.0],
            "low": [29500.0, 29800.0],
            "close": [30200.0, 30900.0],
            "volume": [100.0, 120.0],
        }
    )


def _make_signals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "direction": ["long"],
            "sl_price": [29000.0],
        }
    )


def test_backtest_returns_result(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backtest endpoint returns a valid BacktestResponse."""
    ohlcv_df = _make_ohlcv()
    signals_df = _make_signals()

    monkeypatch.setattr("web.api.routers.backtest.get_ohlcv", lambda *a, **kw: ohlcv_df)
    monkeypatch.setattr(
        "web.api.routers.backtest.detect_signals_for_strategy",
        lambda *a, **kw: signals_df,
    )

    result = BacktestResult(symbol="BTCUSDT", timeframe="1h", strategy="fvg")
    trade = Trade(
        signal_time=1_700_000_000_000,
        entry_time=1_700_003_600_000,
        entry_price=30100.0,
        direction="long",
        sl_price=29000.0,
        tp_price=32300.0,
        exit_time=1_700_007_200_000,
        exit_price=32300.0,
        outcome="win",
    )
    result.trades.append(trade)

    monkeypatch.setattr(
        "web.api.routers.backtest.run_backtest", lambda *a, **kw: result
    )

    resp = web_client.post(
        "/api/backtest",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "fvg",
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["strategy"] == "fvg"
    assert data["total_trades"] == 1
    assert len(data["trades"]) == 1
    assert data["trades"][0]["outcome"] == "win"
    # Long/short split fields are present (trade is long → short fields are 0/None)
    assert data["long_closed_trades"] == 1
    assert data["long_win_count"] == 1
    assert data["long_win_rate"] == pytest.approx(1.0)
    assert data["short_closed_trades"] == 0
    assert data["short_win_rate"] is None


def test_backtest_run_stamps_universe_policy(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint's upsert_backtest_run call carries the active universe policy."""
    monkeypatch.setattr(
        "web.api.routers.backtest.get_ohlcv", lambda *a, **kw: _make_ohlcv()
    )
    monkeypatch.setattr(
        "web.api.routers.backtest.detect_signals_for_strategy",
        lambda *a, **kw: _make_signals(),
    )
    result = BacktestResult(symbol="AAPL", timeframe="1d", strategy="fvg")
    monkeypatch.setattr(
        "web.api.routers.backtest.run_backtest", lambda *a, **kw: result
    )
    from utils.config_validation import UniversePolicy

    policy = UniversePolicy(
        scope="liquid_large_cap",
        as_of="today",
        survivorship_note="Test caveat: survivors only.",
    )
    monkeypatch.setattr("web.api.routers.backtest.load_universe_policy", lambda: policy)
    captured: dict[str, object] = {}

    def _fake_upsert(*args: object, **kwargs: object) -> str:
        captured.update(kwargs)
        return "run123"

    monkeypatch.setattr("web.api.routers.backtest.upsert_backtest_run", _fake_upsert)
    resp = web_client.post(
        "/api/backtest",
        json={"symbol": "AAPL", "timeframe": "1d", "strategy": "fvg"},
    )
    assert resp.status_code == 200
    assert captured["universe_policy"] == policy.to_json()


def test_backtest_unknown_strategy_returns_422(
    web_client: TestClient,
) -> None:
    """Unknown strategy returns 422."""
    resp = web_client.post(
        "/api/backtest",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "not_a_strategy",
        },
    )
    assert resp.status_code == 422


def test_backtest_no_data_returns_404(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty OHLCV returns 404."""
    monkeypatch.setattr(
        "web.api.routers.backtest.get_ohlcv",
        lambda *a, **kw: pd.DataFrame(),
    )
    resp = web_client.post(
        "/api/backtest",
        json={"symbol": "BTCUSDT", "timeframe": "1h", "strategy": "fvg"},
    )
    assert resp.status_code == 404
