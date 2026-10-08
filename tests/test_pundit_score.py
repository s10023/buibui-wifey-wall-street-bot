"""Tests for tools/pundit_score.py — pundit-ledger scorer (equity port).

Level parsing, family tagging, aggregation and output shape are ported verbatim from
the parent repo's suite: that logic is byte-identical, so keeping the tests identical
keeps the two ledgers comparable. Everything that touches the *tape* is rewritten
against session-anchored equity bars, because the six divergences documented in
``tools/pundit_score.py`` all live there.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.trading_calendar import nyse_sessions
from tools.pundit_score import (
    AUDIT_ELIGIBLE_N,
    CellStats,
    LedgerCall,
    Override,
    ScoredCall,
    _cell_dict,
    _cell_table,
    aggregate,
    audit_eligible_cells,
    bar_close_ms,
    bar_outcome,
    build_parser,
    build_priors,
    deadline_ms,
    find_entry_start,
    find_fill,
    find_ref_bar,
    last_expected_close_ms,
    load_ledger,
    load_ohlcv_for_calls,
    load_overrides,
    parse_level_field,
    render_report,
    resolve_levels,
    score_call,
    session_close_ms,
    session_date,
    tag_family,
    window_sessions,
)

NY = "America/New_York"
SYMBOL = "TSLA"
#: 2026-06-01 is a Monday; the run to 2026-06-26 carries no NYSE holiday
#: (Juneteenth 2026 falls on Friday 06-19 and IS a holiday, so the calendar —
#: not a hand-written weekday list — has to supply the sessions).
FIXTURE_START = date(2026, 6, 1)


def _ny_ms(day: date, hour: int, minute: int = 0) -> int:
    """UTC epoch-ms for a New-York wall-clock time on ``day``."""
    ts = pd.Timestamp(
        year=day.year, month=day.month, day=day.day, hour=hour, minute=minute, tz=NY
    )
    return int(ts.tz_convert("UTC").timestamp() * 1000)


def _sessions(n: int, start: date = FIXTURE_START) -> list[date]:
    return nyse_sessions(start, start + timedelta(days=2 * n + 30))[:n]


def _daily(
    rows: list[tuple[float, float, float, float]], start: date = FIXTURE_START
) -> pd.DataFrame:
    """Daily equity frame: one bar per NYSE session, open_time at NY midnight."""
    days = _sessions(len(rows), start)
    return pd.DataFrame(
        [
            {
                "symbol": SYMBOL,
                "timeframe": "1d",
                "open_time": _ny_ms(day, 0),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1.0,
            }
            for day, (o, h, lo, c) in zip(days, rows, strict=True)
        ]
    )


def _hourly(
    rows: list[tuple[float, float, float, float]], start: date = FIXTURE_START
) -> pd.DataFrame:
    """Intraday frame: 7 RTH bars per session, 09:30..15:30 NY, rolling to the next day."""
    days = _sessions(len(rows) // 7 + 2, start)
    recs = []
    for i, (o, h, lo, c) in enumerate(rows):
        day = days[i // 7]
        recs.append(
            {
                "symbol": SYMBOL,
                "timeframe": "1h",
                "open_time": _ny_ms(day, 9, 30) + (i % 7) * 3_600_000,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1.0,
            }
        )
    return pd.DataFrame(recs)


FAR = _ny_ms(date(2027, 6, 1), 0)  # as_of far beyond every fixture window


def _iso(ts_ms: int) -> str:
    """UTC epoch-ms -> the ISO string shape the ledger stores."""
    return (
        datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")
    )


class TestParseLevelField:
    """Ported verbatim — the level-parsing regexes are byte-identical to the parent."""

    def test_numeric_with_commas_and_dollar(self) -> None:
        p = parse_level_field("$61,696.80")
        assert p.numbers == (61696.80,)
        assert p.zones == ()
        assert not p.unspecified

    def test_k_suffix_expansion(self) -> None:
        assert parse_level_field("81k").numbers == (81000.0,)
        assert parse_level_field("60.5k intraweek value-area low").numbers == (60500.0,)

    def test_zone_hyphen_endash_to(self) -> None:
        for text in ("57,900-58,200", "57,900–58,200", "57,900 to 58,200"):
            p = parse_level_field(text)
            assert p.zones == ((57900.0, 58200.0),), text

    def test_zone_reversed_bounds_are_sorted(self) -> None:
        assert parse_level_field("60,700-59,500").zones == ((59500.0, 60700.0),)

    def test_unspecified_markers(self) -> None:
        for text in (None, "", "unspecified", "n/a", "None"):
            assert parse_level_field(text).unspecified, repr(text)

    def test_month_anchored_years_are_not_price_levels(self) -> None:
        """Equity divergence 7 — the defect the first real ledger run exposed.

        "starting Aug-Sep 2026" on a 7,436 index parsed 2,026 as the target and the
        ref-relative sanity gate waved it through, because US index levels live in the
        same band as year strings.
        """
        p = parse_level_field("10-20% drawdown from the top, starting Aug-Sep 2026")
        assert 2026.0 not in p.numbers
        for text, year in (
            ("sweep the October 2023 swing lows", 2023.0),
            ("retest of the April 2025 low", 2025.0),
            ("a low in the Jul-Oct 2026 window", 2026.0),
            ("Dec. 2024 high", 2024.0),
        ):
            assert year not in parse_level_field(text).numbers, text

    def test_bare_year_without_a_month_cue_is_left_alone(self) -> None:
        # With no date cue a 4-digit number is indistinguishable from a real level, so
        # it stays a candidate and the sanity gate decides.
        assert 2026.0 in parse_level_field("target 2026").numbers

    def test_messy_multi_number_keeps_zone_and_singles(self) -> None:
        p = parse_level_field(
            "Sweep range low ~57,900-58,200 then reclaim (price 58,254)"
        )
        assert (57900.0, 58200.0) in p.zones
        assert 58254.0 in p.numbers

    def test_leading_negation_with_incidental_number_is_unspecified(self) -> None:
        """A field whose head says NO level was given carries no level.

        The trailing number is incidental context -- a resistance reference, a fib
        ratio, a historical-analog decade. Harvesting it fabricates a precise call
        the pundit never made, and the sanity gate cannot catch the worst form
        because the phantom number IS the reference close.

        The first three strings are verbatim from this repo's
        ``docs/plans/pundit-calls.jsonl``; the rest are the upstream forms
        (parent #589) kept so a shared regex is not narrowed by a small ledger.
        """
        for text in (
            "not stated (implied ~454 resistance)",
            "not stated (verbal call, no explicit price given)",
            "not stated (qualitative; 1970s analog chops sideways a year or two)",
            "Not specified -- the video gives no explicit entry (~64,017.6)",
            "unspecified (~58,872, already broke below consolidation)",
            "unspecified (implied above consolidation range ~80,000)",
            "No explicit target given; implied continuation toward 66,500-66,900",
            "not given (roughly 64k)",
            "n/a (describes the trigger itself); the 63,000-63,500 zone IS it",
        ):
            p = parse_level_field(text)
            assert p.unspecified, repr(text)
            assert p.numbers == (), repr(text)
            assert p.zones == (), repr(text)

    def test_leading_negation_drops_scenario_indices(self) -> None:
        """'scenario 1 / scenario 2' harvested as prices 1.0 and 4.0."""
        p = parse_level_field(
            "none yet - scenario 1: 4h MSB at 60.9 then plan entry; scenario 2: fr"
        )
        assert p.unspecified
        assert p.numbers == ()

    def test_trailing_hedge_keeps_the_level_but_marks_it_hedged(self) -> None:
        """A stated level with a hedged PROVENANCE note is still a real level.

        Distinct from the leading-negation class: here the negation qualifies
        where the level came from, not whether one exists. Dropping it would lose
        a genuine call, so the level survives and is flagged instead. Verbatim
        from this repo's ledger (the `luckychartape` TSLA entry).
        """
        p = parse_level_field("~420 (current market, no explicit entry stated)")
        assert not p.unspecified
        assert 420.0 in p.numbers
        assert p.hedged

    def test_plain_level_is_not_hedged(self) -> None:
        assert not parse_level_field("$61,696.80").hedged
        assert not parse_level_field("57,900-58,200").hedged


class TestSessionHelpers:
    """Equity divergence 6 groundwork: bars and windows are anchored to NY sessions."""

    def test_session_date_uses_new_york_calendar_date(self) -> None:
        # 04:00 UTC on 2026-07-31 is midnight EDT — still the 31st in New York.
        assert session_date(_ny_ms(date(2026, 7, 31), 0)) == date(2026, 7, 31)
        # 21:00 ET on 2026-07-31 is already 2026-08-01 in UTC, but it is still the
        # 31st's session — the ET calendar date is what keys a session, not the UTC one.
        after_bell = _ny_ms(date(2026, 7, 31), 21)
        assert pd.Timestamp(after_bell, unit="ms", tz="UTC").date() == date(2026, 8, 1)
        assert session_date(after_bell) == date(2026, 7, 31)

    def test_session_close_is_dst_correct(self) -> None:
        # 16:00 EDT == 20:00 UTC in July; 16:00 EST == 21:00 UTC in January.
        edt = pd.Timestamp(session_close_ms(date(2026, 7, 31)), unit="ms", tz="UTC")
        est = pd.Timestamp(session_close_ms(date(2026, 1, 30)), unit="ms", tz="UTC")
        assert edt.strftime("%H:%M") == "20:00"
        assert est.strftime("%H:%M") == "21:00"

    def test_bar_close_offsets(self) -> None:
        # A daily bar opens at NY midnight and closes at the 16:00 bell, 16h later.
        day = date(2026, 7, 31)
        assert bar_close_ms(_ny_ms(day, 0), "1d") == _ny_ms(day, 16)
        assert bar_close_ms(_ny_ms(day, 9, 30), "1h") == _ny_ms(day, 10, 30)


class TestWindows:
    def test_window_sessions_mapping(self) -> None:
        assert window_sessions("intraday") == 2
        assert window_sessions("swing") == 21
        assert window_sessions("unspecified") == 10
        assert window_sessions("weird") == 10

    def test_intraday_deadline_counts_the_current_session(self) -> None:
        # Monday 2026-06-22 12:00 ET: this session still has a close ahead of it, so
        # the 2-session window is Monday + Tuesday.
        called = _ny_ms(date(2026, 6, 22), 12)
        assert deadline_ms(called, "intraday") == session_close_ms(date(2026, 6, 23))

    def test_deadline_after_the_bell_starts_at_the_next_session(self) -> None:
        called = _ny_ms(date(2026, 6, 22), 16, 30)  # after Monday's close
        assert deadline_ms(called, "intraday") == session_close_ms(date(2026, 6, 24))

    def test_last_expected_close_ignores_the_overnight_hole(self) -> None:
        """Equity divergence 8 — the other defect the first real run exposed.

        Run at 21:42 ET, the tape's last bar is that afternoon's 16:00 close. Comparing
        stored data against wall-clock now would call every symbol STALE every evening.
        """
        evening = _ny_ms(date(2026, 6, 23), 21, 42)
        assert last_expected_close_ms(evening) == session_close_ms(date(2026, 6, 23))
        # Mid-session, the session in progress has not closed yet -> the prior one.
        midday = _ny_ms(date(2026, 6, 23), 12)
        assert last_expected_close_ms(midday) == session_close_ms(date(2026, 6, 22))
        # Sunday: the last close is Friday's.
        sunday = _ny_ms(date(2026, 6, 28), 12)
        assert last_expected_close_ms(sunday) == session_close_ms(date(2026, 6, 26))

    def test_weekend_call_skips_to_monday(self) -> None:
        # Friday-evening call: wall-clock 48h would expire before any trading happens.
        # 2026-06-26 is a Friday -> the window must be Monday 06-29 + Tuesday 06-30.
        called = _ny_ms(date(2026, 6, 26), 20)
        assert deadline_ms(called, "intraday") == session_close_ms(date(2026, 6, 30))


class TestLoaders:
    def _good_line(self) -> dict[str, object]:
        return {
            "source": "youtube",
            "author": "A",
            "url": "https://x.com/A/status/1",
            "call_ts_utc": "2026-06-22T20:30:00Z",
            "symbol": SYMBOL,
            "direction": "long",
            "entry": "100",
            "stop": "90",
            "target": "120",
            "horizon": "swing",
            "confidence": "",
            "raw_quote": "sweep and reclaim",
        }

    def test_load_ledger_parses_and_warns(self, tmp_path: Path) -> None:
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(self._good_line()) + "\n{not json\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert len(calls) == 1
        assert calls[0].author == "A"
        assert calls[0].line_no == 1
        expected_ms = int(datetime(2026, 6, 22, 20, 30, tzinfo=UTC).timestamp() * 1000)
        assert calls[0].call_ts_ms == expected_ms
        assert len(warnings) == 1 and "line 2" in warnings[0]

    def test_load_ledger_optional_numeric_px(self, tmp_path: Path) -> None:
        line = self._good_line() | {"entry_px": 101.0}
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, _ = load_ledger(p)
        assert calls[0].entry_px == 101.0
        assert calls[0].stop_px is None

    def test_load_ledger_warns_on_non_object_json_line(self, tmp_path: Path) -> None:
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line()) + "\n" + json.dumps([1, 2]) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert len(calls) == 1
        assert len(warnings) == 1 and "line 2" in warnings[0]

    def test_load_ledger_warns_on_malformed_call_ts_utc(self, tmp_path: Path) -> None:
        bad_line = self._good_line() | {"call_ts_utc": "not-a-date"}
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line()) + "\n" + json.dumps(bad_line) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert len(calls) == 1
        assert len(warnings) == 1 and "line 2" in warnings[0]

    def test_null_symbol_is_skipped_with_a_named_warning(self, tmp_path: Path) -> None:
        """The null-symbol guard, enforced in code rather than left as skill prose.

        Without it ``str(None)`` becomes the literal ticker "None", which then reports
        UNRESOLVABLE — indistinguishable from a symbol we simply have not backfilled.
        """
        p = tmp_path / "calls.jsonl"
        lines = [json.dumps(self._good_line())]
        for bad in (None, "", "null", "N/A", "  "):
            lines.append(json.dumps(self._good_line() | {"symbol": bad}))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert len(calls) == 1
        assert len(warnings) == 5
        assert all("no symbol resolved" in w for w in warnings)

    def test_author_is_normalised_at_read(self, tmp_path: Path) -> None:
        """A ledger written with a mixed '@' convention groups as one person.

        Without this the scorer reports two shorter track records and neither
        may clear a min_n marker the whole would have.
        """
        lines = [
            self._good_line() | {"author": "@fenggemeigu"},
            self._good_line() | {"author": "fenggemeigu"},
            self._good_line() | {"author": "  @fenggemeigu  "},
        ]
        p = tmp_path / "calls.jsonl"
        p.write_text(
            "".join(json.dumps(x) + "\n" for x in lines),
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert warnings == []
        assert {c.author for c in calls} == {"fenggemeigu"}

    def test_out_of_enum_horizon_is_rejected_with_a_named_warning(
        self, tmp_path: Path
    ) -> None:
        """The live silent defect this port closes.

        An unrecognised horizon such as 'scalp' would load fine and then take both
        unspecified fallbacks: 1d bars instead of 1h, and a 10-session window
        instead of 2.
        """
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line() | {"horizon": "scalp"}) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert calls == []
        assert len(warnings) == 1
        assert "line 1" in warnings[0] and "scalp" in warnings[0]

    def test_missing_horizon_is_unspecified_not_an_error(self, tmp_path: Path) -> None:
        """Absence is a member of the enum, unlike `direction`."""
        line = self._good_line()
        del line["horizon"]
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert warnings == []
        assert calls[0].horizon == "unspecified"

    def test_out_of_enum_direction_is_rejected_with_a_named_warning(
        self, tmp_path: Path
    ) -> None:
        """Parity with the parent. score_call already gated this to UNSCORED,
        so the change is that the rejection now names the ledger line."""
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line() | {"direction": "range"}) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert calls == []
        assert len(warnings) == 1
        assert "line 1" in warnings[0] and "range" in warnings[0]

    def test_direction_casing_still_loads(self, tmp_path: Path) -> None:
        """Dropping 'SHORT' would be a regression — the old read lowercased."""
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line() | {"direction": "SHORT"}) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert warnings == []
        assert calls[0].direction == "short"

    def test_live_ledger_rows_all_survive_the_new_guards(self) -> None:
        """No behaviour change on the committed ledger.

        Measured at port time: 19 rows, all in-enum, 4 author keys none of
        which collide. If this ever fails, a real row was newly rejected and
        the scorecard changed — which is exactly what should be noticed.
        """
        ledger = Path("docs/plans/pundit-calls.jsonl")
        if not ledger.exists():  # gitignored; absent on a fresh clone
            pytest.skip("pundit ledger not present")
        calls, warnings = load_ledger(ledger)
        enum_warnings = [w for w in warnings if "direction" in w or "horizon" in w]
        assert enum_warnings == [], f"live ledger rows newly rejected: {enum_warnings}"
        assert calls, "ledger present but no rows loaded"

    def test_load_overrides_and_missing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "overrides.jsonl"
        p.write_text(
            json.dumps(
                {"url": "https://x.com/A/status/1", "stop_px": 89.0, "skip": False}
            )
            + "\n"
            + json.dumps(
                {"url": "https://x.com/B/status/2", "skip": True, "note": "dup"}
            )
            + "\n",
            encoding="utf-8",
        )
        ov = load_overrides(p)
        assert ov["https://x.com/A/status/1"].stop_px == 89.0
        assert ov["https://x.com/B/status/2"].skip is True
        assert load_overrides(tmp_path / "absent.jsonl") == {}


def _call(**kw: object) -> LedgerCall:
    base: dict[str, object] = {
        "line_no": 1,
        "source": "youtube",
        "author": "A",
        "url": "https://x.com/A/status/1",
        "call_ts_utc": "2026-06-22T20:30:00Z",
        "symbol": SYMBOL,
        "direction": "long",
        "entry": "100",
        "stop": "90",
        "target": "120",
        "horizon": "swing",
        "confidence": "",
        "raw_quote": "",
    }
    base.update(kw)
    return LedgerCall(**base)  # type: ignore[arg-type]


class TestScoreTimeframe:
    def test_horizon_selects_the_frame(self) -> None:
        """Equity divergence 1 — swing calls resolve on 1d, not on scarce 1h history."""
        assert _call(horizon="intraday").timeframe == "1h"
        assert _call(horizon="swing").timeframe == "1d"
        assert _call(horizon="unspecified").timeframe == "1d"
        assert _call(horizon="nonsense").timeframe == "1d"


class TestResolveLevels:
    """Ported verbatim — level selection is pure arithmetic, unchanged from the parent."""

    REF = 58000.0

    def test_clean_numeric_all_fields_ok(self) -> None:
        lv = resolve_levels(
            _call(entry="58,000", stop="57,000", target="60,000"), None, self.REF
        )
        assert (lv.entry_px, lv.stop_px, lv.target_px) == (58000.0, 57000.0, 60000.0)
        assert lv.parse_confidence == "ok"
        assert not lv.entry_is_thesis

    def test_zone_entry_mid_stop_far_target_near_long(self) -> None:
        lv = resolve_levels(
            _call(entry="57,900-58,200", stop="57,500-57,700", target="59,500-60,700"),
            None,
            self.REF,
        )
        assert lv.entry_px == 58050.0  # zone mid
        assert lv.stop_px == 57500.0  # far edge for a long = lower bound
        assert lv.target_px == 59500.0  # near edge for a long = lower bound

    def test_zone_edges_short(self) -> None:
        lv = resolve_levels(
            _call(
                direction="short",
                entry="58,800-59,000",
                stop="59,200-59,600",
                target="57,000-57,400",
            ),
            None,
            self.REF,
        )
        assert lv.stop_px == 59600.0  # far edge for a short = upper bound
        assert lv.target_px == 57400.0  # near edge for a short = upper bound

    def test_sanity_gate_skips_date_noise(self) -> None:
        lv = resolve_levels(
            _call(entry="58,000", target="liquidity below 58K (June 25 low ~58,043)"),
            None,
            self.REF,
        )
        assert lv.target_px == 58000.0  # '58K' expands; '25' rejected by the gate
        assert lv.parse_confidence == "low"  # multiple sane candidates -> ambiguous

    def test_sanity_gate_rejects_all_falls_back(self) -> None:
        lv = resolve_levels(_call(entry="HTF demand ~38-45"), None, self.REF)
        assert lv.entry_is_thesis and lv.entry_px == self.REF
        assert lv.parse_confidence == "fallback"

    def test_unspecified_entry_thesis_fallback(self) -> None:
        lv = resolve_levels(
            _call(entry="unspecified", stop="", target=""), None, self.REF
        )
        assert lv.entry_is_thesis and lv.entry_px == self.REF
        assert lv.stop_px is None and lv.target_px is None
        assert lv.parse_confidence == "fallback"

    def test_ledger_px_beats_text_and_override_beats_both(self) -> None:
        call = _call(entry="55,000", entry_px=58100.0)
        assert resolve_levels(call, None, self.REF).entry_px == 58100.0
        ov = Override(url=call.url, entry_px=58200.0)
        lv = resolve_levels(call, ov, self.REF)
        assert lv.entry_px == 58200.0
        assert lv.parse_confidence == "override"

    def test_index_scale_levels_are_rejected_against_an_etf_reference(self) -> None:
        """Why ^GSPC is backfilled directly rather than proxied through SPY.

        An S&P index level (7,434) against a SPY reference close (~660) is 11x the
        reference — the sanity gate rejects it and the call silently degrades to a
        thesis entry, losing exactly the levels that make it scoreable.
        """
        lv = resolve_levels(_call(entry="7,434-7,438"), None, 660.0)
        assert lv.entry_is_thesis and lv.entry_px == 660.0

    def test_hedged_entry_near_ref_close_is_a_thesis_not_a_precise_call(self) -> None:
        """The upstream 8a defect, at the layer where it does harm.

        The phantom number IS the reference close, so the sanity gate passes it
        and the entry price barely moves -- but the CONFIDENCE label is the whole
        point: 'ok' asserts the pundit named 58,010, 'fallback' says we scored a
        thesis from the reference close. Only the second is true.
        """
        lv = resolve_levels(
            _call(entry="unspecified (~58,010 at post)", stop="", target=""),
            None,
            self.REF,
        )
        assert lv.entry_is_thesis and lv.entry_px == self.REF
        assert lv.parse_confidence == "fallback"

    def test_hedged_stop_and_target_are_dropped_not_invented(self) -> None:
        """Here the numeric effect is large, not cosmetic.

        This is the shape that scored on this repo's own ledger: the
        `luckychartape` TSLA short carried a stop of 454.00 harvested out of
        "not stated (implied ~454 resistance)", at `parse_confidence = ok`.
        """
        lv = resolve_levels(
            _call(
                entry="58,000",
                stop="not stated (implied ~59,000 resistance)",
                target="No explicit target given; implied toward 60,500-60,900",
            ),
            None,
            self.REF,
        )
        assert lv.stop_px is None
        assert lv.target_px is None

    def test_hedged_provenance_keeps_level_at_low_confidence(self) -> None:
        lv = resolve_levels(
            _call(entry="58,100 (not stated in text, read off the chart)"),
            None,
            self.REF,
        )
        assert lv.entry_px == 58100.0
        assert not lv.entry_is_thesis
        assert lv.parse_confidence == "low"


class TestTagFamily:
    """Ported verbatim — the taxonomy is deliberately kept identical to the parent."""

    def test_one_case_per_family(self) -> None:
        cases = {
            "sweep range low then reclaim": "sweep_reclaim",
            "rotation toward the composite POC": "vp_level",
            "PDL is the trigger": "ref_level",
            "holding the 1W 50EMA": "ema_trend",
            "CVD remains heavy, absorption at lows": "flow",
            "spot-demand accumulation zone below": "accumulation_zone",
            "break of $81 would be very positive": "breakout_deviation",
            "just vibes": "other",
        }
        for text, family in cases.items():
            assert tag_family(text) == family, text

    def test_priority_order_first_match_wins(self) -> None:
        assert tag_family("sweep into the POC") == "sweep_reclaim"

    def test_space_wrapped_keyword_hits_at_left_edge(self) -> None:
        assert tag_family("reported oi levels are climbing") == "flow"

    def test_keyword_prefix_of_longer_word_does_not_hit(self) -> None:
        assert tag_family("the value of this setup is unclear") == "other"


class TestBarOutcome:
    """Equity divergences 4 and 5 — the gap-aware, adverse-first bar resolver."""

    def test_intrabar_touch_long(self) -> None:
        assert bar_outcome("long", 100, 125, 99, 95.0, 120.0) == ("WIN", 120.0)
        assert bar_outcome("long", 100, 101, 94, 95.0, 120.0) == ("LOSS", 95.0)

    def test_gap_through_stop_fills_at_the_open_not_the_stop(self) -> None:
        # Opened at 90, clean through a 95 stop. The parent's containment test
        # (low <= 95 <= high) is FALSE here — it would miss the stop entirely.
        assert bar_outcome("long", 90, 92, 88, 95.0, 120.0) == ("LOSS", 90.0)

    def test_gap_through_target_fills_at_the_open(self) -> None:
        assert bar_outcome("long", 125, 130, 124, 95.0, 120.0) == ("WIN", 125.0)

    def test_adverse_first_when_both_reached_intrabar(self) -> None:
        # Opened between the levels, then traded through both: unknowable order, so
        # the stop wins (the parent's convention, preserved).
        assert bar_outcome("long", 101, 130, 88, 95.0, 120.0) == ("LOSS", 95.0)

    def test_open_beyond_stop_outranks_a_target_touched_later(self) -> None:
        assert bar_outcome("long", 90, 130, 88, 95.0, 120.0) == ("LOSS", 90.0)

    def test_short_direction_mirrors(self) -> None:
        assert bar_outcome("short", 100, 101, 75, 110.0, 80.0) == ("WIN", 80.0)
        assert bar_outcome("short", 100, 115, 99, 110.0, 80.0) == ("LOSS", 110.0)
        assert bar_outcome("short", 115, 118, 114, 110.0, 80.0) == ("LOSS", 115.0)
        assert bar_outcome("short", 75, 76, 70, 110.0, 80.0) == ("WIN", 75.0)

    def test_no_levels_reached(self) -> None:
        assert bar_outcome("long", 100, 105, 99, 95.0, 120.0) is None
        assert bar_outcome("long", 100, 105, 99, None, None) is None


class TestRefBarAndFill:
    """Equity divergences 2 and 3 — closed-bar reference, next-open entry."""

    def test_ref_bar_is_the_last_fully_closed_bar(self) -> None:
        df = _daily([(100, 101, 99, 100)] * 5)
        days = _sessions(5)
        # Mid-session on day 2: that bar has NOT closed, so the reference is day 1.
        assert find_ref_bar(df, _ny_ms(days[2], 12), "1d") == 1
        # After the bell on day 2: day 2 has closed and becomes the reference.
        assert find_ref_bar(df, _ny_ms(days[2], 16, 30), "1d") == 2

    def test_ref_bar_none_before_any_close(self) -> None:
        df = _daily([(100, 101, 99, 100)] * 3)
        assert find_ref_bar(df, _ny_ms(_sessions(1)[0], 9), "1d") is None
        assert find_ref_bar(pd.DataFrame(), FAR, "1d") is None

    def test_after_hours_call_still_resolves(self) -> None:
        """The parent returns None here — no candle contains a 20:30 UTC timestamp."""
        df = _daily([(100, 101, 99, 100)] * 5)
        after_bell = _ny_ms(_sessions(5)[1], 16, 30)
        assert find_ref_bar(df, after_bell, "1d") == 1
        assert find_entry_start(df, after_bell) == 2

    def test_entry_start_is_first_bar_opening_after_the_call(self) -> None:
        df = _daily([(100, 101, 99, 100)] * 4)
        days = _sessions(4)
        assert find_entry_start(df, _ny_ms(days[1], 12)) == 2
        assert find_entry_start(df, FAR) is None

    def test_thesis_fills_at_next_open(self) -> None:
        df = _daily([(100, 101, 99, 100), (107, 110, 106, 108)])
        assert find_fill(df, 1, 999.0, True, FAR) == (1, 107.0)

    def test_level_fill_on_first_touch(self) -> None:
        df = _daily([(100, 101, 99, 100), (100, 101, 99, 100), (99, 100, 95, 96)])
        assert find_fill(df, 1, 98.0, False, FAR) == (2, 98.0)

    def test_level_fill_through_an_overnight_gap_fills_at_the_open(self) -> None:
        # Entry 98 is never inside bar 2's range (91..93) — but price gapped from a
        # 100 close straight past it, so the fill is the 92 open, not a miss.
        df = _daily([(100, 101, 99, 100), (100, 101, 99, 100), (92, 93, 91, 92)])
        assert find_fill(df, 1, 98.0, False, FAR) == (2, 92.0)

    def test_level_fill_respects_deadline(self) -> None:
        df = _daily([(100, 101, 99, 100), (100, 101, 99, 100), (99, 100, 95, 96)])
        limit = _ny_ms(_sessions(3)[1], 23)
        assert find_fill(df, 1, 98.0, False, limit) is None


def _score(
    call: LedgerCall,
    df: pd.DataFrame,
    as_of_ms: int = FAR,
    override: Override | None = None,
) -> ScoredCall:
    return score_call(call, override, df, as_of_ms)


WARMUP: list[tuple[float, float, float, float]] = [(100.0, 101.0, 99.0, 100.0)] * 16
#: A call placed after the bell on the last warm-up session (index 15).
CALL_TS = _ny_ms(_sessions(16)[15], 16, 30)
CALL_ISO = _iso(CALL_TS)
#: "Right now" for the still-running fixtures: the closing bell of the last session
#: present in an 18-bar frame. Asking as-of any LATER than the data ends is what STALE
#: is for, so an OPEN fixture has to stop exactly here.
AS_OF_DATA_END = _ny_ms(_sessions(18)[17], 16)


class TestScoreCall:
    def test_win_target_hit_with_stop_gives_rr(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="100", stop="90", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100), (100, 125, 99, 120)])
        sc = _score(call, df)
        assert sc.state == "WIN" and sc.win is True
        assert sc.fill_px == 100.0 and sc.exit_px == 120.0
        assert sc.r == 2.0  # (120-100)/(100-90)

    def test_gap_down_loss_is_worse_than_minus_one_r(self) -> None:
        """The headline equity difference: a gap through the stop costs more than 1R."""
        call = _call(
            call_ts_utc=CALL_ISO, entry="100", stop="95", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100), (90, 92, 88, 89)])
        sc = _score(call, df)
        assert sc.state == "LOSS"
        assert sc.exit_px == 90.0
        assert sc.r == -2.0  # (90-100)/5 — not the -1.0 a containment test would give

    def test_adverse_first_same_bar_is_loss(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="100", stop="90", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100), (101, 130, 85, 110)])
        sc = _score(call, df)
        assert sc.state == "LOSS" and sc.r == -1.0

    def test_thesis_entry_fills_at_the_next_open(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO,
            entry="unspecified",
            stop="",
            target="",
            horizon="swing",
        )
        df = _daily(WARMUP + [(104, 106, 103, 105)] * 22)
        sc = _score(call, df)
        assert sc.fill_px == 104.0  # the open AFTER the call, never the prior close
        assert sc.levels is not None and sc.levels.entry_is_thesis

    def test_intraday_call_expires_on_the_1h_frame_and_scales_by_risk(self) -> None:
        # Intraday -> 1h frame + a 2-session window. The thesis entry fills at the next
        # session's open (100), price then drifts to 104 and never touches a level, so
        # the window expires in profit at (104-100)/5 = 0.8R.
        rows = [(100.0, 101.0, 99.0, 100.0)] * 15 + [(104.0, 104.5, 103.5, 104.0)] * 34
        df = _hourly(rows)
        # Call placed after the 7th (final) bar of session 2 closes.
        call_ts = _ny_ms(_sessions(2)[1], 16, 30)
        sc = _score(
            _call(
                call_ts_utc=_iso(call_ts),
                entry="unspecified",
                stop="95",
                target="unspecified",
                horizon="intraday",
            ),
            df,
        )
        assert sc.call.timeframe == "1h"
        assert sc.state == "WIN"
        assert sc.fill_px == 100.0
        assert sc.r is not None and abs(sc.r - 0.8) < 1e-9

    def test_no_stop_uses_atr_proxy_and_sign(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="100", stop="", target="", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 95, 96)] * 22)
        sc = _score(call, df)
        assert sc.r is None  # no stop -> no R
        assert sc.atr_r is not None  # ATR14 has 16 warm-up sessions to work with

    def test_short_direction_win(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO,
            direction="short",
            entry="100",
            stop="110",
            target="80",
            horizon="swing",
        )
        df = _daily(WARMUP + [(100, 101, 99, 100), (99, 102, 75, 80)])
        sc = _score(call, df)
        assert sc.state == "WIN" and sc.r == 2.0  # (100-80)/(110-100)

    def test_open_in_position_before_expiry(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="100", stop="90", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100)] * 2)
        as_of = AS_OF_DATA_END
        sc = _score(call, df, as_of)
        assert sc.state == "OPEN" and sc.note == "in position"

    def test_open_awaiting_trigger(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="90", stop="85", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100)] * 2)
        as_of = AS_OF_DATA_END
        sc = _score(call, df, as_of)
        assert sc.state == "OPEN" and sc.note == "awaiting trigger"

    def test_not_triggered_after_deadline(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="90", stop="85", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100)] * 30)  # never trades down to 90
        sc = _score(call, df)
        assert sc.state == "NOT_TRIGGERED"

    def test_stale_when_data_ends_mid_window(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="100", stop="90", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100)] * 2)  # 21-session window, 2 sessions
        sc = _score(call, df)
        assert sc.state == "STALE" and "sync first" in sc.note

    def test_neutral_unscored_and_skip(self) -> None:
        df = _daily(WARMUP + [(100, 101, 99, 100)])
        assert _score(_call(call_ts_utc=CALL_ISO, direction="neutral"), df).state == (
            "UNSCORED"
        )
        ov = Override(url="https://x.com/A/status/1", skip=True)
        assert _score(_call(call_ts_utc=CALL_ISO), df, FAR, ov).state == "SKIPPED"

    def test_unresolvable_without_data(self) -> None:
        assert _score(_call(call_ts_utc=CALL_ISO), pd.DataFrame()).state == (
            "UNRESOLVABLE"
        )

    def test_unresolvable_when_data_starts_after_the_call(self) -> None:
        df = _daily([(100, 101, 99, 100)] * 3, start=date(2026, 9, 1))
        sc = _score(_call(call_ts_utc=CALL_ISO), df)
        assert sc.state == "UNRESOLVABLE" and "starts after call" in sc.note

    def test_stop_on_wrong_side_of_entry_is_unscored(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="100", stop="110", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100), (100, 125, 99, 120)])
        sc = _score(call, df)
        assert sc.state == "UNSCORED" and "wrong side" in sc.note

    def test_target_on_wrong_side_of_a_short_is_unscored_not_an_instant_win(
        self,
    ) -> None:
        """The phantom-WIN defect (seen 2026-08-04), in its original one-legged shape.

        A short quoting only "unless it reclaims 29,200" — a *stop* mis-written into
        ``target`` — leaves entry to fall back to the market. The target then sits above
        the fill, so the very first bar opens through it and books a WIN at ~0.00 R. The
        write-side pairwise rule cannot see this (there is no stated entry to contradict);
        it has to be caught here.
        """
        call = _call(
            call_ts_utc=CALL_ISO, direction="short", target="120", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100), (100, 101, 99, 100)])
        sc = _score(call, df)
        assert sc.state == "UNSCORED", f"expected UNSCORED, got {sc.state} R={sc.r}"
        assert "target" in sc.note and "wrong side" in sc.note

    def test_target_on_wrong_side_of_a_long_is_unscored(self) -> None:
        call = _call(
            call_ts_utc=CALL_ISO, entry="100", stop="90", target="80", horizon="swing"
        )
        df = _daily(WARMUP + [(100, 101, 99, 100), (100, 125, 99, 120)])
        sc = _score(call, df)
        assert sc.state == "UNSCORED" and "target" in sc.note


def _scored_fixture() -> list[ScoredCall]:
    win = _score(
        _call(
            call_ts_utc=CALL_ISO, entry="100", stop="90", target="120", horizon="swing"
        ),
        _daily(WARMUP + [(100, 101, 99, 100), (100, 125, 99, 120)]),
    )
    loss = _score(
        _call(
            call_ts_utc=CALL_ISO,
            author="B",
            url="https://x.com/B/status/2",
            entry="100",
            stop="90",
            target="120",
            horizon="swing",
            raw_quote="POC rotation",
        ),
        _daily(WARMUP + [(100, 101, 99, 100), (101, 130, 85, 110)]),
    )
    open_ = _score(
        _call(
            call_ts_utc=CALL_ISO,
            author="A",
            url="https://x.com/A/status/3",
            entry="100",
            stop="90",
            target="120",
            horizon="swing",
        ),
        _daily(WARMUP + [(100, 101, 99, 100)] * 2),
        AS_OF_DATA_END,
    )
    return [win, loss, open_]


class TestAggregateAndOutputs:
    def test_aggregate_per_author(self) -> None:
        cells = aggregate(_scored_fixture(), lambda sc: sc.call.author)
        a, b = cells["A"], cells["B"]
        assert (a.n, a.triggered, a.open_, a.resolved, a.wins) == (2, 2, 1, 1, 1)
        assert a.hit_rate == 1.0 and a.avg_r == 2.0
        assert (b.n, b.resolved, b.wins) == (1, 1, 0)
        assert b.avg_r == -1.0

    def test_audit_eligible_cells_is_inclusive_at_the_threshold(self) -> None:
        # `>=`, not `>`. The whole point is to notice the crossing, and an
        # off-by-one here would delay the notice by one observation forever.
        cells = {
            "under": CellStats(n=AUDIT_ELIGIBLE_N - 1),
            "at": CellStats(n=AUDIT_ELIGIBLE_N),
            "over": CellStats(n=AUDIT_ELIGIBLE_N + 5),
        }
        assert audit_eligible_cells(cells) == ["at", "over"]

    def test_audit_eligible_cells_empty_when_nothing_qualifies(self) -> None:
        assert audit_eligible_cells({"a": CellStats(n=1)}) == []

    def test_report_notes_the_crossing_and_says_no_gate_fires(self) -> None:
        # 15 copies of the fixture puts author A at n=30 -- exactly the boundary
        # (the fixture is [A, B, A], so each copy adds 2 to A).
        scored = _scored_fixture() * 15
        report = render_report(scored, [], "2026-08-04T00:00:00Z", 5)
        assert f"n≥{AUDIT_ELIGIBLE_N}" in report
        # It must say plainly that nothing fires. This notice replaced a
        # docstring that implied a live n>=30 gate; restating the same false
        # promise in the report would just move the defect.
        assert "No gate is implemented here and none fires" in report
        assert "A" in report

    def test_report_is_silent_when_no_cell_has_crossed(self) -> None:
        # Negative control: without this, a note printed unconditionally would
        # pass the test above while telling the operator nothing.
        report = render_report(_scored_fixture(), [], "2026-08-04T00:00:00Z", 5)
        assert f"n≥{AUDIT_ELIGIBLE_N}" not in report

    def test_render_report_sections_and_audit_trail(self) -> None:
        report = render_report(
            _scored_fixture(), ["ledger line 9: skipped"], "2026-08-04T00:00:00Z", 5
        )
        assert "## Per author" in report
        assert "## Per setup-family" in report
        assert "## Audit trail" in report
        assert "ledger line 9" in report
        assert "OPEN" in report and "WIN" in report and "LOSS" in report
        assert "⚠" in report
        assert "NYSE sessions" in report  # equity window policy is stated in the header
        # ATR-R leads avg R: it is the complete resolved sample,
        # where avg R is computed only over calls that stated a stop and so
        # carries its own (r_n/resolved) denominator. See CellStats.r_coverage.
        assert (
            "| author | n | trig | open | resolved | wins | losses "
            "| hit% | avg ATR-R | avg R (cov) | |" in report
        )
        assert "| A | 2 | 2 | 1 | 1 | 1 | 0 |" in report
        assert "| B | 1 | 1 | 0 | 1 | 0 | 1 |" in report
        # every markdown table (header/delimiter/every data row) has a uniform
        # column count — a mismatch is a real rendering defect.
        table: list[str] = []
        for line in [*report.splitlines(), ""]:
            if line.startswith("|"):
                table.append(line)
            elif table:
                counts = {ln.count("|") for ln in table}
                assert len(counts) == 1, table
                table = []

    def test_build_priors_schema_and_determinism(self) -> None:
        scored = _scored_fixture()
        p1 = build_priors(scored, "2026-08-04T00:00:00Z", "2026-08-04T09:00:00Z", 5)
        p2 = build_priors(scored, "2026-08-04T00:00:00Z", "2026-08-04T09:00:00Z", 5)
        assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
        assert p1["as_of"] == "2026-08-04T00:00:00Z"
        policy = p1["policy"]
        assert isinstance(policy, dict)
        assert policy["window_unit"] == "nyse_sessions"
        assert policy["windows_sessions"]["intraday"] == 2
        authors = p1["authors"]
        assert isinstance(authors, dict) and authors["A"]["n"] == 2
        # families is NESTED {family: {direction: stats}}, never a flat
        # "family/direction" key — the binding shape for the daily-brief consumer.
        families = p1["families"]
        assert isinstance(families, dict)
        for fam_key in families:
            assert "/" not in fam_key, "families must be nested, not 'family/direction'"
        other = families["other"]
        assert isinstance(other, dict)
        assert other["long"]["n"] == 2  # win + open_, both family=other/direction=long
        vp_level = families["vp_level"]
        assert isinstance(vp_level, dict)
        assert vp_level["long"]["n"] == 1  # loss call, raw_quote "POC rotation"
        assert vp_level["long"]["hit_rate"] == 0.0


class TestDbAndCli:
    def test_load_ohlcv_loads_the_horizon_frame_per_symbol(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        upsert_ohlcv(conn, _daily([(100, 110, 90, 105)] * 30))
        hourly = _hourly([(100, 110, 90, 105)] * 30)
        upsert_ohlcv(conn, hourly)
        swing = _call(call_ts_utc=CALL_ISO, horizon="swing")
        intraday = _call(call_ts_utc=CALL_ISO, horizon="intraday")
        data = load_ohlcv_for_calls(conn, [swing, intraday], FAR)
        assert set(data) == {(SYMBOL, "1d"), (SYMBOL, "1h")}
        assert not data[(SYMBOL, "1d")].empty
        assert list(data[(SYMBOL, "1d")]["open_time"]) == sorted(
            data[(SYMBOL, "1d")]["open_time"]
        )

    def test_load_ohlcv_skips_timeframes_no_call_asked_for(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        upsert_ohlcv(conn, _daily([(100, 110, 90, 105)] * 30))
        data = load_ohlcv_for_calls(conn, [_call(call_ts_utc=CALL_ISO)], FAR)
        assert set(data) == {(SYMBOL, "1d")}

    def test_build_parser_defaults(self) -> None:
        args = build_parser().parse_args([])
        assert args.ledger == Path("docs/plans/pundit-calls.jsonl")
        assert args.overrides == Path("docs/plans/pundit-overrides.jsonl")
        assert args.json == Path("docs/plans/pundit-priors.json")
        assert args.min_n == 5
        assert args.as_of is None

    def test_parse_as_of(self) -> None:
        args = build_parser().parse_args(["--as-of", "2026-08-04T00:00:00Z"])
        assert args.as_of == "2026-08-04T00:00:00Z"


def _cov_sc(state: str, r: float | None, atr_r: float | None, win: bool) -> ScoredCall:
    """ScoredCall carrying only what a cell roll-up reads. Reuses `_call`."""
    return ScoredCall(
        call=_call(),
        levels=None,
        family="other",
        state=state,
        r=r,
        atr_r=atr_r,
        win=win,
    )


class TestRCoverageDisclosure:
    """`avg_r` is computed only over calls that stated a stop, and WINNERS are
    the ones that disproportionately lack one — so `avg_r` silently describes a
    loss-enriched subsample while `n` describes the whole cell.

    Measured on this repo's own ledger 2026-08-12 (19 calls, 8 resolved): WIN
    r-coverage **1/3 = 33%** against LOSS **5/5 = 100%**, severe enough to
    invert the headline — `@fenggemeigu` reads avg_r **-0.41** where the
    complete atr_r sample is **+0.80**. Reproduce with
    `docs/plans/scripts/pundit_r_coverage.py`.

    Every fixture here is deliberately asymmetric (a win with no R beside a loss
    with one). A fixture where every call carries an R would make these
    assertions pass against the censored code too.
    """

    @staticmethod
    def _censored_cell() -> CellStats:
        c = CellStats()
        c.add(_cov_sc("WIN", None, 2.0, True))  # the dropped winner
        c.add(_cov_sc("LOSS", -1.0, -0.5, False))
        c.add(_cov_sc("WIN", 1.0, 1.0, True))
        return c

    def test_coverage_is_below_one_when_a_winner_lacks_a_stop(self) -> None:
        c = self._censored_cell()
        assert c.resolved == 3
        assert c.r_n == 2
        assert c.r_coverage == pytest.approx(2 / 3)

    def test_censoring_moves_avg_r_away_from_avg_atr_r(self) -> None:
        """The defect made visible: same three calls, two different answers."""
        c = self._censored_cell()
        avg_r, avg_atr_r = c.avg_r, c.avg_atr_r
        assert avg_r is not None and avg_atr_r is not None
        assert avg_r == pytest.approx(0.0)  # (-1.0 + 1.0) / 2
        assert avg_atr_r == pytest.approx((2.0 - 0.5 + 1.0) / 3)
        assert avg_r < avg_atr_r  # censoring biases avg_r DOWN

    def test_full_coverage_reports_one(self) -> None:
        c = CellStats()
        c.add(_cov_sc("WIN", 1.0, 1.0, True))
        c.add(_cov_sc("LOSS", -1.0, -1.0, False))
        assert c.r_coverage == pytest.approx(1.0)

    def test_coverage_is_none_with_nothing_resolved(self) -> None:
        c = CellStats()
        c.add(_cov_sc("OPEN", None, None, False))
        assert c.r_coverage is None

    def test_cell_dict_publishes_the_denominator(self) -> None:
        d = _cell_dict(self._censored_cell())
        assert d["r_n"] == 2
        assert d["resolved"] == 3
        assert d["r_coverage"] == pytest.approx(2 / 3)
        assert d["atr_r_n"] == 3

    def test_cell_table_shows_coverage_beside_avg_r(self) -> None:
        rows = _cell_table({"A": self._censored_cell()}, "author", min_n=1)
        body = rows[-1]
        assert "(2/3)" in body, f"coverage not disclosed in report row: {body}"

    def test_priors_json_carries_coverage(self) -> None:
        priors = build_priors(
            [
                _cov_sc("WIN", None, 2.0, True),
                _cov_sc("LOSS", -1.0, -0.5, False),
            ],
            "2026-08-12T00:00:00Z",
            "2026-08-12T00:00:00Z",
            min_n=1,
        )
        authors = priors["authors"]
        assert isinstance(authors, dict)
        cell = authors["A"]
        assert isinstance(cell, dict)
        assert cell["r_coverage"] == pytest.approx(0.5)
        assert cell["r_n"] == 1
