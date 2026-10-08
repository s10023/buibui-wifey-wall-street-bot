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
    days: list[int] | None = None,
) -> AuditCell:
    rng = np.random.default_rng(seed)
    supp = rng.normal(mean, std, n).tolist()
    # One trade per day unless a test says otherwise: these cases predate the
    # cluster unit and are about the verdict logic, so they must stay UNclustered
    # or the deflator would silently change what they measure.
    return AuditCell(
        label=label,
        supp_r=supp,
        cluster_key=days if days is not None else list(range(n)),
        kept_r=kept or [],
    )


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
        cell = AuditCell(
            label="z",
            supp_r=[-1.0] * 40,
            cluster_key=list(range(40)),
            kept_r=[1.0] * 10,
        )
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_ENABLE

    def test_zero_variance_deterministic_win_disables(self) -> None:
        cell = AuditCell(
            label="z",
            supp_r=[1.0] * 40,
            cluster_key=list(range(40)),
            kept_r=[-1.0] * 10,
        )
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


def test_powered_null_requires_the_ci_inside_the_bar() -> None:
    """A powered null is CI CONTAINMENT, never ``n >= min_n``.

    Both cells below have the SAME mean (0.0) and the SAME n (50, well past
    ``min_n``), so any criterion keyed on sample size calls both "powered".
    They differ only in dispersion, which is the thing that decides whether an
    effect the size of the bar has actually been ruled out.
    """
    # NOT an alternating [+x, -x] sequence: every even-length block of one of
    # those averages to exactly 0, so the circular block bootstrap returns
    # CI [0, 0] however large x is, and the "wide" cell is not wide at all.
    # Same shape for both, differing only by a factor of 100 in scale.
    tight = audit_guard.AuditCell("tight", [-0.01] * 25 + [0.01] * 25, list(range(50)))
    wide = audit_guard.AuditCell("wide", [-1.0] * 25 + [1.0] * 25, list(range(50)))

    tv, wv = audit_guard.evaluate_audit_cells([tight, wide], bar=0.05)

    # Positive control: the fixture must actually produce a wide CI, or the
    # discrimination below is vacuous regardless of what the assertions say.
    assert wv.ci_lo is not None and wv.ci_hi is not None
    assert wv.ci_hi - wv.ci_lo > 0.05

    # Neither clears the bar, so both are INSUFFICIENT decisions...
    assert tv.decision == audit_guard.DECISION_INSUFFICIENT
    assert wv.decision == audit_guard.DECISION_INSUFFICIENT
    assert tv.n_supp == wv.n_supp == 50  # positive control: n cannot separate them

    # ...but only the tight cell has ruled out an effect at the bar.
    assert tv.powered_null is True
    assert wv.powered_null is False


def test_powered_null_is_false_when_the_cell_was_never_tested() -> None:
    """Below ``min_n`` no CI is computed, so containment is unknowable."""
    (v,) = audit_guard.evaluate_audit_cells(
        [audit_guard.AuditCell("thin", [0.0] * 5, list(range(5)))],
        bar=0.05,
        min_n=30,
    )
    assert v.decision == audit_guard.DECISION_INSUFFICIENT
    assert v.ci_lo is None
    assert v.powered_null is False


# --------------------------------------------------------------------------
# powered_null — the extracted criterion, tested directly
# --------------------------------------------------------------------------
#
# The two cases above reach the predicate through `evaluate_audit_cells`, which
# only ever hands it two finite floats. The guard clause is therefore reachable
# only from the other callers (`tools/distil_power.py`), so it needs its own
# tests here rather than a fixture that cannot deliver the input.


def test_powered_null_true_only_when_ci_inside_bar() -> None:
    # Positive control: a genuinely contained CI. Without one of these the suite
    # cannot distinguish "correct" from "always False".
    assert audit_guard.powered_null(-0.02, 0.03, bar=0.05) is True
    # Touching the bar is not inside it — the predicate is strict on both ends.
    assert audit_guard.powered_null(-0.05, 0.03, bar=0.05) is False
    assert audit_guard.powered_null(-0.02, 0.05, bar=0.05) is False
    # Straddles the bar on one side.
    assert audit_guard.powered_null(-0.02, 0.30, bar=0.05) is False


