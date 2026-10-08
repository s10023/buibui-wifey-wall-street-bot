"""OV-1 replay costs, the three-leg gate and the reported diagnostics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.overlay.replay import arm_returns
from analytics.overlay.report import (
    SEED,
    beta_attribution,
    bootstrap_legs,
    evaluate_overlay,
    overlay_verdict,
    summarize_arm,
)
from analytics.research_guards.bootstrap import BootstrapCI


def _ci(lo: float, hi: float) -> BootstrapCI:
    return BootstrapCI(point=(lo + hi) / 2, lo=lo, hi=hi, alpha=0.05, n_valid=100)


class TestVerdict:
    @pytest.mark.parametrize(
        ("d_ui", "ratio", "d_sr", "expected"),
        [
            ((-5.0, -1.0), 0.60, (-0.05, 0.20), "FOUND"),
            ((-5.0, -1.0), 0.80, (-0.05, 0.20), "BOUNDED"),  # leg 2 fails
            ((-5.0, -1.0), 0.60, (-0.15, 0.20), "BOUNDED"),  # leg 3 straddles -0.10
            ((-5.0, -1.0), 0.60, (-0.10, 0.20), "BOUNDED"),  # lo == -0.10 is not above
            ((0.0, 3.0), 0.60, (-0.05, 0.20), "EXCLUDED"),  # leg 1 wholly at or above 0
            (
                (-5.0, -1.0),
                0.60,
                (-0.40, -0.11),
                "EXCLUDED",
            ),  # leg 3 wholly below -0.10
            ((-5.0, 0.0), 0.60, (-0.05, 0.20), "UNREGISTERED"),  # hi == 0 is not below
            ((-5.0, 2.0), 0.60, (-0.05, 0.20), "UNREGISTERED"),
        ],
    )
    def test_truth_table(
        self,
        d_ui: tuple[float, float],
        ratio: float,
        d_sr: tuple[float, float],
        expected: str,
    ) -> None:
        assert overlay_verdict(_ci(*d_ui), ratio, _ci(*d_sr)) == expected

    def test_ratio_at_the_bar_passes(self) -> None:
        assert overlay_verdict(_ci(-5, -1), 0.75, _ci(-0.05, 0.2)) == "FOUND"


def _frame(n: int = 1_500, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("1990-01-01", periods=n)
    mkt = rng.normal(0.0003, 0.012, n)
    mkt[600:700] = -0.01  # one crash the overlay sits out
    pos = np.ones(n)
    pos[605:720] = 0.0
    return pd.DataFrame({"mkt": mkt, "rf": 0.0001, "pos": pos}, index=idx)


class TestArmReturns:
    def test_cost_is_charged_per_unit_of_position_change(self) -> None:
        idx = pd.bdate_range("2000-01-03", periods=4)
        frame = pd.DataFrame(
            {"mkt": [0.01] * 4, "rf": [0.001] * 4, "pos": [1.0, 0.0, 0.0, 1.0]},
            index=idx,
        )
        arms = arm_returns(frame, bps=2.0)
        assert arms["bh"].tolist() == pytest.approx([0.01] * 4)
        assert arms["ov"].tolist() == pytest.approx(
            [0.01, 0.001 - 0.0002, 0.001, 0.01 - 0.0002]
        )

    def test_zero_cost_flat_earns_rf(self) -> None:
        arms = arm_returns(_frame(), bps=0.0)
        flat = arms["pos"] == 0.0
        assert (arms.loc[flat, "ov"] == arms.loc[flat, "rf"]).all()


class TestGate:
    def test_overlay_that_sits_out_a_crash_lowers_ulcer(self) -> None:
        arms = arm_returns(_frame(), bps=2.0)
        gate = evaluate_overlay(arms, n_boot=60, block=50, seed=SEED)
        assert gate.d_ui.point < 0.0
        assert gate.ui_ratio < 1.0
        assert gate.verdict in {"FOUND", "BOUNDED", "UNREGISTERED", "EXCLUDED"}

    def test_identical_arms_give_zero_differences(self) -> None:
        frame = _frame()
        frame["pos"] = 1.0
        arms = arm_returns(frame, bps=2.0)
        d_ui, d_sr = bootstrap_legs(arms, n_boot=30, block=50, seed=1)
        assert d_ui.lo == d_ui.hi == 0.0
        assert d_sr.lo == d_sr.hi == 0.0

    def test_legs_share_resamples(self) -> None:
        # Same seed twice → the same CI; the legs are paired by construction.
        arms = arm_returns(_frame(), bps=2.0)
        a = bootstrap_legs(arms, n_boot=40, block=50, seed=3)
        b = bootstrap_legs(arms, n_boot=40, block=50, seed=3)
        assert a == b


class TestDiagnostics:
    def test_beta_attribution_recovers_a_known_mix(self) -> None:
        rng = np.random.default_rng(8)
        n = 5_000
        idx = pd.bdate_range("1990-01-01", periods=n)
        rf = np.full(n, 0.0001)
        mkt = rf + rng.normal(0.0003, 0.01, n)
        ov = rf + 0.4 * (mkt - rf) + 0.0002 + rng.normal(0, 0.001, n)
        arms = pd.DataFrame({"bh": mkt, "ov": ov, "rf": rf, "pos": 0.4}, index=idx)
        att = beta_attribution(arms)
        assert att.beta == pytest.approx(0.4, abs=0.01)
        assert att.alpha_annual == pytest.approx(0.0002 * 252, rel=0.1)
        assert att.alpha_t > 5.0

    def test_summary_fields(self) -> None:
        arms = arm_returns(_frame(), bps=2.0)
        bh = summarize_arm(arms, "bh")
        ov = summarize_arm(arms, "ov")
        assert bh.in_market == 1.0 and bh.switches_per_year == 0.0
        assert 0.0 < ov.in_market < 1.0
        years = (arms.index[-1] - arms.index[0]).days / 365.25
        assert ov.switches_per_year == pytest.approx(2 / years)
        assert ov.max_dd > bh.max_dd  # less negative
        assert ov.tuw_sessions >= 0
