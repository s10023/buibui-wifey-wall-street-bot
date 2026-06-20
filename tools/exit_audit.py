"""MFE/MAE diagnostic over the live alert ledger (exit spec §2) — diagnose mode.

Answers "is the expiry / −R leak exit-fixable or entry-broken?" by reporting,
per cohort (win / loss / expired) and per (strategy, tf, direction) cell:
mean/median MFE_R and MAE_R, the share of trades whose MFE reached
>=0.5R / >=1.0R, the median tp_r they were asked to reach, and median bars
held. Read the tables against the 4-pattern verdict grid printed in the footer.

Read-only — no writes, no schema changes. The exit-policy A/B (spec §3–§5,
parent PR #437) ships as a follow-up and adds a `--replay` mode here.

Usage::

    PYTHONPATH=. poetry run python tools/exit_audit.py
    PYTHONPATH=. poetry run python tools/exit_audit.py --min-n 20
    PYTHONPATH=. poetry run python tools/exit_audit.py --csv /tmp/excursions.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

from analytics.exits import aggregate_cohorts, compute_excursions
from analytics.store import DEFAULT_DB_PATH


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--min-n",
        type=int,
        default=30,
        help="hide cohort×cell rows with fewer than this many trades",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="optional path to dump the per-alert excursion rows",
    )
    args = parser.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    excursions = compute_excursions(con)
    resolved_row = con.execute(
        "SELECT count(*) FROM signal_alert_outcomes "
        "WHERE outcome IN ('win', 'loss', 'expired')"
    ).fetchone()
    resolved = int(resolved_row[0]) if resolved_row else 0
    print(
        f"Coverage: {len(excursions)} of {resolved} resolved alerts scored "
        f"({resolved - len(excursions)} skipped: zero-risk or missing OHLCV)"
    )
    if excursions.empty:
        print("(nothing to report)")
        return

    _print_df(
        "Cohort roll-up (all cells)", aggregate_cohorts(excursions, by=(), min_n=1)
    )
    _print_df(
        f"Cohort × (strategy, tf, direction) — min_n={args.min_n}",
        aggregate_cohorts(excursions, min_n=args.min_n),
    )
    print(
        "\nVerdict grid (exit spec §2):\n"
        "  expired reach_10 high + tp_r_p50 higher  => TP unreachably far: "
        "lower tp_r / add partial at 1R (exit fix).\n"
        "  expired reach_05 low                     => weak entries, not exits: "
        "prune/down-weight; don't paper over with exit tuning.\n"
        "  loss mfe high before SL                  => went +1R then reversed: "
        "breakeven / trail candidate (exit fix).\n"
        "  loss mfe low, fast mae                   => just wrong: exit can't "
        "help; entry/SL issue."
    )

    if args.csv is not None:
        excursions.to_csv(args.csv, index=False)
        print(f"\nPer-alert excursions written to {args.csv}")


if __name__ == "__main__":
    main()
