"""Migration 008 — ohlcv: re-fetch 1wk series that Yahoo anchored off Monday

Under ``period="max"`` Yahoo anchors ``1wk`` bars on the weekday of the
symbol's first session, so a history that began on a Tuesday is stamped Tuesday
for every week. Measured 2026-10-09: six universe series were wholly off-Monday,
ABBV, UNP, LUV, PEG and SATS on Tuesday and AEE on Thursday, while the other 502
were wholly on Monday. A request that starts on or after the first session
anchors on Monday (``yf.Ticker("ABBV").history(interval="1wk",
start="2013-01-07")`` returns 718 Monday bars; ``period="max"`` returns 719
Tuesday ones). ``analytics/data_fetcher.py::_monday_anchored`` now makes every
fetch Monday-anchored (#323).

Re-syncing cannot repair the stored rows. ``sync`` upserts on
``(symbol, timeframe, open_time)``, so Monday bars would land beside the
Tuesday ones and leave a series with two bars per week. A stamp shift cannot
repair them either: a Tuesday bar spans Tuesday to the next Monday, which
straddles two Monday weeks. This migration replaces the off-Monday rows with a
fresh Monday-anchored fetch.

**It fetches before it deletes, per symbol.** A symbol the provider no longer
serves keeps its rows and is reported, because deleting history that cannot be
re-fetched is the irreversible case (migration 007). SATS is the known instance:
it is ``delisted: true`` in ``config/universe.json`` (#281), Yahoo returns 404,
and it is never re-synced, so its 442 Tuesday rows stay.

This is the one migration that needs the network. Rows come from ``fetch_bars``
starting at the symbol's earliest stored off-Monday bar, and are stored through
``data_sync._store_page``, the same quality check and quarantine a backfill
applies. Idempotent by value: a second run finds no off-Monday rows on any
re-fetched symbol and reports only the unserved ones.

Usage:
    python migrations/008_realign_off_monday_weekly_bars.py [--db PATH] [--apply]

Dry-run by default: prints what would change and exits without writing. The dry
run makes no network calls.
A .bak copy must exist alongside the DB before --apply will proceed.

The .bak guard tests existence, not freshness, as in every migration here. Cut a
fresh copy from the current database before --apply; a weeks-old one satisfies
it silently.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.data_fetcher import BARS_MAX_LIMIT, fetch_bars  # noqa: E402
from analytics.data_sync import _store_page  # noqa: E402
from analytics.store import DEFAULT_DB_PATH  # noqa: E402

#: ``open_time`` is UTC ms; a 1wk bar stamps 04:00/05:00 UTC, i.e. midnight ET,
#: so the UTC calendar day is the ET day. DuckDB's ``dayofweek`` has Monday = 1.
_OFF_MONDAY = "dayofweek(to_timestamp(open_time / 1000) AT TIME ZONE 'UTC') <> 1"

_SUMMARY_SQL = f"""
SELECT symbol, count(*), min(open_time), max(open_time)
FROM ohlcv
WHERE timeframe = '1wk' AND {_OFF_MONDAY}
GROUP BY symbol
ORDER BY symbol
"""

_DELETE_SQL = (
    f"DELETE FROM ohlcv WHERE timeframe = '1wk' AND symbol = ? AND {_OFF_MONDAY}"
)


def migrate(db_path: str, apply: bool) -> dict[str, list[str]]:
    """Returns ``{"realigned": [...], "kept": [...]}`` by symbol."""
    if apply and not os.path.exists(db_path + ".bak"):
        print(f"Refusing to run: {db_path}.bak not found. Back the DB up first.")
        sys.exit(1)

    outcome: dict[str, list[str]] = {"realigned": [], "kept": []}
    conn = duckdb.connect(db_path, read_only=not apply)
    try:
        rows = conn.execute(_SUMMARY_SQL).fetchall()
        print(f"1wk series with off-Monday bars: {len(rows)}")
        for symbol, n, _first, _last in rows:
            print(f"  {symbol:<6} {n:>4} off-Monday bar(s)")

        if not apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return outcome

        for symbol, n, first_ms, _last in rows:
            try:
                fresh = fetch_bars(symbol, "1wk", int(first_ms))
            except Exception as exc:  # noqa: BLE001 - one symbol must not stop the rest
                print(f"  {symbol}: KEPT {n} row(s), fetch failed: {exc}")
                outcome["kept"].append(symbol)
                continue
            if fresh.empty:
                print(f"  {symbol}: KEPT {n} row(s), the provider serves nothing")
                outcome["kept"].append(symbol)
                continue
            if len(fresh) >= BARS_MAX_LIMIT:
                print(f"  {symbol}: KEPT {n} row(s), fetch filled a whole page")
                outcome["kept"].append(symbol)
                continue
            conn.execute("BEGIN")
            try:
                conn.execute(_DELETE_SQL, [symbol])
                stored = _store_page(conn, symbol, "1wk", fresh, series_ends_here=True)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            print(
                f"  {symbol}: replaced {n} off-Monday row(s) with {stored} Monday row(s)"
            )
            outcome["realigned"].append(symbol)

        print(
            f"\nApplied: {len(outcome['realigned'])} realigned, "
            f"{len(outcome['kept'])} kept."
        )
        return outcome
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
