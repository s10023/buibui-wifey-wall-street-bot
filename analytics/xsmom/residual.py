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

_SLOW_SPEEDS_DEFAULT: tuple[tuple[int, int, float], ...] = (
    (16, 64, 3.75), (32, 128, 2.65), (64, 256, 1.91),
)  # default speeds minus the fast (8, 32) leg = the skip-month analog


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


def sector_neutral_demean(
    forecasts: pd.DataFrame, sector_map: dict[str, str]
) -> pd.DataFrame:
    """Cross-sectionally demean within each GICS sector (skipna per row).

    Columns absent from `sector_map` are grouped under their own None bucket and
    demeaned among themselves. Each row of each sector group sums to ~0.
    """
    out = forecasts.copy()
    groups: dict[str | None, list[str]] = {}
    for col in forecasts.columns:
        groups.setdefault(sector_map.get(str(col)), []).append(str(col))
    for cols in groups.values():
        sub = forecasts[cols]
        out[cols] = sub.sub(sub.mean(axis=1), axis=0)
    return out


def xs_residual_leverage(
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    beta_window: int = _BETA_WINDOW,
    sector_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Causal cross-sectional leverage from *residual* momentum, sized on
    *actual* return vol.

    Forecast = EWMAC on the residual price series (relative residual strength),
    demeaned cross-sectionally (sector-neutral when `sector_map` is given),
    `.shift(1)`-ed, then vol-parity scaled by each name's ACTUAL annualized return
    vol so the position targets real vol. Mirrors `xs_leverage` exactly except for
    the residual forecast input and the optional sector-neutral demean.
    """
    resid_closes = residual_closes(closes, beta_window)
    f = xs_forecasts(resid_closes, cfg)
    demeaned = (
        sector_neutral_demean(f, sector_map)
        if sector_map is not None
        else f.sub(f.mean(axis=1), axis=0)
    )
    demeaned_shifted = demeaned.shift(1)
    union = pd.DatetimeIndex(demeaned.index)
    ann = np.sqrt(cfg.annualization_days)

    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        lev = (demeaned_shifted[sym] / 10.0) * (cfg.vol_target_annual / vol_ann)
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    lev_df = pd.DataFrame(lev_cols, index=union)
    if cfg.xs_dollar_neutral:
        lev_df = lev_df.sub(lev_df.mean(axis=1), axis=0)
    return lev_df


def long_only_residual_leverage(
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    beta_window: int = _BETA_WINDOW,
    sector_map: dict[str, str] | None = None,
    quantile: float = 0.8,
) -> pd.DataFrame:
    """Long-only top-`quantile` residual-momentum leverage (wife-sleeve form).

    Same residual forecast + (optional) sector-neutral demean + `.shift(1)` as
    `xs_residual_leverage`, but keep only names whose shifted demeaned forecast is
    in the top cross-sectional `quantile` AND positive each day; each kept name
    gets a unit vol-targeted long, everything else is 0 (no shorts, no
    dollar-neutral re-center). Causal.
    """
    resid_closes = residual_closes(closes, beta_window)
    f = xs_forecasts(resid_closes, cfg)
    demeaned = (
        sector_neutral_demean(f, sector_map)
        if sector_map is not None
        else f.sub(f.mean(axis=1), axis=0)
    )
    shifted = demeaned.shift(1)
    thresh = shifted.quantile(quantile, axis=1)
    longs = shifted.ge(thresh, axis=0) & shifted.gt(0.0)
    union = pd.DatetimeIndex(demeaned.index)
    ann = np.sqrt(cfg.annualization_days)

    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        unit = (cfg.vol_target_annual / vol_ann).replace([np.inf, -np.inf], np.nan)
        lev_cols[sym] = unit.where(longs[sym], 0.0)
    return pd.DataFrame(lev_cols, index=union)
