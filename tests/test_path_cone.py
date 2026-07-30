"""Tests for analytics/stats/path_cone.py using in-memory DuckDB.

Synthetic-session construction: every bar opens at 100 and closes at 100 + k.
Bar 1 carries the session low (99); bar 2 carries the session high (101). With
k ∈ [−0.5, +0.5] every other bar stays inside [99.5, 100.5], so every
complete session's range is exactly (101 − 99) / 100 = 0.02 and the trailing
ADR14 of any session with 14 complete predecessors is exactly 0.02. A
session's normalized close path is then constant at k / 2:
((100 + k − 100) / 100) / 0.02 = k / 2.

Equity adaptation vs the parent (crypto, 24 hourly bars/UTC day): a complete
RTH session has 7 hourly bars (yfinance 1h, 09:30–16:00 ET) and sessions only
exist Mon–Fri, so bands/timing vectors have 7 steps and the combo grid is
3 directions × 6 weekday keys = 18.
"""

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats.path_cone import (
    PathConeBundle,
    compute_path_cone,
    compute_today_path,
)

_SYMBOL = "CONE"
_HOUR_MS = 3_600_000
# RTH open 13:30 UTC (09:30 ET during EDT); bars at 13:30 … 19:30.
_SESSION_OPEN_H = 13.5
# Fixed clock: Monday 2026-03-02 17:45 UTC — all tests pass now_ms explicitly.
_NOW = datetime(2026, 3, 2, 17, 45, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)


def _bar_ms(day: date, h: int) -> int:
    """open_time of hourly bar h (0-based) on `day`."""
    base = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    return base + int(_SESSION_OPEN_H * _HOUR_MS) + h * _HOUR_MS


def _insert_day(
    conn: duckdb.DuckDBPyConnection,
    day: date,
    *,
    k: float = 0.0,
    n_bars: int = 7,
    wide_high: float | None = None,
) -> None:
    """Insert one synthetic RTH session of 1h bars (see module docstring)."""
    close = 100.0 + k
    for h in range(n_bars):
        if h == 0:
            high, low = 100.5, 99.0
        elif h == 1:
            high = wide_high if wide_high is not None else 101.0
            low = 99.5
        else:
            high, low = max(100.5, close), min(99.5, close)
        conn.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [_SYMBOL, "1h", _bar_ms(day, h), 100.0, high, low, close, 100.0],
        )


def _seed_warmup(conn: duckdb.DuckDBPyConnection, start: date, n: int = 14) -> None:
    """n consecutive complete weekday doji (k=0) sessions starting at `start`."""
    d = start
    inserted = 0
    while inserted < n:
        if d.weekday() < 5:
            _insert_day(conn, d)
            inserted += 1
        d += timedelta(days=1)


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_cone_bands_hand_computed(conn: duckdb.DuckDBPyConnection) -> None:
    """Median band equals the hand-computed median of the normalized closes."""
    start = date(2026, 1, 5)  # Monday
    _seed_warmup(conn, start)  # Jan 5 … Jan 22 weekdays complete, all range 2%
    _insert_day(conn, date(2026, 1, 23), k=0.5)  # Friday, bull, norm +0.25
    _insert_day(conn, date(2026, 1, 26), k=0.25)  # Monday, bull, norm +0.125
    _insert_day(conn, date(2026, 1, 27), k=-0.5)  # Tuesday, bear, norm −0.25
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert isinstance(cone, PathConeBundle)
    assert cone.total_days == 3
    combo = cone.combos["all|all"]
    assert combo.n == 3
    assert len(combo.bands) == 7
    for step in range(7):
        assert combo.bands[step][2] == pytest.approx(0.125)  # p50
    assert cone.combos["bull|all"].n == 2
    assert cone.combos["bear|all"].n == 1
    assert cone.combos["bull|fri"].n == 1
    assert cone.combos["bear|tue"].n == 1
    empty = cone.combos["bear|mon"]
    assert empty.n == 0
    assert empty.bands == []
    assert empty.low_in_by == []
    assert len(cone.combos) == 18  # 3 directions × 6 weekday keys


def test_timing_distributions(conn: duckdb.DuckDBPyConnection) -> None:
    """Bar 1 always carries the session low, bar 2 the high (earliest-tie)."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 1, 23), k=0.5)
    _insert_day(conn, date(2026, 1, 26), k=-0.5)
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    combo = cone.combos["all|all"]
    assert combo.low_in_by[0] == pytest.approx(1.0)  # low set by bar 1
    assert combo.high_in_by[0] == pytest.approx(0.0)  # high not yet at bar 1
    assert combo.high_in_by[1] == pytest.approx(1.0)  # high set by bar 2
    assert combo.low_in_by[6] == pytest.approx(1.0)  # cumulative → 1.0


def test_pivots_and_excursions(conn: duckdb.DuckDBPyConnection) -> None:
    """Pivot magnitudes and excursion percentiles match hand computation."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 1, 23), k=0.5)  # bull, norm +0.25
    _insert_day(conn, date(2026, 1, 26), k=0.25)  # bull, norm +0.125
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    combo = cone.combos["bull|all"]
    # high magnitude = (101−100)/100/0.02 = 0.5 on every session; same for low.
    assert combo.high_piv[0] == pytest.approx(0.5)  # p50
    assert combo.high_piv[1] == pytest.approx(0.5)  # p80
    assert combo.low_piv[0] == pytest.approx(0.5)
    # constant paths → MFE = norm; np.percentile([0.125, 0.25], 50) = 0.1875
    assert combo.mfe_p[1] == pytest.approx(0.1875)
    assert combo.mae_p[1] == pytest.approx(0.1875)


