"""Migration 005 — signal_alert_outcomes: re-price gapped fills on BOTH sides

Until 2026-08-20 the live resolver booked a flat `-1.0` for every loss and the
`implied_tp_r` credit for every win, regardless of where the bar actually opened.
A bar that OPENS beyond the level does not fill at the level — it fills at the
open. `engine.py` had the identical assumption (`exit_price = sl_price` /
`= tp_price`), so the absence was SHARED and biased neither book against the
other. Both are fixed forward in the same PR; this restates the rows already
written.

⚠ SYMMETRIC, AND THAT IS THE POINT. The 2026-08-19 audit measured only the
ADVERSE tail and this migration was very nearly written one-sided. On this
ledger 46 of 218 losses (21.1%) gap through their stop, but **12 of 46 wins
(26.1%) gap through their target** — the higher rate of the two. Pricing only
the losses does not remove a bias, it installs its mirror: it would move pooled
`avg_r` -0.2192 -> -0.3218 where the symmetric answer is ~-0.2554, overstating
the real bias by ~65% and making every sleeve look worse than it is against a
stop-free benchmark. Measured by `docs/plans/scripts/gap_through_tp_live.py`,
the mirror of the audit's four scripts.

RE-DERIVED, NEVER RECONSTRUCTED — same rule as 004. Each row's new gross R comes
from re-running the resolver's own `_scan_forward` against that row's stored
geometry and its OHLCV history. Nothing here re-implements the fill rule; if it
did, the boundary this migration exists to remove would survive it.

COST IS CARRIED OVER, NOT RECOMPUTED, and that is a deliberate narrowing.
`live_cost_r` is a function of direction, entry, stop, entry/exit TIMES and the
ADV/sigma context — never of the exit PRICE. The gap fill changes where a trade
filled, not which bar resolved it, so `outcome_filled_at_ms` is unchanged and
every cost input is unchanged with it. Recomputing would re-derive the identical
number while adding a second way for 004's basis to drift. `outcome_r` is
therefore rewritten as `new_gross - stored_cost`, and a row with
`outcome_cost_r IS NULL` (UNPRICED, never "cost nothing") keeps its gross basis.

THE REPLICA CHECK IS THE GATE. A re-run must reproduce each row's stored
`outcome` and `outcome_filled_at_ms` exactly; the audit got 218/218 on losses and
46/46 on wins. A row that does not reproduce is COUNTED AND SKIPPED rather than
rewritten — a mismatch means the row's OHLCV moved under it, and a migration
that "fixes" a row it can no longer reproduce is writing a guess.

IDEMPOTENT BY VALUE, not by a state flag. There is no "already restated" column
to key on, and adding one to record a one-off would be worse than the problem.
Instead only rows whose recomputed value actually DIFFERS are written, so a
second run reports zero changes. That also makes the dry run's count the exact
size of the change.

NO ERA CUTOFF. The resolver booked the level from the first row to the last, so
every resolved row is uniformly on the old basis and one rule covers all of them.

Rewriting in place is safe: `signal_id` is
`{symbol}-{tf}-{strategy}-{open_time}-{direction}`, so `outcome_r` is not part of
the row identity. No key churn, no cascade — same reasoning as 003 and 004.

Usage:
    python migrations/005_symmetric_gap_fill.py [--db PATH] [--apply]

Dry-run by default: prints what would change and exits without writing.
A .bak copy must exist alongside the DB before --apply will proceed.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.data_store import get_ohlcv  # noqa: E402
from analytics.signal.outcome_backfill import (  # noqa: E402
    DEFAULT_MAX_HOLD_BARS,
    _scan_forward,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402


def migrate(db_path: str, apply: bool) -> None:
    if apply and not os.path.exists(db_path + ".bak"):
        print(f"Refusing to run: {db_path}.bak not found. Back the DB up first.")
        sys.exit(1)

    conn = duckdb.connect(db_path, read_only=not apply)
    try:
        rows = conn.execute(
            "SELECT signal_id, symbol, tf, direction, candle_ts_ms, entry_price, "
            "sl_price, tp_price, rr_ratio, outcome, outcome_r, outcome_cost_r, "
            "outcome_filled_at_ms "
            "FROM signal_alert_outcomes "
            # win/loss ONLY. An `expired` row is marked to market at the last
            # bar's close, never at a level, so a fill rule cannot move it — and
            # its `outcome_filled_at_ms` depends on where the fetch window ends,
            # so including it put 10 rows into the replica check's mismatch
            # column for a reason that had nothing to do with this migration.
            # Scope the restatement to the rows the change can actually reach.
            "WHERE outcome IN ('win', 'loss') "
            "AND tp_price IS NOT NULL AND sl_price IS NOT NULL "
            "ORDER BY symbol, tf, candle_ts_ms"
        ).fetchall()

        n_row = conn.execute(
            "SELECT COUNT(*), AVG(outcome_r) FROM signal_alert_outcomes "
            "WHERE outcome IS NOT NULL"
        ).fetchone()
        n_resolved = int(n_row[0]) if n_row else 0
        avg_before = float(n_row[1]) if n_row and n_row[1] is not None else 0.0

        print(f"Resolved rows in ledger : {n_resolved}")
        print(f"Rows eligible to re-price: {len(rows)}")
        if not rows:
            print("Nothing to do.")
            conn.close()
            return

        by_key: dict[tuple[str, str], list[tuple]] = {}
        for r in rows:
            by_key.setdefault((str(r[1]), str(r[2])), []).append(r)

        updates: list[tuple[float, str]] = []
        replica_ok = 0
        replica_bad = 0
        no_hold_cap = 0
        no_bars = 0
        gapped_losses = 0
        gapped_wins = 0
        total_delta = 0.0

        for (symbol, tf), key_rows in by_key.items():
            max_hold = DEFAULT_MAX_HOLD_BARS.get(tf)
            if max_hold is None:
                no_hold_cap += len(key_rows)
                continue
            earliest = min(int(r[4]) for r in key_rows)
            # ⚠ The end bound is the table's own MAX(open_time), never a
            # bar-count arithmetic. A bar count is NOT a calendar span on an RTH
            # tape: `4h` RTH is 2 bars/day, so `max_hold` bars spans `max_hold/2`
            # DAYS, and the obvious `latest + max_hold * tf_secs` window is short
            # by a factor of six. It was, and it silently truncated 6 of 264
            # walks into replica mismatches — rows this migration would then have
            # skipped, on a fetch bug rather than on any property of the data.
            end_row = conn.execute(
                "SELECT MAX(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = ?",
                [symbol, tf],
            ).fetchone()
            if not end_row or end_row[0] is None:
                no_bars += len(key_rows)
                continue
            bars = get_ohlcv(conn, symbol, tf, earliest, int(end_row[0]))
            if bars.empty:
                no_bars += len(key_rows)
                continue

            for r in key_rows:
                (
                    signal_id,
                    _sym,
                    _tf,
                    direction,
                    candle_ts_ms,
                    entry_price,
                    sl_price,
                    tp_price,
                    rr_ratio,
                    outcome,
                    outcome_r,
                    outcome_cost_r,
                    filled_at_ms,
                ) = r

                new_outcome, new_gross, new_filled = _scan_forward(
                    bars,
                    int(candle_ts_ms),
                    str(direction),
                    float(entry_price),
                    float(sl_price),
                    float(tp_price),
                    float(rr_ratio),
                    max_hold,
                )
                # The gate: a row we cannot reproduce is not a row we may rewrite.
                if (
                    new_outcome != outcome
                    or new_gross is None
                    or new_filled is None
                    or int(new_filled) != int(filled_at_ms)
                ):
                    replica_bad += 1
                    continue
                replica_ok += 1

                cost = float(outcome_cost_r) if outcome_cost_r is not None else 0.0
                new_r = float(new_gross) - cost
                if abs(new_r - float(outcome_r)) < 1e-12:
                    continue
                if outcome == "loss":
                    gapped_losses += 1
                else:
                    gapped_wins += 1  # `outcome` can only be 'win' here
                total_delta += new_r - float(outcome_r)
                updates.append((new_r, str(signal_id)))

        print(f"Replica check           : {replica_ok} ok / {replica_bad} mismatched")
        if no_hold_cap:
            print(f"  skipped, no hold cap  : {no_hold_cap}")
        if no_bars:
            print(f"  skipped, no OHLCV     : {no_bars}")
        print(f"Rows whose value changes: {len(updates)}")
        print(f"  gapped losses (worse) : {gapped_losses}")
        print(f"  gapped wins   (better): {gapped_wins}")
        if n_resolved:
            print(f"Pooled avg_r before     : {avg_before:+.4f}")
            print(
                f"Pooled avg_r after      : {avg_before + total_delta / n_resolved:+.4f}"
            )
            print(
                f"  net move              : {total_delta / n_resolved:+.4f}R per resolved row"
            )

        if not apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            conn.close()
            return

        if updates:
            conn.executemany(
                "UPDATE signal_alert_outcomes SET outcome_r = ? WHERE signal_id = ?",
                updates,
            )
        print(f"\nApplied: {len(updates)} row(s) re-priced.")
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
