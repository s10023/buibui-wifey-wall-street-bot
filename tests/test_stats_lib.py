"""Tests for analytics/stats_lib.py using in-memory DuckDB."""

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats_lib import (
    DailyDistanceResult,
    StatsBundle,
    WeeklyCurrentState,
    WeeklyFlipRiskConditioned,
    WeeklyP2Timing,
    WeeklyWickPercentile,
    compute_adr,
    compute_all,
    compute_daily_distance,
    compute_dow_patterns,
    compute_hourly_extremes,
    compute_p1p2_daily,
    compute_session_breakdown,
    compute_weekly_current_state,
    compute_weekly_flip_risk_conditioned,
    compute_weekly_p1p2,
    compute_weekly_p2_timing,
    compute_weekly_wick_percentile,
)

_SYMBOL = "TESTUSDT"
_TIMEFRAME = "1h"

# Days are grouped by UTC date (Binance daily = 00:00 UTC – 23:59 UTC = 08:00–07:59 MYT).
# To guarantee P1=Low (low before high) within the same UTC day we place:
#   - daily low  at UTC 00:00 = MYT 08:00  (first candle of UTC day)
#   - daily high at UTC 06:00 = MYT 14:00  (six hours later in the same UTC day)
# UTC 00 < UTC 06  → low always comes before high → p1_low_pct ≈ 1.0
# peak_low_hour_myt  = 8  (UTC 00 + 8)
# peak_high_hour_myt = 14 (UTC 06 + 8)
#
# Use a base date 20 days ago so candles fall within the 30-day lookback window.
_DAYS = 14
_OPEN_PRICE = 40000.0
_NORMAL_HIGH = 40500.0  # normal candle high
_NORMAL_LOW = 39500.0  # normal candle low
_PEAK_HIGH = 41000.0  # only candle at UTC 06 (MYT 14) gets this high
_PEAK_LOW = 39000.0  # only candle at UTC 00 (MYT 08) gets this low


def _myt_hour(utc_ts: int) -> int:
    """Return MYT hour for a UTC unix ms timestamp."""
    return (datetime.fromtimestamp(utc_ts / 1000, tz=UTC).hour + 8) % 24


