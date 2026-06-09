"""Tests for analytics/backtest/stats_overfit.py."""

import math

from analytics.backtest.stats_overfit import _moments, sharpe_ratio


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
