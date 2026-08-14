"""Gap-fill "magnet" sleeve (edge-hunt #5, equity-native).

Tests whether an unfilled overnight price gap acts as a magnet — whether price
drifts toward a nearby unfilled gap edge — cross-sectionally over the breadth
universe, on 1d bars.

Read-only: no DB writes, no schema change, no detector, no dispatch. Under the
TA freeze this is research, not a signal surface (the #183 precedent: a
measurement that changes no dispatch is outside the freeze).
"""

from analytics.gapfill.replay import (
    gapfill_market_return,
    load_daily_ohlc,
    replay_gapfill_grid,
)
from analytics.gapfill.report import GapfillGridReport, evaluate_gapfill_grid
from analytics.gapfill.signals import (
    GAP_MAX_AGE,
    MAX_DIST_SIGMA,
    MIN_GAP_SIGMA,
    cross_sectional_long_score,
    gap_fill_stats,
    magnet_score,
    nearest_unfilled_level,
    range_regime_mask,
    reversal_score,
)

__all__ = [
    "GAP_MAX_AGE",
    "MAX_DIST_SIGMA",
    "MIN_GAP_SIGMA",
    "GapfillGridReport",
    "cross_sectional_long_score",
    "evaluate_gapfill_grid",
    "gap_fill_stats",
    "gapfill_market_return",
    "load_daily_ohlc",
    "magnet_score",
    "nearest_unfilled_level",
    "range_regime_mask",
    "replay_gapfill_grid",
    "reversal_score",
]
