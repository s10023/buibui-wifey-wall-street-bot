import json
from pathlib import Path
from typing import Any

import pytest

from tools.x_route import (
    check_level_order,
    check_row_levels,
    first_level,
    main,
    route_target,
)


@pytest.mark.parametrize(
    "content_type, verdict, expected",
    [
        ("setup", "NOVEL", "docs/plans/pundit-calls.jsonl"),
        ("mechanic", "NOVEL", "docs/plans/mechanics-backlog.md"),
        ("claim", "NOVEL", "docs/plans/thesis-inbox.md"),
        ("claim", "ALREADY-TESTED", None),
        ("claim", "FROZEN-CATEGORY", None),
        ("claim", "NOT-FALSIFIABLE", None),
    ],
)
def test_route_target(content_type: str, verdict: str, expected: str | None) -> None:
    assert route_target(content_type, verdict) == expected


def test_route_target_unroutable() -> None:
    with pytest.raises(ValueError):
        route_target("claim", "BOGUS")


# ---------------------------------------------------------------------------
# Setup suppressors (parent #521, ported 2026-08-13). Both drops used to live only in
# the skills' markdown routing table, so each depended on the orchestrator reading
# prose correctly at the end of a long batch. `rejected` is here because upstream's
# 2026-07-31 round 3 shipped one: a pundit walked through a short and then explicitly
# argued AGAINST taking it, which is `setup` + `retrospective: false`, so the table
# routed it to Stream C and pundit_score.py scored him on a trade he declined. Only
# the digest reader caught it.
#
# In wifey the exposure was wider: `/ingest-video` pass 1 SETS `retrospective` on any
# setup lifted from a channel's intro recap, and nothing read it — so a recap call
# routed carrying today's `call_ts_utc` and was scored on an already-resolved trade.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["retrospective", "rejected"])
def test_a_suppressed_setup_is_dropped(flag: str) -> None:
    assert route_target("setup", "NOVEL", **{flag: True}) is None


def test_an_ordinary_setup_still_routes_to_stream_c() -> None:
    assert route_target("setup", "NOVEL") == "docs/plans/pundit-calls.jsonl"


# The flags describe a trade call. A mechanic or a claim has no entry to decline,
# and both skills already pin them to false — honouring the flag there would let a
# mis-set field silently delete a routable item.
@pytest.mark.parametrize(
    "content_type, expected",
    [
        ("mechanic", "docs/plans/mechanics-backlog.md"),
        ("claim", "docs/plans/thesis-inbox.md"),
    ],
)
def test_suppressors_do_not_apply_outside_setup(
    content_type: str, expected: str
) -> None:
    assert (
        route_target(content_type, "NOVEL", retrospective=True, rejected=True)
        == expected
    )


class TestFirstLevel:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("290 (fib cluster 286-291)", 290.0),
            ("Last week's high, roughly 1,038-1,040", 1038.0),
            ("~420 (current market, no explicit entry stated)", 420.0),
            ("$29,200 — a reclaim above it", 29200.0),
            ("60.5k", 60500.0),
            ("", None),
            ("not stated", None),
            (None, None),
        ],
    )
    def test_extracts_the_first_price_like_number(
        self, text: str | None, expected: float | None
    ) -> None:
        assert first_level(text) == expected

    def test_strips_month_anchored_years(self) -> None:
        """The 2,026-on-a-7,436-index trap — a year is not a level."""
        assert first_level("drawdown starting Aug-Sep 2026, down to 6,800") == 6800.0

    def test_strips_percentages(self) -> None:
        """'10-20% drawdown' must not read as a level of 10."""
        assert first_level("10-20% drawdown toward 5,900") == 5900.0

    @pytest.mark.parametrize(
        "text, expected",
        [
            # The 2026-08-05 gold row: entry read as 4, sign-checked against stop 4000.
            ("break above the 4h descending trendline", None),
            ("break above the 4h trendline, then 4,180", 4180.0),
            ("the 1d chart support at 190", 190.0),
            ("wait for the 15m close above 6,800", 6800.0),
            ("1wk structure, target 235", 235.0),
            # A CJK channel names the frame on nearly every setup.
            ("4小时级别的下降趋势线", None),
            ("日线级别回踩 4,000", 4000.0),
            ("30分钟结构确认后 210", 210.0),
        ],
    )
    def test_strips_chart_timeframes(self, text: str, expected: float | None) -> None:
        """'the 4h trendline' must not read as a level of 4."""
        assert first_level(text) == expected

    def test_timeframe_stripping_leaves_real_levels_alone(self) -> None:
        """Bare numbers and decimal magnitudes must survive the timeframe pass."""
        assert first_level("190") == 190.0
        assert first_level("3800-3900") == 3800.0
        assert first_level("60.5k") == 60500.0
        # A decimal magnitude is not an integer+unit token, so it is untouched.
        assert first_level("4.5m") == 4.5

    def test_timeframe_in_entry_no_longer_fakes_an_ordering_pass(self) -> None:
        """The silent half of the bug: a stripped '4h' must not out-rank a real stop.

        Before the fix ``entry`` parsed as 4, so a long with a stop of 3 read as
        correctly ordered (4 > 3) and passed — a wrong-sided leg waved through.
        """
        entry = first_level("break above the 4h trendline")
        assert entry is None
        assert check_level_order("long", entry=entry, stop=3.0, target=None) == ""


