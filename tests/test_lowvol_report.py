import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.lowvol.replay import bab_market_return, replay_bab_grid
from analytics.lowvol.report import BabGridReport, evaluate_bab_grid
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema

_DAY = 86_400_000
_T0 = 1_514_764_800_000


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, 500))
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


def test_evaluate_bab_grid_structure_and_gate() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    cfg = ForecastConfig()
    books = replay_bab_grid(conn, cfg, syms, beta_window=60, vol_window=60)
    mkt = bab_market_return(conn, syms)
    rep = evaluate_bab_grid(books, cfg, mkt)

    assert isinstance(rep, BabGridReport)
    assert set(rep.cells) == {
        "beta_neutral_ls",
        "beta_long_only",
        "vol_neutral_ls",
        "vol_long_only",
    }
    assert rep.committed_key == "beta_neutral_ls"
    assert set(rep.attribution) == set(rep.cells)
    assert isinstance(rep.passed, bool)
    assert isinstance(rep.deploy_grade, bool)
    # the realized portfolio beta of every cell is a finite number
    assert np.isfinite(rep.attribution["beta_neutral_ls"].beta)
