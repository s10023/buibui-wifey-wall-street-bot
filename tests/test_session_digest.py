"""The pure half of `tools/session_digest.py`: report -> finding mapping, Issue
ordering, handoff parsing, rendering. The probes it composes are tested in their
own modules; the I/O (gh, Task Scheduler, analytics.db) is not exercised here."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import session_digest as sd
from tools.freshness_check import Graded, OhlcvReport, SignalReport


def _ohlcv(stale: int = 0, scheduled: int = 26, unscheduled: int = 0) -> OhlcvReport:
    graded = [
        Graded("AAPL", "1d", date(2026, 9, 18 - i), 7 + i, 7.0 + i)
        for i in range(stale)
    ]
    return OhlcvReport(graded, scheduled, unscheduled, None, None)


def _signal(newest: date | None = date(2026, 9, 24)) -> SignalReport:
    return SignalReport(newest, 3, None, 242, 0, Path("signal_state.json"))


def _issue(n: int, *labels: str, title: str = "t") -> dict[str, Any]:
    return {"number": n, "title": title, "labels": [{"name": x} for x in labels]}


class TestScheduler:
    def test_an_unscheduled_signal_watch_is_RED_and_names_the_installer(self) -> None:
        (f,) = sd.scheduler_findings(False)
        assert f.level == "RED"
        assert "install-tasks.ps1" in f.action

    def test_a_scheduled_signal_watch_is_silent(self) -> None:
        assert sd.scheduler_findings(True) == []


class TestFreshness:
    def test_stale_watchlist_is_RED_with_the_oldest_session(self) -> None:
        (f,) = sd.freshness_findings(_signal(), _ohlcv(stale=3))
        assert f.level == "RED"
        assert "3 of 26" in f.detail
        assert "2026-09-16" in f.detail  # the oldest of 18, 17, 16

    def test_fresh_watchlist_is_silent(self) -> None:
        assert sd.freshness_findings(_signal(), _ohlcv()) == []

    def test_an_unreadable_db_is_AMBER_never_silent(self) -> None:
        """Positive control for the silent case above: 'nothing read' must not
        render like 'all fresh'."""
        (f,) = sd.freshness_findings(_signal(), _ohlcv(scheduled=0, unscheduled=0))
        assert f.level == "AMBER"

    def test_missing_watermarks_are_RED(self) -> None:
        findings = sd.freshness_findings(_signal(newest=None), _ohlcv())
        assert [f.label for f in findings] == ["no dispatch watermarks"]


class TestBackupAndCadence:
    def test_a_stale_backup_is_RED(self) -> None:
        report = SimpleNamespace(ok=False, verdict="STALE", detail="10.0d old")
        (f,) = sd.backup_findings(report)
        assert (f.level, f.label) == ("RED", "backup STALE")

    def test_a_fresh_backup_is_silent(self) -> None:
        assert sd.backup_findings(SimpleNamespace(ok=True)) == []

    def test_only_overdue_cadence_tasks_are_listed(self) -> None:
        task = SimpleNamespace(label="/sanity-check", slug="sanity-check")
        statuses = [
            SimpleNamespace(task=task, overdue=True, detail="9d ago"),
            SimpleNamespace(task=task, overdue=False, detail="1d ago"),
        ]
        (f,) = sd.cadence_findings(statuses)
        assert "make cadence-stamp TASK=sanity-check" in f.action


class TestIssues:
    def test_priority_then_number_with_untriaged_last(self) -> None:
        issues = [_issue(5), _issue(9, "p2"), _issue(2, "p3"), _issue(7, "p1")]
        assert [i["number"] for i in sd.sort_issues(issues)] == [7, 9, 2, 5]

    def test_format_shows_priority_first_then_other_labels(self) -> None:
        line = sd.format_issue(_issue(3, "ops", "p1", title="Fix it"))
        assert line == "#3 [p1 effort:? ops] Fix it"

    def test_the_effort_label_follows_the_priority(self) -> None:
        line = sd.format_issue(_issue(4, "ops", "effort:high", "p2", title="Do"))
        assert line == "#4 [p2 effort:high ops] Do"

    def test_an_unset_effort_reads_effort_question_mark(self) -> None:
        assert "[untriaged effort:?]" in sd.format_issue(_issue(1))

    def test_an_unlabelled_issue_reads_untriaged(self) -> None:
        assert "[untriaged " in sd.format_issue(_issue(1))


class TestHandoff:
    def test_first_move_headings_are_extracted(self) -> None:
        text = "# Next\n\n## ▶ FIRST MOVE — ship X\nbody\n## Other\n"
        assert sd.handoff_first_moves(text) == ["FIRST MOVE — ship X"]


class TestRender:
    def test_a_failed_issue_fetch_is_LOUD_and_distinct_from_zero(self) -> None:
        broke = sd.render([], None, "gh exit 1", [], for_model=False)
        empty = sd.render([], [], "", [], for_model=False)
        assert "BROKE could not fetch open Issues: gh exit 1" in broke
        assert "Open Issues (0):" in empty
        assert "BROKE" not in empty

    def test_the_model_banner_appears_only_for_the_hook(self) -> None:
        f = [sd.Finding("RED", "x", "y")]
        assert "Lead your first reply" in sd.render(f, [], "", [], for_model=True)
        assert "Lead your first reply" not in sd.render(f, [], "", [], for_model=False)

    def test_header_counts_reds(self) -> None:
        f = [sd.Finding("RED", "a", "b"), sd.Finding("AMBER", "c", "d")]
        assert "wifey daily check — 1 RED" in sd.render(f, [], "", [], for_model=False)

    def test_max_issues_truncates_with_a_count(self) -> None:
        issues = [_issue(n, "p2") for n in range(1, 6)]
        out = sd.render([], issues, "", [], for_model=False, max_issues=2)
        assert "Open Issues (5):" in out
        assert "… and 3 more" in out


def _weekdays(start: date, end: date) -> list[date]:
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5]


class TestCore:
    def test_pre_open_expects_yesterdays_close_only(self) -> None:
        now = datetime(2026, 10, 8, 9, 15, tzinfo=UTC)  # Thursday, pre-open
        assert sd.core_findings(date(2026, 10, 7), now, _weekdays) == []

    def test_a_missed_session_is_amber(self) -> None:
        now = datetime(2026, 10, 8, 9, 15, tzinfo=UTC)
        (f,) = sd.core_findings(date(2026, 10, 6), now, _weekdays)
        assert (f.level, f.label, f.action) == ("AMBER", "core stale", "make core-sync")
        assert "1 closed session(s) missing" in f.detail

    def test_todays_bar_is_owed_once_the_session_has_closed(self) -> None:
        now = datetime(2026, 10, 8, 21, 30, tzinfo=UTC)
        assert sd.core_findings(date(2026, 10, 7), now, _weekdays)
        assert sd.core_findings(date(2026, 10, 8), now, _weekdays) == []

    def test_a_weekend_owes_nothing(self) -> None:
        now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)  # Sunday
        assert sd.core_findings(date(2026, 10, 9), now, _weekdays) == []

    def test_render_places_the_core_line_and_omits_it_when_empty(self) -> None:
        out = sd.render([], [], "", [], for_model=False, core="Core OV-1×VM: x")
        assert out.splitlines()[2] == "Core OV-1×VM: x"
        assert "Core" not in sd.render([], [], "", [], for_model=False)

    def test_main_sends_the_core_line_to_telegram(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[str] = []
        monkeypatch.setattr(sd, "collect_findings", list)
        monkeypatch.setattr(sd, "fetch_issues", lambda: ([], ""))
        monkeypatch.setattr(sd, "collect_core", lambda: ("Core OV-1×VM: x", []))
        monkeypatch.setattr("utils.telegram.send_telegram_message", sent.append)
        monkeypatch.setattr("dotenv.load_dotenv", lambda: None)
        monkeypatch.setattr("sys.argv", ["session_digest.py", "--telegram"])
        sd.main()
        assert len(sent) == 1
        assert "Core OV-1×VM: x" in sent[0]


class TestNeverCrashes:
    def test_a_raising_probe_becomes_a_BROKE_line(self) -> None:
        def boom() -> list[sd.Finding]:
            raise RuntimeError("db gone")

        (f,) = sd._guarded("freshness probe", boom)
        assert f.level == "BROKE"
        assert "db gone" in f.detail

    def test_hook_mode_exits_zero_even_with_reds(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            sd, "collect_findings", lambda: [sd.Finding("RED", "x", "y")]
        )
        monkeypatch.setattr(sd, "fetch_issues", lambda: (None, "offline"))
        monkeypatch.setattr(sd, "collect_core", lambda: ("", []))
        monkeypatch.setattr("sys.argv", ["session_digest.py"])
        sd.main()  # would raise SystemExit on a non-zero exit
        assert "RED   x: y" in capsys.readouterr().out

    def test_exit_nonzero_opts_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sd, "collect_findings", lambda: [sd.Finding("RED", "x", "y")]
        )
        monkeypatch.setattr(sd, "fetch_issues", lambda: ([], ""))
        monkeypatch.setattr(sd, "collect_core", lambda: ("", []))
        monkeypatch.setattr("sys.argv", ["session_digest.py", "--exit-nonzero"])
        with pytest.raises(SystemExit) as exc:
            sd.main()
        assert exc.value.code == 1
