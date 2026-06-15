"""Sweep commit-gate — overfitting refusal for the WFO ``tp_r`` decision.

Wraps the pure :mod:`analytics.research_guards` statistics into a single
*commit / do-not-commit / insufficient* verdict for one swept cell
(``strategy × symbol × tf``). The gate is **additive** to the existing OOS
filter (``/param-sweep-apply`` already drops ``OVERFIT`` rows and requires
positive OOS ``avg_r``); it adds the multiple-testing correction the project
currently lacks.

Commit rule (all three must hold)::

    DSR >= dsr_threshold   AND   PBO <= pbo_threshold   AND   n_obs >= MinTRL

Pure: no DB / IO. Inputs are per-trial return series; the caller (param_sweep)
adapts ``SweepRow`` objects into :class:`TrialPerf`.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from analytics.research_guards import (
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)

DSR_THRESHOLD = 0.95
PBO_THRESHOLD = 0.5
MINTRL_CONFIDENCE = 0.95
DEFAULT_N_SPLITS = 14

DECISION_COMMIT = "COMMIT"
DECISION_BLOCK = "DO_NOT_COMMIT"
DECISION_INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class TrialPerf:
    """One swept param combo's realised trades (full-window, IS+OOS).

    ``returns`` are per-trade R multiples (after fees) and ``times`` are the
    aligned entry timestamps (ms) used to bin the CSCV performance matrix.
    """

    label: str
    returns: list[float]
    times: list[int]


@dataclass(frozen=True)
class CommitGateVerdict:
    decision: str  # COMMIT | DO_NOT_COMMIT | INSUFFICIENT
    dsr: float | None
    pbo: float | None
    min_trl: float | None
    n_obs: int
    n_trials: int
    reasons: list[str]

    @property
    def committable(self) -> bool:
        return self.decision == DECISION_COMMIT


def _trial_sharpe(returns: Sequence[float]) -> float:
    """Per-trade Sharpe of one trial: ``mean / std(ddof=1)``.

    ``0.0`` when fewer than two returns or the series has no dispersion.
    """
    arr = np.asarray(returns, dtype=np.float64)
    if arr.shape[0] < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(arr)) / sd


def _build_perf_matrix(
    trials: Sequence[TrialPerf], n_bins: int
) -> npt.NDArray[np.float64]:
    """Calendar-binned ``(n_bins, n_trials)`` performance matrix for CSCV.

    Trials are not trade-aligned (different params -> different trades), so each
    column is binned onto a shared time grid over the union trade span; a cell
    holds the sum of that trial's per-trade R inside the bin.
    """
    mat = np.zeros((n_bins, len(trials)), dtype=np.float64)
    all_times = [t for tr in trials for t in tr.times]
    if not all_times:
        return mat
    tmin, tmax = min(all_times), max(all_times)
    span = tmax - tmin
    for j, tr in enumerate(trials):
        for t, r in zip(tr.times, tr.returns, strict=False):
            if span == 0:
                b = 0
            else:
                b = int((t - tmin) / span * n_bins)
                if b >= n_bins:
                    b = n_bins - 1
            mat[b, j] += r
    return mat


def _decide(
    *,
    dsr: float,
    pbo: float,
    min_trl: float,
    n_obs: int,
    dsr_threshold: float,
    pbo_threshold: float,
) -> tuple[str, list[str]]:
    """Apply the three hard checks. Returns ``(decision, failing_reasons)``."""
    reasons: list[str] = []
    if dsr < dsr_threshold:
        reasons.append(f"DSR {dsr:.2f} < {dsr_threshold:.2f}")
    if pbo > pbo_threshold:
        reasons.append(f"PBO {pbo:.2f} > {pbo_threshold:.2f}")
    if n_obs < min_trl:
        trl = "∞" if math.isinf(min_trl) else f"{math.ceil(min_trl)}"
        reasons.append(f"n {n_obs} < MinTRL {trl}")
    return (DECISION_COMMIT if not reasons else DECISION_BLOCK, reasons)


def evaluate_commit_gate(
    chosen: TrialPerf,
    all_trials: Sequence[TrialPerf],
    *,
    n_grid: int,
    dsr_threshold: float = DSR_THRESHOLD,
    pbo_threshold: float = PBO_THRESHOLD,
    mintrl_confidence: float = MINTRL_CONFIDENCE,
    n_splits: int = DEFAULT_N_SPLITS,
) -> CommitGateVerdict:
    """Verdict for committing ``chosen``'s params, given the full grid.

    ``n_grid`` is the true number of trials searched (>= ``len(all_trials)`` when
    the caller truncated to top-N); it is the N-floor fed to the deflation so a
    truncated grid cannot make DSR look better than it is.
    """
    n_trials = len(all_trials)
    n_obs = len(chosen.returns)
    min_obs = 2 * n_splits

    if n_trials < 2:
        return CommitGateVerdict(
            DECISION_INSUFFICIENT,
            None,
            None,
            None,
            n_obs,
            n_trials,
            [f"only {n_trials} trial(s); need >= 2 to deflate"],
        )
    if n_obs < min_obs:
        return CommitGateVerdict(
            DECISION_INSUFFICIENT,
            None,
            None,
            None,
            n_obs,
            n_trials,
            [f"{n_obs} trades < {min_obs} (2x n_splits) — stats unstable"],
        )

    sr = _trial_sharpe(chosen.returns)
    trial_srs = [_trial_sharpe(t.returns) for t in all_trials]
    dsr = deflated_sharpe_ratio(
        sr,
        n_obs,
        n_trials=max(n_grid, n_trials),
        sr_variance=statistics.variance(trial_srs),
    )
    min_trl = min_track_record_length(sr, confidence=mintrl_confidence)
    perf = _build_perf_matrix(all_trials, min_obs)
    pbo = cscv_pbo(perf, n_splits=n_splits).pbo

    decision, reasons = _decide(
        dsr=dsr,
        pbo=pbo,
        min_trl=min_trl,
        n_obs=n_obs,
        dsr_threshold=dsr_threshold,
        pbo_threshold=pbo_threshold,
    )
    return CommitGateVerdict(decision, dsr, pbo, min_trl, n_obs, n_trials, reasons)
