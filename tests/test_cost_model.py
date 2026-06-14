"""Tests for the Phase 0.4 equity cost model (analytics/backtest/cost_model.py)."""

from dataclasses import dataclass

import numpy as np
import pytest

from analytics.backtest.cost_model import (
    BARS_PER_DAY,
    CostBreakdown,
    CostContext,
    CostModel,
    bars_per_day_for_tf,
    build_cost_context,
    cost_model_from_toml,
)

_DAY_MS = 86_400_000


@dataclass
class _FakeTrade:
    """Minimal TradeLike — keeps this test file engine-free."""

    direction: str = "long"
    entry_price: float = 100.0
    sl_price: float = 98.0
    entry_time: int = 0
    exit_time: int | None = _DAY_MS


class TestCostModelValidation:
    def test_defaults_construct(self) -> None:
        model = CostModel()
        assert model.adv_thresholds == (5e6, 5e7, 5e8)
        assert model.half_spread_bps == (20.0, 8.0, 3.0, 1.0)
        assert model.borrow_rate_annual == 0.01

    def test_spread_bucket_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="one more entry"):
            CostModel(adv_thresholds=(1e6,), half_spread_bps=(10.0,))

    def test_non_ascending_thresholds_raise(self) -> None:
        with pytest.raises(ValueError, match="ascending"):
            CostModel(adv_thresholds=(5e7, 5e6), half_spread_bps=(20.0, 8.0, 3.0))

    def test_negative_params_raise(self) -> None:
        with pytest.raises(ValueError):
            CostModel(impact_coef=-0.1)
        with pytest.raises(ValueError):
            CostModel(notional_usd=0.0)
        with pytest.raises(ValueError):
            CostModel(borrow_rate_annual=-0.01)
        with pytest.raises(ValueError):
            CostModel(commission_bps=-1.0)
        with pytest.raises(ValueError):
            CostModel(adv_window_days=0.0)


class TestSpreadBucket:
    def test_bucket_edges(self) -> None:
        model = CostModel()
        # 0 = least liquid (widest spread); an ADV exactly at a threshold
        # falls in the MORE liquid bucket (searchsorted side="right").
        assert model.spread_bucket(None) == 0
        assert model.spread_bucket(0.0) == 0
        assert model.spread_bucket(-1.0) == 0
        assert model.spread_bucket(1e6) == 0
        assert model.spread_bucket(5e6) == 1
        assert model.spread_bucket(1e7) == 1
        assert model.spread_bucket(5e7) == 2
        assert model.spread_bucket(1e8) == 2
        assert model.spread_bucket(5e8) == 3
        assert model.spread_bucket(1e9) == 3


class TestCostBreakdown:
    def test_hand_computed_long(self) -> None:
        # risk = |100 - 98| = 2 → notional_to_r = entry/risk = 50.
        # bucket(1e8) = 2 → 3 bps half-spread → spread_r = 2 × 3e-4 × 50 = 0.03
        # impact/leg = 1.0 × 0.02 × sqrt(10_000 / 1e8) = 2e-4
        #   → impact_r = 2 × 2e-4 × 50 = 0.02
        model = CostModel()
        ctx = CostContext(adv_dollars=1e8, sigma_daily=0.02)
        bd = model.cost_breakdown(_FakeTrade(), ctx)
        assert bd.spread_r == pytest.approx(0.03)
        assert bd.impact_r == pytest.approx(0.02)
        assert bd.borrow_r == 0.0  # longs pay no borrow
        assert bd.commission_r == 0.0  # retail default
        assert bd.total_r == pytest.approx(0.05)
        assert model.cost_r(_FakeTrade(), ctx) == pytest.approx(0.05)

    def test_hand_computed_short_borrow(self) -> None:
        # 2 calendar days held → borrow_r = 0.01 × (2/365) × 50; charged once.
        model = CostModel()
        ctx = CostContext(adv_dollars=1e8, sigma_daily=0.02)
        trade = _FakeTrade(direction="short", sl_price=102.0, exit_time=2 * _DAY_MS)
        bd = model.cost_breakdown(trade, ctx)
        expected_borrow = 0.01 * (2.0 / 365.0) * 50.0
        assert bd.borrow_r == pytest.approx(expected_borrow)
        assert bd.total_r == pytest.approx(0.03 + 0.02 + expected_borrow)

    def test_open_short_pays_no_borrow(self) -> None:
        model = CostModel()
        trade = _FakeTrade(direction="short", sl_price=102.0, exit_time=None)
        bd = model.cost_breakdown(trade, CostContext(adv_dollars=1e8, sigma_daily=0.02))
        assert bd.borrow_r == 0.0

    def test_none_ctx_falls_back_conservatively(self) -> None:
        # No ADV → widest bucket (20 bps); no sigma → zero impact.
        model = CostModel()
        bd = model.cost_breakdown(_FakeTrade(), None)
        assert bd.spread_r == pytest.approx(2 * 0.0020 * 50.0)
        assert bd.impact_r == 0.0

    def test_zero_risk_is_zero_cost(self) -> None:
        model = CostModel()
        trade = _FakeTrade(sl_price=100.0)  # entry == sl
        assert model.cost_breakdown(trade, None) == CostBreakdown()

    def test_commission_bps(self) -> None:
        model = CostModel(commission_bps=5.0)
        bd = model.cost_breakdown(_FakeTrade(), None)
        assert bd.commission_r == pytest.approx(2 * 0.0005 * 50.0)

    def test_to_json_is_canonical(self) -> None:
        a = CostModel().to_json()
        b = CostModel().to_json()
        assert a == b
        assert '"borrow_rate_annual":0.01' in a
        # Different params → different stamp (feeds the run_id hash).
        assert CostModel(impact_coef=0.5).to_json() != a


