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
