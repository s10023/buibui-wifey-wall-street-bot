"""Survival metrics: drawdown, ulcer index, time under water, breach curve."""

from __future__ import annotations

import math

import numpy as np
import pytest

from analytics.research_guards.survival import (
    _stationary_paths,
    drawdown_breach_curve,
    drawdown_series,
    max_drawdown,
    time_under_water,
    ulcer_index,
)


def _r(*xs: float) -> np.ndarray:
    return np.array(xs, dtype=np.float64)


class TestDrawdown:
    def test_starting_wealth_counts_as_a_peak(self) -> None:
        # A first-day loss is under water on day one.
        dd = drawdown_series(_r(-0.10, 0.0))
        assert dd.tolist() == pytest.approx([-0.10, -0.10])

    def test_recovery_returns_to_zero(self) -> None:
        dd = drawdown_series(_r(0.10, -0.50, 1.0))
        assert dd.tolist() == pytest.approx([0.0, -0.50, 0.0])

    def test_max_drawdown(self) -> None:
        assert max_drawdown(_r(0.10, -0.20, -0.25, 0.5)) == pytest.approx(
            0.8 * 0.75 - 1.0
        )


class TestUlcerIndex:
    def test_hand_computed_in_percent(self) -> None:
        # Drawdowns 0, -10%, 0 → sqrt((0 + 100 + 0) / 3).
        r = _r(0.0, -0.10, 1.0 / 0.9 - 1.0)
        assert ulcer_index(r) == pytest.approx(math.sqrt(100.0 / 3.0))

    def test_never_under_water_is_zero(self) -> None:
        assert ulcer_index(_r(0.01, 0.02, 0.0)) == 0.0

    def test_longer_drawdown_of_same_depth_scores_higher(self) -> None:
        short = _r(-0.2, 0.25, 0.0, 0.0, 0.0)
        long_ = _r(-0.2, 0.0, 0.0, 0.0, 0.25)
        assert ulcer_index(long_) > ulcer_index(short)

    def test_empty_is_nan(self) -> None:
        assert math.isnan(ulcer_index(_r()))


class TestTimeUnderWater:
    def test_longest_spell(self) -> None:
        r = _r(-0.1, 0.0, 0.2, -0.05, 0.0, 0.0, 0.0)
        assert time_under_water(r) == 4

    def test_zero_when_always_at_a_peak(self) -> None:
        assert time_under_water(_r(0.01, 0.01)) == 0


class TestStationaryPaths:
    def test_indices_in_range_and_shape(self) -> None:
        rng = np.random.default_rng(1)
        idx = _stationary_paths(100, 50, 7, 10, rng)
        assert idx.shape == (7, 50)
        assert idx.min() >= 0 and idx.max() < 100

    def test_mean_block_length_matches(self) -> None:
        rng = np.random.default_rng(2)
        idx = _stationary_paths(10_000, 5_000, 4, 20, rng)
        # A block continues while the next index is the previous + 1 (mod n).
        cont = (np.diff(idx, axis=1) % 10_000) == 1
        assert 1.0 / (1.0 - cont.mean()) == pytest.approx(20.0, rel=0.1)


class TestBreachCurve:
    def test_monotone_in_depth_and_bounded(self) -> None:
        rng = np.random.default_rng(3)
        r = rng.normal(0.0003, 0.012, 3_000)
        curve = drawdown_breach_curve(
            r, [0.05, 0.15, 0.30], horizon=500, n_paths=600, block=50, seed=7
        )
        vals = list(curve.values())
        assert all(0.0 <= v <= 1.0 for v in vals)
        assert vals == sorted(vals, reverse=True)
        assert vals[0] > vals[-1]

    def test_extremes(self) -> None:
        up = np.full(300, 0.001)
        down = np.full(300, -0.01)
        assert drawdown_breach_curve(up, [0.01], horizon=100, n_paths=50, seed=1) == {
            0.01: 0.0
        }
        assert drawdown_breach_curve(down, [0.5], horizon=100, n_paths=50, seed=1) == {
            0.5: 1.0
        }

    def test_same_seed_pairs_two_arms(self) -> None:
        # Equal length and seed → identical paths, so a half-scaled arm can
        # never breach a depth the full arm did not.
        rng = np.random.default_rng(4)
        r = rng.normal(0.0, 0.02, 2_000)
        full = drawdown_breach_curve(r, [0.2], horizon=400, n_paths=400, seed=9)
        half = drawdown_breach_curve(0.5 * r, [0.2], horizon=400, n_paths=400, seed=9)
        assert half[0.2] <= full[0.2]
        assert full == drawdown_breach_curve(r, [0.2], horizon=400, n_paths=400, seed=9)

    def test_chunking_is_invisible(self) -> None:
        # n_paths spanning several chunks still produces one probability per depth.
        r = np.random.default_rng(5).normal(0.0, 0.01, 800)
        out = drawdown_breach_curve(r, [0.1], horizon=200, n_paths=1_234, seed=3)
        assert set(out) == {0.1}

    def test_rejects_degenerate_input(self) -> None:
        with pytest.raises(ValueError):
            drawdown_breach_curve(_r(0.01), [0.1], horizon=10)
