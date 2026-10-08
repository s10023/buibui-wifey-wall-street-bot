"""Velocity-alternation signal construction (edge-hunt #6, thesis H-007).

THE THESIS, as filed in ``docs/plans/thesis-inbox.md`` (H-007, @fenggemeigu
2026-07-05): *the pace of a decline predicts the pace of the next move — a slow
grind lower tends to be followed by a sharp rally, a sharp drop by a slow
recovery.*

WHAT THAT MAKES TRADEABLE. Both halves predict a recovery; they differ only in
its **pace**. Over a fixed forward horizon a sharp rally pays more than a slow
one, so the cross-sectional bet is **long the slow decliners, short the fast
ones** — i.e. long low velocity.

THE DECOMPOSITION IS THE WHOLE POINT. ``velocity = depth / duration``, so a low
velocity arises from a *shallow* decline or a *long* one. Those are two
completely different, already-known effects:

* shallow-vs-deep is short-term reversal / momentum in disguise;
* long-vs-short is a "how long has this been falling" calendar effect.

H-007 claims something neither of those does — that the **ratio** carries
information its two components do not. So the family below ships ``depth`` and
``duration`` as *separately booked control arms on the identical population*,
and ``report`` prints the committed cell's correlation to each. A ``broad_ls``
that merely tracks either control has found nothing new, whatever its Sharpe.
This is the ``corr_to_reversal`` discipline from edge-hunt #5 and the
``corr_to_trend`` +0.62 read that sank the xsmom sleeve.

PRE-REGISTERED, DECLARED HERE BEFORE ANY RESULT WAS READ:

===========================  =========================================
``LOOKBACK`` 60              formation window, sessions
``MIN_DEPTH`` 0.10           a decline must be >=10% off the window peak
``MIN_DURATION`` 3           ...and have taken >=3 sessions
committed cell               ``broad_ls`` (see ``replay.COMMITTED_KEY``)
gate                         DSR>=0.95 ∧ PBO<=0.5 ∧ boot_lo>0 ∧ Sharpe>=0.7
===========================  =========================================

CAUSALITY. Every score ends in ``.shift(1)``, so day ``d``'s leverage is built
only from information complete at ``d-1``; ``tests/test_lookahead.py``'s rule and
the sleeve causality guards apply. The trailing vol used for sizing is
itself already ``.shift(1)``-ed inside ``beta_neutral_leverage``.

Read-only research: no detector, no dispatch, no DB write — outside the TA
freeze, as a measurement that changes no dispatch).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Single definition, deliberately imported rather than re-inlined: a fifth copy
# of the cross-sectional z is exactly how the sleeve gate's MinTRL leg diverged
# unnoticed across four sites. Positive orientation — high score -> long.
from analytics.gapfill.signals import cross_sectional_long_score

__all__ = [
    "LOOKBACK",
    "MIN_DEPTH",
    "MIN_DURATION",
    "cross_sectional_long_score",
    "decline_metrics",
    "depth_score",
    "duration_score",
    "eligible_count",
    "velocity_score",
]

LOOKBACK = 60
"""Formation window in sessions. A decline is measured against this window's peak."""

MIN_DEPTH = 0.10
"""Minimum drawdown off the window peak for a name to be eligible (10%)."""

MIN_DURATION = 3
"""Minimum sessions since the peak. Below this, `depth/duration` is a noise ratio."""


def _union(bars_by_symbol: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    idx: pd.DatetimeIndex | None = None
    for bars in bars_by_symbol.values():
        cur = pd.DatetimeIndex(bars.index)
        idx = cur if idx is None else idx.union(cur)
    if idx is None:
        return pd.DatetimeIndex([])
    return idx.sort_values()


def decline_metrics(close: pd.Series) -> pd.DataFrame:
    """Per-session ``depth`` / ``duration`` / ``velocity`` for one symbol.

    ``depth``    fractional drawdown from the highest close in the trailing
                 ``LOOKBACK`` window, `(peak - close) / peak`, >= 0.
    ``duration`` sessions elapsed since that peak was set. On a tie the EARLIEST
                 peak wins (``np.argmax`` semantics), giving the longer duration
                 — stated because it is a convention, not a derivation.
    ``velocity`` ``depth / duration`` — mean fractional decline per session.

    Rows with less than ``LOOKBACK`` history, or that fail the eligibility floors
    (``MIN_DEPTH`` / ``MIN_DURATION``), are NaN in all three columns. Keeping one
    eligibility mask for all three is what makes the control arms comparable: the
    thesis is about the ratio, so the controls have to run on the ratio's own
    population, not a wider one.

    NOT shifted — the callers below own the shift, so there is exactly one place
    the causal convention is applied per score.
    """
    roll = close.rolling(LOOKBACK, min_periods=LOOKBACK)
    peak = roll.max()
    pos = roll.apply(lambda a: float(np.argmax(a)), raw=True)
    duration = (LOOKBACK - 1) - pos

    depth = ((peak - close) / peak).replace([np.inf, -np.inf], np.nan)
    eligible = (depth >= MIN_DEPTH) & (duration >= MIN_DURATION)

    out = pd.DataFrame(
        {
            "depth": depth.where(eligible),
            "duration": duration.where(eligible),
        },
        index=close.index,
    )
    out["velocity"] = (out["depth"] / out["duration"]).replace(
        [np.inf, -np.inf], np.nan
    )
    return out


def _score_frame(
    bars_by_symbol: dict[str, pd.DataFrame], column: str, sign: float
) -> pd.DataFrame:
    """Assemble a `.shift(1)`-ed per-symbol score frame from `decline_metrics`."""
    union = _union(bars_by_symbol)
    cols: dict[str, pd.Series] = {}
    for sym, bars in bars_by_symbol.items():
        metric = decline_metrics(bars["close"])[column]
        cols[sym] = (sign * metric).shift(1).reindex(union)
    return pd.DataFrame(cols, index=union)


def velocity_score(bars_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """THE THESIS ARM: ``-velocity``, so a SLOW decline scores high -> long.

    The sign is the whole hypothesis. H-007 says a slow grind lower is followed
    by a sharp rally, so slow (low velocity) is the long side. Inverting this
    inverts the sleeve.
    """
    return _score_frame(bars_by_symbol, "velocity", -1.0)


def depth_score(bars_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """CONTROL ARM 1: ``-depth`` — long the SHALLOW decliners.

    Isolates the magnitude half of ``velocity``. This is short-term
    reversal/momentum wearing different clothes; if the thesis arm correlates
    with it, that is what the sleeve actually found.
    """
    return _score_frame(bars_by_symbol, "depth", -1.0)


def duration_score(bars_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """CONTROL ARM 2: ``+duration`` — long the LONGEST-running decliners.

    Isolates the time half. Sign is positive because a long duration is the other
    route to a low velocity, so this control has to point the same way the thesis
    arm does for the comparison to mean anything.
    """
    return _score_frame(bars_by_symbol, "duration", 1.0)


def eligible_count(bars_by_symbol: dict[str, pd.DataFrame]) -> pd.Series:
    """Eligible names per session — the population the book can actually trade.

    Deliberately a COUNT, not a rate. Edge-hunt #5 shipped "90.3% of gaps fill
    within 60 sessions" and it survived review because it was true; a matched
    placebo filled 88.9%, so the real content was +1.5pp. A rate over a horizon
    needs a matched null before it means anything, and this sleeve does not need
    one, so it does not quote one.
    """
    frame = velocity_score(bars_by_symbol)
    return frame.notna().sum(axis=1)
