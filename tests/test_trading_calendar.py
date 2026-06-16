"""Tests for analytics/trading_calendar.py (real XNYS calendar)."""

from datetime import date

import pandas as pd

from analytics.trading_calendar import check_session_gaps, nyse_sessions


def _ohlcv(open_times: list[int], timeframe: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "timeframe": timeframe,
                "open_time": t,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000_000.0,
            }
            for t in open_times
        ]
    )


def _ot(d: date, hour_utc: int = 12) -> int:
    ts = pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=hour_utc, tz="UTC")
    return int(ts.value // 1_000_000)


def test_excludes_weekends_and_holidays() -> None:
    sessions = set(nyse_sessions(date(2024, 1, 1), date(2024, 1, 31)))
    assert date(2024, 1, 1) not in sessions  # New Year's Day (holiday)
    assert date(2024, 1, 2) in sessions  # first trading day of 2024
    assert date(2024, 1, 6) not in sessions  # Saturday
    assert date(2024, 1, 7) not in sessions  # Sunday
    assert date(2024, 1, 15) not in sessions  # MLK Day (holiday)
    assert date(2024, 1, 16) in sessions  # trading day


def test_returns_sorted_ascending() -> None:
    sessions = nyse_sessions(date(2024, 7, 1), date(2024, 7, 12))
    assert sessions == sorted(sessions)
    assert date(2024, 7, 4) not in sessions  # Independence Day


def test_single_trading_day() -> None:
    assert nyse_sessions(date(2024, 7, 3), date(2024, 7, 3)) == [date(2024, 7, 3)]


def test_single_holiday_is_empty() -> None:
    assert nyse_sessions(date(2024, 7, 4), date(2024, 7, 4)) == []


def test_end_before_start_is_empty() -> None:
    assert nyse_sessions(date(2024, 7, 10), date(2024, 7, 1)) == []


def test_out_of_bounds_dates_dont_raise() -> None:
    # 1970 is before the calendar's first_session — must clamp to [], not raise
    # (gap detection is warn-only and must never crash ingestion).
    assert nyse_sessions(date(1970, 1, 1), date(1970, 1, 31)) == []


def test_check_session_gaps_out_of_bounds_frame_no_crash() -> None:
    # epoch-ms near 1970 (pre-calendar) -> no sessions, no gaps, no exception
    df = _ohlcv([1_000, 2_000, 3_000], "1d")
    rep = check_session_gaps(df, "1d")
    assert rep.has_gaps is False


def test_check_session_gaps_flags_missing_trading_day() -> None:
    # Jan 2, 3, 4 2024 are all NYSE trading days; drop Jan 3 -> one gap
    df = _ohlcv([_ot(date(2024, 1, 2)), _ot(date(2024, 1, 4))], "1d")
    rep = check_session_gaps(df, "1d")
    assert rep.has_gaps is True
    assert date(2024, 1, 3) in rep.missing


def test_check_session_gaps_clean_contiguous_range() -> None:
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    df = _ohlcv([_ot(d) for d in days], "1d")
    rep = check_session_gaps(df, "1d")
    assert rep.has_gaps is False


def test_check_session_gaps_ignores_weekend_absence() -> None:
    # Fri Jan 5 -> Mon Jan 8: the weekend is NOT a gap (no sessions Sat/Sun)
    df = _ohlcv([_ot(date(2024, 1, 5)), _ot(date(2024, 1, 8))], "1d")
    rep = check_session_gaps(df, "1d")
    assert rep.has_gaps is False


def test_check_session_gaps_empty_frame() -> None:
    rep = check_session_gaps(_ohlcv([], "1d"), "1d")
    assert rep.n_present == 0
    assert rep.has_gaps is False
