"""H-024 phase 2 — the routine/opportunistic classifier.

Every rule test ships a positive control that MOVES: the repo's standing lesson
is that a "did not change" assertion is satisfied both by the invariant holding
and by the perturbation never arriving
(``docs/audits/2026-08-13-vacuous-causality-guards.md``). Here the control is
always a one-month or one-year edit to a fixture that is otherwise identical,
asserted to flip the label — so a classifier that ignored its input entirely
would fail, not pass.
"""

from __future__ import annotations

import pandas as pd

from analytics.insider.classify import (
    OPPORTUNISTIC,
    ROUTINE,
    UNCLASSIFIABLE,
    classify_insiders,
    classify_owner_year,
    cohort_shape,
    label_transactions,
    trade_calendar,
)


def _txns(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """Build a transaction frame from (cik, trade date, code) triples."""
    return pd.DataFrame(
        {
            "owner_cik": [r[0] for r in rows],
            "transaction_date": [r[1] for r in rows],
            "transaction_code": [r[2] for r in rows],
        }
    )


def _months(pairs: list[tuple[int, int]]) -> set[tuple[int, int]]:
    return set(pairs)


class TestClassifyOwnerYear:
    """CMP's rule itself, driven directly on a trade calendar."""

    def test_same_month_three_consecutive_years_is_routine(self) -> None:
        months = _months([(2015, 3), (2016, 3), (2017, 3)])
        assert classify_owner_year(months, 2018) == ROUTINE

    def test_control_breaking_the_streak_by_one_month_flips_to_opportunistic(
        self,
    ) -> None:
        """The positive control: same fixture, one month moved, label must move."""
        routine = _months([(2015, 3), (2016, 3), (2017, 3)])
        perturbed = _months([(2015, 3), (2016, 4), (2017, 3)])
        assert classify_owner_year(routine, 2018) == ROUTINE
        assert classify_owner_year(perturbed, 2018) == OPPORTUNISTIC

    def test_traded_every_year_but_no_common_month_is_opportunistic(self) -> None:
        months = _months([(2015, 1), (2016, 6), (2017, 11)])
        assert classify_owner_year(months, 2018) == OPPORTUNISTIC

    def test_a_missing_year_is_unclassifiable_not_opportunistic(self) -> None:
        """Absence of history is its own state — collapsing it would inflate the
        opportunistic arm with insiders the rule never examined."""
        months = _months([(2015, 3), (2017, 3)])
        assert classify_owner_year(months, 2018) == UNCLASSIFIABLE

    def test_control_adding_the_missing_year_makes_it_classifiable(self) -> None:
        sparse = _months([(2015, 3), (2017, 3)])
        filled = _months([(2015, 3), (2016, 3), (2017, 3)])
        assert classify_owner_year(sparse, 2018) == UNCLASSIFIABLE
        assert classify_owner_year(filled, 2018) == ROUTINE

    def test_two_consecutive_same_month_years_are_not_enough(self) -> None:
        """The streak bar is 3; 2 must not clear it."""
        months = _months([(2015, 5), (2016, 3), (2017, 3)])
        assert classify_owner_year(months, 2018) == OPPORTUNISTIC

    def test_future_trades_cannot_leak_into_a_label(self) -> None:
        """Causality: only the lookback window is consulted."""
        without_future = _months([(2015, 3), (2016, 4), (2017, 3)])
        with_future = without_future | {(2018, 3), (2019, 3), (2020, 3)}
        assert classify_owner_year(with_future, 2018) == OPPORTUNISTIC
        assert classify_owner_year(with_future, 2018) == classify_owner_year(
            without_future, 2018
        )

    def test_trades_older_than_the_window_do_not_rescue_a_streak(self) -> None:
        months = _months([(2011, 3), (2012, 3), (2013, 3), (2015, 1), (2016, 6)])
        assert classify_owner_year(months, 2018) == UNCLASSIFIABLE

    def test_a_later_classification_year_reads_a_later_window(self) -> None:
        """The same history gives different labels in different years, which is
        what 'at the start of each calendar year' means."""
        months = _months([(2015, 3), (2016, 3), (2017, 3), (2018, 9)])
        assert classify_owner_year(months, 2018) == ROUTINE
        assert classify_owner_year(months, 2019) == OPPORTUNISTIC


class TestTradeCalendar:
    def test_collects_year_month_pairs_per_insider(self) -> None:
        frame = _txns(
            [
                ("A", "2015-03-04", "P"),
                ("A", "2015-03-20", "S"),
                ("B", "2016-07-01", "P"),
            ]
        )
        cal = trade_calendar(frame)
        assert cal["A"] == {(2015, 3)}
        assert cal["B"] == {(2016, 7)}

    def test_filed_before_drops_filings_not_yet_public(self) -> None:
        frame = _txns([("A", "2017-12-28", "P")])
        frame["filing_date"] = ["2018-01-03"]
        assert trade_calendar(frame) == {"A": {(2017, 12)}}
        assert trade_calendar(frame, filed_before="2018-01-01") == {}

    def test_a_row_without_a_filing_date_survives_the_filter(self) -> None:
        """Otherwise the comparison measures the missing column, not the rule."""
        frame = _txns([("A", "2017-12-28", "P")])
        frame["filing_date"] = [None]
        assert trade_calendar(frame, filed_before="2018-01-01") == {"A": {(2017, 12)}}


class TestClassifyInsiders:
    def test_labels_every_insider_for_every_year_in_the_frame(self) -> None:
        frame = _txns(
            [
                ("A", "2015-03-02", "P"),
                ("A", "2016-03-02", "P"),
                ("A", "2017-03-02", "P"),
                ("A", "2018-03-02", "P"),
            ]
        )
        out = classify_insiders(frame)
        label = out.set_index(["owner_cik", "year"])["label"]
        assert label[("A", 2018)] == ROUTINE
        assert label[("A", 2015)] == UNCLASSIFIABLE

    def test_non_open_market_codes_are_excluded_from_the_population(self) -> None:
        """A gift (G) every March must not manufacture a routine trader."""
        gifts = _txns(
            [
                ("A", "2015-03-02", "G"),
                ("A", "2016-03-02", "G"),
                ("A", "2017-03-02", "G"),
            ]
        )
        assert classify_insiders(gifts).empty

    def test_control_the_same_dates_as_purchases_do_classify(self) -> None:
        purchases = _txns(
            [
                ("A", "2015-03-02", "P"),
                ("A", "2016-03-02", "P"),
                ("A", "2017-03-02", "P"),
            ]
        )
        out = classify_insiders(purchases, years=[2018])
        assert out.set_index(["owner_cik", "year"])["label"][("A", 2018)] == ROUTINE

    def test_unclassifiable_insiders_are_returned_not_dropped(self) -> None:
        frame = _txns([("A", "2017-03-02", "P")])
        out = classify_insiders(frame, years=[2018])
        assert list(out["label"]) == [UNCLASSIFIABLE]

    def test_empty_input_gives_an_empty_frame_with_the_right_columns(self) -> None:
        out = classify_insiders(_txns([]))
        assert out.empty
        assert list(out.columns) == ["owner_cik", "year", "label"]

    def test_require_filed_by_year_start_can_change_a_label(self) -> None:
        """The observability divergence is real and measurable, not theoretical."""
        rows = [
            ("A", "2015-03-02", "P"),
            ("A", "2016-03-02", "P"),
            ("A", "2017-03-28", "P"),
        ]
        frame = _txns(rows)
        frame["filing_date"] = ["2015-03-04", "2016-03-04", "2018-01-03"]
        frozen = classify_insiders(frame, years=[2018])
        observable = classify_insiders(
            frame, years=[2018], require_filed_by_year_start=True
        )
        assert frozen.set_index("owner_cik")["label"]["A"] == ROUTINE
        assert observable.set_index("owner_cik")["label"]["A"] == UNCLASSIFIABLE


class TestLabelTransactions:
    def test_every_trade_that_year_inherits_the_traders_label(self) -> None:
        """Inheritance is month-blind: a routine trader's off-month trade is still
        routine, which is the whole content of 'inherits'."""
        frame = _txns(
            [
                ("A", "2015-03-02", "P"),
                ("A", "2016-03-02", "P"),
                ("A", "2017-03-02", "P"),
                ("A", "2018-03-02", "P"),
                ("A", "2018-09-14", "S"),
            ]
        )
        out = label_transactions(frame)
        y2018 = out[out["trade_year"] == 2018]
        assert set(y2018["label"]) == {ROUTINE}

    def test_a_trade_carries_its_trade_years_label_not_its_filing_years(self) -> None:
        frame = _txns(
            [
                ("A", "2015-03-02", "P"),
                ("A", "2016-03-02", "P"),
                ("A", "2017-03-02", "P"),
                ("A", "2018-12-28", "P"),
            ]
        )
        frame["filing_date"] = ["2015-03-04", "2016-03-04", "2017-03-04", "2019-01-03"]
        out = label_transactions(frame)
        december = out[out["transaction_date"] == "2018-12-28"]
        assert list(december["trade_year"]) == [2018]
        assert list(december["label"]) == [ROUTINE]

    def test_tranching_does_not_move_a_label(self) -> None:
        single = _txns(
            [
                ("A", "2015-03-02", "P"),
                ("A", "2016-03-02", "P"),
                ("A", "2017-03-02", "P"),
                ("A", "2018-06-01", "S"),
            ]
        )
        tranched = _txns(
            [
                ("A", "2015-03-02", "P"),
                ("A", "2016-03-02", "P"),
                ("A", "2017-03-02", "P"),
                ("A", "2018-06-01", "S"),
                ("A", "2018-06-01", "S"),
                ("A", "2018-06-01", "S"),
            ]
        )
        one = label_transactions(single)
        many = label_transactions(tranched)
        assert set(one[one["trade_year"] == 2018]["label"]) == {ROUTINE}
        assert set(many[many["trade_year"] == 2018]["label"]) == {ROUTINE}
        # ...but it DOES move the row count, which is why cohort_shape reports both.
        assert (many["trade_year"] == 2018).sum() == 3
        assert (one["trade_year"] == 2018).sum() == 1


class TestCohortShape:
    def test_the_three_units_are_counted_separately(self) -> None:
        frame = _txns(
            [
                ("A", "2015-03-02", "P"),
                ("A", "2016-03-02", "P"),
                ("A", "2017-03-02", "P"),
                ("A", "2018-06-01", "S"),
                ("A", "2018-06-01", "S"),
                ("B", "2015-01-05", "P"),
                ("B", "2016-06-05", "P"),
                ("B", "2017-11-05", "P"),
                ("B", "2018-02-02", "P"),
            ]
        )
        shape = cohort_shape(label_transactions(frame))
        assert shape.rows[ROUTINE] == 2
        assert shape.trade_days[ROUTINE] == 1
        assert shape.insiders[ROUTINE] == 1
        assert shape.rows[OPPORTUNISTIC] == 1
        assert shape.insiders[OPPORTUNISTIC] == 1

    def test_routine_row_share_is_over_classified_rows_only(self) -> None:
        frame = _txns(
            [
                ("A", "2015-03-02", "P"),
                ("A", "2016-03-02", "P"),
                ("A", "2017-03-02", "P"),
                ("A", "2018-06-01", "S"),
                ("B", "2015-01-05", "P"),
                ("B", "2016-06-05", "P"),
                ("B", "2017-11-05", "P"),
                ("B", "2018-02-02", "P"),
            ]
        )
        shape = cohort_shape(label_transactions(frame))
        assert shape.classified_rows == 2
        assert shape.routine_row_share == 0.5

    def test_empty_input_is_zeroed_not_absent(self) -> None:
        shape = cohort_shape(label_transactions(_txns([])))
        assert shape.rows[ROUTINE] == 0
        assert shape.classified_rows == 0
