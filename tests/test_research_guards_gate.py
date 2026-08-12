"""Tests for `analytics/research_guards/gate.py` — the shared sleeve gate.

Every leg is exercised so that flipping it alone flips the verdict. The four
sleeves previously inlined this expression identically four times, and the one
test that checked a verdict re-derived the same expression to build its own
expectation — so it passed against any implementation. See
`make check-orphan-tests` and CLAUDE.md's note on tests that re-implement their
subject.
"""

from __future__ import annotations

import math

from analytics.research_guards import (
    DEPLOY_SHARPE,
    GATE_DSR,
    GATE_PBO,
    GATE_SHARPE,
    min_track_record_length,
    passes_gate,
    passes_sleeve_gate,
)

# A cell that clears every leg comfortably. Each test perturbs exactly one field.
_PASSING = {
    "dsr": 0.99,
    "pbo": 0.10,
    "boot_lo": 0.05,
    "n_obs": 2000.0,
    "min_trl": 500.0,
    "sharpe_annual": 1.8,
}


class TestGateConstants:
    """Pin the thresholds by EQUALITY — `>=` would pass against any value."""

    def test_thresholds_are_exactly_the_published_values(self) -> None:
        assert GATE_DSR == 0.95
        assert GATE_PBO == 0.5
        assert GATE_SHARPE == 0.7
        assert DEPLOY_SHARPE == 1.0


class TestPassesGate:
    """The published three-leg gate, shared verbatim with the parent repo."""

    def test_all_three_legs_clear(self) -> None:
        assert passes_gate(dsr=0.99, pbo=0.1, boot_lo=0.05) is True

    def test_dsr_below_floor_fails(self) -> None:
        assert passes_gate(dsr=0.94, pbo=0.1, boot_lo=0.05) is False

    def test_dsr_exactly_at_floor_passes(self) -> None:
        assert passes_gate(dsr=GATE_DSR, pbo=0.1, boot_lo=0.05) is True

    def test_pbo_above_ceiling_fails(self) -> None:
        assert passes_gate(dsr=0.99, pbo=0.51, boot_lo=0.05) is False

    def test_pbo_exactly_at_ceiling_passes(self) -> None:
        assert passes_gate(dsr=0.99, pbo=GATE_PBO, boot_lo=0.05) is True

    def test_boot_lo_at_zero_fails(self) -> None:
        """The leg is strict: a CI touching zero is not an edge."""
        assert passes_gate(dsr=0.99, pbo=0.1, boot_lo=0.0) is False

    def test_nan_pbo_fails_explicitly(self) -> None:
        """CSCV returns NaN when the trial matrix is too small to split."""
        assert passes_gate(dsr=0.99, pbo=float("nan"), boot_lo=0.05) is False

    def test_nan_dsr_fails(self) -> None:
        assert passes_gate(dsr=float("nan"), pbo=0.1, boot_lo=0.05) is False

    def test_nan_boot_lo_fails(self) -> None:
        assert passes_gate(dsr=0.99, pbo=0.1, boot_lo=float("nan")) is False


class TestPassesSleeveGate:
    """wifey's five-leg composition — one definition for all four sleeves."""

    def test_baseline_cell_passes(self) -> None:
        assert passes_sleeve_gate(**_PASSING) is True

    def test_each_statistical_leg_alone_flips_the_verdict(self) -> None:
        for field, bad in (("dsr", 0.94), ("pbo", 0.51), ("boot_lo", -0.01)):
            assert passes_sleeve_gate(**{**_PASSING, field: bad}) is False, field

    def test_min_trl_leg_alone_flips_the_verdict(self) -> None:
        """The leg the parent's gate deliberately excludes."""
        assert passes_sleeve_gate(**{**_PASSING, "min_trl": 2001.0}) is False

    def test_infinite_min_trl_fails(self) -> None:
        """`min_track_record_length` returns `inf` below the target Sharpe."""
        assert passes_sleeve_gate(**{**_PASSING, "min_trl": float("inf")}) is False

    def test_sharpe_leg_alone_flips_the_verdict(self) -> None:
        assert passes_sleeve_gate(**{**_PASSING, "sharpe_annual": 0.69}) is False

    def test_sharpe_exactly_at_bar_passes(self) -> None:
        assert passes_sleeve_gate(**{**_PASSING, "sharpe_annual": GATE_SHARPE}) is True

    def test_gate_sharpe_override_is_honoured(self) -> None:
        cell = {**_PASSING, "sharpe_annual": 0.8}
        assert passes_sleeve_gate(**cell) is True
        assert passes_sleeve_gate(**cell, gate_sharpe=1.5) is False


class TestMinTrlImposesAnUndeclaredBar:
    """Positive control on the divergence documented in the module docstring.

    The sleeves declare `GATE_SHARPE = 0.7` as their pre-registered bar, but
    call `min_track_record_length` with a target of annualized Sharpe 1.0. A
    sample at or below that target can never confirm it, so the MinTRL leg
    rejects the whole 0.7–1.58 band that the declared bar admits. If someone
    changes the target, these assertions are what says so.
    """

    ANN = 252.0**0.5

    def _min_trl_at(self, annualized_sharpe: float) -> float:
        return min_track_record_length(
            annualized_sharpe / self.ANN,
            target_sr=1.0 / self.ANN,
            confidence=0.95,
        )

    def test_min_trl_is_infinite_at_or_below_the_target(self) -> None:
        for ann_sr in (0.3, 0.7, 1.0):
            assert math.isinf(self._min_trl_at(ann_sr)), ann_sr

    def test_a_cell_at_the_declared_bar_is_rejected_by_min_trl(self) -> None:
        """Sharpe 0.7 clears every bar the code NAMES and still fails."""
        assert (
            passes_sleeve_gate(
                dsr=0.99,
                pbo=0.10,
                boot_lo=0.05,
                n_obs=2000.0,
                min_trl=self._min_trl_at(GATE_SHARPE),
                sharpe_annual=GATE_SHARPE,
            )
            is False
        )

    def test_the_effective_bar_at_daily_depth_is_far_above_the_declared_one(
        self,
    ) -> None:
        assert self._min_trl_at(1.5) > 2000.0  # still rejected at n=2000
        assert self._min_trl_at(1.7) <= 2000.0  # accepted
