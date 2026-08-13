"""Tests for tools/stamp_universe_listed.py — the `listed` stamp resolver (N3).

``resolve_listed`` is the whole decision, and it is pure so these tests reach it
without the DB (the suite never touches the real analytics.db). The cases that
matter are the boundary ones: a first bar ON the truncation floor carries no
information and must NOT be stamped, because 477 of 505 members share it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from tools.stamp_universe_listed import (
    LISTED_TIMEFRAME,
    floor_session,
    resolve_listed,
)

FLOOR = date(2018, 1, 2)


def _members(*specs: tuple[str, str | None]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for symbol, listed in specs:
        member: dict[str, Any] = {
            "sector": "Industrials",
            "kind": "stock",
            "delisted": False,
        }
        if listed is not None:
            member["listed"] = listed
        out[symbol] = member
    return out


class TestFloorSession:
    def test_floor_is_first_nyse_session_at_or_after_since(self) -> None:
        # 2018-01-01 is a holiday; the first session is the 2nd
        assert floor_session(date(2018, 1, 1)) == FLOOR

    def test_floor_of_a_session_is_that_session(self) -> None:
        assert floor_session(FLOOR) == FLOOR

    def test_floor_skips_a_weekend(self) -> None:
        # 2026-06-13 is a Saturday
        assert floor_session(date(2026, 6, 13)) == date(2026, 6, 15)

    def test_timeframe_is_pinned_to_1d(self) -> None:
        # 4h history starts 2024-05-16, so stamping from it would mark every
        # full-history survivor as a 2024 listing.
        assert LISTED_TIMEFRAME == "1d"


class TestResolveListed:
    def test_first_bar_after_floor_is_stamped(self) -> None:
        members = _members(("CRWD", None))
        out, changes, no_bars = resolve_listed(
            members, {"CRWD": date(2019, 6, 12)}, FLOOR
        )
        assert out["CRWD"]["listed"] == "2019-06-12"
        assert [(c.symbol, c.before, c.after, c.kind) for c in changes] == [
            ("CRWD", None, "2019-06-12", "add")
        ]
        assert no_bars == []

    def test_first_bar_ON_the_floor_is_not_stamped(self) -> None:
        """The floor is shared by every truncated survivor — no information."""
        members = _members(("AAPL", None))
        out, changes, _ = resolve_listed(members, {"AAPL": FLOOR}, FLOOR)
        assert "listed" not in out["AAPL"]
        assert changes == []

    def test_first_bar_before_floor_is_not_stamped(self) -> None:
        """A deeper backfill (SPY/QQQ reach 2007) is still a full-history name."""
        members = _members(("SPY", None))
        out, changes, _ = resolve_listed(members, {"SPY": date(2007, 3, 1)}, FLOOR)
        assert "listed" not in out["SPY"]
        assert changes == []

    def test_one_session_after_the_floor_is_stamped(self) -> None:
        """The boundary is strict: the floor itself is excluded, the next day is not."""
        members = _members(("NEW", None))
        out, changes, _ = resolve_listed(members, {"NEW": date(2018, 1, 3)}, FLOOR)
        assert out["NEW"]["listed"] == "2018-01-03"
        assert len(changes) == 1

    def test_agreeing_existing_stamp_is_not_a_change(self) -> None:
        members = _members(("UBER", "2019-05-10"))
        out, changes, _ = resolve_listed(members, {"UBER": date(2019, 5, 10)}, FLOOR)
        assert out["UBER"]["listed"] == "2019-05-10"
        assert changes == []

    def test_disagreeing_existing_stamp_is_corrected(self) -> None:
        members = _members(("UBER", "2019-01-01"))
        out, changes, _ = resolve_listed(members, {"UBER": date(2019, 5, 10)}, FLOOR)
        assert out["UBER"]["listed"] == "2019-05-10"
        assert [(c.before, c.after, c.kind) for c in changes] == [
            ("2019-01-01", "2019-05-10", "correct")
        ]

    def test_stamp_at_the_floor_is_removed(self) -> None:
        """A truncation artifact already in the file must come back out."""
        members = _members(("AAPL", "2018-01-02"))
        out, changes, _ = resolve_listed(members, {"AAPL": FLOOR}, FLOOR)
        assert "listed" not in out["AAPL"]
        assert [(c.before, c.after, c.kind) for c in changes] == [
            ("2018-01-02", None, "remove")
        ]

    def test_member_with_no_bars_is_left_untouched_and_reported(self) -> None:
        """An absent `listed` reads as full-history, so silence here is a lie."""
        members = _members(("NOBARS", None), ("KEPT", "2019-06-12"))
        out, changes, no_bars = resolve_listed(
            members, {"KEPT": date(2019, 6, 12)}, FLOOR
        )
        assert no_bars == ["NOBARS"]
        assert "listed" not in out["NOBARS"]
        assert changes == []

    def test_no_bars_does_not_erase_an_existing_stamp(self) -> None:
        """A wiped/partial OHLCV table must not silently un-stamp the file."""
        members = _members(("PLTR", "2020-09-30"))
        out, changes, no_bars = resolve_listed(members, {}, FLOOR)
        assert out["PLTR"]["listed"] == "2020-09-30"
        assert no_bars == ["PLTR"]
        assert changes == []

    def test_is_idempotent(self) -> None:
        members = _members(("AAPL", None), ("CRWD", None), ("SPY", None))
        first_bars = {
            "AAPL": FLOOR,
            "CRWD": date(2019, 6, 12),
            "SPY": date(2007, 3, 1),
        }
        once, changes, _ = resolve_listed(members, first_bars, FLOOR)
        assert len(changes) == 1
        twice, changes2, _ = resolve_listed(once, first_bars, FLOOR)
        assert twice == once
        assert changes2 == []

    def test_does_not_mutate_the_input(self) -> None:
        members = _members(("CRWD", None))
        resolve_listed(members, {"CRWD": date(2019, 6, 12)}, FLOOR)
        assert "listed" not in members["CRWD"]

    def test_preserves_other_fields_and_member_order(self) -> None:
        members = _members(("ZTS", None), ("CRWD", None), ("AAPL", None))
        members["ZTS"]["sector"] = "Health Care"
        out, _, _ = resolve_listed(
            members, {"CRWD": date(2019, 6, 12), "ZTS": FLOOR, "AAPL": FLOOR}, FLOOR
        )
        assert list(out) == ["ZTS", "CRWD", "AAPL"]
        assert out["ZTS"]["sector"] == "Health Care"
        assert out["ZTS"]["kind"] == "stock"
        assert out["ZTS"]["delisted"] is False
