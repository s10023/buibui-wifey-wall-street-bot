"""Read-only DuckDB front door for the cross-asset TSMOM sleeve (edge-hunt #3).

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + all-zero funding)
and runs the pre-registered 2x2 grid over the frozen cross-asset ETF basket via
``run_forecast_backtest``. The only module in ``analytics/xasset/`` that touches
the DB; never writes.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.xasset.universe import MARKET_PROXY, broad_symbols, commodity_symbols


def xasset_market_return(conn: duckdb.DuckDBPyConnection) -> pd.Series:
    """SPY daily return — the equity-beta benchmark for the guardrail (read-only).

    Loaded on its own (not as an equal-weight basket mean) so the realized-beta
    diagnostic regresses each book against the actual equity market proxy.
    """
    closes, _ = load_daily_inputs(conn, [MARKET_PROXY])
    spy = closes.get(MARKET_PROXY)
    if spy is None:
        return pd.Series(dtype=float)
    return spy.pct_change()


def replay_xasset_grid(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
) -> dict[str, ForecastBookResult]:
    """Run the pre-registered `{broad,commodity} x {long-short,long-flat}` 2x2.

    All four books run through the shared ``run_forecast_backtest`` (multi-speed
    EWMAC + portfolio vol governor). The gated cell is ``broad_ls``; the other
    three are diagnostic + feed the DSR/PBO trial count.
    """
    broad_closes, broad_fund = load_daily_inputs(conn, broad_symbols())
    comm_closes, comm_fund = load_daily_inputs(conn, commodity_symbols())

    books: dict[str, ForecastBookResult] = {}
    books["broad_ls"] = run_forecast_backtest(broad_closes, broad_fund, cfg)
    books["broad_long"] = run_forecast_backtest(
        broad_closes, broad_fund, cfg, long_only=True
    )
    books["commodity_ls"] = run_forecast_backtest(comm_closes, comm_fund, cfg)
    books["commodity_long"] = run_forecast_backtest(
        comm_closes, comm_fund, cfg, long_only=True
    )
    return books
