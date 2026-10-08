"""Survival metrics for a book's daily return path: drawdown, ulcer index, breach odds.

The Risk pillar's missing piece (edge-pillars spec, #378): the repo could price a
Sharpe but not say how deep or how long a book stays under water. Pure numpy, no
IO, so every later book can reuse it.

Conventions, fixed here so callers cannot drift:

- Wealth starts at 1.0 *before* the first return, and that starting value counts
  as a peak. A path that loses on day one is under water on day one.
- Drawdown is a fraction at or below zero (``-0.25`` is 25% below the peak).
- The ulcer index (Martin & McCann, 1989) is reported in **percent**, as Martin
  defined it: ``sqrt(mean((100 * DD_t)^2))``. A ratio of two ulcer indices is
  unit-free, so the OV-1 gate is unaffected by the choice.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt


def drawdown_series(returns: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Fractional distance below the running wealth peak at each session."""
    r = np.asarray(returns, dtype=np.float64)
    wealth = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(np.maximum(wealth, 1.0))
    return wealth / peak - 1.0


def ulcer_index(returns: npt.NDArray[np.float64]) -> float:
    """Martin's ulcer index in percent: depth and duration over the whole path."""
    dd = drawdown_series(returns)
    if dd.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean((100.0 * dd) ** 2)))


def max_drawdown(returns: npt.NDArray[np.float64]) -> float:
    """Worst drawdown as a fraction (``-0.5`` is a 50% fall). Report, never gate."""
    dd = drawdown_series(returns)
    return float(dd.min()) if dd.size else float("nan")


def time_under_water(returns: npt.NDArray[np.float64]) -> int:
    """Longest run of consecutive sessions spent below a prior peak."""
    under = drawdown_series(returns) < 0.0
    longest = run = 0
    for flag in under:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return longest


def _stationary_paths(
    n: int, length: int, n_paths: int, block: int, rng: np.random.Generator
) -> npt.NDArray[np.int64]:
    """``n_paths`` x ``length`` stationary-bootstrap index paths over ``range(n)``.

    Vectorised form of ``bootstrap._stationary_indices`` (geometric blocks with
    mean ``block``, wrap-around), generalised to a path length other than ``n``.
    It does not reproduce that function's random stream, so do not swap one for
    the other under a pinned seed.
    """
    p = 1.0 / block
    restart = rng.random((n_paths, length)) < p
    restart[:, 0] = True
    starts = rng.integers(0, n, size=(n_paths, length))
    pos = np.arange(length)
    # For each cell, the column where its current block began.
    block_col = np.maximum.accumulate(np.where(restart, pos[None, :], 0), axis=1)
    origin = np.take_along_axis(starts, block_col, axis=1)
    return ((origin + (pos[None, :] - block_col)) % n).astype(np.int64)


def drawdown_breach_curve(
    returns: npt.NDArray[np.float64],
    depths: Sequence[float],
    *,
    horizon: int,
    n_paths: int = 5_000,
    block: int = 252,
    seed: int | None = None,
) -> dict[float, float]:
    """P(a drawdown of at least ``D`` occurs within ``horizon`` sessions), per depth.

    Each path is a stationary-bootstrap resample of ``horizon`` sessions starting
    from fresh wealth, and its worst drawdown is compared with each ``D`` (a
    positive fraction, ``0.25`` for 25%). Two arms of equal length called with
    the same ``seed`` draw identical index paths, so their curves are paired.
    A plain-language translation of the ulcer index: report it, do not gate on it.
    """
    r = np.asarray(returns, dtype=np.float64)
    n = int(r.shape[0])
    if n < 2 or horizon < 1:
        raise ValueError("need at least two returns and a positive horizon")
    rng = np.random.default_rng(seed)
    blk = max(1, min(block, n - 1))
    worst = np.empty(n_paths, dtype=np.float64)
    # Chunked so 5,000 five-year paths stay near 25 MB per array, not 400 MB.
    for lo in range(0, n_paths, _PATH_CHUNK):
        k = min(_PATH_CHUNK, n_paths - lo)
        idx = _stationary_paths(n, horizon, k, blk, rng)
        wealth = np.cumprod(1.0 + r[idx], axis=1)
        peak = np.maximum.accumulate(np.maximum(wealth, 1.0), axis=1)
        worst[lo : lo + k] = (wealth / peak - 1.0).min(axis=1)
    return {float(d): float(np.mean(worst <= -d)) for d in depths}


_PATH_CHUNK = 500
