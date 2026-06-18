"""Per-instrument cross-sectional forecasts, leverage, and portfolio aggregation.

All sizing is causal: the position held during day `d` is sized from information
through day `d-1` only. The cross-sectional demean is a same-day reduction over
causal forecasts; the `.shift(1)` is applied AFTER demeaning, BEFORE sizing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.ewmac import combine_forecasts
from analytics.forecast.vol import ew_return_vol


def _union_index(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    return union.sort_values()


def xs_forecasts(closes: dict[str, pd.Series], cfg: ForecastConfig) -> pd.DataFrame:
    """Raw combined EWMAC forecasts per instrument, aligned to the union daily index.

    Columns = symbols, index = sorted union of all instrument dates. NaN where an
    instrument has not-yet-warmed-up or where return-vol is undefined. NaN warmup bars
    are intentional: the cross-sectional demean (`xs_demeaned_forecasts`) skips NaN via
    `mean(axis=1)`, so only warmed-up instruments contribute to the mean.
    Causal: each column is `combine_forecasts(...)`, which uses only closes through
    each day.
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        f = combine_forecasts(
            close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
        )
        cols[sym] = f.reindex(union)
    return pd.DataFrame(cols, index=union)


def xs_demeaned_forecasts(
    closes: dict[str, pd.Series], cfg: ForecastConfig
) -> pd.DataFrame:
    """Cross-sectionally demeaned forecasts (relative strength).

    `g_i(d) = f_i(d) - mean_{j in active(d)} f_j(d)`; the row mean skips NaN so it
    is taken over the active instruments only. Each active row sums to ~0
    (dollar-neutral). Not yet shifted — see `xs_leverage`.
    """
    f = xs_forecasts(closes, cfg)
    return f.sub(f.mean(axis=1), axis=0)


def xs_leverage(closes: dict[str, pd.Series], cfg: ForecastConfig) -> pd.DataFrame:
    """Causal cross-sectional (demeaned) vol-parity leverage matrix.

    Demean the forecast across active instruments (dollar-neutral), shift one day
    (position on day `d` uses info through `d-1`), then vol-target each leg:
    `leverage_i = (g_i_shifted / 10) * (vol_target / vol_ann_i)`. The `/10` mirrors
    the trend sleeve so magnitudes are comparable; the absolute level is governed
    downstream. Columns = symbols, index = union daily index. When
    ``cfg.xs_dollar_neutral`` is set, the matrix is re-centered so each day's
    active leverage sums to zero (dollar-neutral).
    """
    demeaned = xs_demeaned_forecasts(closes, cfg)
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
        # Subtract the per-day active-set mean leverage so each day's positions
        # net to zero (dollar-neutral). Same skipna idiom as the forecast demean:
        # NaN cells stay NaN; a same-day op on already-shifted leverage adds no
        # look-ahead.
        lev_df = lev_df.sub(lev_df.mean(axis=1), axis=0)
    return lev_df


@dataclass(frozen=True)
class XSBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-governor (NaN-free; warm-up = 0.0)
    pre_governor_return: np.ndarray
    governor: np.ndarray  # NaN for the first gov_window warm-up bars
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]


def run_xs_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> XSBookResult:
    """Causal dollar-neutral long-short book over the demeaned forecast.

    Per instrument: gross = leverage * return; honest costs = turnover
    `|Δlev|*(fee+slip)` + funding `leverage*funding` (shorts receive funding).
    Aggregate = SUM of legs (long-short portfolio P&L; the level is set by the
    causal 20%-vol governor, so sum-vs-mean is only a scale it absorbs).
    """
    leverage = xs_leverage(closes, cfg)
    union = pd.DatetimeIndex(leverage.index)
    cost = cfg.fee_pct + cfg.slippage_pct

    per_net: dict[str, pd.Series] = {}
    net_cols: list[pd.Series] = []
    for sym, close in closes.items():
        lev = leverage[sym]
        r = close.pct_change().reindex(union)
        gross = lev * r
        turnover = (lev - lev.shift(1).fillna(0.0)).abs() * cost
        fund = (
            fundings.get(sym, pd.Series(0.0, index=close.index))
            .reindex(union)
            .fillna(0.0)
        )
        funding_cost = lev * fund
        net = gross - turnover - funding_cost
        per_net[sym] = net
        net_cols.append(net)

    net_mat = pd.concat(net_cols, axis=1)
    active = net_mat.notna().sum(axis=1)
    pre = net_mat.sum(axis=1)  # all-NaN warm-up rows -> 0.0 (skipna)

    ann = np.sqrt(cfg.annualization_days)
    trailing_vol = (
        pre.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().shift(1) * ann
    )
    g = (cfg.vol_target_annual / trailing_vol).clip(cfg.g_min, cfg.g_max)
    port = g.fillna(0.0) * pre

    return XSBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_governor_return=pre.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        active_count=active.to_numpy(dtype=np.int64),
        per_instrument_net=per_net,
    )


def equity_curve(result: XSBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0) for the curve-metrics slice."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
