"""Migration 001 — backtest_runs.day_filter: BOOLEAN → TEXT

Converts existing rows:
  TRUE  → "tue_thu"  (was: suppress Mon + Fri)
  FALSE → "off"      (was: no day filter)

Also recomputes run_id for every row (the hash includes day_filter, so the
string representation changes) and cascades the updated run_id to
backtest_trades.

Usage:
    python migrations/001_day_filter_text.py [--db PATH]

Default DB path: analytics.db (relative to project root).
A .bak copy must already exist before running — this script will refuse to
proceed if analytics.db.bak is not found alongside the DB file.
"""

import argparse
import hashlib
import os
import sys


def _run_id(
    symbol: str,
    timeframe: str,
    strategy: str,
    days: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
    smt_trend_filter: int,
    secondary_symbol: str | None,
) -> str:
    key = (
        f"{symbol}|{timeframe}|{strategy}|{days}|{sl_pct}|{tp_r}|"
        f"{fee_pct}|{day_filter}|{smt_trend_filter}|{secondary_symbol}"
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def migrate(db_path: str) -> None:
    bak_path = db_path + ".bak"
    if not os.path.exists(bak_path):
        print(f"ERROR: backup not found at {bak_path}")
        print("Create a backup first:  cp analytics.db analytics.db.bak")
        sys.exit(1)

    import duckdb

    conn = duckdb.connect(db_path)

    try:
        # 1. Read all existing backtest_runs rows
        rows = conn.execute(
            "SELECT run_id, symbol, timeframe, strategy, days, sl_pct, tp_r, "
            "fee_pct, day_filter, smt_trend_filter, secondary_symbol "
            "FROM backtest_runs"
        ).fetchall()

        print(f"Found {len(rows)} rows in backtest_runs")

        # 2. Build old_run_id → new values mapping
        updates: list[
            tuple[str, str, str]
        ] = []  # (old_run_id, new_run_id, new_day_filter)
        for row in rows:
            (
                old_id,
                symbol,
                tf,
                strategy,
                days,
                sl_pct,
                tp_r,
                fee_pct,
                df_bool,
                smt,
                sec,
            ) = row
            new_df = "no_monfi" if df_bool else "off"
            new_id = _run_id(
                symbol, tf, strategy, days, sl_pct, tp_r, fee_pct, new_df, smt, sec
            )
            updates.append((old_id, new_id, new_df))

        # Detect any run_id collisions (two old rows mapping to the same new id)
        new_ids = [u[1] for u in updates]
        if len(new_ids) != len(set(new_ids)):
            print("ERROR: run_id collision detected after recomputation — aborting")
            sys.exit(1)

        # 3. Recreate backtest_runs with TEXT column
        print("Recreating backtest_runs with TEXT day_filter column...")
        conn.execute("""
            CREATE TABLE backtest_runs_new (
                run_id               TEXT    PRIMARY KEY,
                symbol               TEXT    NOT NULL,
                timeframe            TEXT    NOT NULL,
                strategy             TEXT    NOT NULL,
                data_start_ms        BIGINT  NOT NULL,
                data_end_ms          BIGINT  NOT NULL,
                days                 INTEGER NOT NULL,
                sl_pct               DOUBLE  NOT NULL,
                tp_r                 DOUBLE  NOT NULL,
                fee_pct              DOUBLE  NOT NULL,
                day_filter           TEXT    NOT NULL,
                smt_trend_filter     INTEGER NOT NULL,
                secondary_symbol     TEXT,
                total_signals        INTEGER NOT NULL,
                closed_trades        INTEGER NOT NULL,
                win_count            INTEGER NOT NULL,
                loss_count           INTEGER NOT NULL,
                win_rate             DOUBLE  NOT NULL,
                avg_r                DOUBLE  NOT NULL,
                total_r              DOUBLE  NOT NULL,
                max_drawdown_r       DOUBLE  NOT NULL,
                run_at_ms            BIGINT  NOT NULL,
                sweep_id             TEXT
            )
        """)

        # 4. Insert converted rows (day_filter value converted via CASE in SQL)
        conn.execute("""
            INSERT INTO backtest_runs_new
            SELECT
                br.run_id,
                br.symbol, br.timeframe, br.strategy,
                br.data_start_ms, br.data_end_ms, br.days,
                br.sl_pct, br.tp_r, br.fee_pct,
                CASE WHEN br.day_filter THEN 'no_monfi' ELSE 'off' END AS day_filter,
                br.smt_trend_filter, br.secondary_symbol,
                br.total_signals, br.closed_trades, br.win_count, br.loss_count,
                br.win_rate, br.avg_r, br.total_r, br.max_drawdown_r,
                br.run_at_ms, br.sweep_id
            FROM backtest_runs br
        """)

        # 5. Update run_ids to the recomputed values using a temp mapping table
        print("Updating run_ids...")
        import pandas as pd

        mapping_df = pd.DataFrame(
            [(old, new) for old, new, _ in updates],
            columns=["old_id", "new_id"],
        )
        conn.register("_id_map", mapping_df)
        try:
            conn.execute("""
                UPDATE backtest_runs_new
                SET run_id = m.new_id
                FROM _id_map m
                WHERE backtest_runs_new.run_id = m.old_id
            """)
        finally:
            conn.unregister("_id_map")

        # 6. Cascade run_id updates to backtest_trades
        trades_row = conn.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()
        trades_count = trades_row[0] if trades_row else 0
        print(f"Cascading run_id updates to {trades_count} backtest_trades rows...")

        mapping_df2 = pd.DataFrame(
            [(old, new) for old, new, _ in updates],
            columns=["old_id", "new_id"],
        )
        conn.register("_id_map2", mapping_df2)
        try:
            conn.execute("""
                UPDATE backtest_trades
                SET run_id = m.new_id
                FROM _id_map2 m
                WHERE backtest_trades.run_id = m.old_id
            """)
        finally:
            conn.unregister("_id_map2")

        # 7. Swap tables
        print("Swapping tables...")
        conn.execute("DROP TABLE backtest_runs")
        conn.execute("ALTER TABLE backtest_runs_new RENAME TO backtest_runs")

        # 8. Verify
        count_row = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()
        count = count_row[0] if count_row else 0
        sample = conn.execute(
            "SELECT day_filter, COUNT(*) FROM backtest_runs GROUP BY day_filter"
        ).fetchall()
        print(f"\nMigration complete. {count} rows in backtest_runs.")
        print("day_filter distribution:", sample)

        # Record the apply (#467). Imported lazily, like duckdb above, after
        # putting the repo root on the path: this script predates that convention.
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from analytics.store.schema_migrations import record_applied

        record_applied(conn, __file__, count)

    except Exception:
        conn.close()
        raise

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="analytics.db", help="Path to analytics.db")
    args = parser.parse_args()
    migrate(args.db)
