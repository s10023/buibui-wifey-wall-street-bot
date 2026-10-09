"""Migration 009 — ohlcv: delete BNY's wrong-instrument `4h` bars (#468)

`BNY` (Bank of New York Mellon, ex-`BK`) trades around $140. Its `4h` series
held 816 bars from 2024-06-17 to 2026-02-06 at $9.33–11.08 on a median volume
of 22,258, then a 104.8-day gap, then real BNY from 2026-05-22 at $139+ on a
median of about 1.0M. The cheap bars are a different security: the provider's
intraday history for the rebranded ticker served what is, by price and volume,
the ticker's previous holder (a ~$10 closed-end fund; inferred, not confirmed).
Left in place they put a fabricated 13.8x bar into every pooled `4h`
cross-section over the universe. Measured 2026-10-09 by #469's level-break
scan, which flags exactly this series on the live DB.

`1d` and `1wk` are untouched and clean: their sub-$50 rows are BK's real
2018–2023 range, not this instrument.

Re-backfill cannot repair it, as with AVB (migration 007): on 2026-10-09
``yf.Ticker("BNY").history(interval="1h")`` still returned ~$10 bars for
Jan–Feb 2026 while ``fast_info`` quoted 143.61. A routine `4h` sync will not
re-import them, because sync extends forward from the newest stored bar; a
hand-run full `4h` backfill would, and #469's ingest warning names it when it
does.

Two predicates, and they must agree, as in 007: value (`close < 50`) and date
(`open_time < 2026-04-01`). The margin is wide both ways: the good bars' minimum
low is 137.50 against the bad bars' maximum high of 11.10, a 12.4x gap. Not a
delisting, so `config/universe.json` is untouched.

Idempotent by value: a second run finds nothing and reports zero.

Usage:
    python migrations/009_purge_bny_4h_wrong_instrument.py [--db PATH] [--apply]

Dry-run by default. A .bak copy must exist alongside the DB before --apply will
proceed. The guard tests existence, not freshness: cut a fresh copy first.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.schema_migrations import record_applied  # noqa: E402

#: 2026-04-01 UTC, inside the empty gap between the last bad bar
#: (1770399000000, 2026-02-06) and the first good one (1779456600000,
#: 2026-05-22). Paired with the value predicate rather than trusted alone.
_SEAM_MS: int = 1_775_001_600_000

#: Close below which a BNY `4h` bar cannot be BNY. Good bars bottom at a low of
#: 137.50; the bad band tops out at a high of 11.10.
_WRONG_INSTRUMENT_CLOSE: float = 50.0

_WRONG_ROWS_SQL = """
SELECT symbol, timeframe, open_time, close
FROM ohlcv
WHERE symbol = 'BNY' AND timeframe = '4h' AND close < ? AND open_time < ?
ORDER BY open_time
"""

#: Rows matching ONE predicate but not the other. Must be empty — if it is not,
#: the two populations have started to overlap and the rule needs re-deriving.
_DISAGREEMENT_SQL = """
SELECT count(*) FROM ohlcv
WHERE symbol = 'BNY' AND timeframe = '4h' AND ((close < ?) <> (open_time < ?))
"""


def migrate(db_path: str, apply: bool) -> int:
    """Return the number of wrong-instrument rows found (deleted under apply)."""
    if apply and not os.path.exists(db_path + ".bak"):
        print(f"Refusing to run: {db_path}.bak not found. Back the DB up first.")
        sys.exit(1)

    conn = duckdb.connect(db_path, read_only=not apply)
    try:
        params = [_WRONG_INSTRUMENT_CLOSE, _SEAM_MS]
        disagree = conn.execute(_DISAGREEMENT_SQL, params).fetchone()
        n_disagree = int(disagree[0]) if disagree else 0
        if n_disagree:
            print(
                f"Refusing to run: {n_disagree} BNY 4h row(s) match the value "
                "predicate but not the date one (or vice versa). The two "
                "populations were disjoint when this was written; re-derive the rule."
            )
            sys.exit(1)

        rows = conn.execute(_WRONG_ROWS_SQL, params).fetchall()
        total = conn.execute(
            "SELECT count(*) FROM ohlcv WHERE symbol = 'BNY' AND timeframe = '4h'"
        ).fetchone()
        print(f"BNY 4h rows in ohlcv     : {total[0] if total else 0}")
        print(f"Wrong-instrument to purge: {len(rows)}")
        if rows:
            closes = [float(r[3]) for r in rows]
            print(f"  BNY 4h   in {min(closes):.2f}..{max(closes):.2f}")

        if not apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return len(rows)

        conn.execute("BEGIN TRANSACTION")
        try:
            if rows:
                conn.executemany(
                    "DELETE FROM ohlcv "
                    "WHERE symbol = ? AND timeframe = ? AND open_time = ?",
                    [(r[0], r[1], r[2]) for r in rows],
                )
            record_applied(conn, __file__, len(rows))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        print(f"\nApplied: {len(rows)} row(s) deleted.")
        return len(rows)
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    migrate(args.db, args.apply)


if __name__ == "__main__":
    main()
