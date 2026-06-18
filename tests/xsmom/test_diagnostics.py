from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest


def test_equal_weight_market_return_active_set_mean() -> None:
    from analytics.xsmom.diagnostics import equal_weight_market_return

    idx = pd.date_range("2021-01-01", periods=4, freq="D")
    closes = {
        "A": pd.Series([100.0, 110.0, 121.0, 133.1], index=idx),  # +10%/day
        "B": pd.Series([100.0, 100.0, 100.0, 100.0], index=idx),  # 0%/day
    }
    mkt = equal_weight_market_return(closes)
    # day 0 is NaN (pct_change); day 1 = mean(+0.10, 0.0) = 0.05
    assert math.isnan(mkt.iloc[0])
    assert mkt.iloc[1] == pytest.approx(0.05)


def test_equal_weight_market_return_skips_absent_instrument() -> None:
    from analytics.xsmom.diagnostics import equal_weight_market_return

    idx_full = pd.date_range("2021-01-01", periods=4, freq="D")
    idx_late = pd.date_range("2021-01-03", periods=2, freq="D")
    closes = {
        "A": pd.Series([100.0, 110.0, 121.0, 133.1], index=idx_full),  # +10%/day
        "C": pd.Series([100.0, 200.0], index=idx_late),  # present only days 2-3
    }
    mkt = equal_weight_market_return(closes)
    # day 1: only A present -> 0.10; day 3: A +10% and C +100% -> mean 0.55
    assert mkt.loc[idx_full[1]] == pytest.approx(0.10)
    assert mkt.loc[idx_full[3]] == pytest.approx(0.55)


def test_beta_attribution_recovers_known_alpha_beta() -> None:
    from analytics.xsmom.diagnostics import beta_attribution

    rng = np.random.default_rng(0)
    n = 2000
    mkt = rng.normal(0.0, 0.02, n)
    noise = rng.normal(0.0, 0.001, n)
    port = 0.0003 + 1.4 * mkt + noise
    ba = beta_attribution(port, mkt, ann_days=365.0)
    assert abs(ba.beta - 1.4) < 0.05
    assert abs(ba.alpha_annual - 0.0003 * 365.0) < 0.02
    assert ba.r_squared > 0.95
    assert ba.alpha_tstat > 10.0  # true t-stat ~13.3 at SNR 20:1, n=2000
    # hedged stream = alpha + residual: positive mean, small vol -> high Sharpe
    assert ba.beta_hedged_sharpe > 1.0


def test_beta_attribution_degenerate_market_is_safe() -> None:
    from analytics.xsmom.diagnostics import beta_attribution

    port = np.array([0.01, -0.02, 0.03, 0.0, 0.015])
    mkt = np.zeros(5)  # zero-variance market
    ba = beta_attribution(port, mkt, ann_days=365.0)
    assert ba.beta == 0.0
    assert ba.r_squared == 0.0
    # hedged == port when there is no market factor to remove
    assert abs(ba.alpha_annual - float(np.mean(port)) * 365.0) < 1e-9


def test_subperiod_sharpe_by_year_and_trailing() -> None:
    from analytics.xsmom.diagnostics import subperiod_sharpe

    idx = pd.date_range("2021-01-01", "2022-12-31", freq="D")
    rng = np.random.default_rng(1)
    # 2021 strongly positive drift, 2022 flat-ish — distinct per-year Sharpe.
    r = np.where(
        idx.year.to_numpy() == 2021,
        rng.normal(0.002, 0.01, len(idx)),
        rng.normal(0.0, 0.01, len(idx)),
    )
    rep = subperiod_sharpe(r, idx, ann_days=365.0)
    assert set(rep.by_year.keys()) == {2021, 2022}
    assert rep.by_year[2021] > rep.by_year[2022]
    assert rep.n_obs == len(idx)
    # trailing 1y window pulls only from 2022 -> close to the 2022 figure
    assert rep.trailing_1y < rep.by_year[2021]
    # trailing 2y covers both years -> Sharpe strictly between the per-year values
    assert rep.by_year[2022] < rep.trailing_2y < rep.by_year[2021]


def test_subperiod_sharpe_degenerate_slices_are_zero_not_nan() -> None:
    from analytics.xsmom.diagnostics import subperiod_sharpe

    idx = pd.date_range("2021-01-01", periods=1, freq="D")
    rep = subperiod_sharpe(np.array([0.01]), idx, ann_days=365.0)
    assert rep.by_year[2021] == 0.0  # single obs -> 0.0, not NaN
    assert rep.trailing_1y == 0.0
    assert rep.trailing_2y == 0.0
