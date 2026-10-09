"""Migration 002 — backtest_runs.adr_suppress_threshold: declared → executed

Until 2026-08-11 the two writers that set this column (`_collect_sweep_results`
and `signal.scanner`) stored the config's declared value unconditionally, so a
row recorded 0.80 even where the ADR gate provably never touched it. The column
is provenance — it answers "what suppressed the signals behind this row" — so a
declared-but-not-executed value makes the audit trail corroborate a gate that
did not run. Same defect class as #144's `days`.

This migration rewrites only rows the current code and config govern, i.e. rows
written at or after #142 (`54cef12`, 2026-08-06T17:07:01+08:00 = 09:07:01Z),
which is the commit that made the gate no-op on timeframes where a calendar day
holds one bar. Earlier rows are deliberately left alone: before #142
`_filter_signals_by_adr` had no timeframe guard, so on `1d`/`1wk` the gate
genuinely DID run (degenerately — see
docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md), and #141 had not yet
moved `adr_exempt` into the shared base, so `bos` was not exempt on the weekdays
config. Their 0.80 is defensible as written, and the config state at write time
is not recorded anywhere, so it cannot be reconstructed. Rewriting them would
replace one false claim with another.

Because `adr_suppress_threshold` is part of the run_id hash, every rewritten row
gets a new run_id, cascaded to backtest_trades. Collisions are checked for and
abort the migration rather than silently merging two measurements.

Usage:
    python migrations/002_adr_threshold_executed.py [--db PATH] [--apply]

Dry-run by default: prints what would change and exits without writing.
--apply refuses unless <db>.bak is a byte copy of the DB: cut a fresh one first.
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from analytics.signal.gates import effective_adr_threshold  # noqa: E402
from analytics.store.backtest_runs import _backtest_run_id  # noqa: E402
from analytics.store.migration_bak import require_fresh_bak  # noqa: E402
from analytics.store.schema_migrations import is_recorded, record_applied  # noqa: E402

# #142 (54cef12) — "no-op the ADR gate where a calendar day holds one bar".
# Rows written at or after this instant ran the gate the current code describes.
CUTOFF_MS = int(
    datetime.datetime(2026, 8, 6, 9, 7, 1, tzinfo=datetime.UTC).timestamp() * 1000
)

# day_filter identifies which live config produced a row. Both configs inherit
# adr_exempt from the shared base since #141, but eqh_eql is still config-local
# to signal_watch.toml, so the mapping matters.
CONFIG_BY_DAY_FILTER = {
    "tue_thu": "config/signal_watch.toml",
    "weekdays": "config/signal_watch_weekdays.toml",
}


def migrate(db_path: str, apply: bool) -> None:
    from analytics.signal_config import load_signal_config

    if apply:
        require_fresh_bak(db_path)

    cfgs = {k: load_signal_config(v) for k, v in CONFIG_BY_DAY_FILTER.items()}
    conn = duckdb.connect(db_path)
    if apply and is_recorded(conn, __file__):
        # The predicate reads TODAY's config, not the config at write time, so
        # once applied it reads later config drift as pending: on 2026-10-09 it
        # flagged 13 truthful eqh_eql rows that `fa89d57` exempted after they
        # were written (#467). A re-run would rewrite them. Dry runs still work.
        conn.close()
        print(
            "Refusing to run: 002 is already recorded in schema_migrations, and "
            "its predicate now flags config drift, not undone work (#467)."
        )
        sys.exit(1)
    try:
        rows = conn.execute(
            "SELECT run_id, symbol, timeframe, strategy, days, sl_pct, tp_r, "
            "fee_pct, day_filter, adr_suppress_threshold, volume_suppress, "
            "cost_model, sweep_id, run_at_ms FROM backtest_runs "
            "WHERE run_at_ms >= ?",
            [CUTOFF_MS],
        ).fetchall()

        existing = {
            r[0] for r in conn.execute("SELECT run_id FROM backtest_runs").fetchall()
        }
        updates: list[tuple[str, float | None, str]] = []
        skipped_unknown: dict[str, int] = {}

        for (
            run_id,
            symbol,
            timeframe,
            strategy,
            days,
            sl_pct,
            tp_r,
            fee_pct,
            day_filter,
            recorded,
            volume_suppress,
            cost_model,
            sweep_id,
            _run_at,
        ) in rows:
            cfg = cfgs.get(day_filter)
            if cfg is None:
                skipped_unknown[day_filter] = skipped_unknown.get(day_filter, 0) + 1
                continue

            exempt = bool(
                getattr(cfg.strategy_params.get(strategy), "adr_exempt", False)
            )
            executed = effective_adr_threshold(
                cfg.bias.adr_suppress_threshold, timeframe, adr_exempt=exempt
            )
            if (recorded is None) == (executed is None):
                continue

            # sweep rows carry origin="sweep" (no suffix); a NULL sweep_id means
            # the live EV gate wrote it.
            origin = "sweep" if sweep_id is not None else "live_gate"
            new_id = _backtest_run_id(
                symbol,
                timeframe,
                strategy,
                days,
                sl_pct,
                tp_r,
                fee_pct,
                day_filter,
                executed,
                volume_suppress,
                cost_model=cost_model,
                origin=origin,
            )
            if new_id == run_id:
                continue
            if new_id in existing:
                print(
                    f"ABORT: rewriting {run_id} ({symbol}/{timeframe}/{strategy}) "
                    f"would collide with existing run_id {new_id}."
                )
                sys.exit(1)
            existing.add(new_id)
            updates.append((run_id, executed, new_id))

        print(f"Rows at/after #142 cutoff: {len(rows)}")
        print(
            f"Rows whose recorded threshold contradicts what executed: {len(updates)}"
        )
        if skipped_unknown:
            print(f"Skipped rows with an unmapped day_filter: {skipped_unknown}")

        if not updates:
            print("Nothing to do.")
            if apply:
                record_applied(conn, __file__, 0)
            conn.close()
            return

        if not apply:
            print("\nDry run — pass --apply to write. Sample of first 5:")
            for old, executed, new in updates[:5]:
                print(f"  {old} → {new}   adr_suppress_threshold 0.8 → {executed}")
            conn.close()
            return

        conn.execute("BEGIN TRANSACTION")
        for old, executed, new in updates:
            conn.execute(
                "UPDATE backtest_trades SET run_id = ? WHERE run_id = ?", [new, old]
            )
            conn.execute(
                "UPDATE backtest_runs SET run_id = ?, adr_suppress_threshold = ? "
                "WHERE run_id = ?",
                [new, executed, old],
            )
        record_applied(conn, __file__, len(updates))
        conn.execute("COMMIT")

        remaining = conn.execute(
            "SELECT COUNT(*) FROM backtest_runs WHERE run_at_ms >= ? "
            "AND adr_suppress_threshold IS NOT NULL",
            [CUTOFF_MS],
        ).fetchone()
        print(f"\nMigration complete. {len(updates)} rows rewritten.")
        print(
            f"Post-cutoff rows still carrying a threshold: {remaining[0] if remaining else 0}"
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
