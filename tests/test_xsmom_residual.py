import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import run_xs_backtest, xs_leverage
from analytics.xsmom.residual import (
    residual_close,
    residual_closes,
    residual_returns,
    rolling_beta,
    sector_neutral_demean,
)


def test_rolling_beta_recovers_known_beta() -> None:
    idx = pd.date_range("2020-01-01", periods=40, freq="D")
    rng = np.random.default_rng(0)
    mkt = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    inst = 1.5 * mkt + pd.Series(rng.normal(0, 1e-6, 40), index=idx)
    beta = rolling_beta(inst, mkt, window=20)
    assert np.isnan(beta.iloc[18])  # warm-up: < window obs
    assert abs(beta.iloc[-1] - 1.5) < 0.01


def test_rolling_beta_is_causal() -> None:
    idx = pd.date_range("2020-01-01", periods=40, freq="D")
    rng = np.random.default_rng(1)
    mkt = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    inst = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    base = rolling_beta(inst, mkt, window=20)
    inst2 = inst.copy()
    inst2.iloc[30] *= 5.0  # bump a FUTURE bar
    pert = rolling_beta(inst2, mkt, window=20)
    assert base.iloc[25] == pert.iloc[25]  # past beta unchanged
    assert base.iloc[30] != pert.iloc[30]  # bumped bar changed (non-vacuous)


def test_residual_returns_strips_market_component() -> None:
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    inst = pd.Series([0.02, 0.03, -0.01, 0.00, 0.05], index=idx)
    mkt = pd.Series([0.01, 0.01, -0.01, 0.00, 0.02], index=idx)
    beta = pd.Series([2.0, 2.0, 2.0, 2.0, 2.0], index=idx)
    resid = residual_returns(inst, mkt, beta)
    # r_i - beta * r_mkt
    assert abs(resid.iloc[0] - (0.02 - 2.0 * 0.01)) < 1e-12
    assert abs(resid.iloc[4] - (0.05 - 2.0 * 0.02)) < 1e-12


def test_residual_close_builds_price_and_is_causal() -> None:
    idx = pd.date_range("2020-01-01", periods=60, freq="D")
    close = pd.Series(100.0 + np.arange(60), index=idx)
    mkt = pd.Series(np.linspace(0.001, 0.002, 60), index=idx)
    base = residual_close(close, mkt, window=20)
    assert base.notna().sum() > 0  # a usable price series emerges post warm-up
    close2 = close.copy()
    close2.iloc[50] *= 1.5  # bump a FUTURE bar
    pert = residual_close(close2, mkt, window=20)
    assert base.iloc[40] == pert.iloc[40]  # past price unchanged (causal)
    assert base.iloc[50] != pert.iloc[50]  # bumped bar changed (non-vacuous)


def test_residual_closes_covers_all_symbols() -> None:
    idx = pd.date_range("2020-01-01", periods=60, freq="D")
    closes = {
        "A": pd.Series(100.0 + np.arange(60), index=idx),
        "B": pd.Series(50.0 + 2 * np.arange(60), index=idx),
    }
    out = residual_closes(closes, window=20)
    assert set(out) == {"A", "B"}


def test_sector_neutral_demean_zeroes_within_sector() -> None:
    idx = pd.date_range("2020-01-01", periods=2, freq="D")
    forecasts = pd.DataFrame(
        {"A": [1.0, 2.0], "B": [3.0, 4.0], "C": [10.0, 10.0]}, index=idx
    )
    sector_map = {"A": "Tech", "B": "Tech", "C": "Energy"}
    out = sector_neutral_demean(forecasts, sector_map)
    # within Tech each row sums to ~0; single-name Energy sector -> 0
    assert abs(out.loc[idx[0], "A"] + out.loc[idx[0], "B"]) < 1e-12
    assert abs(out.loc[idx[0], "A"] - (-1.0)) < 1e-12  # 1 - mean(1,3) = -1
    assert abs(out.loc[idx[0], "C"]) < 1e-12


def _toy_closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2020-01-01", periods=400, freq="D")
    rng = np.random.default_rng(3)
    out = {}
    for s in ("A", "B", "C", "D"):
        out[s] = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.01, 400)), index=idx)
    return out


def test_injected_default_leverage_is_byte_identical() -> None:
    closes = _toy_closes()
    fundings: dict[str, pd.Series] = {}
    cfg = ForecastConfig()
    base = run_xs_backtest(closes, fundings, cfg)
    lev = xs_leverage(closes, cfg)
    injected = run_xs_backtest(closes, fundings, cfg, leverage=lev)
    np.testing.assert_array_equal(base.portfolio_return, injected.portfolio_return)
    np.testing.assert_array_equal(base.governor, injected.governor)
