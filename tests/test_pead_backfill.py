"""Edge-hunt #4 (PEAD-lite): backfill announcement-matching unit (fixture-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.pead_backfill import build_rows
from utils.edgar_client import parse_announce_dates, parse_eps_facts

_FIX = Path(__file__).parent / "fixtures" / "edgar"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_FIX / name).read_text())
    return data


def _rows_by_period() -> dict[tuple[int, str], dict[str, Any]]:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    ann = parse_announce_dates(_load("aapl_submissions_trimmed.json"))
    rows = build_rows(facts, ann, "AAPL", "0000320193")
    return {(r["fy"], r["fp"]): r for r in rows}


def test_quarter_with_matching_8k_uses_announcement_date() -> None:
    by = _rows_by_period()
    q1 = by[(2023, "Q1")]
    assert q1["source"] == "8k"
    assert q1["announce_date"] == "2023-02-02"  # the 8-K item-2.02, before the 10-Q
    assert q1["filed_date"] == "2023-02-03"


def test_quarter_without_8k_falls_back_to_filed_date() -> None:
    by = _rows_by_period()
    q3 = by[(2023, "Q3")]
    assert q3["source"] == "10q"
    assert q3["announce_date"] == q3["filed_date"] == "2023-08-04"


def test_rows_carry_symbol_cik_and_all_quarters() -> None:
    by = _rows_by_period()
    assert set(by) == {(2023, "Q1"), (2023, "Q2"), (2023, "Q3"), (2023, "Q4")}
    for r in by.values():
        assert r["symbol"] == "AAPL"
        assert r["cik"] == "0000320193"
