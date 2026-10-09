"""Migration 004 — signal_alert_outcomes: gross R → net of costs

`run_backtest` has charged its trades since Phase 0.4, and the live outcome
resolver never charged anything. So `signal_alert_outcomes.outcome_r` was a
gross figure sitting in the same units as a net one, and every
backtest-vs-live comparison was biased **in favour of live** — the opposite of
the direction one assumes when a live book underperforms its backtest. The
resolver is fixed forward in the same PR as this migration; this restates the
rows it already wrote.

Why the restatement is not optional. Fixing only the resolver leaves the ledger
with two permanent bases split at an arbitrary instant — gross before the fix,
net after — and a boundary inside one column is worse than a uniformly wrong
column, because it silently breaks every pooled statistic that spans it. The
parent's own port shipped without the restatement and ended up with exactly
that. One consistent basis is the deliverable; the resolver change alone is not.

Re-derived, never reconstructed. Cost is recomputed from each row's own stored
geometry (`direction`, `entry_price`, `sl_price`, `candle_ts_ms`,
`outcome_filled_at_ms`) plus the OHLCV history around its signal bar, through
the same `live_cost_r` the resolver now calls. Sharing that one definition is
the point: if the migration priced rows its own way, the boundary would survive
the migration that exists to remove it.

No era cutoff, unlike 002 — and for a stronger reason than 003's. There is no
era to split: the resolver charged zero from the first row to the last, so every
resolved row is uniformly gross and one rule covers all of them. The guard is
`outcome_cost_r IS NULL`, which is state rather than a date, so this is
idempotent by construction and a re-run finds nothing. That also means a row
resolved by the new resolver is never touched here.

Conservative where it cannot see. A row whose signal bar is missing from OHLCV
gets `ctx=None`, and `cost_breakdown` then charges the widest spread bucket with
zero impact. It overcharges rather than quietly charging nothing, because a
missing context must never be able to look like a free trade. The count is
reported so the fraction is visible rather than assumed.

Both configs must agree. `signal_alert_outcomes` has no column recording which
live config produced a row, so a per-row cost model is not attributable. This
refuses to run unless both configs resolve to the same `CostModel` — they do,
because `[backtest.cost_model]` lives in the shared base — rather than silently
picking one. If they ever diverge, the fix is a provenance column, not a guess.

What this does not fix, and how to frame it. Uncharged costs were an
asymmetry — the backtest charged, live did not — which is exactly why they
biased the comparison and why this migration exists. A gap through the stop is a
different animal: it still books exactly -1.0R here, but `engine.py` also sets
`exit_price = sl_price`, so both books assume a clean touch. That absence is
shared, so it does not bias live against backtest — it just means both overstate.
It is also the bigger number: on this ledger 46 of 218 losses (21.1%) gapped
through, worth ~-0.10R per resolved row versus this migration's -0.014R. So the
claim here is "the two books are now on one basis", never "the ledger is right".

Rewriting in place is safe: `signal_id` is
`{symbol}-{tf}-{strategy}-{open_time}-{direction}`, so neither column is part of
the row identity. No key churn, no cascade — same reasoning as 003.

Usage:
    python migrations/004_live_ledger_net_of_cost.py [--db PATH] [--apply]

Dry-run by default: prints what would change and exits without writing.
--apply refuses unless <db>.bak is a byte copy of the DB: cut a fresh one first.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.backtest.cost_model import CostModel  # noqa: E402
from analytics.data_store import get_ohlcv  # noqa: E402
from analytics.signal._common import parse_timeframe_secs  # noqa: E402
from analytics.signal.outcome_backfill import (  # noqa: E402
    cost_context_at,
    cost_window_bars,
    live_cost_r,
)
from analytics.signal_config import load_signal_config  # noqa: E402
from analytics.store.migration_bak import require_fresh_bak  # noqa: E402

# Both live configs. They inherit `[backtest.cost_model]` from the shared base,
# so they agree — but that is asserted below rather than assumed, because the
# table cannot attribute a row to either one.
LIVE_CONFIGS = ("config/signal_watch.toml", "config/signal_watch_weekdays.toml")


def _resolve_cost_model(
    config_paths: tuple[str, ...],
) -> tuple[CostModel | None, float]:
    """The one (CostModel, fee_pct) governing every row, or exit if ambiguous."""
    seen: dict[str, tuple[CostModel | None, float]] = {}
    for path in config_paths:
        if not os.path.exists(path):
            print(f"Refusing to run: config {path} not found.")
            sys.exit(1)
        cfg = load_signal_config(Path(path))
        model = cfg.backtest.cost_model
        key = (
            f"{model.to_json() if model is not None else 'None'}|{cfg.backtest.fee_pct}"
        )
        seen[key] = (model, cfg.backtest.fee_pct)

    if len(seen) != 1:
        print("Refusing to run: the live configs disagree on the cost basis.")
        for key in seen:
            print(f"  {key}")
        print(
            "signal_alert_outcomes records no config provenance, so rows cannot "
            "be attributed. Add a provenance column rather than picking one."
        )
        sys.exit(1)
    return next(iter(seen.values()))


def migrate(db_path: str, apply: bool) -> None:
    if apply:
        require_fresh_bak(db_path)

    cost_model, fee_pct = _resolve_cost_model(LIVE_CONFIGS)
    if cost_model is None and fee_pct == 0.0:
        print(
            "Refusing to run: the live config prices nothing (no cost_model, "
            "fee_pct=0). Charging zero would stamp outcome_cost_r=0.0 on every "
            "row, which asserts 'measured, no cost' where the truth is 'unpriced'."
        )
        sys.exit(1)

    # Dry run must not be able to write, and must not take a write lock the user
    # then has to wait out. Plain `duckdb.connect` on purpose: a hand-run
    # migration colliding with another process should fail loudly, not queue.
    conn = duckdb.connect(db_path, read_only=not apply)
    try:
        # The column may not exist yet: `init_schema` adds it, but a DB that has
        # not been opened by the app since the schema change will not have it.
        # This migration therefore adds it itself rather than depending on an
        # unrelated startup path having run first — a migration that only works
        # after something else happened is a migration that fails in the field.
        col_row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'signal_alert_outcomes' "
            "AND column_name = 'outcome_cost_r'"
        ).fetchone()
        has_cost_col = bool(col_row[0]) if col_row else False
        if not has_cost_col and apply:
            conn.execute(
                "ALTER TABLE signal_alert_outcomes ADD COLUMN outcome_cost_r DOUBLE"
            )
            has_cost_col = True

        # Without the column every resolved row is on the gross basis by
        # construction, so the dry run can still report the full restatement.
        unrestated = (
            "AND outcome_cost_r IS NULL" if has_cost_col else "/* column absent */"
        )
        rows = conn.execute(
            "SELECT signal_id, symbol, tf, direction, candle_ts_ms, entry_price, "
            "sl_price, outcome, outcome_r, outcome_filled_at_ms "
            "FROM signal_alert_outcomes "
            f"WHERE outcome IS NOT NULL {unrestated} "
            "ORDER BY symbol, tf, candle_ts_ms"
        ).fetchall()
        if not has_cost_col:
            print("NOTE: outcome_cost_r absent; --apply will add it.")

        n_resolved_row = conn.execute(
            "SELECT COUNT(*), AVG(outcome_r) FROM signal_alert_outcomes "
            "WHERE outcome IS NOT NULL"
        ).fetchone()
        n_resolved = int(n_resolved_row[0]) if n_resolved_row else 0
        avg_before = (
            float(n_resolved_row[1])
            if n_resolved_row and n_resolved_row[1] is not None
            else 0.0
        )

        print(f"Resolved rows in ledger      : {n_resolved}")
        print(f"Rows still on the gross basis: {len(rows)}")
        if not rows:
            print("Nothing to do — every resolved row already carries a cost.")
            conn.close()
            return

        # Group by (symbol, tf) so each frame's OHLCV is fetched once — the same
        # batching shape the resolver uses.
        by_key: dict[tuple[str, str], list[tuple]] = {}
        for r in rows:
            by_key.setdefault((str(r[1]), str(r[2])), []).append(r)

        updates: list[tuple[float, float, str]] = []
        total_cost = 0.0
        no_context = 0
        no_geometry = 0

        for (symbol, tf), key_rows in by_key.items():
            tf_secs = parse_timeframe_secs(tf)
            earliest = min(int(r[4]) for r in key_rows)
            latest = max(int(r[4]) for r in key_rows)
            window_ms = (
                cost_window_bars(cost_model, tf) * tf_secs * 1000
                if cost_model is not None
                else 0
            )
            bars = get_ohlcv(
                conn, symbol, tf, earliest - window_ms, latest + tf_secs * 1000
            )

            for (
                signal_id,
                _sym,
                _tf,
                direction,
                candle_ts_ms,
                entry_price,
                sl_price,
                _outcome,
                outcome_r,
                filled_at,
            ) in key_rows:
                if entry_price is None or sl_price is None or outcome_r is None:
                    # Cost in R is undefined without the geometry that defines R.
                    # Left NULL deliberately: stamping 0.0 would claim a measured
                    # zero where the truth is that the row cannot be priced.
                    no_geometry += 1
                    continue

                ctx = None
                if cost_model is not None and not bars.empty:
                    ctx = cost_context_at(bars, int(candle_ts_ms), tf, cost_model)
                if cost_model is not None and ctx is None:
                    no_context += 1

                cost_r = live_cost_r(
                    direction=str(direction),
                    entry_price=float(entry_price),
                    sl_price=float(sl_price),
                    entry_time_ms=int(candle_ts_ms) + tf_secs * 1000,
                    exit_time_ms=int(filled_at) if filled_at is not None else None,
                    cost_model=cost_model,
                    fee_pct=fee_pct,
                    ctx=ctx,
                )
                total_cost += cost_r
                updates.append((float(outcome_r) - cost_r, cost_r, str(signal_id)))

        n_priced = len(updates)
        mean_cost = total_cost / n_priced if n_priced else 0.0
        avg_after = (
            (avg_before * n_resolved - total_cost) / n_resolved if n_resolved else 0.0
        )

        print(f"Rows priced                  : {n_priced}")
        print(f"  unpriceable (no geometry)  : {no_geometry}")
        print(f"  widest-bucket fallback     : {no_context} (signal bar not in OHLCV)")
        print(f"Mean cost charged            : {mean_cost:.4f}R per row")
        print(f"Total R removed              : {-total_cost:+.4f}R")
        print(f"Pooled avg_r: {avg_before:+.4f} → {avg_after:+.4f} (gross → net)")

        if not apply:
            print("\nDry run — pass --apply to write.")
            conn.close()
            return

        conn.execute("BEGIN TRANSACTION")
        conn.executemany(
            "UPDATE signal_alert_outcomes "
            "SET outcome_r = ?, outcome_cost_r = ? WHERE signal_id = ?",
            updates,
        )
        conn.execute("COMMIT")

        after = conn.execute(
            "SELECT AVG(outcome_r) FROM signal_alert_outcomes WHERE outcome IS NOT NULL"
        ).fetchone()
        residual = conn.execute(
            "SELECT COUNT(*) FROM signal_alert_outcomes "
            "WHERE outcome IS NOT NULL AND outcome_cost_r IS NULL"
        ).fetchone()
        print(f"\nMigration complete. {n_priced} rows restated to a net basis.")
        print(
            f"Pooled avg_r now: "
            f"{float(after[0]) if after and after[0] is not None else 0.0:+.4f}"
        )
        print(
            f"Rows left on the gross basis: "
            f"{int(residual[0]) if residual else 0} (expected {no_geometry})"
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
