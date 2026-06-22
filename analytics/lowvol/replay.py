"""Read-only DuckDB front door for the low-beta / BAB sleeve (edge-hunt #2).

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + all-zero equity
funding) and runs the BAB 2x2 over the breadth universe's active single-name
stocks. The only module in ``analytics/lowvol/`` that touches the DB; never writes.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.lowvol.signals import (
    beta_neutral_leverage,
    causal_betas,
    cross_sectional_score,
    long_only_leverage,
    realized_vols,
)
from analytics.xsmom.book import XSBookResult, run_xs_backtest
from analytics.xsmom.diagnostics import equal_weight_market_return
from utils.config_validation import load_research_universe


def _universe_symbols(min_history_days: int | None) -> list[str]:
    uni = load_research_universe(min_history_days=min_history_days)
    return uni.stocks()  # active single-name stocks; ETFs excluded from the XS set


def bab_market_return(conn: duckdb.DuckDBPyConnection, symbols: list[str]) -> pd.Series:
    """Equal-weight market daily return over the symbol set (read-only)."""
    closes, _ = load_daily_inputs(conn, symbols)
    return equal_weight_market_return(closes)


def replay_bab_grid(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    beta_window: int,
    vol_window: int,
    min_history_days: int | None = None,
) -> dict[str, XSBookResult]:
    """Run the pre-registered `{beta,vol} x {beta-neutral L/S, long-only}` 2x2.

    All four books are sized on actual closes and booked through the shared
    cost-aware ``run_xs_backtest``. The gated cell is ``beta_neutral_ls``; the
    other three are diagnostic + feed the DSR/PBO trial count.
    """
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)

    betas = causal_betas(closes, window=beta_window)
    vols = realized_vols(closes, window=vol_window)
    beta_score = cross_sectional_score(betas)
    vol_score = cross_sectional_score(vols)

    books: dict[str, XSBookResult] = {}
    books["beta_neutral_ls"] = run_xs_backtest(
        closes,
        fundings,
        cfg,
        leverage=beta_neutral_leverage(beta_score, betas, closes, cfg),
    )
    books["beta_long_only"] = run_xs_backtest(
        closes, fundings, cfg, leverage=long_only_leverage(beta_score, closes, cfg)
    )
    books["vol_neutral_ls"] = run_xs_backtest(
        closes,
        fundings,
        cfg,
        leverage=beta_neutral_leverage(vol_score, betas, closes, cfg),
    )
    books["vol_long_only"] = run_xs_backtest(
        closes, fundings, cfg, leverage=long_only_leverage(vol_score, closes, cfg)
    )
    return books
