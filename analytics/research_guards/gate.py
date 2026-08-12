"""The sleeve acceptance gate — one definition, so it cannot drift per sleeve.

Two layers live here, and the split is the point of the module.

**Layer 1 — the published three-leg gate** (shared with the parent repo)::

    DSR >= 0.95  AND  PBO <= 0.5  AND  boot_lo > 0

These three legs and their thresholds are the parent's
``analytics/research_guards/gate.py`` verbatim (parent #586). Keeping them
identical is deliberate: a sleeve measured in either repo should be judged by
the same statistical bar, and a threshold that is restated rather than imported
is the failure mode this project keeps meeting — a bare number that looks
portable, is copied, and then silently means something else.

**Layer 2 — wifey's four-leg sleeve composition** (:func:`passes_sleeve_gate`).
The four equity sleeves (``xsmom``, ``lowvol``, ``xasset``, ``pead``) add one
economic leg to the three statistical ones above::

    AND sharpe_annual >= GATE_SHARPE

Each leg now does distinct work: DSR is multiplicity-corrected significance,
PBO is overfitting, ``boot_lo`` is the interval, ``GATE_SHARPE`` is the
economic bar. ``min_track_record_length`` is still computed and reported beside
them as **a stamp that gates nothing** — same as the parent.

**Why MinTRL is NOT a leg** (decided 2026-08-12, user call; it *was* one until
then). The sleeves called it with ``target_sr`` equal to an annualized Sharpe of
**1.0**, so it asked "can I confirm Sharpe >= 1?" and returned ``inf`` for any
sample at or below that target — no amount of data confirms a hypothesis the
sample contradicts. As a pass condition it imposed an *undeclared* bar far above
the declared ``GATE_SHARPE = 0.7``:

===========  ==========================================
``n_obs``    effective annualized-Sharpe bar from MinTRL
===========  ==========================================
500          2.174
1000         1.829
2000         1.585
3000         1.478
===========  ==========================================

At wifey's daily depth (~2000 observations) the real bar was **~1.585**. A cell
scoring 0.7–1.58 cleared every threshold the code named and was rejected by one
it did not, and :data:`DEPLOY_SHARPE` (1.0) could never discriminate, since it is
only consulted on a cell that already passed.

**Re-targeting to ``target_sr = 0`` was considered and REJECTED as a no-op, not
adopted.** MinTRL round-trips with PSR (``mintrl.py``'s own docstring), so
``min_trl(0) <= n_obs`` is exactly ``PSR(benchmark=0) >= 0.95``; and
:func:`~analytics.research_guards.deflated_sharpe_ratio` *is* PSR with the
benchmark set to the expected-max Sharpe, which is never negative. So
``DSR >= 0.95`` strictly implies ``min_trl(0) <= n_obs`` — the leg could never
bind. Measured over 300,000 random draws: of the 124,882 clearing DSR, **zero**
would have been blocked. A guard that cannot fire is worse than no guard,
because it reads as protection.

**This change is verdict-neutral on every recorded result.** The gate is read on
four committed cells (``forecast`` computes ``min_trl`` and applies no gate), and
each fails on two or more of the remaining legs — see
``tests/test_research_guards_gate.py::TestRecordedVerdictsAreUnchanged``, which
pins that against the published per-sleeve numbers. Dropping the leg also
*revives* :data:`DEPLOY_SHARPE`: with the effective bar back at 0.7, the 1.0 tier
annotation discriminates again.
"""

from __future__ import annotations

import math

GATE_DSR = 0.95
"""Deflated Sharpe floor — the probability the Sharpe survives multiple testing."""

GATE_PBO = 0.5
"""Probability-of-backtest-overfitting ceiling, from CSCV."""

GATE_SHARPE = 0.7
"""Pre-registered net-of-cost annualized Sharpe bar for a sleeve's committed cell.

Declared by all four equity sleeves, and since 2026-08-12 this is also the
**effective** bar — the MinTRL leg that used to raise it to ~1.585 was dropped.
"""

DEPLOY_SHARPE = 1.0
"""Deploy-grade tier annotation — **not** the pass/fail line.

Consulted only on a cell that already passed :func:`passes_sleeve_gate`. That
made it inert while the MinTRL leg held the effective bar at ~1.585; with the
bar back at :data:`GATE_SHARPE` it discriminates again.
"""


def passes_gate(dsr: float, pbo: float, boot_lo: float) -> bool:
    """The published three-leg gate. ``True`` only if all three legs clear.

    A NaN ``pbo`` fails rather than propagating: CSCV returns NaN when the
    trial matrix is too small to split, and ``float('nan') <= 0.5`` is
    ``False`` in Python anyway — this makes that intent explicit instead of
    leaving a passing verdict resting on IEEE comparison semantics.

    ``dsr`` and ``boot_lo`` are compared directly. A NaN in either also fails,
    for the same reason and by the same mechanism.
    """
    if math.isnan(pbo):
        return False
    return dsr >= GATE_DSR and pbo <= GATE_PBO and boot_lo > 0.0


def passes_sleeve_gate(
    *,
    dsr: float,
    pbo: float,
    boot_lo: float,
    sharpe_annual: float,
    gate_sharpe: float = GATE_SHARPE,
) -> bool:
    """wifey's four-leg sleeve gate: :func:`passes_gate` plus the Sharpe bar.

    One definition for all four equity sleeves, which previously inlined this
    expression identically four times (and a fifth time inside a test, which is
    why no test could falsify it).

    ``gate_sharpe`` is a parameter rather than a constant read so a sleeve that
    ever pre-registers a different bar states it at the call site instead of
    shadowing the shared name.

    **``min_trl`` and ``n_obs`` are deliberately NOT arguments.** MinTRL is a
    reported stamp, not a leg — read the module docstring before re-adding it;
    at ``target_sr`` = Sharpe 1.0 it silently raised the bar to ~1.585, and at
    ``target_sr`` = 0 it is strictly implied by ``dsr`` and can never fire.
    Dropping the parameters rather than ignoring them is what makes a re-add a
    deliberate act: every call site would have to change again.
    """
    if not passes_gate(dsr, pbo, boot_lo):
        return False
    return sharpe_annual >= gate_sharpe
