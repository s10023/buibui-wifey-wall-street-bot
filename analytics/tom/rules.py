"""TOM's rule and frame: hold the market over the turn of the month, ``RF`` otherwise.

Frozen in the edge-pillars spec § Amendment 2 (#422). A session is in the
window when it is the last session of its calendar month or one of the first
three, so the book enters at the close before the month's last session and
exits at the close of the next month's third. Membership reads the calendar
alone, never a return, which is what makes the rule causal by construction.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd

#: Sessions held at the start of each month, beside the previous month's last.
FIRST_SESSIONS = 3

PRIMARY_START = pd.Timestamp("1988-01-01")
SECONDARY_START = pd.Timestamp("2006-01-01")


def tom_position(dates: pd.DatetimeIndex) -> pd.Series:
    """1.0 on each window session of ``dates`` (a session calendar), else 0.0.

    The calendar must run past any session whose label matters: the last
    session in ``dates`` always reads as its month's last, because nothing
    after it is visible. :func:`complete_months` removes that edge.
    """
    month = dates.to_period("M")
    ranks = pd.Series(range(len(dates)), index=dates).groupby(month)
    from_start = ranks.cumcount()
    from_end = ranks.cumcount(ascending=False)
    pos = ((from_start < FIRST_SESSIONS) | (from_end == 0)).astype(float)
    return pd.Series(pos.to_numpy(), index=dates, name="pos")


def complete_months(
    dates: pd.DatetimeIndex, sessions_fn: Callable[[date, date], list[date]]
) -> pd.DatetimeIndex:
    """``dates`` without a trailing month the calendar has not finished.

    ``sessions_fn(start, end)`` is the exchange schedule (inclusive). When it
    lists a session after the last date but in the same month, that month's
    last session is not in ``dates`` yet, so the whole month is cut.
    """
    last = dates[-1].date()
    month_end = (pd.Timestamp(last) + pd.offsets.MonthEnd(0)).date()
    later = [d for d in sessions_fn(last + timedelta(days=1), month_end) if d > last]
    if not later:
        return dates
    return dates[dates.to_period("M") < pd.Period(last, "M")]


def tom_frame(
    ff: pd.DataFrame,
    *,
    start: pd.Timestamp,
    sessions_fn: Callable[[date, date], list[date]],
    lag: int = 0,
) -> pd.DataFrame:
    """Columns ``mkt``, ``rf`` and ``pos`` from the French daily file, from ``start``.

    The window is labelled on the file's whole calendar before the panel is
    cut, so the panel's first month is labelled like any other. ``lag`` shifts
    the position that many sessions later (the execution-lag sensitivity).
    """
    dates = complete_months(pd.DatetimeIndex(ff.index), sessions_fn)
    pos = tom_position(dates).shift(lag)
    frame = pd.DataFrame(
        {
            "mkt": ff["mkt_rf"].reindex(dates) + ff["rf"].reindex(dates),
            "rf": ff["rf"].reindex(dates),
            "pos": pos,
        }
    )
    frame = frame.loc[frame.index >= start]
    if frame.isna().any().any():
        raise ValueError(
            f"{int(frame.isna().any(axis=1).sum())} panel sessions have a NaN"
        )
    return frame
