"""Causal low-beta / BAB signal + leverage primitives (edge-hunt #2).

Pure: numpy + pandas + the forecast vol primitive + the causal `rolling_beta` and
`equal_weight_market_return` already shipped for the residual XS sleeve. No DB, no
engine. Every transform is causal — trailing rolling windows are `.shift(1)`-ed so
the position held during day `d` is sized from information through day `d-1`.
Spec: docs/superpowers/specs/2026-06-22-edge-hunt-2-lowvol-bab-design.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.vol import ew_return_vol
from analytics.xsmom.diagnostics import equal_weight_market_return
from analytics.xsmom.residual import rolling_beta

_BETA_WINDOW = 252  # a-priori trailing sessions for the market beta (matches residual)
_VOL_WINDOW = 252  # a-priori trailing sessions for the realized-vol ranking


def _union(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex([])
    for s in closes.values():
        idx = idx.union(pd.DatetimeIndex(s.index))
    return idx.sort_values()


def causal_betas(
    closes: dict[str, pd.Series], *, window: int = _BETA_WINDOW
) -> pd.DataFrame:
    """Per-name trailing market beta vs the equal-weight market, `.shift(1)`-ed.

    Beta at day `d` is computed from returns through `d-1` (causal). Columns =
    symbols, index = sorted union daily index. Warm-up bars are NaN.
    """
    mkt = equal_weight_market_return(closes)
    union = _union(closes)
    cols = {
        sym: rolling_beta(close.pct_change(), mkt, window).shift(1).reindex(union)
        for sym, close in closes.items()
    }
    return pd.DataFrame(cols, index=union)


def realized_vols(
    closes: dict[str, pd.Series], *, window: int = _VOL_WINDOW
) -> pd.DataFrame:
    """Per-name trailing realized return vol (rolling std), `.shift(1)`-ed.

    Vol at day `d` uses returns through `d-1` (causal). Parallels `causal_betas`
    so the two grid rows use the same as-of-`d-1` convention.
    """
    union = _union(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        v = close.pct_change().rolling(window, min_periods=window).std().shift(1)
        cols[sym] = v.reindex(union)
    return pd.DataFrame(cols, index=union)


def cross_sectional_score(metric: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-scored *negative* demean: low metric -> high (long) score.

    Per row: `-(x - row_mean) / row_std`. Dimensionless so the beta-rank and
    vol-rank rows size into the same vol-target band (Sharpe is scale-invariant
    regardless). Rows with <2 finite values or zero dispersion -> all-NaN; inf -> NaN.
    """
    demeaned = metric.sub(metric.mean(axis=1), axis=0)
    z = demeaned.div(metric.std(axis=1), axis=0)
    return (-z).replace([np.inf, -np.inf], np.nan)


def _beta_neutralize(lev: pd.DataFrame, betas: pd.DataFrame) -> pd.DataFrame:
    """Scale the short leg per day so the net causal portfolio beta is zero.

    `Σ_i w_i β_i = 0` after scaling the short leg by `k = -β_long / β_short`
    (β_long = Σ over long positions, β_short = Σ over short positions). A day with
    no valid short leg, k ≤ 0, or k NaN is left untouched (residual beta accepted;
    the realized-beta diagnostic confirms it nets to ≈0 across the full sample).
    """
    contrib = lev * betas
    long_mask = lev > 0
    short_mask = lev < 0
    beta_long = contrib.where(long_mask).sum(axis=1, min_count=1)
    beta_short = contrib.where(short_mask).sum(axis=1, min_count=1)
    k = (-beta_long / beta_short).replace([np.inf, -np.inf], np.nan)
    k = k.where(k.notna() & (k > 0.0), 1.0)
    scaled = lev.mul(k, axis=0)
    return lev.where(~short_mask, scaled)


def beta_neutral_leverage(
    score: pd.DataFrame,
    betas: pd.DataFrame,
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> pd.DataFrame:
    """Vol-parity long-short leverage from a cross-sectional `score`, beta-neutralized.

    `lev_i = score_i * (vol_target / vol_ann_i)` (vol-parity, the `xs_*_leverage`
    machinery), then the short leg is scaled so the net causal portfolio beta is
    zero (`_beta_neutralize`). `score` is already `.shift(1)`-ed via `causal_betas`
    / `realized_vols`, so no further shift here. `betas` (always the causal market
    betas) drives the neutralization regardless of which metric `score` ranks on.
    """
    union = pd.DatetimeIndex(score.index)
    ann = np.sqrt(cfg.annualization_days)
    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        lev = score[sym] * (cfg.vol_target_annual / vol_ann)
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    lev_df = pd.DataFrame(lev_cols, index=union)
    return _beta_neutralize(lev_df, betas.reindex(union))
