"""Audit-tool verdicts via bootstrap CI + multiple-testing haircut.

Consumers here are :mod:`analytics.warning_audit` (via
``tools/warning_value_audit.py``) and ``tools/regime_gate_replay.py``. It
replaces a crude ±0.05R bar with two statistical gates that BOTH must hold
before a cell earns an ``ENABLE`` / ``DISABLE`` verdict:

1. **Effect size (cluster bootstrap CI).** A CI on the suppressed slice's mean R
   must clear the ±``bar`` on the correct side (``ci.hi <= -bar`` → losers we
   should drop; ``ci.lo >= +bar`` → winners we must not suppress). It resamples
   whole **clusters**, not trades.
2. **Multiple-testing significance (Holm haircut).** Each tested cell's
   two-sided p-value (from its slice Sharpe) is Holm-adjusted across the family
   of cells tested in one audit run; the adjusted p-value must be ``< alpha``.
   The t-statistic uses ``n_eff = n / DEFF``, never the trade count.

⚠ **BOTH legs are priced on the cluster unit, and it is REQUIRED.** A block
bootstrap absorbs *serial* dependence — it resamples runs adjacent in the array
it is handed — and same-day cross-symbol trades are scattered through that
array, so no block length reaches them. Undeflated, this repo's own panel read
30 of 64 cells as significant where 12 survive, and single-strategy CIs were
~1.9x too narrow. See :mod:`analytics.research_guards.cluster` and
``docs/audits/2026-08-20-audit-guard-cluster-key-fix.md``.

Cells with ``n_supp < min_n``, **or with an unusable ``cluster_key``**, are
``INSUFFICIENT`` and excluded from the family (they never inflate the haircut
denominator). The verdict / reason shape mirrors :mod:`analytics.sweep_guard` so
the project's guard consumers stay consistent.

Pure: no DB / IO. Consumes :mod:`analytics.research_guards`.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Literal

import numpy as np
import numpy.typing as npt

from analytics.research_guards import (
    cluster_bootstrap_ci,
    cluster_stats,
    haircut_sharpe,
)

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

_NORM = NormalDist()


@dataclass(frozen=True)
class AuditCell:
    """One cell's inputs.

    ``supp_r`` are the per-trade R multiples of the would-be-suppressed slice
    (the verdict statistic operates on their mean). ``kept_r`` are the surviving
    trades' R — used only for the ``CONCENTRATE`` kept-vs-suppressed comparison;
    pass ``[]`` when there is no kept slice (e.g. the ADR aggregate view).

    ``cluster_key`` is the dependence unit, one entry per ``supp_r`` row, and it
    is **REQUIRED and positioned before the defaulted** ``kept_r`` so mypy
    forces every call site to state it. For this repo's panels that is the
    **session day**: same-day cross-symbol trades share the day's move, and a
    block bootstrap cannot reach them because it resamples runs adjacent *in the
    array*, where those rows are scattered.

    ⚠ **A mismatched length FAILS CLOSED to ``INSUFFICIENT``** rather than
    falling back to per-trade resampling. An unmeasurable panel and an
    uncorrelated one must not both read as a deflator of 1.0 — that is the
    fail-open shape this repo already closed in ``tools/distil_power.py``, and
    the whole reason the key is required instead of optional.

    Keys are opaque and only compared for equality, so a UTC-day integer, an ISO
    date or a ``(symbol, day)`` tuple all work. **For US RTH equities the UTC
    calendar day IS the session day** — RTH spans 13:30–20:00 UTC in summer and
    14:30–21:00 UTC in winter, both inside one UTC date — so flooring a
    millisecond stamp to the UTC day is correct here and needs no ET conversion.
    """

    label: str
    supp_r: Sequence[float]
    cluster_key: Sequence[Hashable]
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
    n_clusters: int | None = None
    """Distinct ``cluster_key`` values in the suppressed slice — the real sample
    size behind ``n_supp``. ``None`` when the cell was never tested."""
    design_effect: float | None = None
    """``1 + (m̄-1)·ICC``: how many trades it takes to buy one independent
    observation. ``None`` when untested. Reported because a deflator applied
    silently is indistinguishable from one that was forgotten."""
    powered_null: bool = False
    """True iff the CI lies strictly INSIDE ±``bar`` — i.e. an effect worth
    acting on has been ruled out, not merely left uncalled.

    Computed by :func:`powered_null`, which is the single definition — call it
    rather than restating ``ci_lo > -bar and ci_hi < bar`` anywhere. Defaults
    ``False``: a cell that was never tested (``n < min_n``, no CI) has
    established nothing.
    """


def powered_null(ci_lo: float | None, ci_hi: float | None, *, bar: float) -> bool:
    """True iff a two-sided CI lies strictly INSIDE ``±bar``.

    **This is the only honest test for a powered null, and ``n >= min_n`` is
    not a substitute for it.** A sample-size floor says a test *ran*; it never
    says the test could have *seen* anything, so it cannot distinguish "the
    effect is smaller than the bar" from "the CI is five times the bar and we
    cannot tell". Only the bar carries the notion of "worth acting on", so only
    a CI sized against the bar can license a negative claim.

    The warning-value audit is this repo's own falsifier: awarding COSMETIC on
    ``n >= min_n`` labelled 11 of 12 cells a null, and under this predicate
    **0 of 11 survive** at a half-width median 4.1× the bar
    (``docs/audits/2026-08-13-warning-value-audit.md``).

    Returns ``False`` for a missing or non-finite bound: a cell that was never
    tested has established nothing. That default is load-bearing — the failure
    callers care about is a null claimed too easily, so the untested case must
    fall to ``INSUFFICIENT``, never to ``powered``.

    This predicate is two-sided. A best-of-k arm sweep needs a one-sided
    variant, which this repo does not have; do not reach for this one there.
    """
    if ci_lo is None or ci_hi is None:
        return False
    if not (math.isfinite(ci_lo) and math.isfinite(ci_hi)):
        return False
    return ci_lo > -bar and ci_hi < bar


MS_PER_DAY = 86_400_000


def session_day_keys(ts_ms: Sequence[int]) -> list[int]:
    """UTC calendar day per millisecond stamp — the cluster unit for this repo.

    **For US RTH equities the UTC day IS the session day**, which is why no ET
    conversion appears here: the regular session spans 13:30–20:00 UTC under EDT
    and 14:30–21:00 UTC under EST, and both sit inside a single UTC date. That is
    a property of the *session*, not of the bar grid — CLAUDE.md's warning that a
    `4h` bar renders at a different ET hour either side of the DST switch is
    about hour-keyed logic and does not reach a day key.

    ⚠ **Do not port this to a 24h tape.** On crypto the UTC day is an arbitrary
    cut through a continuous session, so it would group trades that share no
    common driver while splitting ones that do.

    One definition, used by every :class:`AuditCell` producer, so two consumers
    cannot silently cluster on different units and report comparable numbers.
    """
    return [int(t) // MS_PER_DAY for t in ts_ms]


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


def _two_sided_p(abs_sr: float, n_eff: int) -> float:
    """Two-sided p-value for ``Sharpe != 0`` — matches ``haircut_sharpe``'s
    internal ``t = sr·√n`` so the family p-values align exactly with the value
    the haircut recomputes per cell.

    ⚠ **``n_eff``, never the trade count.** Callers must pass the
    cluster-deflated count and pass the *same* integer to ``haircut_sharpe``,
    or the two disagree about the test they are running. Undeflated, this leg
    called 30 of 64 cells significant where 12 survive — a larger error channel
    than the CI it sits beside.
    """
    t = abs_sr * math.sqrt(n_eff)
    return 2.0 * (1.0 - _NORM.cdf(abs(t)))


@dataclass
class _Eligible:
    idx: int
    arr: npt.NDArray[np.float64]
    keys: Sequence[Hashable]
    sr: float
    abs_p: float
    n_eff: int
    n_clusters: int
    design_effect: float


def evaluate_audit_cells(
    cells: Sequence[AuditCell],
    *,
    bar: float = DEFAULT_BAR,
    alpha: float = DEFAULT_ALPHA,
    min_n: int = DEFAULT_MIN_N,
    haircut_method: _Method = "holm",
    n_boot: int = DEFAULT_N_BOOT,
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
    skip_reason: dict[int, str] = {}
    for i, cell in enumerate(cells):
        arr = np.asarray(cell.supp_r, dtype=np.float64)
        n = int(arr.shape[0])
        if len(cell.cluster_key) != n:
            # FAIL CLOSED. Resampling trades here would report a confident,
            # undeflated verdict — the exact failure the required key exists to
            # prevent — so an unusable key must cost the verdict, not the guard.
            skip_reason[i] = f"cluster_key length {len(cell.cluster_key)} != n_supp {n}"
            continue
        if n < min_n or n < 2:
            skip_reason[i] = f"n {n} < min_n {min_n}"
            continue
        cs = cluster_stats(arr, cell.cluster_key)
        n_eff = max(2, int(round(cs.n_eff)))
        sr = _slice_sharpe(arr)
        eligible.append(
            _Eligible(
                i,
                arr,
                cell.cluster_key,
                sr,
                _two_sided_p(abs(sr), n_eff),
                n_eff,
                cs.n_clusters,
                cs.design_effect,
            )
        )

    family_p = [e.abs_p for e in eligible]
    n_tests = len(family_p)
    adj_by_idx: dict[int, float] = {}
    ci_by_idx: dict[int, tuple[float, float]] = {}
    stats_by_idx: dict[int, tuple[int, float]] = {
        e.idx: (e.n_clusters, e.design_effect) for e in eligible
    }
    for e in eligible:
        # SAME integer in both, or the family p-value and the value the haircut
        # recomputes describe different tests.
        hr = haircut_sharpe(
            abs(e.sr),
            e.n_eff,
            n_tests,
            method=haircut_method,
            pvalues_all=family_p,
        )
        adj_by_idx[e.idx] = hr.adjusted_pvalue
        ci = cluster_bootstrap_ci(
            e.arr, e.keys, _mean, n_boot=n_boot, alpha=alpha, seed=seed
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
                    [skip_reason.get(i, f"n {n_supp} < min_n {min_n}")],
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
                n_clusters=stats_by_idx[i][0],
                design_effect=stats_by_idx[i][1],
                powered_null=powered_null(ci_lo, ci_hi, bar=bar),
            )
        )
    return out
