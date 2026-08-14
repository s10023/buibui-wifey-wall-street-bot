"""Migration 003 — signal_alert_outcomes: credit the EFFECTIVE TP, not the declared one

`scanner.py` recorded `rr_ratio = eff_alert_tp_r` (the *configured* `tp_r`) while
`_resolve_outcome_sl_tp` set `tp_price` to a detector's **structural** TP when it
had one, falling back to `entry ± sl_dist × tp_r` only otherwise. The resolver
then walked `tp_price` and, on a hit, credited `outcome_r = rr_ratio` — so an
alert whose TP sat 2.0R away was booked at 5.0R. Same declared-vs-executed family
as #142's `adr_suppress_threshold` and #144's `days`: a column recording what was
*configured* is not a record of what *happened*, and the nearer leg is the one
nobody wrote down.

Found by PR #194 (the exit-policy replay), whose `fixed` arm reproduced the live
resolver's label on 267 of 267 rows only once it derived the target from
`tp_price`. Fixed forward in the same PR as this migration: the resolver now
credits `implied_tp_r`, and the scanner records it.

TWO COLUMNS, TWO DIFFERENT CLAIMS, both re-derived from the row's own stored
geometry (`entry_price`, `sl_price`, `tp_price`) — never from config, so nothing
here is reconstructed or guessed:

  * `rr_ratio`  — the target the alert actually carried. Rewritten on every
    divergent row regardless of outcome, because it is read as an R level by
    `analytics/exits/mfe_mae.py` (the `max(prior_fav, rr_ratio)` win clamp, which
    a declared value inflates) and by its `tp_r_p50` cohort median.
  * `outcome_r` — the R credited on resolution. Rewritten on **wins only**: a
    loss books -1.0 and an expired row books mark-to-market off `sl_price`, so
    neither ever read `rr_ratio`.

NO ERA CUTOFF, deliberately — unlike 002, and this was checked against the DATA
rather than against git. An era split would look like resolved wins whose
`outcome_r` matches the *implied* target while disagreeing with `rr_ratio`; there
are none. Measured on the pre-migration `.bak`: of **45** resolved wins, **45**
credited exactly `rr_ratio` and **0** credited anything else, spanning the full
`fired_at_ms` range of the table. One era, so one rule. (The file's own history is
not a usable check here — it predates the `analytics/signal/` split and the
fork's history was copied, so `git show <old-sha>:<path>` returns nothing.)
The other historical writer,
`tools/backfill_null_tp_outcomes.py`, forces the pct fallback
(`struct_sl=struct_tp=0.0`), which makes implied ≡ declared by construction, so
its rows are not divergent and are not touched.

Rewriting in place is safe here and was NOT safe in 002: `signal_id` is
`{symbol}-{tf}-{strategy}-{open_time}-{direction}`, so neither column is part of
the row identity. No key churn, no cascade, and no risk of leaving the old row
behind as a fake before/after pair.

Idempotent: it compares stored against derived, so a second run finds nothing.

Usage:
    python migrations/003_outcome_r_effective_tp.py [--db PATH] [--apply]

Dry-run by default: prints what would change and exits without writing.
A .bak copy must exist alongside the DB before --apply will proceed.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.signal.outcome_backfill import implied_tp_r  # noqa: E402

# Float tolerance for "the stored value already equals the derived one". The
# derived value round-trips through a division, so an exact compare would
# rewrite pct-fallback rows for a 1e-16 difference.
TOL = 1e-6


def migrate(db_path: str, apply: bool) -> None:
    if apply and not os.path.exists(db_path + ".bak"):
        print(f"Refusing to run: {db_path}.bak not found. Back the DB up first.")
        sys.exit(1)

    # A dry run must not be able to write, and must not take a write lock the
    # user then has to wait out. Plain `duckdb.connect` rather than
    # `connect_with_retry` on purpose: a hand-run migration that collides with
    # another process should fail loudly, not silently queue behind it.
    conn = duckdb.connect(db_path, read_only=not apply)
    try:
        rows = conn.execute(
            "SELECT signal_id, direction, entry_price, sl_price, tp_price, "
            "rr_ratio, outcome, outcome_r FROM signal_alert_outcomes "
            "WHERE entry_price IS NOT NULL AND sl_price IS NOT NULL "
            "AND tp_price IS NOT NULL AND rr_ratio IS NOT NULL"
        ).fetchall()

        rr_updates: list[tuple[float, str]] = []
        r_updates: list[tuple[float, str]] = []
        preview: list[str] = []
        credit_delta = 0.0

        for (
            signal_id,
            direction,
            entry,
            sl_price,
            tp_price,
            rr_ratio,
            outcome,
            outcome_r,
        ) in rows:
            effective = implied_tp_r(
                direction=str(direction),
                entry=float(entry),
                sl_price=float(sl_price),
                rr_ratio=float(rr_ratio),
                tp_price=float(tp_price),
            )
            if abs(effective - float(rr_ratio)) <= TOL:
                continue

            rr_updates.append((effective, str(signal_id)))

            # A loss books -1.0 and an expired row marks to market off sl_price;
            # only a win was ever credited from rr_ratio.
            if (
                outcome == "win"
                and outcome_r is not None
                and abs(float(outcome_r) - effective) > TOL
            ):
                r_updates.append((effective, str(signal_id)))
                credit_delta += effective - float(outcome_r)
                preview.append(
                    f"  {signal_id}\n"
                    f"      rr_ratio  {float(rr_ratio):.3f} → {effective:.3f}\n"
                    f"      outcome_r {float(outcome_r):.3f} → {effective:.3f}"
                )

        resolved = conn.execute(
            "SELECT COUNT(*) FROM signal_alert_outcomes WHERE outcome IS NOT NULL"
        ).fetchone()
        n_resolved = int(resolved[0]) if resolved else 0

        print(f"Rows with scoreable geometry: {len(rows)}")
        print(
            f"Rows whose declared rr_ratio differs from the effective TP: "
            f"{len(rr_updates)}"
        )
        print(
            f"  of which resolved WINS needing an outcome_r rewrite: {len(r_updates)}"
        )
        print(
            f"Total R credited back: {credit_delta:+.4f}R "
            f"over {n_resolved} resolved rows "
            f"({credit_delta / n_resolved if n_resolved else 0.0:+.4f}R each)"
        )

        if not rr_updates:
            print("Nothing to do.")
            conn.close()
            return

        before = conn.execute(
            "SELECT AVG(outcome_r) FROM signal_alert_outcomes WHERE outcome IS NOT NULL"
        ).fetchone()
        avg_before = float(before[0]) if before and before[0] is not None else 0.0
        avg_after = (
            (avg_before * n_resolved + credit_delta) / n_resolved if n_resolved else 0.0
        )
        print(f"Pooled avg_r: {avg_before:+.4f} → {avg_after:+.4f}")

        if not apply:
            print("\nDry run — pass --apply to write. Wins that would change:")
            for line in preview:
                print(line)
            conn.close()
            return

        conn.execute("BEGIN TRANSACTION")
        conn.executemany(
            "UPDATE signal_alert_outcomes SET rr_ratio = ? WHERE signal_id = ?",
            rr_updates,
        )
        conn.executemany(
            "UPDATE signal_alert_outcomes SET outcome_r = ? WHERE signal_id = ?",
            r_updates,
        )
        conn.execute("COMMIT")

        after = conn.execute(
            "SELECT AVG(outcome_r) FROM signal_alert_outcomes WHERE outcome IS NOT NULL"
        ).fetchone()
        print(
            f"\nMigration complete. {len(rr_updates)} rr_ratio and "
            f"{len(r_updates)} outcome_r values rewritten."
        )
        print(
            f"Pooled avg_r now: {float(after[0]) if after and after[0] else 0.0:+.4f}"
        )

    except Exception:
        conn.close()
        raise

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="analytics.db", help="Path to analytics.db")
    parser.add_argument(
        "--apply", action="store_true", help="Write changes (default: dry run)"
    )
    args = parser.parse_args()
    migrate(args.db, args.apply)
