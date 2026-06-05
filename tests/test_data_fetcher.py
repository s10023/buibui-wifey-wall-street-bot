"""Tests for yfinance-backed data_fetcher."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pandas as pd

from analytics.data_fetcher import OHLCV_COLUMNS, fetch_bars


def _yf_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a UTC-naive OHLCV DataFrame matching utils.yfinance_client output."""
    idx = pd.DatetimeIndex([r["ts"] for r in rows])
    return pd.DataFrame(
        {
            "open": [r["open"] for r in rows],
            "high": [r["high"] for r in rows],
            "low": [r["low"] for r in rows],
            "close": [r["close"] for r in rows],
            "volume": [r["volume"] for r in rows],
        },
        index=idx,
    )


def _row(ts: str = "2024-01-15T14:30:00", price: float = 186.0) -> dict[str, Any]:
    return {
        "ts": ts,
        "open": 185.0,
        "high": 187.5,
        "low": 184.0,
        "close": price,
        "volume": 1_000_000.0,
    }


def test_fetch_bars_returns_canonical_columns() -> None:
    start_ms = int(datetime(2024, 1, 15, tzinfo=UTC).timestamp() * 1000)
    with patch(
        "analytics.data_fetcher.fetch_history",
        return_value=_yf_frame([_row()]),
    ):
        result = fetch_bars("AAPL", "1d", start_ms)
    assert list(result.columns) == OHLCV_COLUMNS


def test_fetch_bars_maps_fields_correctly() -> None:
    start_ms = int(datetime(2024, 1, 15, tzinfo=UTC).timestamp() * 1000)
    with patch(
        "analytics.data_fetcher.fetch_history",
        return_value=_yf_frame([_row()]),
    ):
        result = fetch_bars("AAPL", "1d", start_ms)
    row = result.iloc[0]
    assert row["symbol"] == "AAPL"
    assert row["timeframe"] == "1d"
    assert row["close"] == 186.0
    assert row["open_time"] == int(
        datetime(2024, 1, 15, 14, 30, tzinfo=UTC).timestamp() * 1000
    )


def test_fetch_bars_empty_returns_correct_columns() -> None:
    start_ms = int(datetime(2024, 1, 15, tzinfo=UTC).timestamp() * 1000)
    with patch(
        "analytics.data_fetcher.fetch_history",
        return_value=pd.DataFrame(),
    ):
        result = fetch_bars("AAPL", "1d", start_ms)
    assert result.empty
    assert list(result.columns) == OHLCV_COLUMNS


def test_fetch_bars_respects_limit() -> None:
    rows = [_row(ts=f"2024-01-{15 + i:02d}T14:30:00") for i in range(5)]
    start_ms = int(datetime(2024, 1, 15, tzinfo=UTC).timestamp() * 1000)
    with patch(
        "analytics.data_fetcher.fetch_history",
        return_value=_yf_frame(rows),
    ):
        result = fetch_bars("AAPL", "1d", start_ms, limit=3)
    assert len(result) == 3


def test_fetch_bars_drops_rows_with_nan_ohlcv() -> None:
    """yfinance intermittently returns the current forming bar with NaN OHLCV.

    Those rows must be dropped — otherwise a NULL reaches the NOT NULL ohlcv
    columns and the whole scan cycle crashes (observed live on META 1d).
    """
    good = _row(ts="2024-01-15T14:30:00", price=186.0)
    bad = _row(ts="2024-01-16T14:30:00")
    bad["close"] = float("nan")
    start_ms = int(datetime(2024, 1, 15, tzinfo=UTC).timestamp() * 1000)
    with patch(
        "analytics.data_fetcher.fetch_history",
        return_value=_yf_frame([good, bad]),
    ):
        result = fetch_bars("AAPL", "1d", start_ms)
    assert len(result) == 1
    assert result.iloc[0]["close"] == 186.0
    assert not result["close"].isna().any()


def test_fetch_bars_all_nan_returns_empty_columns() -> None:
    """A fetch whose only rows are NaN collapses to the empty canonical frame."""
    bad = _row()
    bad["close"] = float("nan")
    start_ms = int(datetime(2024, 1, 15, tzinfo=UTC).timestamp() * 1000)
    with patch(
        "analytics.data_fetcher.fetch_history",
        return_value=_yf_frame([bad]),
    ):
        result = fetch_bars("AAPL", "1d", start_ms)
    assert result.empty
    assert list(result.columns) == OHLCV_COLUMNS


def test_fetch_bars_4h_resamples_from_1h() -> None:
    """Caller asks for 4h -> fetcher pulls 1h and resamples anchored to 13:30 UTC."""
    # 8 consecutive 1h bars starting 13:30 UTC -> 2 complete 4h bars
    rows = [_row(ts=f"2024-01-15T{13 + i:02d}:30:00") for i in range(8)]
    start_ms = int(datetime(2024, 1, 15, tzinfo=UTC).timestamp() * 1000)
    with patch(
        "analytics.data_fetcher.fetch_history",
        return_value=_yf_frame(rows),
    ) as mock_fetch:
        result = fetch_bars("AAPL", "4h", start_ms)
    # underlying fetch must request 1h, not 4h
    assert mock_fetch.call_args.kwargs["interval"] == "1h"
    assert len(result) == 2
    assert (result["timeframe"] == "4h").all()
