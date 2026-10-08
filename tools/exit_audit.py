"""MFE/MAE diagnostic over the live alert ledger (exit spec §2) — diagnose mode.

Answers "is the expiry / −R leak exit-fixable or entry-broken?" by reporting,
per cohort (win / loss / expired) and per (strategy, tf, direction) cell:
mean/median MFE_R and MAE_R, the share of trades whose MFE reached
>=0.5R / >=1.0R, the median tp_r they were asked to reach, and median bars
held. Read the tables against the 4-pattern verdict grid printed in the footer.

`--replay` switches to the exit-policy A/B (spec §3–§5): every
resolved alert is re-resolved under each named policy and scored in R space.
Read `analytics/exits/audit.py`'s docstring for what the headline metric is and
why it is not upstream's portfolio Sharpe.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/exit_audit.py
    PYTHONPATH=. poetry run python tools/exit_audit.py --min-n 20
    PYTHONPATH=. poetry run python tools/exit_audit.py --csv /tmp/excursions.csv
    PYTHONPATH=. poetry run python tools/exit_audit.py --replay
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

from analytics.exits import (
    POLICY_KINDS,
    TIME_STOP_FLOOR_BY_TF,
    aggregate_cohorts,
    baseline_agreement,
    compute_excursions,
    resolve_ledger_under_policy,
    run_exit_ab,
)
from analytics.signal.outcome_backfill import DEFAULT_MAX_HOLD_BARS
from analytics.store import DEFAULT_DB_PATH


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


def _run_replay(con: duckdb.DuckDBPyConnection, n_boot: int) -> None:
    """Print the exit-policy A/B table (spec §3–§5)."""
    caps = ", ".join(f"{k} {v}" for k, v in sorted(DEFAULT_MAX_HOLD_BARS.items()))
    floors = ", ".join(f"{k} {v}" for k, v in sorted(TIME_STOP_FLOOR_BY_TF.items()))
    print(f"\nmax_hold_bars: {caps}\ntime-stop floors (winner bars->1R p90): {floors}")

    agree, n_base = baseline_agreement(resolve_ledger_under_policy(con, "fixed"))
    print(
        f"\nPositive control — replayed 'fixed' vs the RECORDED ledger label: "
        f"{agree}/{n_base} agree"
        + (f" ({agree / n_base:.1%})" if n_base else "")
        + "\n  (reported, not asserted: adverse-first ties and time-stop "
        "mark-to-market differ from the live resolver by design)"
    )
    print(f"Arms: {', '.join(POLICY_KINDS)}")

    rows = run_exit_ab(con, n_boot=n_boot)
    if not rows:
        print("\n(no policies evaluated)")
        return
    frame = pd.DataFrame(
        [
            {
                "policy": r.name,
                "n": r.n,
                "sharpe_R": r.sharpe,
                "avg_r": r.avg_r,
                "win%": r.win_rate,
                "expiry%": r.expiry_rate,
                "hold_bars": r.avg_hold_bars,
                "dsr": r.dsr,
                "d_sharpe": r.d_sharpe,
                "uplift_R": r.uplift,
                "ci_lo": r.uplift_ci.lo if r.uplift_ci else float("nan"),
                "ci_hi": r.uplift_ci.hi if r.uplift_ci else float("nan"),
                "n_paired": r.n_paired,
            }
            for r in rows
        ]
    )
    _print_df("Exit-policy A/B (R space; baseline = first row)", frame)
    print(
        "\nHeadline = per-trade R Sharpe (NOT annualized). The decisive leg is\n"
        "the PAIRED uplift CI: an arm beats the baseline only if [ci_lo, ci_hi]\n"
        "excludes 0. `dsr` is a stamp, not a leg — see audit.py's docstring."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="run the exit-policy A/B instead of the MFE/MAE diagnostic",
    )
    parser.add_argument(
        "--n-boot",
        type=int,
        default=10_000,
        help="bootstrap resamples for the paired uplift CI (--replay only)",
    )
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

    if args.replay:
        _run_replay(con, args.n_boot)
        return

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
