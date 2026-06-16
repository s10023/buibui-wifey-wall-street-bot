"""Tests for analytics/data_quality.py."""

from datetime import date

import pandas as pd

from analytics.data_quality import (
    DataQualityReport,
    SessionGapReport,
    check_ohlcv,
    detect_session_gaps,
    quarantine,
)

_COLS = ["symbol", "timeframe", "open_time", "open", "high", "low", "close", "volume"]


def _frame(rows: list[dict]) -> pd.DataFrame:
    """Build an OHLCV frame; each row dict overrides the clean defaults."""
    base = {
        "symbol": "AAPL",
        "timeframe": "1d",
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }
    out = []
    for i, r in enumerate(rows):
        row = {**base, "open_time": 1_000 + i * 86_400_000}
        row.update(r)
        out.append(row)
    return pd.DataFrame(out, columns=_COLS)


def test_clean_frame_is_clean() -> None:
    rep = check_ohlcv(_frame([{}, {}, {}]))
    assert isinstance(rep, DataQualityReport)
    assert rep.n_rows == 3
    assert rep.quarantine_idx == ()
    assert rep.is_clean is True


def test_nan_price_is_quarantined() -> None:
    rep = check_ohlcv(_frame([{}, {"close": float("nan")}, {}]))
    assert rep.nan_idx == (1,)
    assert 1 in rep.quarantine_idx
    assert rep.is_clean is False


def test_nonpositive_price_is_quarantined() -> None:
    rep = check_ohlcv(_frame([{}, {"low": 0.0}, {"open": -5.0}]))
    assert rep.nonpositive_price_idx == (1, 2)
    assert set(rep.quarantine_idx) == {1, 2}


def test_broken_bar_geometry_is_quarantined() -> None:
    # high below low; and high below open
    rep = check_ohlcv(_frame([{"high": 98.0}, {"high": 100.5, "open": 101.0}]))
    assert set(rep.bad_bar_idx) == {0, 1}
    assert set(rep.quarantine_idx) == {0, 1}


def test_empty_frame() -> None:
    rep = check_ohlcv(_frame([]))
    assert rep.n_rows == 0
    assert rep.quarantine_idx == ()
    assert rep.is_clean is False


def test_duplicate_timestamp_quarantines_later_row() -> None:
    df = _frame([{}, {}, {}])
    df.loc[2, "open_time"] = df.loc[1, "open_time"]  # row 2 duplicates row 1
    rep = check_ohlcv(df)
    assert rep.duplicate_time_idx == (2,)
    assert 2 in rep.quarantine_idx


def test_nonmonotonic_timestamp_is_warned_not_dropped() -> None:
    df = _frame([{}, {}, {}])
    # row 0's open_time is 1_000 (see _frame); 0 is earlier than every row -> backwards
    df.loc[2, "open_time"] = 0
    rep = check_ohlcv(df)
    assert rep.nonmonotonic_idx == (2,)
    assert 2 not in rep.quarantine_idx  # warn-only


def test_zero_volume_is_warned_not_dropped() -> None:
    rep = check_ohlcv(_frame([{}, {"volume": 0.0}, {}]))
    assert rep.zero_volume_idx == (1,)
    assert 1 not in rep.quarantine_idx


def test_return_outlier_flagged() -> None:
    # row 1 close jumps +80% vs row 0 (101 -> 181.8): outlier, not split-like
    rep = check_ohlcv(
        _frame([{}, {"open": 180.0, "high": 182.0, "low": 179.0, "close": 181.8}, {}])
    )
    assert 1 in rep.return_outlier_idx
    assert 1 not in rep.suspected_split_idx


def test_suspected_split_flagged() -> None:
    # row 1 close halves vs row 0 (101 -> ~50.5): ratio ~0.5 == 2:1 split
    rep = check_ohlcv(
        _frame([{}, {"open": 50.0, "high": 51.0, "low": 49.5, "close": 50.5}, {}])
    )
    assert 1 in rep.suspected_split_idx


