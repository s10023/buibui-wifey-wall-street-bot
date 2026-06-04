"""Stats package — split from analytics/stats_lib.py."""

from analytics.stats.adr import ADRResult, compute_adr
from analytics.stats.bundle import StatsBundle, compute_all
from analytics.stats.daily_distance import DailyDistanceResult, compute_daily_distance
from analytics.stats.dow import DOWResult, DOWRow, compute_dow_patterns
from analytics.stats.hourly import (
    HourlyExtremeRow,
    HourlyResult,
    compute_hourly_extremes,
)
from analytics.stats.live_outcomes import (
    LiveOutcomeCell,
    LiveOutcomesResult,
    LiveOutcomesRollup,
    LiveOutcomeStrategyRow,
    compute_live_outcomes,
)
from analytics.stats.p1p2 import P1P2Result, compute_p1p2_daily
from analytics.stats.session import (
    SessionResult,
    SessionRow,
    compute_session_breakdown,
)
from analytics.stats.weekly_flip_risk import (
    WeeklyFlipRiskConditioned,
    WeeklyFlipRiskConditionedRow,
    compute_weekly_flip_risk_conditioned,
)
from analytics.stats.weekly_p1p2 import WeeklyP1P2Result, compute_weekly_p1p2
from analytics.stats.weekly_p2_timing import WeeklyP2Timing, compute_weekly_p2_timing
from analytics.stats.weekly_state import (
    WeeklyCurrentState,
    compute_weekly_current_state,
)
from analytics.stats.weekly_wick import (
    WeeklyWickPercentile,
    compute_weekly_wick_percentile,
)

__all__ = [
    "ADRResult",
    "DOWResult",
    "DOWRow",
    "DailyDistanceResult",
    "HourlyExtremeRow",
    "HourlyResult",
    "LiveOutcomeCell",
    "LiveOutcomeStrategyRow",
    "LiveOutcomesResult",
    "LiveOutcomesRollup",
    "P1P2Result",
    "SessionResult",
    "SessionRow",
    "StatsBundle",
    "WeeklyCurrentState",
    "WeeklyFlipRiskConditioned",
    "WeeklyFlipRiskConditionedRow",
    "WeeklyP1P2Result",
    "WeeklyP2Timing",
    "WeeklyWickPercentile",
    "compute_adr",
    "compute_all",
    "compute_daily_distance",
    "compute_dow_patterns",
    "compute_hourly_extremes",
    "compute_live_outcomes",
    "compute_p1p2_daily",
    "compute_session_breakdown",
    "compute_weekly_current_state",
    "compute_weekly_flip_risk_conditioned",
    "compute_weekly_p1p2",
    "compute_weekly_p2_timing",
    "compute_weekly_wick_percentile",
]
