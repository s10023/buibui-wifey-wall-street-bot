"""ADR mean/median — `compute_adr` unit tests (ported with parent #598).

The fixture is deliberately SKEWED. A uniform range distribution makes mean and
median identical, so a test built on one would pass against an implementation that
computed the mean twice — the vacuous-fixture trap this repo audits for elsewhere
(`make check-orphan-tests`, `tests/test_schema_insert_arity.py`). Here 13 quiet days
sit beside one very loud one, so mean and median differ by ~4.5x and only a real
median passes.

`LOUD` is 50%, which no real US equity session produces — index circuit breakers
halt trading at 20%. That is deliberate: the fixture's job is to make the two
statistics provably distinguishable, not to look like a tape. Realism lives in
`docs/plans/scripts/dow_return_noise.py`, which measures both against real bars.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd
import pytest

from analytics.data_store import init_schema
from analytics.stats.adr import compute_adr
from analytics.store.market_data import upsert_ohlcv

SYM = "SPY"
QUIET = 0.01  # 1% day — realistic for SPY (measured 30d mean 0.94%)
LOUD = 0.50  # synthetic outlier, placed inside the 14d window
OUTLIER_AT = 5  # days back from today
N_DAYS = 20


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    """One 1h bar per day at 12:00 UTC, newest = today.

    `compute_adr` reads the **1h** timeframe and aggregates to sessions — it does
    not read 1d bars. 12:00 UTC is chosen so the bar lands on the same calendar
    DATE under both UTC and this machine's UTC+8 local zone, which is what DuckDB
    renders in (12+8=20 is still the same day). A midnight bar would bucket into
    different dates under the two zones and silently split the fixture.
    """
    today = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    rows: list[dict[str, object]] = []
    for back in range(N_DAYS):
        rng = LOUD if back == OUTLIER_AT else QUIET
        ts = int((today - timedelta(days=back)).timestamp() * 1000)
        rows.append(
            {
                "symbol": SYM,
                "timeframe": "1h",
                "open_time": ts,
                "open": 100.0,
                "high": 100.0 * (1.0 + rng),
                "low": 100.0,
                "close": 100.0,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
    upsert_ohlcv(conn, pd.DataFrame(rows))


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn)
    return conn


def test_adr_14_mean_is_dragged_by_the_outlier() -> None:
    res = compute_adr(_conn(), SYM)
    assert res.adr_14 == pytest.approx((13 * QUIET + LOUD) / 14)


def test_adr_14_median_ignores_the_outlier() -> None:
    res = compute_adr(_conn(), SYM)
    assert res.adr_14_median == pytest.approx(QUIET)


def test_adr_30_mean_and_median_both_present() -> None:
    res = compute_adr(_conn(), SYM)
    assert res.adr_30 == pytest.approx((19 * QUIET + LOUD) / N_DAYS)
    assert res.adr_30_median == pytest.approx(QUIET)


def test_mean_and_median_are_actually_different() -> None:
    """The non-degeneracy check: a mean computed twice would pass everything above."""
    res = compute_adr(_conn(), SYM)
    assert res.adr_14 > res.adr_14_median * 4
    assert res.adr_30 > res.adr_30_median * 3


def test_today_consumed_still_divides_by_the_MEAN() -> None:
    """Display-only guard.

    `adr_14` feeds `weekly_state`, `weekly_wick`, `signal/stats_context` and the
    alert formatter. Adding a median column must not move any of that. Today is a
    QUIET day, so consumed-vs-mean is ~0.22 while consumed-vs-median would be 1.0 —
    this assertion fails loudly the day someone repoints it.
    """
    res = compute_adr(_conn(), SYM)
    assert res.today_range_pct == pytest.approx(QUIET)
    assert res.today_consumed_pct == pytest.approx(QUIET / ((13 * QUIET + LOUD) / 14))
    assert res.today_consumed_pct is not None
    assert res.today_consumed_pct < 0.5  # would be exactly 1.0 against the median


def test_median_survives_a_short_history() -> None:
    """Fewer bars than the window: both stats must still return, not divide by 14."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    today = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    ts = int(today.timestamp() * 1000)
    upsert_ohlcv(
        conn,
        pd.DataFrame(
            [
                {
                    "symbol": SYM,
                    "timeframe": "1h",
                    "open_time": ts,
                    "open": 100.0,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000.0,
                    "taker_buy_volume": 500.0,
                }
            ]
        ),
    )
    res = compute_adr(conn, SYM)
    assert res.adr_14 == pytest.approx(0.02)
    assert res.adr_14_median == pytest.approx(0.02)


def test_no_data_still_raises() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    with pytest.raises(ValueError, match="No OHLCV data"):
        compute_adr(conn, "NOSUCHTICKER")


def test_seed_is_inside_the_35d_window() -> None:
    """Guards the fixture itself: compute_adr is now-anchored via _start_ms(35)."""
    assert N_DAYS < 35
    assert time.time() > 0
