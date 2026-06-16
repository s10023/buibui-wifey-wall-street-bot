"""Tests for analytics/trading_calendar.py (real XNYS calendar)."""

from datetime import date

from analytics.trading_calendar import nyse_sessions


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
