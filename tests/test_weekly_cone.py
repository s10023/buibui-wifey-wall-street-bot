"""Tests for analytics/stats/weekly_cone.py using in-memory DuckDB.

Synthetic-week construction mirrors tests/test_path_cone.py: every bar opens
at 100 and closes at 100 + k. Bar 1 carries the week low (99); bar 2 carries
the week high (101). With k in [-0.5, +0.5] every other bar stays inside
[99.5, 100.5], so every complete week's range is exactly (101 - 99) / 100 =
0.02 and the trailing AWR14 of any week with 14 complete predecessors is
exactly 0.02. A week's normalized close path is then constant at k / 2:
((100 + k - 100) / 100) / 0.02 = k / 2.

Equity adaptation vs the parent (crypto, 168 hourly bars/week): a complete
trading week is 5 RTH sessions x 7 hourly bars = 35 bars, Mon-Fri; holiday
and early-close weeks drop out via the completeness rule.
"""

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats import weekly_cone
from analytics.stats.weekly_cone import (
    WEEK_BARS,
    WeeklyConeBundle,
    WeekRecord,
    compute_current_week_path,
    compute_weekly_cone,
    week_records,
)

_SYMBOL = "WCONE"
_HOUR_MS = 3_600_000
_SESSION_BARS = 7
# RTH open 13:30 UTC (09:30 ET during EDT); bars at 13:30 … 19:30.
_SESSION_OPEN_H = 13.5
# Fixed clock: Wednesday 2026-03-04 17:45 UTC (mid-session, bar 4 forming).
_NOW = datetime(2026, 3, 4, 17, 45, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)
_CURRENT_WEEK = date(2026, 3, 2)  # the Monday of _NOW


def _insert_ohlcv_rows(
    conn: duckdb.DuckDBPyConnection, n_rows: int, params: list[object]
) -> None:
    """Insert n_rows OHLCV rows in one statement.

    DuckDB pays a fixed per-statement cost that dwarfs the row itself, and its
    executemany just loops, so a bar-at-a-time seed is far slower than folding
    the same rows into a single multi-row VALUES clause. These fixtures seed 14+
    weeks of RTH hourly bars per test, which is why they showed up as pure setup
    in --durations.
    """
    values = ",".join(["(?,?,?,?,?,?,?,?)"] * n_rows)
    conn.execute(
        "INSERT OR REPLACE INTO ohlcv "
        f"(symbol, timeframe, open_time, open, high, low, close, volume) "
        f"VALUES {values}",
        params,
    )


def _insert_week(
    conn: duckdb.DuckDBPyConnection,
    monday: date,
    *,
    k: float = 0.0,
    n_bars: int = WEEK_BARS,
) -> None:
    """Insert one synthetic Monday-anchored trading week of RTH 1h bars."""
    close = 100.0 + k
    params: list[object] = []
    for i in range(n_bars):
        day = monday + timedelta(days=i // _SESSION_BARS)
        h = i % _SESSION_BARS
        base_ms = int(
            datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
        )
        open_time = base_ms + int(_SESSION_OPEN_H * _HOUR_MS) + h * _HOUR_MS
        if i == 0:
            high, low = 100.5, 99.0
        elif i == 1:
            high, low = 101.0, 99.5
        else:
            high, low = max(100.5, close), min(99.5, close)
        params.extend((_SYMBOL, "1h", open_time, 100.0, high, low, close, 100.0))
    _insert_ohlcv_rows(conn, n_bars, params)


def _seed_warmup(
    conn: duckdb.DuckDBPyConnection, start_monday: date, n: int = 14
) -> None:
    """n consecutive complete doji (k=0) weeks starting at start_monday."""
    for i in range(n):
        _insert_week(conn, start_monday + timedelta(weeks=i))


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_warmup_weeks_produce_no_records(conn: duckdb.DuckDBPyConnection) -> None:
    """The first 14 qualifying weeks have no AWR window, so no records."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=14), n=14)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 0
    assert bundle.combos["all"].n == 0
    assert bundle.combos["all"].bands == []


def test_fifteenth_week_enters_population(conn: duckdb.DuckDBPyConnection) -> None:
    """One week past warmup yields exactly one record, direction bull."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 1
    assert bundle.combos["bull"].n == 1
    assert bundle.combos["bear"].n == 0
    assert bundle.combos["all"].n == 1


def test_normalization_identity(conn: duckdb.DuckDBPyConnection) -> None:
    """Normalized path at bar 35 equals week return / AWR14 (== k / 2)."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    bands = bundle.combos["bull"].bands
    assert len(bands) == WEEK_BARS
    # Single member -> every percentile equals that member's value.
    assert bands[-1] == pytest.approx([0.2] * 5)


def test_short_week_excluded(conn: duckdb.DuckDBPyConnection) -> None:
    """A 34-bar week (holiday / early close) never enters the population."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4, n_bars=34)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 0


def test_week_boundary_across_year_end(conn: duckdb.DuckDBPyConnection) -> None:
    """Monday anchoring holds across a year boundary (2025-12-29 is a Monday)."""
    _seed_warmup(conn, date(2025, 12, 29) - timedelta(weeks=14), n=14)
    _insert_week(conn, date(2025, 12, 29), k=-0.4)
    now = int(datetime(2026, 1, 7, 12, 0, tzinfo=UTC).timestamp() * 1000)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=now)
    assert bundle.total_weeks == 1
    assert bundle.combos["bear"].n == 1


