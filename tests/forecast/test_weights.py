"""Tests for analytics.forecast.weights.candidate_schemes."""

from __future__ import annotations

from analytics.forecast.config import ForecastConfig
from analytics.forecast.weights import WeightScheme, candidate_schemes


def test_all_schemes_sum_to_one() -> None:
    for s in candidate_schemes(ForecastConfig()).values():
        assert abs(sum(s.weights) - 1.0) < 1e-12


def test_scheme_lengths_match_speeds() -> None:
    cfg = ForecastConfig()
    for s in candidate_schemes(cfg).values():
        assert len(s.weights) == len(cfg.speeds)


def test_a_priori_flags() -> None:
    schemes = candidate_schemes(ForecastConfig())
    assert schemes["equal"].a_priori is True
    assert schemes["inverse_cost"].a_priori is True
    assert schemes["fast_tilt_linear"].a_priori is False
    assert schemes["fast_tilt_geom"].a_priori is False
    assert schemes["drop_two_slowest"].a_priori is False
    assert schemes["fast_only"].a_priori is False


def test_fast_only_zeros_all_but_first() -> None:
    w = candidate_schemes(ForecastConfig())["fast_only"].weights
    assert w[0] == 1.0 and all(x == 0.0 for x in w[1:])


def test_drop_two_slowest_zeros_slow_half() -> None:
    w = candidate_schemes(ForecastConfig())["drop_two_slowest"].weights
    assert w[0] > 0.0 and w[1] > 0.0 and w[2] == 0.0 and w[3] == 0.0


def test_fast_tilt_geom_strictly_decreasing() -> None:
    w = candidate_schemes(ForecastConfig())["fast_tilt_geom"].weights
    assert w[0] > w[1] > w[2] > w[3]


def test_inverse_cost_favours_slow_leg() -> None:
    # slower legs trade less (cheaper) -> a-priori cost logic weights them up
    w = candidate_schemes(ForecastConfig())["inverse_cost"].weights
    assert w[-1] > w[0]


def test_weightscheme_is_namedtuple_shape() -> None:
    s = candidate_schemes(ForecastConfig())["equal"]
    assert isinstance(s, WeightScheme)
    assert len(s.weights) == 4 and isinstance(s.a_priori, bool)
