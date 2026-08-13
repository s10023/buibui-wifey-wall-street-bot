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
    # Perturb a MIDDLE bar: leverage at index k is sized from info <= k-1, so
    # close[k] must not affect leverage[:k+1].
    #
    # This bumped the LAST bar until 2026-08-13, which made the test VACUOUS:
    # with the last bar bumped there is no k+1 to observe, so the assertion could
    # only ever say "earlier values are unchanged" — and that is equally true when
    # the causal shift is absent. Measured: deleting `forecast = forecast.shift(1)`
    # in analytics/forecast/book.py left the old assertion PASSING.
    close = _close()
    cfg = ForecastConfig()
    funding = pd.Series(0.0, index=close.index)
    base = instrument_returns(close, funding, cfg)

    k = len(close) // 2
    bumped = close.copy()
    bumped.iloc[k] *= 1.5
    after = instrument_returns(bumped, funding, cfg)

    assert np.allclose(
        base["leverage"].iloc[: k + 1].fillna(0.0),
        after["leverage"].iloc[: k + 1].fillna(0.0),
    )

    # Positive control. The assertion above is a "did NOT change" claim, which is
    # satisfied both by "the invariant holds" and by "the bump never reached the
    # sizing" — so assert the stimulus is live. Measured on THIS fixture (a seeded
    # random walk, not the parent's ramp): leverage[k+1] moves 0.1974 -> 0.1414,
    # delta ~5.6e-02, and neither value is near a cap (leverage spans -2.02..2.37).
    # NaN would satisfy a bare `!=`, so require finite first.
    lev_base = float(base["leverage"].iloc[k + 1])
    lev_after = float(after["leverage"].iloc[k + 1])
    assert np.isfinite(lev_base) and np.isfinite(lev_after), (
        "leverage[k+1] must be warmed up for the control to mean anything"
    )
    assert abs(lev_after - lev_base) > 1e-9, (
        "perturbation never propagated to leverage[k+1] — the causality "
        "assertion above is vacuous and would pass with the causal shift removed"
    )


def test_long_only_false_is_byte_identical_to_default() -> None:
    close = _close()
    zero = pd.Series(0.0, index=close.index)
    cfg = ForecastConfig()
    default = instrument_returns(close, zero, cfg)
    explicit = instrument_returns(close, zero, cfg, long_only=False)
    assert default.equals(explicit)


def test_long_only_true_never_shorts() -> None:
    close = _close()
    zero = pd.Series(0.0, index=close.index)
    out = instrument_returns(close, zero, ForecastConfig(), long_only=True)
    lev = out["leverage"].dropna()
    assert (lev >= 0.0).all()