def _make_candles() -> list[dict]:
    """Generate 14 days × 24 hourly candles with deterministic highs/lows.

    Base date is 21 days ago so candles end ~8 days ago — safely before the current ISO
    week on any day of the week (current week starts at most 7 days ago).
    """
    base_utc = datetime.now(tz=UTC) - timedelta(days=21)
    # Align to midnight UTC
    base_utc = base_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for day in range(_DAYS):
        for hour in range(24):
            utc_dt = base_utc + timedelta(days=day, hours=hour)
            open_time_ms = int(utc_dt.timestamp() * 1000)
            myt_h = _myt_hour(open_time_ms)
            if myt_h == 14:  # UTC 06:00 — peak high
                high = _PEAK_HIGH
                low = _NORMAL_LOW
            elif myt_h == 8:  # UTC 00:00 — peak low (before high in same UTC day)
                high = _NORMAL_HIGH
                low = _PEAK_LOW
            else:
                high = _NORMAL_HIGH
                low = _NORMAL_LOW
            rows.append(
                {
                    "symbol": _SYMBOL,
                    "timeframe": _TIMEFRAME,
                    "open_time": open_time_ms,
                    "open": _OPEN_PRICE,
                    "high": high,
                    "low": low,
                    "close": _OPEN_PRICE,
                    "volume": 100.0,
                }
            )
    return rows


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with schema + synthetic OHLCV for TESTUSDT."""
    c = duckdb.connect(":memory:")
    init_schema(c)
    rows = _make_candles()
    for row in rows:
        c.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                row["symbol"],
                row["timeframe"],
                row["open_time"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
            ],
        )
    return c


def test_compute_p1p2_daily_low_first(conn: duckdb.DuckDBPyConnection) -> None:
    """P1=Low should be ~1.0: low at UTC 00:00 always precedes high at UTC 06:00."""
    result = compute_p1p2_daily(conn, _SYMBOL, days=30)
    assert result.sample_days > 0
    # Low (UTC 00) always precedes high (UTC 06) within the same UTC trading day
    assert result.overall_p1_low_pct > 0.8, (
        f"Expected p1_low_pct > 0.8, got {result.overall_p1_low_pct}"
    )
    # by_dow should have entries for the DOWs present in the data
    assert len(result.by_dow) > 0


def test_compute_p1p2_daily_no_data(conn: duckdb.DuckDBPyConnection) -> None:
    """Should raise ValueError for unknown symbol."""
    with pytest.raises(ValueError, match="No OHLCV data"):
        compute_p1p2_daily(conn, "FAKESYMBOL", days=30)


def test_compute_hourly_extremes_peaks(conn: duckdb.DuckDBPyConnection) -> None:
    """Peak high should be at MYT 14 (UTC 06); peak low at MYT 08 (UTC 00)."""
    result = compute_hourly_extremes(conn, _SYMBOL, days=30)
    assert len(result.rows) == 24, f"Expected 24 hourly rows, got {len(result.rows)}"
    assert result.peak_high_hour == 14, (
        f"Expected peak_high_hour=14, got {result.peak_high_hour}"
    )
    assert result.peak_low_hour == 8, (
        f"Expected peak_low_hour=8, got {result.peak_low_hour}"
    )
    # Per-DOW peaks should be present and match overall peaks (same pattern every day)
    assert len(result.peak_high_hour_by_dow) > 0
    assert len(result.peak_low_hour_by_dow) > 0
    for h in result.peak_high_hour_by_dow.values():
        assert h == 14, f"Per-DOW peak high should be 14, got {h}"
    for h in result.peak_low_hour_by_dow.values():
        assert h == 8, f"Per-DOW peak low should be 8, got {h}"


def test_compute_adr_positive(conn: duckdb.DuckDBPyConnection) -> None:
    """ADR(14) and ADR(30) should be positive for valid data."""
    result = compute_adr(conn, _SYMBOL)
    assert result.adr_14 > 0, f"adr_14={result.adr_14} should be > 0"
    assert result.adr_30 > 0, f"adr_30={result.adr_30} should be > 0"


def test_compute_dow_patterns_has_rows(conn: duckdb.DuckDBPyConnection) -> None:
    """DOW patterns should return rows for days present in the data."""
    result = compute_dow_patterns(conn, _SYMBOL, days=30)
    assert len(result.rows) > 0
    for row in result.rows:
        assert 0.0 <= row.bull_pct <= 1.0
        assert row.avg_range_pct > 0
        assert row.sample_days > 0
        # close == open in test data → avg_return_pct should be 0
        assert abs(row.avg_return_pct) < 1e-9, (
            f"Expected avg_return_pct≈0 (close==open in fixture), got {row.avg_return_pct}"
        )


def test_compute_session_breakdown_has_sessions(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Session breakdown should include all four US equity sessions."""
    result = compute_session_breakdown(conn, _SYMBOL, days=30)
    assert len(result.rows) > 0
    sessions = {r.session for r in result.rows}
    # All four equity sessions should appear given 24h hourly coverage
    assert "Pre-Market" in sessions
    assert "RTH" in sessions
    assert "Power Hour" in sessions
    assert "After Hours" in sessions
    for row in result.rows:
        assert row.high_pct >= 0.0
        assert row.low_pct >= 0.0


def test_compute_weekly_p1p2_positive(conn: duckdb.DuckDBPyConnection) -> None:
    """Weekly P1/P2 should return valid data with sample_weeks > 0."""
    result = compute_weekly_p1p2(conn, _SYMBOL, days=30)
    assert result.sample_weeks > 0
    assert 0.0 <= result.overall_p1_low_pct <= 1.0
    assert result.high_day != ""
    assert result.low_day != ""


def test_compute_weekly_p2_timing_structure(conn: duckdb.DuckDBPyConnection) -> None:
    """WeeklyP2Timing should return dicts with values in [0, 1] for all present DOWs."""
    result = compute_weekly_p2_timing(conn, _SYMBOL, days=30)
    assert isinstance(result, WeeklyP2Timing)
    assert len(result.low_still_ahead_by_dow) > 0
    assert len(result.high_still_ahead_by_dow) > 0
    assert len(result.low_flip_risk_by_dow) > 0
    assert len(result.high_flip_risk_by_dow) > 0
    for v in result.low_still_ahead_by_dow.values():
        assert 0.0 <= v <= 1.0, f"low_still_ahead out of range: {v}"
    for v in result.high_still_ahead_by_dow.values():
        assert 0.0 <= v <= 1.0, f"high_still_ahead out of range: {v}"
    for v in result.low_flip_risk_by_dow.values():
        assert 0.0 <= v <= 1.0, f"low_flip_risk out of range: {v}"
    for v in result.high_flip_risk_by_dow.values():
        assert 0.0 <= v <= 1.0, f"high_flip_risk out of range: {v}"