def test_thin_data_returns_empty_never_raises(conn: duckdb.DuckDBPyConnection) -> None:
    """No bars at all -> empty combos, no exception."""
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert isinstance(bundle, WeeklyConeBundle)
    assert bundle.total_weeks == 0
    assert set(bundle.combos) == {"all", "bull", "bear"}


def test_determinism(conn: duckdb.DuckDBPyConnection) -> None:
    """Same inputs -> identical bundle."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=16), n=15)
    a = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    b = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert a == b


def test_current_week_path_partial(conn: duckdb.DuckDBPyConnection) -> None:
    """Partial current week: elapsed_h counts only closed bars."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=14), n=14)
    _insert_week(conn, _CURRENT_WEEK, k=0.2, n_bars=19)
    path = compute_current_week_path(conn, _SYMBOL, now_ms=_NOW_MS)
    assert path is not None
    assert path.week_open == 100.0
    assert path.awr14_current == pytest.approx(0.02)
    # _NOW is Wed 17:45 UTC: Mon 7 + Tue 7 + Wed 13:30-16:30 (4) have closed;
    # the 17:30 bar is forming.
    assert path.elapsed_h == 18
    assert len(path.points) == 19


def test_current_week_path_none_without_awr(conn: duckdb.DuckDBPyConnection) -> None:
    """Fewer than 14 complete prior weeks -> None."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=3), n=3)
    _insert_week(conn, _CURRENT_WEEK, k=0.2, n_bars=19)
    assert compute_current_week_path(conn, _SYMBOL, now_ms=_NOW_MS) is None


def test_low_high_timing_curves(conn: duckdb.DuckDBPyConnection) -> None:
    """Low is set at bar 1 and high at bar 2 by construction."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    combo = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS).combos["all"]
    assert combo.low_in_by[0] == pytest.approx(1.0)
    assert combo.high_in_by[0] == pytest.approx(0.0)
    assert combo.high_in_by[1] == pytest.approx(1.0)
    assert len(combo.low_in_by) == WEEK_BARS


def test_week_records_matches_cone_population(conn: duckdb.DuckDBPyConnection) -> None:
    """The public wrapper returns exactly the population the cone counts."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=17), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=3), k=0.4)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=2), k=-0.4)

    records = week_records(conn, _SYMBOL, now_ms=_NOW_MS)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)

    assert len(records) == bundle.total_weeks == 2
    assert all(len(r.norm_path) == WEEK_BARS for r in records)
    assert {r.direction for r in records} == {"bull", "bear"}


def test_week_records_are_chronological(conn: duckdb.DuckDBPyConnection) -> None:
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=17), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=3), k=0.4)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=2), k=-0.4)

    records = week_records(conn, _SYMBOL, now_ms=_NOW_MS)
    assert [r.week for r in records] == sorted(r.week for r in records)


def test_week_records_thin_data_returns_empty(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Matches compute_weekly_cone: never raises on a short history."""
    assert week_records(conn, _SYMBOL, now_ms=_NOW_MS) == []


def test_private_alias_still_resolves() -> None:
    """The rename must not break internal references inside the module."""
    assert weekly_cone._WeekRecord is WeekRecord