class TestBarsPerDay:
    def test_known_timeframes(self) -> None:
        assert BARS_PER_DAY["4h"] == 2.0
        assert bars_per_day_for_tf("1d") == 1.0
        assert bars_per_day_for_tf("1wk") == 0.2

    def test_unknown_timeframe_falls_back_to_one(self) -> None:
        assert bars_per_day_for_tf("15m") == 1.0


class TestBuildCostContext:
    def test_hand_computed_adv_and_sigma(self) -> None:
        # closes 100 -> 102 (+2%) -> 99.96 (-2%); population stdev of
        # [0.02, -0.02] = 0.02; bars_per_day=4 -> sigma_daily = 0.02*sqrt(4).
        closes = np.array([100.0, 102.0, 99.96])
        volumes = np.array([1000.0, 1000.0, 1000.0])
        ctx = build_cost_context(closes, volumes, 2, 4.0, 20)
        expected_adv = (100_000.0 + 102_000.0 + 99_960.0) / 3.0 * 4.0
        assert ctx.adv_dollars == pytest.approx(expected_adv)
        assert ctx.sigma_daily == pytest.approx(0.04)

    def test_window_truncates_old_bars(self) -> None:
        # window_bars=2 -> only the last two bars feed ADV/sigma.
        closes = np.array([1.0, 100.0, 100.0])
        volumes = np.array([1e9, 1000.0, 1000.0])
        ctx = build_cost_context(closes, volumes, 2, 1.0, 2)
        assert ctx.adv_dollars == pytest.approx(100_000.0)

    def test_causal_future_bars_do_not_change_context(self) -> None:
        closes = np.array([100.0, 101.0, 99.0, 102.0, 98.0, 103.0])
        volumes = np.array([1e6, 2e6, 3e6, 4e6, 5e6, 6e6])
        before = build_cost_context(closes[:3], volumes[:3], 2, 1.0, 20)
        after = build_cost_context(closes, volumes, 2, 1.0, 20)
        assert before == after

    def test_insufficient_history_yields_nones(self) -> None:
        closes = np.array([100.0, 101.0])
        volumes = np.array([1000.0, 1000.0])
        ctx = build_cost_context(closes, volumes, 1, 1.0, 20)
        assert ctx.adv_dollars is not None  # 2 bars are enough for ADV
        assert ctx.sigma_daily is None  # but not for a return stdev

    def test_zero_volume_yields_no_adv(self) -> None:
        closes = np.array([100.0, 100.0, 100.0])
        volumes = np.array([0.0, 0.0, 0.0])
        ctx = build_cost_context(closes, volumes, 2, 1.0, 20)
        assert ctx.adv_dollars is None

    def test_flat_series_has_zero_sigma_not_none(self) -> None:
        closes = np.array([100.0, 100.0, 100.0, 100.0])
        volumes = np.array([1000.0] * 4)
        ctx = build_cost_context(closes, volumes, 3, 1.0, 20)
        assert ctx.sigma_daily == 0.0


class TestCostModelFromToml:
    def test_absent_block_returns_none(self) -> None:
        assert cost_model_from_toml(None) is None

    def test_disabled_block_returns_none(self) -> None:
        assert cost_model_from_toml({"enabled": False}) is None
        assert cost_model_from_toml({}) is None  # enabled defaults to false

    def test_non_table_raises(self) -> None:
        with pytest.raises(ValueError, match="TOML table"):
            cost_model_from_toml(True)

    def test_enabled_with_defaults(self) -> None:
        model = cost_model_from_toml({"enabled": True})
        assert model == CostModel()

    def test_enabled_with_overrides(self) -> None:
        model = cost_model_from_toml(
            {
                "enabled": True,
                "adv_thresholds": [1e6, 1e7],
                "half_spread_bps": [25.0, 10.0, 4.0],
                "impact_coef": 0.5,
                "notional_usd": 25_000.0,
                "borrow_rate_annual": 0.02,
                "commission_bps": 1.0,
                "adv_window_days": 10.0,
            }
        )
        assert model is not None
        assert model.adv_thresholds == (1e6, 1e7)
        assert model.half_spread_bps == (25.0, 10.0, 4.0)
        assert model.impact_coef == 0.5
        assert model.notional_usd == 25_000.0
        assert model.borrow_rate_annual == 0.02
        assert model.commission_bps == 1.0
        assert model.adv_window_days == 10.0

    def test_invalid_values_raise_with_block_name(self) -> None:
        with pytest.raises(ValueError, match="backtest.cost_model"):
            cost_model_from_toml({"enabled": True, "adv_thresholds": [1e7, 1e6]})
