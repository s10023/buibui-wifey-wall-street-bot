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

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class OverfitStats:
    """Per-config + sweep-level overfitting figures attached to a SweepRow.

    is_sharpe / oos_sharpe / deflated_sharpe are per-config; n_trials and pbo are
    sweep-level (identical on every row of a given sweep).
    """

    is_sharpe: float
    oos_sharpe: float
    deflated_sharpe: float
    n_trials: int
    pbo: float


@dataclass(frozen=True)
class PBOResult:
    """Probability of Backtest Overfitting via CSCV.

    ``pbo`` is the fraction of IS/OOS partitions where the IS-best config lands
    below the OOS median (logit < 0); NaN when not computable (< 2 configs, or
    fewer timeline blocks than ``n_splits`` can halve).
    """

    pbo: float
    n_combinations: int
    logits: tuple[float, ...]


def _col_mean(block: np.ndarray) -> np.ndarray:
    """Per-config performance over a submatrix: mean R per column."""
    return np.asarray(block.mean(axis=0), dtype=float)


def probability_of_backtest_overfitting(
    perf_matrix: np.ndarray,
    n_splits: int = 16,
) -> PBOResult:
    """CSCV PBO over a T×N matrix (rows = time-aligned observations, cols = trials).

    Partitions the T rows into ``S`` (even, ≤ T) contiguous submatrices, then for
    every way of choosing S/2 of them as in-sample: ranks configs by IS mean R,
    takes the IS-best, and records its OOS rank as a logit. PBO = P(logit < 0).
    """
    m = np.asarray(perf_matrix, dtype=float)
    if m.ndim != 2:
        raise ValueError("perf_matrix must be 2-D (T observations × N configs)")
    t, n = m.shape
    s = n_splits - (n_splits % 2)
    while s > t:
        s -= 2
    if n < _MIN_N or s < _MIN_N:
        return PBOResult(float("nan"), 0, ())

    blocks = np.array_split(np.arange(t), s)
    logits: list[float] = []
    for is_combo in itertools.combinations(range(s), s // 2):
        is_set = set(is_combo)
        is_rows = np.concatenate([blocks[i] for i in is_combo])
        oos_rows = np.concatenate([blocks[i] for i in range(s) if i not in is_set])
        is_perf = _col_mean(m[is_rows])
        oos_perf = _col_mean(m[oos_rows])
        best = int(np.argmax(is_perf))
        # dense rank of every config's OOS perf (1 = worst … n = best)
        order = oos_perf.argsort()
        ranks = np.empty(n, dtype=float)
        ranks[order] = np.arange(1, n + 1)
        omega = ranks[best] / (n + 1)
        omega = min(max(omega, 1e-6), 1.0 - 1e-6)
        logits.append(math.log(omega / (1.0 - omega)))

    arr = np.asarray(logits)
    pbo = float((arr < 0.0).mean())
    return PBOResult(pbo, len(logits), tuple(float(x) for x in logits))


def build_performance_matrix(
    trade_points: Sequence[Sequence[tuple[int, float]]],
    n_rows: int,
) -> np.ndarray:
    """Time-align per-config (entry_time_ms, pnl_r) points into a T×N matrix.

    Each column is one config; each row is an equal-width time bucket over the
    shared [min entry_time, max entry_time] span; the cell is the summed R of
    that config's trades in that bucket (0 where the config has no trade). The
    common time axis is what lets CSCV re-partition rows across configs.
    """
    n = len(trade_points)
    all_times = [t for cfg in trade_points for (t, _) in cfg]
    if n == 0 or not all_times or n_rows < 1:
        return np.zeros((0, n), dtype=float)
    t0 = min(all_times)
    span = (max(all_times) - t0) or 1
    m = np.zeros((n_rows, n), dtype=float)
    for j, cfg in enumerate(trade_points):
        for t, r in cfg:
            b = int((t - t0) / span * n_rows)
            if b >= n_rows:
                b = n_rows - 1
            m[b, j] += r
    return m
