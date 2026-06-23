"""Edge-hunt #4 (PEAD-lite): replay grid over a seeded in-memory DuckDB."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.pead.replay import pead_market_return, replay_pead_grid
from analytics.store.earnings import upsert_earnings_facts
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.book import equity_curve

# All five are active single-name stocks AND in the S&P-100 snapshot (mega arm).
_STOCKS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")
_T0 = 1_514_764_800_000  # 2018-01-01 in Unix ms
_DAY = 86_400_000
_N = 1_100  # ~3 calendar years of daily bars (kept small for the 30s test budget)
_FY_RANGE = range(2018, 2021)  # 3 fiscal years → SUEs warmed up from 2020-Q1


def _ohlcv_rows() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    open_time = [_T0 + i * _DAY for i in range(_N)]
    frames = []
    for sym in (*_STOCKS, "SPY"):
        px = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, _N)))
        frames.append(
            pd.DataFrame(
                {
                    "symbol": sym,
                    "timeframe": "1d",
                    "open_time": open_time,
                    "open": px,
                    "high": px * 1.01,
                    "low": px * 0.99,
                    "close": px,
                    "volume": 1.0e6,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _earnings_rows() -> list[dict[str, object]]:
    rng = np.random.default_rng(3)
    rows: list[dict[str, object]] = []
    for sym in _STOCKS:
        eps = 1.0
        for fy in _FY_RANGE:
            for i, fp in enumerate(("Q1", "Q2", "Q3", "Q4")):
                eps += 0.05 + float(rng.normal(0, 0.08))
                month = (2, 5, 8, 11)[i]
                a = f"{fy}-{month:02d}-05"
                rows.append(
                    {
                        "symbol": sym,
                        "cik": sym,
                        "fy": fy,
                        "fp": fp,
                        "period_end": a,
                        "eps_diluted": round(eps, 3),
                        "announce_date": a,
                        "filed_date": a,
                        "accn": "x",
                        "source": "8k",
                    }
                )
    return rows


def _seeded_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_ohlcv(conn, _ohlcv_rows())
    upsert_earnings_facts(conn, _earnings_rows())
    return conn


def test_grid_has_four_cells_each_with_a_curve() -> None:
    conn = _seeded_conn()
    grid = replay_pead_grid(conn, ForecastConfig(), window=60)
    assert set(grid) == {"broad_ls", "broad_long", "mega_ls", "mega_long"}
    for r in grid.values():
        assert len(r.portfolio_return) > 0
        assert len(equity_curve(r)) > 0


def test_spy_benchmark_is_a_nonempty_series() -> None:
    conn = _seeded_conn()
    spy = pead_market_return(conn)
    assert isinstance(spy, pd.Series)
    assert len(spy) > 0


def test_long_short_book_actually_takes_positions() -> None:
    conn = _seeded_conn()
    grid = replay_pead_grid(conn, ForecastConfig(), window=60)
    # with warmed-up SUEs the dollar-neutral book is non-trivial
    assert np.any(grid["broad_ls"].portfolio_return != 0.0)
