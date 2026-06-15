"""Tests for analytics/research_guards/psr.py."""

import pytest

from analytics.research_guards.psr import probabilistic_sharpe_ratio


class TestProbabilisticSharpeRatio:
    def test_zero_sharpe_returns_half(self) -> None:
        assert probabilistic_sharpe_ratio(0.0, 100) == pytest.approx(0.5)

    def test_worked_example(self) -> None:
        # sr=0.5, T=24, normal moments, zero benchmark -> ~0.9881 (hand-computed)
        psr = probabilistic_sharpe_ratio(0.5, 24, skew=0.0, kurtosis=3.0)
        assert psr == pytest.approx(0.9881, abs=5e-4)

    def test_negative_skew_lowers_psr(self) -> None:
        base = probabilistic_sharpe_ratio(0.5, 100)
        skewed = probabilistic_sharpe_ratio(0.5, 100, skew=-0.5)
        assert skewed < base

    def test_higher_kurtosis_lowers_psr(self) -> None:
        base = probabilistic_sharpe_ratio(0.5, 100, kurtosis=3.0)
        fat = probabilistic_sharpe_ratio(0.5, 100, kurtosis=6.0)
        assert fat < base

    def test_more_observations_raises_psr(self) -> None:
        few = probabilistic_sharpe_ratio(0.5, 30)
        many = probabilistic_sharpe_ratio(0.5, 300)
        assert many > few

    def test_result_in_unit_interval(self) -> None:
        for sr in (-1.0, 0.0, 0.5, 2.0):
            p = probabilistic_sharpe_ratio(sr, 50)
            assert 0.0 <= p <= 1.0

    def test_too_few_observations_raises(self) -> None:
        with pytest.raises(ValueError):
            probabilistic_sharpe_ratio(0.5, 1)

    def test_degenerate_moments_raise(self) -> None:
        # skew=5, sr=2 forces the variance term negative
        with pytest.raises(ValueError):
            probabilistic_sharpe_ratio(2.0, 50, skew=5.0, kurtosis=3.0)
