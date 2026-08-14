"""Velocity-alternation sleeve (edge-hunt #6, equity-native, thesis H-007).

Tests whether the **pace** of a decline predicts the pace of the next move — a
slow grind lower followed by a sharp rally, a sharp drop by a slow recovery —
cross-sectionally over the breadth universe, on 1d bars.

The construction decomposes ``velocity = depth / duration`` and books both
components as separate control arms, because the thesis' only novel claim is that
the ratio carries information neither component does.

Read-only: no DB writes, no schema change, no detector, no dispatch. Under the
TA freeze this is research, not a signal surface (the #183 precedent: a
measurement that changes no dispatch is outside the freeze).
"""

from analytics.velocity.replay import (
    COMMITTED_KEY,
    CONTROL_KEYS,
    replay_velocity_grid,
    velocity_market_return,
)
from analytics.velocity.report import (
    VelocityGridReport,
    evaluate_velocity_grid,
    mean_gross_turnover,
)
from analytics.velocity.signals import (
    LOOKBACK,
    MIN_DEPTH,
    MIN_DURATION,
    cross_sectional_long_score,
    decline_metrics,
    depth_score,
    duration_score,
    eligible_count,
    velocity_score,
)

__all__ = [
    "COMMITTED_KEY",
    "CONTROL_KEYS",
    "LOOKBACK",
    "MIN_DEPTH",
    "MIN_DURATION",
    "VelocityGridReport",
    "cross_sectional_long_score",
    "decline_metrics",
    "depth_score",
    "duration_score",
    "eligible_count",
    "evaluate_velocity_grid",
    "mean_gross_turnover",
    "replay_velocity_grid",
    "velocity_market_return",
    "velocity_score",
]
