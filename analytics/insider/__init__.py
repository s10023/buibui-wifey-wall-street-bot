"""H-024 insider sleeve — Form 4 ingestion, classification and book.

Phase 1 (this module set) is fetch + parse + store only: it produces
``insider_transactions`` rows and a parse-coverage number, and computes no
return. Design and the frozen pre-registration:
``docs/superpowers/specs/2026-08-29-h024-insider-routine-opportunistic-design.md``.
"""

from analytics.insider.form4 import (
    Form4Filing,
    Form4Transaction,
    ParseOutcome,
    iter_form4_filings,
    parse_form4,
    raw_document_name,
)

__all__ = [
    "Form4Filing",
    "Form4Transaction",
    "ParseOutcome",
    "iter_form4_filings",
    "parse_form4",
    "raw_document_name",
]
