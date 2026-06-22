import numpy as np
import pandas as pd

from analytics.lowvol.signals import (
    causal_betas,
    cross_sectional_score,
    realized_vols,
)


def _toy_closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2020-01-01", periods=400, freq="D")
    rng = np.random.default_rng(3)
    out = {}
    for s in ("A", "B", "C", "D"):
        out[s] = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.01, 400)), index=idx)
    return out


def test_causal_betas_shape_and_causality() -> None:
    closes = _toy_closes()
    b = causal_betas(closes, window=60)
    assert set(b.columns) == set(closes)
    assert np.isnan(b["A"].to_numpy()[:60]).all()  # warm-up + shift NaN

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[200] *= 1.5  # bump a FUTURE bar (middle, live region)
    b2 = causal_betas(closes2, window=60)
    # B's beta vs the EW market: a bump at 200 moves the market return at 200,
    # which only affects rolling windows that include bar 200 (-> shifted 201..260).
    assert b["B"].to_numpy()[150] == b2["B"].to_numpy()[150]  # past unchanged
    assert b["B"].to_numpy()[230] != b2["B"].to_numpy()[230]  # within reach (non-vacuous)


def test_realized_vols_is_causal_and_positive() -> None:
    closes = _toy_closes()
    v = realized_vols(closes, window=60)
    assert set(v.columns) == set(closes)
    finite = v.to_numpy()[np.isfinite(v.to_numpy())]
    assert (finite >= 0.0).all()

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[200] *= 1.5  # future bar
    v2 = realized_vols(closes2, window=60)
    assert v["A"].to_numpy()[150] == v2["A"].to_numpy()[150]  # past unchanged
    assert v["A"].to_numpy()[230] != v2["A"].to_numpy()[230]  # within reach (non-vacuous)


def test_cross_sectional_score_low_metric_is_long() -> None:
    idx = pd.date_range("2020-01-01", periods=1, freq="D")
    metric = pd.DataFrame({"A": [0.5], "B": [1.0], "C": [1.5]}, index=idx)
    score = cross_sectional_score(metric)
    # low metric (A) -> highest (long) score; high metric (C) -> lowest (short)
    assert score["A"].iloc[0] > 0.0
    assert score["C"].iloc[0] < 0.0
    assert abs(float(score.iloc[0].mean())) < 1e-12  # demeaned -> row sums to ~0


from analytics.lowvol.signals import _beta_neutralize


def test_beta_neutralize_zeroes_net_portfolio_beta() -> None:
    idx = pd.date_range("2020-01-01", periods=1, freq="D")
    lev = pd.DataFrame(
        {"A": [1.0], "B": [1.0], "C": [-1.0], "D": [-1.0]}, index=idx
    )  # 2 long, 2 short
    betas = pd.DataFrame(
        {"A": [0.5], "B": [0.7], "C": [1.3], "D": [1.5]}, index=idx
    )
    out = _beta_neutralize(lev, betas)
    net = (out * betas).sum(axis=1)
    assert abs(float(net.iloc[0])) < 1e-12  # beta-neutral by construction
    # long leg untouched, short leg scaled by k = -beta_long/beta_short = 1.2/2.8
    assert out["A"].iloc[0] == 1.0
    assert abs(out["C"].iloc[0] - (-1.0 * 1.2 / 2.8)) < 1e-12


def test_beta_neutralize_leaves_degenerate_short_leg_untouched() -> None:
    idx = pd.date_range("2020-01-01", periods=1, freq="D")
    lev = pd.DataFrame({"A": [1.0], "B": [1.0]}, index=idx)  # no short leg
    betas = pd.DataFrame({"A": [0.5], "B": [0.7]}, index=idx)
    out = _beta_neutralize(lev, betas)
    assert out["A"].iloc[0] == 1.0 and out["B"].iloc[0] == 1.0  # unchanged (k -> 1.0)


from analytics.forecast.config import ForecastConfig
from analytics.lowvol.signals import beta_neutral_leverage


def test_beta_neutral_leverage_shape_neutrality_and_causality() -> None:
    closes = _toy_closes()
    cfg = ForecastConfig()
    betas = causal_betas(closes, window=60)
    score = cross_sectional_score(betas)
    lev = beta_neutral_leverage(score, betas, closes, cfg)
    assert set(lev.columns) == set(closes)

    # net causal portfolio beta ≈ 0 on (essentially) every live day; the median is
    # robust to the rare degenerate day the k>0 guard leaves un-neutralized. The
    # exact math is proven deterministically in test_beta_neutralize_*.
    contrib = (lev * betas).sum(axis=1, min_count=1)
    live = contrib.dropna()
    assert len(live) > 0
    assert float(live.abs().median()) < 1e-6

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[-1] *= 1.5  # bump the future-most bar
    betas2 = causal_betas(closes2, window=60)
    lev2 = beta_neutral_leverage(cross_sectional_score(betas2), betas2, closes2, cfg)
    np.testing.assert_array_equal(lev["B"].to_numpy()[:-1], lev2["B"].to_numpy()[:-1])


from analytics.lowvol.signals import long_only_leverage


def test_long_only_leverage_is_nonnegative_and_causal() -> None:
    closes = _toy_closes()
    cfg = ForecastConfig()
    score = cross_sectional_score(causal_betas(closes, window=60))
    lev = long_only_leverage(score, closes, cfg, quantile=0.5)
    stacked = lev.to_numpy()
    finite = stacked[np.isfinite(stacked)]
    assert (finite >= 0.0).all()  # long-only: no negative legs
    assert finite.any()  # at least some non-zero longs emerge

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[-1] *= 1.5  # future-most bar
    score2 = cross_sectional_score(causal_betas(closes2, window=60))
    lev2 = long_only_leverage(score2, closes2, cfg, quantile=0.5)
    np.testing.assert_array_equal(lev["B"].to_numpy()[:-1], lev2["B"].to_numpy()[:-1])


def test_package_reexports() -> None:
    import analytics.lowvol as lv

    for name in (
        "causal_betas",
        "realized_vols",
        "cross_sectional_score",
        "beta_neutral_leverage",
        "long_only_leverage",
    ):
        assert hasattr(lv, name)
