"""API-level tests for the weekly cone fields on GET /api/stats/{symbol}."""

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats.bundle import compute_all
from analytics.stats.weekly_cone import WEEK_BARS
from web.api.models.stats import (
    CurrentWeekPathResponse,
    StatsResponse,
    WeeklyConeResponse,
)
from web.api.routers.stats import _bundle_to_response

_SYMBOL = "WAPI"
_HOUR_MS = 3_600_000
_SESSION_BARS = 7
_SESSION_OPEN_H = 13.5
_CURRENT_WEEK = date(2026, 3, 2)


def _insert_ohlcv_rows(
    conn: duckdb.DuckDBPyConnection, n_rows: int, params: list[object]
) -> None:
    """Insert n_rows OHLCV rows in ONE statement.

    DuckDB pays a fixed per-statement cost that dwarfs the row itself, and its
    executemany just loops, so a bar-at-a-time seed is far slower than folding
    the same rows into a single multi-row VALUES clause. This fixture seeds 28
    weeks of RTH hourly bars per test, which is why it showed up as pure setup
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


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    for i in range(20):
        _insert_week(c, _CURRENT_WEEK - timedelta(weeks=20 - i), k=0.2)
    # 1d bars so compute_all's other stats have something to chew on.
    day_params: list[object] = []
    for i in range(140):
        day_ms = (
            int(datetime(2025, 11, 1, tzinfo=UTC).timestamp() * 1000) + i * 86_400_000
        )
        day_params.extend((_SYMBOL, "1d", day_ms, 100.0, 101.0, 99.0, 100.2, 100.0))
    _insert_ohlcv_rows(c, 140, day_params)
    # Extra 1h weeks anchored on the REAL wall-clock "now" (not the frozen
    # _CURRENT_WEEK above). compute_adr — and several sibling stats in
    # compute_all — window off datetime.now(tz=UTC) internally with no
    # now_ms override, so without this block ADR's lookback finds zero rows
    # and compute_all raises ValueError long before weekly_cone is reached.
    real_now = datetime.now(tz=UTC)
    this_real_monday = (real_now - timedelta(days=real_now.weekday())).date()
    for i in range(1, 9):
        _insert_week(c, this_real_monday - timedelta(weeks=i), k=0.2)
    return c


def test_bundle_carries_weekly_cone(conn: duckdb.DuckDBPyConnection) -> None:
    """StatsBundle gains a populated weekly_cone field."""
    bundle = compute_all(conn, _SYMBOL, 180)
    assert set(bundle.weekly_cone.combos) == {"all", "bull", "bear"}
    assert bundle.weekly_cone.total_weeks > 0


def test_weekly_cone_response_roundtrip_empty() -> None:
    """The response model serializes and validates on the degenerate shell."""
    resp = WeeklyConeResponse(combos={}, total_weeks=0)
    assert WeeklyConeResponse.model_validate_json(resp.model_dump_json()) == resp


def test_weekly_cone_response_roundtrip_populated(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A POPULATED weekly cone actually exercises _bundle_to_response's
    weekly-cone construction (the empty-combos test above never touches the
    nested WeeklyConeCombo fields — bands/low_in_by/mae_p/etc — because
    there is nothing in `combos` to iterate), and the nested payload must
    itself survive a JSON round-trip."""
    bundle = compute_all(conn, _SYMBOL, 180)
    assert bundle.weekly_cone.combos["all"].n > 0  # genuinely populated

    response = _bundle_to_response(bundle)
    assert response.weekly_cone is not None  # _bundle_to_response always sets it
    assert response.weekly_cone.total_weeks == bundle.weekly_cone.total_weeks
    resp_all = response.weekly_cone.combos["all"]
    src_all = bundle.weekly_cone.combos["all"]
    assert resp_all.n == src_all.n
    assert resp_all.bands == src_all.bands
    assert resp_all.bands  # non-empty nested payload actually exercised

    round_tripped = StatsResponse.model_validate_json(response.model_dump_json())
    assert round_tripped.weekly_cone == response.weekly_cone


def test_current_week_path_response_optional() -> None:
    """The live overlay model is independently constructible."""
    cw = CurrentWeekPathResponse(
        points=[0.1, 0.2], elapsed_h=1, awr14_current=0.02, week_open=100.0
    )
    assert cw.elapsed_h == 1
