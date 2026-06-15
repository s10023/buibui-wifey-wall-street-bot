"""Audit-tool verdicts via bootstrap CI + multiple-testing haircut.

Replaces the crude ±0.05R bar in ``tools/gate_audit.py`` and
``tools/adr_threshold_audit.py`` with two statistical gates that BOTH must hold
before a cell earns an ``ENABLE`` / ``DISABLE`` verdict:

1. **Effect size (bootstrap CI).** A block/stationary-bootstrap CI on the
   suppressed slice's mean R must clear the ±``bar`` on the correct side
   (``ci.hi <= -bar`` → losers we should drop; ``ci.lo >= +bar`` → winners we
   must not suppress). Serial-correlation aware — an iid CI understates the
   width on autocorrelated trade streams.
2. **Multiple-testing significance (Holm haircut).** Each tested cell's
   two-sided p-value (from its slice Sharpe) is Holm-adjusted across the family
   of cells tested in one audit run; the adjusted p-value must be ``< alpha``.

Cells with ``n_supp < min_n`` are ``INSUFFICIENT`` and excluded from the family
(they never inflate the haircut denominator). The verdict / reason shape mirrors
:mod:`analytics.sweep_guard` so the project's guard consumers stay consistent.

Pure: no DB / IO. Consumes :mod:`analytics.research_guards`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Literal

import numpy as np
import numpy.typing as npt

from analytics.research_guards import block_bootstrap_ci, haircut_sharpe

DEFAULT_BAR = 0.05
DEFAULT_ALPHA = 0.05
DEFAULT_MIN_N = 30
DEFAULT_N_BOOT = 2000
DEFAULT_SEED = 12345

DECISION_ENABLE = "ENABLE"
DECISION_DISABLE = "DISABLE"
DECISION_CONCENTRATE = "CONCENTRATE"
DECISION_INSUFFICIENT = "INSUFFICIENT"

_Method = Literal["bonferroni", "holm", "bhy"]
_BootMethod = Literal["stationary", "circular"]

_NORM = NormalDist()


@dataclass(frozen=True)
class AuditCell:
    """One cell's inputs.

    ``supp_r`` are the per-trade R multiples of the would-be-suppressed slice
    (the verdict statistic operates on their mean). ``kept_r`` are the surviving
    trades' R — used only for the ``CONCENTRATE`` kept-vs-suppressed comparison;
    pass ``[]`` when there is no kept slice (e.g. the ADR aggregate view).
    """

    label: str
    supp_r: Sequence[float]
    kept_r: Sequence[float] = field(default_factory=list)


@dataclass(frozen=True)
class CellVerdict:
    decision: str  # ENABLE | DISABLE | CONCENTRATE | INSUFFICIENT
    n_supp: int
    n_kept: int
    supp_avg: float | None
    kept_avg: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_pvalue: float | None
    n_tests: int
    reasons: list[str]


def _mean(arr: npt.NDArray[np.float64]) -> float:
    return float(np.mean(arr))


def _slice_sharpe(arr: npt.NDArray[np.float64]) -> float:
    """Per-trade Sharpe ``mean / std(ddof=1)``; ``0.0`` with no dispersion.

    ``±inf`` for a zero-variance, non-zero-mean slice: a deterministic edge has
    no sampling uncertainty, so it is treated as maximally significant.
    """
    if arr.shape[0] < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    mean = float(np.mean(arr))
    if sd == 0.0:
        return 0.0 if mean == 0.0 else math.copysign(math.inf, mean)
    return mean / sd


def _two_sided_p(abs_sr: float, n_obs: int) -> float:
    """Two-sided p-value for ``Sharpe != 0`` — matches ``haircut_sharpe``'s
    internal ``t = sr·√n`` so the family p-values align exactly with the value
    the haircut recomputes per cell.
    """
    t = abs_sr * math.sqrt(n_obs)
    return 2.0 * (1.0 - _NORM.cdf(abs(t)))


@dataclass
class _Eligible:
    idx: int
    arr: npt.NDArray[np.float64]
    sr: float
    abs_p: float


def evaluate_audit_cells(
    cells: Sequence[AuditCell],
    *,
    bar: float = DEFAULT_BAR,
    alpha: float = DEFAULT_ALPHA,
    min_n: int = DEFAULT_MIN_N,
    haircut_method: _Method = "holm",
    n_boot: int = DEFAULT_N_BOOT,
    boot_method: _BootMethod = "circular",
    seed: int | None = DEFAULT_SEED,
    enable_concentrate: bool = True,
) -> list[CellVerdict]:
    """Verdict per cell, sharing one Holm haircut family across all tested cells.

    Returns a list aligned 1:1 with ``cells``. A cell earns ``ENABLE`` /
    ``DISABLE`` only if its bootstrap CI clears the ±``bar`` AND its Holm-adjusted
    p-value is ``< alpha``; ``CONCENTRATE`` refines the ``DISABLE`` branch when the
    kept slice out-performs the (reliably positive) suppressed slice by ≥ ``bar``.
    Everything else is ``INSUFFICIENT``.
    """
    eligible: list[_Eligible] = []
    for i, cell in enumerate(cells):
        arr = np.asarray(cell.supp_r, dtype=np.float64)
        n = int(arr.shape[0])
        if n < min_n or n < 2:
            continue
        sr = _slice_sharpe(arr)
        eligible.append(_Eligible(i, arr, sr, _two_sided_p(abs(sr), n)))

    family_p = [e.abs_p for e in eligible]
    n_tests = len(family_p)
    adj_by_idx: dict[int, float] = {}
    ci_by_idx: dict[int, tuple[float, float]] = {}
    for e in eligible:
        hr = haircut_sharpe(
            abs(e.sr),
            e.arr.shape[0],
            n_tests,
            method=haircut_method,
            pvalues_all=family_p,
        )
        adj_by_idx[e.idx] = hr.adjusted_pvalue
        ci = block_bootstrap_ci(
            e.arr, _mean, n_boot=n_boot, alpha=alpha, method=boot_method, seed=seed
        )
        ci_by_idx[e.idx] = (ci.lo, ci.hi)

    out: list[CellVerdict] = []
    for i, cell in enumerate(cells):
        supp = np.asarray(cell.supp_r, dtype=np.float64)
        kept = np.asarray(cell.kept_r, dtype=np.float64)
        n_supp = int(supp.shape[0])
        n_kept = int(kept.shape[0])
        supp_avg = float(np.mean(supp)) if n_supp else None
        kept_avg = float(np.mean(kept)) if n_kept else None

        if i not in adj_by_idx:
            out.append(
                CellVerdict(
                    DECISION_INSUFFICIENT,
                    n_supp,
                    n_kept,
                    supp_avg,
                    kept_avg,
                    None,
                    None,
                    None,
                    n_tests,
                    [f"n {n_supp} < min_n {min_n}"],
                )
            )
            continue

        adj_p = adj_by_idx[i]
        ci_lo, ci_hi = ci_by_idx[i]
        significant = adj_p < alpha
        reasons: list[str] = []

        if ci_hi <= -bar and significant:
            decision = DECISION_ENABLE
        elif ci_lo >= bar and significant:
            if (
                enable_concentrate
                and kept_avg is not None
                and supp_avg is not None
                and kept_avg >= supp_avg + bar
            ):
                decision = DECISION_CONCENTRATE
            else:
                decision = DECISION_DISABLE
        else:
            decision = DECISION_INSUFFICIENT
            if not significant:
                reasons.append(f"Holm-adj p {adj_p:.3f} >= alpha {alpha:.2f}")
            if ci_hi > -bar and ci_lo < bar:
                reasons.append(
                    f"CI [{ci_lo:.3f}, {ci_hi:.3f}] does not clear ±{bar:.2f}R"
                )

        out.append(
            CellVerdict(
                decision,
                n_supp,
                n_kept,
                supp_avg,
                kept_avg,
                ci_lo,
                ci_hi,
                adj_p,
                n_tests,
                reasons,
            )
        )
    return out
