"""NYSE trading-calendar wrapper (N3 PR2).

The ONLY module that imports ``exchange_calendars``. It exposes NYSE session
dates so the rest of the codebase can reason about trading sessions without a
direct dependency on the calendar library, and bridges the calendar to the pure
``analytics.data_quality.detect_session_gaps`` math.

No DB, no side effects beyond a process-lifetime cached calendar handle.
"""

from __future__ import annotations

import functools
from datetime import date
from typing import Any

import exchange_calendars as xcals
import pandas as pd

from analytics.data_quality import SessionGapReport, detect_session_gaps
from analytics.data_quality import _et_date as _session_date

_CALENDAR_NAME = "XNYS"  # exchange_calendars' code for NYSE
# Explicit lower bound so coverage is stable, not a rolling "today − 20y" window
# (the library's default). 1990 covers the breadth-universe backfill horizon and
# most daily history; deeper bars clamp out of gap detection gracefully (below).
_CALENDAR_START = "1990-01-01"


@functools.lru_cache(maxsize=1)
def _calendar() -> Any:
    """Process-lifetime cached XNYS calendar handle (construction is non-trivial)."""
    return xcals.get_calendar(_CALENDAR_NAME, start=_CALENDAR_START)


def nyse_sessions(start: date, end: date) -> list[date]:
    """NYSE trading-session dates in ``[start, end]`` inclusive, sorted ascending.

    Excludes weekends and NYSE holidays. Returns ``[]`` when ``end < start``. The
    query is clamped to the calendar's supported ``[first_session, last_session]``
    window so out-of-range dates degrade to fewer/zero sessions rather than
    raising ``DateOutOfBounds`` — gap detection is a warn-only convenience and
    must never crash ingestion.
    """
    if end < start:
        return []
    cal = _calendar()
    lo = max(pd.Timestamp(start), cal.first_session)
    hi = min(pd.Timestamp(end), cal.last_session)
    if hi < lo:
        return []
    sessions = cal.sessions_in_range(lo, hi)
    result: list[date] = [ts.date() for ts in sessions]
    return result


def check_session_gaps(df: pd.DataFrame, timeframe: str) -> SessionGapReport:
    """Calendar-backed bridge: detect missing NYSE sessions in an OHLCV frame.

    Computes the observed ET session-date range, asks the NYSE calendar for the
    trading sessions spanning it, and diffs via the pure
    ``data_quality.detect_session_gaps``. Empty frame -> empty (no-gap) report.
    """
    if df.empty:
        return detect_session_gaps([], timeframe, [])
    open_times = [int(t) for t in df["open_time"].tolist()]
    lo = min(_session_date(t) for t in open_times)
    hi = max(_session_date(t) for t in open_times)
    sessions = nyse_sessions(lo, hi)
    return detect_session_gaps(open_times, timeframe, sessions)
