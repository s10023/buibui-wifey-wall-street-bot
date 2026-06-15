"""Tests for `analytics/audit_guard.py` — bootstrap-CI + haircut audit verdicts.

The engine replaces the crude ±0.05R bar in the audit tools with two gates that
must BOTH hold for an ENABLE/DISABLE verdict:

  1. a stationary/circular block-bootstrap CI on the suppressed slice's mean R
     must clear the ±bar on the correct side, and
  2. the Holm multiple-testing adjusted p-value (across the tested-cell family)
     must be significant (< alpha).

Cells below `min_n` are INSUFFICIENT and excluded from the haircut family.
"""

from __future__ import annotations

import numpy as np

from analytics import audit_guard
from analytics.audit_guard import (
    DECISION_CONCENTRATE,
    DECISION_DISABLE,
    DECISION_ENABLE,
    DECISION_INSUFFICIENT,
    AuditCell,
)


def _normal_cell(
    mean: float,
    std: float,
    n: int,
    *,
    seed: int,
    kept: list[float] | None = None,
    label: str = "c",
) -> AuditCell:
    rng = np.random.default_rng(seed)
    supp = rng.normal(mean, std, n).tolist()
    return AuditCell(label=label, supp_r=supp, kept_r=kept or [])


# Small n_boot keeps the suite fast; verdicts on well-separated slices are
# stable far below the production default.
_KW = {"n_boot": 1500, "seed": 7}


class TestEnableDisable:
    def test_enable_when_suppressed_slice_reliably_loses(self) -> None:
        # Noisy losers well below -bar → CI clears -bar, highly significant.
        cell = _normal_cell(-0.6, 0.7, 80, seed=1)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_ENABLE
        assert v.ci_hi is not None and v.ci_hi <= -0.05
        assert v.adj_pvalue is not None and v.adj_pvalue < 0.05

    def test_disable_when_suppressed_slice_reliably_wins(self) -> None:
        # Suppressed slice are reliable winners; kept does NOT outperform.
        kept = np.random.default_rng(2).normal(0.1, 0.7, 80).tolist()
        cell = _normal_cell(0.6, 0.7, 80, seed=3, kept=kept)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_DISABLE
        assert v.ci_lo is not None and v.ci_lo >= 0.05

    def test_insufficient_when_ci_straddles_the_bar(self) -> None:
        # Mean barely negative, wide noise → CI straddles -bar → INSUFFICIENT
        # even though the point estimate is < 0 (the OLD ±0.05R bar might fire).
        cell = _normal_cell(-0.03, 0.6, 80, seed=4)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_INSUFFICIENT


class TestConcentrate:
    def test_concentrate_when_kept_outperforms_positive_suppressed(self) -> None:
        kept = np.random.default_rng(5).normal(1.2, 0.5, 80).tolist()
        cell = _normal_cell(0.3, 0.5, 80, seed=6, kept=kept)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_CONCENTRATE

    def test_concentrate_folds_into_disable_when_disabled(self) -> None:
        kept = np.random.default_rng(5).normal(1.2, 0.5, 80).tolist()
        cell = _normal_cell(0.3, 0.5, 80, seed=6, kept=kept)
        [v] = audit_guard.evaluate_audit_cells(
            [cell],
            enable_concentrate=False,
            **_KW,  # type: ignore[arg-type]
        )
        assert v.decision == DECISION_DISABLE

    def test_no_concentrate_when_kept_does_not_clear_delta(self) -> None:
        # supp ~ +0.5, kept ~ +0.52 — delta < bar → stays DISABLE.
        kept = np.random.default_rng(7).normal(0.52, 0.4, 80).tolist()
        cell = _normal_cell(0.5, 0.4, 80, seed=8, kept=kept)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_DISABLE


class TestMinN:
    def test_below_min_n_is_insufficient_and_excluded_from_family(self) -> None:
        small = _normal_cell(-0.6, 0.7, 10, seed=9, label="small")
        big = _normal_cell(-0.6, 0.7, 80, seed=10, label="big")
        verdicts = audit_guard.evaluate_audit_cells([small, big], **_KW)  # type: ignore[arg-type]
        by_label = {c.label: v for c, v in zip([small, big], verdicts, strict=True)}
        assert by_label["small"].decision == DECISION_INSUFFICIENT
        assert by_label["small"].adj_pvalue is None  # not bootstrapped / not tested
        # `big` evaluated as the SOLE family member (n_tests == 1).
        assert by_label["big"].n_tests == 1
        assert by_label["big"].decision == DECISION_ENABLE

    def test_empty_input_returns_empty(self) -> None:
        assert audit_guard.evaluate_audit_cells([]) == []


class TestHaircut:
    def test_adj_pvalue_grows_with_family_size(self) -> None:
        # Same strong cell judged alone vs inside a 15-cell family: the Holm
        # haircut can only make the adjusted p-value larger (or equal).
        target = _normal_cell(-0.4, 0.8, 120, seed=20, label="t")
        nulls = [_normal_cell(0.0, 1.0, 120, seed=30 + i) for i in range(14)]

        [alone] = audit_guard.evaluate_audit_cells([target], **_KW)  # type: ignore[arg-type]
        family = audit_guard.evaluate_audit_cells([target, *nulls], **_KW)  # type: ignore[arg-type]
        in_family = family[0]

        assert alone.adj_pvalue is not None and in_family.adj_pvalue is not None
        assert in_family.adj_pvalue >= alone.adj_pvalue

    def test_haircut_demotes_marginal_cell_to_insufficient(self) -> None:
        # A cell whose CI comfortably clears -bar and is significant ALONE
        # (raw p ~ 0.005) is demoted to INSUFFICIENT inside a 20-cell family,
        # purely by the multiple-testing haircut (Holm ×20 ~ 0.10) — its CI
        # still clears the bar.
        target = _normal_cell(-0.5, 1.43, 60, seed=42, label="marg")
        nulls = [_normal_cell(0.0, 1.0, 60, seed=50 + i) for i in range(19)]

        [alone] = audit_guard.evaluate_audit_cells([target], **_KW)  # type: ignore[arg-type]
        assert alone.decision == DECISION_ENABLE

        family = audit_guard.evaluate_audit_cells([target, *nulls], **_KW)  # type: ignore[arg-type]
        in_family = family[0]
        assert in_family.decision == DECISION_INSUFFICIENT
        # CI unchanged by family size → the flip is the haircut, not the CI.
        assert in_family.ci_hi is not None and in_family.ci_hi <= -0.05
        assert in_family.adj_pvalue is not None and in_family.adj_pvalue >= 0.05


class TestDegenerate:
    def test_zero_variance_deterministic_loss_enables(self) -> None:
        # 40 identical -1.0R trades: no sampling uncertainty → maximally
        # significant deterministic loss → ENABLE.
        cell = AuditCell(label="z", supp_r=[-1.0] * 40, kept_r=[1.0] * 10)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_ENABLE

    def test_zero_variance_deterministic_win_disables(self) -> None:
        cell = AuditCell(label="z", supp_r=[1.0] * 40, kept_r=[-1.0] * 10)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_DISABLE


class TestMethodOption:
    def test_bonferroni_method_runs(self) -> None:
        cell = _normal_cell(-0.6, 0.7, 80, seed=1)
        [v] = audit_guard.evaluate_audit_cells(
            [cell],
            haircut_method="bonferroni",
            **_KW,  # type: ignore[arg-type]
        )
        assert v.decision == DECISION_ENABLE
