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

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tools import session_digest
from tools.cadence_check import (
    TASKS,
    Task,
    evaluate,
    fetch_issue_text,
    issue_text,
    join_verdicts,
    parse_mark,
    sot_path,
    stamp,
)
from tools.claude_home import memory_dir, project_slug

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
            f"{NOW - timedelta(days=2):%Y-%m-%dT%H:%M:%SZ}\n", encoding="utf-8"
        )
        st = evaluate(WEEKLY, tmp_path, NOW)
        assert not st.overdue
        assert st.age_days is not None and 1.9 < st.age_days < 2.1

    def test_a_stale_mark_IS_overdue(self, tmp_path: Path) -> None:
        """Positive control for the only transition that matters."""
        (tmp_path / "demo").write_text(
            f"{NOW - timedelta(days=8):%Y-%m-%dT%H:%M:%SZ}\n", encoding="utf-8"
        )
        st = evaluate(WEEKLY, tmp_path, NOW)
        assert st.overdue
        assert "OVERDUE" in st.detail

    def test_the_boundary_is_STRICTLY_greater(self, tmp_path: Path) -> None:
        """Exactly at the period is still fresh — pin it so a refactor cannot drift it."""
        (tmp_path / "demo").write_text(
            f"{NOW - timedelta(days=7):%Y-%m-%dT%H:%M:%SZ}\n", encoding="utf-8"
        )
        assert not evaluate(WEEKLY, tmp_path, NOW).overdue

    def test_an_UNREADABLE_mark_is_overdue_not_fresh(self, tmp_path: Path) -> None:
        """The direction that matters: a corrupt mark must never report fresh.

        An mtime fallback would fail the other way — it reports *fresher than
        reality*, because touching a file is not running the task.
        """
        (tmp_path / "demo").write_text("corrupted\n", encoding="utf-8")
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


def _row(date: str, verdict: str, stem: str) -> str:
    return f"| {date} | a title | {verdict} | [{stem}.md]({stem}.md) |"


class TestVerdictJoin:
    """The audit-verdict → SoT ownership join (the other half of parent #641).

    The parent's predicates live in a gitignored file and are proven by a
    hand-run sibling that DUPLICATES them; here the module is tracked and
    importable, so the real predicates are under test — drift is impossible.
    """

    STEM = "2026-08-20-audit-guard-cross-sectional-clustering"

    def test_the_decisive_mutation_strip_the_owner_and_it_reds(self) -> None:
        """The parent's seven-week class: same index, owning SoT row removed."""
        idx = _row("2026-08-20", "FOUND — the CI is too narrow", self.STEM)
        owned = join_verdicts(idx, f"closed by docs/audits/{self.STEM}.md")
        assert owned.unowned == ()
        orphaned = join_verdicts(idx, "a SoT that names nothing")
        assert orphaned.unowned == (self.STEM,)

    def test_the_live_index_flags_against_an_empty_sot(self) -> None:
        """Positive control on the COMMITTED corpus: the predicates reach it.

        If this audit's verdict text is ever legitimately edited, this pin
        moves with it — update the stem, do not weaken the empty-SoT assertion.
        """
        idx = Path("docs/audits/INDEX.md").read_text(encoding="utf-8")
        res = join_verdicts(idx, "")
        assert res.total >= 41
        assert self.STEM in res.unowned

    def test_a_settled_FOUND_never_flags(self) -> None:
        for verdict in (
            "FOUND and FIXED — a coverage defect",
            "FOUND, and closed in this branch.",
            "FOUND but DECLINED as net harmful",
        ):
            res = join_verdicts(_row("2026-08-26", verdict, "some-audit"), "")
            assert res.unowned == (), verdict

    def test_INSUFFICIENT_carrying_a_CANDIDATE_still_flags(self) -> None:
        """Pins that INSUFFICIENT stays OFF the settled list: the one live
        SUPPRESS-CANDIDATE rides in an 'INSUFFICIENT on 11 of 12' verdict, and
        a global veto would silently skip exactly the row with the payload."""
        idx = _row(
            "2026-08-13",
            "INSUFFICIENT on 11 of 12 cells; one SUPPRESS-CANDIDATE",
            "warning-audit",
        )
        assert join_verdicts(idx, "").unowned == ("warning-audit",)

    def test_a_blind_verdict_counts_and_never_flags(self) -> None:
        res = join_verdicts(_row("2026-08-07", "—", "grandfathered"), "")
        assert res.unowned == () and res.blind == 1 and res.total == 1

    def test_rows_match_by_date_shape_not_by_year(self) -> None:
        """The parent keys on `| 2026-` and goes blind — green on 0/0 — at the
        new year. A 2027 row must parse."""
        res = join_verdicts(_row("2027-01-05", "FOUND — a defect", "next-year"), "")
        assert res.total == 1 and res.unowned == ("next-year",)

    def test_a_malformed_row_is_skipped_not_crashed(self) -> None:
        assert join_verdicts("| 2026-08-20 | too few cells |", "").total == 0

    def test_zero_rows_is_reported_as_zero_not_as_clean(self) -> None:
        """total=0 is the parser-drift state; the caller prints it loud."""
        res = join_verdicts("# no table here\n", "anything")
        assert res.total == 0 and res.blind == 0 and res.unowned == ()

    def test_sot_path_encodes_the_repo_root(self) -> None:
        """Pinned against the shared derivation, not against a literal.

        The literal it replaced hardcoded `.claude-personal` and a POSIX-only
        slug, so on Windows it asserted a path no host has and the check it
        guards silently found nothing. The RULE itself is pinned
        platform-independently in `test_claude_home.py`; what matters here is
        that `sot_path` routes through it rather than growing a sixth copy.
        """
        repo = Path("/srv/demo")
        assert sot_path(repo) == memory_dir(repo) / "project_todo_master.md"
        assert sot_path(repo).parent.parent.name == project_slug(repo)


