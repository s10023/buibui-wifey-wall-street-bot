"""Read-only DuckDB front door for the velocity-alternation sleeve (edge-hunt #6).

The only module in ``analytics/velocity/`` that touches the DB; never writes.

This sleeve needs closes only — ``depth`` and ``duration`` are both defined off
the close series — so it reuses ``gapfill.replay.load_daily_ohlc`` rather than
adding a fifth loader with its own index conventions. That loader already asserts
(``TestLoaderMatchesForecastLoader``) that its closes are identical to the
forecast sleeve's, so all six sleeves are provably describing one population.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.gapfill.replay import load_daily_ohlc
from analytics.lowvol.signals import (
    beta_neutral_leverage,
    causal_betas,
    long_only_leverage,
)
from analytics.velocity.signals import (
    cross_sectional_long_score,
    depth_score,
    duration_score,
    velocity_score,
)
from analytics.xsmom.book import XSBookResult, run_xs_backtest
from analytics.xsmom.diagnostics import equal_weight_market_return
from utils.config_validation import load_research_universe

COMMITTED_KEY = "broad_ls"
"""The pre-registered gated cell. Declared here, before any result is read."""

CONTROL_KEYS = ("depth_control", "duration_control")
"""The two decomposition controls. Read their correlations before the Sharpe."""


def _universe_symbols(min_history_days: int | None) -> list[str]:
    uni = load_research_universe(min_history_days=min_history_days)
    return uni.stocks()  # active single-name stocks; ETFs excluded from the XS set


def velocity_market_return(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.Series:
    """Equal-weight market daily return over the symbol set (read-only)."""
    _, closes, _ = load_daily_ohlc(conn, symbols)
    return equal_weight_market_return(closes)


def replay_velocity_grid(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> tuple[dict[str, XSBookResult], dict[str, pd.DataFrame]]:
    """Run the pre-registered four-arm family. Returns ``(books, leverages)``.

    * ``broad_ls`` — beta-neutral cross-sectional long/short on ``-velocity``.
      **The gated cell** (``COMMITTED_KEY``), declared before results are read.
    * ``depth_control`` — the same book on ``-depth`` alone. Isolates magnitude;
      this is reversal/momentum in disguise.
    * ``duration_control`` — the same book on ``+duration`` alone. Isolates time.
    * ``long_only`` — top-quintile long-only form. Reported for family
      consistency, **not read as evidence**: #198 measured ``long_only`` to be a
      construction artifact in every sleeve (``run_xs_backtest`` SUMS legs, which
      offsets in L/S and does not in long-only -> realized beta +44.6).

    All four are sized on actual closes and booked through the shared cost-aware
    ``run_xs_backtest``, so costs and the vol governor are identical across arms,
    and the family is the honest multiple-testing set for DSR/PBO.

    The leverage matrices are returned alongside because turnover is not on
    ``XSBookResult`` and turnover is what killed edge-hunt #5 (~211x daily gross)
    — a sleeve that cannot state its turnover cannot claim to be tradeable.
    """
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    bars, closes, fundings = load_daily_ohlc(conn, syms)
    if not closes:
        return {}, {}

    betas = causal_betas(closes)
    thesis_z = cross_sectional_long_score(velocity_score(bars))
    depth_z = cross_sectional_long_score(depth_score(bars))
    duration_z = cross_sectional_long_score(duration_score(bars))

    leverages = {
        "broad_ls": beta_neutral_leverage(thesis_z, betas, closes, cfg),
        "depth_control": beta_neutral_leverage(depth_z, betas, closes, cfg),
        "duration_control": beta_neutral_leverage(duration_z, betas, closes, cfg),
        "long_only": long_only_leverage(thesis_z, closes, cfg),
    }
    books = {
        key: run_xs_backtest(closes, fundings, cfg, leverage=lev)
        for key, lev in leverages.items()
    }
    return books, leverages
