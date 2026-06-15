"""Multiple-testing Sharpe haircut (Harvey & Liu, 2014 — classic core).

Adjusts a single-test p-value for the number of strategies tried, then backs
the adjustment out into a haircut Sharpe. v1 ships the three classic
adjustments (Bonferroni / Holm / BHY) on p-values; the full Harvey-Liu
empirical-t procedure is a later refinement.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

_NORM = NormalDist()

_Method = Literal["bonferroni", "holm", "bhy"]

# Φ⁻¹ blows up at the open endpoints; keep adjusted p-values strictly inside.
_P_FLOOR = 1e-15


@dataclass(frozen=True)
class HaircutResult:
    adjusted_pvalue: float
    haircut_sharpe: float
    haircut_pct: float
    method: str
    fell_back: bool


def _adjust_pvalues(pvals: Sequence[float], method: _Method) -> list[float]:
    """Holm (FWER) or BHY (FDR) adjusted p-values in the original order."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [0.0] * m
    if method == "holm":
        running = 0.0
        for rank, idx in enumerate(order):  # ascending; factor = m - rank
            val = min(1.0, (m - rank) * pvals[idx])
            running = max(running, val)
            adjusted[idx] = running
    else:  # bhy — Benjamini-Hochberg-Yekutieli (FDR under dependence)
        c_m = sum(1.0 / k for k in range(1, m + 1))
        prev = 1.0
        for rank in range(m - 1, -1, -1):  # step-up from the largest p-value
            idx = order[rank]
            val = min(1.0, c_m * m / (rank + 1) * pvals[idx])
            prev = min(prev, val)
            adjusted[idx] = prev
    return adjusted


def haircut_sharpe(
    sr: float,
    n_obs: int,
    n_tests: int,
    method: _Method = "holm",
    pvalues_all: Sequence[float] | None = None,
) -> HaircutResult:
    """Adjust ``sr`` for having searched ``n_tests`` strategies.

    ``pvalues_all`` (the full set of per-test p-values) is required for the
    ``holm`` / ``bhy`` step-down ordering; if it is omitted the function falls
    back to Bonferroni and sets ``fell_back=True``. For ``holm`` / ``bhy`` pass
    ``pvalues_all`` with ``len == n_tests``.
    """
    if n_tests < 1:
        raise ValueError("n_tests must be >= 1")
    if sr <= 0.0:
        return HaircutResult(1.0, 0.0, 0.0, method, False)

    t = sr * math.sqrt(n_obs)
    p = 2.0 * (1.0 - _NORM.cdf(abs(t)))
    fell_back = False
    if n_tests == 1:
        p_adj = p
    elif method == "bonferroni":
        p_adj = min(1.0, p * n_tests)
    elif pvalues_all is None:
        p_adj = min(1.0, p * n_tests)
        fell_back = True
    else:
        adjusted = _adjust_pvalues(list(pvalues_all), method)
        nearest = min(range(len(pvalues_all)), key=lambda i: abs(pvalues_all[i] - p))
        p_adj = adjusted[nearest]

    p_clamped = min(max(p_adj, _P_FLOOR), 1.0)
    t_adj = _NORM.inv_cdf(1.0 - p_clamped / 2.0)
    haircut = max(0.0, t_adj) / math.sqrt(n_obs)
    haircut_pct = 1.0 - haircut / sr  # sr > 0 here
    return HaircutResult(p_adj, haircut, haircut_pct, method, fell_back)
