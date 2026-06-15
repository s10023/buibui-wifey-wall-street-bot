"""Tests for analytics/research_guards/haircut.py."""

import math
from statistics import NormalDist

import pytest

from analytics.research_guards.haircut import haircut_sharpe


class TestHaircutSharpe:
    def test_single_test_no_haircut(self) -> None:
        res = haircut_sharpe(0.3, 100, n_tests=1)
        assert res.haircut_sharpe == pytest.approx(0.3)
        assert res.haircut_pct == pytest.approx(0.0, abs=1e-9)

    def test_bonferroni_adjustment(self) -> None:
        sr, n_obs, n_tests = 0.3, 100, 20
        res = haircut_sharpe(sr, n_obs, n_tests, method="bonferroni")
        t = sr * math.sqrt(n_obs)
        p = 2.0 * (1.0 - NormalDist().cdf(abs(t)))
        assert res.adjusted_pvalue == pytest.approx(min(1.0, p * n_tests))

    def test_holm_at_most_bonferroni(self) -> None:
        pvals = [0.001, 0.01, 0.02, 0.03, 0.2]
        sr, n_obs = 0.3, 100
        holm = haircut_sharpe(
            sr, n_obs, n_tests=len(pvals), method="holm", pvalues_all=pvals
        )
        bonf = haircut_sharpe(sr, n_obs, n_tests=len(pvals), method="bonferroni")
        assert holm.adjusted_pvalue <= bonf.adjusted_pvalue + 1e-12

    def test_missing_pvalues_falls_back_to_bonferroni(self) -> None:
        res = haircut_sharpe(0.3, 100, n_tests=10, method="holm")
        assert res.fell_back is True

    def test_nonpositive_sharpe_full_haircut(self) -> None:
        res = haircut_sharpe(-0.1, 100, n_tests=5)
        assert res.haircut_sharpe == 0.0
        assert res.haircut_pct == 0.0

    def test_more_tests_reduce_sharpe(self) -> None:
        res = haircut_sharpe(0.3, 100, n_tests=50, method="bonferroni")
        assert 0.0 <= res.haircut_sharpe <= 0.3
        assert res.haircut_pct >= 0.0
