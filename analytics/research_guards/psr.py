"""Probabilistic Sharpe Ratio (Bailey & López de Prado, 2012).

Pure-math helper for the research-guards package — no DB / IO / network.
"""

import math
from statistics import NormalDist

_NORM = NormalDist()


def probabilistic_sharpe_ratio(
    sr: float,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    sr_benchmark: float = 0.0,
) -> float:
    """Probability that the true Sharpe exceeds ``sr_benchmark``.

    ``kurtosis`` is **non-excess** (a normal distribution has kurtosis 3.0).
    scipy/pandas report *excess* kurtosis, so a caller passing one of those
    moments must add 3.0 first.

    Returns a probability in ``[0, 1]``.
    """
    if n_obs < 2:
        raise ValueError("n_obs must be >= 2")
    variance = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    if variance <= 0.0:
        raise ValueError("degenerate higher moments: non-positive PSR variance term")
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(variance)
    return _NORM.cdf(z)
