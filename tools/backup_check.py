"""Report how old the newest verified backup snapshot is.

This is the **observed-state probe** that `tools/cadence_check.py`'s exclusion note
points at. `make backup` fails that tool's inclusion rule (4) — a scheduled
`wifey-backup.timer` clears it, so no human action does — but its real risk was
never "a human forgot". It is **the timer stopping silently**, and a mark cannot
see that.

⚠ **A green timer is not a current backup**, and the reason changed once already.
Until 2026-08-19 only the off-site leg was scheduled, so it mirrored a
`~/backups/wifey` that only a manual `make backup` filled — it logged
``off-site backup OK (4 verified snapshot(s))`` while faithfully copying a tree
frozen two days earlier, and **the log line is byte-identical either way**. Both
legs are scheduled now, so that specific hole is closed; the residual one is
narrower and is what this file measures. Alerting is failure-only (`OnFailure=`),
which makes the channel unfalsifiable: a timer with nothing to report and a timer
that stopped firing look the same from the Telegram side.

The transferable rule, from `deploy/README.md`: **a scheduled job can only attest
to the step it performs.** For a green light to mean "the data is current",
something has to check the *input's* age rather than the copy's exit code. That is
this probe's whole job — it reads the tree the off-site leg copies FROM.

⚠ **The two tiers are DIFFERENT ARTIFACTS and are graded separately.** `daily/`
holds verified snapshots, each carrying ``MANIFEST.json``; `weekly/` holds a
format-independent **parquet export** and carries no manifest at all. Grading them
together — the first draft did — reports every weekly dir as a malformed snapshot,
so the banner ships a permanent warning about a directory that is exactly as the
backup script intended. **A check that is never clean stops being read**, which is
why the daily tier alone decides the verdict and the archive is reported beside it.

Four properties are load-bearing:

**1. The manifest's CONTENT is authoritative, not the directory's mtime.** Every
snapshot carries ``captured_at_utc``, so the timestamp is read from inside the
file. This is `cadence_check`'s divergence (2) for the same reason: mtime moves for
things that are not runs — a restore that does not preserve times, an editor, an
`rclone` round-trip — and it fails in the direction that reports *fresher than
reality*. The weekly tier has no manifest to read, so it falls back to the date the
script stamped into the **directory name**, which is still a recorded decision
rather than a filesystem side effect.

**2. Every unreadable state reads as STALE, never as fresh.** A missing manifest, an
unparseable timestamp and an empty tree all funnel to the same verdict. This
matches the off-site script's own stance that a missing ``MANIFEST.json`` is a
fault rather than "nothing to do", and it is the only safe direction: the opposite
mistake reports a healthy backup for a machine that has none.

**3. An ABSENT root is a distinct verdict from a stale one** (``NO BACKUP ROOT``).
Collapsing them prints the milder of the two, and they want different actions —
one is "the timer broke", the other is "this machine never backed up at all".

**4. It is ADVISORY and must never gate CI**, for the same structural reason
`cadence_check` is: ``$WIFEY_BACKUP_ROOT`` is machine-local single-copy state that
no clone has, so a CI run would report a missing backup forever. A check that can
only be red in CI is worse than no check. ``--exit-nonzero`` opts in, for a human
who wants a shell condition.

⚠ **Known hole, named rather than papered over: this measures the LOCAL tree only.**
It cannot see whether the off-site mirror actually received it — that needs a
network `rclone` call, and a probe that fails when the laptop is offline would
report a backup problem for a connectivity one. The off-site leg's own success plus
a fresh source here is the two-part answer; neither half is sufficient alone.

⚠ **It also does not REFUSE anything.** Wiring a staleness refusal into
`deploy/backup-offsite.sh` is the second candidate fix in `deploy/README.md` and
remains a deliberate user call, because a guard that costs you the backup is worse
than the gap it closes.

Usage::

    make backup-check
    poetry run python tools/backup_check.py --exit-nonzero   # shell condition
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Where `deploy/backup-analytics.sh` publishes, and what the off-site leg copies.
DEFAULT_ROOT = Path.home() / "backups" / "wifey"

#: Verified snapshots, each with a MANIFEST.json. This tier decides the verdict.
SNAPSHOT_TIER = "daily"

#: Parquet export (`--weekly-if-due`) — a format-independent archive, no manifest.
ARCHIVE_TIER = "weekly"

#: Days before the newest snapshot is STALE. The local leg fires twice daily
#: (08:10 / 13:10 UTC) and the units carry `Persistent=true`, so a single missed
#: fire runs on resume and is not evidence of a fault. Two full days without a
#: snapshot means at least one whole cycle was missed with the machine awake.
DEFAULT_MAX_AGE_DAYS = 2.0

#: The backup script's own `--weekly-if-due` bar, mirrored so the two agree.
ARCHIVE_MAX_AGE_DAYS = 7.0

MANIFEST = "MANIFEST.json"

_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class Snapshot:
    """One published snapshot and the capture time read from its manifest."""

    path: Path
    captured_at: datetime


@dataclass(frozen=True)
class Report:
    """The probe's verdict. ``age_days`` is None whenever nothing was measurable."""

    verdict: str  #: FRESH / STALE / NO SNAPSHOTS / NO BACKUP ROOT
    detail: str
    age_days: float | None
    newest: Snapshot | None
    unreadable: tuple[Path, ...]
    archive_age_days: float | None = None
    archive_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.verdict == "FRESH"

    @property
    def archive_stale(self) -> bool:
        """Informational only — never promoted into ``verdict``.

        The parquet export is belt-and-braces beside the daily `.db` snapshot, so
        a stale archive beside a fresh snapshot is a weaker state than it looks
        and must not turn the banner red on its own.
        """
        return self.archive_age_days is None or self.archive_age_days > (
            ARCHIVE_MAX_AGE_DAYS
        )


