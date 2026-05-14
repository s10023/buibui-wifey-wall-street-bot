"""Combo-backtest health check.

Spot-checks `backtest_combos` and `backtest_cross_tf_combos` after a
refresh run. Prints totals, freshness (rows from runs in the last N
hours), distribution across day_filter buckets, and the count of rows
meeting the live alert gates plus the top viable combos.

Use this after::

    make wifey-combo-backtest    CONFIG=<cfg> SAVE=1 SINCE=2025-09-12
    make wifey-cross-tf-backtest CONFIG=<cfg> SAVE=1 SINCE=2025-09-12

Live gate defaults match `[combo]` in `config/strategy_params.toml`
(same-TF: tue_thu + avg_r ≥ 1.0; cross-TF: tue_thu + avg_r ≥ 0.0).

Usage::

    PYTHONPATH=. poetry run python tools/combo_health.py
    PYTHONPATH=. poetry run python tools/combo_health.py --fresh-hours 6
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from analytics.store import DEFAULT_DB_PATH


def _print_table(title: str, rows: list[tuple], cols: list[str]) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("(no rows)")
        return
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(x) for x in r))


def _run(
    con: duckdb.DuckDBPyConnection, sql: str, params: tuple = ()
) -> tuple[list[tuple], list[str]]:
    rows = con.execute(sql, params).fetchall()
    cols = [d[0] for d in con.description]
    return rows, cols


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--fresh-hours", type=float, default=2.0, help="freshness window"
    )
    parser.add_argument(
        "--same-tf-min-avg-r",
        type=float,
        default=1.0,
        help="live gate avg_r floor for same-TF",
    )
    parser.add_argument(
        "--cross-tf-min-avg-r",
        type=float,
        default=0.0,
        help="live gate avg_r floor for cross-TF",
    )
    parser.add_argument(
        "--min-trades", type=int, default=5, help="minimum closed_trades for viability"
    )
    parser.add_argument(
        "--day-filter", default="tue_thu", help="day_filter bucket for live gates"
    )
    args = parser.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    cutoff_ms = int((time.time() - args.fresh_hours * 3600) * 1000)

    print(f"DB: {args.db}")

    for tbl in ("backtest_combos", "backtest_cross_tf_combos"):
        rows, cols = _run(
            con,
            f"""SELECT COUNT(*) AS n,
                       to_timestamp(MAX(run_at_ms)/1000) AS last_run_utc
                FROM {tbl}""",
        )
        _print_table(f"{tbl}: totals + last run", rows, cols)

        rows, cols = _run(
            con,
            f"SELECT COUNT(*) AS n_fresh FROM {tbl} WHERE run_at_ms > ?",
            (cutoff_ms,),
        )
        _print_table(f"{tbl}: rows from runs < {args.fresh_hours:g}h ago", rows, cols)

        rows, cols = _run(
            con,
            f"SELECT day_filter, COUNT(*) AS n FROM {tbl} GROUP BY 1 ORDER BY 2 DESC",
        )
        _print_table(f"{tbl}: day_filter distribution", rows, cols)

    # Live-gate viable counts
    rows, cols = _run(
        con,
        """SELECT COUNT(*) AS n_viable
           FROM backtest_combos
           WHERE day_filter = ? AND avg_r >= ? AND closed_trades >= ?""",
        (args.day_filter, args.same_tf_min_avg_r, args.min_trades),
    )
    _print_table(
        f"same-TF viable (day_filter={args.day_filter}, avg_r>={args.same_tf_min_avg_r}, n>={args.min_trades})",
        rows,
        cols,
    )

    rows, cols = _run(
        con,
        """SELECT COUNT(*) AS n_viable
           FROM backtest_cross_tf_combos
           WHERE day_filter = ? AND avg_r >= ? AND closed_trades >= ?""",
        (args.day_filter, args.cross_tf_min_avg_r, args.min_trades),
    )
    _print_table(
        f"cross-TF viable (day_filter={args.day_filter}, avg_r>={args.cross_tf_min_avg_r}, n>={args.min_trades})",
        rows,
        cols,
    )

    # Top viable rows for eyeball sanity check
    rows, cols = _run(
        con,
        """SELECT strategy_a, strategy_b, symbol, timeframe,
                  closed_trades AS n,
                  printf('%.0f%%', win_rate*100) AS wr,
                  printf('%.2fR', avg_r) AS avg_r
           FROM backtest_combos
           WHERE day_filter = ? AND avg_r >= ? AND closed_trades >= ?
           ORDER BY avg_r DESC LIMIT 10""",
        (args.day_filter, args.same_tf_min_avg_r, args.min_trades),
    )
    _print_table("top 10 same-TF viable combos", rows, cols)

    rows, cols = _run(
        con,
        """SELECT strategy_htf, tf_htf, strategy_ltf, tf_ltf, symbol,
                  closed_trades AS n,
                  printf('%.0f%%', win_rate*100) AS wr,
                  printf('%.2fR', avg_r) AS avg_r
           FROM backtest_cross_tf_combos
           WHERE day_filter = ? AND avg_r >= ? AND closed_trades >= ?
           ORDER BY avg_r DESC LIMIT 10""",
        (args.day_filter, args.cross_tf_min_avg_r, args.min_trades),
    )
    _print_table("top 10 cross-TF viable combos", rows, cols)


if __name__ == "__main__":
    main()
