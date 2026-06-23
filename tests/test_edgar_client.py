"""Edge-hunt #4 (PEAD-lite): EDGAR client parser tests (fixture-only, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.edgar_client import (
    parse_announce_dates,
    parse_eps_facts,
    ticker_to_cik,
)

_FIX = Path(__file__).parent / "fixtures" / "edgar"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIX / name).read_text())


def test_ticker_to_cik_zero_pads_to_10() -> None:
    tickers = _load("company_tickers_trimmed.json")
    assert ticker_to_cik(tickers, "AAPL") == "0000320193"
    assert ticker_to_cik(tickers, "aapl") == "0000320193"  # case-insensitive
    assert ticker_to_cik(tickers, "NOPE") is None


def test_parse_eps_facts_returns_quarterly_only() -> None:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    # every returned fact is ~one fiscal quarter (Q4 is derived, no native span)
    for f in facts:
        span = (f.period_end - f.period_start).days
        assert 80 <= span <= 100 or f.fp == "Q4"
    # fiscal labels present and de-duplicated by (fy, fp) — YTD rows excluded
    keys = [(f.fy, f.fp) for f in facts]
    assert keys == [(2023, "Q1"), (2023, "Q2"), (2023, "Q3"), (2023, "Q4")]


def test_parse_eps_facts_keeps_earliest_filed_on_restatement() -> None:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    target = next(f for f in facts if (f.fy, f.fp) == (2023, "Q1"))
    assert target.filed == "2023-02-03"  # the original, not the 2023-05-05 amendment
    assert target.eps_diluted == 1.88


def test_parse_eps_facts_derives_q4_from_fy_minus_interims() -> None:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    q4 = next(f for f in facts if (f.fy, f.fp) == (2023, "Q4"))
    # FY 6.13 − (1.88 + 1.52 + 1.26) = 1.47
    assert abs(q4.eps_diluted - 1.47) < 1e-9


def test_parse_announce_dates_prefers_8k_item_202() -> None:
    subs = _load("aapl_submissions_trimmed.json")
    dates = parse_announce_dates(subs)
    assert dates == ["2023-02-02", "2023-05-04"]  # 8-K item-2.02 only, sorted
    assert "2023-01-15" not in dates  # the 5.02 8-K is excluded
