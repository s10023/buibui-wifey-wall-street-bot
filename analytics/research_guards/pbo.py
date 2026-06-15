"""Probability of Backtest Overfitting via CSCV (Bailey, Borwein, LdP, 2015).

Combinatorially Symmetric Cross-Validation: split the per-period performance
matrix into ``n_splits`` blocks, and over every balanced train/test partition
ask whether the in-sample-best trial stays good out-of-sample. PBO is the
fraction of partitions where it lands in the bottom half OOS. Pure math.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    logits: list[float]
    degradation_slope: float
    n_combinations: int


def _sharpe(col: npt.NDArray[np.float64]) -> float:
    """Per-period Sharpe of one trial's returns (the default CSCV metric)."""
    if col.shape[0] < 2:
        return 0.0
    sd = float(np.std(col, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(col)) / sd


def _relative_rank(values: npt.NDArray[np.float64], target: int, n: int) -> float:
    """Average-rank relative position of ``target`` in ``values``, in (0, 1)."""
    v = values[target]
    less = int(np.sum(values < v))
    equal = int(np.sum(values == v))  # includes the target itself
    rank = less + (equal + 1) / 2.0  # 1-indexed average rank
    return rank / (n + 1)


def _ols_slope(x: list[float], y: list[float]) -> float:
    """OLS slope of ``y`` on ``x`` (0.0 when ``x`` has no variance)."""
    xa = np.asarray(x, dtype=np.float64)
    ya = np.asarray(y, dtype=np.float64)
    xm = float(xa.mean())
    denom = float(np.sum((xa - xm) ** 2))
    if denom == 0.0:
        return 0.0
    ym = float(ya.mean())
    return float(np.sum((xa - xm) * (ya - ym)) / denom)


def cscv_pbo(
    perf_matrix: npt.NDArray[np.float64],
    n_splits: int = 14,
    metric: Callable[[npt.NDArray[np.float64]], float] | None = None,
) -> PBOResult:
    """Probability of Backtest Overfitting over a (T_periods, N_trials) matrix.

    Splits the ``T`` rows into ``n_splits`` equal blocks (remainder dropped) and
    iterates every balanced train/test partition — ``C(n_splits, n_splits/2)``
    combinations (e.g. ``C(14, 7) = 3432`` at the default). ``metric`` defaults
    to per-trial Sharpe.
    """
    if n_splits < 4 or n_splits % 2 != 0:
        raise ValueError("n_splits must be even and >= 4")
    m = np.asarray(perf_matrix, dtype=np.float64)
    if m.ndim != 2:
        raise ValueError("perf_matrix must be 2-D (T_periods, N_trials)")
    t_periods, n_trials = int(m.shape[0]), int(m.shape[1])
    if n_trials < 2:
        raise ValueError("need >= 2 trials")
    block_size = t_periods // n_splits
    if block_size < 2:
        raise ValueError("not enough periods for n_splits (need >= 2 rows per block)")
    score = _sharpe if metric is None else metric

    blocks = [m[i * block_size : (i + 1) * block_size] for i in range(n_splits)]
    half = n_splits // 2
    logits: list[float] = []
    is_perf: list[float] = []
    oos_perf: list[float] = []
    for train_ids in combinations(range(n_splits), half):
        train_set = set(train_ids)
        test_ids = [i for i in range(n_splits) if i not in train_set]
        train = np.vstack([blocks[i] for i in train_ids])
        test = np.vstack([blocks[i] for i in test_ids])
        is_metrics = np.array([score(train[:, c]) for c in range(n_trials)])
        oos_metrics = np.array([score(test[:, c]) for c in range(n_trials)])
        n_star = int(np.argmax(is_metrics))
        omega = _relative_rank(oos_metrics, n_star, n_trials)
        omega = min(max(omega, 1.0 / (n_trials + 1)), n_trials / (n_trials + 1))
        logits.append(math.log(omega / (1.0 - omega)))
        is_perf.append(float(is_metrics[n_star]))
        oos_perf.append(float(oos_metrics[n_star]))

    pbo = sum(1 for lam in logits if lam <= 0.0) / len(logits)
    return PBOResult(
        pbo=pbo,
        logits=logits,
        degradation_slope=_ols_slope(is_perf, oos_perf),
        n_combinations=len(logits),
    )
