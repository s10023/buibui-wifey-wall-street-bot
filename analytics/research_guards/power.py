"""Required-effect-size inversion — how large a Sharpe the gate demands.

Promoted 2026-08-14 from two independent implementations that agreed to
0.0000% across a 16-cell grid but were held to it by nothing:
``tools/era_power_price.required_sharpe``, which inverted the production
``deflated_sharpe_ratio``, and ``tools/multi_regime_power.required_sr``, which
re-derived the PSR z-formula by hand and bisected on that. The second is the
shape the first's own docstring names as "how a spec and a driver come to
disagree", which is why the hand-derived copy is the one that goes.

This module owns the **effect-size bar only**. The powered-null containment
criterion belongs to :func:`analytics.audit_guard.powered_null`, and the
one-sided best-of-k variant to
:func:`analytics.sl_horizon.negative_claim_licensed`. Neither is restated here,
and neither should be: six sites in this repo have already spelled that rule
six different ways.
"""

from __future__ import annotations

import math

from analytics.research_guards.dsr import deflated_sharpe_ratio
from analytics.research_guards.gate import GATE_DSR
from analytics.research_guards.psr import probabilistic_sharpe_ratio

__all__ = ["required_sharpe"]

_MAX_SHARPE = 1e6
_ITERS = 200


def required_sharpe(
    n_obs: int,
    *,
    n_trials: int | None = None,
    sr_variance: float | None = None,
    benchmark_sr: float | None = None,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    target: float = GATE_DSR,
) -> float:
    """Smallest Sharpe whose deflated/probabilistic SR reaches ``target``.

    Provide **exactly one** source of the benchmark, mirroring
    :func:`deflated_sharpe_ratio`'s own contract:

    * ``n_trials`` + ``sr_variance`` — inverts :func:`deflated_sharpe_ratio`, so
      the benchmark is the expected maximum Sharpe of that trial family.
    * ``benchmark_sr`` — inverts :func:`probabilistic_sharpe_ratio` against a
      benchmark already in hand.

    Both paths invert a **production** function rather than a hand-derived
    closed form, which is what keeps the bar and the gate in agreement by
    construction.

    ``kurtosis`` is **non-excess** (normal = 3.0), matching
    :func:`probabilistic_sharpe_ratio`.

    Returns ``math.inf`` when ``target`` is unreachable at any finite Sharpe.
    That is a finding rather than an error: the PSR z-statistic is bounded above
    by roughly ``sqrt(2 * (n_obs - 1))``, so a small sample against a wide trial
    family cannot clear the gate at *any* effect size, and reporting a huge
    finite number instead would read as "nearly there".
    """
    has_multiplicity = n_trials is not None or sr_variance is not None
    if has_multiplicity == (benchmark_sr is not None):
        raise ValueError(
            "provide exactly one of (n_trials + sr_variance) or benchmark_sr"
        )
    if has_multiplicity and (n_trials is None or sr_variance is None):
        raise ValueError("n_trials and sr_variance are both required together")
    if n_obs < 2:
        return math.inf

    def attained(sr: float) -> float:
        """Probability reached at ``sr``; degenerate variance counts as attained.

        ``probabilistic_sharpe_ratio`` raises on a non-positive variance term
        where the pre-promotion ``required_sr`` treated it as ``z = +inf``.
        Mirroring that keeps the promoted reports byte-identical.
        """
        try:
            if benchmark_sr is not None:
                return probabilistic_sharpe_ratio(
                    sr,
                    n_obs,
                    skew=skew,
                    kurtosis=kurtosis,
                    sr_benchmark=benchmark_sr,
                )
            assert n_trials is not None and sr_variance is not None
            return deflated_sharpe_ratio(
                sr,
                n_obs,
                n_trials=n_trials,
                sr_variance=sr_variance,
                skew=skew,
                kurtosis=kurtosis,
            )
        except ValueError:
            return 1.0

    lo, hi = 0.0, 1.0
    for _ in range(_ITERS):
        if attained(hi) >= target:
            break
        lo, hi = hi, hi * 2.0
        if hi > _MAX_SHARPE:
            return math.inf
    for _ in range(_ITERS):
        mid = (lo + hi) / 2.0
        if attained(mid) >= target:
            hi = mid
        else:
            lo = mid
    return hi
