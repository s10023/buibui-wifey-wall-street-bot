"""H-024 phase 3 — the calendar-time insider book.

Every rule test ships a positive control that MOVES, per
``docs/audits/2026-08-13-vacuous-causality-guards.md``: a "did not change"
assertion is satisfied both by the invariant holding and by the perturbation
never arriving, so each invariant here is paired with an edit that must change
the number the invariant pins.

**The causality control observes the channel it protects.** It asserts on the
BOOK's return on the formation day, not on an upstream weight — the vacuity
audit's finding was that a control on a neighbouring value can fire through a
different path and certify nothing about the guard it was written for.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.backtest.cost_model import CostModel
from analytics.insider.book import (
    book_weights,
    cohort_weights,
    daily_weights,
    filing_month,
    leg_weights,
    month_end_dates,
    trailing_adv,
)
from analytics.insider.classify import OPPORTUNISTIC, ROUTINE

SYMBOLS = ["AAA", "BBB"]


def _index(periods: int = 120, start: str = "2018-01-01") -> pd.DatetimeIndex:
    """A business-day tape — the same shape ``load_insider_inputs`` returns."""
    return pd.DatetimeIndex(pd.bdate_range(start, periods=periods))


def _panel(
    index: pd.DatetimeIndex, values: dict[str, float], columns: list[str] = SYMBOLS
) -> pd.DataFrame:
    return pd.DataFrame(
        {col: float(values.get(col, 1.0)) for col in columns}, index=index
    )


def _txns(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    """Rows of (symbol, filing date, code, label)."""
    return pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "filing_date": [r[1] for r in rows],
            "transaction_code": [r[2] for r in rows],
            "label": [r[3] for r in rows],
        }
    )


class TestMonthEndDates:
    """Formation lands on a trading day the tape actually has."""

    def test_picks_the_last_trading_day_of_each_month(self) -> None:
        index = _index(periods=60)
        ends = month_end_dates(index)
        jan = ends[pd.Period("2018-01", freq="M")]
        assert jan == pd.Timestamp("2018-01-31")

    def test_control_removing_that_day_moves_formation_back_a_session(self) -> None:
        """A calendar month-end the tape lacks must not index nothing."""
        index = _index(periods=60)
        trimmed = index[index != pd.Timestamp("2018-01-31")]
        assert month_end_dates(trimmed)[pd.Period("2018-01", freq="M")] == pd.Timestamp(
            "2018-01-30"
        )

    def test_empty_index_has_no_months(self) -> None:
        assert month_end_dates(pd.DatetimeIndex([])) == {}


class TestFilingMonth:
    """The public clock: filing date, else acceptance, else nothing."""

    def test_uses_the_filing_date(self) -> None:
        frame = _txns([("AAA", "2018-02-14", "P", OPPORTUNISTIC)])
        assert filing_month(frame).iloc[0] == pd.Period("2018-02", freq="M")

    def test_falls_back_to_acceptance_when_the_filing_date_is_missing(self) -> None:
        frame = _txns([("AAA", "2018-02-14", "P", OPPORTUNISTIC)])
        frame["filing_date"] = [None]
        frame["acceptance_ts"] = ["2018-03-02T18:30:00"]
        assert filing_month(frame).iloc[0] == pd.Period("2018-03", freq="M")

    def test_a_row_with_neither_clock_is_dropped_not_dated_from_its_trade(self) -> None:
        frame = _txns([("AAA", "2018-02-14", "P", OPPORTUNISTIC)])
        frame["filing_date"] = [None]
        frame["transaction_date"] = ["2018-02-01"]
        assert pd.isna(filing_month(frame).iloc[0])


class TestCohortWeights:
    """Value weights, on the ADV proxy Amendment 4 rules in."""

    def _adv(self, index: pd.DatetimeIndex, aaa: float, bbb: float) -> pd.DataFrame:
        return _panel(index, {"AAA": aaa, "BBB": bbb})

    def test_weights_are_proportional_to_adv_and_sum_to_one(self) -> None:
        index = _index()
        cohorts = cohort_weights(
            _txns(
                [
                    ("AAA", "2018-01-10", "P", OPPORTUNISTIC),
                    ("BBB", "2018-01-11", "P", OPPORTUNISTIC),
                ]
            ),
            self._adv(index, 3e8, 1e8),
            label=OPPORTUNISTIC,
            code="P",
        )
        weights = cohorts[pd.Period("2018-01", freq="M")]
        assert weights.sum() == 1.0
        assert weights["AAA"] == 0.75
        assert weights["BBB"] == 0.25

    def test_control_changing_one_names_adv_moves_the_weights(self) -> None:
        index = _index()
        rows = _txns(
            [
                ("AAA", "2018-01-10", "P", OPPORTUNISTIC),
                ("BBB", "2018-01-11", "P", OPPORTUNISTIC),
            ]
        )
        base = cohort_weights(
            rows, self._adv(index, 3e8, 1e8), label=OPPORTUNISTIC, code="P"
        )[pd.Period("2018-01", freq="M")]
        moved = cohort_weights(
            rows, self._adv(index, 1e8, 1e8), label=OPPORTUNISTIC, code="P"
        )[pd.Period("2018-01", freq="M")]
        assert base["AAA"] == 0.75
        assert moved["AAA"] == 0.5

    def test_a_name_with_no_adv_at_formation_is_dropped_not_floored(self) -> None:
        index = _index()
        adv = self._adv(index, 3e8, 1e8)
        adv["BBB"] = np.nan
        weights = cohort_weights(
            _txns(
                [
                    ("AAA", "2018-01-10", "P", OPPORTUNISTIC),
                    ("BBB", "2018-01-11", "P", OPPORTUNISTIC),
                ]
            ),
            adv,
            label=OPPORTUNISTIC,
            code="P",
        )[pd.Period("2018-01", freq="M")]
        assert list(weights.index) == ["AAA"]
        assert weights.sum() == 1.0

    def test_the_other_arm_and_the_other_side_are_excluded(self) -> None:
        index = _index()
        rows = _txns(
            [
                ("AAA", "2018-01-10", "P", OPPORTUNISTIC),
                ("BBB", "2018-01-11", "P", ROUTINE),
                ("BBB", "2018-01-12", "S", OPPORTUNISTIC),
            ]
        )
        adv = self._adv(index, 1e8, 1e8)
        buys = cohort_weights(rows, adv, label=OPPORTUNISTIC, code="P")
        assert list(buys[pd.Period("2018-01", freq="M")].index) == ["AAA"]
        sells = cohort_weights(rows, adv, label=OPPORTUNISTIC, code="S")
        assert list(sells[pd.Period("2018-01", freq="M")].index) == ["BBB"]
        routine = cohort_weights(rows, adv, label=ROUTINE, code="P")
        assert list(routine[pd.Period("2018-01", freq="M")].index) == ["BBB"]


class TestDailyWeights:
    """Cohorts expand causally, and a 3-month hold runs as overlapping thirds."""

    def _cohort(self, month: str, name: str = "AAA") -> dict[pd.Period, pd.Series]:
        return {pd.Period(month, freq="M"): pd.Series({name: 1.0})}

    def test_the_cohort_is_unfunded_on_its_formation_day(self) -> None:
        index = _index()
        weights = daily_weights(self._cohort("2018-01"), index, SYMBOLS, hold_months=1)
        assert weights.loc[pd.Timestamp("2018-01-31"), "AAA"] == 0.0
        assert weights.loc[pd.Timestamp("2018-02-01"), "AAA"] == 1.0

    def test_control_moving_the_cohort_a_month_moves_the_first_funded_day(self) -> None:
        index = _index()
        january = daily_weights(self._cohort("2018-01"), index, SYMBOLS, hold_months=1)
        february = daily_weights(self._cohort("2018-02"), index, SYMBOLS, hold_months=1)
        assert january.loc[pd.Timestamp("2018-02-01"), "AAA"] == 1.0
        assert february.loc[pd.Timestamp("2018-02-01"), "AAA"] == 0.0
        assert february.loc[pd.Timestamp("2018-03-01"), "AAA"] == 1.0

    def test_a_one_month_hold_is_replaced_by_the_next_cohort(self) -> None:
        index = _index()
        weights = daily_weights(self._cohort("2018-01"), index, SYMBOLS, hold_months=1)
        assert weights.loc[pd.Timestamp("2018-02-28"), "AAA"] == 1.0
        assert weights.loc[pd.Timestamp("2018-03-01"), "AAA"] == 0.0

    def test_three_month_holds_run_as_overlapping_thirds(self) -> None:
        index = _index()
        cohorts = {
            pd.Period(m, freq="M"): pd.Series({"AAA": 1.0})
            for m in ("2018-01", "2018-02", "2018-03")
        }
        weights = daily_weights(cohorts, index, SYMBOLS, hold_months=3)
        # Ramp: one sleeve funded, then two, then all three.
        assert weights.loc[pd.Timestamp("2018-02-01"), "AAA"] == 1.0 / 3.0
        assert weights.loc[pd.Timestamp("2018-03-01"), "AAA"] == 2.0 / 3.0
        assert weights.loc[pd.Timestamp("2018-04-02"), "AAA"] == 1.0

    def test_control_the_ramp_is_honest_rather_than_renormalised(self) -> None:
        """A single cohort under a 3-month hold funds a THIRD, never a whole leg."""
        index = _index()
        weights = daily_weights(self._cohort("2018-01"), index, SYMBOLS, hold_months=3)
        assert weights.loc[pd.Timestamp("2018-02-01"), "AAA"] == 1.0 / 3.0


class TestLegWeights:
    """Long-only is a funded leg; long-short is dollar-neutral."""

    def test_long_only_sums_to_one_and_long_short_sums_to_zero(self) -> None:
        index = _index()
        adv = _panel(index, {"AAA": 1e8, "BBB": 1e8})
        rows = _txns(
            [
                ("AAA", "2018-01-10", "P", OPPORTUNISTIC),
                ("BBB", "2018-01-11", "S", OPPORTUNISTIC),
            ]
        )
        day = pd.Timestamp("2018-02-01")
        long_only = leg_weights(
            rows,
            adv,
            index,
            SYMBOLS,
            label=OPPORTUNISTIC,
            long_only=True,
            hold_months=1,
        )
        assert long_only.loc[day].sum() == 1.0
        ls = leg_weights(
            rows,
            adv,
            index,
            SYMBOLS,
            label=OPPORTUNISTIC,
            long_only=False,
            hold_months=1,
        )
        assert ls.loc[day].sum() == 0.0
        assert ls.loc[day, "AAA"] == 1.0
        assert ls.loc[day, "BBB"] == -1.0


class TestBookWeights:
    """P&L, costs and the lookahead guard."""

    def _inputs(
        self, index: pd.DatetimeIndex, jump_day: str, jump: float = 1.10
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        closes = _panel(index, {"AAA": 100.0, "BBB": 100.0})
        closes.loc[closes.index >= pd.Timestamp(jump_day), "AAA"] = 100.0 * jump
        adv = _panel(index, {"AAA": 1e9, "BBB": 1e9})
        sigma = _panel(index, {"AAA": 0.0, "BBB": 0.0})
        return closes, adv, sigma

    def _weights(self, index: pd.DatetimeIndex) -> pd.DataFrame:
        return daily_weights(
            {pd.Period("2018-01", freq="M"): pd.Series({"AAA": 1.0})},
            index,
            SYMBOLS,
            hold_months=1,
        )

    def test_a_return_on_the_formation_day_is_not_earned(self) -> None:
        """The lookahead guard, asserted on the BOOK rather than on a weight."""
        index = _index()
        closes, adv, sigma = self._inputs(index, "2018-01-31")
        book = book_weights(
            self._weights(index),
            closes,
            adv,
            sigma,
            CostModel(
                half_spread_bps=(0.0,) * 4, impact_coef=0.0, borrow_rate_annual=0.0
            ),
        )
        returns = pd.Series(book.portfolio_return, index=book.daily_index)
        assert returns.loc[pd.Timestamp("2018-01-31")] == 0.0

    def test_control_the_same_jump_one_day_later_is_earned(self) -> None:
        """Paired with the guard above: the channel is live, not merely quiet."""
        index = _index()
        closes, adv, sigma = self._inputs(index, "2018-02-01")
        book = book_weights(
            self._weights(index),
            closes,
            adv,
            sigma,
            CostModel(
                half_spread_bps=(0.0,) * 4, impact_coef=0.0, borrow_rate_annual=0.0
            ),
        )
        returns = pd.Series(book.portfolio_return, index=book.daily_index)
        assert returns.loc[pd.Timestamp("2018-02-01")] > 0.09

    def test_costs_are_charged_on_turnover_and_reduce_the_return(self) -> None:
        index = _index()
        closes, adv, sigma = self._inputs(index, "2018-02-02")
        weights = self._weights(index)
        free = CostModel(
            half_spread_bps=(0.0,) * 4, impact_coef=0.0, borrow_rate_annual=0.0
        )
        priced = CostModel(impact_coef=0.0, borrow_rate_annual=0.0)
        gross = book_weights(weights, closes, adv, sigma, free)
        net = book_weights(weights, closes, adv, sigma, priced)
        entry = gross.daily_index.get_loc(pd.Timestamp("2018-02-01"))
        # One unit of turnover at the most liquid bucket = 1 bp.
        assert gross.portfolio_return[entry] == 0.0
        assert net.portfolio_return[entry] < 0.0
        assert abs(net.portfolio_return[entry] + 1e-4) < 1e-9

    def test_an_unpriceable_name_pays_the_widest_bucket(self) -> None:
        """Unknown ADV must cost MORE than a liquid name, never nothing."""
        index = _index()
        closes, adv, sigma = self._inputs(index, "2018-02-02")
        weights = self._weights(index)
        priced = CostModel(impact_coef=0.0, borrow_rate_annual=0.0)
        liquid = book_weights(weights, closes, adv, sigma, priced)
        blind = adv.copy()
        blind["AAA"] = np.nan
        unknown = book_weights(weights, closes, blind, sigma, priced)
        entry = liquid.daily_index.get_loc(pd.Timestamp("2018-02-01"))
        assert unknown.portfolio_return[entry] < liquid.portfolio_return[entry]
        assert abs(unknown.portfolio_return[entry] + 20e-4) < 1e-9

    def test_short_weights_accrue_borrow_every_held_day(self) -> None:
        index = _index()
        closes, adv, sigma = self._inputs(index, "2018-06-01")
        weights = -self._weights(index)
        free = CostModel(
            half_spread_bps=(0.0,) * 4, impact_coef=0.0, borrow_rate_annual=0.0
        )
        borrowed = CostModel(
            half_spread_bps=(0.0,) * 4, impact_coef=0.0, borrow_rate_annual=0.01
        )
        held = pd.Timestamp("2018-02-15")  # inside the hold, no turnover that day
        flat = book_weights(weights, closes, adv, sigma, free)
        charged = book_weights(weights, closes, adv, sigma, borrowed)
        idx = flat.daily_index.get_loc(held)
        assert flat.portfolio_return[idx] == 0.0
        assert abs(charged.portfolio_return[idx] + 0.01 / 252.0) < 1e-12

    def test_the_book_carries_no_governor_by_design(self) -> None:
        index = _index()
        closes, adv, sigma = self._inputs(index, "2018-02-01")
        book = book_weights(self._weights(index), closes, adv, sigma, CostModel())
        assert np.array_equal(book.portfolio_return, book.pre_governor_return)
        assert np.all(book.governor == 1.0)


class TestTrailingAdv:
    """The weight key and the cost key are one number, computed causally."""

    def test_is_a_trailing_mean_of_dollar_volume(self) -> None:
        index = _index(periods=5)
        dollars = pd.DataFrame({"AAA": [1.0, 3.0, 5.0, 7.0, 9.0]}, index=index)
        adv = trailing_adv(dollars, 2)
        assert adv["AAA"].iloc[0] == 1.0
        assert adv["AAA"].iloc[1] == 2.0
        assert adv["AAA"].iloc[4] == 8.0

    def test_control_a_later_bar_cannot_change_an_earlier_adv(self) -> None:
        index = _index(periods=5)
        base = pd.DataFrame({"AAA": [1.0, 3.0, 5.0, 7.0, 9.0]}, index=index)
        future = base.copy()
        future.loc[index[4], "AAA"] = 1e9
        assert (
            trailing_adv(base, 2)["AAA"].iloc[1]
            == trailing_adv(future, 2)["AAA"].iloc[1]
        )
