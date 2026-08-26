"""Tests for `tools/freshness_check.py`.

The reading half is machine-local and untestable in CI by construction; the
grading half is pure and is what these cover. Two of these exist specifically
because a green check that has never been red is indistinguishable from one that
cannot be:

- `TestScheduledTierHasTeeth` is the positive control. It constructs the
  perturbation the guard exists to catch and asserts the guard catches it.
- `TestRthDivergenceFromParent` pins the one hard divergence from the parent
  tool. A verbatim port would divide wall-clock by bar length, which on an RTH
  tape overstates `4h` age by 3x and adds a phantom weekend every Monday; this
  asserts the session-based answer, so a future "tidy-up" back to the parent's
  formula fails here rather than in production.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from analytics.backtest.cost_model import BARS_PER_DAY
from tools.freshness_check import (
    BASE_TOLERANCE_SESSIONS,
    SCHEDULED_GAP_SESSIONS,
    SCHEDULED_TIMEFRAMES,
    Series,
    evaluate_ohlcv,
    evaluate_signal,
    grade,
    latest_session,
    read_scheduled_symbols,
    read_watermarks,
    sessions_elapsed,
    tolerance_sessions_for,
)


# A deterministic weekday-only calendar. Deliberately not the real NYSE calendar:
# these tests are about the grading rule, and a holiday moving would otherwise
# change their meaning silently.
def _weekday_sessions(start: date, end: date) -> list[date]:
    if end < start:
        return []
    out: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


# 2026-08-20 is a Thursday; 2026-08-24 the following Monday.
THU = date(2026, 8, 20)
FRI = date(2026, 8, 21)
MON = date(2026, 8, 24)
TUE = date(2026, 8, 25)


def _ms(day: date, hour: int = 13, minute: int = 30) -> int:
    """Epoch ms for an RTH bar on `day` (13:30 UTC = the first 4h bin, EDT)."""
    return int(
        datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC).timestamp()
        * 1000
    )


class TestSessionsElapsed:
    def test_same_session_is_zero(self) -> None:
        assert sessions_elapsed(THU, THU, _weekday_sessions) == 0

    def test_weekend_counts_one_session_not_three_days(self) -> None:
        """Fri -> Mon is one session, though it is three calendar days."""
        assert sessions_elapsed(FRI, MON, _weekday_sessions) == 1

    def test_counts_intervening_sessions(self) -> None:
        # Thu -> Tue: Fri, Mon, Tue = 3 sessions.
        assert sessions_elapsed(THU, TUE, _weekday_sessions) == 3

    def test_future_bar_never_reads_negative(self) -> None:
        """A bar stamped ahead of now is a different defect.

        It must not read as freshness with room to spare, which a negative age
        would give it.
        """
        assert sessions_elapsed(TUE, THU, _weekday_sessions) == 0


class TestToleranceSessionsFor:
    def test_scheduled_timeframes_have_a_tolerance(self) -> None:
        expected = BASE_TOLERANCE_SESSIONS + SCHEDULED_GAP_SESSIONS
        assert tolerance_sessions_for("4h") == expected
        assert tolerance_sessions_for("1d") == expected

    def test_unscheduled_timeframes_return_none(self) -> None:
        """No declared cadence must not silently become a tight default."""
        assert tolerance_sessions_for("1wk") is None
        assert tolerance_sessions_for("1h") is None
        assert tolerance_sessions_for("nonsense") is None


class TestRthDivergenceFromParent:
    """The port's one hard divergence: age is sessions, never wall-clock/bar_ms."""

    def test_4h_two_sessions_back_is_four_bars(self) -> None:
        graded = grade(
            Series("AAPL", "4h", _ms(FRI)), now=TUE, sessions_fn=_weekday_sessions
        )
        # 2 sessions (Mon, Tue) x 2 bars/day RTH = 4. The parent's wall-clock
        # formula would give ~(4 calendar days x 6) = 24.
        assert graded.age_sessions == 2
        assert graded.age_bars == 4.0

    def test_weekend_adds_no_phantom_bars(self) -> None:
        """Fri -> Mon is one session on 1d, though wall-clock says three days."""
        graded = grade(
            Series("AAPL", "1d", _ms(FRI)), now=MON, sessions_fn=_weekday_sessions
        )
        assert graded.age_bars == 1.0

    def test_unknown_timeframe_is_unmeasurable_not_fresh(self) -> None:
        graded = grade(
            Series("AAPL", "3d", _ms(FRI)), now=TUE, sessions_fn=_weekday_sessions
        )
        assert graded.age_bars is None
        assert not graded.measurable


class TestScheduledTierHasTeeth:
    """Positive control: the perturbation the guard exists to catch."""

    def test_a_stale_scheduled_series_is_reported(self) -> None:
        # 2026-06-18 is the date wifey's universe had actually frozen at.
        rows = [Series("AAPL", "1d", _ms(date(2026, 6, 18)))]
        report = evaluate_ohlcv(
            rows,
            now=TUE,
            scheduled_symbols=frozenset({"AAPL"}),
            sessions_fn=_weekday_sessions,
        )
        assert not report.ok
        assert [(s.symbol, s.timeframe) for s in report.stale] == [("AAPL", "1d")]

    def test_a_fresh_scheduled_series_is_not_reported(self) -> None:
        """The other half of the control: the guard must be able to be green."""
        rows = [Series("AAPL", "1d", _ms(MON))]
        report = evaluate_ohlcv(
            rows,
            now=TUE,
            scheduled_symbols=frozenset({"AAPL"}),
            sessions_fn=_weekday_sessions,
        )
        assert report.ok
        assert report.scheduled_total == 1

    def test_every_scheduled_timeframe_is_measurable(self) -> None:
        """Adding a scheduled timeframe without a bars-per-day entry must fail HERE.

        `evaluate_ohlcv` reports an unmeasurable scheduled series rather than
        passing it, but that branch is unreachable while this invariant holds —
        so the invariant is what gets asserted. Same shape as the max-hold
        calibration coverage test: a guard whose only fix is a calibration
        decision has to ship with that decision.
        """
        assert set(BARS_PER_DAY) >= SCHEDULED_TIMEFRAMES


