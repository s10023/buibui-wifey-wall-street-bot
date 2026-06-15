"""Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

Deflates an observed Sharpe by the expected-maximum Sharpe that ``N`` trials
would produce by chance, then expresses the result as a PSR. Pure math.
"""

import math
import statistics
from collections.abc import Sequence
from statistics import NormalDist

from analytics.research_guards.psr import probabilistic_sharpe_ratio

EULER_MASCHERONI = 0.5772156649015329
_NORM = NormalDist()


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe across ``n_trials`` independent trials.

    Uses the Gumbel-tail approximation from Bailey & LdP (2014). Returns 0.0
    when there is no effective trial multiplicity (``n_trials < 2``) or no
    cross-trial dispersion (``sr_variance <= 0``) — i.e. no deflation.
    """
    if n_trials < 2 or sr_variance <= 0.0:
        return 0.0
    std = math.sqrt(sr_variance)
    n = float(n_trials)
    gamma = EULER_MASCHERONI
    term = (1.0 - gamma) * _NORM.inv_cdf(1.0 - 1.0 / n) + gamma * _NORM.inv_cdf(
        1.0 - 1.0 / (n * math.e)
    )
    return std * term


def deflated_sharpe_ratio(
    sr: float,
    n_obs: int,
    *,
    trial_srs: Sequence[float] | None = None,
    n_trials: int | None = None,
    sr_variance: float | None = None,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """PSR with the benchmark set to the expected-maximum Sharpe.

    Provide **exactly one** source of trial multiplicity:

    * ``trial_srs`` — the per-trial Sharpe samples (``N`` and variance derived),
      or
    * ``n_trials`` + ``sr_variance`` — both required.

    Returns a probability in ``[0, 1]``. Equals
    :func:`probabilistic_sharpe_ratio` (benchmark 0) when there is no
    multiplicity to deflate against.
    """
    path_a = trial_srs is not None
    path_b = n_trials is not None or sr_variance is not None
    if path_a == path_b:
        raise ValueError("provide exactly one of trial_srs or (n_trials + sr_variance)")
    if path_a:
        assert trial_srs is not None  # narrowed by path_a
        srs = list(trial_srs)
        sr0 = (
            0.0
            if len(srs) < 2
            else expected_max_sharpe(len(srs), statistics.variance(srs))
        )
    else:
        if n_trials is None or sr_variance is None:
            raise ValueError("path B requires both n_trials and sr_variance")
        sr0 = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(sr, n_obs, skew, kurtosis, sr_benchmark=sr0)