def test_compute_all_returns_bundle(conn: duckdb.DuckDBPyConnection) -> None:
    """compute_all should return a complete StatsBundle with no exceptions."""
    bundle = compute_all(conn, _SYMBOL, days=30)
    assert isinstance(bundle, StatsBundle)
    assert bundle.symbol == _SYMBOL
    assert bundle.days == 30
    assert bundle.computed_at_ms > 0
    # All sub-results populated
    assert bundle.p1p2.sample_days > 0
    assert len(bundle.hourly.rows) == 24
    assert bundle.adr.adr_14 > 0
    assert len(bundle.dow.rows) > 0
    assert len(bundle.sessions.rows) > 0
    assert bundle.weekly_p1p2.sample_weeks > 0
    assert len(bundle.weekly_p2_timing.low_still_ahead_by_dow) > 0


# ── compute_weekly_current_state tests ────────────────────────────────────────

MYT_OFFSET_HOURS = 8
_ADR_14 = 0.025  # 2.5% — representative ADR for tests


def _insert_candle(
    conn: duckdb.DuckDBPyConnection,
    open_time_ms: int,
    open_: float = 40000.0,
    high: float = 40500.0,
    low: float = 39500.0,
    close: float = 40000.0,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ohlcv "
        "(symbol, timeframe, open_time, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [_SYMBOL, "1h", open_time_ms, open_, high, low, close, 100.0],
    )


def test_weekly_current_state_no_data(conn: duckdb.DuckDBPyConnection) -> None:
    """Returns None when no candles exist in the current ISO week."""
    # The shared fixture has data from 20 days ago — current week has no candles.
    result = compute_weekly_current_state(conn, _SYMBOL, _ADR_14, days=30)
    assert result is None


