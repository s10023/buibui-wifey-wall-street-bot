"""Tests for analytics/research_guards/dsr.py."""

import pytest

from analytics.research_guards.dsr import deflated_sharpe_ratio, expected_max_sharpe
from analytics.research_guards.psr import probabilistic_sharpe_ratio


class TestExpectedMaxSharpe:
    def test_single_trial_returns_zero(self) -> None:
        assert expected_max_sharpe(1, 0.04) == 0.0

    def test_no_variance_returns_zero(self) -> None:
        assert expected_max_sharpe(50, 0.0) == 0.0

    def test_increases_with_trials(self) -> None:
        assert expected_max_sharpe(100, 0.04) > expected_max_sharpe(10, 0.04)

    def test_increases_with_variance(self) -> None:
        assert expected_max_sharpe(50, 0.09) > expected_max_sharpe(50, 0.01)


class TestDeflatedSharpeRatio:
    def test_requires_exactly_one_source(self) -> None:
        with pytest.raises(ValueError):
            deflated_sharpe_ratio(0.5, 100)  # neither provided
        with pytest.raises(ValueError):
            deflated_sharpe_ratio(0.5, 100, trial_srs=[0.1, 0.2], n_trials=2)  # both

    def test_single_trial_equals_psr(self) -> None:
        dsr = deflated_sharpe_ratio(0.5, 100, n_trials=1, sr_variance=0.04)
        psr = probabilistic_sharpe_ratio(0.5, 100, sr_benchmark=0.0)
        assert dsr == pytest.approx(psr)

    def test_deflation_below_psr_for_many_trials(self) -> None:
        dsr = deflated_sharpe_ratio(0.5, 100, n_trials=200, sr_variance=0.04)
        psr = probabilistic_sharpe_ratio(0.5, 100)
        assert dsr < psr

    def test_more_trials_lower_dsr(self) -> None:
        few = deflated_sharpe_ratio(0.5, 100, n_trials=10, sr_variance=0.04)
        many = deflated_sharpe_ratio(0.5, 100, n_trials=500, sr_variance=0.04)
        assert many < few

    def test_trial_srs_path_in_unit_interval(self) -> None:
        srs = [0.1, 0.2, 0.3, 0.15, 0.25, 0.05]
        dsr = deflated_sharpe_ratio(0.6, 120, trial_srs=srs)
        assert 0.0 <= dsr <= 1.0
