"""Tests for overnight gap detection (replaces CME gap for equities)."""

import pandas as pd
import pytest

from analytics.overnight_gap_lib import (
    OvernightGap,
    gap_fill_warning,
    get_overnight_gap,
)


def _make_df(rows: list[dict]) -> pd.DataFrame:  # type: ignore[type-arg]
    return pd.DataFrame(rows)


def test_gap_up_detected() -> None:
    df = _make_df(
        [
            {
                "open_time": 1,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1e6,
                "vwap": 100.5,
            },
            {
                "open_time": 2,
                "open": 103.0,
                "high": 105.0,
                "low": 102.0,
                "close": 104.0,
                "volume": 1e6,
                "vwap": 103.5,
            },
        ]
    )
    gap = get_overnight_gap(df)
    assert gap is not None
    assert gap.gap_up is True
    assert gap.prev_close == pytest.approx(101.0)
    assert gap.today_open == pytest.approx(103.0)
    assert gap.gap_pct == pytest.approx((103.0 - 101.0) / 101.0)


def test_gap_down_detected() -> None:
    df = _make_df(
        [
            {
                "open_time": 1,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1e6,
                "vwap": 100.5,
            },
            {
                "open_time": 2,
                "open": 98.0,
                "high": 99.0,
                "low": 97.0,
                "close": 98.5,
                "volume": 1e6,
                "vwap": 98.2,
            },
        ]
    )
    gap = get_overnight_gap(df)
    assert gap is not None
    assert gap.gap_up is False
    assert gap.gap_pct == pytest.approx((98.0 - 101.0) / 101.0)


def test_gap_up_filled_when_low_touches_prev_close() -> None:
    df = _make_df(
        [
            {
                "open_time": 1,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1e6,
                "vwap": 100.5,
            },
            {
                "open_time": 2,
                "open": 105.0,
                "high": 107.0,
                "low": 101.0,
                "close": 106.0,
                "volume": 1e6,
                "vwap": 105.0,
            },
        ]
    )
    gap = get_overnight_gap(df)
    assert gap is not None
    assert gap.filled is True


def test_gap_up_not_filled_when_low_above_prev_close() -> None:
    df = _make_df(
        [
            {
                "open_time": 1,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1e6,
                "vwap": 100.5,
            },
            {
                "open_time": 2,
                "open": 105.0,
                "high": 107.0,
                "low": 103.0,
                "close": 106.0,
                "volume": 1e6,
                "vwap": 105.0,
            },
        ]
    )
    gap = get_overnight_gap(df)
    assert gap is not None
    assert gap.filled is False


def test_returns_none_when_fewer_than_two_rows() -> None:
    df = _make_df(
        [
            {
                "open_time": 1,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1e6,
                "vwap": 100.5,
            },
        ]
    )
    assert get_overnight_gap(df) is None


def test_gap_fill_warning_long_below_unfilled_gap_down() -> None:
    gap = OvernightGap(
        gap_pct=-0.02, gap_up=False, filled=False, prev_close=101.0, today_open=99.0
    )
    warning = gap_fill_warning(gap, direction="long", entry=100.0)
    assert warning is not None
    assert "101" in warning


def test_gap_fill_warning_none_when_gap_filled() -> None:
    gap = OvernightGap(
        gap_pct=-0.02, gap_up=False, filled=True, prev_close=101.0, today_open=99.0
    )
    assert gap_fill_warning(gap, direction="long", entry=100.0) is None


def test_gap_fill_warning_short_above_unfilled_gap_up() -> None:
    gap = OvernightGap(
        gap_pct=0.02, gap_up=True, filled=False, prev_close=99.0, today_open=101.0
    )
    warning = gap_fill_warning(gap, direction="short", entry=100.0)
    assert warning is not None