class TestCheckLevelOrder:
    def test_well_formed_long_and_short_pass(self) -> None:
        assert check_level_order("long", entry=100, stop=90, target=120) == ""
        assert check_level_order("short", entry=100, stop=110, target=80) == ""

    def test_target_on_the_wrong_side_of_a_short_is_caught(self) -> None:
        """The 2026-08-04 defect: a reclaim *stop* written into a short's target."""
        note = check_level_order("short", entry=28274, target=29200)
        assert "target 29200 on the wrong side of entry 28274" in note
        assert "for a short" in note

    def test_target_on_the_wrong_side_of_a_long_is_caught(self) -> None:
        assert "wrong side" in check_level_order("long", entry=100, target=80)

    def test_stop_on_the_wrong_side_is_caught(self) -> None:
        assert "wrong side" in check_level_order("long", entry=100, stop=110)
        assert "wrong side" in check_level_order("short", entry=100, stop=90)

    def test_stop_target_pair_is_checked_without_an_entry(self) -> None:
        assert "wrong side" in check_level_order("long", stop=120, target=90)
        assert check_level_order("long", stop=90, target=120) == ""

    def test_every_violated_pair_is_reported(self) -> None:
        """A fully transposed long names all three broken pairs, not just the first."""
        note = check_level_order("long", entry=100, stop=120, target=80)
        assert note.count("wrong side") == 3

    def test_equality_is_a_violation(self) -> None:
        """Zero risk and zero reward both yield a garbage R, not a trade."""
        assert "wrong side" in check_level_order("long", entry=100, stop=100)
        assert "wrong side" in check_level_order("long", entry=100, target=100)

    @pytest.mark.parametrize("direction", ["long", "short", "LONG", " Short "])
    def test_direction_is_case_and_space_tolerant(self, direction: str) -> None:
        assert check_level_order(direction, entry=100) == ""

    def test_missing_legs_are_skipped(self) -> None:
        assert check_level_order("short", target=29200) == ""
        assert check_level_order("long") == ""

    def test_unjudgeable_direction_warns_rather_than_passing_silently(self) -> None:
        note = check_level_order("neutral", entry=100, stop=110, target=120)
        assert "neither long nor short" in note


class TestCheckRowLevels:
    def test_free_text_row_passes(self) -> None:
        row = {
            "symbol": "MU",
            "direction": "short",
            "entry": "Short with stop at last week's high",
            "stop": "Last week's high, roughly 1,038-1,040",
            "target": "A breakdown of the 740 level",
        }
        assert check_row_levels(row) == ""

    def test_free_text_row_with_a_transposed_leg_warns(self) -> None:
        row = {
            "symbol": "^NDX",
            "direction": "short",
            "entry": "28,274 current",
            "stop": "",
            "target": "29,200 — unless it reclaims it",
        }
        assert "wrong side" in check_row_levels(row)

    def test_numeric_px_fields_win_over_the_text(self) -> None:
        row: dict[str, Any] = {
            "direction": "long",
            "entry": "garbled 999",
            "entry_px": 100.0,
            "target_px": 80.0,
        }
        assert "entry 100" in check_row_levels(row)

    def test_missing_fields_are_tolerated(self) -> None:
        assert check_row_levels({"direction": "long"}) == ""


class TestCheckLevelsCli:
    def _write(self, tmp_path: Path, rows: list[dict[str, Any]]) -> str:
        path = tmp_path / "rows.jsonl"
        path.write_text(
            "".join(json.dumps(r) + "\n" for r in rows) + "\n", encoding="utf-8"
        )
        return str(path)

    def test_clean_file_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        src = self._write(
            tmp_path,
            [{"symbol": "AAPL", "direction": "long", "entry": "100", "target": "120"}],
        )
        monkeypatch.setattr("sys.argv", ["x_route.py", "--check-levels", src])
        assert main() == 0
        assert "OK AAPL long" in capsys.readouterr().out

    def test_bad_row_warns_and_exits_one_without_mutating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        row = {
            "symbol": "^NDX",
            "direction": "short",
            "entry": "28274",
            "target": "29200",
        }
        src = self._write(tmp_path, [row])
        before = Path(src).read_text(encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["x_route.py", "--check-levels", src])
        assert main() == 1
        out = capsys.readouterr().out
        assert "WARN ^NDX short" in out and "nothing was modified" in out
        assert Path(src).read_text(encoding="utf-8") == before

    def test_unparseable_line_warns_rather_than_crashing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        path = tmp_path / "rows.jsonl"
        path.write_text("{not json\n[1, 2]\n", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["x_route.py", "--check-levels", str(path)])
        assert main() == 1
        out = capsys.readouterr().out
        assert "WARN unparseable" in out and "WARN not a JSON object" in out

    def test_requires_the_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["x_route.py"])
        with pytest.raises(SystemExit):
            main()
