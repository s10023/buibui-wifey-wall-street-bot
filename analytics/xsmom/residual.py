"""Residual (beta/sector-neutral) cross-sectional momentum primitives.

Pure: numpy + pandas + the forecast EWMAC/vol primitives. No DB, no engine. Every
transform is causal — a trailing rolling window or a same-day reduction over
already-causal inputs; positions are shifted downstream in `xs_residual_leverage`.
Experiment #1 (docs/superpowers/specs/2026-06-21-equity-edge-hunt-roadmap-
residual-xsmom-design.md).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.vol import ew_return_vol
from analytics.xsmom.book import xs_forecasts
from analytics.xsmom.diagnostics import equal_weight_market_return


def rolling_beta(inst_ret: pd.Series, mkt_ret: pd.Series, window: int) -> pd.Series:
    """Causal trailing OLS beta = Cov(inst, mkt) / Var(mkt) over `window` bars.

    `min_periods=window` so warm-up bars are NaN. Uses only data through each day
    (a trailing window never reads future bars). inf/zero-var -> NaN.
    """
    df = pd.concat([inst_ret, mkt_ret], axis=1, keys=["i", "m"])
    cov = df["i"].rolling(window, min_periods=window).cov(df["m"])
    var = df["m"].rolling(window, min_periods=window).var()
    beta = cov / var
    return beta.replace([np.inf, -np.inf], np.nan)


def residual_returns(
    inst_ret: pd.Series, mkt_ret: pd.Series, beta: pd.Series
) -> pd.Series:
    """`r_i - beta_i * r_mkt`, aligned on the union of the three indices."""
    idx = inst_ret.index.union(mkt_ret.index).union(beta.index)
    i = inst_ret.reindex(idx)
    m = mkt_ret.reindex(idx)
    b = beta.reindex(idx)
    return i - b * m


_BETA_WINDOW = 252  # a-priori trailing sessions for the market beta


def residual_close(
    inst_close: pd.Series, mkt_ret: pd.Series, window: int
) -> pd.Series:
    """Synthetic residual *price* = cumprod(1 + residual_returns).

    Feedable to `combine_forecasts` as a price series so EWMAC momentum is
    computed on the beta-stripped path. Leading warm-up bars are NaN.
    """
    inst_ret = inst_close.pct_change()
    beta = rolling_beta(inst_ret, mkt_ret, window)
    resid = residual_returns(inst_ret, mkt_ret, beta)
    return (1.0 + resid).cumprod()


def residual_closes(
    closes: dict[str, pd.Series], window: int = _BETA_WINDOW
) -> dict[str, pd.Series]:
    """Residual price series per instrument vs the equal-weight market."""
    mkt = equal_weight_market_return(closes)
    return {sym: residual_close(c, mkt, window) for sym, c in closes.items()}
