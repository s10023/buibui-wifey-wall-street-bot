"""Free EDGAR earnings ingestion for edge-hunt #4 (PEAD-lite).

Pure fetch + parse over the SEC's public JSON APIs (no key; a descriptive
``User-Agent`` and a ≤10 req/s throttle per SEC fair-access rules). Mirrors
``utils/yfinance_client.py``: no module-level side effects, the network is
isolated to the ``fetch_*`` shims, and every parser is pure and fixture-tested.

The signal driver is the canonical academic surprise — a *seasonal-random-walk*
SUE built from reported diluted EPS — so **no paid analyst consensus is needed**.
``EarningsPerShareDiluted`` XBRL facts carry the value, the fiscal period, and the
filing date; the 8-K item-2.02 filing date supplies the announcement anchor.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# SEC fair-access requires a User-Agent that identifies the client and gives a
# reachable contact, so *some* address has to reach the wire. It must not reach
# SOURCE: this address was a literal here from PR #104 (2026-06-23), which was
# fine while the repo was private and became a public PII disclosure the moment
# it flipped on 2026-08-06. Contact comes from the environment — set
# EDGAR_CONTACT_EMAIL in `.env` (gitignored).
#
# ⚠ MEASURED 2026-08-29: a UA carrying a URL is REFUSED (HTTP 403) by both SEC
# hosts, with or without parentheses. The previous fallback here was the repo
# URL, documented as "SEC may throttle an address-less UA harder" — that was too
# kind by the time it was measured: unset meant a hard 403 on every endpoint, so
# `make wifey-pead-backfill` could not run at all on a box without the env var,
# and the failure rendered as a network error rather than as a missing setting.
# Probe matrix (data.sec.gov / www.sec.gov): name+email 200/200 · name only
# 200/403 · URL with parens 403/403 · URL without parens 403/403. So the contact
# must be email-shaped, and an unset contact now fails LOUD at the call site
# instead of buying a 403 three frames away.
_MIN_INTERVAL = 0.12  # ~8 req/s, comfortably under the SEC's 10 req/s ceiling
_last_call = 0.0

# Bounded retry for TRANSIENT failures only.
#
# ⚠ Without this, one dropped packet costs a filing PERMANENTLY: the backfill's
# callers catch per-filing exceptions and continue, so a network blip does not
# crash a run, it silently removes rows from it and still reports success. That
# is the repo's silent-surface class, and a long run on a laptop that changes
# networks meets it constantly.
#
# ⚠ Only transient shapes are retried. A 403 is the User-Agent contract and a
# 404 is a document that does not exist; retrying either burns the backoff
# budget three times over and buries a configuration error under what looks
# like flakiness. 429 and 5xx are the server asking to be asked again.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 4
_BACKOFF_BASE = 1.5  # seconds; 1.5 / 3.0 / 6.0 between the four attempts


def _is_transient(exc: Exception) -> bool:
    """True when retrying ``exc`` could plausibly succeed."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRY_STATUS
    # URLError covers DNS failure and connection reset; TimeoutError covers the
    # urlopen timeout. HTTPError subclasses URLError, so it is checked first.
    return isinstance(exc, urllib.error.URLError | TimeoutError)


def _open_with_retry(req: urllib.request.Request) -> bytes:
    """Fetch ``req`` with backoff on transient failures, honouring the throttle.

    The throttle clock is advanced before every attempt, retries included, so a
    retry storm can never breach the SEC's fair-access ceiling.
    """
    global _last_call
    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                data: bytes = r.read()
            _last_call = time.monotonic()
            return data
        except Exception as exc:  # noqa: BLE001 — re-raised below unless transient
            _last_call = time.monotonic()
            if not _is_transient(exc) or attempt == _MAX_ATTEMPTS - 1:
                raise
            last = exc
            time.sleep(_BACKOFF_BASE * (2**attempt))
    raise AssertionError(f"unreachable: {last}")  # pragma: no cover


_FP_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}


@dataclass(frozen=True)
class EpsFact:
    """One originally-filed quarterly diluted-EPS observation."""

    fy: int
    fp: str  # "Q1" | "Q2" | "Q3" | "Q4"
    period_start: date
    period_end: date
    eps_diluted: float
    filed: str  # ISO date the value was first filed (causal anchor for dedup)
    accn: str


