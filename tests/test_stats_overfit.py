"""Tests for analytics/backtest/stats_overfit.py."""

import math

import numpy as np

from analytics.backtest.stats_overfit import (
    PBOResult,
    _moments,
    build_performance_matrix,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)

_RNG = np.random.default_rng(0)


def test_sharpe_zero_mean_is_zero() -> None:
    assert sharpe_ratio([1.0, -1.0, 1.0, -1.0]) == 0.0


def test_sharpe_positive_for_positive_edge() -> None:
    sr = sharpe_ratio([2.0, 1.0, 2.0, 1.0])
    assert sr > 0.0


def test_sharpe_degenerate_returns_zero() -> None:
    assert sharpe_ratio([]) == 0.0
    assert sharpe_ratio([1.0]) == 0.0  # n < 2
    assert sharpe_ratio([3.0, 3.0, 3.0]) == 0.0  # zero variance


def test_sharpe_known_value() -> None:
    # mean=1.0, sample std (ddof=1) of [3,-1,3,-1] = sqrt(16/3) ≈ 2.3094
    sr = sharpe_ratio([3.0, -1.0, 3.0, -1.0])
    assert math.isclose(sr, 1.0 / math.sqrt(16.0 / 3.0), rel_tol=1e-9)


def test_moments_symmetric_zero_skew() -> None:
    mean, std, skew, kurt = _moments([3.0, -1.0, 3.0, -1.0])
    assert math.isclose(mean, 1.0, rel_tol=1e-9)
    assert std > 0.0
    assert math.isclose(skew, 0.0, abs_tol=1e-9)  # symmetric
    assert kurt > 0.0  # non-excess kurtosis (normal == 3)


def test_psr_in_unit_interval_and_monotonic() -> None:
    lo = probabilistic_sharpe_ratio(0.2, n=100, skew=0.0, kurt=3.0)
    hi = probabilistic_sharpe_ratio(0.5, n=100, skew=0.0, kurt=3.0)
    assert 0.0 <= lo <= 1.0
    assert hi > lo  # larger observed Sharpe ⇒ higher confidence


def test_psr_half_at_benchmark() -> None:
    # observed Sharpe exactly equals the benchmark ⇒ PSR == 0.5
    p = probabilistic_sharpe_ratio(0.3, n=100, skew=0.0, kurt=3.0, sr_star=0.3)
    assert abs(p - 0.5) < 1e-9


def test_psr_degenerate_returns_zero() -> None:
    assert probabilistic_sharpe_ratio(0.3, n=1, skew=0.0, kurt=3.0) == 0.0


def test_expected_max_sharpe_grows_with_trials() -> None:
    a = expected_max_sharpe(0.04, n_trials=5)
    b = expected_max_sharpe(0.04, n_trials=500)
    assert 0.0 < a < b  # more trials ⇒ higher expected best-of-noise Sharpe


def test_expected_max_sharpe_degenerate() -> None:
    assert expected_max_sharpe(0.04, n_trials=1) == 0.0
    assert expected_max_sharpe(0.0, n_trials=100) == 0.0


def test_dsr_deflates_below_psr_with_many_trials() -> None:
    # a Sharpe that looks good standalone gets deflated once you account for
    # having searched many noisy trials with high cross-trial dispersion
    trials = [0.0, 0.05, -0.04, 0.5, 0.1, -0.1, 0.2, -0.2, 0.3, -0.3]
    psr0 = probabilistic_sharpe_ratio(0.5, n=60, skew=0.0, kurt=3.0)
    dsr = deflated_sharpe_ratio(0.5, trials, n_returns=60, skew=0.0, kurt=3.0)
    assert dsr < psr0
    assert 0.0 <= dsr <= 1.0


def test_dsr_single_trial_equals_psr0() -> None:
    dsr = deflated_sharpe_ratio(0.4, [0.4], n_returns=50, skew=0.0, kurt=3.0)
    psr0 = probabilistic_sharpe_ratio(0.4, n=50, skew=0.0, kurt=3.0)
    assert abs(dsr - psr0) < 1e-9


def test_pbo_dominant_config_is_low() -> None:
    # config 0 is consistently best in every row ⇒ never overfit ⇒ PBO ≈ 0
    noise = _RNG.normal(0, 0.1, size=(200, 5))
    noise[:, 0] += 1.0
    res = probability_of_backtest_overfitting(noise, n_splits=10)
    assert isinstance(res, PBOResult)
    assert res.pbo < 0.1


def test_pbo_adversarial_is_high() -> None:
    # IS-best column is engineered to be OOS-worst on every split ⇒ PBO ≈ 1
    t = 200
    half = t // 2
    m = _RNG.normal(0, 0.05, size=(t, 4))
    m[:half, 0] += 1.0  # column 0 dominates the first half (IS on many combos)
    m[half:, 0] -= 1.0  # ...and is worst in the second half (OOS)
    res = probability_of_backtest_overfitting(m, n_splits=8)
    assert res.pbo > 0.5


def test_pbo_pure_noise_near_half() -> None:
    # no real edge ⇒ OOS rank of the IS-best is a coin flip ⇒ PBO ≈ 0.5 in
    # expectation. A single matrix is noisy at this size, so average several.
    rng = np.random.default_rng(0)
    vals = [
        probability_of_backtest_overfitting(
            rng.normal(0, 1.0, size=(300, 8)), n_splits=10
        ).pbo
        for _ in range(8)
    ]
    assert 0.35 < sum(vals) / len(vals) < 0.65


def test_pbo_degenerate_is_nan() -> None:
    one_col = _RNG.normal(0, 1.0, size=(100, 1))
    assert math.isnan(probability_of_backtest_overfitting(one_col).pbo)
    tiny = _RNG.normal(0, 1.0, size=(1, 5))  # fewer rows than splits can halve
    assert math.isnan(probability_of_backtest_overfitting(tiny, n_splits=16).pbo)


def test_build_performance_matrix_buckets_by_time() -> None:
    # 2 buckets over span [0, 100): bucket0 = [0, 50), bucket1 = [50, 100].
    cfg_a = [(0, 1.0), (100, 2.0)]  # t=100 clamps into the last bucket
    cfg_b = [(0, -1.0), (10, 0.5)]  # both land in bucket0 ⇒ summed
    m = build_performance_matrix([cfg_a, cfg_b], n_rows=2)
    assert m.shape == (2, 2)
    assert m[0, 0] == 1.0 and m[1, 0] == 2.0  # cfg_a: bucket0=1.0, bucket1=2.0
    assert m[0, 1] == -0.5  # cfg_b: -1.0 and 0.5 both land in bucket0
    assert m[1, 1] == 0.0


def test_build_performance_matrix_empty() -> None:
    assert build_performance_matrix([], n_rows=4).shape == (0, 0)
    assert build_performance_matrix([[], []], n_rows=4).shape == (0, 2)
