"""The Sharpe primitive every sleeve feeds into the gate — one definition.

``per_period_sharpe`` was copied byte-identically into ``analytics.forecast.report``
and ``analytics.xsmom.report``, with a third copy inlined inside
``analytics.xsmom.diagnostics._ann_sharpe`` — three copies, no shared import and no
cross-reference comment. That is not a cosmetic duplication: its output is the
``sr_d`` that goes straight into :func:`deflated_sharpe_ratio` and the bootstrap
statistic behind ``boot_lo``, i.e. two of the three legs
:func:`analytics.research_guards.passes_sleeve_gate` compares ACROSS sleeves. Three
copies means a numeric change to one — loosening the ``1e-12`` degenerate-sd guard,
or switching ``ddof`` — silently desynchronises the very numbers the gate exists to
compare, and neither mypy nor the suite can see it.

**The two annualisation conventions are NOT interchangeable, and they already
collide by name.** ``ann_sharpe`` here takes an ``ann_factor`` that is ALREADY
``sqrt(periods_per_year)`` — both report modules compute
``math.sqrt(cfg.annualization_days)`` and pass that.
``analytics.xsmom.diagnostics`` has a private ``_ann_sharpe`` whose second argument
is raw ``ann_days`` and which square-roots internally, so the two functions share a
name and a shape while meaning different things. Importing the wrong one changes
every number it touches by a ``sqrt`` factor — about 15.9x at 252 — and nothing
would fail. Diagnostics keeps its wrapper for that reason; it now delegates the
per-period half here and applies its own ``sqrt`` visibly at the boundary.

⚠ **This is NOT the one Sharpe in the repo, and unifying the others is a BEHAVIOUR
CHANGE, not a cleanup.** Five siblings compute a mean/sd Sharpe and are deliberately
left alone, because they disagree on the degenerate-input contract in ways that are
load-bearing for their own callers:

===================================== ==================== =========================
Function                              Degenerate guard     Why it differs
===================================== ==================== =========================
``audit_guard._slice_sharpe``         ``sd == 0.0``        returns **±inf** for a
                                                           zero-variance non-zero
                                                           mean, on purpose: a
                                                           deterministic edge has no
                                                           sampling uncertainty
``sweep_guard._trial_sharpe``         ``sd == 0.0``        stricter than ``1e-12``
``research_guards.pbo._sharpe``       ``sd == 0.0``        stricter than ``1e-12``
``backtest.stats_overfit.sharpe_ratio`` ``std == 0.0``     shares ``_moments`` with
                                                           the skew/kurt PSR needs
``forecast.metrics.sharpe``           ``sd < 1e-10``       pandas curve input, and
                                                           annualises internally
===================================== ==================== =========================

Three different thresholds are in play (exact ``0.0``, ``1e-12``, ``1e-10``). Only
the ``1e-12`` group is this function. Routing any of the others through here would
change results on inputs where the guards disagree, so it needs its own evidence and
its own PR — see ``tests/test_research_guards_sharpe.py`` for the pins.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


def per_period_sharpe(r: npt.NDArray[np.float64]) -> float:
    """Mean / sd of ``r``, in the units ``r`` is sampled at. No annualisation.

    Returns ``0.0`` rather than raising or emitting NaN for the two degenerate
    inputs — fewer than two observations, and a standard deviation below
    ``1e-12``. That floor runs in the opposite direction to the one people
    expect: it rejects only a (near-)exactly flat series, so a set of returns
    that is merely tightly clustered still produces a finite, enormous Sharpe.
    There is no dispersion floor here, and adding one would be a behaviour
    change needing its own evidence.
    """
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd)


def ann_sharpe(r: npt.NDArray[np.float64], ann_factor: float) -> float:
    """Annualised Sharpe. ``ann_factor`` is ALREADY ``sqrt(periods_per_year)``.

    Pass ``math.sqrt(cfg.annualization_days)``, never ``annualization_days``.
    """
    return per_period_sharpe(r) * ann_factor
