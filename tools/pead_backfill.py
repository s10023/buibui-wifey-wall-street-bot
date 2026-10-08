"""One-shot EDGAR earnings ingestion for edge-hunt #4 (PEAD-lite).

Loops the research breadth universe, resolves each ticker to its CIK, pulls the
free EDGAR ``companyfacts`` (diluted EPS) + ``submissions`` (8-K item-2.02
announcement dates), matches each quarter to its announcement, and upserts into
the ``earnings_facts`` table. Run once; not part of the daily ``make go-live``.

The announcement anchor for each quarter is the earliest 8-K item-2.02 filing
strictly after the period-end and on/before the 10-Q/10-K filing date; when no
such 8-K is found the 10-Q ``filed`` date is the (later, conservative) fallback.
``build_rows`` is the pure, fixture-testable matching unit; the network lives in
``main``.

Run via ``make wifey-pead-backfill`` or
``PYTHONPATH=. poetry run python tools/pead_backfill.py [--limit N] [--db PATH]``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.store import DEFAULT_DB_PATH
from analytics.store.earnings import upsert_earnings_facts
from analytics.store.schema import init_schema
from utils.config_validation import load_research_universe
from utils.edgar_client import (
    EpsFact,
    fetch_company_facts,
    fetch_company_tickers,
    fetch_submissions,
    parse_announce_dates,
    parse_eps_facts,
    ticker_to_cik,
)


def build_rows(
    eps_facts: list[EpsFact],
    announce_dates: list[str],
    symbol: str,
    cik: str,
) -> list[dict[str, Any]]:
    """Match each EPS fact to its announcement date → ``earnings_facts`` rows.

    Announcement = the earliest 8-K item-2.02 in ``(period_end, filed]``; the
    10-Q/10-K ``filed`` date is the fallback (``source="10q"``) when none matches.
    """
    ann = sorted(date.fromisoformat(d) for d in announce_dates)
    rows: list[dict[str, Any]] = []
    for f in eps_facts:
        filed_d = date.fromisoformat(f.filed)
        candidates = [d for d in ann if f.period_end < d <= filed_d]
        if candidates:
            announce, source = candidates[0], "8k"
        else:
            announce, source = filed_d, "10q"
        rows.append(
            {
                "symbol": symbol,
                "cik": cik,
                "fy": f.fy,
                "fp": f.fp,
                "period_end": f.period_end.isoformat(),
                "eps_diluted": f.eps_diluted,
                "announce_date": announce.isoformat(),
                "filed_date": f.filed,
                "accn": f.accn,
                "source": source,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # EDGAR_CONTACT_EMAIL may live only in .env
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path")
    parser.add_argument(
        "--limit", type=int, default=None, help="cap number of symbols (debug)"
    )
    args = parser.parse_args(argv)

    symbols = load_research_universe().stocks()
    if args.limit:
        symbols = symbols[: args.limit]

    print(f"📥 PEAD backfill: {len(symbols)} symbols → {args.db}")
    tickers = fetch_company_tickers()
    conn = duckdb.connect(args.db)
    init_schema(conn)

    n_with_eps = n_missing_cik = n_errors = n_rows = n_8k = 0
    for i, sym in enumerate(symbols, 1):
        cik = ticker_to_cik(tickers, sym)
        if cik is None:
            n_missing_cik += 1
            continue
        try:
            facts = parse_eps_facts(fetch_company_facts(cik))
            ann = parse_announce_dates(fetch_submissions(cik))
        except Exception as exc:  # noqa: BLE001 — warn-only, never abort the run
            print(f"  ⚠ {sym} (CIK {cik}): {type(exc).__name__}: {exc}")
            n_errors += 1
            continue
        rows = build_rows(facts, ann, sym, cik)
        if rows:
            upsert_earnings_facts(conn, rows)
            n_with_eps += 1
            n_rows += len(rows)
            n_8k += sum(1 for r in rows if r["source"] == "8k")
        if i % 50 == 0:
            print(f"  …{i}/{len(symbols)}")

    conn.close()
    fallback = (n_rows - n_8k) / n_rows if n_rows else 0.0
    print(
        f"✅ done: {n_with_eps} names with EPS, {n_rows} quarters, "
        f"{n_missing_cik} missing CIK, {n_errors} errors, "
        f"8-K coverage {100 * (1 - fallback):.1f}% ({n_rows - n_8k} on 10-Q fallback)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
