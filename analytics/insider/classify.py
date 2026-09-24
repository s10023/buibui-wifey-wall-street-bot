"""Routine vs opportunistic insider classification — pure, H-024 phase 2.

Implements the CMP rule frozen in
``docs/superpowers/specs/2026-08-29-h024-insider-routine-opportunistic-design.md``:

    At the start of each calendar year, an insider is *classifiable* if she made
    ≥1 open-market trade in each of the three preceding years; a classifiable
    insider is **routine** if some calendar month contains a trade of hers in
    each of ≥3 consecutive preceding years, else **opportunistic**.
    Unclassifiable insiders are excluded. Every subsequent trade that year
    inherits the trader's label.

Nothing here touches the DB or the network: it takes the frame
``analytics.store.get_insider_transactions`` returns and gives back labels, so
every branch is fixture-testable.

Three decisions the frozen text left implicit, resolved here and pinned by tests
rather than left to whoever writes the book:

1. **A trade carries its trade year's label, not its filing year's.** CMP labels
   by trade year, and keying on the filing year would be self-referential: a
   December Y-1 trade filed in January Y is part of the history that classifies
   year Y, so labelling it with the year-Y label would let a trade help decide
   its own label. Formation still picks the trade up when it is *filed* — the
   two clocks do different jobs and neither substitutes for the other.
2. **Tranching cannot move a label.** Both tests are existence tests ("≥1 trade
   in the year", "a trade in that month"), so one sale broken into five
   transaction rows on one day counts exactly like one. It does move a trade
   *count*, which is why :func:`cohort_shape` reports rows and trade-days
   separately instead of picking one and calling it "trades".
3. **Classification is recomputed, never stored.** The spec's "classification is
   derived state" — a second source of truth would drift from the transactions
   it summarises, and this is cheap.

The lookback and streak constants are the pre-registration, not tuning knobs:
changing either re-opens the trial count. They are parameters only so that the
tests can drive the general rule and so the observability divergence below can
be measured; production callers pass neither.

The frozen rule reads trade dates, which is very slightly optimistic at the
year boundary. A trade made in late December Y-1 is typically filed in
January Y (post-SOX the lag is ≤2 business days), so classifying year Y "at the
start of the year" from trade dates can use one filing an outsider could not yet
have read. ``require_filed_by_year_start`` re-runs the same rule against only
the filings actually public on 1 January; the divergence between the two is a
measurement, reported by ``tools/insider_cohort.py``, not a silent default
change. The frozen behaviour stays the default because deviating from a frozen
pre-registration on an unmeasured hunch is the larger error.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

#: Years of history a classifiable insider must have traded in. Frozen.
CLASSIFY_LOOKBACK_YEARS = 3

#: Consecutive same-month years that make an insider routine. Frozen.
ROUTINE_MIN_STREAK_YEARS = 3

#: The pre-registered study population: open-market purchases and sales.
OPEN_MARKET_CODES = ("P", "S")

ROUTINE = "routine"
OPPORTUNISTIC = "opportunistic"
UNCLASSIFIABLE = "unclassifiable"


def _open_market(txns: pd.DataFrame, codes: tuple[str, ...] | None) -> pd.DataFrame:
    """Restrict to the pre-registered codes when the column is present.

    The classifier is handed frames from more than one place — the store
    accessor already filters, a fixture may not — so it re-applies the filter
    rather than trusting its caller. Filtering twice is free; filtering never is
    a silent population change.
    """
    if codes is None or "transaction_code" not in txns.columns:
        return txns
    return txns[txns["transaction_code"].astype(str).isin(codes)]


def trade_calendar(
    txns: pd.DataFrame,
    filed_before: str | None = None,
) -> dict[str, set[tuple[int, int]]]:
    """Map each insider's CIK to the set of (year, month) she traded in.

    ``filed_before`` (an ISO date) keeps only filings public before that date,
    which is what makes the observability check above possible. A row with no
    ``filing_date`` is kept under that filter: the alternative silently deletes
    history whenever the column is absent, which would make the comparison read
    as a divergence caused by the data rather than by the rule.
    """
    out: dict[str, set[tuple[int, int]]] = {}
    if txns.empty:
        return out

    frame = txns
    if filed_before is not None and "filing_date" in frame.columns:
        filed = pd.to_datetime(frame["filing_date"], errors="coerce")
        cutoff = pd.Timestamp(filed_before)
        frame = frame[filed.isna() | (filed < cutoff)]
        if frame.empty:
            return out

    dates = pd.to_datetime(frame["transaction_date"], errors="coerce")
    for cik, stamp in zip(frame["owner_cik"].astype(str), dates, strict=True):
        if pd.isna(stamp):
            continue
        out.setdefault(cik, set()).add((int(stamp.year), int(stamp.month)))
    return out


def _has_consecutive_run(years: set[int], window: range, min_streak: int) -> bool:
    """True when ``years`` contains ``min_streak`` consecutive members of ``window``."""
    run = 0
    for year in window:
        run = run + 1 if year in years else 0
        if run >= min_streak:
            return True
    return False


def classify_owner_year(
    months: set[tuple[int, int]],
    year: int,
    lookback_years: int = CLASSIFY_LOOKBACK_YEARS,
    min_streak_years: int = ROUTINE_MIN_STREAK_YEARS,
) -> str:
    """Label one insider for one classification year from her trade calendar.

    ``months`` is that insider's whole history; only the ``lookback_years``
    preceding ``year`` are consulted, so passing future trades cannot leak.
    """
    window = range(year - lookback_years, year)
    traded_years = {y for (y, _m) in months if y in window}
    if len(traded_years) < lookback_years:
        return UNCLASSIFIABLE
    for month in range(1, 13):
        same_month_years = {y for (y, m) in months if m == month and y in window}
        if _has_consecutive_run(same_month_years, window, min_streak_years):
            return ROUTINE
    return OPPORTUNISTIC


def classify_insiders(
    txns: pd.DataFrame,
    years: Iterable[int] | None = None,
    *,
    codes: tuple[str, ...] | None = OPEN_MARKET_CODES,
    require_filed_by_year_start: bool = False,
    lookback_years: int = CLASSIFY_LOOKBACK_YEARS,
    min_streak_years: int = ROUTINE_MIN_STREAK_YEARS,
) -> pd.DataFrame:
    """Label every (insider, year) pair observable in ``txns``.

    ``years`` defaults to every year the frame carries a trade in. Returns
    columns ``owner_cik``, ``year``, ``label`` — including ``unclassifiable``
    rows, so what was excluded stays auditable from the output rather than
    having to be re-derived from an absence.
    """
    frame = _open_market(txns, codes)
    empty = pd.DataFrame({"owner_cik": [], "year": [], "label": []})
    if frame.empty:
        return empty.astype({"owner_cik": str, "year": int, "label": str})

    trade_years = pd.to_datetime(frame["transaction_date"], errors="coerce").dt.year
    if years is None:
        years = sorted({int(y) for y in trade_years.dropna().unique()})

    whole_history = trade_calendar(frame)
    records: list[dict[str, object]] = []
    for year in sorted({int(y) for y in years}):
        calendar = (
            trade_calendar(frame, filed_before=f"{year}-01-01")
            if require_filed_by_year_start
            else whole_history
        )
        for cik in whole_history:
            records.append(
                {
                    "owner_cik": cik,
                    "year": year,
                    "label": classify_owner_year(
                        calendar.get(cik, set()),
                        year,
                        lookback_years=lookback_years,
                        min_streak_years=min_streak_years,
                    ),
                }
            )
    if not records:
        return empty.astype({"owner_cik": str, "year": int, "label": str})
    return pd.DataFrame.from_records(records)


def label_transactions(
    txns: pd.DataFrame,
    *,
    codes: tuple[str, ...] | None = OPEN_MARKET_CODES,
    require_filed_by_year_start: bool = False,
    lookback_years: int = CLASSIFY_LOOKBACK_YEARS,
    min_streak_years: int = ROUTINE_MIN_STREAK_YEARS,
) -> pd.DataFrame:
    """Attach each trade's label, keyed on its trade year (see module docstring).

    Every input row in the study population comes back, ``unclassifiable``
    included; the book filters. Adds ``trade_year`` and ``label``.
    """
    frame = _open_market(txns, codes).copy()
    if frame.empty:
        frame["trade_year"] = pd.Series(dtype=int)
        frame["label"] = pd.Series(dtype=str)
        return frame

    frame["trade_year"] = pd.to_datetime(
        frame["transaction_date"], errors="coerce"
    ).dt.year
    labels = classify_insiders(
        frame,
        codes=None,  # already filtered above; filtering again is a no-op, not a bug
        require_filed_by_year_start=require_filed_by_year_start,
        lookback_years=lookback_years,
        min_streak_years=min_streak_years,
    )
    lookup = labels.set_index(["owner_cik", "year"])["label"].to_dict()
    frame["label"] = [
        UNCLASSIFIABLE
        if pd.isna(year)
        else str(lookup.get((str(cik), int(year)), UNCLASSIFIABLE))
        for cik, year in zip(
            frame["owner_cik"].astype(str), frame["trade_year"], strict=True
        )
    ]
    return frame


@dataclass(frozen=True)
class CohortShape:
    """The phase-2 deliverable: how the population splits, in three units.

    The three units disagree on purpose, and the WP's ~55% is a *trade* share.
    Rows are transaction rows (a tranched sale inflates them), trade-days are
    distinct (insider, date) pairs, and insiders are distinct CIKs — a split
    quoted without its unit is not comparable to anything.
    """

    rows: dict[str, int]
    trade_days: dict[str, int]
    insiders: dict[str, int]

    @property
    def classified_rows(self) -> int:
        return self.rows.get(ROUTINE, 0) + self.rows.get(OPPORTUNISTIC, 0)

    @property
    def routine_row_share(self) -> float:
        """Routine share of classified rows — the WP-comparable ~55% figure."""
        total = self.classified_rows
        return self.rows.get(ROUTINE, 0) / total if total else float("nan")


def cohort_shape(labelled: pd.DataFrame) -> CohortShape:
    """Count the labelled population in all three units at once."""
    buckets = (ROUTINE, OPPORTUNISTIC, UNCLASSIFIABLE)
    if labelled.empty:
        zero = dict.fromkeys(buckets, 0)
        return CohortShape(rows=dict(zero), trade_days=dict(zero), insiders=dict(zero))

    rows = {b: int((labelled["label"] == b).sum()) for b in buckets}
    trade_days = {
        b: int(
            labelled[labelled["label"] == b]
            .loc[:, ["owner_cik", "transaction_date"]]
            .drop_duplicates()
            .shape[0]
        )
        for b in buckets
    }
    insiders = {
        b: int(labelled.loc[labelled["label"] == b, "owner_cik"].nunique())
        for b in buckets
    }
    return CohortShape(rows=rows, trade_days=trade_days, insiders=insiders)
