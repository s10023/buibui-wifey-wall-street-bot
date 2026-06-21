import numpy as np
import pandas as pd

from analytics.xsmom.residual import (
    residual_close,
    residual_closes,
    residual_returns,
    rolling_beta,
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
