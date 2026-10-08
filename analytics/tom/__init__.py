"""TOM: turn-of-the-month market exposure, an edge claim on beta-hedged returns (#422).

Frozen in ``docs/superpowers/specs/2026-10-08-edge-pillars-research-design.md``
§ Amendment 2. Reads only the French daily file, never ``analytics.db``.
"""

from analytics.tom.report import (
    N_TRIALS,
    SR_VARIANCE_ANNUAL,
    TomGate,
    WindowSpread,
    evaluate_tom,
    hedged_ci,
    hedged_returns,
    premise_reading,
    tom_verdict,
    window_spread,
)
from analytics.tom.rules import (
    FIRST_SESSIONS,
    PRIMARY_START,
    SECONDARY_START,
    complete_months,
    tom_frame,
    tom_position,
)

__all__ = [
    "FIRST_SESSIONS",
    "N_TRIALS",
    "PRIMARY_START",
    "SECONDARY_START",
    "SR_VARIANCE_ANNUAL",
    "TomGate",
    "WindowSpread",
    "complete_months",
    "evaluate_tom",
    "hedged_ci",
    "hedged_returns",
    "premise_reading",
    "tom_frame",
    "tom_position",
    "tom_verdict",
    "window_spread",
]
