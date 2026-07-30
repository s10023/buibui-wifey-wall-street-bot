"""StatsBundle orchestrator — calls every per-stat compute function."""

import time
from dataclasses import dataclass

import duckdb

from analytics.stats.adr import ADRResult, compute_adr
from analytics.stats.dow import DOWResult, compute_dow_patterns
from analytics.stats.hourly import HourlyResult, compute_hourly_extremes
from analytics.stats.p1p2 import P1P2Result, compute_p1p2_daily
from analytics.stats.path_cone import PathConeBundle, compute_path_cone
from analytics.stats.session import SessionResult, compute_session_breakdown
from analytics.stats.weekly_cone import WeeklyConeBundle, compute_weekly_cone
from analytics.stats.weekly_flip_risk import (
    WeeklyFlipRiskConditioned,
    compute_weekly_flip_risk_conditioned,
)
from analytics.stats.weekly_p1p2 import WeeklyP1P2Result, compute_weekly_p1p2
from analytics.stats.weekly_p2_timing import WeeklyP2Timing, compute_weekly_p2_timing


@dataclass
class StatsBundle:
    """Complete statistics bundle for one symbol."""

    symbol: str
    days: int
    computed_at_ms: int
    p1p2: P1P2Result
    hourly: HourlyResult
    adr: ADRResult
    dow: DOWResult
    sessions: SessionResult
    weekly_p1p2: WeeklyP1P2Result
    weekly_p2_timing: WeeklyP2Timing
    weekly_flip_risk_conditioned: WeeklyFlipRiskConditioned
    path_cone: PathConeBundle
    weekly_cone: WeeklyConeBundle


def compute_all(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> StatsBundle:
    """Compute all statistics and return a StatsBundle.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    p1p2 = compute_p1p2_daily(conn, symbol, days)
    hourly = compute_hourly_extremes(conn, symbol, days)
    adr = compute_adr(conn, symbol)
    dow = compute_dow_patterns(conn, symbol, days)
    sessions = compute_session_breakdown(conn, symbol, days)
    weekly_p1p2 = compute_weekly_p1p2(conn, symbol, days)
    weekly_p2_timing = compute_weekly_p2_timing(conn, symbol, days)
    weekly_flip_risk_conditioned = compute_weekly_flip_risk_conditioned(
        conn, symbol, days
    )
    path_cone = compute_path_cone(conn, symbol)
    weekly_cone = compute_weekly_cone(conn, symbol)

    return StatsBundle(
        symbol=symbol,
        days=days,
        computed_at_ms=int(time.time() * 1000),
        p1p2=p1p2,
        hourly=hourly,
        adr=adr,
        dow=dow,
        sessions=sessions,
        weekly_p1p2=weekly_p1p2,
        weekly_p2_timing=weekly_p2_timing,
        weekly_flip_risk_conditioned=weekly_flip_risk_conditioned,
        path_cone=path_cone,
        weekly_cone=weekly_cone,
    )
