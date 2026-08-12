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

**Layer 2 — wifey's five-leg sleeve composition** (:func:`passes_sleeve_gate`).
The four equity sleeves (``xsmom``, ``lowvol``, ``xasset``, ``pead``) each add
two more legs to the three above::

    AND n_obs >= min_trl  AND  sharpe_annual >= GATE_SHARPE

**This diverges from the parent, which excludes ``min_trl`` on purpose**, and
the divergence has a measured cost. ``min_track_record_length`` is called with
``target_sr`` equal to an annualized Sharpe of **1.0**, so it asks "can I
confirm Sharpe >= 1?" — and returns ``inf`` for any sample whose own Sharpe is
at or below 1.0, because no amount of data confirms a hypothesis the sample
contradicts. The leg therefore imposes an *undeclared* Sharpe bar well above
the declared one:

===========  ==========================================
``n_obs``    effective annualized-Sharpe bar from MinTRL
===========  ==========================================
500          2.174
1000         1.829
2000         1.585
3000         1.478
===========  ==========================================

At wifey's daily depth (~2000 observations) the real bar is **~1.585**, not the
``GATE_SHARPE = 0.7`` the sleeves declare as "pre-registered". A cell scoring
between 0.7 and 1.58 clears every bar the code names and is rejected by one it
does not. :data:`DEPLOY_SHARPE` (1.0) is inert for the same reason: it is only
ever consulted on a cell that already passed, and a passing cell is already
above 1.58.

**Nothing here changes that composition.** The gate is read on four committed
cells (``xsmom``'s residual grid, ``lowvol``, ``xasset``, ``pead``; ``forecast``
computes ``min_trl`` and applies no gate), and each already fails on DSR, PBO,
``boot_lo`` or the Sharpe leg — all of which bind before MinTRL. So no verdict
recorded in ``CLAUDE.md`` rests on it: this is preventive, not a repair.
It is documented rather than removed because dropping a leg changes what a
recorded verdict means, and that is a research decision, not a refactor.
"""

from __future__ import annotations

import math

GATE_DSR = 0.95
"""Deflated Sharpe floor — the probability the Sharpe survives multiple testing."""

GATE_PBO = 0.5
"""Probability-of-backtest-overfitting ceiling, from CSCV."""

GATE_SHARPE = 0.7
"""Pre-registered net-of-cost annualized Sharpe bar for a sleeve's committed cell.

Declared by all four equity sleeves. See the module docstring: the MinTRL leg in
:func:`passes_sleeve_gate` makes the *effective* bar ~1.585 at n=2000.
"""

DEPLOY_SHARPE = 1.0
"""Deploy-grade tier annotation — **not** the pass/fail line.

Consulted only on a cell that already passed :func:`passes_sleeve_gate`, which
is why it currently cannot discriminate anything.
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
    n_obs: float,
    min_trl: float,
    sharpe_annual: float,
    gate_sharpe: float = GATE_SHARPE,
) -> bool:
    """wifey's five-leg sleeve gate: :func:`passes_gate` plus MinTRL and Sharpe.

    One definition for all four equity sleeves, which previously inlined this
    expression identically four times (and a fifth time inside a test, which is
    why no test could falsify it).

    ``gate_sharpe`` is a parameter rather than a constant read so a sleeve that
    ever pre-registers a different bar states it at the call site instead of
    shadowing the shared name.

    Read the module docstring before changing the leg set: ``min_trl`` imposes
    an undeclared Sharpe bar far above ``gate_sharpe``.
    """
    if not passes_gate(dsr, pbo, boot_lo):
        return False
    return n_obs >= min_trl and sharpe_annual >= gate_sharpe
