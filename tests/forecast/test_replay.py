import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs, replay_universe
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, n: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2018-01-01", tz="UTC")
    steps = rng.normal(0.0006, 0.012, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    rows = pd.DataFrame(
        {
            "symbol": sym,
            "timeframe": "1d",
            "open_time": [
                int((start + pd.Timedelta(days=i)).timestamp() * 1000) for i in range(n)
            ],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        }
    )
    upsert_ohlcv(conn, rows)


def test_load_daily_inputs_returns_zero_funding() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAA", 400, 0)
    closes, fundings = load_daily_inputs(conn, ["AAA", "MISSING"])
    assert "AAA" in closes and "MISSING" not in closes  # empty symbol skipped
    assert (fundings["AAA"] == 0.0).all()  # equities: no funding


def test_replay_universe_runs_book() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    for k, sym in enumerate(("AAA", "BBB", "CCC")):
        _seed(conn, sym, 500, k)
    result = replay_universe(conn, ForecastConfig(), symbols=["AAA", "BBB", "CCC"])
    assert len(result.portfolio_return) > 0
    assert np.isfinite(result.portfolio_return).all()
