"""Shared fixtures for tests."""

import json
import re
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd
import pytest
from fastapi.testclient import TestClient

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from string."""
    return _ANSI_RE.sub("", text)


# --- Sample data used across all tests ---

SAMPLE_COINS_CONFIG: dict[str, Any] = {
    "BTCUSDT": {"leverage": 25, "sl_percent": 2.0},
    "ETHUSDT": {"leverage": 20, "sl_percent": 2.5},
    "SOLUSDT": {"leverage": 20, "sl_percent": 3.5},
}

SAMPLE_CONFIG_JSON = json.dumps(SAMPLE_COINS_CONFIG)

SAMPLE_COIN_ORDER = list(SAMPLE_COINS_CONFIG.keys())


_OHLCV_COLS = [
    "symbol",
    "timeframe",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


def _candle(
    open_time: int,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _make_ohlcv(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_OHLCV_COLS)


@pytest.fixture()
def web_client() -> Generator[TestClient]:
    """TestClient with lifespan patched to avoid touching the real DB.

    Patches duckdb.connect in web.api.main so the lifespan never opens
    analytics.db (which may be locked by signal watch). get_db and require_token
    are overridden so route handlers receive a mock connection and skip auth.
    """
    from web.api.deps import get_db, require_token
    from web.api.main import app

    mock_conn = MagicMock(spec=duckdb.DuckDBPyConnection)

    app.dependency_overrides[get_db] = lambda: mock_conn
    app.dependency_overrides[require_token] = lambda: None

    with (
        patch("web.api.main.duckdb.connect", return_value=mock_conn),
        patch("web.api.main.init_schema"),
        TestClient(app, raise_server_exceptions=True) as client,
    ):
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def sample_coins_config() -> dict[str, Any]:
    """Valid coins.json configuration."""
    return SAMPLE_COINS_CONFIG.copy()


@pytest.fixture
def sample_coin_order() -> list[str]:
    """Coin order from config."""
    return SAMPLE_COIN_ORDER.copy()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Regenerate golden JSON files instead of comparing against them.",
    )
