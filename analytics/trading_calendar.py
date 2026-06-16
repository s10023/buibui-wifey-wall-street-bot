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

_CALENDAR_NAME = "XNYS"  # exchange_calendars' code for NYSE


@functools.lru_cache(maxsize=1)
def _calendar() -> Any:
    """Process-lifetime cached XNYS calendar handle (construction is non-trivial)."""
    return xcals.get_calendar(_CALENDAR_NAME)


def nyse_sessions(start: date, end: date) -> list[date]:
    """NYSE trading-session dates in ``[start, end]`` inclusive, sorted ascending.

    Excludes weekends and NYSE holidays. Returns ``[]`` when ``end < start``.
    """
    if end < start:
        return []
    sessions = _calendar().sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    result: list[date] = [ts.date() for ts in sessions]
    return result
