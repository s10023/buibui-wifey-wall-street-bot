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
