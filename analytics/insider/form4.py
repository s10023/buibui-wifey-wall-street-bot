"""Form 4 (statement of changes in beneficial ownership) parsing — pure.

Every function here is fixture-testable and touches no network. The XML shape
was validated against a real filing (Apple Inc., accession 0001140361-26-034741,
fetched 2026-08-29); the committed fixtures are synthetic reconstructions of that
shape, because a real Form 4 names a private individual and a test fixture is a
poor place to keep one.

Three parse hazards are handled explicitly rather than discovered later, because
this repo's standing lesson is that ingest parsing fails *silently*:

1. **Values are wrapped.** Every transaction field is
   ``<transactionShares><value>N</value></transactionShares>``. A filer may emit
   ``<footnoteId>`` in place of ``<value>`` — a price "as described in footnote
   3" — which yields a field that exists but carries no number.
2. **A filing can report for several owners.** A joint Form 4 lists multiple
   ``<reportingOwner>`` blocks against ONE set of transactions. The study's unit
   is the *insider*, so one row is emitted per (owner × transaction); the
   alternative — attributing to the first owner — would silently drop the other
   insiders' trading histories, which is the input the classifier keys on.
3. **Derivative transactions are a different instrument.** Only
   ``nonDerivativeTable`` is read; option grants and exercises live in
   ``derivativeTable`` and are out of scope by pre-registration.

A transaction that cannot be parsed is RETURNED as a failure, never dropped: the
phase-1 acceptance observable is a parse *rate*, and a parser that silently skips
what it cannot read reports 100% coverage by construction.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

# Open-market purchase / sale. The pre-registration studies these two only; the
# parser stores every non-derivative code so the filter stays visible downstream
# rather than being baked into ingestion where nothing can audit it.
OPEN_MARKET_CODES = ("P", "S")


@dataclass(frozen=True)
class Form4Filing:
    """One Form 4 filing's index entry, before its document is fetched."""

    accession: str
    primary_document: str
    filing_date: str  # ISO date the filing became public
    acceptance: str  # ISO timestamp; the outsider-observable clock


@dataclass(frozen=True)
class Form4Transaction:
    """One (reporting owner × non-derivative transaction) pair."""

    owner_cik: str
    owner_name: str
    is_officer: bool
    is_director: bool
    transaction_date: str
    transaction_code: str
    shares: float
    price_per_share: float
    acquired_disposed: str
    seq: int


@dataclass(frozen=True)
class ParseOutcome:
    """Parsed transactions plus the reasons any were rejected.

    ``failures`` is load-bearing: it is the denominator side of the phase-1
    coverage observable, so a filing that parses to zero usable rows is
    distinguishable from one that was never read.
    """

    transactions: list[Form4Transaction]
    failures: list[str]

    @property
    def ok(self) -> bool:
        return bool(self.transactions) and not self.failures


def raw_document_name(primary_document: str) -> str:
    """Strip EDGAR's XSL-renderer prefix to reach the machine-readable XML.

    ``filings.recent.primaryDocument`` for a Form 4 is ``xslF345X06/form4.xml``,
    which serves *rendered HTML*. The raw XML sits at the same accession under
    the bare filename. Fetching the prefixed path yields a document that parses
    as XML in some viewers and carries none of the ownership elements.
    """
    return primary_document.rsplit("/", 1)[-1]


def _value_of(parent: ET.Element | None, tag: str) -> str | None:
    """Text of ``<tag><value>…</value></tag>``, or None when it is footnote-only."""
    if parent is None:
        return None
    node = parent.find(tag)
    if node is None:
        return None
    value = node.find("value")
    if value is None or value.text is None:
        return None
    text = value.text.strip()
    return text or None


def _flag(node: ET.Element | None, tag: str) -> bool:
    """Read a Form 4 boolean, which filer agents emit as 1/0 or true/false."""
    if node is None:
        return False
    el = node.find(tag)
    if el is None or el.text is None:
        return False
    return el.text.strip().lower() in ("1", "true")