def read_captured_at(manifest: Path) -> datetime | None:
    """``captured_at_utc`` from a snapshot manifest, or None if unusable.

    Returns None rather than raising: a corrupt manifest, a missing field and a
    malformed timestamp are all the same verdict, and none of them may read as
    fresh. Deliberately does NOT fall back to the file's mtime — see property (1).
    """
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stamped = raw.get("captured_at_utc") if isinstance(raw, dict) else None
    if not isinstance(stamped, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamped.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def collect_snapshots(root: Path) -> tuple[list[Snapshot], list[Path]]:
    """Readable snapshots under ``root/daily``, plus the dirs that failed to parse.

    The unreadable list is returned rather than discarded so the report can say
    "there are snapshot dirs but I could not read them", which is a different
    operational state from an empty tree. It covers the snapshot tier only — a
    weekly parquet dir is not a malformed snapshot, it is a different artifact.
    """
    found: list[Snapshot] = []
    unreadable: list[Path] = []
    tier_dir = root / SNAPSHOT_TIER
    if not tier_dir.is_dir():
        return found, unreadable
    for entry in sorted(tier_dir.iterdir()):
        if not entry.is_dir():
            continue
        captured = read_captured_at(entry / MANIFEST)
        if captured is None:
            unreadable.append(entry)
            continue
        found.append(Snapshot(entry, captured))
    return found, unreadable


def newest_archive(root: Path, now: datetime) -> tuple[float, Path] | None:
    """Age in days of the newest weekly parquet export, from its stamped name.

    No manifest exists in this tier, so the directory name — the stamp the backup
    script chose — is the record. A name that is not a date is skipped rather than
    dated from mtime, which would report fresher than reality.
    """
    tier_dir = root / ARCHIVE_TIER
    if not tier_dir.is_dir():
        return None
    dated: list[tuple[datetime, Path]] = []
    for entry in sorted(tier_dir.iterdir()):
        if not entry.is_dir() or not _STAMP_RE.match(entry.name):
            continue
        try:
            stamped = datetime.strptime(entry.name, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue
        dated.append((stamped, entry))
    if not dated:
        return None
    stamped, path = max(dated, key=lambda pair: pair[0])
    return (now - stamped).total_seconds() / 86400.0, path


def evaluate(root: Path, now: datetime, max_age_days: float) -> Report:
    """Grade the tree. Every unmeasurable state lands on a non-FRESH verdict."""
    if not root.is_dir():
        return Report(
            "NO BACKUP ROOT",
            f"{root} does not exist — this machine has no local backup at all",
            None,
            None,
            (),
        )

    archive = newest_archive(root, now)
    archive_age = archive[0] if archive else None
    archive_path = archive[1] if archive else None

    found, unreadable = collect_snapshots(root)
    if not found:
        detail = (
            f"{len(unreadable)} dir(s) under {SNAPSHOT_TIER}/ but none carried a "
            f"readable {MANIFEST}"
            if unreadable
            else f"no snapshots under {root / SNAPSHOT_TIER}"
        )
        return Report(
            "NO SNAPSHOTS",
            detail,
            None,
            None,
            tuple(unreadable),
            archive_age,
            archive_path,
        )

    newest = max(found, key=lambda s: s.captured_at)
    age = (now - newest.captured_at).total_seconds() / 86400.0
    stamp = f"{newest.captured_at:%Y-%m-%d %H:%MZ}"
    verdict, detail = (
        (
            "STALE",
            f"newest snapshot is {age:.1f}d old ({stamp}), over the "
            f"{max_age_days:.0f}d bar — check the timers actually fired",
        )
        if age > max_age_days
        else ("FRESH", f"newest snapshot is {age:.1f}d old ({stamp})")
    )
    return Report(
        verdict, detail, age, newest, tuple(unreadable), archive_age, archive_path
    )


def render(report: Report, root: Path, max_age_days: float) -> str:
    """The banner. Reads the age of the INPUT, never a unit's `enabled` state."""
    mark = "+" if report.ok else "!"
    lines = [
        "backup_check — the age of the newest verified snapshot\n",
        f"  {mark} {report.verdict:<15} {report.detail}",
        f"      root: {root}",
    ]
    if report.newest is not None:
        lines.append(f"      newest: {report.newest.path}")
    if report.archive_age_days is not None:
        flag = "⚠ " if report.archive_stale else ""
        lines.append(
            f"      {flag}weekly parquet archive: {report.archive_age_days:.1f}d old "
            f"(bar {ARCHIVE_MAX_AGE_DAYS:.0f}d, informational)"
        )
    elif report.verdict != "NO BACKUP ROOT":
        lines.append("      ⚠ no weekly parquet archive (informational)")
    if report.unreadable:
        lines.append(
            f"      ⚠ {len(report.unreadable)} unreadable dir(s) under "
            f"{SNAPSHOT_TIER}/, e.g. {report.unreadable[0]}"
        )
    if not report.ok:
        lines += [
            "",
            "      → run `make backup`, then check the timers:",
            "        systemctl --user list-timers 'wifey-*'",
            "      → why it matters: alerting is failure-only, so a timer that",
            "        silently stopped looks identical to one with nothing to say.",
        ]
    lines += [
        "",
        f"  Bar is {max_age_days:.0f}d on {SNAPSHOT_TIER}/. ADVISORY — never gates CI,",
        "  because the backup root is machine-local state no clone has. Measures the",
        "  LOCAL tree only: a fresh source here plus the off-site leg's own success is",
        "  the full answer, and neither half is sufficient alone.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Report the newest snapshot's age.")
    ap.add_argument(
        "--root",
        default=os.environ.get("WIFEY_BACKUP_ROOT") or str(DEFAULT_ROOT),
        help="snapshot tree to grade (default: $WIFEY_BACKUP_ROOT or ~/backups/wifey)",
    )
    ap.add_argument(
        "--max-age-days",
        type=float,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"staleness bar in days (default {DEFAULT_MAX_AGE_DAYS:g})",
    )
    ap.add_argument(
        "--exit-nonzero",
        action="store_true",
        help="exit 1 unless FRESH (opt-in; never use this in CI)",
    )
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    report = evaluate(root, datetime.now(UTC), args.max_age_days)
    print(render(report, root, args.max_age_days))
    if args.exit_nonzero and not report.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