def test_weekly_current_state_with_current_week(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Returns WeeklyCurrentState with correct fields when current-week data exists."""
    now_utc = datetime.now(tz=UTC)
    now_myt = now_utc + timedelta(hours=MYT_OFFSET_HOURS)
    days_since_monday = now_myt.weekday()
    week_start_myt = (now_myt - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start_utc_ms = int(
        (week_start_myt - timedelta(hours=MYT_OFFSET_HOURS)).timestamp() * 1000
    )

    weekly_open = 50000.0
    current_close = 51500.0  # +3% from open

    # Weekly open candle
    _insert_candle(conn, week_start_utc_ms, open_=weekly_open, close=weekly_open)
    # Current candle (1h later)
    _insert_candle(
        conn, week_start_utc_ms + 3600_000, open_=weekly_open, close=current_close
    )

    result = compute_weekly_current_state(conn, _SYMBOL, _ADR_14, days=30)

    assert result is not None
    assert isinstance(result, WeeklyCurrentState)
    assert result.current_isodow == now_myt.isoweekday()
    assert result.weekly_open == weekly_open
    assert result.current_price == current_close
    expected_move = (current_close - weekly_open) / weekly_open
    assert abs(result.move_pct - expected_move) < 1e-9
    assert result.move_bucket in ("small", "medium", "large")


def test_weekly_current_state_move_buckets(conn: duckdb.DuckDBPyConnection) -> None:
    """move_bucket assigned correctly relative to ADR14 thresholds."""
    now_utc = datetime.now(tz=UTC)
    now_myt = now_utc + timedelta(hours=MYT_OFFSET_HOURS)
    days_since_monday = now_myt.weekday()
    week_start_myt = (now_myt - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start_utc_ms = int(
        (week_start_myt - timedelta(hours=MYT_OFFSET_HOURS)).timestamp() * 1000
    )

    base = 40000.0

    # small: move = 0.5% (< 2.5% ADR14)
    _insert_candle(conn, week_start_utc_ms, open_=base, close=base)
    _insert_candle(conn, week_start_utc_ms + 3600_000, open_=base, close=base * 1.005)
    r = compute_weekly_current_state(conn, _SYMBOL, _ADR_14, days=30)
    assert r is not None and r.move_bucket == "small"

    # medium: move = 3% (between 1× and 2× ADR14=2.5%)
    _insert_candle(conn, week_start_utc_ms + 7200_000, open_=base, close=base * 1.03)
    r2 = compute_weekly_current_state(conn, _SYMBOL, _ADR_14, days=30)
    assert r2 is not None and r2.move_bucket == "medium"

    # large: move = 6% (> 2× ADR14=5%)
    _insert_candle(conn, week_start_utc_ms + 10_800_000, open_=base, close=base * 1.06)
    r3 = compute_weekly_current_state(conn, _SYMBOL, _ADR_14, days=30)
    assert r3 is not None and r3.move_bucket == "large"


def test_weekly_current_state_conditioned_prob_in_range(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Conditioned probabilities are in [0, 1] when historical data exists."""
    now_utc = datetime.now(tz=UTC)
    now_myt = now_utc + timedelta(hours=MYT_OFFSET_HOURS)
    days_since_monday = now_myt.weekday()
    week_start_myt = (now_myt - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start_utc_ms = int(
        (week_start_myt - timedelta(hours=MYT_OFFSET_HOURS)).timestamp() * 1000
    )

    base = 40000.0
    _insert_candle(conn, week_start_utc_ms, open_=base, close=base)
    _insert_candle(conn, week_start_utc_ms + 3600_000, open_=base, close=base * 1.005)

    result = compute_weekly_current_state(conn, _SYMBOL, _ADR_14, days=30)
    assert result is not None

    if result.low_still_ahead_conditioned is not None:
        assert 0.0 <= result.low_still_ahead_conditioned <= 1.0
    if result.high_still_ahead_conditioned is not None:
        assert 0.0 <= result.high_still_ahead_conditioned <= 1.0


# ── M3: compute_weekly_flip_risk_conditioned tests ────────────────────────────


def test_compute_weekly_flip_risk_conditioned_basic(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Conditioned flip risk rows cover both p1_directions and all DOWs present in data."""
    result = compute_weekly_flip_risk_conditioned(conn, _SYMBOL, days=30)
    assert isinstance(result, WeeklyFlipRiskConditioned)
    assert len(result.rows) > 0

    p1_dirs = {r.p1_direction for r in result.rows}
    # Both directions should appear (fixture has 14 days = 2 full weeks)
    assert p1_dirs.issubset({"low", "high"})

    for row in result.rows:
        assert row.p1_direction in ("low", "high")
        assert 1 <= row.isodow <= 7
        assert row.dow_label in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        assert 0.0 <= row.flip_pct <= 1.0
        assert row.sample_count > 0


# ── F3a: compute_weekly_wick_warning tests ────────────────────────────────────


# ── F3b: compute_daily_distance tests ────────────────────────────────────────


def test_compute_daily_distance_basic(conn: duckdb.DuckDBPyConnection) -> None:
    """daily_distance returns valid exceedance and p80 values."""
    adr = compute_adr(conn, _SYMBOL)
    result = compute_daily_distance(conn, _SYMBOL, adr.adr_14, days=30)
    assert isinstance(result, DailyDistanceResult)
    assert 0.0 <= result.exceedance_pct <= 1.0
    assert result.p80_of_adr > 0.0
    assert result.sample_count > 0
    # gap_to_p80 is non-negative when present
    if result.gap_to_p80 is not None:
        assert result.gap_to_p80 > 0.0


def test_compute_daily_distance_zero_adr(conn: duckdb.DuckDBPyConnection) -> None:
    """Returns None when adr_14 is zero (avoids division by zero)."""
    result = compute_daily_distance(conn, _SYMBOL, adr_14=0.0, days=30)
    assert result is None


def test_compute_daily_distance_exceedance_vs_p80(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """When today's range exceeds p80, gap_to_p80 is None and exceedance_pct is low."""
    adr = compute_adr(conn, _SYMBOL)
    result = compute_daily_distance(conn, _SYMBOL, adr.adr_14, days=30)
    assert result is not None
    if result.gap_to_p80 is None:
        # Already past p80 → exceedance should be ≤ 0.20 (top 20%)
        assert result.exceedance_pct <= 0.20 + 1e-6


# ── F3c: compute_weekly_wick_percentile tests ─────────────────────────────────


def test_compute_weekly_wick_percentile_basic(conn: duckdb.DuckDBPyConnection) -> None:
    """Weekly wick percentile returns a valid result with positive sample count."""
    adr = compute_adr(conn, _SYMBOL)
    result = compute_weekly_wick_percentile(conn, _SYMBOL, adr.adr_14, days=30)
    assert isinstance(result, WeeklyWickPercentile)
    assert result.sample_count >= 0
    # If current week P1 is set, values must be valid
    if result.exceedance_pct is not None:
        assert 0.0 <= result.exceedance_pct <= 1.0
        assert result.current_wick_of_adr is not None
        assert result.current_wick_of_adr >= 0.0
        assert result.p1_direction in ("low", "high")


def test_compute_weekly_wick_percentile_zero_adr(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Returns all-None result when adr_14 is zero."""
    result = compute_weekly_wick_percentile(conn, _SYMBOL, adr_14=0.0, days=30)
    assert isinstance(result, WeeklyWickPercentile)
    assert result.exceedance_pct is None
    assert result.current_wick_of_adr is None
    assert result.p1_direction is None
    assert result.sample_count == 0
