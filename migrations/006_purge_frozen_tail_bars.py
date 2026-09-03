"""Migration 006 — ohlcv: delete forward-filled dead-tape bars already stored

When a name stops trading, the provider keeps quoting it: the final print is
forward-filled at zero volume for as many sessions as the series is still
carried. Those bars are not observations. In a pooled cross-section they read as
consecutive exact-zero returns with no variance, which is the one shape that
cannot be distinguished from a real, perfectly flat asset.

`analytics/data_quality.py` now quarantines them at ingest (`frozen_tail_idx`,
gated on `series_ends_here`), but quarantine drops rows BEFORE storage and so
cannot reach what is already in the table. This deletes those rows once.

⚠ POSITION IS THE DISCRIMINATOR, AND THE ROW SHAPE ALONE IS NOT. This migration
was very nearly written against "zero volume AND unchanged close", which is how
the defect was first described. Measured 2026-09-02 over the whole DB that
predicate matches **1,368 rows**, of which only **9** are the dead tails: the
other **1,359** sit mid-history in live names — 808 `SW`, 231 `AMCR`, 188
`^GSPC`, 36 `^TNX`. Requiring the run to TERMINATE the series leaves exactly
those 9, across 3 (symbol, timeframe) series. The nearest surviving frozen row is
81 bars from its series end (`^TYX 1d`, over a full-column scan rather than the
top rows), so the two populations do not overlap and the rule has margin.

⚠ ZERO VOLUME ALONE IS NOT EVEN A HINT. `DX-Y.NYB`, `^TNX` and `^TYX` are
permanently zero-volume and perfectly healthy. 12,507 zero-volume rows are
stored and this rule removes 9 of them; the other 12,498 are LEFT ALONE rather
than certified correct, which is the claim the measurement actually supports.
The unchanged close is load-bearing.

Reproduce every figure above by swapping the run predicate in `_FROZEN_TAIL_SQL`:
the row-shape count (1,368) drops the `f.rn < live.first_live` clause, the
per-symbol split groups by `symbol`, and the margin is `min(rn)` over frozen rows
outside the purge set (`^TYX 1d` at 81).

DELETION, NOT A FLAG. `ohlcv` has no quarantine column and adding one to record
a one-off would be worse than the problem; the ingest path expresses the same
decision by never storing the row. A deleted bar is recoverable by refetch (and
would simply be re-quarantined), so this is not destroying a unique observation.

IDEMPOTENT BY VALUE. There is no "already purged" state to key on — the rule is
recomputed from the table each run, so a second run finds nothing and reports
zero. That also makes the dry run's count the exact size of the change.

SCOPE IS THE TAIL, NEVER THE SERIES. The last TRADED bar is kept: `SATS` stops
dead at real volume and so contributes nothing here, and `EA`/`EQR` keep their
final huge-volume session. Membership is a separate decision recorded in
`config/universe.json` (`delisted: true`), because a delisted member is retained
rather than deleted — deleting it is the survivorship edit the universe's own
note warns about.

Usage:
    python migrations/006_purge_frozen_tail_bars.py [--db PATH] [--apply]

Dry-run by default: prints what would change and exits without writing.
A .bak copy must exist alongside the DB before --apply will proceed.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.store import DEFAULT_DB_PATH  # noqa: E402

#: Rows whose (symbol, timeframe, open_time) belong to the maximal run of
#: frozen bars that ends at the series' newest bar. `rn` counts backwards from
#: the end, so the run is every frozen row with `rn` below the first non-frozen
#: one. A series with no non-frozen row anywhere is left alone: `first_live` is
#: NULL, the comparison is NULL, and nothing is selected — the same
#: keep-on-doubt direction as the ingest rule's row 0.
_FROZEN_TAIL_SQL = """
WITH w AS (
    SELECT symbol, timeframe, open_time, close, volume,
           lag(close) OVER (
               PARTITION BY symbol, timeframe ORDER BY open_time
           ) AS prev_close,
           row_number() OVER (
               PARTITION BY symbol, timeframe ORDER BY open_time DESC
           ) AS rn
    FROM ohlcv
), f AS (
    SELECT symbol, timeframe, open_time, rn, close,
           (volume <= 0 AND close = prev_close) AS frozen
    FROM w
), live AS (
    SELECT symbol, timeframe, min(rn) FILTER (WHERE NOT frozen) AS first_live
    FROM f GROUP BY 1, 2
)
SELECT f.symbol, f.timeframe, f.open_time, f.close
FROM f JOIN live USING (symbol, timeframe)
WHERE f.frozen AND f.rn < live.first_live
ORDER BY f.symbol, f.timeframe, f.open_time
"""


def migrate(db_path: str, apply: bool) -> None:
    if apply and not os.path.exists(db_path + ".bak"):
        print(f"Refusing to run: {db_path}.bak not found. Back the DB up first.")
        sys.exit(1)

    conn = duckdb.connect(db_path, read_only=not apply)
    try:
        rows = conn.execute(_FROZEN_TAIL_SQL).fetchall()

        total = conn.execute("SELECT count(*) FROM ohlcv").fetchone()
        zero_vol = conn.execute(
            "SELECT count(*) FROM ohlcv WHERE volume <= 0"
        ).fetchone()
        print(f"Rows in ohlcv            : {total[0] if total else 0}")
        print(f"  zero volume (all kept) : {zero_vol[0] if zero_vol else 0}")
        print(f"Frozen-tail rows to purge: {len(rows)}")

        by_series: dict[tuple[str, str], list[tuple[int, float]]] = {}
        for symbol, timeframe, open_time, close in rows:
            by_series.setdefault((str(symbol), str(timeframe)), []).append(
                (int(open_time), float(close))
            )
        for (symbol, timeframe), bars in sorted(by_series.items()):
            print(
                f"  {symbol:<10} {timeframe:<4} {len(bars):>3} bar(s) "
                f"frozen at {bars[0][1]:.4f}"
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
