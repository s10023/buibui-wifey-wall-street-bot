"""Tests for the conflict resolver helper.

The conflict resolution block is exposed as `_apply_conflict_resolver` in
`analytics/signal/gates.py`, extracted from `scanner.run_scan_cycle`.
These tests pin the live behaviour so future ports (PR-4b backtest replay)
can reuse the helper with confidence.
"""

from __future__ import annotations

import logging

import pytest

from analytics.signal.gates import _apply_conflict_resolver
from analytics.signal.types import SignalEvent


def _evt(
    strategy: str, direction: str, confidence: int, open_time: int = 0
) -> SignalEvent:
    return SignalEvent(
        symbol="AAPL",
        timeframe="4h",
        strategy=strategy,
        direction=direction,
        reason="t",
        open_time=open_time,
        price=100.0,
        confidence=confidence,
    )


class TestApplyConflictResolver:
    def test_empty_returns_empty(self) -> None:
        assert _apply_conflict_resolver([], "AAPL", "4h") == []

    def test_single_direction_unchanged(self) -> None:
        events = [_evt("bos", "long", 3), _evt("orb", "long", 4)]
        out = _apply_conflict_resolver(events, "AAPL", "4h")
        assert out == events
        # No conflict flag set when no opposition.
        assert all(not e.conflict for e in out)

    def test_long_wins_by_confidence(self, caplog: pytest.LogCaptureFixture) -> None:
        long_evt = _evt("bos", "long", 4)
        short_evt = _evt("engulfing", "short", 2)
        with caplog.at_level(logging.INFO, logger="analytics.signal.gates"):
            out = _apply_conflict_resolver([long_evt, short_evt], "AAPL", "4h")
        assert out == [long_evt]
        assert long_evt.conflict is True
        assert "LONG wins" in "\n".join(r.getMessage() for r in caplog.records)

    def test_short_wins_by_confidence(self, caplog: pytest.LogCaptureFixture) -> None:
        long_evt = _evt("bos", "long", 1)
        short_evt = _evt("engulfing", "short", 5)
        with caplog.at_level(logging.INFO, logger="analytics.signal.gates"):
            out = _apply_conflict_resolver([long_evt, short_evt], "AAPL", "4h")
        assert out == [short_evt]
        assert short_evt.conflict is True
        assert "SHORT wins" in "\n".join(r.getMessage() for r in caplog.records)

    def test_tie_sends_both_with_conflict_flag(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        long_evt = _evt("bos", "long", 3)
        short_evt = _evt("engulfing", "short", 3)
        with caplog.at_level(logging.INFO, logger="analytics.signal.gates"):
            out = _apply_conflict_resolver([long_evt, short_evt], "AAPL", "4h")
        assert sorted(out, key=lambda e: e.direction) == sorted(
            [long_evt, short_evt], key=lambda e: e.direction
        )
        assert long_evt.conflict is True
        assert short_evt.conflict is True
        assert "Conflict tie" in "\n".join(r.getMessage() for r in caplog.records)

    def test_max_confidence_per_side(self) -> None:
        # Multiple events per side; comparison uses the per-side MAX.
        out = _apply_conflict_resolver(
            [
                _evt("bos", "long", 2),
                _evt("orb", "long", 5),  # long max = 5
                _evt("engulfing", "short", 3),
                _evt("pin_bar", "short", 4),  # short max = 4
            ],
            "AAPL",
            "4h",
        )
        # Long side wins on max 5 > 4; only long events kept.
        assert {e.direction for e in out} == {"long"}
        assert len(out) == 2

    def test_custom_confidence_resolver(self) -> None:
        # Backtest replay needs to read confidence from a ratings dict instead
        # of the SignalEvent's editorial field — verify the override hook
        # works as PR-4b will use it.
        long_evt = _evt("bos", "long", 0)
        short_evt = _evt("engulfing", "short", 0)
        ratings = {"bos": 4, "engulfing": 2}
        out = _apply_conflict_resolver(
            [long_evt, short_evt],
            "AAPL",
            "4h",
            confidence_resolver=lambda e: ratings.get(e.strategy, 0),
        )
        # bos (long) rating 4 beats engulfing (short) rating 2.
        assert out == [long_evt]
        assert long_evt.conflict is True
