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

⚠ **A long run is interrupt-safe only under ``--resume``.** A marker row is
written to ``insider_backfill_progress`` once a symbol finishes with zero
errors, and ``--resume`` skips exactly those, so an interrupted or
partially-failed symbol is retried in full rather than assumed done. Without it
a restart re-walks from symbol 1 (safe, via the upsert, but wasteful). The
measured cost of getting this wrong is not a crash: the per-filing handlers below
swallow network errors and continue, so an outage silently removes rows from a
run that still reports success.

``build_rows`` is the pure, fixture-testable unit; the network lives in ``main``.
The run reports the phase-1 acceptance observable — the share of fetched filings
that parsed cleanly — and that number is the deliverable, not a return.

Run via ``make wifey-insider-backfill`` or
``PYTHONPATH=. poetry run python tools/insider_backfill.py
[--stride N] [--limit N] [--max-filings-per-symbol N] [--symbols A,B]
[--db PATH] [--resume] [--max-consecutive-failures N]``.
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
from analytics.store.insider import (
    completed_symbols,
    mark_symbol_complete,
    upsert_insider_transactions,
)
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


def select_symbols(
    universe: list[str],
    *,
    symbols: str | None = None,
    stride: int = 1,
    limit: int | None = None,
) -> list[str]:
    """Pick this run's symbols from the research universe.

    ⚠ **``--limit`` alone takes the HEAD, and `config/universe.json` is grouped by
    SECTOR** — so ``--limit 15`` is fifteen Information Technology mega-caps rather
    than a sample of anything. Measured 2026-09-01: those fifteen carry **18,549**
    Form 4 documents, of which CRM (4,175) and ACN (2,922) are ~38% on their own, so
    the head is simultaneously the slowest slice in the universe and the least
    representative one.

    That matters beyond runtime. Parse coverage is phase 1's pre-registered
    acceptance observable, and malformed filings concentrate among small and older
    filers — none of which the head contains. A head-sampled coverage figure
    therefore cannot support the >=80% gate in either direction: it is not evidence
    the universe clears the floor, and a failure would not be evidence it does not.
    ``stride`` spreads the pick across the file's ordering, which is both cheaper
    and the only form that answers the question the gate asks.
    """
    if symbols:
        wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        unknown = [s for s in wanted if s not in set(universe)]
        if unknown:
            raise ValueError(
                f"not in the research universe: {', '.join(sorted(unknown))}"
            )
        return wanted
    if stride < 1:
        raise ValueError(f"--stride must be >= 1, got {stride}")
    picked = universe[::stride]
    return picked[:limit] if limit is not None else picked


def sample_filings(filings: list[Form4Filing], cap: int | None) -> list[Form4Filing]:
    """Evenly spaced subset of one symbol's filings, for a coverage sample.

    ⚠ **Taking the first ``cap`` would BIAS THE OBSERVABLE OPTIMISTICALLY.**
    ``collect_filings`` returns newest-first, and recent Form 4s are the most
    uniform — modern XML from current filing agents. Parse failures concentrate in
    older documents, so a head-capped sample measures the easy end of the range and
    reports a parse-coverage figure that is too high. The >=80% floor exists to
    catch exactly the documents such a cap would exclude, which makes the naive
    version worse than no cap at all.

    Spreading across the list keeps the sampled window the same width as the full
    one. This is the same head-vs-spread defect as :func:`select_symbols`, one level
    down: there it was sectors, here it is filing vintage.
    """
    if cap is None or cap >= len(filings) or not filings:
        return filings
    if cap < 1:
        raise ValueError(f"--max-filings-per-symbol must be >= 1, got {cap}")
    step = len(filings) / cap
    return [filings[int(i * step)] for i in range(cap)]


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
        "--limit",
        type=int,
        default=None,
        help="cap number of symbols; applied AFTER --stride. Alone it takes the "
        "head of a sector-grouped file — pair it with --stride for a sample",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=1,
        help="take every Nth universe member, spreading the pick across sectors",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="explicit comma-separated symbols; overrides --stride/--limit",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip symbols already completed with zero errors for this --since; "
        "an interrupted or partially-failed symbol is retried in full",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=5,
        help="abort after this many symbols fail end-to-end in a row — a "
        "sustained outage should stop the run loudly, not grind through the "
        "universe recording empty results",
    )
    parser.add_argument(
        "--max-filings-per-symbol",
        type=int,
        default=None,
        help="sample at most N filings per symbol, spread evenly across the "
        "window. For a coverage estimate, filer diversity beats depth",
    )
    args = parser.parse_args(argv)

    try:
        symbols = select_symbols(
            load_research_universe().stocks(),
            symbols=args.symbols,
            stride=args.stride,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"❌ {exc}")
        return 2

    print(f"📥 H-024 Form 4 backfill: {len(symbols)} symbols since {args.since}")
    try:
        tickers = fetch_company_tickers()
    except EdgarContactMissing as exc:
        print(f"❌ {exc}")
        return 2

    conn = duckdb.connect(args.db)
    init_schema(conn)

    n_skipped = 0
    if args.resume:
        done = completed_symbols(conn, args.since)
        before = len(symbols)
        symbols = [s for s in symbols if s not in done]
        n_skipped = before - len(symbols)
        print(
            f"⏭  resume: {n_skipped} symbol(s) already complete, {len(symbols)} to go"
        )
        if not symbols:
            conn.close()
            print("✅ nothing to do — every selected symbol is already complete")
            return 0

    n_missing_cik = n_errors = n_rows = 0
    n_filings = n_parsed_clean = n_empty = 0
    n_consecutive_failures = 0
    aborted = False
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
            n_consecutive_failures += 1
            if n_consecutive_failures >= args.max_consecutive_failures:
                aborted = True
                break
            continue

        filings = sample_filings(filings, args.max_filings_per_symbol)

        # Per-symbol FETCH errors only. Parse failures are deliberately excluded:
        # a code-M option exercise carries no price and reports a failure on
        # every run, so counting those here would deny the symbol a marker
        # forever and re-fetch it on each resume — resume that never resumes.
        sym_fetch_errors = 0
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
                sym_fetch_errors += 1
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
        # Marker AFTER the upsert, and only on a clean symbol. An interrupt in
        # the gap costs a harmless re-fetch; the reverse order would record a
        # symbol as done whose rows were never written.
        if sym_fetch_errors == 0:
            mark_symbol_complete(conn, sym, args.since, len(filings), len(rows))
            n_consecutive_failures = 0
        elif sym_fetch_errors == len(filings) and filings:
            n_consecutive_failures += 1
            if n_consecutive_failures >= args.max_consecutive_failures:
                aborted = True
                break
        else:
            n_consecutive_failures = 0
        if i % 25 == 0:
            print(f"  …{i}/{len(symbols)} · {n_rows} rows · {n_filings} filings")

    conn.close()
    if aborted:
        print(
            f"⛔ ABORTED after {n_consecutive_failures} consecutive symbol "
            f"failures — this is what a sustained outage looks like. Nothing "
            f"is lost: re-run with --resume once the network is back."
        )
        return 1
    coverage = n_parsed_clean / n_filings if n_filings else 0.0
    print(
        f"✅ done: {n_rows} transaction rows from {n_filings} filings, "
        f"{n_missing_cik} missing CIK, {n_errors} errors, {n_skipped} skipped"
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
