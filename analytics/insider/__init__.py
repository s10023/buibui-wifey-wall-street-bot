"""H-024 insider sleeve — Form 4 ingestion, classification and book.

Phase 1 is fetch + parse + store (``form4``); phase 2 adds the routine vs
opportunistic classifier (``classify``); phase 3 adds the calendar-time book
(``book``), its read-only DB front door (``replay``) and the gated verdict
(``report``). Design and the frozen pre-registration:
``docs/superpowers/specs/2026-08-29-h024-insider-routine-opportunistic-design.md``.

Only ``replay`` touches the DB; ``book`` and ``report`` are pure so every
branch is fixture-testable, which is the shape every sleeve here follows.
"""

from analytics.insider.book import (
    BUY_CODE,
    DAYS_PER_YEAR,
    PLACEBOS,
    SELL_CODE,
    STUDY_START,
    TRIALS,
    InsiderInputs,
    book_weights,
    cohort_weights,
    daily_weights,
    filing_month,
    leg_weights,
    month_end_dates,
    trailing_adv,
    trailing_sigma,
)
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
from analytics.insider.replay import (
    MARKET_PROXY,
    insider_market_return,
    labelled_transactions,
    load_insider_inputs,
    replay_insider_trials,
    shared_base_cost_model,
)
from analytics.insider.report import (
    LONG_ONLY_KEYS,
    PRIMARY_KEY,
    InsiderReport,
    PairedDifference,
    annualized_mean_bps,
    evaluate_insider_trials,
    paired_difference,
)

__all__ = [
    "BUY_CODE",
    "CLASSIFY_LOOKBACK_YEARS",
    "DAYS_PER_YEAR",
    "InsiderInputs",
    "InsiderReport",
    "LONG_ONLY_KEYS",
    "MARKET_PROXY",
    "PLACEBOS",
    "PRIMARY_KEY",
    "PairedDifference",
    "SELL_CODE",
    "STUDY_START",
    "TRIALS",
    "annualized_mean_bps",
    "book_weights",
    "cohort_weights",
    "daily_weights",
    "evaluate_insider_trials",
    "filing_month",
    "insider_market_return",
    "labelled_transactions",
    "leg_weights",
    "load_insider_inputs",
    "month_end_dates",
    "paired_difference",
    "replay_insider_trials",
    "shared_base_cost_model",
    "trailing_adv",
    "trailing_sigma",
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
