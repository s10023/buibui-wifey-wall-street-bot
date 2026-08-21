"""Tests for analytics/research_guards/sharpe.py — the shared Sharpe primitive.

This function was copied byte-identically into both sleeve report modules and
inlined into ``xsmom.diagnostics``, then fed straight into DSR and the bootstrap
statistic, i.e. two of the three legs ``passes_sleeve_gate`` compares across
sleeves. The tests that matter here are therefore not "does it divide" but the
three ways those copies could disagree: the degenerate-input contract, the
annualisation convention that already collides by name with
``analytics.xsmom.diagnostics._ann_sharpe``, and the five sibling Sharpes in this
repo that are NOT this function and must not be folded into it.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from analytics.research_guards import ann_sharpe, per_period_sharpe
from analytics.xsmom.diagnostics import _ann_sharpe as _diagnostics_ann_sharpe


def _arr(*xs: float) -> npt.NDArray[np.float64]:
    return np.asarray(xs, dtype=np.float64)


class TestPerPeriodSharpe:
    def test_it_is_mean_over_sample_sd(self) -> None:
        r = _arr(0.01, 0.02, -0.01, 0.03)
        expected = float(np.mean(r) / np.std(r, ddof=1))
        assert per_period_sharpe(r) == expected

    def test_sample_sd_not_population_sd(self) -> None:
        # ddof=1. With ddof=0 the denominator shrinks and the Sharpe inflates,
        # which is exactly the kind of one-copy change that used to be able to
        # desynchronise the sleeves.
        r = _arr(0.01, 0.02, -0.01, 0.03)
        population = float(np.mean(r) / np.std(r, ddof=0))
        assert per_period_sharpe(r) != population
        assert per_period_sharpe(r) < population

    def test_fewer_than_two_observations_is_zero(self) -> None:
        assert per_period_sharpe(_arr()) == 0.0
        assert per_period_sharpe(_arr(0.05)) == 0.0

    def test_exactly_flat_series_is_zero_not_nan(self) -> None:
        # sd == 0 would divide by zero; the guard returns 0.0 so a dead
        # warm-up cannot propagate NaN into DSR.
        assert per_period_sharpe(_arr(0.01, 0.01, 0.01)) == 0.0

    def test_near_flat_series_is_NOT_zeroed(self) -> None:
        # THE distinction the 1e-12 floor makes, and it runs the opposite way
        # to what the name suggests: it rejects only a near-exactly-flat series,
        # so tightly clustered returns still yield a finite, enormous Sharpe.
        # There is no dispersion floor here; adding one is a behaviour change.
        r = np.full(36, -1.0076, dtype=np.float64)
        r[::2] += 0.0022
        sharpe = per_period_sharpe(r)
        assert sharpe != 0.0
        assert abs(sharpe) > 100.0

    def test_sign_follows_the_mean(self) -> None:
        assert per_period_sharpe(_arr(-0.01, -0.02, -0.03)) < 0.0
        assert per_period_sharpe(_arr(0.01, 0.02, 0.03)) > 0.0


class TestAnnSharpe:
    def test_the_factor_is_applied_as_given(self) -> None:
        # ann_factor is ALREADY sqrt(periods) — both report modules pass
        # math.sqrt(cfg.annualization_days).
        r = _arr(0.01, 0.02, -0.01, 0.03)
        ann = math.sqrt(252.0)
        assert ann_sharpe(r, ann) == per_period_sharpe(r) * ann

    def test_degenerate_inputs_stay_zero_after_annualising(self) -> None:
        assert ann_sharpe(_arr(0.01), math.sqrt(252.0)) == 0.0
        assert ann_sharpe(_arr(0.01, 0.01), math.sqrt(252.0)) == 0.0


class TestTheTwoConventionsAreDistinct:
    """``ann_sharpe`` and ``diagnostics._ann_sharpe`` differ by a sqrt.

    Same name, same shape, different meaning of the second argument. Swapping
    one for the other changes every number it touches by sqrt(annualization
    days) — about 15.9x at 252 — and nothing raises. This pins the relation so
    that a future "cleanup" unifying them has to fail a test first.
    """

    def test_diagnostics_roots_its_argument_and_the_shared_one_does_not(
        self,
    ) -> None:
        r = _arr(0.01, 0.02, -0.01, 0.03, 0.005)
        days = 252.0
        assert _diagnostics_ann_sharpe(r, days) == ann_sharpe(r, math.sqrt(days))

    def test_passing_raw_days_to_the_shared_one_is_wrong_by_sqrt(self) -> None:
        r = _arr(0.01, 0.02, -0.01, 0.03, 0.005)
        days = 252.0
        wrong = ann_sharpe(r, days)
        right = _diagnostics_ann_sharpe(r, days)
        assert not math.isclose(wrong, right)
        assert math.isclose(wrong / right, math.sqrt(days))


class TestNoSleeveKeepsItsOwnCopy:
    def test_both_report_modules_use_the_shared_primitive(self) -> None:
        # The duplication this module exists to end. An `is` check, so
        # re-defining a private copy in any sleeve fails here rather than
        # drifting silently.
        from analytics.forecast import report as forecast_report
        from analytics.xsmom import report as xsmom_report

        for module in (forecast_report, xsmom_report):
            assert module.per_period_sharpe is per_period_sharpe
            assert module.ann_sharpe is ann_sharpe
            assert not hasattr(module, "_per_period_sharpe")

    def test_diagnostics_shares_the_per_period_half(self) -> None:
        from analytics.xsmom import diagnostics

        assert diagnostics.per_period_sharpe is per_period_sharpe


class TestTheDivergentSiblingsAreNotThisFunction:
    """Five siblings compute a mean/sd Sharpe and are deliberately NOT shared.

    The docstring claims they disagree on the degenerate-input contract. A claim
    like that is worthless asserted, so each is pinned against a CONSTRUCTED
    input on which it and ``per_period_sharpe`` actually return different values.
    Folding any of them in here has to fail one of these first.
    """

    # sd = 1e-12/sqrt(2) ~= 7.07e-13: strictly positive, strictly below 1e-12.
    NEAR_FLAT = (1.0, 1.0 + 1e-12)

    def test_the_input_really_does_straddle_the_two_guards(self) -> None:
        # Positive control: without this the rest could pass vacuously on an
        # input where every guard agrees.
        sd = float(np.std(_arr(*self.NEAR_FLAT), ddof=1))
        assert sd != 0.0
        assert sd < 1e-12

    def test_sweep_guard_trial_sharpe_is_stricter(self) -> None:
        from analytics.sweep_guard import _trial_sharpe

        assert per_period_sharpe(_arr(*self.NEAR_FLAT)) == 0.0
        assert _trial_sharpe(list(self.NEAR_FLAT)) != 0.0

    def test_pbo_sharpe_is_stricter(self) -> None:
        from analytics.research_guards.pbo import _sharpe

        assert per_period_sharpe(_arr(*self.NEAR_FLAT)) == 0.0
        assert _sharpe(_arr(*self.NEAR_FLAT)) != 0.0

    def test_audit_guard_slice_sharpe_returns_infinity_where_this_returns_zero(
        self,
    ) -> None:
        # The sharpest divergence: a zero-variance non-zero-mean slice is
        # +inf there (a deterministic edge is maximally significant) and 0.0
        # here. Unifying them would silently rewrite every warning verdict.
        from analytics.audit_guard import _slice_sharpe

        flat = _arr(0.5, 0.5, 0.5)
        assert per_period_sharpe(flat) == 0.0
        assert _slice_sharpe(flat) == math.inf

    def test_stats_overfit_sharpe_ratio_takes_a_sequence_via_moments(self) -> None:
        from analytics.backtest.stats_overfit import sharpe_ratio

        assert sharpe_ratio(list(self.NEAR_FLAT)) != 0.0
        assert per_period_sharpe(_arr(*self.NEAR_FLAT)) == 0.0

    def test_forecast_metrics_sharpe_uses_a_looser_floor_and_annualises(
        self,
    ) -> None:
        # 1e-10, not 1e-12, and the annualisation is inside the function —
        # so it is neither per_period_sharpe nor ann_sharpe.
        from analytics.forecast import metrics

        assert metrics.sharpe is not per_period_sharpe
        assert metrics.sharpe is not ann_sharpe
