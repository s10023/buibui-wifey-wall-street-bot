"""Seed `schema_migrations` for 001–009 on the DB they were verified against (#467)

`schema_migrations` arrived after every existing migration had already run (or
been found moot), so their apply paths never wrote a row. This records them once,
from the 2026-10-09 read-only probe in #467 plus the two applies that day, so that
`make freshness-check` starts from an honest baseline rather than listing all
nine as unrecorded forever.

Each row's note says how that migration was established, and `applied_at_utc` is
left NULL because no application time was recorded. Run it only against the DB
the probe read (this host's `analytics.db`): on any other DB these notes would be
claims nobody checked, and the right move there is to re-run the probe.

Not numbered, so the coverage leg (which reads `migrations/0*.py`) does not
count it as a migration. Idempotent: an id that already has a row is skipped.

Usage:
    python migrations/seed_applied_2026_10_09.py [--db PATH] [--apply]

Dry-run by default. Insert-only into a table nothing else reads, so no `.bak`
guard; take `make backup` first anyway if the DB has moved since the last one.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.schema_migrations import (  # noqa: E402
    record_applied,
    recorded_ids,
)

_PROBE = "verified by read-only probe 2026-10-09 (#467)"

#: (migration id, rows_affected, note). rows_affected is known only for the two
#: applies run on 2026-10-09 with their output captured.
SEED: tuple[tuple[str, int | None, str], ...] = (
    (
        "001_day_filter_text",
        None,
        f"moot: day_filter is VARCHAR, no pre-fork row survives; {_PROBE}",
    ),
    (
        "002_adr_threshold_executed",
        None,
        f"0 pending under write-time config; {_PROBE}. Its 13 predicate hits are "
        "fa89d57 config drift: never re-run --apply",
    ),
    ("003_outcome_r_implied_tp", None, f"0 of 624 rows diverge; {_PROBE}"),
    ("004_live_ledger_net_of_cost", None, f"0 resolved rows unpriced; {_PROBE}"),
    ("005_symmetric_gap_fill", None, f"489/489 replica OK, 0 would change; {_PROBE}"),
    ("006_purge_frozen_tail_bars", None, f"0 rows pending; {_PROBE}"),
    (
        "007_purge_avb_wrong_instrument_tail",
        34,
        "applied by hand 2026-10-09 (#445): 27 1d + 7 1wk AVB rows",
    ),
    (
        "008_realign_off_monday_weekly_bars",
        5,
        "applied 2026-10-09: ABBV/AEE/LUV/PEG/UNP realigned; SATS kept by design "
        "(delisted, provider serves nothing)",
    ),
    (
        "009_purge_bny_4h_wrong_instrument",
        816,
        "applied by hand 2026-10-09 (#468): BNY 4h, 178 rows kept",
    ),
)


def seed(db_path: str, apply: bool) -> list[str]:
    """Return the ids that were (or, on a dry run, would be) seeded."""
    conn = duckdb.connect(db_path, read_only=not apply)
    try:
        done = recorded_ids(conn)
        todo = [row for row in SEED if row[0] not in done]
        for mid, _rows, _note in SEED:
            state = "already recorded" if mid in done else "to seed"
            print(f"  {mid:<40} {state}")
        if not apply:
            print(
                f"\nDRY RUN — {len(todo)} row(s) would be written. Re-run with --apply."
            )
            return [row[0] for row in todo]
        conn.execute("BEGIN TRANSACTION")
        try:
            for mid, rows, note in todo:
                # record_applied keys on the file stem, so pass the id as a name.
                record_applied(conn, f"{mid}.py", rows, note, applied=False)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        print(f"\nSeeded: {len(todo)} row(s).")
        return [row[0] for row in todo]
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    seed(args.db, args.apply)


if __name__ == "__main__":
    main()
