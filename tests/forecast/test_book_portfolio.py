from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.book import (
    ForecastBookResult,
    equity_curve,
    run_forecast_backtest,
)
from analytics.forecast.config import ForecastConfig


def _series(values: np.ndarray, start: str = "2021-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx)


def test_two_instrument_uptrend_positive_and_shapes() -> None:
    a = _series(np.linspace(100.0, 400.0, 600))
    b = _series(np.linspace(50.0, 220.0, 600))
    z = pd.Series(0.0, index=a.index)
    res = run_forecast_backtest(
        {"AAA": a, "BBB": b}, {"AAA": z, "BBB": z}, ForecastConfig()
    )
    assert isinstance(res, ForecastBookResult)
    assert len(res.daily_index) == len(res.portfolio_return)
    assert set(res.per_instrument_net) == {"AAA", "BBB"}
    curve = equity_curve(res)
    assert curve.iloc[-1] > curve.iloc[0]  # net-positive trend book


def test_governor_is_clamped() -> None:
    a = _series(np.linspace(100.0, 400.0, 600))
    z = pd.Series(0.0, index=a.index)
    cfg = ForecastConfig()
    res = run_forecast_backtest({"AAA": a}, {"AAA": z}, cfg)
    g = res.governor[~np.isnan(res.governor)]
    assert (g >= cfg.g_min - 1e-9).all()
    assert (g <= cfg.g_max + 1e-9).all()


def test_governor_is_causal() -> None:
    # Use a volatile random-walk so governor is not clamped at k.
    # Perturb a MIDDLE bar k: governor at index k uses trailing_vol[k] which
    # is computed via .shift(1) from pre[:k] only — so close[k] must not
    # affect governor[:k+1].
    rng = np.random.default_rng(42)
    prices = np.exp(np.cumsum(rng.standard_normal(600) * 0.05)) * 100.0
    a = _series(prices)
    z = pd.Series(0.0, index=a.index)
    cfg = ForecastConfig()
    base = run_forecast_backtest({"AAA": a}, {"AAA": z}, cfg)
    # k=100 is in an unclamped region (verified: governor[100] ≈ 0.656)
    k = 100
    bumped = a.copy()
    bumped.iloc[k] *= 3.0
    after = run_forecast_backtest({"AAA": bumped}, {"AAA": z}, cfg)
    np.testing.assert_allclose(
        base.governor[: k + 1], after.governor[: k + 1], equal_nan=True
    )


def test_inactive_instrument_excluded_from_mean() -> None:
    # BBB starts late (NaNs before its listing) -> early days driven by AAA only
    a = _series(np.linspace(100.0, 400.0, 600))
    b_vals = np.concatenate([np.full(300, np.nan), np.linspace(50.0, 90.0, 300)])
    b = _series(b_vals)
    z = pd.Series(0.0, index=a.index)
    res = run_forecast_backtest(
        {"AAA": a, "BBB": b}, {"AAA": z, "BBB": z}, ForecastConfig()
    )
    # active count rises once BBB warms up
    assert res.active_count[50] <= 1
    assert res.active_count[-1] == 2


def test_single_instrument_portfolio_return_is_finite_and_aligned() -> None:
    # additive equity check: a single trending name yields a NaN-free portfolio
    # return whose length equals the union index (warm-up days are 0.0, not NaN).
    a = _series(np.linspace(100.0, 400.0, 600))
    z = pd.Series(0.0, index=a.index)
    res = run_forecast_backtest({"AAA": a}, {"AAA": z}, ForecastConfig())
    assert len(res.portfolio_return) == len(res.daily_index)
    assert np.isfinite(res.portfolio_return).all()
