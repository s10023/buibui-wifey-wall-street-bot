"""Tests for `analytics/research_guards/cluster.py` — the cluster unit.

A block bootstrap resamples runs of observations ADJACENT IN THE ARRAY. Trades
that share a session day are scattered through that array, so no block length
reaches them; only resampling the cluster itself does. These tests pin that the
deflation bites when clustering is present, stays inert when it is not, and
never manufactures more independence than the raw count.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from analytics.research_guards import cluster_bootstrap_ci, cluster_stats


def _mean(a: npt.NDArray[np.float64]) -> float:
    return float(np.mean(a))


class TestClusterStats:
    def test_singleton_clusters_deflate_nothing(self) -> None:
        """Every observation its own cluster: no grouping, so n_eff == n_obs."""
        rng = np.random.default_rng(1)
        vals = rng.normal(0.0, 1.0, 60)
        cs = cluster_stats(vals, list(range(60)))
        assert cs.n_clusters == 60
        assert cs.icc == 0.0
        assert cs.design_effect == 1.0
        assert cs.n_eff == 60.0

    def test_one_cluster_is_one_observation(self) -> None:
        """A single day of trades is ONE draw, however many rows it carries."""
        rng = np.random.default_rng(2)
        vals = rng.normal(0.0, 1.0, 60)
        cs = cluster_stats(vals, [7] * 60)
        assert cs.n_clusters == 1
        assert cs.n_eff == 1.0

    def test_perfectly_correlated_days_collapse_to_the_day_count(self) -> None:
        """Identical values within a day, different across: ICC 1, n_eff ~= k.

        This is the shape the audit found in the wild — same-day trades share
        the day's move — just taken to its limit so the answer is exact.
        """
        vals = np.repeat(np.arange(12, dtype=np.float64), 10)  # 12 days x 10
        keys = [d for d in range(12) for _ in range(10)]
        cs = cluster_stats(vals, keys)
        assert cs.n_clusters == 12
        assert cs.mean_size == 10.0
        assert cs.icc == 1.0
        # DEFF = 1 + (10-1)*1 = 10 → 120/10 = 12, i.e. exactly the day count.
        assert cs.design_effect == 10.0
        assert cs.n_eff == 12.0

    def test_negative_sample_icc_cannot_manufacture_independence(self) -> None:
        """⚠ The deflator only ever SHRINKS.

        Anti-correlated clusters give a negative sample ICC, which would push
        DEFF below 1 and invent MORE effective observations than trades. The
        failure this guard exists to prevent is a confident number, so the
        clamp is load-bearing rather than cosmetic.
        """
        vals = np.array([1.0, -1.0] * 20, dtype=np.float64)
        keys = [i // 2 for i in range(40)]  # each pair is one cluster
        cs = cluster_stats(vals, keys)
        assert cs.icc == 0.0
        assert cs.design_effect == 1.0
        assert cs.n_eff <= 40.0

    def test_no_dispersion_is_not_a_crash(self) -> None:
        cs = cluster_stats(np.zeros(30), [i // 3 for i in range(30)])
        assert cs.design_effect >= 1.0
        assert 1.0 <= cs.n_eff <= 30.0


class TestClusterBootstrapCI:
    def test_clustering_widens_the_interval(self) -> None:
        """The whole point, with the positive control stated.

        Same 120 values both times — only the KEY changes — so any width
        difference is the resampling unit and nothing else.
        """
        rng = np.random.default_rng(11)
        day_effect = rng.normal(0.0, 1.0, 12)
        vals = np.repeat(day_effect, 10) + rng.normal(0.0, 0.1, 120)

        unclustered = cluster_bootstrap_ci(
            vals, list(range(120)), _mean, n_boot=800, seed=5
        )
        clustered = cluster_bootstrap_ci(
            vals, [d for d in range(12) for _ in range(10)], _mean, n_boot=800, seed=5
        )

        # Positive control: both intervals are real, finite and non-degenerate.
        assert unclustered.n_valid == 800 and clustered.n_valid == 800
        assert unclustered.hi > unclustered.lo
        assert clustered.hi > clustered.lo

        assert (clustered.hi - clustered.lo) > (unclustered.hi - unclustered.lo)

    def test_point_estimate_is_the_full_sample_statistic(self) -> None:
        """Resampling must not move the point — only the uncertainty around it."""
        vals = np.arange(40, dtype=np.float64)
        ci = cluster_bootstrap_ci(vals, [i // 4 for i in range(40)], _mean, n_boot=200)
        assert ci.point == float(np.mean(vals))

    def test_fewer_than_two_clusters_refuses_to_invent_a_width(self) -> None:
        """One cluster cannot be resampled ACROSS, so the CI is uninformative.

        Returning a narrow interval built from a single day would be the
        fail-open answer — confident and wrong — so the bounds go infinite and
        the verdict layer reads that as "does not clear the bar".
        """
        vals = np.arange(30, dtype=np.float64)
        ci = cluster_bootstrap_ci(vals, [1] * 30, _mean, n_boot=100)
        assert ci.n_valid == 0
        assert ci.lo == float("-inf")
        assert ci.hi == float("inf")

    def test_seeded_runs_reproduce(self) -> None:
        vals = np.random.default_rng(3).normal(0.0, 1.0, 60)
        keys = [i // 5 for i in range(60)]
        a = cluster_bootstrap_ci(vals, keys, _mean, n_boot=300, seed=99)
        b = cluster_bootstrap_ci(vals, keys, _mean, n_boot=300, seed=99)
        assert (a.lo, a.hi) == (b.lo, b.hi)