def iter_form4_filings(
    submissions: dict[str, Any], since: str | None = None
) -> list[Form4Filing]:
    """Form 4 index entries from one ``filings.recent`` or shard payload.

    Accepts either the full submissions document or a bare shard (which has the
    column arrays at top level). ``since`` filters on filing date, inclusive.

    ⚠ This reads ONE payload. ``filings.recent`` caps at 1000 entries, so a
    caller covering a multi-year window must also walk ``filings.files`` — see
    :func:`utils.edgar_client.fetch_submissions_shard`.
    """
    block = submissions.get("filings", {}).get("recent", submissions)
    forms = block.get("form", [])
    accns = block.get("accessionNumber", [])
    docs = block.get("primaryDocument", [])
    dates = block.get("filingDate", [])
    accepted = block.get("acceptanceDateTime", [])

    out: list[Form4Filing] = []
    for i, form in enumerate(forms):
        if form != "4":
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if since and filing_date < since:
            continue
        out.append(
            Form4Filing(
                accession=accns[i] if i < len(accns) else "",
                primary_document=docs[i] if i < len(docs) else "",
                filing_date=filing_date,
                acceptance=accepted[i] if i < len(accepted) else "",
            )
        )
    return out


def parse_form4(xml_bytes: bytes) -> ParseOutcome:
    """Parse one Form 4 document into (owner × non-derivative transaction) rows.

    Returns rejected rows in ``failures`` rather than dropping them, so the
    caller can measure a real coverage rate.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return ParseOutcome([], [f"xml: {exc}"])

    owners: list[tuple[str, str, bool, bool]] = []
    for owner in root.findall("reportingOwner"):
        ident = owner.find("reportingOwnerId")
        cik = ident.findtext("rptOwnerCik", "").strip() if ident is not None else ""
        name = ident.findtext("rptOwnerName", "").strip() if ident is not None else ""
        rel = owner.find("reportingOwnerRelationship")
        if not cik:
            continue
        owners.append((cik, name, _flag(rel, "isOfficer"), _flag(rel, "isDirector")))

    if not owners:
        return ParseOutcome([], ["no reporting owner with a CIK"])

    table = root.find("nonDerivativeTable")
    if table is None:
        # Legitimate and common: a Form 4 reporting only option activity. Not a
        # parse failure — there is simply nothing in scope to read.
        return ParseOutcome([], [])

    rows: list[Form4Transaction] = []
    failures: list[str] = []
    for seq, txn in enumerate(table.findall("nonDerivativeTransaction")):
        coding = txn.find("transactionCoding")
        amounts = txn.find("transactionAmounts")
        date = _value_of(txn, "transactionDate")
        code = (
            coding.findtext("transactionCode", "").strip() if coding is not None else ""
        )
        shares = _value_of(amounts, "transactionShares")
        price = _value_of(amounts, "transactionPricePerShare")
        adcode = _value_of(amounts, "transactionAcquiredDisposedCode") or ""

        missing = [
            label
            for label, got in (
                ("date", date),
                ("code", code or None),
                ("shares", shares),
                ("price", price),
            )
            if got is None
        ]
        if missing:
            failures.append(f"txn {seq}: missing {'/'.join(missing)}")
            continue
        try:
            shares_f = float(str(shares).replace(",", ""))
            price_f = float(str(price).replace(",", ""))
        except ValueError:
            failures.append(f"txn {seq}: non-numeric shares/price")
            continue

        for cik, name, is_off, is_dir in owners:
            rows.append(
                Form4Transaction(
                    owner_cik=cik,
                    owner_name=name,
                    is_officer=is_off,
                    is_director=is_dir,
                    transaction_date=str(date),
                    transaction_code=code,
                    shares=shares_f,
                    price_per_share=price_f,
                    acquired_disposed=adcode,
                    seq=seq,
                )
            )
    return ParseOutcome(rows, failures)