class TestUnscheduledTier:
    def test_unscheduled_series_never_makes_the_report_fail(self) -> None:
        """Nothing refreshes the 505 universe, so its age is an absence, not a fault."""
        rows = [Series("ZTS", "1d", _ms(date(2026, 6, 18)))]
        report = evaluate_ohlcv(
            rows,
            now=TUE,
            scheduled_symbols=frozenset({"AAPL"}),
            sessions_fn=_weekday_sessions,
        )
        assert report.ok
        assert report.stale == []
        assert report.unscheduled_total == 1
        assert report.unscheduled_oldest == date(2026, 6, 18)

    def test_1wk_is_unscheduled_even_for_a_watchlist_symbol(self) -> None:
        """The live scan does not read 1wk, so a watchlist name's weekly bars
        go stale exactly like the universe's."""
        rows = [Series("AAPL", "1wk", _ms(date(2026, 6, 15)))]
        report = evaluate_ohlcv(
            rows,
            now=TUE,
            scheduled_symbols=frozenset({"AAPL"}),
            sessions_fn=_weekday_sessions,
        )
        assert report.ok
        assert report.scheduled_total == 0
        assert report.unscheduled_total == 1

    def test_absent_watchlist_grades_nothing_rather_than_everything(self) -> None:
        """Degrade toward reporting an absence, never toward ~500 phantom faults."""
        rows = [Series("AAPL", "1d", _ms(date(2026, 6, 18)))]
        report = evaluate_ohlcv(
            rows, now=TUE, scheduled_symbols=frozenset(), sessions_fn=_weekday_sessions
        )
        assert report.ok
        assert report.scheduled_total == 0
        assert report.unscheduled_total == 1


class TestEvaluateSignal:
    def test_no_watermarks_is_the_one_finding_this_leg_makes(self) -> None:
        report = evaluate_signal({}, now=TUE, sessions_fn=_weekday_sessions)
        assert not report.ok
        assert report.newest_session is None

    def test_an_old_watermark_is_not_a_failure(self) -> None:
        """⚠ Anti-regression for the defect this leg shipped with.

        The watermark advances on DISPATCH, not on every run, and `day_filter`
        makes dispatch intermittent. Grading it printed STALE on a healthy
        system, so `ok` must stay true however old the mark is.
        """
        marks = {"AAPL:4h:ema": _ms(date(2026, 1, 2))}
        report = evaluate_signal(marks, now=TUE, sessions_fn=_weekday_sessions)
        assert report.ok
        assert report.age_sessions is not None and report.age_sessions > 100

    def test_primary_and_wife_are_dated_separately(self) -> None:
        """A primary mark ahead of the :wife mark is the normal resting state."""
        marks = {
            "AAPL:4h:ema": _ms(MON),
            "AAPL:4h:ema:wife": _ms(THU),
        }
        report = evaluate_signal(marks, now=TUE, sessions_fn=_weekday_sessions)
        assert report.newest_session == MON
        assert report.wife_newest_session == THU
        assert report.newest_session > report.wife_newest_session
        assert report.watermark_count == 2
        assert report.wife_count == 1

    def test_wife_session_is_none_when_no_send_has_happened(self) -> None:
        marks = {"AAPL:4h:ema": _ms(THU)}
        report = evaluate_signal(marks, now=TUE, sessions_fn=_weekday_sessions)
        assert report.wife_newest_session is None


class TestReadersDegradeRatherThanCrash:
    def test_absent_state_file_reads_empty(self, tmp_path: Path) -> None:
        assert read_watermarks(tmp_path / "nope.json") == {}

    def test_malformed_state_file_reads_empty(self, tmp_path: Path) -> None:
        bad = tmp_path / "signal_state.json"
        bad.write_text("{not json")
        assert read_watermarks(bad) == {}

    def test_absent_watchlist_reads_empty(self, tmp_path: Path) -> None:
        assert read_scheduled_symbols(tmp_path / "nope.json") == frozenset()

    def test_universe_policy_is_not_a_symbol(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text('{"AAPL": {"sl_pct": 2}, "universe_policy": {"scope": "x"}}')
        assert read_scheduled_symbols(path) == frozenset({"AAPL"})

    def test_watchlist_as_a_bare_list_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text('["AAPL", "MSFT"]')
        assert read_scheduled_symbols(path) == frozenset({"AAPL", "MSFT"})


class TestLatestSession:
    def test_returns_the_most_recent_session_on_or_before_now(self) -> None:
        # 2026-08-23 is a Sunday; the latest session is the Friday before.
        sunday = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        assert latest_session(sunday, _weekday_sessions) == FRI

    def test_falls_back_to_the_plain_date_when_no_session_is_found(self) -> None:
        def _no_sessions(start: date, end: date) -> list[date]:
            return []

        now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
        assert latest_session(now, _no_sessions) == date(2026, 8, 26)
