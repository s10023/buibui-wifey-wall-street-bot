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

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from analytics.backtest.cost_model import BARS_PER_DAY
from tools.freshness_check import (
    BASE_TOLERANCE_SESSIONS,
    SCHEDULED_GAP_SESSIONS,
    SCHEDULED_TIMEFRAMES,
    UNIVERSE_CADENCE,
    UNIVERSE_GAP_SESSIONS,
    WATCHLIST_CADENCE,
    Series,
    evaluate_ohlcv,
    evaluate_signal,
    grade,
    latest_session,
    read_scheduled_symbols,
    read_universe_symbols,
    read_watermarks,
    resolve_cadence,
    sessions_elapsed,
    sessions_per_bar,
    tolerance_sessions_for,
    universe_timer_enabled,
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


def _sessions_back(now: date, n: int) -> date:
    """The session `n` weekday-sessions before `now`, by the test calendar.

    Derived by walking rather than hand-counting dates: a literal would encode
    the answer this file is meant to be checking, and a weekend miscount is
    exactly the class of error these tests exist to catch.
    """
    day = now
    remaining = n
    while remaining > 0:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return day


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
        assert tolerance_sessions_for("4h", WATCHLIST_CADENCE) == expected
        assert tolerance_sessions_for("1d", WATCHLIST_CADENCE) == expected

    def test_unscheduled_timeframes_return_none(self) -> None:
        """No declared cadence must not silently become a tight default."""
        assert tolerance_sessions_for("1wk", WATCHLIST_CADENCE) is None
        assert tolerance_sessions_for("1h", WATCHLIST_CADENCE) is None
        assert tolerance_sessions_for("nonsense", WATCHLIST_CADENCE) is None

    def test_the_bar_span_term_leaves_intraday_and_daily_untouched(self) -> None:
        """Regression pin: adding the third term must not move a shipped bar.

        4h and 1d bars close inside one session, so `max(0, span - 1)` is 0 for
        both. If this moves, every FRESH watchlist series is silently regraded.
        """
        assert sessions_per_bar("4h") == 0.5
        assert sessions_per_bar("1d") == 1.0
        for timeframe in ("4h", "1d"):
            assert tolerance_sessions_for(timeframe, UNIVERSE_CADENCE) == (
                BASE_TOLERANCE_SESSIONS + UNIVERSE_GAP_SESSIONS
            )

    def test_a_weekly_bar_is_not_graded_on_the_daily_footing(self) -> None:
        """The `1wk` term exists so the weekly tier can ever be green.

        A weekly bar stamps Monday and closes Friday, so a perfectly refreshed
        weekly series trails a daily one by four sessions for reasons that are
        not staleness. Without the term the tolerance would be 7 and every
        weekly series would red forever — the parent's wall-clock failure mode
        in a new place.
        """
        assert sessions_per_bar("1wk") == 5.0
        naive = BASE_TOLERANCE_SESSIONS + UNIVERSE_GAP_SESSIONS
        assert tolerance_sessions_for("1wk", UNIVERSE_CADENCE) == naive + 4.0

    def test_an_unknown_bars_per_day_returns_none_rather_than_a_default(self) -> None:
        """A timeframe absent from the shared table has no derivable span."""
        assert sessions_per_bar("nonsense") is None


class TestResolveCadence:
    def test_the_tightest_cadence_wins_when_both_cover_a_series(self) -> None:
        """A watchlist name is refreshed daily whether or not the weekly timer
        also touches it, so grading it weekly would let it sit stale and read
        FRESH."""
        cadence = resolve_cadence(
            "SPY", "1d", watchlist=frozenset({"SPY"}), universe=frozenset({"SPY"})
        )
        assert cadence is WATCHLIST_CADENCE

    def test_a_watchlist_symbols_weekly_bars_take_the_universe_cadence(self) -> None:
        """The live scan does not read 1wk, so only the universe timer covers it."""
        cadence = resolve_cadence(
            "SPY", "1wk", watchlist=frozenset({"SPY"}), universe=frozenset({"SPY"})
        )
        assert cadence is UNIVERSE_CADENCE

    def test_nothing_covering_a_series_returns_none(self) -> None:
        assert (
            resolve_cadence(
                "ZTS", "1d", watchlist=frozenset({"SPY"}), universe=frozenset()
            )
            is None
        )


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
        """With no cadence covering it, a series' age is an absence, not a fault.

        This is the world where the universe-sync timer is not enabled, which
        `evaluate_ohlcv`'s empty `universe_symbols` default expresses.
        """
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
        """The live scan does not read 1wk, so the DAILY timer never covers it.

        With the weekly timer off, a watchlist name's weekly bars go stale
        exactly like the universe's. `TestResolveCadence` covers the other
        world, where the universe timer picks them up.
        """
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


class TestUniverseTierIsGatedOnTheTimer:
    """The universe tier must be graded only where its timer actually runs.

    The units are opt-in and nothing in the repo installs them, so "the universe
    has a cadence" is true on one box and false on the next. These pin both
    worlds, because a tool that hardcodes either answer is wrong on half the
    machines — and the wrong direction (grading with no timer) prints ~1,100
    phantom faults, which is how a leg stops being read.
    """

    def test_a_stale_universe_series_is_graded_when_members_are_passed(self) -> None:
        """Positive control: the perturbation the new tier exists to catch."""
        rows = [Series("ZTS", "1d", _ms(date(2026, 6, 18)))]
        report = evaluate_ohlcv(
            rows,
            now=TUE,
            scheduled_symbols=frozenset({"AAPL"}),
            sessions_fn=_weekday_sessions,
            universe_symbols=frozenset({"ZTS"}),
        )
        assert not report.ok
        assert [(g.symbol, g.timeframe) for g in report.stale] == [("ZTS", "1d")]
        assert report.universe_scheduled

    def test_the_same_series_is_an_absence_when_the_timer_is_off(self) -> None:
        """The other half: identical input, no members, no fault reported."""
        rows = [Series("ZTS", "1d", _ms(date(2026, 6, 18)))]
        report = evaluate_ohlcv(
            rows,
            now=TUE,
            scheduled_symbols=frozenset({"AAPL"}),
            sessions_fn=_weekday_sessions,
        )
        assert report.ok
        assert report.unscheduled_total == 1
        assert not report.universe_scheduled

    def test_a_weekly_series_inside_its_cadence_is_green(self) -> None:
        """End-to-end proof the bar-span term lets the weekly tier be green.

        Eight sessions behind is stale under the naive 7-session bar and fresh
        under the correct 11. Without the term this tier could never report
        FRESH, and a leg that is never green stops being read.
        """
        newest = _sessions_back(TUE, 8)
        rows = [Series("ZTS", "1wk", _ms(newest))]
        report = evaluate_ohlcv(
            rows,
            now=TUE,
            scheduled_symbols=frozenset(),
            sessions_fn=_weekday_sessions,
            universe_symbols=frozenset({"ZTS"}),
        )
        assert report.scheduled_total == 1
        assert report.ok

    def test_a_weekly_series_past_its_cadence_is_still_caught(self) -> None:
        """...and the loosened bar is not so loose that nothing can trip it."""
        newest = _sessions_back(TUE, 20)
        rows = [Series("ZTS", "1wk", _ms(newest))]
        report = evaluate_ohlcv(
            rows,
            now=TUE,
            scheduled_symbols=frozenset(),
            sessions_fn=_weekday_sessions,
            universe_symbols=frozenset({"ZTS"}),
        )
        assert not report.ok


class TestUniverseReaders:
    def test_the_committed_universe_parses_to_its_members(self) -> None:
        """Reads the real committed file: the members key is the seam."""
        symbols = read_universe_symbols(Path("config/universe.json"))
        assert len(symbols) > 400
        assert "AAPL" in symbols
        # `universe_policy` and `membership_as_of` are config, not symbols.
        assert "universe_policy" not in symbols
        assert "membership_as_of" not in symbols

    def test_delisted_members_are_not_graded(self) -> None:
        """Nothing refreshes a delisted member, so grading it is a false STALE.

        `analytics_runner` resolves `--universe` through `active_symbols()`, so
        the weekly sync and this probe must agree on the roster or the probe
        reports a fault for a decision. Both directions are asserted: the live
        name stays graded, the delisted one drops out.
        """
        raw = json.loads(Path("config/universe.json").read_text(encoding="utf-8"))
        delisted = {s for s, m in raw["members"].items() if m.get("delisted")}
        assert delisted, (
            "fixture assumption: the committed universe has delisted members"
        )
        symbols = read_universe_symbols(Path("config/universe.json"))
        assert not (symbols & delisted), (
            f"delisted members graded: {symbols & delisted}"
        )
        assert "AAPL" in symbols, "active members must still be graded"
        assert len(symbols) == len(raw["members"]) - len(delisted)

    def test_a_malformed_member_is_kept_rather_than_excused(
        self, tmp_path: Path
    ) -> None:
        """An unreadable member degrades to being graded, never to being hidden."""
        p = tmp_path / "u.json"
        p.write_text(
            json.dumps(
                {
                    "members": {
                        "AAA": {"sector": "X", "kind": "stock", "delisted": False},
                        "BBB": {"sector": "X", "kind": "stock", "delisted": True},
                        "CCC": "not-a-dict",
                    }
                }
            ),
            encoding="utf-8",
        )
        assert read_universe_symbols(p) == {"AAA", "CCC"}

    def test_an_absent_universe_reads_empty_rather_than_raising(self) -> None:
        """A probe must report an absent universe, never crash on one."""
        assert read_universe_symbols(Path("does/not/exist.json")) == frozenset()

    def test_an_unknown_timer_is_not_enabled(self) -> None:
        """Every unreadable state degrades to False — the direction that cannot
        invent faults. Covers the no-systemd and non-Linux boxes too, where the
        subprocess raises rather than returning a state."""
        assert not universe_timer_enabled("wifey-does-not-exist.timer")


class TestEvaluateSignal:
    def test_no_watermarks_is_the_one_finding_this_leg_makes(self) -> None:
        report = evaluate_signal({}, now=TUE, sessions_fn=_weekday_sessions)
        assert not report.ok
        assert report.newest_session is None

    def test_an_old_watermark_is_not_a_failure(self) -> None:
        """The watermark advances on dispatch, not on every run, and `day_filter`
        makes dispatch intermittent. Grading it would print STALE on a healthy
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
        bad.write_text("{not json", encoding="utf-8")
        assert read_watermarks(bad) == {}

    def test_absent_watchlist_reads_empty(self, tmp_path: Path) -> None:
        assert read_scheduled_symbols(tmp_path / "nope.json") == frozenset()

    def test_universe_policy_is_not_a_symbol(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(
            '{"AAPL": {"sl_pct": 2}, "universe_policy": {"scope": "x"}}',
            encoding="utf-8",
        )
        assert read_scheduled_symbols(path) == frozenset({"AAPL"})

    def test_watchlist_as_a_bare_list_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text('["AAPL", "MSFT"]', encoding="utf-8")
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
