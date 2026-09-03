"""H-024 phase 2 — report the routine/opportunistic cohort shape.

Reads ``insider_transactions``, applies the frozen CMP classifier and prints how
the population splits. **It computes no return**: the pre-registration puts the
first look at one inside phase 3's gated report, so this script deliberately has
no access to a price.

The deliverable is the split. The spec's benchmark is the working paper's ~55%
of *trades* from routine traders, and it is a smell test rather than a result —
"a wildly different split is a parser smell, not a finding". Three things make a
naive comparison to it wrong, so all three are printed:

* **The unit.** Rows, trade-days and insiders give different numbers from the
  same data; a tranched sale is one trade-day and several rows. The WP's figure
  is a trade share, so ``routine share of classified rows`` is the comparable
  line and the others are context.
* **The denominator.** Unclassifiable insiders are neither arm. Folding them
  into "opportunistic" would inflate that arm with insiders the rule never
  examined, so the share is over CLASSIFIED rows and the unclassifiable count
  is printed beside it rather than hidden in it.
* **The panel.** Our universe is 505 large caps from 2018; the WP is a 1986–2007
  all-filer panel. A split that differs for that reason is not a parser problem,
  which is why this prints the shape and stops rather than grading it.

Run via ``make wifey-insider-cohort`` or
``PYTHONPATH=. poetry run python tools/insider_cohort.py [--db PATH]
[--symbols A,B] [--since YEAR]``.
"""

from __future__ import annotations

import argparse

import pandas as pd

from analytics.db_retry import connect_with_retry
from analytics.insider.classify import (
    OPPORTUNISTIC,
    ROUTINE,
    UNCLASSIFIABLE,
    classify_insiders,
    cohort_shape,
    label_transactions,
)
from analytics.store import DEFAULT_DB_PATH, get_insider_transactions

#: Working-paper trade share from routine traders. A SMELL TEST, not a target.
WP_ROUTINE_TRADE_SHARE = 0.55


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:5.1f}%" if whole else "    —"


def report(txns: pd.DataFrame) -> int:
    """Print the cohort shape. Returns a process exit code."""
    print("H-024 phase 2 — routine vs opportunistic cohort shape")
    print("=" * 62)
    if txns.empty:
        print("\nNo open-market (P/S) insider transactions stored.")
        print("Run `make wifey-insider-backfill` first — phase 2 has nothing to read.")
        return 1

    symbols = txns["symbol"].nunique() if "symbol" in txns.columns else 0
    print(
        f"\nPopulation: {len(txns):,} P/S rows · {symbols} symbols · "
        f"{txns['owner_cik'].nunique():,} insiders"
    )
    dates = pd.to_datetime(txns["transaction_date"], errors="coerce")
    print(f"Trade dates: {dates.min().date()} → {dates.max().date()}")

    labelled = label_transactions(txns)
    shape = cohort_shape(labelled)

    print("\nSplit, in all three units (they disagree BY DESIGN):")
    print(f"{'':22}{'rows':>12}{'trade-days':>14}{'insiders':>12}")
    for bucket in (ROUTINE, OPPORTUNISTIC, UNCLASSIFIABLE):
        print(
            f"  {bucket:<20}{shape.rows[bucket]:>12,}"
            f"{shape.trade_days[bucket]:>14,}{shape.insiders[bucket]:>12,}"
        )

    classified = shape.classified_rows
    print(f"\nClassified rows: {classified:,} of {len(labelled):,}")
    if classified:
        share = shape.routine_row_share
        print(f"Routine share of classified rows : {share:6.1%}")
        print(f"Working-paper benchmark (trades) : {WP_ROUTINE_TRADE_SHARE:6.1%}")
        print(
            f"Difference                       : {share - WP_ROUTINE_TRADE_SHARE:+6.1%}"
        )
        print(
            "  ⚠ A smell test, not a gate. Different panel (505 large caps from\n"
            "    2018 vs an all-filer 1986–2007 panel), so a gap is not by itself\n"
            "    evidence of a parser fault — and agreement is not evidence of none."
        )
    else:
        print(
            "  ⚠ NOTHING is classifiable. Every insider needs a trade in each of the\n"
            "    three preceding years, so a backfill whose window does not reach\n"
            "    three years before its earliest trade classifies no one."
        )

    print("\nBy classification year (rows):")
    per_year = labelled.groupby(["trade_year", "label"]).size().unstack(fill_value=0)
    for bucket in (ROUTINE, OPPORTUNISTIC, UNCLASSIFIABLE):
        if bucket not in per_year.columns:
            per_year[bucket] = 0
    for rec in per_year.reset_index().to_dict("records"):
        routine, opp = int(rec[ROUTINE]), int(rec[OPPORTUNISTIC])
        unclassed = int(rec[UNCLASSIFIABLE])
        total = routine + opp + unclassed
        print(
            f"  {int(rec['trade_year'])}  routine {routine:>7,}  "
            f"opportunistic {opp:>7,}  unclassifiable {unclassed:>7,}   "
            f"({_pct(routine, total)} routine of all)"
        )

    _observability_divergence(txns)
    _code_mix(txns)
    print(
        "\nNo return has been computed. Phase 3 owns the book, the gates and the"
        "\nfirst look at a price."
    )
    return 0


