"""Tests for analytics.xsmom.report — XSReport + evaluate_xs."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.report import XSReport, evaluate_xs


def _result(returns: np.ndarray) -> XSBookResult:
    idx = pd.date_range("2021-01-01", periods=len(returns), freq="D")
    return XSBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 2, dtype=np.int64),
        per_instrument_net={"AAA": pd.Series(returns, index=idx)},
    )


def test_report_shape_and_corr_to_trend() -> None:
    rng = np.random.default_rng(0)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    trials = {"combined": r, "s8_32": r * 1.1, "s64_256": r * 0.2}
    trend = 0.0008 + 0.01 * rng.standard_normal(800)
    rep = evaluate_xs(res, ForecastConfig(), trial_returns=trials, trend_returns=trend)
    assert isinstance(rep, XSReport)
    assert rep.n_obs == 800
    assert rep.boot_lo <= rep.sharpe_annual <= rep.boot_hi
    assert 0.0 <= rep.pbo <= 1.0
    assert -1.0 <= rep.corr_to_trend <= 1.0
    assert rep.trend_sharpe != 0.0


def test_corr_to_trend_identical_is_one() -> None:
    rng = np.random.default_rng(1)
    r = 0.001 + 0.01 * rng.standard_normal(400)
    res = _result(r)
    rep = evaluate_xs(
        res, ForecastConfig(), trial_returns={"combined": r}, trend_returns=r
    )
    assert rep.corr_to_trend > 0.99
    # single-trial family -> PBO guard returns NaN
    assert math.isnan(rep.pbo)


def test_corr_to_trend_anticorrelated_is_negative() -> None:
    rng = np.random.default_rng(2)
    r = 0.001 + 0.01 * rng.standard_normal(400)
    res = _result(r)
    rep = evaluate_xs(
        res, ForecastConfig(), trial_returns={"combined": r}, trend_returns=-r
    )
    assert rep.corr_to_trend < -0.99
    assert math.isnan(rep.pbo)


def test_flat_returns_degenerate_to_zero() -> None:
    res = _result(np.zeros(500))
    rep = evaluate_xs(
        res,
        ForecastConfig(),
        trial_returns={"combined": np.zeros(500)},
        trend_returns=np.zeros(500),
    )
    assert rep.sharpe_annual == 0.0
    assert rep.corr_to_trend == 0.0
    # sr_d == 0 degenerate branch
    assert rep.dsr == 0.0
    assert math.isinf(rep.min_trl)
