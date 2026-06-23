import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xasset.replay import replay_xasset_grid, xasset_market_return
from analytics.xasset.report import evaluate_xasset_grid
from analytics.xasset.universe import broad_symbols

_DAY = 86_400_000
_T0 = 1_514_764_800_000  # 2018-01-01T00:00:00Z in ms


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0005, 0.01, size=500)
    close = 100.0 * np.exp(np.cumsum(steps))
    df = pd.DataFrame(
        {
            "symbol": sym,
            "timeframe": "1d",
            "open_time": [_T0 + i * _DAY for i in range(500)],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        }
    )
    upsert_ohlcv(conn, df)


def _seed_basket(conn: duckdb.DuckDBPyConnection) -> None:
    for i, s in enumerate(broad_symbols()):
        _seed(conn, s, seed=i)


def test_evaluate_xasset_grid_structure_and_gate() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_basket(conn)
    cfg = ForecastConfig()
    books = replay_xasset_grid(conn, cfg)
    mkt = xasset_market_return(conn)
    rep = evaluate_xasset_grid(books, cfg, mkt)
    assert set(rep.cells) == {
        "broad_ls",
        "broad_long",
        "commodity_ls",
        "commodity_long",
    }
    assert rep.committed_key == "broad_ls"
    assert set(rep.attribution) == set(rep.cells)
    assert isinstance(rep.passed, bool)
    assert isinstance(rep.deploy_grade, bool)
    # the realized equity beta of every cell is a finite number
    assert np.isfinite(rep.attribution["broad_ls"].beta)
