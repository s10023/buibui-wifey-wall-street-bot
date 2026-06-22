"""Per-instrument subsystem returns and portfolio aggregation for the EWMAC trend sleeve.

All sizing is causal: the position held during day `d` is sized from information
through day `d-1` only. Forecast and vol series are `.shift(1)` before sizing;
`r_d = close_d/close_{d-1}-1` is what that position earns.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.ewmac import combine_forecasts
from analytics.forecast.vol import ew_return_vol


def instrument_returns(
    close: pd.Series,
    funding_daily: pd.Series,
    cfg: ForecastConfig,
    *,
    long_only: bool = False,
) -> pd.DataFrame:
    """Causal subsystem returns for one instrument.

    Columns: leverage, gross, turnover_cost, funding_cost, net (indexed like
    `close`). `funding_daily` is the day's summed funding rate aligned to the
    close index (0.0 where missing). Equities carry no funding, so the caller
    passes an all-zero series.

    When ``long_only`` is True the combined forecast is clipped at zero before
    sizing (long or flat, never short) — the deployable no-short form. Default
    False is byte-identical to the signed book.
    """
    forecast = combine_forecasts(
        close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
    )
    if long_only:
        forecast = forecast.clip(lower=0.0)
    forecast = forecast.shift(1)
    # ew_return_vol is already causal (.shift(1) baked in) — no extra shift
    vol_ann = ew_return_vol(close, cfg.vol_span).mul(np.sqrt(cfg.annualization_days))

    leverage = (forecast / 10.0) * (cfg.vol_target_annual / vol_ann)
    leverage = leverage.replace([np.inf, -np.inf], np.nan)

    r = close.pct_change()
    gross = leverage * r

    lev_prev = leverage.shift(1).fillna(0.0)
    turnover_cost = (leverage - lev_prev).abs() * (cfg.fee_pct + cfg.slippage_pct)

    fund = funding_daily.reindex(close.index).fillna(0.0)
    funding_cost = leverage * fund

    net = gross - turnover_cost - funding_cost

    return pd.DataFrame(
        {
            "leverage": leverage,
            "gross": gross,
            "turnover_cost": turnover_cost,
            "funding_cost": funding_cost,
            "net": net,
        }
    )


@dataclass(frozen=True)
class ForecastBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-governor (NaN-free; warm-up = 0.0)
    pre_governor_return: np.ndarray
    governor: np.ndarray  # NaN for the first gov_window warm-up bars
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]


def run_forecast_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    long_only: bool = False,
) -> ForecastBookResult:
    """Aggregate per-instrument subsystem returns + causal vol governor.

    ``long_only`` is threaded to ``instrument_returns`` (default False is the
    signed book, byte-identical to before).
    """
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    union = union.sort_values()

    per_net: dict[str, pd.Series] = {}
    net_cols: list[pd.Series] = []
    for sym, close in closes.items():
        fund = fundings.get(sym, pd.Series(0.0, index=close.index))
        out = instrument_returns(close, fund, cfg, long_only=long_only)
        net = out["net"].reindex(union)
        per_net[sym] = net
        net_cols.append(net)

    net_mat = pd.concat(net_cols, axis=1)
    active = net_mat.notna().sum(axis=1)
    pre = net_mat.mean(axis=1)  # equal risk weight across active instruments
    pre = pre.fillna(0.0)

    ann = np.sqrt(cfg.annualization_days)
    trailing_vol = (
        pre.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().shift(1) * ann
    )
    g = (cfg.vol_target_annual / trailing_vol).clip(cfg.g_min, cfg.g_max)
    port = g.fillna(0.0) * pre

    return ForecastBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_governor_return=pre.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        active_count=active.to_numpy(dtype=np.int64),
        per_instrument_net=per_net,
    )


def equity_curve(result: ForecastBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0) for the curve metrics."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
