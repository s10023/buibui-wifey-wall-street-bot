"""Tests for analytics/research_guards/mintrl.py."""

import math

from analytics.research_guards.mintrl import min_track_record_length
from analytics.research_guards.psr import probabilistic_sharpe_ratio


class TestMinTrackRecordLength:
    def test_unreachable_when_sr_not_above_target(self) -> None:
        assert math.isinf(min_track_record_length(0.1, target_sr=0.1))
        assert math.isinf(min_track_record_length(0.05, target_sr=0.1))

    def test_higher_sr_needs_fewer_obs(self) -> None:
        assert min_track_record_length(0.8) < min_track_record_length(0.2)

    def test_higher_confidence_needs_more_obs(self) -> None:
        strict = min_track_record_length(0.5, confidence=0.99)
        loose = min_track_record_length(0.5, confidence=0.90)
        assert strict > loose

    def test_round_trip_against_psr(self) -> None:
        sr, conf = 0.5, 0.95
        n = math.ceil(min_track_record_length(sr, confidence=conf))
        assert probabilistic_sharpe_ratio(sr, n) >= conf
