"""H-024 phase 3 — the verdict assembly: family, gate, placebos, β reads.

The books here are SYNTHETIC rather than replayed: this module tests what the
report does with a family, not whether the sleeve has an edge, and a fixture
whose returns are constructed is the only way to assert that a placebo cannot
reach the DSR family or that a pure-market book reads as beta.

⚠ **The evaluate stack is module-scoped on purpose.** ``evaluate_xs`` runs a
10,000-resample bootstrap per book and this repo's per-test budget is 30s;
building the report once keeps every assertion below free.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.forecast.config import ForecastConfig
from analytics.insider.book import PLACEBOS, TRIALS
from analytics.insider.report import (
    InsiderReport,
    annualized_mean_bps,
    evaluate_insider_trials,
    paired_difference,
)
from analytics.xsmom.book import XSBookResult

N_DAYS = 70


def _index() -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.bdate_range("2018-01-01", periods=N_DAYS))


def _book(returns: np.ndarray) -> XSBookResult:
    index = _index()
    r = np.asarray(returns, dtype=np.float64)
    return XSBookResult(
        daily_index=index,
        portfolio_return=r,
        pre_governor_return=r.copy(),
        governor=np.ones(len(index), dtype=np.float64),
        active_count=np.full(len(index), 3.0),
        per_instrument_net={"AAA": pd.Series(r, index=index)},
    )


def _market() -> np.ndarray:
    return np.asarray(
        np.random.default_rng(11).normal(0.0004, 0.01, N_DAYS), dtype=np.float64
    )


@pytest.fixture(scope="module")
def market_ret() -> pd.Series:
    return pd.Series(_market(), index=_index())


@pytest.fixture(scope="module")
def books() -> dict[str, XSBookResult]:
    """Four trials and four placebos, each built to exercise one report leg.

    ``T1`` carries a drift its placebo lacks (the reversal must NOT fire),
    ``T2`` is pure market beta (a long-only pass that is not signal) and ``T4``
    carries genuine alpha over the market (a long-only pass that is).
    """
    rng = np.random.default_rng(5)
    noise = rng.normal(0.0, 0.008, N_DAYS)
    market = _market()
    return {
        "T1": _book(noise + 0.0012),
        "T2": _book(market),  # perfectly explained by the market
        "T3": _book(rng.normal(0.0, 0.009, N_DAYS)),
        "T4": _book(0.5 * market + 0.0015 + rng.normal(0.0, 0.004, N_DAYS)),
        "P1": _book(noise),  # the same noise, without T1's drift
        "P2": _book(rng.normal(0.0, 0.008, N_DAYS)),
        "P3": _book(rng.normal(0.0, 0.009, N_DAYS)),
        "P4": _book(rng.normal(0.0, 0.007, N_DAYS)),
    }


@pytest.fixture(scope="module")
def report(books: dict[str, XSBookResult], market_ret: pd.Series) -> InsiderReport:
    return evaluate_insider_trials(books, ForecastConfig(), market_ret)


@pytest.fixture(scope="module")
def trials_only(books: dict[str, XSBookResult], market_ret: pd.Series) -> InsiderReport:
    """The same four trials with every control removed."""
    four = {k: v for k, v in books.items() if k in TRIALS}
    return evaluate_insider_trials(four, ForecastConfig(), market_ret)


@pytest.fixture(scope="module")
def three_trials(
    books: dict[str, XSBookResult], market_ret: pd.Series
) -> InsiderReport:
    """A genuinely smaller family — the control for the family-size test."""
    three = {k: v for k, v in books.items() if k in TRIALS and k != "T3"}
    return evaluate_insider_trials(three, ForecastConfig(), market_ret)


class TestStructure:
    def test_every_book_is_scored_and_the_primary_is_t1(
        self, report: InsiderReport
    ) -> None:
        assert set(report.cells) == set(TRIALS) | set(PLACEBOS)
        assert set(report.attribution) == set(report.cells)
        assert report.primary_key == "T1"
        assert isinstance(report.passed, bool)

    def test_every_trial_is_paired_with_its_own_placebo(
        self, report: InsiderReport
    ) -> None:
        assert set(report.paired) == set(TRIALS)
        assert report.paired["T1"].placebo == "P1"
        assert report.paired["T4"].placebo == "P4"

    def test_realized_beta_is_finite(self, report: InsiderReport) -> None:
        assert np.isfinite(report.realized_beta)


class TestDsrFamilyIsTheFourTrials:
    """A control is not a trial — the pre-registered family size is four."""

    def test_adding_the_placebos_does_not_move_a_trials_dsr(
        self, report: InsiderReport, trials_only: InsiderReport
    ) -> None:
        assert report.cells["T1"].dsr == trials_only.cells["T1"].dsr

    def test_control_dropping_a_real_trial_DOES_move_it(
        self, trials_only: InsiderReport, three_trials: InsiderReport
    ) -> None:
        """Paired with the test above: the family is read, not ignored."""
        assert trials_only.cells["T1"].dsr != three_trials.cells["T1"].dsr


class TestReversalObservable:
    """The spec's closing condition, read on the paired difference."""

    def test_identical_books_are_indistinguishable(self) -> None:
        r = np.random.default_rng(3).normal(0.0005, 0.01, N_DAYS)
        pair = paired_difference(
            _book(r), _book(r.copy()), trial_key="T1", placebo_key="P1"
        )
        assert pair.mean_diff_daily == 0.0
        assert pair.indistinguishable

    def test_control_a_real_drift_separates_them(self) -> None:
        """The same fixture plus a drift must flip the verdict, or the CI is inert."""
        r = np.random.default_rng(3).normal(0.0, 0.004, N_DAYS)
        pair = paired_difference(
            _book(r + 0.004), _book(r), trial_key="T1", placebo_key="P1"
        )
        assert pair.mean_diff_daily > 0.0
        assert not pair.indistinguishable

    def test_the_primary_trial_drives_the_report_flag(
        self, report: InsiderReport
    ) -> None:
        assert report.reversal_fires is report.paired["T1"].indistinguishable

    def test_two_never_funded_books_are_UNMEASURABLE_not_agreeing(self) -> None:
        """An empty panel and a powered null must not print the same verdict."""
        empty = np.zeros(N_DAYS, dtype=np.float64)
        pair = paired_difference(
            _book(empty), _book(empty.copy()), trial_key="T1", placebo_key="P1"
        )
        assert pair.measurable is False
        assert pair.indistinguishable is False

    def test_control_funding_one_side_makes_the_same_pair_measurable(self) -> None:
        """Paired with the test above: measurability reads the books, not the diff."""
        empty = np.zeros(N_DAYS, dtype=np.float64)
        funded = np.random.default_rng(9).normal(0.0, 0.01, N_DAYS)
        pair = paired_difference(
            _book(funded), _book(empty), trial_key="T1", placebo_key="P1"
        )
        assert pair.measurable is True


class TestLongOnlyCondition:
    """A long-only book is market exposure until its hedged alpha says otherwise."""

    def test_a_pure_market_book_reads_as_beta_not_edge(
        self, report: InsiderReport
    ) -> None:
        assert report.long_only_signal["T2"] is False

    def test_control_a_book_with_alpha_over_the_market_reads_as_signal(
        self, report: InsiderReport
    ) -> None:
        assert report.long_only_signal["T4"] is True

    def test_only_the_long_only_trials_carry_the_condition(
        self, report: InsiderReport
    ) -> None:
        assert set(report.long_only_signal) == {"T2", "T4"}


class TestUnits:
    """A daily mean and the WP's bps/month are not comparable by eye."""

    def test_a_daily_mean_converts_to_monthly_basis_points(self) -> None:
        assert annualized_mean_bps(0.001) == pytest.approx(210.0)

    def test_a_nan_stays_nan_rather_than_becoming_zero(self) -> None:
        assert np.isnan(annualized_mean_bps(float("nan")))
