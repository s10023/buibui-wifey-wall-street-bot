"""Applied-state record for the one-shot scripts in `migrations/` (#467).

The scripts keep no state of their own: each recomputes its predicate from the
table ("idempotent by value"), so whether one reached a given `analytics.db` could
only be re-derived by hand. That let migration 007 sit unapplied on this host's DB
through a host migration and three backups until #445 found it. Re-running the
predicates is not a sound substitute either: 002's reads today's config and flags
13 truthful rows, 001's population no longer exists, and 008 keeps a by-design
residual (SATS).

So the record is written, not derived. Each `--apply` path calls
`record_applied` (inside its own write transaction where it has one), and
`make freshness-check` lists every `migrations/0*.py` without a row. The table
lives inside `analytics.db`, so it travels with every backup and restore:
restoring an older snapshot correctly shows the older applied set, which a
tracked file could not.

A row means "this script's apply path ran here", including a zero-row apply. It
never means the script's predicate is empty today.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb

TABLE = "schema_migrations"

# Keyed on (migration_id, recorded_at_utc) so a re-run appends rather than
# overwriting the first application. `applied_at_utc` is NULL on a seeded row,
# whose application time was never recorded; the note says how it was verified.
DDL = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id     TEXT      NOT NULL,
        recorded_at_utc  TIMESTAMP NOT NULL,
        applied_at_utc   TIMESTAMP,
        rows_affected    BIGINT,
        git_sha          TEXT,
        note             TEXT,
        PRIMARY KEY (migration_id, recorded_at_utc)
    )
"""


def ensure_schema_migrations(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the record table if absent. `init_schema` calls this too."""
    conn.execute(DDL)


def migration_id(script: str | Path) -> str:
    """The id a script records under: its file stem, e.g. `007_purge_avb_...`."""
    return Path(script).stem


def _utc_now() -> datetime:
    # Naive UTC: a tz-aware value bound to a TIMESTAMP column is converted
    # through the session time zone, which differs per host.
    return datetime.now(UTC).replace(tzinfo=None)


def git_sha() -> str | None:
    """Short HEAD of the checkout running the script; None outside git."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=Path(__file__).resolve().parent,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else None


def record_applied(
    conn: duckdb.DuckDBPyConnection,
    script: str | Path,
    rows_affected: int | None,
    note: str = "",
    *,
    applied: bool = True,
) -> None:
    """Record that ``script``'s apply path ran on this DB.

    Call it on every apply, including one that changed zero rows. ``applied``
    is False only for a seeded row, whose application time is unknown.
    """
    ensure_schema_migrations(conn)
    now = _utc_now()
    conn.execute(
        "INSERT INTO schema_migrations (migration_id, recorded_at_utc, applied_at_utc,"
        " rows_affected, git_sha, note) VALUES (?, ?, ?, ?, ?, ?)",
        [
            migration_id(script),
            now,
            now if applied else None,
            rows_affected,
            git_sha(),
            note or None,
        ],
    )


def table_present(conn: duckdb.DuckDBPyConnection) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [TABLE],
    ).fetchone()
    return bool(row and row[0])


def recorded_ids(conn: duckdb.DuckDBPyConnection) -> frozenset[str]:
    """Every migration id with at least one row; empty when the table is absent."""
    if not table_present(conn):
        return frozenset()
    return frozenset(
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT migration_id FROM schema_migrations"
        ).fetchall()
    )


def is_recorded(conn: duckdb.DuckDBPyConnection, script: str | Path) -> bool:
    return migration_id(script) in recorded_ids(conn)


def script_ids(migrations_dir: Path) -> list[str]:
    """Ids of the numbered scripts in ``migrations_dir``, in order."""
    return sorted(p.stem for p in migrations_dir.glob("0*.py"))
