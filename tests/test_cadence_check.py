"""Tests for the cadence marker check.

Every check ships a **positive control** — an input that makes it fire — per the
lesson pinned in CLAUDE.md: a "did not change" assertion is satisfied both by the
invariant holding and by the perturbation never arriving.

The sharpest one here is :meth:`TestRoundTrip.test_a_stamped_mark_reads_back_fresh`.
It is not a tautology: the parent's version WRITES an ISO timestamp and READS
``st_mtime``, so its two halves measure different things and could disagree without
any test noticing. This asserts the writer and the reader agree on the same field.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.cadence_check import TASKS, Task, evaluate, parse_mark, stamp

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
WEEKLY = Task("/demo", "demo", 7.0, "the named consequence")


class TestParseMark:
    def test_trailing_z_is_utc(self) -> None:
        assert parse_mark("2026-08-20T06:45:52Z\n") == datetime(
            2026, 8, 20, 6, 45, 52, tzinfo=UTC
        )

    def test_comments_and_blanks_are_skipped(self) -> None:
        got = parse_mark("# stamped by /sanity-check\n\n2026-08-20T06:45:52Z\n")
        assert got is not None and got.day == 20

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        got = parse_mark("2026-08-20T06:45:52\n")
        assert got is not None and got.tzinfo is UTC

    def test_garbage_returns_none_rather_than_raising(self) -> None:
        """Every unreadable state must funnel to one verdict, never an exception."""
        assert parse_mark("not a timestamp\n") is None

    def test_an_empty_file_returns_none(self) -> None:
        assert parse_mark("\n\n") is None


class TestEvaluate:
    def test_a_missing_mark_is_OVERDUE(self, tmp_path: Path) -> None:
        """The fail-safe direction: a lost mark must shout, never look fresh."""
        st = evaluate(WEEKLY, tmp_path, NOW)
        assert st.overdue
        assert st.age_days is None
        assert "never recorded" in st.detail

    def test_a_fresh_mark_is_not_overdue(self, tmp_path: Path) -> None:
        (tmp_path / "demo").write_text(
            f"{NOW - timedelta(days=2):%Y-%m-%dT%H:%M:%SZ}\n"
        )
        st = evaluate(WEEKLY, tmp_path, NOW)
        assert not st.overdue
        assert st.age_days is not None and 1.9 < st.age_days < 2.1

    def test_a_stale_mark_IS_overdue(self, tmp_path: Path) -> None:
        """Positive control for the only transition that matters."""
        (tmp_path / "demo").write_text(
            f"{NOW - timedelta(days=8):%Y-%m-%dT%H:%M:%SZ}\n"
        )
        st = evaluate(WEEKLY, tmp_path, NOW)
        assert st.overdue
        assert "OVERDUE" in st.detail

    def test_the_boundary_is_STRICTLY_greater(self, tmp_path: Path) -> None:
        """Exactly at the period is still fresh — pin it so a refactor cannot drift it."""
        (tmp_path / "demo").write_text(
            f"{NOW - timedelta(days=7):%Y-%m-%dT%H:%M:%SZ}\n"
        )
        assert not evaluate(WEEKLY, tmp_path, NOW).overdue

    def test_an_UNREADABLE_mark_is_overdue_not_fresh(self, tmp_path: Path) -> None:
        """The direction that matters: a corrupt mark must never report fresh.

        An mtime fallback would fail the other way — it reports *fresher than
        reality*, because touching a file is not running the task.
        """
        (tmp_path / "demo").write_text("corrupted\n")
        st = evaluate(WEEKLY, tmp_path, NOW)
        assert st.overdue
        assert "unreadable" in st.detail
        assert st.age_days is None


class TestStamp:
    def test_it_refuses_an_undeclared_task(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as e:
            stamp("not-a-task", tmp_path, NOW)
        assert "unknown task" in str(e.value)

    def test_it_creates_the_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "nested" / "task-marks"
        stamp(TASKS[0].slug, root, NOW)
        assert (root / TASKS[0].slug).exists()


class TestRoundTrip:
    def test_a_stamped_mark_reads_back_fresh(self, tmp_path: Path) -> None:
        """Writer and reader must agree on the SAME field.

        The parent writes an ISO line and reads `st_mtime` — two fields for one
        fact, which can disagree with nothing noticing. This is the control that
        would catch that split here.
        """
        task = TASKS[0]
        stamp(task.slug, tmp_path, NOW)
        st = evaluate(task, tmp_path, NOW)
        assert not st.overdue
        assert st.age_days is not None and st.age_days < 0.001

    def test_a_stamp_goes_stale_after_its_period(self, tmp_path: Path) -> None:
        task = TASKS[0]
        stamp(task.slug, tmp_path, NOW)
        assert evaluate(task, tmp_path, NOW + timedelta(days=8)).overdue


class TestDeclaredTasks:
    """The inclusion rule is prose; these pin the parts of it that are checkable."""

    def test_every_task_names_its_consequence(self) -> None:
        """Rule 2: staleness must have a NAMED consequence, or the line is noise."""
        for t in TASKS:
            assert t.consequence.strip(), t.slug

    def test_slugs_are_unique_and_filename_safe(self) -> None:
        slugs = [t.slug for t in TASKS]
        assert len(slugs) == len(set(slugs))
        for s in slugs:
            assert "/" not in s and s == s.strip()

    def test_every_period_is_positive(self) -> None:
        for t in TASKS:
            assert t.every_days > 0, t.slug