def test_powered_null_untested_bound_is_never_powered() -> None:
    """A missing or non-finite bound means untested, which establishes nothing."""
    assert audit_guard.powered_null(None, 0.01, bar=0.05) is False
    assert audit_guard.powered_null(-0.01, None, bar=0.05) is False
    assert audit_guard.powered_null(float("-inf"), 0.01, bar=0.05) is False
    assert audit_guard.powered_null(-0.01, float("nan"), bar=0.05) is False


def test_powered_null_scales_with_the_bar() -> None:
    """``bar`` carries the units, so the same CI flips with it."""
    assert audit_guard.powered_null(-0.10, 0.10, bar=0.05) is False
    assert audit_guard.powered_null(-0.10, 0.10, bar=0.50) is True


# --------------------------------------------------------------------------
# the cluster unit — required, fail-closed, and applied to BOTH legs
# --------------------------------------------------------------------------


class TestClusterKeyFailsClosed:
    """An unusable key must cost the verdict, never the guard.

    Falling back to per-trade resampling would report a confident, undeflated
    answer — the fail-open shape this repo already closed in
    ``tools/distil_power.py``, where a deflator was accepted but not applied.
    """

    def test_length_mismatch_is_insufficient_and_says_why(self) -> None:
        cell = AuditCell("bad", [-0.6] * 40, cluster_key=[1, 2, 3])
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_INSUFFICIENT
        assert v.ci_lo is None and v.adj_pvalue is None
        assert any("cluster_key length 3 != n_supp 40" in r for r in v.reasons)

    def test_the_same_cell_with_a_valid_key_is_not_insufficient(self) -> None:
        """Positive control: without it, the test above passes on any refusal."""
        good = _normal_cell(-0.6, 0.7, 80, seed=1)
        [v] = audit_guard.evaluate_audit_cells([good], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_ENABLE

    def test_a_refused_cell_leaves_the_holm_family(self) -> None:
        """It must not inflate the denominator every sibling is haircut by."""
        good = _normal_cell(-0.6, 0.7, 80, seed=1, label="good")
        bad = AuditCell("bad", [-0.6] * 40, cluster_key=[1])
        verdicts = audit_guard.evaluate_audit_cells([good, bad], **_KW)  # type: ignore[arg-type]
        assert verdicts[0].n_tests == 1
        assert verdicts[1].decision == DECISION_INSUFFICIENT


def _day_clustered(
    n_days: int, per_day: int, *, seed: int, mu: float = -0.3, sd: float = 0.5
) -> tuple[list[float], list[int]]:
    """Trades whose signal is carried by the DAY, not by the trade.

    Each day draws one common effect and every trade that day sits on it with
    little independent noise — the shape the audit measured (median ICC 0.598).

    The defaults are deliberately marginal. An overwhelming effect
    survives the correction and should: at ``mu=-0.6, sd=0.7`` this fixture
    still earns ENABLE on the day unit (CI ``[-1.23, -0.16]``, adj-p 0.019)
    even though the CI widens 3x and the p-value moves 11 orders of magnitude.
    The fix removes *unsupported* confidence, not every verdict, so a flip test
    has to be built on a cell that was genuinely borderline — which is exactly
    the population the audit found losing significance.
    """
    rng = np.random.default_rng(seed)
    day_means = rng.normal(mu, sd, n_days)
    vals: list[float] = []
    keys: list[int] = []
    for d in range(n_days):
        vals.extend((day_means[d] + rng.normal(0.0, 0.05, per_day)).tolist())
        keys.extend([d] * per_day)
    return vals, keys


class TestClusteringChangesTheVerdict:
    def test_day_clustered_evidence_no_longer_clears_the_gates(self) -> None:
        """The acceptance test for the whole fix.

        IDENTICAL values both times — only the key changes — so the flip is the
        resampling and deflation unit and nothing else. Undeflated, 80 trades on
        8 days read as 80 independent draws and the cell earns a verdict; on the
        day unit it is 8 draws and does not.
        """
        vals, days = _day_clustered(8, 10, seed=0)
        honest = AuditCell("honest", vals, cluster_key=days)
        naive = AuditCell("naive", vals, cluster_key=list(range(len(vals))))

        [hv] = audit_guard.evaluate_audit_cells([honest], **_KW)  # type: ignore[arg-type]
        [nv] = audit_guard.evaluate_audit_cells([naive], **_KW)  # type: ignore[arg-type]

        # Positive control: the naive reading really does earn a verdict, or
        # "the honest one does not" is vacuous.
        assert nv.decision == DECISION_ENABLE

        assert hv.decision == DECISION_INSUFFICIENT
        assert hv.n_supp == nv.n_supp == 80  # a size rule cannot separate them

    def test_both_legs_move_in_the_safe_direction(self) -> None:
        """CI wider AND p-value larger — the two channels the audit measured."""
        vals, days = _day_clustered(8, 10, seed=0)
        honest = AuditCell("honest", vals, cluster_key=days)
        naive = AuditCell("naive", vals, cluster_key=list(range(len(vals))))

        [hv] = audit_guard.evaluate_audit_cells([honest], **_KW)  # type: ignore[arg-type]
        [nv] = audit_guard.evaluate_audit_cells([naive], **_KW)  # type: ignore[arg-type]

        assert hv.ci_lo is not None and hv.ci_hi is not None
        assert nv.ci_lo is not None and nv.ci_hi is not None
        assert (hv.ci_hi - hv.ci_lo) > (nv.ci_hi - nv.ci_lo)

        assert hv.adj_pvalue is not None and nv.adj_pvalue is not None
        assert hv.adj_pvalue > nv.adj_pvalue

    def test_the_deflation_is_reported_not_silent(self) -> None:
        """A deflator applied silently is indistinguishable from one forgotten."""
        vals, days = _day_clustered(8, 10, seed=0)
        [hv] = audit_guard.evaluate_audit_cells(
            [AuditCell("honest", vals, cluster_key=days)],
            **_KW,  # type: ignore[arg-type]
        )
        assert hv.n_clusters == 8
        assert hv.design_effect is not None and hv.design_effect > 1.0

        [nv] = audit_guard.evaluate_audit_cells(
            [AuditCell("naive", vals, cluster_key=list(range(len(vals))))],
            **_KW,  # type: ignore[arg-type]
        )
        assert nv.n_clusters == 80
        assert nv.design_effect == 1.0


class TestSessionDayKeys:
    def test_intraday_stamps_collapse_to_one_key(self) -> None:
        """13:30 and 20:00 UTC are the same RTH session, so the same cluster."""
        base = 20_000 * audit_guard.MS_PER_DAY
        open_utc = base + 13 * 3_600_000 + 30 * 60_000  # 09:30 EDT
        close_utc = base + 20 * 3_600_000  # 16:00 EDT
        assert audit_guard.session_day_keys([open_utc, close_utc]) == [20_000, 20_000]

    def test_winter_session_also_stays_inside_one_utc_date(self) -> None:
        """EST shifts RTH to 14:30-21:00 UTC — still one UTC day, so no ET math."""
        base = 20_100 * audit_guard.MS_PER_DAY
        open_utc = base + 14 * 3_600_000 + 30 * 60_000  # 09:30 EST
        close_utc = base + 21 * 3_600_000  # 16:00 EST
        assert audit_guard.session_day_keys([open_utc, close_utc]) == [20_100, 20_100]

    def test_consecutive_days_are_distinct_keys(self) -> None:
        d = audit_guard.MS_PER_DAY
        assert audit_guard.session_day_keys([5 * d, 6 * d]) == [5, 6]
