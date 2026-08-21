"""Tests for the backup staleness probe.

Every check ships a **positive control** — an input that makes it fire — per the
lesson pinned in CLAUDE.md: a "did not change" assertion is satisfied both by the
invariant holding and by the perturbation never arriving.

Two here are not tautologies:

:meth:`TestContentIsAuthoritative.test_a_fresh_mtime_cannot_rescue_an_old_manifest`
is the whole reason this probe reads ``captured_at_utc`` instead of ``st_mtime``.
The two fields are independent, so an implementation that quietly fell back to
mtime would still pass every other test in this file — it would simply report a
restored or rsynced tree as fresh. This is the only test that separates them.

:meth:`TestTiersAreDifferentArtifacts.test_weekly_parquet_is_not_an_unreadable_snapshot`
pins a false positive the first draft actually shipped. ``weekly/`` holds a parquet
export and carries no ``MANIFEST.json`` by design, so grading it as a snapshot
printed a permanent warning about a directory that was exactly as intended — and a
check that is never clean stops being read.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.backup_check import (
    ARCHIVE_TIER,
    DEFAULT_MAX_AGE_DAYS,
    SNAPSHOT_TIER,
    evaluate,
    read_captured_at,
    render,
)

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)


def write_snapshot(root: Path, day: str, captured: datetime | str | None) -> Path:
    """Publish a snapshot dir; ``captured`` None omits the field, str writes it raw."""
    d = root / SNAPSHOT_TIER / day
    d.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"method": "copy-from-database"}
    if captured is not None:
        payload["captured_at_utc"] = (
            captured if isinstance(captured, str) else f"{captured:%Y-%m-%dT%H:%M:%S}Z"
        )
    (d / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


def write_archive(root: Path, day: str) -> Path:
    """Publish a weekly parquet export — deliberately with no manifest."""
    d = root / ARCHIVE_TIER / day / "parquet"
    d.mkdir(parents=True, exist_ok=True)
    (d / "backtest_runs.parquet").write_bytes(b"\x00")
    return d.parent


class TestFreshAndStale:
    def test_todays_snapshot_is_fresh(self, tmp_path: Path) -> None:
        write_snapshot(tmp_path, "2026-08-21", NOW - timedelta(hours=3))
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "FRESH"
        assert report.ok
        assert report.age_days is not None and report.age_days < 1.0

    def test_snapshot_past_the_bar_is_stale(self, tmp_path: Path) -> None:
        """Positive control for the bar: same tree, only the age moved."""
        write_snapshot(tmp_path, "2026-08-15", NOW - timedelta(days=6))
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "STALE"
        assert not report.ok
        assert report.age_days is not None and report.age_days > DEFAULT_MAX_AGE_DAYS

    def test_newest_wins_when_several_exist(self, tmp_path: Path) -> None:
        write_snapshot(tmp_path, "2026-08-10", NOW - timedelta(days=11))
        write_snapshot(tmp_path, "2026-08-21", NOW - timedelta(hours=1))
        assert evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS).verdict == "FRESH"

    def test_the_bar_is_a_parameter_not_a_constant(self, tmp_path: Path) -> None:
        """The same tree grades both ways, so the bar is genuinely doing work."""
        write_snapshot(tmp_path, "2026-08-18", NOW - timedelta(days=3))
        assert evaluate(tmp_path, NOW, 2.0).verdict == "STALE"
        assert evaluate(tmp_path, NOW, 7.0).verdict == "FRESH"


class TestContentIsAuthoritative:
    def test_a_fresh_mtime_cannot_rescue_an_old_manifest(self, tmp_path: Path) -> None:
        """The point of the whole probe: read the field, never the filesystem.

        The dir and its manifest are written right now, so every mtime on this
        tree is current. Only ``captured_at_utc`` says otherwise. An implementation
        that fell back to mtime would call this FRESH — which is exactly how a
        restored or rsynced backup tree reports a backup it does not have.
        """
        d = write_snapshot(tmp_path, "2026-08-21", NOW - timedelta(days=30))
        assert (d / "MANIFEST.json").stat().st_mtime > 0  # written just now
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "STALE"
        assert report.age_days is not None and report.age_days > 29

    def test_the_scripts_real_manifest_format_reads_back(self, tmp_path: Path) -> None:
        """Writer/reader agreement, against a manifest copied from a live snapshot.

        Not a tautology: the reader is pinned to the exact shape
        ``deploy/backup-analytics.sh`` emits — a ``Z``-suffixed, timezone-naive
        ISO string, which ``datetime.fromisoformat`` rejects unless the ``Z`` is
        translated first. A reader that dropped that translation would return
        None here and grade a healthy tree as NO SNAPSHOTS.
        """
        manifest = tmp_path / "MANIFEST.json"
        manifest.write_text(
            json.dumps(
                {
                    "captured_at_utc": "2026-08-21T13:11:05Z",
                    "method": "copy-from-database",
                    "git_commit": "d422d3a",
                }
            ),
            encoding="utf-8",
        )
        got = read_captured_at(manifest)
        assert got == datetime(2026, 8, 21, 13, 11, 5, tzinfo=UTC)
        assert got.tzinfo is not None  # aware, so the subtraction in evaluate() works

    def test_an_absent_manifest_returns_none_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        assert read_captured_at(tmp_path / "gone.json") is None


class TestUnreadableNeverReadsAsFresh:
    def test_missing_manifest_is_not_a_snapshot(self, tmp_path: Path) -> None:
        (tmp_path / SNAPSHOT_TIER / "2026-08-21").mkdir(parents=True)
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "NO SNAPSHOTS"
        assert len(report.unreadable) == 1

    def test_corrupt_json_is_not_a_snapshot(self, tmp_path: Path) -> None:
        d = tmp_path / SNAPSHOT_TIER / "2026-08-21"
        d.mkdir(parents=True)
        (d / "MANIFEST.json").write_text("{not json", encoding="utf-8")
        assert evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS).verdict == "NO SNAPSHOTS"

    def test_missing_field_is_not_a_snapshot(self, tmp_path: Path) -> None:
        write_snapshot(tmp_path, "2026-08-21", None)
        assert evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS).verdict == "NO SNAPSHOTS"

    def test_unparseable_timestamp_is_not_a_snapshot(self, tmp_path: Path) -> None:
        write_snapshot(tmp_path, "2026-08-21", "last Tuesday")
        assert evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS).verdict == "NO SNAPSHOTS"

    def test_a_corrupt_newest_falls_back_to_the_readable_older_one(
        self, tmp_path: Path
    ) -> None:
        """A corrupt manifest must not mask an older good snapshot, or hide itself."""
        write_snapshot(tmp_path, "2026-08-10", NOW - timedelta(days=11))
        write_snapshot(tmp_path, "2026-08-21", "garbage")
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "STALE"  # graded on the 11d-old readable one
        assert len(report.unreadable) == 1


class TestAbsentRootIsItsOwnVerdict:
    def test_missing_root_is_not_collapsed_into_stale(self, tmp_path: Path) -> None:
        report = evaluate(tmp_path / "nope", NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "NO BACKUP ROOT"
        assert not report.ok
        assert report.age_days is None

    def test_empty_root_is_no_snapshots_not_no_root(self, tmp_path: Path) -> None:
        """The two states want different actions, so they must stay distinguishable."""
        assert evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS).verdict == "NO SNAPSHOTS"


class TestTiersAreDifferentArtifacts:
    def test_weekly_parquet_is_not_an_unreadable_snapshot(self, tmp_path: Path) -> None:
        """Regression: the first draft flagged every weekly dir as malformed.

        ``weekly/`` carries a parquet export and no manifest by design. Counting it
        as a broken snapshot puts a permanent warning in the banner.
        """
        write_snapshot(tmp_path, "2026-08-21", NOW - timedelta(hours=2))
        write_archive(tmp_path, "2026-08-19")
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "FRESH"
        assert report.unreadable == ()
        assert "⚠" not in render(report, tmp_path, DEFAULT_MAX_AGE_DAYS)

    def test_archive_age_is_reported_from_its_stamped_name(
        self, tmp_path: Path
    ) -> None:
        write_snapshot(tmp_path, "2026-08-21", NOW - timedelta(hours=2))
        write_archive(tmp_path, "2026-08-19")
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.archive_age_days is not None
        assert 2.0 < report.archive_age_days < 3.0

    def test_a_stale_archive_does_not_turn_the_verdict_red(
        self, tmp_path: Path
    ) -> None:
        """Informational means informational — the parquet tier is belt-and-braces."""
        write_snapshot(tmp_path, "2026-08-21", NOW - timedelta(hours=2))
        write_archive(tmp_path, "2026-06-01")
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "FRESH"
        assert report.ok
        assert report.archive_stale  # flagged in the banner, absent from the verdict
        assert "⚠" in render(report, tmp_path, DEFAULT_MAX_AGE_DAYS)

    def test_a_snapshot_tier_dir_with_no_manifest_is_still_flagged(
        self, tmp_path: Path
    ) -> None:
        """Control for the test above: the exemption is scoped to `weekly/` only."""
        write_snapshot(tmp_path, "2026-08-21", NOW - timedelta(hours=2))
        (tmp_path / SNAPSHOT_TIER / "2026-08-20").mkdir(parents=True)
        report = evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS)
        assert report.verdict == "FRESH"
        assert len(report.unreadable) == 1


class TestBannerSaysWhatItMeasured:
    def test_a_bad_verdict_names_the_next_action(self, tmp_path: Path) -> None:
        write_snapshot(tmp_path, "2026-08-01", NOW - timedelta(days=20))
        out = render(
            evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS),
            tmp_path,
            DEFAULT_MAX_AGE_DAYS,
        )
        assert "make backup" in out
        assert "list-timers" in out

    def test_the_banner_states_its_own_scope(self, tmp_path: Path) -> None:
        """The known hole is named in the output, not only in the docstring."""
        write_snapshot(tmp_path, "2026-08-21", NOW - timedelta(hours=1))
        out = render(
            evaluate(tmp_path, NOW, DEFAULT_MAX_AGE_DAYS),
            tmp_path,
            DEFAULT_MAX_AGE_DAYS,
        )
        assert "LOCAL tree only" in out
        assert "ADVISORY" in out
