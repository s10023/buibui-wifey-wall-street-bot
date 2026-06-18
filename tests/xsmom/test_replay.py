from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.replay import replay_xs, replay_xs_trials

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, slope: float) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + slope * i,
            "high": 101.0 + slope * i,
            "low": 99.0 + slope * i,
            "close": 100.0 + slope * i,
            "volume": 1000.0,
        }
        for i in range(320)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_replay_xs_returns_book_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAA", 1.0)
    _seed(conn, "BBB", -0.5)
    res = replay_xs(conn, ForecastConfig(), symbols=["AAA", "BBB"])
    assert isinstance(res, XSBookResult)
    assert res.portfolio_return.shape[0] > 0
    assert not np.isnan(res.portfolio_return).any()


def test_replay_xs_trials_has_per_speed_plus_combined() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAA", 1.0)
    _seed(conn, "BBB", -0.5)
    trials = replay_xs_trials(conn, ForecastConfig(), symbols=["AAA", "BBB"])
    assert set(trials) == {"s8_32", "s16_64", "s32_128", "s64_256", "combined"}
    for v in trials.values():
        assert isinstance(v, np.ndarray)
