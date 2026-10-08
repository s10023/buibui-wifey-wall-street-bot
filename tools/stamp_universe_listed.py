"""Stamp ``listed`` on config/universe.json from DB first-1d-bar ground truth (N3).

``UniverseMember.listed`` is defined as *the member's first available 1d bar
date*, and it is the only seam ``ResearchUniverse.with_min_history`` reads. It
was stamped on **3 of 505** members (GEV/PLTR/UBER, by hand), so
``min_history_days`` filtered almost nothing: a name with 17 bars survived an
8-year history floor because an absent ``listed`` means "full-history survivor".

**The floor is not observable, and that bounds the claim.** Our 1d history starts
at the universe backfill's ``--since`` (2018-01-01 → first NYSE session
2018-01-02), so a member whose first bar sits *on* that session is truncated by
the sample and indistinguishable from a genuine listing that day. Those stay
``None`` — the conservative reading, and the same "is this metric's floor
reachable by every row?" trap as the 2026-08-12 exits audit. Only a first bar
**strictly after** the floor session is evidence of a listing date, which is
exactly what README documents (``listed`` is set "on names that list after the
backfill start"). Members with no 1d bars at all are left untouched and reported
loudly rather than silently read as survivors.

Re-run after ``tools/expand_universe_sp500.py`` or ``make wifey-universe-backfill``:
a new constituent arrives with no ``listed`` key, which is the permissive value.

Read-only by default; ``--write`` rewrites the file.

Usage::

    PYTHONPATH=. poetry run python tools/stamp_universe_listed.py
    PYTHONPATH=. poetry run python tools/stamp_universe_listed.py --write
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from analytics.store import DEFAULT_DB_PATH
from analytics.trading_calendar import nyse_sessions

UNIVERSE_PATH = Path("config/universe.json")
DEFAULT_SINCE = date(2018, 1, 1)
# The field is *defined* against 1d bars, so this is a constant and not a flag —
# stamping from 4h (history starts 2024-05-16) would mark 477 survivors as 2024
# listings.
LISTED_TIMEFRAME = "1d"
_FLOOR_SEARCH_DAYS = 30


@dataclass(frozen=True)
class StampChange:
    """One member whose ``listed`` value the DB disagrees with."""

    symbol: str
    before: str | None
    after: str | None

    @property
    def kind(self) -> str:
        if self.before is None:
            return "add"
        if self.after is None:
            return "remove"
        return "correct"


def floor_session(since: date) -> date:
    """First NYSE session on or after ``since`` — the truncation floor.

    A first bar on this date carries no information: every full-history survivor
    in the sample starts here.
    """
    sessions = nyse_sessions(since, since + timedelta(days=_FLOOR_SEARCH_DAYS))
    if not sessions:
        raise ValueError(
            f"no NYSE session within {_FLOOR_SEARCH_DAYS} days of {since} — "
            "cannot establish the truncation floor"
        )
    return sessions[0]


def resolve_listed(
    members: Mapping[str, Mapping[str, Any]],
    first_bars: Mapping[str, date],
    floor: date,
) -> tuple[dict[str, dict[str, Any]], list[StampChange], list[str]]:
    """Return (members with ``listed`` reconciled, changes, symbols with no bars).

    Pure — no DB, no I/O. ``first_bars`` maps symbol → first 1d bar date; a
    symbol absent from it has no bars and is left exactly as it is (its absence
    from the result's change list is why ``main`` reports it separately).
    """
    out: dict[str, dict[str, Any]] = {}
    changes: list[StampChange] = []
    no_bars: list[str] = []
    for symbol, params in members.items():
        member = dict(params)
        before = member.get("listed")
        first = first_bars.get(symbol)
        if first is None:
            no_bars.append(symbol)
            out[symbol] = member
            continue
        after = first.isoformat() if first > floor else None
        if after != before:
            changes.append(StampChange(symbol=symbol, before=before, after=after))
        if after is None:
            member.pop("listed", None)
        else:
            member["listed"] = after
        out[symbol] = member
    return out, changes, no_bars


def first_bar_dates(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> dict[str, date]:
    """Map symbol → UTC date of its earliest ``LISTED_TIMEFRAME`` bar.

    Symbols with no bars are absent from the result. 1d ``open_time`` is stamped
    04:00/05:00 UTC, i.e. ET midnight, so the UTC date *is* the session date.
    """
    rows = conn.execute(
        f"""
        SELECT symbol, MIN(open_time) AS first_ms
        FROM ohlcv
        WHERE timeframe = '{LISTED_TIMEFRAME}' AND symbol IN (SELECT UNNEST(?))
        GROUP BY symbol
        """,
        [symbols],
    ).fetchall()
    return {
        str(sym): datetime.fromtimestamp(int(ms) / 1000, tz=UTC).date()
        for sym, ms in rows
        if ms is not None
    }


def _print_report(
    changes: list[StampChange],
    no_bars: list[str],
    first_bars: Mapping[str, date],
    floor: date,
    n_members: int,
) -> None:
    at_floor = sum(1 for d in first_bars.values() if d == floor)
    before_floor = sum(1 for d in first_bars.values() if d < floor)
    print(
        f"{n_members} members · {len(first_bars)} with {LISTED_TIMEFRAME} bars · "
        f"truncation floor {floor.isoformat()}"
    )
    print(
        f"  {at_floor} start ON the floor (unstampable by construction) · "
        f"{before_floor} start before it (deeper backfill) · "
        f"{len(first_bars) - at_floor - before_floor} list after it"
    )
    if no_bars:
        print(
            f"  ⚠ {len(no_bars)} member(s) have NO {LISTED_TIMEFRAME} bars and were "
            f"left untouched — an absent `listed` reads as full-history: "
            f"{', '.join(no_bars)}"
        )
        print("    → make wifey-universe-backfill")
    print()
    if not changes:
        print("No changes — every `listed` already matches the DB.")
        return
    print(f"{len(changes)} change(s):")
    width = max(len(c.symbol) for c in changes)
    for c in sorted(changes, key=lambda c: (c.after or "", c.symbol)):
        print(
            f"  {c.kind:7s} {c.symbol.ljust(width)}  "
            f"{c.before or '—'} → {c.after or '—'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument("--universe", type=Path, default=UNIVERSE_PATH)
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=DEFAULT_SINCE,
        help="Universe backfill start (default 2018-01-01, per make "
        "wifey-universe-backfill); the floor is the first NYSE session at/after it",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the universe file (default: report only)",
    )
    args = parser.parse_args()

    universe: dict[str, Any] = json.loads(args.universe.read_text(encoding="utf-8"))
    members: dict[str, dict[str, Any]] = universe["members"]

    conn = duckdb.connect(str(args.db), read_only=True)
    # Same DuckDB aggregate fast-path bug worked around in tools/universe_coverage.py
    # and analytics/store/market_data.get_latest_open_time.
    conn.execute("SET disabled_optimizers='statistics_propagation'")
    try:
        first_bars = first_bar_dates(conn, list(members))
    finally:
        conn.close()

    floor = floor_session(args.since)
    stamped, changes, no_bars = resolve_listed(members, first_bars, floor)
    _print_report(changes, no_bars, first_bars, floor, len(members))

    if not changes:
        return
    if not args.write:
        print("\n(dry run — pass --write to apply)")
        return
    universe["members"] = stamped
    args.universe.write_text(json.dumps(universe, indent=2) + "\n", encoding="utf-8")
    print(f"\n✅ wrote {args.universe} ({len(changes)} member(s) restamped)")


if __name__ == "__main__":
    main()
