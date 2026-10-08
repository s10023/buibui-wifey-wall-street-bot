"""Tests for `analytics/research_guards/power.py` — the effect-size bar.

The 16-cell grid below is an equivalence pin, not a sample. It was measured on
2026-08-14 against the two pre-promotion implementations
(`tools/era_power_price.required_sharpe` and `tools/multi_regime_power.required_sr`),
which agreed to 0.0000%. Pinning it turns that agreement from an observation
into an invariant: if a future edit moves any of these, the bar and the gate
have silently drifted apart.
"""

from __future__ import annotations

import math

import pytest

from analytics.research_guards import expected_max_sharpe, required_sharpe

# (n_obs, n_trials, sr_variance) -> required Sharpe, measured 2026-08-14.
PIN: dict[tuple[int, int, float], float] = {
    (200, 1, 0.0): 0.116999,
    (200, 16, 0.05): 0.527028,
    (200, 44, 0.05): 0.625443,
    (200, 320, 0.05): 0.785352,
    (1000, 1, 0.0): 0.052076,
    (1000, 16, 0.05): 0.457288,
    (1000, 44, 0.05): 0.553837,
    (1000, 320, 0.05): 0.710213,
    (4000, 1, 0.0): 0.026015,
    (4000, 16, 0.05): 0.429779,
    (4000, 44, 0.05): 0.525698,
    (4000, 320, 0.05): 0.680847,
    (20000, 1, 0.0): 0.011632,
    (20000, 16, 0.05): 0.414715,
    (20000, 44, 0.05): 0.510313,
    (20000, 320, 0.05): 0.664831,
}


@pytest.mark.parametrize(("key", "expected"), sorted(PIN.items()))
def test_multiplicity_path_matches_the_pin(
    key: tuple[int, int, float], expected: float
) -> None:
    n_obs, n_trials, sr_variance = key
    got = required_sharpe(n_obs, n_trials=n_trials, sr_variance=sr_variance)
    assert got == pytest.approx(expected, abs=5e-6)


@pytest.mark.parametrize(("key", "expected"), sorted(PIN.items()))
def test_benchmark_path_agrees_with_multiplicity_path(
    key: tuple[int, int, float], expected: float
) -> None:
    """The two entry paths must not disagree — that is the whole point."""
    n_obs, n_trials, sr_variance = key
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    got = required_sharpe(n_obs, benchmark_sr=sr0)
    assert got == pytest.approx(expected, abs=5e-6)


def test_rises_with_trial_count() -> None:
    """Trial count dominates n — the bar must move a long way on it."""
    one = required_sharpe(4000, n_trials=1, sr_variance=0.05)
    many = required_sharpe(4000, n_trials=320, sr_variance=0.05)
    assert many > one * 20


def test_falls_with_sample_size() -> None:
    assert required_sharpe(20000, n_trials=16, sr_variance=0.05) < required_sharpe(
        200, n_trials=16, sr_variance=0.05
    )


def test_unreachable_returns_inf_not_a_big_number() -> None:
    """A bar no Sharpe can clear is a finding, and `inf` is how it is said.

    The PSR z-statistic is bounded above by ~sqrt(2*(n_obs-1)), so at small
    `n_obs` the gate is unreachable at ANY Sharpe rather than merely large.
    """
    assert required_sharpe(2, n_trials=320, sr_variance=0.05) == math.inf


def test_n_obs_below_two_is_unreachable() -> None:
    assert required_sharpe(1, n_trials=1, sr_variance=0.0) == math.inf


def test_rejects_both_benchmark_sources() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        required_sharpe(1000, n_trials=16, sr_variance=0.05, benchmark_sr=0.4)


def test_rejects_neither_benchmark_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        required_sharpe(1000)


def test_rejects_half_a_multiplicity_pair() -> None:
    with pytest.raises(ValueError, match="both required"):
        required_sharpe(1000, n_trials=16)
