import numpy as np
import pandas as pd

from analytics.forecast.book import instrument_returns
from analytics.forecast.config import ForecastConfig


def _close(n: int = 400, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    steps = rng.normal(0.0005, 0.01, size=n)
    return pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)


def test_zero_funding_means_zero_funding_cost() -> None:
    close = _close()
    zero = pd.Series(0.0, index=close.index)
    out = instrument_returns(close, zero, ForecastConfig())
    assert (out["funding_cost"].fillna(0.0) == 0.0).all()
    # net == gross - turnover_cost when funding is zero
    recon = out["gross"] - out["turnover_cost"]
    assert np.allclose(out["net"].fillna(0.0), recon.fillna(0.0))


def test_position_is_causal_no_lookahead() -> None:
    # perturbing the LAST close must not change any earlier leverage value
    close = _close()
    cfg = ForecastConfig()
    base = instrument_returns(close, pd.Series(0.0, index=close.index), cfg)
    bumped = close.copy()
    bumped.iloc[-1] *= 1.10
    after = instrument_returns(bumped, pd.Series(0.0, index=bumped.index), cfg)
    assert np.allclose(
        base["leverage"].iloc[:-1].fillna(0.0),
        after["leverage"].iloc[:-1].fillna(0.0),
    )
