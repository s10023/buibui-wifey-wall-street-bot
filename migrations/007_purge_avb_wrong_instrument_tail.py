"""Migration 007 — ohlcv: delete AVB's wrong-instrument tail

`AVB` (AvalonBay Communities, NYSE) trades around $184. Between 2026-07-17 and
2026-08-24 the provider's HISTORY endpoint began serving a different, much
cheaper instrument under that ticker — 27 `1d` bars and 7 `1wk` bars in the
$63–71 band — and then stopped updating it altogether. Those bars are not
observations of AvalonBay, and left in place they put a fabricated **-61.1%**
session (177.32 -> 68.93 on 2026-07-17) into every pooled cross-section that
reads the research universe.

This is not the split seam `data_sync.ADJUSTMENT_BASIS_TOL` guards, and
the two must not be conflated. A split restates the whole series consistently
and is repaired by re-syncing it; here the provider is serving the wrong
security, so a re-backfill cannot repair it. Measured 2026-09-04:
``yf.Ticker("AVB").history(start="2018-01-01")`` returns **27 rows**, all of
them the bogus window, while ``fast_info`` reports the real $184.06 — so a
re-backfill would overwrite the 27 bad bars with the same 27 bad bars and reach
none of the 2,127 good ones. Deletion is the only repair available from this
source.

Not a delisting, so `config/universe.json` is deliberately untouched. `EA`,
`EQR` and `SATS` were flagged `delisted: true` in #281 because they had stopped
trading; AVB is alive and quoted. Flagging it would state something false and
would remove a live constituent from the breadth universe. What AVB becomes
after this is a stale series (last good bar 2026-06-18), which
`make freshness-check` already reports and which a re-backfill will fix by
itself once the provider repairs the ticker.

Two predicates, and they agree exactly. The rows are selected by both value
(`close < 100`) and date (`open_time >= 2026-07-13`), because either alone
would be a claim about a boundary rather than about a population. Measured over
the full AVB history the two sets are identical, and the margin is wide in both
directions: the good bars' minimum low is 118.17 (not the close — the low is
the value that could dip across a close-based threshold) against the bogus
band's maximum high of 70.61, a 1.67x gap with nothing inside it. The good
series spans 2018-01-02 -> 2026-06-18 (`1d`) with no row anywhere below 118.17.

Idempotent by value, like 006: there is no "already purged" flag, the predicate
is recomputed each run, so a second run finds nothing and reports zero.

Usage:
    python migrations/007_purge_avb_wrong_instrument_tail.py [--db PATH] [--apply]

Dry-run by default: prints what would change and exits without writing.
A .bak copy must exist alongside the DB before --apply will proceed.

The .bak guard tests existence, not freshness — all seven migrations do. Cut a
fresh copy from the current database before --apply; a weeks-old one satisfies
it silently.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.store import DEFAULT_DB_PATH  # noqa: E402

#: 2026-07-01 UTC, chosen to sit inside the empty gap between the two
#: populations rather than on either edge of one: the last good bar is
#: 1781755200000 (`1wk` 2026-06-15 / `1d` 2026-06-18) and the first bogus bar is
#: 1783915200000 (`1wk` 2026-07-13 / `1d` 2026-07-17), with no AVB row of any
#: timeframe in between. Paired with the value predicate rather than trusted
#: alone.
_SEAM_MS: int = 1_782_864_000_000

#: Close below which an AVB bar cannot be AvalonBay. Good bars bottom at a low
#: of 118.17; the bogus band tops out at a high of 70.61.
_WRONG_INSTRUMENT_CLOSE: float = 100.0

_WRONG_TAIL_SQL = """
SELECT symbol, timeframe, open_time, close
FROM ohlcv
WHERE symbol = 'AVB' AND close < ? AND open_time >= ?
ORDER BY timeframe, open_time
"""

#: Rows matching ONE predicate but not the other. Must be empty — if it is not,
#: the two populations have started to overlap and the rule needs re-deriving
#: rather than re-running.
_DISAGREEMENT_SQL = """
SELECT count(*) FROM ohlcv
WHERE symbol = 'AVB' AND ((close < ?) <> (open_time >= ?))
"""


def migrate(db_path: str, apply: bool) -> None:
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
                f"Refusing to run: {n_disagree} AVB row(s) match the value predicate "
                "but not the date one (or vice versa). The two populations were "
                "disjoint when this was written; re-derive the rule."
            )
            sys.exit(1)

        rows = conn.execute(_WRONG_TAIL_SQL, params).fetchall()
        total = conn.execute(
            "SELECT count(*) FROM ohlcv WHERE symbol = 'AVB'"
        ).fetchone()
        print(f"AVB rows in ohlcv        : {total[0] if total else 0}")
        print(f"Wrong-instrument to purge: {len(rows)}")

        by_tf: dict[str, list[float]] = {}
        for _symbol, timeframe, _open_time, close in rows:
            by_tf.setdefault(str(timeframe), []).append(float(close))
        for timeframe, closes in sorted(by_tf.items()):
            print(
                f"  AVB {timeframe:<4} {len(closes):>3} bar(s) "
                f"in {min(closes):.2f}..{max(closes):.2f}"
            )

        if not apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return

        if rows:
            conn.executemany(
                "DELETE FROM ohlcv "
                "WHERE symbol = ? AND timeframe = ? AND open_time = ?",
                [(r[0], r[1], r[2]) for r in rows],
            )
        print(f"\nApplied: {len(rows)} row(s) deleted.")
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
