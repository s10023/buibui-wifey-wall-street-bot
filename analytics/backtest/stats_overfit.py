"""Overfitting / multiple-testing controls for the WFO sweep (Phase 0.3a/b).

Pure math — no DB, no network, no engine import. Implements:

* ``sharpe_ratio`` — per-trade Sharpe over an R-multiple series, ``mean / std``
  (sample std, ddof=1; non-annualized — the engine's native unit is per-trade R).
* ``probabilistic_sharpe_ratio`` / ``deflated_sharpe_ratio`` — Bailey & López de
  Prado (2014). DSR deflates the selected config's Sharpe by the expected maximum
  Sharpe under ``N`` independent zero-edge trials.
* ``probability_of_backtest_overfitting`` — Combinatorially-Symmetric CV (Bailey,
  Borwein, López de Prado & Zhu 2017). Fraction of IS/OOS partitions where the
  IS-best config lands below the OOS median.

Φ / Φ⁻¹ come from stdlib ``statistics.NormalDist`` (scipy is not a dependency).
Skew/kurtosis are standardized 3rd/4th moments (ddof=1 scale); kurtosis is
non-excess (normal == 3). All functions return ``0.0`` / ``NaN`` on degenerate
input rather than raising.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist

import numpy as np

_MIN_N = 2
_EULER_MASCHERONI = 0.5772156649015329
_NORM = NormalDist()


def _moments(returns: Sequence[float]) -> tuple[float, float, float, float]:
    """Return (mean, std, skew, kurt) of an R series.

    std is the sample std (ddof=1). skew/kurt are standardized central moments
    over the sample std; kurt is non-excess (normal == 3). Returns zeros when
    n < 2 or std == 0.
    """
    arr = np.asarray(list(returns), dtype=float)
    n = arr.size
    if n < _MIN_N:
        return (float(arr.mean()) if n else 0.0, 0.0, 0.0, 0.0)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    if std == 0.0:
        return (mean, 0.0, 0.0, 0.0)
    z = (arr - mean) / std
    skew = float((z**3).mean())
    kurt = float((z**4).mean())
    return (mean, std, skew, kurt)


def sharpe_ratio(returns: Sequence[float]) -> float:
    """Per-trade Sharpe ``mean / std`` (sample std). 0.0 on degenerate input."""
    mean, std, _, _ = _moments(returns)
    if std == 0.0:
        return 0.0
    return mean / std


def probabilistic_sharpe_ratio(
    sr: float,
    n: int,
    skew: float,
    kurt: float,
    sr_star: float = 0.0,
) -> float:
    """Probabilistic Sharpe Ratio — P(true SR > sr_star) given the estimate.

    PSR = Φ( (SR - SR*)·√(n-1) / √(1 - skew·SR + (kurt-1)/4·SR²) ).
    Returns 0.0 when n < 2 or the variance term is non-positive (ill-conditioned).
    """
    if n < _MIN_N:
        return 0.0
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom <= 0.0:
        return 0.0
    stat = (sr - sr_star) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(_NORM.cdf(stat))


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """Expected maximum Sharpe under n_trials independent zero-edge trials.

    SR*_0 = √V · [ (1-γ)·Z⁻¹(1 - 1/N) + γ·Z⁻¹(1 - 1/(N·e)) ]  (Bailey & LdP 2014).
    Returns 0.0 when N < 2 or V <= 0 (no deflation possible).
    """
    if n_trials < _MIN_N or sr_variance <= 0.0:
        return 0.0
    max_z = (1.0 - _EULER_MASCHERONI) * _NORM.inv_cdf(1.0 - 1.0 / n_trials) + (
        _EULER_MASCHERONI * _NORM.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    )
    return math.sqrt(sr_variance) * max_z


def deflated_sharpe_ratio(
    observed_sr: float,
    trial_sharpes: Sequence[float],
    n_returns: int,
    skew: float,
    kurt: float,
) -> float:
    """Deflated Sharpe Ratio = PSR evaluated against the expected-max-Sharpe null.

    ``trial_sharpes`` is the per-config Sharpe of every grid trial (used to
    estimate the cross-trial Sharpe variance V and the trial count N). With a
    single trial, V is undefined ⇒ SR*_0 = 0 ⇒ DSR == PSR(0).
    """
    arr = np.asarray(list(trial_sharpes), dtype=float)
    if arr.size < _MIN_N:
        sr_star = 0.0
    else:
        sr_star = expected_max_sharpe(float(arr.var(ddof=1)), arr.size)
    return probabilistic_sharpe_ratio(observed_sr, n_returns, skew, kurt, sr_star)