def _issue(number: int, body: str | None = "", **extra: Any) -> dict[str, Any]:
    return {"number": number, "title": f"issue {number}", "body": body, **extra}


class TestIssueOwners:
    """Issues own audit verdicts since planning moved there (#379)."""

    STEM = "2026-08-20-audit-guard-cross-sectional-clustering"

    def test_an_issue_body_naming_the_audit_owns_it(self) -> None:
        """Decisive mutation with an Issue as owner: drop the Issue, and it reds."""
        idx = _row("2026-08-20", "FOUND — the CI is too narrow", self.STEM)
        owner = issue_text([_issue(400, f"see docs/audits/{self.STEM}.md")])
        assert join_verdicts(idx, owner).unowned == ()
        assert join_verdicts(idx, issue_text([_issue(401)])).unowned == (self.STEM,)

    def test_a_null_body_reads_as_empty(self) -> None:
        assert issue_text([_issue(403, None)]) == "#403 issue 403\n"


class _FakeGh:
    """Stands in for `subprocess.run`, serving one JSON page per `gh api` call."""

    def __init__(self, pages: list[list[dict[str, Any]]], rc: int = 0) -> None:
        self.pages, self.rc = pages, rc
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "auth", "token"]:
            return subprocess.CompletedProcess(cmd, 0, "tok\n", "")
        page = self.pages[len([c for c in self.calls if c[1] == "api"]) - 1]
        return subprocess.CompletedProcess(cmd, self.rc, json.dumps(page), "denied")


class TestFetchIssueText:
    """The REST pager lives in `session_digest` and serves both tools."""

    def test_a_pull_request_never_owns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The issues endpoint returns PRs too; only an Issue is an owner."""
        stem = TestIssueOwners.STEM
        pr = _issue(402, f"fixes {stem}", pull_request={"url": "x"})
        fake = _FakeGh([[pr, _issue(403, "an Issue")]])
        monkeypatch.setattr(session_digest.subprocess, "run", fake)
        text, _ = fetch_issue_text()
        assert text is not None and "an Issue" in text and stem not in text

    def test_ownership_reads_every_state_and_the_digest_reads_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A closed Issue still owns (it records where the work was done)."""
        fake = _FakeGh([[], []])
        monkeypatch.setattr(session_digest.subprocess, "run", fake)
        fetch_issue_text()
        session_digest.fetch_issues()
        api = [c[2] for c in fake.calls if c[1] == "api"]
        assert "state=all" in api[0] and "state=open" in api[1]

    def test_pages_are_walked_until_a_short_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        full = [_issue(n) for n in range(100)]
        fake = _FakeGh([full, [_issue(500, "the last page")]])
        monkeypatch.setattr(session_digest.subprocess, "run", fake)
        text, err = fetch_issue_text()
        assert err == "" and text is not None
        assert "#0 issue 0" in text and "the last page" in text
        api = [c for c in fake.calls if c[1] == "api"]
        assert [c[2].rsplit("page=", 1)[1] for c in api] == ["1", "2"]

    def test_the_command_is_REST_and_never_follows_link_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`gh issue list` is GraphQL and `--paginate` follows `repositories/{id}`
        Link URLs; the cloud proxy refuses both, so neither may come back."""
        fake = _FakeGh([[]])
        monkeypatch.setattr(session_digest.subprocess, "run", fake)
        fetch_issue_text()
        (api,) = [c for c in fake.calls if c[1] == "api"]
        assert api[2].startswith("repos/") and "--paginate" not in api

    def test_a_gh_failure_is_None_never_an_empty_owner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unread source must not read as one that names nothing."""
        monkeypatch.setattr(session_digest.subprocess, "run", _FakeGh([[]], rc=1))
        text, err = fetch_issue_text()
        assert text is None and err == "denied"
