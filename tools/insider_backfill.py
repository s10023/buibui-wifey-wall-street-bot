"""One-shot EDGAR Form 4 ingestion for H-024 (routine vs opportunistic insiders).

Loops the research breadth universe, resolves each ticker to its CIK, walks that
company's Form 4 index (``filings.recent`` **plus** every older shard reaching
into the window), fetches each filing's raw ownership XML, and upserts the
non-derivative transactions into ``insider_transactions``.

⚠ **The window starts three years before the study.** The classifier needs a
trade in each of the three preceding years to label an insider at all, so a run
covering only the study window would leave every insider unclassifiable. The
default ``--since 2015-01-01`` buys classification from 2018, which is the
universe's price-data floor.

``build_rows`` is the pure, fixture-testable unit; the network lives in ``main``.
The run reports the phase-1 acceptance observable — the share of fetched filings
that parsed cleanly — and that number is the deliverable, not a return.

Run via ``make wifey-insider-backfill`` or
``PYTHONPATH=. poetry run python tools/insider_backfill.py [--limit N] [--db PATH]``.
Requires ``EDGAR_CONTACT_EMAIL`` (SEC 403s any other User-Agent shape).
"""

from __future__ import annotations

import argparse
from typing import Any

import duckdb
from dotenv import load_dotenv

from analytics.insider.form4 import (
    Form4Filing,
    ParseOutcome,
    iter_form4_filings,
    parse_form4,
    raw_document_name,
)
from analytics.store import DEFAULT_DB_PATH
from analytics.store.insider import upsert_insider_transactions
from analytics.store.schema import init_schema
from utils.config_validation import load_research_universe
from utils.edgar_client import (
    EdgarContactMissing,
    fetch_archive_document,
    fetch_company_tickers,
    fetch_submissions,
    fetch_submissions_shard,
    ticker_to_cik,
)


def build_rows(
    outcome: ParseOutcome,
    filing: Form4Filing,
    symbol: str,
    issuer_cik: str,
) -> list[dict[str, Any]]:
    """Flatten one parsed filing into ``insider_transactions`` rows."""
    return [
        {
            "symbol": symbol,
            "issuer_cik": issuer_cik,
            "owner_cik": t.owner_cik,
            "owner_name": t.owner_name,
            "is_officer": t.is_officer,
            "is_director": t.is_director,
            "transaction_date": t.transaction_date,
            "transaction_code": t.transaction_code,
            "shares": t.shares,
            "price_per_share": t.price_per_share,
            "acquired_disposed": t.acquired_disposed,
            "accession": filing.accession,
            "filing_date": filing.filing_date or None,
            "acceptance_ts": filing.acceptance or None,
            "seq": t.seq,
        }
        for t in outcome.transactions
    ]


def collect_filings(submissions: dict[str, Any], since: str) -> list[Form4Filing]:
    """Form 4 index entries from ``recent`` plus every shard reaching the window.

    A shard is fetched only when its ``filingTo`` is on/after ``since``; skipping
    the rest keeps a full-universe run from pulling decades of 1990s filings.
    """
    filings = iter_form4_filings(submissions, since=since)
    for shard in submissions.get("filings", {}).get("files", []):
        if str(shard.get("filingTo", "")) < since:
            continue
        name = shard.get("name")
        if not name:
            continue
        filings.extend(iter_form4_filings(fetch_submissions_shard(name), since=since))
    return filings


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # EDGAR_CONTACT_EMAIL may live only in .env
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path")
    parser.add_argument(
        "--since",
        default="2015-01-01",
        help="earliest filing date (default buys 3y of classification history)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="cap number of symbols (debug)"
    )
    args = parser.parse_args(argv)

    symbols = load_research_universe().stocks()
    if args.limit:
        symbols = symbols[: args.limit]

    print(f"📥 H-024 Form 4 backfill: {len(symbols)} symbols since {args.since}")
    try:
        tickers = fetch_company_tickers()
    except EdgarContactMissing as exc:
        print(f"❌ {exc}")
        return 2

    conn = duckdb.connect(args.db)
    init_schema(conn)

    n_missing_cik = n_errors = n_rows = 0
    n_filings = n_parsed_clean = n_empty = 0
    for i, sym in enumerate(symbols, 1):
        cik = ticker_to_cik(tickers, sym)
        if cik is None:
            n_missing_cik += 1
            continue
        try:
            filings = collect_filings(fetch_submissions(cik), args.since)
        except Exception as exc:  # noqa: BLE001 — warn-only, never abort the run
            print(f"  ⚠ {sym} (CIK {cik}) index: {type(exc).__name__}: {exc}")
            n_errors += 1
            continue

        rows: list[dict[str, Any]] = []
        for f in filings:
            n_filings += 1
            try:
                xml = fetch_archive_document(
                    cik, f.accession, raw_document_name(f.primary_document)
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠ {sym} {f.accession}: {type(exc).__name__}: {exc}")
                n_errors += 1
                continue
            outcome = parse_form4(xml)
            if outcome.failures:
                for reason in outcome.failures[:2]:
                    print(f"  ⚠ {sym} {f.accession}: {reason}")
            elif not outcome.transactions:
                n_empty += 1
                n_parsed_clean += 1
            else:
                n_parsed_clean += 1
            rows.extend(build_rows(outcome, f, sym, cik))

        if rows:
            upsert_insider_transactions(conn, rows)
            n_rows += len(rows)
        if i % 25 == 0:
            print(f"  …{i}/{len(symbols)} · {n_rows} rows · {n_filings} filings")

    conn.close()
    coverage = n_parsed_clean / n_filings if n_filings else 0.0
    print(
        f"✅ done: {n_rows} transaction rows from {n_filings} filings, "
        f"{n_missing_cik} missing CIK, {n_errors} errors"
    )
    # The phase-1 acceptance observable. Printed as its own line, with the floor
    # named, so the run reports whether it passed rather than leaving a reader to
    # remember what the number had to clear.
    print(
        f"📊 parse coverage {100 * coverage:.1f}% of fetched filings "
        f"({n_empty} carried no non-derivative transactions) — "
        f"phase-1 floor is 80%: {'PASS' if coverage >= 0.80 else 'FAIL'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
