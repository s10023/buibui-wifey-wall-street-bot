"""Tests for analytics/data_sync.py."""

import inspect
from datetime import date
from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from analytics.data_fetcher import OHLCV_COLUMNS
from analytics.data_quality import SessionGapReport
from analytics.data_store import (
    get_latest_open_time,
    init_schema,
    upsert_ohlcv,
)
from analytics.data_sync import backfill, sync


def _make_conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def _make_df(
    open_times: list[int], symbol: str = "AAPL", timeframe: str = "1h"
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": t,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000_000.0,
            }
            for t in open_times
        ],
        columns=OHLCV_COLUMNS,
    )


def test_data_sync_has_no_funding_or_oi_imports() -> None:
    """Phase-A guard: data_sync.py must not reference funding rates or open interest."""
    import analytics.data_sync as m

    src = inspect.getsource(m)
    assert "funding" not in src.lower()
    assert "open_interest" not in src.lower()


class TestBackfill:
    def test_fetches_and_stores_single_batch(self) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000])
        with patch("analytics.data_sync.fetch_bars", return_value=df):
            total = backfill(conn, "AAPL", "1h", 0)
        assert total == 3
        assert get_latest_open_time(conn, "AAPL", "1h") == 3_000

    def test_returns_zero_on_empty_response(self) -> None:
        conn = _make_conn()
        empty_df = pd.DataFrame(columns=OHLCV_COLUMNS)
        with patch("analytics.data_sync.fetch_bars", return_value=empty_df):
            total = backfill(conn, "AAPL", "1h", 0)
        assert total == 0

    def test_passes_through_start_ms_to_fetch_bars(self) -> None:
        conn = _make_conn()
        captured: list[Any] = []

        def capture(*args: Any, **kwargs: Any) -> pd.DataFrame:
            captured.append((args, kwargs))
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        with patch("analytics.data_sync.fetch_bars", side_effect=capture):
            backfill(conn, "AAPL", "1h", 1_700_000_000_000)
        assert captured, "fetch_bars was not called"
        args, _ = captured[0]
        # fetch_bars(symbol, interval, start_ms, limit=...)
        assert args[0] == "AAPL"
        assert args[1] == "1h"
        assert args[2] == 1_700_000_000_000


class TestSync:
    def test_fetches_from_latest_open_time(self) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _make_df([1_000_000]))
        captured: list[int] = []

        def capture(c: Any, sym: Any, tf: Any, start: int) -> int:
            captured.append(start)
            return 0

        with patch("analytics.data_sync.backfill", side_effect=capture):
            sync(conn, "AAPL", "1h")
        assert captured == [1_000_000]

    def test_raises_when_no_existing_data(self) -> None:
        conn = _make_conn()
        with pytest.raises(ValueError, match="Run backfill first"):
            sync(conn, "AAPL", "1h")

    def test_returns_zero_when_no_new_data(self) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _make_df([1_000_000]))
        empty_df = pd.DataFrame(columns=OHLCV_COLUMNS)
        with patch("analytics.data_sync.fetch_bars", return_value=empty_df):
            total = sync(conn, "AAPL", "1h")
        assert total == 0


def test_backfill_quarantines_bad_rows() -> None:
    conn = _make_conn()
    df = _make_df([1_000, 2_000, 3_000], symbol="AAPL", timeframe="1d")
    df.loc[1, "close"] = float("nan")  # one corrupt row
    with patch("analytics.data_sync.fetch_bars", return_value=df):
        stored = backfill(conn, "AAPL", "1d", 0)
    assert stored == 2  # corrupt row dropped
    row = conn.execute(
        "SELECT COUNT(*) FROM ohlcv WHERE symbol = 'AAPL' AND timeframe = '1d'"
    ).fetchone()
    assert row is not None
    assert row[0] == 2


def test_backfill_clean_data_stores_all_rows() -> None:
    conn = _make_conn()
    df = _make_df([1_000, 2_000, 3_000], symbol="MSFT", timeframe="1d")
    with patch("analytics.data_sync.fetch_bars", return_value=df):
        stored = backfill(conn, "MSFT", "1d", 0)
    assert stored == 3  # unchanged behaviour on clean data


class TestBackfillSessionGapWarning:
    def test_backfill_warns_on_session_gap(self, caplog: Any) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000], timeframe="1d")
        gappy = SessionGapReport("1d", "session", 2, 3, (date(2024, 1, 3),))
        with (
            patch("analytics.data_sync.fetch_bars", return_value=df),
            patch("analytics.data_sync.check_session_gaps", return_value=gappy),
            caplog.at_level("WARNING"),
        ):
            backfill(conn, "AAPL", "1d", 0)
        assert any("session gap" in r.message.lower() for r in caplog.records)

    def test_backfill_silent_when_no_gaps(self, caplog: Any) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000], timeframe="1d")
        clean = SessionGapReport("1d", "session", 3, 3, ())
        with (
            patch("analytics.data_sync.fetch_bars", return_value=df),
            patch("analytics.data_sync.check_session_gaps", return_value=clean),
            caplog.at_level("WARNING"),
        ):
            backfill(conn, "AAPL", "1d", 0)
        assert not any("session gap" in r.message.lower() for r in caplog.records)