def _observability_divergence(txns: pd.DataFrame) -> None:
    """How many labels the frozen trade-date rule owes to a not-yet-public filing.

    The frozen rule classifies from trade dates at the start of the year; a
    December trade filed in January is in that history without having been
    public. This re-runs the identical rule against only what was filed by
    1 January and counts the labels that move. It reports — it does not correct:
    deviating from a frozen pre-registration needs a measurement first, and this
    is that measurement.
    """
    if "filing_date" not in txns.columns:
        print("\nObservability divergence: no filing_date column, not measurable.")
        return
    frozen = classify_insiders(txns)
    observable = classify_insiders(txns, require_filed_by_year_start=True)
    merged = frozen.merge(
        observable, on=["owner_cik", "year"], suffixes=("_frozen", "_observable")
    )
    moved = merged[merged["label_frozen"] != merged["label_observable"]]
    total = len(merged)
    print(
        f"\nObservability divergence: {len(moved):,} of {total:,} "
        f"(insider, year) labels ({_pct(len(moved), total)}) rest on a filing"
        "\n  that was not yet public on 1 January."
    )
    if len(moved):
        print("  Frozen → observable:")
        transitions = (
            moved.groupby(["label_frozen", "label_observable"])
            .size()
            .reset_index(name="n")
        )
        for rec in transitions.to_dict("records"):
            was, now = str(rec["label_frozen"]), str(rec["label_observable"])
            print(f"    {was:<16} → {now:<16} {int(rec['n']):>6,}")


def _code_mix(txns: pd.DataFrame) -> None:
    """Stored transaction-code mix.

    ⚠ This is CONTEXT for the phase-1 shortfall, never a confirmation of it. The
    4.8% of filings that failed to parse are NOT in this table — a rejected row
    is not stored — so no query here can attribute them. The parser now records
    the code in its failure string, which makes the attribution readable off the
    NEXT backfill run; until that run happens the shortfall stays bounded and
    unattributed, exactly as Amendment 2 left it.
    """
    if "transaction_code" not in txns.columns:
        return
    counts = txns["transaction_code"].value_counts()
    print("\nStored code mix (context only — failures are not in this table):")
    for code, n in counts.items():
        print(f"  {code}  {n:>8,}  {_pct(int(n), len(txns))}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path")
    parser.add_argument(
        "--symbols", default=None, help="comma-separated symbols; default all stored"
    )
    parser.add_argument(
        "--since", type=int, default=None, help="drop trades before this calendar year"
    )
    args = parser.parse_args(argv)

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    )
    conn = connect_with_retry(args.db, read_only=True)
    try:
        txns = get_insider_transactions(conn, symbols=symbols)
    finally:
        conn.close()

    if args.since is not None and not txns.empty:
        years = pd.to_datetime(txns["transaction_date"], errors="coerce").dt.year
        txns = txns[years >= args.since]
    return report(txns)


if __name__ == "__main__":
    raise SystemExit(main())
