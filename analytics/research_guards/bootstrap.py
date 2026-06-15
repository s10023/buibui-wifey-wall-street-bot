"""Block / stationary bootstrap confidence intervals (Politis & Romano, 1994).

Resamples wrap-around blocks of a return series to build a percentile CI for an
arbitrary statistic, preserving short-range autocorrelation that an iid
bootstrap would destroy. Pure math (numpy only).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class BootstrapCI:
    point: float
    lo: float
    hi: float
    alpha: float
    n_valid: int


def _stationary_indices(
    n: int, block: int, rng: np.random.Generator
) -> npt.NDArray[np.int64]:
    """Stationary bootstrap (geometric block length, mean ``block``)."""
    p = 1.0 / block
    idx = np.empty(n, dtype=np.int64)
    idx[0] = rng.integers(0, n)
    draws = rng.random(n)
    restarts = rng.integers(0, n, size=n)
    for t in range(1, n):
        if draws[t] < p:
            idx[t] = restarts[t]
        else:
            idx[t] = (idx[t - 1] + 1) % n
    return idx


def _circular_indices(
    n: int, block: int, rng: np.random.Generator
) -> npt.NDArray[np.int64]:
    """Circular block bootstrap (fixed block length, wrap-around)."""
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=n_blocks)
    offsets = np.arange(block)
    idx = ((starts[:, None] + offsets[None, :]) % n).reshape(-1)
    return idx[:n].astype(np.int64)


def block_bootstrap_ci(
    returns: npt.NDArray[np.float64],
    stat_fn: Callable[[npt.NDArray[np.float64]], float],
    n_boot: int = 10_000,
    block: int | None = None,
    alpha: float = 0.05,
    method: Literal["stationary", "circular"] = "stationary",
    seed: int | None = None,
) -> BootstrapCI:
    """Percentile bootstrap CI for ``stat_fn`` over a (possibly serially
    correlated) return series.

    ``block`` defaults to ``round(len(returns) ** (1/3))`` and is clamped to
    ``[1, len-1]``. ``stat_fn`` results that are NaN are dropped (tracked via
    ``n_valid``). ``seed`` makes the resampling reproducible.
    """
    arr = np.asarray(returns, dtype=np.float64)
    n = int(arr.shape[0])
    if n < 2:
        raise ValueError("returns must have length >= 2")
    if block is None:
        block = max(1, round(n ** (1.0 / 3.0)))
    block = max(1, min(block, n - 1))
    rng = np.random.default_rng(seed)
    point = float(stat_fn(arr))
    samples: list[float] = []
    for _ in range(n_boot):
        if method == "stationary":
            idx = _stationary_indices(n, block, rng)
        else:
            idx = _circular_indices(n, block, rng)
        value = stat_fn(arr[idx])
        if not math.isnan(value):
            samples.append(value)
    if samples:
        lo = float(np.quantile(samples, alpha / 2.0))
        hi = float(np.quantile(samples, 1.0 - alpha / 2.0))
    else:
        lo = float("nan")
        hi = float("nan")
    return BootstrapCI(point=point, lo=lo, hi=hi, alpha=alpha, n_valid=len(samples))