def test_normal_move_not_flagged() -> None:
    # +3% day is neither outlier nor split
    rep = check_ohlcv(
        _frame([{}, {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0}, {}])
    )
    assert rep.return_outlier_idx == ()
    assert rep.suspected_split_idx == ()


def test_quarantine_drops_only_bad_rows() -> None:
    df = _frame([{}, {"close": float("nan")}, {}, {"low": 0.0}])
    rep = check_ohlcv(df)
    clean, dropped = quarantine(df, rep)
    assert len(clean) == 2
    assert len(dropped) == 2
    assert clean["close"].tolist() == [101.0, 101.0]
    # input frame is untouched
    assert len(df) == 4


def test_quarantine_clean_frame_is_passthrough() -> None:
    df = _frame([{}, {}, {}])
    rep = check_ohlcv(df)
    clean, dropped = quarantine(df, rep)
    assert len(clean) == 3
    assert dropped.empty
    assert list(clean.columns) == list(df.columns)


def _ot(d: date, hour_utc: int = 12) -> int:
    """Epoch ms for date ``d`` at ``hour_utc`` UTC.

    Noon UTC is the same America/New_York calendar date everywhere in CONUS
    year-round, so _et_date(_ot(d)) == d regardless of DST.
    """
    ts = pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=hour_utc, tz="UTC")
    return int(ts.value // 1_000_000)


class TestDetectSessionGaps:
    def test_no_gap_daily(self) -> None:
        days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        rep = detect_session_gaps([_ot(d) for d in days], "1d", days)
        assert isinstance(rep, SessionGapReport)
        assert rep.unit == "session"
        assert rep.has_gaps is False
        assert rep.missing == ()
        assert rep.n_present == 3
        assert rep.n_expected == 3

    def test_missing_daily_session(self) -> None:
        present = [date(2024, 1, 2), date(2024, 1, 4)]
        sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        rep = detect_session_gaps([_ot(d) for d in present], "1d", sessions)
        assert rep.has_gaps is True
        assert rep.missing == (date(2024, 1, 3),)
        assert rep.n_missing == 1

    def test_4h_missing_whole_day(self) -> None:
        # two 4h bars on D0 and D2, none on D1 -> D1 is a missing session
        d0, d1, d2 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
        ots = [_ot(d0, 14), _ot(d0, 18), _ot(d2, 14), _ot(d2, 18)]
        rep = detect_session_gaps(ots, "4h", [d0, d1, d2])
        assert rep.unit == "session"
        assert rep.missing == (d1,)

    def test_weekly_missing_week(self) -> None:
        # weekly bars anchored on Mondays of W0 and W2; a session in W1 -> W1 missing
        w0 = date(2024, 1, 1)  # Monday
        w1_session = date(2024, 1, 9)  # Tue in week of Jan 8
        w2 = date(2024, 1, 15)  # Monday
        sessions = [w0, w1_session, w2]
        rep = detect_session_gaps([_ot(w0), _ot(w2)], "1wk", sessions)
        assert rep.unit == "week"
        assert rep.missing == (date(2024, 1, 8),)  # Monday of the skipped week

    def test_empty_is_no_gap(self) -> None:
        rep = detect_session_gaps([], "1d", [])
        assert rep.n_present == 0
        assert rep.has_gaps is False
        assert rep.missing == ()

    def test_single_bar_no_gap(self) -> None:
        d = date(2024, 1, 2)
        rep = detect_session_gaps([_ot(d)], "1d", [d])
        assert rep.has_gaps is False

    def test_expected_clamped_to_observed_range(self) -> None:
        # sessions list extends past the observed bars; only interior gaps count
        present = [date(2024, 1, 3), date(2024, 1, 4)]
        sessions = [
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
            date(2024, 1, 5),
        ]
        rep = detect_session_gaps([_ot(d) for d in present], "1d", sessions)
        assert rep.has_gaps is False  # Jan 2 and Jan 5 are outside [min,max] present

    def test_summary_mentions_counts(self) -> None:
        present = [date(2024, 1, 2), date(2024, 1, 4)]
        sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        rep = detect_session_gaps([_ot(d) for d in present], "1d", sessions)
        assert "1" in rep.summary()
        assert "gap" in rep.summary().lower()
