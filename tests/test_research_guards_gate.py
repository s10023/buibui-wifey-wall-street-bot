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
    deflated_sharpe_ratio,
    min_track_record_length,
    passes_gate,
    passes_sleeve_gate,
)

# A cell that clears every leg comfortably. Each test perturbs exactly one field.
_PASSING = {
    "dsr": 0.99,
    "pbo": 0.10,
    "boot_lo": 0.05,
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
    """wifey's four-leg composition — one definition for all four sleeves."""

    def test_baseline_cell_passes(self) -> None:
        assert passes_sleeve_gate(**_PASSING) is True

    def test_each_statistical_leg_alone_flips_the_verdict(self) -> None:
        for field, bad in (("dsr", 0.94), ("pbo", 0.51), ("boot_lo", -0.01)):
            assert passes_sleeve_gate(**{**_PASSING, field: bad}) is False, field

    def test_sharpe_leg_alone_flips_the_verdict(self) -> None:
        assert passes_sleeve_gate(**{**_PASSING, "sharpe_annual": 0.69}) is False

    def test_sharpe_exactly_at_bar_passes(self) -> None:
        assert passes_sleeve_gate(**{**_PASSING, "sharpe_annual": GATE_SHARPE}) is True

    def test_gate_sharpe_override_is_honoured(self) -> None:
        cell = {**_PASSING, "sharpe_annual": 0.8}
        assert passes_sleeve_gate(**cell) is True
        assert passes_sleeve_gate(**cell, gate_sharpe=1.5) is False

    def test_declared_bar_is_now_the_effective_bar(self) -> None:
        """The point of dropping the MinTRL leg: 0.7 means 0.7.

        Under the five-leg gate this cell was rejected by a leg no threshold
        named — MinTRL's target was annualized Sharpe 1.0, so anything at or
        below 1.0 got `inf`.
        """
        assert passes_sleeve_gate(**{**_PASSING, "sharpe_annual": 0.75}) is True

    def test_min_trl_is_not_an_argument(self) -> None:
        """Re-adding the leg must be deliberate, not a default that creeps back."""
        try:
            passes_sleeve_gate(**_PASSING, min_trl=1e9, n_obs=1.0)  # type: ignore[call-arg]
        except TypeError:
            return
        raise AssertionError("passes_sleeve_gate silently accepted a min_trl leg")


class TestMinTrlWasDroppedForTwoDistinctReasons:
    """Positive control on BOTH halves of the 2026-08-12 decision.

    Neither target for `min_track_record_length` gives a usable gate leg, and
    they fail in opposite directions. If someone re-adds the leg, whichever
    target they choose, one of these tests is the one that explains why not.
    """

    ANN = 252.0**0.5

    def _min_trl_at(self, annualized_sharpe: float, target: float) -> float:
        return min_track_record_length(
            annualized_sharpe / self.ANN,
            target_sr=target / self.ANN,
            confidence=0.95,
        )

    def test_target_one_is_infinite_at_or_below_the_target(self) -> None:
        """Reason 1: at target 1.0 the leg imposed an undeclared ~1.585 bar."""
        for ann_sr in (0.3, 0.7, 1.0):
            assert math.isinf(self._min_trl_at(ann_sr, target=1.0)), ann_sr
        # ...and the band it silently rejected sat well above the declared 0.7
        assert self._min_trl_at(1.5, target=1.0) > 2000.0
        assert self._min_trl_at(1.7, target=1.0) <= 2000.0

    def test_target_zero_is_strictly_implied_by_the_dsr_leg(self) -> None:
        """Reason 2: at target 0 the leg can never fire, so it is not a guard.

        MinTRL round-trips with PSR, and DSR *is* PSR with the benchmark set to
        the expected-max Sharpe (never negative). So `dsr >= 0.95` already
        implies `min_trl(0) <= n_obs`.
        """
        checked = 0
        for ann_sr in (0.8, 1.0, 1.4, 2.0, 3.0):
            for n_obs in (250, 500, 1000, 2000, 3000):
                sr_d = ann_sr / self.ANN
                dsr = deflated_sharpe_ratio(
                    sr_d, n_obs, trial_srs=[0.0, 0.01, -0.01, 0.02]
                )
                if dsr < GATE_DSR:
                    continue
                checked += 1
                assert self._min_trl_at(ann_sr, target=0.0) <= n_obs, (ann_sr, n_obs)
        assert checked > 0, "fixture produced no DSR-passing cells to check"


class TestRecordedVerdictsAreUnchanged:
    """Dropping a conjunct is monotone — it can only turn False into True.

    So the only cells at risk from this change are ones that cleared all four
    remaining legs and were blocked *solely* by MinTRL. None of the four
    recorded sleeve verdicts is such a cell: each fails on two or more legs this
    change does not touch.

    Values are the published per-sleeve numbers from CLAUDE.md's verdict table.
    Where a leg's value was never published it is set FAVOURABLY (as if it
    passed), so each assertion rests only on the legs that were actually
    recorded.
    """

    # (label, dsr, pbo, boot_lo, sharpe_annual)
    RECORDED = [
        ("xsmom residual broad_residual_skip", 0.44, 0.0, -0.01, 0.15),
        ("lowvol committed cell", 0.03, 0.0, 1.0, -0.069),
        ("xasset broad_ls", 1.0, 0.79, 1.0, 0.36),
        ("pead broad_ls", 0.20, 0.0, 1.0, 0.10),
    ]

    def test_every_recorded_sleeve_cell_still_fails(self) -> None:
        for label, dsr, pbo, boot_lo, sharpe in self.RECORDED:
            assert (
                passes_sleeve_gate(
                    dsr=dsr, pbo=pbo, boot_lo=boot_lo, sharpe_annual=sharpe
                )
                is False
            ), label

    def test_each_recorded_cell_fails_on_more_than_one_leg(self) -> None:
        """Robustness: no verdict hangs on a single leg, so none is fragile."""
        for label, dsr, pbo, boot_lo, sharpe in self.RECORDED:
            failing = sum(
                [
                    dsr < GATE_DSR,
                    pbo > GATE_PBO,
                    boot_lo <= 0.0,
                    sharpe < GATE_SHARPE,
                ]
            )
            assert failing >= 2, f"{label} fails on only {failing} leg(s)"
