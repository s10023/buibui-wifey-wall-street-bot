import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.replay import replay_residual_grid


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    idx = pd.date_range("2018-01-01", periods=500, freq="D")
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, 500))
    df = pd.DataFrame(
        {
            "symbol": sym,
            "timeframe": "1d",
            "open_time": (idx.asi8 // 10**6),
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1_000_000.0,
        }
    )
    upsert_ohlcv(conn, df)


def test_replay_residual_grid_returns_four_books() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    sector_map = {s: ("Tech" if i % 2 == 0 else "Energy") for i, s in enumerate(syms)}
    cfg = ForecastConfig()
    books = replay_residual_grid(
        conn, cfg, beta_window=60, mega_symbols=syms[:3],
        broad_symbols=syms, sector_map=sector_map,
    )
    assert set(books) == {
        "mega_raw", "mega_residual_skip", "broad_raw", "broad_residual_skip",
    }
    assert books["broad_residual_skip"].portfolio_return.shape[0] > 0


def test_residual_audit_build_row_smoke() -> None:
    from tools.xsmom_residual_audit import build_grid, long_only_sharpe

    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    sector_map = {s: ("Tech" if i % 2 == 0 else "Energy") for i, s in enumerate(syms)}
    rep = build_grid(
        conn, mega=syms[:3], broad=syms, sector_map=sector_map, slippage_bps=2.0,
    )
    assert rep.committed_key == "broad_residual_skip"
    assert "broad_residual_skip" in rep.cells
    lo = long_only_sharpe(conn, syms, sector_map, 2.0)
    assert isinstance(lo, float)
