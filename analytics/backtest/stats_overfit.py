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

from collections.abc import Sequence

import numpy as np

_MIN_N = 2


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
