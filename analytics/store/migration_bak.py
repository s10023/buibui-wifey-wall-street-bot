"""The `.bak` precondition every hand-run script in `migrations/` enforces.

A migration's undo is `<db>.bak`, so the `.bak` has to be the database the
migration is about to change, byte for byte. Testing existence alone let a stale
copy through: on 2026-09-03 the tree's `.bak` was 14 days old and 94 MB smaller,
and on 2026-10-09 a pre-009 copy matched `analytics.db`'s size exactly while
differing in 10 of its 264 one-MiB chunks. Size and mtime cannot carry freshness: DuckDB
never shrinks its file on delete, and `Copy-Item` preserves the mtime.

So the guard compares bytes (about 0.4 s for 276 MB). Measured on duckdb 1.5.6,
that is not too strict: a read-only or a no-write read-write open leaves the
file byte-identical, and a clean close checkpoints and removes `<db>.wal`. A
`.wal` that is present means the file alone is not the whole database, and on
Windows a writer holding the DB makes it unreadable; both refuse rather than
compare.

A successful `--apply` leaves the `.bak` stale on purpose (it is the undo), so a
second `--apply` needs a fresh copy too.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_CHUNK = 1 << 20


def _first_difference(a: Path, b: Path) -> int | None:
    """Byte offset of the first difference between two equal-sized files."""
    offset = 0
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ca = fa.read(_CHUNK)
            cb = fb.read(_CHUNK)
            if ca != cb:
                return offset + next(
                    (i for i, (x, y) in enumerate(zip(ca, cb, strict=False)) if x != y),
                    min(len(ca), len(cb)),
                )
            if not ca:
                return None
            offset += len(ca)


def bak_problem(db_path: str | Path) -> str | None:
    """Why `<db_path>.bak` is not a fresh copy of `db_path`, or None if it is."""
    db = Path(db_path)
    bak = Path(f"{db}.bak")
    if not bak.exists():
        return f"{bak} not found. Back the DB up first."
    if not db.exists():
        return f"{db} not found."
    wal = Path(f"{db}.wal")
    if wal.exists():
        return (
            f"{wal} exists: a process holds the DB open or exited without a "
            "checkpoint, so the file alone is not the whole database. Close every "
            "connection (a clean close checkpoints), then re-copy."
        )
    db_size, bak_size = db.stat().st_size, bak.stat().st_size
    if db_size != bak_size:
        return (
            f"{bak} is not a copy of the current DB "
            f"({bak_size:,} bytes vs {db_size:,})."
        )
    try:
        at = _first_difference(db, bak)
    except OSError as exc:
        return (
            f"cannot read {db} or {bak} to compare them ({exc}); another process "
            "may hold the DB open."
        )
    if at is not None:
        return (
            f"{bak} is not a copy of the current DB (first difference at byte {at:,})."
        )
    return None


def require_fresh_bak(db_path: str | Path) -> None:
    """Exit 1 with the reason unless `<db_path>.bak` is a byte copy of the DB."""
    problem = bak_problem(db_path)
    if problem is None:
        return
    db = os.fspath(db_path)
    print(f"Refusing to run: {problem}")
    print(f"Cut a fresh copy first:  Copy-Item {db} {db}.bak -Force")
    print(f"                  (bash: cp {db} {db}.bak)")
    sys.exit(1)
