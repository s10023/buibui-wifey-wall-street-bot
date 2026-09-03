"""H-024 insider sleeve — Form 4 ingestion, classification and book.

Phase 1 is fetch + parse + store (``form4``); phase 2 adds the routine vs
opportunistic classifier (``classify``). Neither computes a return — the first
look at one happens inside phase 3's gated report and nowhere else. Design and
the frozen pre-registration:
``docs/superpowers/specs/2026-08-29-h024-insider-routine-opportunistic-design.md``.
"""

from analytics.insider.classify import (
    CLASSIFY_LOOKBACK_YEARS,
    OPEN_MARKET_CODES,
    OPPORTUNISTIC,
    ROUTINE,
    ROUTINE_MIN_STREAK_YEARS,
    UNCLASSIFIABLE,
    CohortShape,
    classify_insiders,
    classify_owner_year,
    cohort_shape,
    label_transactions,
    trade_calendar,
)
from analytics.insider.form4 import (
    Form4Filing,
    Form4Transaction,
    ParseOutcome,
    iter_form4_filings,
    parse_form4,
    raw_document_name,
)

__all__ = [
    "CLASSIFY_LOOKBACK_YEARS",
    "OPEN_MARKET_CODES",
    "OPPORTUNISTIC",
    "ROUTINE",
    "ROUTINE_MIN_STREAK_YEARS",
    "UNCLASSIFIABLE",
    "CohortShape",
    "classify_insiders",
    "classify_owner_year",
    "cohort_shape",
    "label_transactions",
    "trade_calendar",
    "Form4Filing",
    "Form4Transaction",
    "ParseOutcome",
    "iter_form4_filings",
    "parse_form4",
    "raw_document_name",
]
