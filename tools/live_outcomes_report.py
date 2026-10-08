"""Eyeball-check `signal_alert_outcomes` after the T2 backfill worker runs.

Reports row totals, the resolved/open mix, and per-(strategy, tf, direction)
win rate + avg_r. Read-only — no writes, no schema changes.

Intended as a stop-gap until the Stats UI card lands. Run after the daemon
has been up long enough for the worker to resolve some trades (typically
≥1 hold-window per TF — see DEFAULT_MAX_HOLD_BARS in
`analytics/signal/outcome_backfill.py`).

Usage::

    PYTHONPATH=. poetry run python tools/live_outcomes_report.py
    PYTHONPATH=. poetry run python tools/live_outcomes_report.py --days 30
    PYTHONPATH=. poetry run python tools/live_outcomes_report.py --min-n 5
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.store import DEFAULT_DB_PATH


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:5.1f}%"


def _fmt_r(x: float | None) -> str:
    return "—" if x is None else f"{x:+.3f}"


def _print_table(title: str, rows: list[tuple], cols: list[str]) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("(no rows)")
        return
    widths = [
        max(len(c), max((len(str(r[i])) for r in rows), default=0))
        for i, c in enumerate(cols)
    ]
    line = " | ".join(c.ljust(w) for c, w in zip(cols, widths, strict=True))
    print(line)
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(str(x).ljust(w) for x, w in zip(r, widths, strict=True)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="restrict aggregates to rows fired in the last N days (0 = all time)",
    )
    parser.add_argument(
        "--min-n",
        type=int,
        default=1,
        help="hide per-cell rows with fewer than this many closed trades",
    )
    args = parser.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    # Roll-up: total rows, resolved mix, NULL outcomes.
    totals, totals_cols = (
        con.execute(
            """
        SELECT
          COUNT(*)                                     AS total_rows,
          COUNT(*) FILTER (WHERE outcome IS NOT NULL)  AS resolved,
          COUNT(*) FILTER (WHERE outcome IS NULL)      AS open,
          COUNT(*) FILTER (WHERE outcome IS NULL
                           AND tp_price IS NULL)       AS open_no_tp,
          COUNT(*) FILTER (WHERE outcome = 'win')      AS wins,
          COUNT(*) FILTER (WHERE outcome = 'loss')     AS losses,
          COUNT(*) FILTER (WHERE outcome = 'expired')  AS expired
        FROM signal_alert_outcomes
        """
        ).fetchone(),
        [d[0] for d in con.description],
    )
    if totals is None or totals[0] == 0:
        print("(signal_alert_outcomes is empty — nothing to report)")
        return
    _print_table("Roll-up", [totals], totals_cols)

    if totals[1] == 0:
        print(
            "\nNo resolved rows yet. The worker writes outcomes only after a "
            "TP/SL touch or after `max_hold_bars` elapses (see "
            "DEFAULT_MAX_HOLD_BARS). Re-run once the daemon has had time."
        )
        return

    where_clause = "WHERE outcome IS NOT NULL"
    params: tuple = ()
    if args.days > 0:
        cutoff_ms = int((time.time() - args.days * 86400) * 1000)
        where_clause += " AND fired_at_ms >= ?"
        params = (cutoff_ms,)
        print(f"\nFilter: last {args.days} day(s) (fired_at_ms >= {cutoff_ms})")
    else:
        print("\nFilter: all time")

    # Per-(strategy, tf, direction) breakdown.
    cell_rows, cell_cols = (
        con.execute(
            f"""
        SELECT
          strategy,
          tf,
          direction,
          COUNT(*)                                AS n,
          COUNT(*) FILTER (WHERE outcome='win')   AS wins,
          COUNT(*) FILTER (WHERE outcome='loss')  AS losses,
          COUNT(*) FILTER (WHERE outcome='expired') AS exp,
          AVG(CASE WHEN outcome='win'  THEN 1.0
                   WHEN outcome='loss' THEN 0.0 END) AS win_rate,
          AVG(outcome_r)                          AS avg_r
        FROM signal_alert_outcomes
        {where_clause}
        GROUP BY strategy, tf, direction
        HAVING COUNT(*) >= ?
        ORDER BY strategy, tf, direction
        """,
            (*params, args.min_n),
        ).fetchall(),
        [d[0] for d in con.description],
    )
    formatted = [
        (s, tf, dr, n, w, lo, ex, _fmt_pct(wr), _fmt_r(ar))
        for (s, tf, dr, n, w, lo, ex, wr, ar) in cell_rows
    ]
    _print_table(
        f"Per (strategy, tf, direction) — min_n={args.min_n}", formatted, cell_cols
    )

    # Per-strategy roll-up across TFs and directions.
    strat_rows, strat_cols = (
        con.execute(
            f"""
        SELECT
          strategy,
          COUNT(*) AS n,
          AVG(CASE WHEN outcome='win'  THEN 1.0
                   WHEN outcome='loss' THEN 0.0 END) AS win_rate,
          AVG(outcome_r)                          AS avg_r
        FROM signal_alert_outcomes
        {where_clause}
        GROUP BY strategy
        HAVING COUNT(*) >= ?
        ORDER BY avg_r DESC
        """,
            (*params, args.min_n),
        ).fetchall(),
        [d[0] for d in con.description],
    )
    strat_formatted = [
        (s, n, _fmt_pct(wr), _fmt_r(ar)) for (s, n, wr, ar) in strat_rows
    ]
    _print_table("Per strategy (rolled up)", strat_formatted, strat_cols)


if __name__ == "__main__":
    main()
