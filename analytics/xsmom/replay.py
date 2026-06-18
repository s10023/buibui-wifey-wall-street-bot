"""Read-only DuckDB front door for the cross-sectional momentum sleeve (equity).

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + all-zero equity
funding) and runs the XS book over the breadth universe's active single-name
stocks (``.stocks()`` — ETFs excluded from the cross-section). The only module in
``analytics/xsmom/`` that touches the DB; never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.xsmom.book import XSBookResult, run_xs_backtest
from utils.config_validation import load_research_universe


def _universe_symbols(min_history_days: int | None) -> list[str]:
    uni = load_research_universe(min_history_days=min_history_days)
    return uni.stocks()  # active single-name stocks; ETFs excluded from the XS set


def replay_xs(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> XSBookResult:
    """Load the universe's 1d inputs and run the XS book (read-only)."""
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)
    return run_xs_backtest(closes, fundings, cfg)


def replay_xs_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> dict[str, np.ndarray]:
    """Daily XS portfolio returns per single-speed sleeve + the combined book.

    The honest multiple-testing family for DSR/PBO. Keys:
    ``s{fast}_{slow}`` per speed in ``cfg.speeds``, plus ``combined``.
    """
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for fast, slow, scalar in cfg.speeds:
        single_cfg = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
        result = run_xs_backtest(closes, fundings, single_cfg)
        trials[f"s{fast}_{slow}"] = result.portfolio_return
    combined = run_xs_backtest(closes, fundings, cfg)
    trials["combined"] = combined.portfolio_return
    return trials
