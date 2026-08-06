"""Tests for analytics/pundit_direction.py — the ledger direction enum guard.

Parity/prevention, not a repair: unlike the parent, this fork's ``score_call``
already gated an out-of-enum direction to UNSCORED. These lock the enum to the
branch that spells it inline, so the two cannot drift.
"""

from __future__ import annotations

import pytest

from analytics.pundit_direction import VALID_DIRECTIONS, normalize_direction


class TestNormalizeDirection:
    def test_accepts_the_three_scored_values(self) -> None:
        assert normalize_direction("long") == "long"
        assert normalize_direction("short") == "short"
        assert normalize_direction("neutral") == "neutral"

    def test_folds_case_and_strips_whitespace(self) -> None:
        assert normalize_direction("  LONG ") == "long"
        assert normalize_direction("Short") == "short"
        assert normalize_direction("Neutral\n") == "neutral"

    def test_rejects_a_fourth_value_and_names_it(self) -> None:
        # The parent's measured case: /ingest-video emitted "range" for a
        # range-trade plan. Upstream that scored as a SHORT; here it was
        # already UNSCORED, and now it never loads.
        with pytest.raises(ValueError, match="range"):
            normalize_direction("range")

    def test_rejects_empty_and_missing(self) -> None:
        with pytest.raises(ValueError):
            normalize_direction("")
        with pytest.raises(ValueError):
            normalize_direction("   ")

    def test_error_names_the_field_so_a_ledger_warning_is_actionable(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            normalize_direction("sideways")

    def test_valid_directions_is_exactly_the_enum_score_call_branches_on(self) -> None:
        # score_call gates `direction not in ("long", "short")` to UNSCORED and
        # then sets dirsign off "long". That tuple is spelled inline, so this
        # is the only thing tying it to the enum. If this set grows, that
        # branch must be revisited in the same commit.
        assert frozenset({"long", "short", "neutral"}) == VALID_DIRECTIONS

    def test_is_idempotent(self) -> None:
        for value in ("long", "SHORT", " neutral "):
            once = normalize_direction(value)
            assert normalize_direction(once) == once
