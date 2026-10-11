"""Tests for analytics/data_quality.py."""

import math
from datetime import date

import pandas as pd
import pytest

from analytics.data_quality import (
    GAPPED_BREAK_LN,
    LARGE_BREAK_LN,
    DataQualityReport,
    SessionGapReport,
    SlotGapReport,
    check_ohlcv,
    classify_level_break,
    detect_session_gaps,
    detect_slot_gaps,
    expected_4h_slots,
    find_level_breaks,
    quarantine,
    quote_disagrees,
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


def _frozen(close: float) -> dict:
    """A forward-filled dead-tape bar: flat OHLC at ``close``, zero volume."""
    return {"open": close, "high": close, "low": close, "close": close, "volume": 0.0}


def _traded(close: float, volume: float = 9_000_000.0) -> dict:
    """A normal bar closing at ``close`` — geometry consistent, real volume."""
    return {
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": volume,
    }


class TestFrozenTail:
    """A stopped tape is quarantined; the same row shape mid-history is not.

    Position is the whole discriminator. Measured 2026-09-02 over the full DB,
    "zero volume AND unchanged close" matches 1,368 rows — 808 ``SW``, 231
    ``AMCR``, 188 ``^GSPC``, all alive and all mid-history — while requiring the
    run to terminate the series leaves 9, every one a name whose tape had
    stopped. Both directions are asserted below, because a quarantine test that
    only shows the drop cannot tell a correct rule from one that drops
    everything.
    """

    def test_trailing_frozen_run_is_quarantined(self) -> None:
        df = _frame(
            [
                _traded(49.0),
                _traded(50.0, 48_700_000.0),
                _frozen(50.0),
                _frozen(50.0),
            ]
        )
        rep = check_ohlcv(df, series_ends_here=True)
        assert rep.frozen_tail_idx == (2, 3)
        assert set(rep.quarantine_idx) >= {2, 3}
        clean, dropped = quarantine(df, rep)
        assert len(clean) == 2
        assert len(dropped) == 2

    def test_interior_frozen_run_is_kept(self) -> None:
        """The ^GSPC / SW / AMCR shape: frozen bars that trading resumes after."""
        df = _frame(
            [
                _traded(50.0),
                _frozen(50.0),
                _frozen(50.0),
                _traded(53.0),
            ]
        )
        rep = check_ohlcv(df, series_ends_here=True)
        assert rep.frozen_tail_idx == ()
        assert rep.quarantine_idx == ()

    def test_series_ends_here_defaults_to_no_quarantine(self) -> None:
        """The paging default: an intermediate page must never drop its tail.

        ``data_sync.backfill`` pages 5000 bars at a time, so without the flag a
        full page's last row is a paging boundary. This is the control that the
        opt-in actually gates something — it is the same frame as the first
        test, and it must come back clean.
        """
        df = _frame(
            [
                _traded(49.0),
                _traded(50.0, 48_700_000.0),
                _frozen(50.0),
                _frozen(50.0),
            ]
        )
        rep = check_ohlcv(df)
        assert rep.frozen_tail_idx == ()
        assert rep.quarantine_idx == ()

    def test_moving_close_at_zero_volume_is_kept(self) -> None:
        """``^TNX`` / ``DX-Y.NYB`` are permanently zero-volume and healthy.

        Keying on zero volume alone would quarantine their whole history, so the
        unchanged close is load-bearing rather than a refinement.
        """
        df = _frame(
            [
                {**_traded(4.10), "volume": 0.0},
                {**_traded(4.18), "volume": 0.0},
                {**_traded(4.05), "volume": 0.0},
            ]
        )
        rep = check_ohlcv(df, series_ends_here=True)
        assert rep.frozen_tail_idx == ()
        assert rep.zero_volume_idx == (0, 1, 2)  # still warned about, never dropped

    def test_wholly_frozen_frame_keeps_its_first_row(self) -> None:
        """Row 0 has no previous close in-frame, so it cannot start a run.

        Failing in the keep direction is deliberate: dropping every row of a
        page would make ``_store_page`` store nothing at all.
        """
        df = _frame([_frozen(50.0), _frozen(50.0), _frozen(50.0)])
        rep = check_ohlcv(df, series_ends_here=True)
        assert rep.frozen_tail_idx == (1, 2)
        clean, _ = quarantine(df, rep)
        assert len(clean) == 1


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


_D = 86_400_000


class TestLevelBreak:
    """#469: the gapped leg flags the two measured wrong instruments and none of
    the benign gapped resumptions; the ungapped leg flags only halvings and
    doublings. Every shape below is a measured row from analytics.db."""

    def test_the_avb_shape_is_a_gapped_break(self) -> None:
        brk = classify_level_break(0, 177.32, 29 * _D, 68.93, "1d")
        assert brk is not None and brk.kind == "gapped"
        assert brk.ratio == pytest.approx(0.3887, abs=1e-4)
        assert brk.gap_days == pytest.approx(29.0)

    def test_the_bny_4h_shape_is_a_gapped_break(self) -> None:
        brk = classify_level_break(0, 10.2, int(104.8 * _D), 140.6, "4h")
        assert brk is not None and brk.kind == "gapped"

    def test_the_1933_bank_holiday_is_not_a_break(self) -> None:
        """The largest benign gapped resumption measured: +16.6% after 12 days."""
        assert classify_level_break(0, 5.84, 12 * _D, 6.81, "1d") is None

    def test_a_gapped_resumption_near_its_level_is_not_a_break(self) -> None:
        """EQR came back from AVB's own 29-day outage 7.7% higher."""
        assert classify_level_break(0, 64.09, 29 * _D, 69.0, "1d") is None

    def test_an_ungapped_doubling_is_large_not_gapped(self) -> None:
        """MRNA +177% on 2026-08-19 is real: it warns, it is not called wrong."""
        brk = classify_level_break(0, 62.96, _D, 174.38, "1d")
        assert brk is not None and brk.kind == "large"

    def test_an_ungapped_move_under_2x_is_silent(self) -> None:
        """An AVB-sized drop with no gap is a real crash's shape (FISV -44%)."""
        assert classify_level_break(0, 100.0, _D, 60.0, "1d") is None

    def test_weekly_needs_more_than_two_weeks_of_gap(self) -> None:
        assert classify_level_break(0, 100.0, 14 * _D, 70.0, "1wk") is None
        brk = classify_level_break(0, 100.0, 21 * _D, 70.0, "1wk")
        assert brk is not None and brk.kind == "gapped"

    def test_an_unknown_timeframe_gets_the_ungapped_leg_only(self) -> None:
        assert classify_level_break(0, 100.0, 30 * _D, 70.0, "5m") is None
        brk = classify_level_break(0, 100.0, 30 * _D, 40.0, "5m")
        assert brk is not None and brk.kind == "large"

    def test_unpriceable_rows_answer_none(self) -> None:
        assert classify_level_break(0, 0.0, 30 * _D, 70.0, "1d") is None
        assert classify_level_break(0, 100.0, 30 * _D, float("nan"), "1d") is None

    def test_thresholds_sit_inside_the_measured_margins(self) -> None:
        """Benign gapped max 0.154 < bar < AVB 0.945; real ungapped breaks at
        ln 1.5 numbered 32 against 7 at ln 2, which is why the bar is ln 2."""
        assert 0.154 < GAPPED_BREAK_LN < 0.945
        assert pytest.approx(math.log(2.0)) == LARGE_BREAK_LN

    def test_find_level_breaks_walks_consecutive_pairs(self) -> None:
        breaks = find_level_breaks(
            [0, _D, 30 * _D, 31 * _D], [100.0, 101.0, 40.0, 41.0], "1d"
        )
        assert [(b.prev_open_time, b.open_time, b.kind) for b in breaks] == [
            (_D, 30 * _D, "gapped")
        ]

    def test_a_series_of_one_bar_has_no_pairs(self) -> None:
        assert find_level_breaks([0], [100.0], "1d") == []


class TestQuoteDisagrees:
    def test_the_avb_history_contradicts_its_quote(self) -> None:
        assert quote_disagrees(68.93, 184.06)

    def test_an_agreeing_quote_does_not(self) -> None:
        assert not quote_disagrees(197.0, 197.0)
        assert not quote_disagrees(197.0, 210.0)

    def test_an_unreadable_quote_is_no_evidence(self) -> None:
        assert not quote_disagrees(68.93, None)
        assert not quote_disagrees(68.93, 0.0)


class TestExpected4hSlots:
    """The pure slot expectation behind `detect_slot_gaps` (#327)."""

    @staticmethod
    def _ms(hh: int, mm: int) -> int:
        return int(pd.Timestamp(2026, 1, 30, hh, mm, tz="UTC").value // 1_000_000)

    def test_regular_winter_session_expects_both_grid_slots(self) -> None:
        got = expected_4h_slots(self._ms(14, 30), self._ms(21, 0))
        assert got == (self._ms(13, 30), self._ms(17, 30))

    def test_regular_summer_session_expects_both_grid_slots(self) -> None:
        got = expected_4h_slots(self._ms(13, 30), self._ms(20, 0))
        assert got == (self._ms(13, 30), self._ms(17, 30))

    def test_early_closes_expect_one_slot_in_either_season(self) -> None:
        assert expected_4h_slots(self._ms(14, 30), self._ms(18, 0)) == (
            self._ms(13, 30),
        )
        assert expected_4h_slots(self._ms(13, 30), self._ms(17, 0)) == (
            self._ms(13, 30),
        )

    def test_detect_ignores_other_timeframes(self) -> None:
        rep = detect_slot_gaps([self._ms(13, 30)], "1d", [])
        assert isinstance(rep, SlotGapReport)
        assert rep.n_expected == 0 and not rep.has_gaps

    def test_summary_names_the_slot_in_utc(self) -> None:
        day = 86_400_000
        d0, d1 = date(2026, 1, 30), date(2026, 1, 31)
        bounds = [
            (d0, self._ms(14, 30), self._ms(21, 0)),
            (d1, self._ms(14, 30) + day, self._ms(21, 0) + day),
        ]
        ots = [self._ms(13, 30), self._ms(13, 30) + day, self._ms(17, 30) + day]
        rep = detect_slot_gaps(ots, "4h", bounds)
        assert rep.missing == (self._ms(17, 30),)
        assert "2026-01-30 17:30Z" in rep.summary()
