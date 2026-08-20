"""Report which recurring tasks are overdue, from the marks they leave behind.

Ported from the parent's `daily_check.py` task-mark block (`docs/plans/task-marks/`),
with three deliberate divergences — each because the parent's reason does not hold
here, not because the rule changed.

**1. This file is TRACKED; the parent's is not.** Upstream `daily_check.py` lives
under gitignored `docs/plans/`, so it dies on a reclone — its own docstring admits
this. wifey learned that lesson on 2026-08-20 when the `.claude/` allowlist was
inverted to a denylist precisely because hooks were silently not surviving a
reclone. **An enforcement layer that a reclone loses is not an enforcement layer.**
The MARKS stay gitignored (they are per-machine state, and `docs/plans/` is already
covered wholesale by `make backup`); only the checker is committed.

**2. The mark's CONTENT is authoritative, not its mtime.** The parent writes an
ISO-8601 line and then reads `st_mtime`, so the content it carefully writes has no
consumer — the same self-referential shape that cost the handoff its `Line count:`
stamp. Worse, mtime moves for reasons that are not runs: opening the file in an
editor, or a restore that does not preserve times. Here the timestamp inside the
file is the measurement, and an unparseable one reads as OVERDUE rather than
falling back to mtime, because the fallback fails in the direction that reports
*fresher than reality*.

**3. Stamping is a flag, not a shell incantation.** The parent tells each skill to
run ``date -u +%FT%TZ > docs/plans/task-marks/<task>``; a redirect typo silently
writes the wrong file and a missing directory fails the write. ``--stamp <task>``
creates the directory, validates the name against the declared table, and refuses
an unknown one. Fix the default, not the human.

⚠ **A MISSING mark reads as OVERDUE on purpose.** That is the fail-safe direction:
a lost or never-written mark must shout, where the opposite mistake reports "fresh"
for a task that has never run once.

⚠ **This is ADVISORY and must never gate CI.** It exits 0 even when everything is
overdue (``--exit-nonzero`` opts in, for a human who wants a shell condition). Two
reasons, and the second is structural: overdue housekeeping is not evidence loss,
and a check that stays red for days until a human runs a skill trains dismissal —
but more decisively, **the marks are gitignored, so a fresh CI clone sees every one
of them absent and would therefore report every task permanently overdue.** A check
that can only ever be red in CI is worse than no check.

Inclusion rule for a task — keep it, or this table rots into noise that gets
skipped. All four must hold:

  1. it rots SILENTLY (nothing in the normal workflow tells you), and
  2. staleness has a NAMED consequence, and
  3. the check is one cheap field, and
  4. exactly one action clears it.

Usage::

    make cadence-check                 # report
    poetry run python tools/cadence_check.py --stamp sanity-check
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MARKS = Path("docs/plans/task-marks")


@dataclass(frozen=True)
class Task:
    """One recurring task, its period, and what going stale actually costs."""

    label: str
    slug: str
    every_days: float
    consequence: str


#: Deliberately SHORT, and every entry satisfies all four inclusion rules above.
#:
#: Not listed, with the rule each fails — recorded so the next session does not
#: re-propose them:
#:   * `make backup`      — a scheduled `wifey-backup.timer` covers it (installed
#:                          2026-08-19), so no human action clears it: fails (4).
#:                          Its real risk is the timer stopping silently, which is
#:                          an observed-state probe rather than a mark.
#:   * `make go-live`     — daily, and a genuine candidate, but nothing stamps it
#:                          today and inventing a cadence the operator has not set
#:                          would make the first run red for a policy reason.
#:   * `/db-update`       — event-driven (after a config change), not on a clock:
#:                          fails (1), since the change itself is the trigger.
#:   * `/ingest-feed`     — a static per-channel floor plus an explicit ledger mean
#:                          skipping a day loses nothing: fails (2).
TASKS: tuple[Task, ...] = (
    Task(
        "/sanity-check",
        "sanity-check",
        7.0,
        "wiring and doc drift ships; the mechanical half only speaks when a PR runs",
    ),
    Task(
        "/sync-parent",
        "sync-parent",
        7.0,
        "the parent keeps merging and nothing here notices — 52 PRs had accumulated "
        "by 2026-08-20 while the handoff called the range empty",
    ),
)


def parse_mark(text: str) -> datetime | None:
    """The first non-comment, non-blank line, as an aware UTC datetime.

    Returns ``None`` rather than raising: every unreadable state funnels to the
    same OVERDUE verdict, so a corrupt mark cannot read as fresh.
    """
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            stamped = datetime.fromisoformat(line.replace("Z", "+00:00"))
        except ValueError:
            return None
        return stamped if stamped.tzinfo else stamped.replace(tzinfo=UTC)
    return None


@dataclass(frozen=True)
class Status:
    task: Task
    age_days: float | None  #: None when never recorded or unreadable
    overdue: bool
    detail: str


def evaluate(task: Task, root: Path, now: datetime) -> Status:
    mark = root / task.slug
    if not mark.exists():
        return Status(
            task, None, True, f"never recorded (due every {task.every_days:.0f}d)"
        )
    stamped = parse_mark(mark.read_text(encoding="utf-8"))
    if stamped is None:
        return Status(
            task, None, True, "mark is unreadable — treated as overdue, never as fresh"
        )
    age = (now - stamped).total_seconds() / 86400.0
    if age > task.every_days:
        return Status(
            task, age, True, f"{age:.1f}d ago ({stamped:%Y-%m-%d %H:%MZ}) — OVERDUE"
        )
    return Status(task, age, False, f"{age:.1f}d ago ({stamped:%Y-%m-%d %H:%MZ})")


def stamp(slug: str, root: Path, now: datetime) -> Path:
    """Record a run. Refuses a slug that is not a declared task."""
    known = {t.slug for t in TASKS}
    if slug not in known:
        raise SystemExit(f"unknown task {slug!r}; declared: {', '.join(sorted(known))}")
    root.mkdir(parents=True, exist_ok=True)
    mark = root / slug
    mark.write_text(f"{now:%Y-%m-%dT%H:%M:%SZ}\n", encoding="utf-8")
    return mark


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stamp", metavar="TASK", help="record that TASK just ran")
    ap.add_argument("--root", default=str(MARKS), help=argparse.SUPPRESS)
    ap.add_argument(
        "--exit-nonzero",
        action="store_true",
        help="exit 1 if anything is overdue (opt-in; never use this in CI)",
    )
    args = ap.parse_args()
    root, now = Path(args.root), datetime.now(UTC)

    if args.stamp:
        print(f"stamped {stamp(args.stamp, root, now)}")
        return

    print("cadence_check — recurring tasks, from the marks they leave\n")
    statuses = [evaluate(t, root, now) for t in TASKS]
    for st in statuses:
        print(f"  {'!' if st.overdue else '+'} {st.task.label:<16} {st.detail}")
        if st.overdue:
            print(f"       → run it, then: make cadence-stamp TASK={st.task.slug}")
            print(f"       → why it matters: {st.task.consequence}")

    n = sum(s.overdue for s in statuses)
    print(
        f"\n  {n} overdue of {len(statuses)}. Advisory only — a missing mark reads as\n"
        "  overdue on purpose, and this never gates CI (the marks are gitignored, so a\n"
        "  fresh clone would report every task overdue forever).\n"
    )
    if args.exit_nonzero and n:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
