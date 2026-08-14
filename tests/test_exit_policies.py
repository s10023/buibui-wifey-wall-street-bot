"""Tests for the exit-policy config layer (exit spec §3 / §7)."""

from dataclasses import FrozenInstanceError

import pytest

from analytics.exits.policies import ExitPolicyConfig, composite, fixed


class TestFixed:
    def test_fixed_is_baseline_policy_zero(self) -> None:
        p = fixed(tp_r=3.5, max_hold_bars=48)
        assert p.name == "fixed"
        assert p.tp_r == 3.5
        assert p.max_hold_bars == 48
        # no breakeven, no partial, time-stop = full window
        assert p.breakeven_arm_r is None
        assert p.partial_frac == 0.0
        assert p.effective_time_stop_bars == 48

    def test_fixed_has_no_partial_or_be(self) -> None:
        p = fixed(tp_r=2.0, max_hold_bars=30)
        assert not p.has_partial
        assert not p.has_breakeven


class TestComposite:
    def test_composite_defaults_lock_at_1r(self) -> None:
        p = composite(tp_r=3.5, max_hold_bars=48, time_stop_bars=24)
        assert p.name == "composite"
        assert p.breakeven_arm_r == 1.0
        assert p.partial_frac == 0.5
        assert p.partial_r == 1.0
        assert p.effective_time_stop_bars == 24
        assert p.has_partial
        assert p.has_breakeven

    def test_composite_custom_params(self) -> None:
        p = composite(
            tp_r=4.0,
            max_hold_bars=96,
            time_stop_bars=65,
            breakeven_arm_r=1.5,
            partial_frac=0.33,
            partial_r=1.5,
        )
        assert p.breakeven_arm_r == 1.5
        assert p.partial_frac == 0.33
        assert p.partial_r == 1.5
        assert p.effective_time_stop_bars == 65


class TestValidation:
    def test_tp_r_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            ExitPolicyConfig(name="x", tp_r=0.0, max_hold_bars=10)

    def test_max_hold_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            ExitPolicyConfig(name="x", tp_r=2.0, max_hold_bars=0)

    def test_time_stop_cannot_exceed_max_hold(self) -> None:
        with pytest.raises(ValueError):
            ExitPolicyConfig(name="x", tp_r=2.0, max_hold_bars=10, time_stop_bars=11)

    def test_time_stop_must_be_at_least_one(self) -> None:
        with pytest.raises(ValueError):
            ExitPolicyConfig(name="x", tp_r=2.0, max_hold_bars=10, time_stop_bars=0)

    def test_partial_frac_in_unit_interval(self) -> None:
        with pytest.raises(ValueError):
            ExitPolicyConfig(
                name="x", tp_r=2.0, max_hold_bars=10, partial_frac=1.0, partial_r=1.0
            )

    def test_partial_requires_positive_r(self) -> None:
        with pytest.raises(ValueError):
            ExitPolicyConfig(
                name="x", tp_r=2.0, max_hold_bars=10, partial_frac=0.5, partial_r=0.0
            )

    def test_breakeven_arm_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            ExitPolicyConfig(name="x", tp_r=2.0, max_hold_bars=10, breakeven_arm_r=0.0)

    def test_frozen(self) -> None:
        p = fixed(tp_r=2.0, max_hold_bars=10)
        with pytest.raises(FrozenInstanceError):
            p.tp_r = 3.0  # type: ignore[misc]
