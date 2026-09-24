"""Cluster-aware CI and design-effect deflation for grouped observations.

A block bootstrap absorbs **serial** dependence — it resamples runs of
observations that are adjacent *in the array it is handed*. It cannot absorb
**cross-sectional** dependence, where the correlated observations are not
adjacent at all: twenty symbols firing on the same session day are twenty rows
scattered through the array, and no block length reaches them.

On this repo's panel the deflation is not cosmetic — day-clustered CIs run
roughly twice as wide as the block-bootstrap ones they replaced, and the
analytic (``√DEFF``) and resampling routes agree closely
(``docs/audits/2026-08-20-audit-guard-cross-sectional-clustering.md``), which
is why both are implemented here rather than one standing in for the other.

Two functions, because a verdict needs both legs and they fail differently:

* :func:`cluster_bootstrap_ci` resamples whole clusters, so the CI widens to
  match the real information content.
* :func:`cluster_stats` returns the design effect and ``n_eff = n_obs / DEFF``
  for the significance leg. That leg carries the larger risk of the two: an
  undeflated ``t = sr·√n_trades`` overstates significance on a clustered panel.

The deflator can only shrink: ``icc`` is clamped to ``[0, 1]`` and
``design_effect`` to ``>= 1``, so a negative sample ICC — routine on small
panels — never manufactures *more* independence than the raw count. The failure
this guards against is a confident number, so the safe direction is fewer
effective observations, never more.

Pure math (numpy only), no DB / IO.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from analytics.research_guards.bootstrap import BootstrapCI


@dataclass(frozen=True)
class ClusterStats:
    """Design-effect deflation for one grouped sample.

    ``n_eff`` is the count to hand a t-statistic, never ``n_obs``. It is
    clamped to ``[1, n_obs]``: a single cluster carries one observation's worth
    of information no matter how many rows it holds, and perfectly uncorrelated
    clusters carry exactly the raw count and no more.
    """

    n_obs: int
    n_clusters: int
    mean_size: float
    icc: float
    design_effect: float
    n_eff: float


def _group_indices(
    cluster_key: Sequence[Hashable],
) -> list[npt.NDArray[np.int64]]:
    """Row indices per distinct key, order-stable so a seeded run reproduces."""
    order: dict[Hashable, list[int]] = {}
    for i, k in enumerate(cluster_key):
        order.setdefault(k, []).append(i)
    return [np.asarray(v, dtype=np.int64) for v in order.values()]


def cluster_stats(
    values: npt.NDArray[np.float64],
    cluster_key: Sequence[Hashable],
) -> ClusterStats:
    """One-way random-effects ICC and the design effect it implies.

    ``ICC = (MSB - MSW) / (MSB + (m0 - 1)·MSW)`` with the unbalanced-design
    adjusted cluster size ``m0 = (N - Σnᵢ²/N) / (k - 1)``; the design effect is
    then ``1 + (m̄ - 1)·ICC`` on the *plain* mean cluster size, matching the
    figure the audit reports.

    Three degenerate shapes, all resolved toward less information:

    * **one cluster** — every observation is in it, so ``n_eff`` is 1. This is
      the honest answer, not a guard: a single day of trades is one draw.
    * **every observation its own cluster** — ``MSW`` has no degrees of freedom,
      so ICC is 0 and ``n_eff == n_obs``. Nothing is clustered, so nothing is
      deflated.
    * **no dispersion** — the ICC denominator vanishes and ICC is 0.
    """
    n_obs = int(values.shape[0])
    groups = _group_indices(cluster_key)
    k = len(groups)
    if n_obs == 0 or k == 0:
        return ClusterStats(n_obs, k, 0.0, 0.0, 1.0, 0.0)

    sizes = np.asarray([g.shape[0] for g in groups], dtype=np.float64)
    mean_size = float(sizes.mean())

    if k == 1:
        # Total clustering: one draw, however many rows it carries.
        return ClusterStats(n_obs, 1, mean_size, 1.0, float(n_obs), 1.0)
    if k == n_obs:
        # Singleton clusters: MSW has no degrees of freedom and nothing is grouped.
        return ClusterStats(n_obs, k, mean_size, 0.0, 1.0, float(n_obs))

    grand = float(values.mean())
    group_means = np.asarray([float(values[g].mean()) for g in groups])
    ss_between = float((sizes * (group_means - grand) ** 2).sum())
    ss_within = float(
        sum(float(((values[g] - values[g].mean()) ** 2).sum()) for g in groups)
    )
    ms_between = ss_between / (k - 1)
    ms_within = ss_within / (n_obs - k)

    m0 = (n_obs - float((sizes**2).sum()) / n_obs) / (k - 1)
    denom = ms_between + (m0 - 1.0) * ms_within
    icc = 0.0 if denom <= 0.0 else (ms_between - ms_within) / denom
    icc = float(min(1.0, max(0.0, icc)))

    design_effect = max(1.0, 1.0 + (mean_size - 1.0) * icc)
    n_eff = float(min(float(n_obs), max(1.0, n_obs / design_effect)))
    return ClusterStats(n_obs, k, mean_size, icc, design_effect, n_eff)


def cluster_bootstrap_ci(
    values: npt.NDArray[np.float64],
    cluster_key: Sequence[Hashable],
    stat_fn: Callable[[npt.NDArray[np.float64]], float],
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int | None = None,
) -> BootstrapCI:
    """Percentile CI resampling whole clusters with replacement.

    The unit of resampling is the cluster, so every observation sharing a key
    travels together and the same-day correlation the block bootstrap could not
    reach is preserved in each replicate.

    Replicates are variable length by construction — drawing ``k`` clusters with
    replacement lands a different row count each time — which is correct and is
    why ``stat_fn`` must be a scale-free statistic such as a mean.
    """
    n_obs = int(values.shape[0])
    groups = _group_indices(cluster_key)
    k = len(groups)
    point = stat_fn(values) if n_obs else float("nan")
    if n_obs == 0 or k < 2:
        # Nothing to resample across: report the point with an uninformative CI
        # rather than a narrow one invented from a single cluster.
        return BootstrapCI(point, float("-inf"), float("inf"), alpha, 0)

    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot, dtype=np.float64)
    valid = 0
    for _b in range(n_boot):
        picks = rng.integers(0, k, size=k)
        idx = np.concatenate([groups[p] for p in picks])
        stat = stat_fn(values[idx])
        if np.isfinite(stat):
            stats[valid] = stat
            valid += 1
    if valid == 0:
        return BootstrapCI(point, float("-inf"), float("inf"), alpha, 0)

    used = stats[:valid]
    lo = float(np.percentile(used, 100.0 * alpha / 2.0))
    hi = float(np.percentile(used, 100.0 * (1.0 - alpha / 2.0)))
    return BootstrapCI(point, lo, hi, alpha, valid)