def _iso(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


class EdgarContactMissing(RuntimeError):
    """``EDGAR_CONTACT_EMAIL`` is unset or not email-shaped.

    Its own class so a caller can distinguish "this box is not configured" from
    a genuine network or parse failure — the distinction the 403 destroyed.
    """


def _user_agent() -> str:
    """SEC fair-access User-Agent, with the contact read from the environment.

    Resolved per call rather than at import so the value is never frozen into a
    module-level constant that could be committed, and so tests can vary it.

    Raises :class:`EdgarContactMissing` when the contact is absent or carries no
    ``@`` — see the measured probe matrix above. Failing here costs one clear
    error; the alternative shipped a 403 that reads as SEC being down.
    """
    contact = os.environ.get("EDGAR_CONTACT_EMAIL", "").strip()
    if "@" not in contact:
        raise EdgarContactMissing(
            "EDGAR_CONTACT_EMAIL must be set to an email-shaped contact before "
            "any SEC fetch: SEC returns 403 for a User-Agent carrying a URL or "
            "no contact at all. Add EDGAR_CONTACT_EMAIL=<you@example.com> to "
            "`.env` (gitignored)."
        )
    return f"buibui-wifey research {contact}"


# ---- network shims (integration-only; never unit-tested) -----------------
def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    data: dict[str, Any] = json.loads(_open_with_retry(req))
    return data


def _get_bytes(url: str) -> bytes:
    """Raw-bytes sibling of :func:`_get_json`, sharing the one throttle clock.

    Form 4 primary documents are XML, not JSON, so they cannot go through
    ``_get_json``; they must still respect the same ≤10 req/s budget, which is
    why both siblings go through :func:`_open_with_retry` — the one place the
    module-level ``_last_call`` is advanced — rather than duplicating a clock.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    return _open_with_retry(req)


def fetch_company_tickers() -> dict[str, Any]:
    return _get_json("https://www.sec.gov/files/company_tickers.json")


def fetch_company_facts(cik: str) -> dict[str, Any]:
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")


def fetch_submissions(cik: str) -> dict[str, Any]:
    return _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")


def fetch_submissions_shard(name: str) -> dict[str, Any]:
    """One older-filings shard named by ``filings.files[].name``.

    ⚠ ``filings.recent`` is a WINDOW, not a history — SEC documents it as the
    most recent 1,000 filings, and that bound was confirmed on ONE company: for a
    heavy Form 4 filer it can start well inside the study window (measured for
    AAPL 2026-08-29: 1000 entries reaching back only to 2015-06-10, with one
    shard covering 1994→2015). A fetcher that reads ``recent`` alone silently
    returns a short history that looks exactly like a quiet insider.
    """
    return _get_json(f"https://data.sec.gov/submissions/{name}")


def fetch_archive_document(cik: str, accession: str, document: str) -> bytes:
    """Raw bytes of one document inside a filing's EDGAR Archives folder.

    ``cik`` may be zero-padded (the Archives path wants it unpadded), and
    ``accession`` may carry dashes (the path wants it without).
    """
    cik_nz = str(int(cik))
    accn_nd = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_nz}/{accn_nd}/{document}"
    return _get_bytes(url)


# ---- pure parsers (fixture-tested) ---------------------------------------
def ticker_to_cik(company_tickers: dict[str, Any], ticker: str) -> str | None:
    """Resolve a ticker to its zero-padded 10-digit CIK, or None if unlisted."""
    want = ticker.upper()
    for row in company_tickers.values():
        if str(row.get("ticker", "")).upper() == want:
            return f"{int(row['cik_str']):010d}"
    return None


def parse_eps_facts(company_facts: dict[str, Any]) -> list[EpsFact]:
    """Quarterly diluted EPS — originally-filed-only, Q4 derived from FY − ΣQ1..Q3.

    Causality / cleanliness rules:
      * keep only 10-Q / 10-K rows with both period dates and a filed date;
      * a *quarter* is a ~one-fiscal-quarter span (80-100 days) tagged Q1/Q2/Q3 —
        this filter discards the YTD (6-/9-month) values 10-Qs also report under
        the same ``fp``;
      * on a restatement (a ``(fy, fp)`` filed twice) keep the **earliest** filed
        value — a later refiling is look-ahead;
      * Q4 has no native quarterly XBRL fact, so derive it as
        ``FY − (Q1 + Q2 + Q3)`` only when all four are present (else no Q4 signal).
    """
    gaap = company_facts.get("facts", {}).get("us-gaap", {})
    units = gaap.get("EarningsPerShareDiluted", {}).get("units", {})
    raw: list[dict[str, Any]] = next(iter(units.values()), [])

    quarter: dict[tuple[int, str], EpsFact] = {}
    fy_fact: dict[int, EpsFact] = {}
    for f in raw:
        if f.get("form") not in ("10-Q", "10-K"):
            continue
        start, end, filed = f.get("start"), f.get("end"), f.get("filed")
        fy, fp = f.get("fy"), f.get("fp")
        if not (start and end and filed and fy is not None and fp):
            continue
        s, e = _iso(start), _iso(end)
        span = (e - s).days
        val = float(f["val"])
        accn = str(f.get("accn", ""))
        if 80 <= span <= 100 and fp in ("Q1", "Q2", "Q3"):
            key = (int(fy), fp)
            cur = quarter.get(key)
            if cur is None or filed < cur.filed:
                quarter[key] = EpsFact(int(fy), fp, s, e, val, filed, accn)
        elif span >= 350 and fp == "FY":
            cur = fy_fact.get(int(fy))
            if cur is None or filed < cur.filed:
                fy_fact[int(fy)] = EpsFact(int(fy), "FY", s, e, val, filed, accn)

    facts = list(quarter.values())
    for fy, fyf in fy_fact.items():
        q1 = quarter.get((fy, "Q1"))
        q2 = quarter.get((fy, "Q2"))
        q3 = quarter.get((fy, "Q3"))
        if q1 and q2 and q3:
            q4_val = fyf.eps_diluted - (
                q1.eps_diluted + q2.eps_diluted + q3.eps_diluted
            )
            facts.append(
                EpsFact(
                    fy, "Q4", q3.period_end, fyf.period_end, q4_val, fyf.filed, fyf.accn
                )
            )
    return sorted(facts, key=lambda f: (f.fy, _FP_ORDER[f.fp]))


def parse_announce_dates(submissions: dict[str, Any]) -> list[str]:
    """Sorted ISO filing dates of the 8-K item-2.02 (earnings-release) filings.

    Item 2.02 ("Results of Operations and Financial Condition") is the earnings
    press release — the announcement that anchors the post-earnings drift, days to
    weeks ahead of the later 10-Q/10-K filing.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    items = recent.get("items", [])
    dates = recent.get("filingDate", [])
    out: list[str] = []
    for form, item, d in zip(forms, items, dates, strict=False):
        if form == "8-K" and "2.02" in (item or ""):
            out.append(d)
    return sorted(out)