def test_causality_perturbation(conn: duckdb.DuckDBPyConnection) -> None:
    """Inflating session d's range must not change d's own normalization —
    only later sessions' (their ADR windows contain d)."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)  # Jan 5 … Jan 22 weekdays
    _insert_day(conn, date(2026, 1, 26), k=0.5)  # Monday — to be perturbed
    _insert_day(conn, date(2026, 1, 27), k=0.25)  # Tuesday — downstream
    before = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    # Blow up Monday's range via a huge bar-2 wick: same open, same closes.
    _insert_day(conn, date(2026, 1, 26), k=0.5, wide_high=110.0)
    after = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert after.combos["all|mon"].bands == before.combos["all|mon"].bands
    assert after.combos["all|tue"].bands != before.combos["all|tue"].bands


def test_exclusions(conn: duckdb.DuckDBPyConnection) -> None:
    """Incomplete sessions drop; doji counts only in 'all'; today never counts."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 1, 23), k=0.5)  # bull
    _insert_day(conn, date(2026, 1, 26), k=0.25, n_bars=6)  # incomplete
    _insert_day(conn, date(2026, 1, 27))  # doji (close == open)
    _insert_day(conn, date(2026, 3, 2), k=0.5, n_bars=5)  # "today"
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert cone.total_days == 2  # Jan 23 + Jan 27 (warmup has no ADR window)
    assert cone.combos["all|all"].n == 2
    assert cone.combos["bull|all"].n == 1  # doji excluded from bull/bear
    assert cone.combos["bear|all"].n == 0


def test_today_path(conn: duckdb.DuckDBPyConnection) -> None:
    """Today's partial path normalizes by trailing ADR14 and counts only
    completed bars in elapsed_h (forming bar still contributes a point)."""
    start = date(2026, 2, 10)  # Tuesday; Feb 10 … Feb 27 = 14 weekday sessions
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 3, 2), k=0.5, n_bars=5)  # today, 5 bars
    tp = compute_today_path(conn, _SYMBOL, now_ms=_NOW_MS)
    assert tp is not None
    assert tp.adr14_today == pytest.approx(0.02)
    assert tp.today_open == pytest.approx(100.0)
    # now = 17:45 UTC → bars 13:30–16:30 are complete (4); 17:30 is forming.
    assert tp.elapsed_h == 4
    assert len(tp.points) == 5
    assert tp.points[-1] == pytest.approx(0.25)  # (100.5−100)/(100×0.02)


def test_today_path_insufficient_history(conn: duckdb.DuckDBPyConnection) -> None:
    """Fewer than 14 complete prior sessions → overlay unavailable (None)."""
    _insert_day(conn, date(2026, 2, 27))
    _insert_day(conn, date(2026, 3, 2), k=0.5, n_bars=5)
    assert compute_today_path(conn, _SYMBOL, now_ms=_NOW_MS) is None


def test_no_data_returns_empty_bundle(conn: duckdb.DuckDBPyConnection) -> None:
    """A symbol with zero OHLCV yields an all-empty bundle, no exception."""
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert cone.total_days == 0
    assert all(c.n == 0 for c in cone.combos.values())


def test_extreme_tie_earliest_hour_wins(conn: duckdb.DuckDBPyConnection) -> None:
    """Two bars sharing the session low → the EARLIEST bar is the timing bar."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 1, 23), k=0.5)
    # Give bar 6 (index 5) the same 99.0 low as bar 1 — a tie.
    conn.execute(
        "UPDATE ohlcv SET low = 99.0 "
        "WHERE symbol = ? AND timeframe = '1h' AND open_time = ?",
        [_SYMBOL, _bar_ms(date(2026, 1, 23), 5)],
    )
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert cone.combos["all|all"].low_in_by[0] == pytest.approx(1.0)


def test_stats_response_requires_path_cone() -> None:
    """Pre-M5 cached JSON (no path_cone) must fail validation so the router's
    corrupted-cache fallback recomputes — the cache self-heals on deploy day."""
    import pydantic
    import pytest as _pytest

    from web.api.models.stats import StatsResponse

    with _pytest.raises(pydantic.ValidationError):
        StatsResponse.model_validate_json('{"symbol": "X", "days": 180}')
    assert "path_cone" in StatsResponse.model_fields
    assert "today_path" in StatsResponse.model_fields
    assert "daily_distance" not in StatsResponse.model_fields


def test_path_cone_response_round_trip() -> None:
    """The cached block survives a JSON dump → validate cycle unchanged."""
    from web.api.models.stats import ConeComboResponse, PathConeResponse

    combo = ConeComboResponse(
        direction="bull",
        weekday="mon",
        n=2,
        bands=[[-0.1, 0.0, 0.125, 0.2, 0.3]] * 7,
        low_in_by=[1.0] * 7,
        high_in_by=[0.0] + [1.0] * 6,
        mae_p=[0.1, 0.19, 0.24],
        mfe_p=[0.1, 0.19, 0.24],
        high_piv=[0.5, 0.5],
        low_piv=[0.5, 0.5],
    )
    original = PathConeResponse(combos={"bull|mon": combo}, total_days=2)
    restored = PathConeResponse.model_validate_json(original.model_dump_json())
    assert restored == original
