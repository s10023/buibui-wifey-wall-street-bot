"""Tests for analytics/research_guards/pbo.py."""

import numpy as np
import numpy.typing as npt
import pytest

from analytics.research_guards.pbo import cscv_pbo


def _mean_metric(col: npt.NDArray[np.float64]) -> float:
    return float(np.mean(col))


class TestCscvPbo:
    def test_odd_splits_raise(self) -> None:
        mat = np.random.default_rng(0).normal(size=(100, 3))
        with pytest.raises(ValueError):
            cscv_pbo(mat, n_splits=7)

    def test_too_few_trials_raise(self) -> None:
        with pytest.raises(ValueError):
            cscv_pbo(np.zeros((100, 1)))

    def test_pure_noise_near_half(self) -> None:
        rng = np.random.default_rng(0)
        mat = rng.normal(0.0, 1.0, size=(300, 8))
        res = cscv_pbo(mat, n_splits=10)
        assert 0.3 <= res.pbo <= 0.7

    def test_genuine_edge_near_zero(self) -> None:
        rng = np.random.default_rng(1)
        mat = rng.normal(0.0, 1.0, size=(300, 6))
        mat[:, 0] += 0.6  # column 0 carries a real positive mean edge
        res = cscv_pbo(mat, n_splits=10)
        assert res.pbo <= 0.1

    def test_inverted_near_one(self) -> None:
        # Two anti-symmetric columns: whichever is best in-sample is worst OOS.
        n_splits = 8
        rows_per_block = 4
        c = float(n_splits - 1)
        col0: list[float] = []
        col1: list[float] = []
        for i in range(n_splits):
            col0.extend([float(i)] * rows_per_block)
            col1.extend([c - float(i)] * rows_per_block)
        mat = np.column_stack([np.array(col0), np.array(col1)])
        res = cscv_pbo(mat, n_splits=n_splits, metric=_mean_metric)
        assert res.pbo >= 0.9

    def test_result_fields(self) -> None:
        rng = np.random.default_rng(2)
        mat = rng.normal(size=(200, 5))
        res = cscv_pbo(mat, n_splits=8)
        assert res.n_combinations == 70  # C(8, 4)
        assert len(res.logits) == 70
