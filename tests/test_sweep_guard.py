"""Tests for analytics/sweep_guard.py — the param-sweep commit gate."""

import math

import numpy as np
import pytest

from analytics.sweep_guard import (
    CommitGateVerdict,
    TrialPerf,
    _build_perf_matrix,
    _decide,
    _trial_sharpe,
    evaluate_commit_gate,
)


class TestTrialSharpe:
    def test_too_few_returns(self) -> None:
        assert _trial_sharpe([]) == 0.0
        assert _trial_sharpe([1.0]) == 0.0

    def test_zero_std(self) -> None:
        assert _trial_sharpe([1.0, 1.0, 1.0]) == 0.0

    def test_mean_over_std(self) -> None:
        returns = [2.0, 1.0, 1.0, -1.0]
        sd = float(np.std(returns, ddof=1))
        assert _trial_sharpe(returns) == pytest.approx(0.75 / sd)

    def test_scale_invariant(self) -> None:
        base = [2.0, 1.0, 1.0, -1.0]
        scaled = [r * 3.0 for r in base]
        assert _trial_sharpe(base) == pytest.approx(_trial_sharpe(scaled))


class TestBuildPerfMatrix:
    def test_shape_and_binning(self) -> None:
        t1 = TrialPerf("a", [1.0, 2.0], [0, 10])
        t2 = TrialPerf("b", [3.0], [5])
        m = _build_perf_matrix([t1, t2], 4)
        assert m.shape == (4, 2)
        # span 0..10 over 4 bins: t=0->0, t=10->clamped 3, t=5->2
        assert m[0, 0] == 1.0
        assert m[3, 0] == 2.0
        assert m[2, 1] == 3.0

    def test_sums_within_bin(self) -> None:
        # span 0..100 over 4 bins (width 25): t=0 and t=1 both land in bin 0,
        # t=100 (the max) clamps to the last bin.
        t1 = TrialPerf("a", [1.0, 0.5, 9.0], [0, 1, 100])
        m = _build_perf_matrix([t1], 4)
        assert m[0, 0] == pytest.approx(1.5)
        assert m[3, 0] == pytest.approx(9.0)

    def test_no_times_returns_zeros(self) -> None:
        t1 = TrialPerf("a", [], [])
        m = _build_perf_matrix([t1, t1], 4)
        assert m.shape == (4, 2)
        assert float(np.sum(m)) == 0.0


class TestDecide:
    def test_all_pass_commits(self) -> None:
        decision, reasons = _decide(
            dsr=0.97,
            pbo=0.30,
            min_trl=10.0,
            n_obs=40,
            dsr_threshold=0.95,
            pbo_threshold=0.5,
        )
        assert decision == "COMMIT"
        assert reasons == []

    def test_low_dsr_blocks(self) -> None:
        decision, reasons = _decide(
            dsr=0.80,
            pbo=0.3,
            min_trl=10.0,
            n_obs=40,
            dsr_threshold=0.95,
            pbo_threshold=0.5,
        )
        assert decision == "DO_NOT_COMMIT"
        assert any("DSR" in r for r in reasons)

    def test_high_pbo_blocks(self) -> None:
        decision, reasons = _decide(
            dsr=0.97,
            pbo=0.6,
            min_trl=10.0,
            n_obs=40,
            dsr_threshold=0.95,
            pbo_threshold=0.5,
        )
        assert decision == "DO_NOT_COMMIT"
        assert any("PBO" in r for r in reasons)

    def test_short_track_record_blocks(self) -> None:
        decision, reasons = _decide(
            dsr=0.97,
            pbo=0.3,
            min_trl=100.0,
            n_obs=40,
            dsr_threshold=0.95,
            pbo_threshold=0.5,
        )
        assert decision == "DO_NOT_COMMIT"
        assert any("MinTRL" in r for r in reasons)

    def test_infinite_mintrl_blocks(self) -> None:
        decision, reasons = _decide(
            dsr=0.10,
            pbo=0.3,
            min_trl=math.inf,
            n_obs=40,
            dsr_threshold=0.95,
            pbo_threshold=0.5,
        )
        assert decision == "DO_NOT_COMMIT"


def _drift_trials() -> list[TrialPerf]:
    """5 trials, each a positive-drift shift of a strong base edge.

    Adding a positive constant raises every bin's Sharpe monotonically, so the
    highest-drift trial is the in-sample AND out-of-sample best in every CSCV
    split -> PBO ~ 0. Sharpe variance across trials is small -> little deflation.
    """
    base = [2.0, 1.0, 1.0, -1.0] * 10  # 40 obs, Sharpe ~0.69
    return [
        TrialPerf(f"t{j}", [r + 0.05 * j for r in base], list(range(40)))
        for j in range(5)
    ]


class TestEvaluateCommitGate:
    def test_strong_clean_edge_commits(self) -> None:
        trials = _drift_trials()
        chosen = trials[-1]  # highest drift = best
        v = evaluate_commit_gate(chosen, trials, n_grid=5, n_splits=4)
        assert isinstance(v, CommitGateVerdict)
        assert v.decision == "COMMIT"
        assert v.committable
        assert v.dsr is not None and v.dsr >= 0.95
        assert v.pbo is not None and v.pbo <= 0.5
        assert v.n_obs == 40

    def test_weak_edge_blocks(self) -> None:
        chosen = TrialPerf("a", [1.0] * 22 + [-1.0] * 18, list(range(40)))  # SR ~0.1
        trials = [
            chosen,
            TrialPerf("b", [0.5] * 20 + [-0.5] * 20, list(range(40))),
            TrialPerf("c", [1.0] * 21 + [-1.0] * 19, list(range(40))),
        ]
        v = evaluate_commit_gate(chosen, trials, n_grid=200, n_splits=4)
        assert v.decision == "DO_NOT_COMMIT"
        assert not v.committable
        assert v.reasons

    def test_single_trial_insufficient(self) -> None:
        t = _drift_trials()[0]
        v = evaluate_commit_gate(t, [t], n_grid=1, n_splits=4)
        assert v.decision == "INSUFFICIENT"
        assert not v.committable

    def test_thin_trade_count_insufficient(self) -> None:
        thin = TrialPerf("x", [1.0, -1.0, 1.0, 2.0, 1.0, 1.0], list(range(6)))
        other = _drift_trials()[0]
        v = evaluate_commit_gate(thin, [thin, other], n_grid=2, n_splits=4)
        assert v.decision == "INSUFFICIENT"
        assert v.n_obs == 6
