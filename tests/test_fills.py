"""Tests for analytics/backtest/fills.py — the shared gap-fill rule. Pure, no I/O."""

from __future__ import annotations

import pytest

from analytics.backtest.fills import (
    gap_fill_price,
    level_is_on_the_expected_side,
    triggers_downward,
)


class TestTriggersDownward:
    """A long's stop and a short's target are the two reached by a falling price."""

    @pytest.mark.parametrize(
        ("direction", "side", "expected"),
        [
            ("long", "stop", True),
            ("long", "target", False),
            ("short", "stop", False),
            ("short", "target", True),
        ],
    )
    def test_all_four_cases(self, direction: str, side: str, expected: bool) -> None:
        assert triggers_downward(direction=direction, side=side) is expected


class TestGapFillPrice:
    """All four legs, each gapped and not — the table this module exists to get right."""

    def test_long_stop_gapped_fills_at_the_worse_open(self) -> None:
        assert gap_fill_price(
            level=95.0, bar_open=92.0, direction="long", side="stop"
        ) == pytest.approx(92.0)

    def test_long_stop_not_gapped_fills_at_the_level(self) -> None:
        assert gap_fill_price(
            level=95.0, bar_open=99.0, direction="long", side="stop"
        ) == pytest.approx(95.0)

    def test_long_target_gapped_fills_at_the_better_open(self) -> None:
        assert gap_fill_price(
            level=110.0, bar_open=114.0, direction="long", side="target"
        ) == pytest.approx(114.0)

    def test_long_target_not_gapped_fills_at_the_level(self) -> None:
        assert gap_fill_price(
            level=110.0, bar_open=104.0, direction="long", side="target"
        ) == pytest.approx(110.0)

    def test_short_stop_gapped_fills_at_the_worse_open(self) -> None:
        assert gap_fill_price(
            level=105.0, bar_open=108.0, direction="short", side="stop"
        ) == pytest.approx(108.0)

    def test_short_stop_not_gapped_fills_at_the_level(self) -> None:
        assert gap_fill_price(
            level=105.0, bar_open=101.0, direction="short", side="stop"
        ) == pytest.approx(105.0)

    def test_short_target_gapped_fills_at_the_better_open(self) -> None:
        assert gap_fill_price(
            level=90.0, bar_open=86.0, direction="short", side="target"
        ) == pytest.approx(86.0)

    def test_short_target_not_gapped_fills_at_the_level(self) -> None:
        assert gap_fill_price(
            level=90.0, bar_open=94.0, direction="short", side="target"
        ) == pytest.approx(90.0)

    def test_open_exactly_at_the_level_counts_as_gapped_and_is_a_no_op(self) -> None:
        """The boundary resolves to the same number either way, so it cannot bite."""
        for direction, side in (
            ("long", "stop"),
            ("long", "target"),
            ("short", "stop"),
            ("short", "target"),
        ):
            got = gap_fill_price(
                level=100.0, bar_open=100.0, direction=direction, side=side
            )
            assert got == pytest.approx(100.0)


class TestGapFillIsNeverBetterThanReality:
    """The property that makes the fix a de-biasing rather than a new bias.

    An adverse gap must never fill better than its level, and a favourable gap
    must never fill worse. Stated as a property because the four-case table above
    can be individually right and jointly asymmetric — which is exactly the shape
    of the one-sided fix this module was written to prevent.
    """

    @pytest.mark.parametrize("bar_open", [80.0, 94.9, 95.0, 95.1, 120.0])
    def test_long_stop_fill_never_exceeds_the_level(self, bar_open: float) -> None:
        assert (
            gap_fill_price(level=95.0, bar_open=bar_open, direction="long", side="stop")
            <= 95.0
        )

    @pytest.mark.parametrize("bar_open", [80.0, 109.9, 110.0, 110.1, 130.0])
    def test_long_target_fill_never_falls_below_the_level(
        self, bar_open: float
    ) -> None:
        assert (
            gap_fill_price(
                level=110.0, bar_open=bar_open, direction="long", side="target"
            )
            >= 110.0
        )

    @pytest.mark.parametrize("bar_open", [80.0, 104.9, 105.0, 105.1, 130.0])
    def test_short_stop_fill_never_falls_below_the_level(self, bar_open: float) -> None:
        assert (
            gap_fill_price(
                level=105.0, bar_open=bar_open, direction="short", side="stop"
            )
            >= 105.0
        )

    @pytest.mark.parametrize("bar_open", [70.0, 89.9, 90.0, 90.1, 120.0])
    def test_short_target_fill_never_exceeds_the_level(self, bar_open: float) -> None:
        assert (
            gap_fill_price(
                level=90.0, bar_open=bar_open, direction="short", side="target"
            )
            <= 90.0
        )


class TestLevelIsOnTheExpectedSide:
    """The guard that keeps a malformed row from being priced as a gap."""

    @pytest.mark.parametrize(
        ("level", "direction", "side", "expected"),
        [
            (95.0, "long", "stop", True),
            (105.0, "long", "stop", False),
            (110.0, "long", "target", True),
            (90.0, "long", "target", False),
            (105.0, "short", "stop", True),
            (95.0, "short", "stop", False),
            (90.0, "short", "target", True),
            (110.0, "short", "target", False),
        ],
    )
    def test_all_eight_cases(
        self, level: float, direction: str, side: str, expected: bool
    ) -> None:
        assert (
            level_is_on_the_expected_side(
                level=level, entry=100.0, direction=direction, side=side
            )
            is expected
        )

    def test_a_level_equal_to_entry_is_not_on_either_side(self) -> None:
        """Strict comparison both ways: a zero-distance level is malformed, not valid."""
        for direction, side in (
            ("long", "stop"),
            ("long", "target"),
            ("short", "stop"),
            ("short", "target"),
        ):
            assert (
                level_is_on_the_expected_side(
                    level=100.0, entry=100.0, direction=direction, side=side
                )
                is False
            )
