"""Tests for analytics/research_guards/bootstrap.py."""

import numpy as np
import numpy.typing as npt
import pytest

from analytics.research_guards.bootstrap import block_bootstrap_ci


def _mean(x: npt.NDArray[np.float64]) -> float:
    return float(np.mean(x))


class TestBlockBootstrapCI:
    def test_brackets_true_mean_iid(self) -> None:
        rng = np.random.default_rng(0)
        trials = 200
        covered = 0
        for s in range(trials):
            data = rng.normal(0.0, 1.0, size=200)
            ci = block_bootstrap_ci(data, _mean, n_boot=500, block=1, alpha=0.1, seed=s)
            if ci.lo <= 0.0 <= ci.hi:
                covered += 1
        # ~90% nominal coverage; allow slack for finite trials / n_boot
        assert covered / trials >= 0.80

    def test_width_shrinks_with_more_data(self) -> None:
        rng = np.random.default_rng(1)
        small = rng.normal(0.0, 1.0, size=50)
        large = rng.normal(0.0, 1.0, size=800)
        ci_small = block_bootstrap_ci(small, _mean, n_boot=800, seed=2)
        ci_large = block_bootstrap_ci(large, _mean, n_boot=800, seed=2)
        assert (ci_large.hi - ci_large.lo) < (ci_small.hi - ci_small.lo)

    def test_seeded_determinism(self) -> None:
        rng = np.random.default_rng(3)
        data = rng.normal(0.0, 1.0, size=120)
        a = block_bootstrap_ci(data, _mean, n_boot=500, seed=42)
        b = block_bootstrap_ci(data, _mean, n_boot=500, seed=42)
        assert (a.lo, a.hi, a.point, a.n_valid) == (b.lo, b.hi, b.point, b.n_valid)

    def test_stationary_wider_than_iid_on_ar1(self) -> None:
        rng = np.random.default_rng(4)
        n = 600
        eps = rng.normal(0.0, 1.0, size=n)
        ar = np.empty(n)
        ar[0] = eps[0]
        phi = 0.8
        for t in range(1, n):
            ar[t] = phi * ar[t - 1] + eps[t]
        block = max(2, round(n ** (1 / 3)))
        wide = block_bootstrap_ci(
            ar, _mean, n_boot=1000, block=block, method="stationary", seed=7
        )
        iid = block_bootstrap_ci(
            ar, _mean, n_boot=1000, block=1, method="stationary", seed=7
        )
        assert (wide.hi - wide.lo) > (iid.hi - iid.lo)

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError):
            block_bootstrap_ci(np.array([1.0]), _mean)
