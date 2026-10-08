"""VM (#421): the volatility weight, its causality, and the increment gate's plumbing.

The causality check perturbs every return after a cut and requires each weight
the pre-cut data determines to be unchanged. A "did not change" check passes
both when the invariant holds and when the perturbation never arrives, and the
weight's cap at 1 can swallow a perturbation, so the fixture keeps the weight
below the cap most of the time and the ``lag=0`` weight, which reads the
session being earned, must be flagged on the same channel.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

from analytics.overlay.frame import vol_target, with_vol_weight
from analytics.overlay.replay import arm_returns
from analytics.overlay.report import evaluate_overlay, summarize_arm
from analytics.overlay.rules import realized_vol, vol_weight

CUTS = 6
VM_POSITIONS = {"ov": "pos", "vm": "w", "ovvm": "w_ov"}


def _returns(n: int = 900, seed: int = 21) -> pd.Series:
    """Returns whose volatility regime switches, so σ̂ crosses the target often."""
    rng = np.random.default_rng(seed)
    scale = np.where((np.arange(n) // 60) % 2 == 0, 0.006, 0.02)
    return pd.Series(
        rng.normal(0.0003, 1.0, n) * scale,
        index=pd.bdate_range("1990-01-01", periods=n),
    )


class TestVolWeight:
    def test_warm_up_is_nan(self) -> None:
        w = vol_weight(_returns(), 0.01, window=20, lag=1)
        assert w.iloc[:20].isna().all()
        assert w.iloc[20:].notna().all()

    def test_weight_is_target_over_sigma_capped_at_one(self) -> None:
        r = _returns()
        sigma = realized_vol(r, 20)
        w = vol_weight(r, 0.01, window=20, lag=0)
        expected = (0.01 / sigma).clip(upper=1.0)
        pd.testing.assert_series_equal(w, expected.rename("w"))
        assert (w.dropna() <= 1.0).all()
        assert (w.dropna() < 1.0).mean() > 0.3  # the fixture is not pinned at the cap

    def test_lag_one_holds_yesterdays_weight(self) -> None:
        r = _returns()
        same = vol_weight(r, 0.01, lag=0)
        held = vol_weight(r, 0.01, lag=1)
        pd.testing.assert_series_equal(held, same.shift(1))

    def test_zero_volatility_is_full_weight(self) -> None:
        r = pd.Series(0.001, index=pd.bdate_range("2000-01-03", periods=30))
        assert (vol_weight(r, 0.01, lag=0).dropna() == 1.0).all()

    def test_series_target_is_aligned(self) -> None:
        r = _returns()
        target = realized_vol(r).expanding().median()
        w = vol_weight(r, target, lag=0)
        idx = r.index[400]
        assert w[idx] == pytest.approx(min(1.0, target[idx] / realized_vol(r)[idx]))

    def test_negative_lag_is_refused(self) -> None:
        with pytest.raises(ValueError):
            vol_weight(_returns(), 0.01, lag=-1)


def _disagreements(rule: Callable[[pd.Series], pd.Series]) -> int:
    """Cuts at which perturbing returns after the cut moves a weight held through cut + 1."""
    r = _returns()
    full = rule(r)
    flagged = 0
    for c in np.linspace(100, len(r) - 2, CUTS).astype(int):
        bent = r.copy()
        # +20% a session: large enough that the capped weight cannot absorb it.
        bent.iloc[c + 1 :] = bent.iloc[c + 1 :] + 0.2
        upto = r.index[: c + 2]  # through session c + 1, which pre-cut data decides
        if not rule(bent).loc[upto].equals(full.loc[upto]):
            flagged += 1
    return flagged


class TestCausality:
    def test_vm_weight_is_causal(self) -> None:
        assert _disagreements(lambda r: vol_weight(r, 0.01, lag=1)) == 0

    def test_execution_lag_is_causal(self) -> None:
        assert _disagreements(lambda r: vol_weight(r, 0.01, lag=2)) == 0

    def test_real_time_target_is_causal(self) -> None:
        def realtime(r: pd.Series) -> pd.Series:
            return vol_weight(r, realized_vol(r).expanding().median(), lag=1)

        assert _disagreements(realtime) == 0

    def test_positive_control_same_session_weight_is_flagged(self) -> None:
        assert _disagreements(lambda r: vol_weight(r, 0.01, lag=0)) == CUTS


def _vm_frame() -> tuple[pd.DataFrame, pd.Series]:
    market = _returns()
    frame = pd.DataFrame(
        {
            "mkt": market,
            "rf": 0.0001,
            "pos": np.where(np.arange(len(market)) % 200 < 150, 1.0, 0.0),
        },
        index=market.index,
    ).iloc[100:]
    return frame, market


class TestWithVolWeight:
    def test_target_is_the_panel_median(self) -> None:
        frame, market = _vm_frame()
        assert vol_target(market, frame.index) == pytest.approx(
            float(realized_vol(market).loc[frame.index].median())
        )

    def test_target_refuses_an_undefined_panel_session(self) -> None:
        market = _returns()
        with pytest.raises(ValueError, match="undefined"):
            vol_target(market, market.index)

    def test_overlay_product_and_warm_up_from_the_full_series(self) -> None:
        frame, market = _vm_frame()
        out = with_vol_weight(frame, market, 0.01)
        # The panel opens at row 100; its weights use returns before the panel.
        assert out["w"].notna().all()
        assert (out["w_ov"] == out["pos"] * out["w"]).all()

    def test_missing_weight_is_refused(self) -> None:
        market = _returns()
        frame = pd.DataFrame(
            {"mkt": market, "rf": 0.0001, "pos": 1.0}, index=market.index
        )
        with pytest.raises(ValueError, match="no VM weight"):
            with_vol_weight(frame, market, 0.01)


class TestFractionalArms:
    def test_fractional_position_is_priced_per_unit_change(self) -> None:
        idx = pd.bdate_range("2000-01-03", periods=4)
        frame = pd.DataFrame(
            {
                "mkt": [0.01] * 4,
                "rf": [0.001] * 4,
                "pos": 1.0,
                "w": [1.0, 0.5, 0.5, 0.75],
            },
            index=idx,
        )
        arms = arm_returns(frame, bps=2.0, positions={"vm": "w"})
        assert arms["vm"].tolist() == pytest.approx(
            [0.01, 0.0055 - 0.0001, 0.0055, 0.0077500 - 0.00005]
        )
        assert list(arms.columns) == ["bh", "vm", "rf", "w"]

    def test_turnover_reads_the_fractional_position(self) -> None:
        frame, market = _vm_frame()
        arms = arm_returns(
            with_vol_weight(frame, market, 0.01), bps=2.0, positions=VM_POSITIONS
        )
        s = summarize_arm(arms, "vm", pos="w")
        years = (arms.index[-1] - arms.index[0]).days / 365.25
        assert s.turnover_per_year == pytest.approx(
            float(arms["w"].diff().abs().sum()) / years
        )
        assert 0.0 < s.in_market < 1.0


class TestIncrementGate:
    def _arms(self) -> pd.DataFrame:
        frame, market = _vm_frame()
        return arm_returns(
            with_vol_weight(frame, market, 0.01), bps=2.0, positions=VM_POSITIONS
        )

    def test_an_arm_against_itself_is_zero(self) -> None:
        gate = evaluate_overlay(
            self._arms(), base="ov", arm="ov", n_boot=30, block=50, seed=1
        )
        assert gate.d_ui.lo == gate.d_ui.hi == 0.0
        assert gate.d_sr.lo == gate.d_sr.hi == 0.0

    def test_swapping_base_and_arm_negates_the_points(self) -> None:
        arms = self._arms()
        a = evaluate_overlay(arms, base="ov", arm="ovvm", n_boot=30, block=50, seed=1)
        b = evaluate_overlay(arms, base="ovvm", arm="ov", n_boot=30, block=50, seed=1)
        assert a.d_ui.point == pytest.approx(-b.d_ui.point)
        assert a.d_sr.point == pytest.approx(-b.d_sr.point)
        assert a.ui_ratio == pytest.approx(1.0 / b.ui_ratio)
