"""Tests for signal_runner._update_ohlcv_cache."""

import duckdb
import pandas as pd

from analytics.data_store import init_schema, upsert_ohlcv
from analytics.signal_runner import _update_ohlcv_cache

_MS = 15 * 60 * 1000  # 15 minutes in ms
_T0 = 1_700_000_000_000  # arbitrary base timestamp (ms)


def _make_row(open_time: int, close: float, volume: float) -> dict:
    return {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "open_time": open_time,
        "open": close,
        "high": close + 10,
        "low": close - 10,
        "close": close,
        "volume": volume,
    }


def _seed_db(conn: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    upsert_ohlcv(conn, df)


def test_warm_cache_replaces_stale_last_row() -> None:
    """The last cached row (a partial candle) must be replaced with its final values.

    Bug: the old code queried from cached_max_ts+1, so the partial last row was
    never updated in the cache even after sync() finalised it in the DB.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    # Seed DB with two closed candles and one partial (current open).
    rows = [
        _make_row(_T0, close=100.0, volume=50.0),  # closed
        _make_row(_T0 + _MS, close=200.0, volume=10.0),  # partial (low volume)
    ]
    _seed_db(conn, rows)

    cache: dict = {}
    now_ms = _T0 + 2 * _MS

    # Cold start — cache built from DB.
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, now_ms)
    assert len(cache[("BTCUSDT", "15m")]) == 2
    assert float(cache[("BTCUSDT", "15m")]["volume"].iloc[-1]) == 10.0  # partial

    # Simulate sync() finalising the partial candle with a large volume spike,
    # and adding the next partial candle.
    rows[1] = _make_row(_T0 + _MS, close=210.0, volume=500.0)  # now finalised
    rows.append(_make_row(_T0 + 2 * _MS, close=210.0, volume=5.0))  # new partial
    _seed_db(conn, rows)

    # Warm update — should replace the stale last row and append the new partial.
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 3 * _MS)

    df = cache[("BTCUSDT", "15m")]
    assert len(df) == 3  # T0, T0+15m (finalised), T0+30m (new partial)
    # The finalised candle must have the updated volume, not the stale partial.
    assert float(df["volume"].iloc[1]) == 500.0, "stale partial volume not replaced"
    assert float(df["close"].iloc[1]) == 210.0, "stale partial close not replaced"


def test_warm_cache_invalidates_on_gap() -> None:
    """If >2 rows arrive the cache is fully rebuilt (missed cycle / gap)."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    rows = [
        _make_row(_T0 + i * _MS, close=float(i * 100), volume=50.0) for i in range(3)
    ]
    _seed_db(conn, rows)

    cache: dict = {}
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 3 * _MS)
    assert len(cache[("BTCUSDT", "15m")]) == 3

    # Add 3 new rows — simulates daemon being down for 2 cycles.
    extra = [
        _make_row(_T0 + i * _MS, close=float(i * 100), volume=50.0) for i in range(3, 6)
    ]
    _seed_db(conn, extra)

    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 6 * _MS)
    assert len(cache[("BTCUSDT", "15m")]) == 6  # full rebuild
