"""Tests for the analytics.strategies package (per-detector modules + seasonality)."""

import datetime
from collections.abc import Callable

import pandas as pd
import pytest

from analytics.strategies import (
    SIGNAL_COLUMNS,
    detect_eqh_eql,
    detect_funding_extreme,
    detect_fvg,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_marubozu_retest,
    detect_orb_breakout,
    detect_order_block,
    detect_trend_day,
    detect_wick_fills,
    seasonality_stats,
)
from tests.conftest import _candle, _make_ohlcv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MS_PER_HOUR = 3_600_000
_BASE_TIME = 1_700_000_000_000


def _hourly_ts(hour_offset: int) -> int:
    """Return a UTC timestamp at 00:00 + hour_offset hours."""
    base = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
    return int((base + datetime.timedelta(hours=hour_offset)).timestamp() * 1000)


def _assert_signal_columns(df: pd.DataFrame) -> None:
    assert list(df.columns) == SIGNAL_COLUMNS


# ---------------------------------------------------------------------------
# Seasonality stats
# ---------------------------------------------------------------------------


class TestSeasonalityStats:
    def test_returns_empty_on_empty_input(self) -> None:
        result = seasonality_stats(pd.DataFrame())
        assert result.empty

    def test_returns_empty_on_single_row(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 90, 105)])
        assert seasonality_stats(df).empty

    def test_returns_correct_period_types(self) -> None:
        rows = [
            _candle(_hourly_ts(i), 100.0, 110.0, 90.0, 105.0)
            for i in range(48)  # 2 full days of hourly candles
        ]
        df = _make_ohlcv(rows)
        stats = seasonality_stats(df)
        assert set(stats["period_type"].unique()) == {
            "day_of_week",
            "hour_of_day",
            "week_of_month",
        }

    def test_avg_return_direction(self) -> None:
        rows = [
            _candle(_hourly_ts(i), 100.0, 110.0, 90.0, 110.0)  # all green (up 10%)
            for i in range(24)
        ]
        df = _make_ohlcv(rows)
        stats = seasonality_stats(df)
        dow_stats = stats[stats["period_type"] == "day_of_week"]
        assert all(dow_stats["avg_return_pct"] > 0)
        assert all(dow_stats["win_rate"] == 1.0)

    def test_win_rate_all_losses(self) -> None:
        rows = [
            _candle(_hourly_ts(i), 110.0, 115.0, 85.0, 90.0)  # all red
            for i in range(24)
        ]
        df = _make_ohlcv(rows)
        stats = seasonality_stats(df)
        dow_stats = stats[stats["period_type"] == "day_of_week"]
        assert all(dow_stats["win_rate"] == 0.0)


# ---------------------------------------------------------------------------
# Wick Fill
# ---------------------------------------------------------------------------


class TestDetectWickFills:
    def test_returns_empty_on_fewer_than_3_candles(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 80, 105)])
        result = detect_wick_fills(df)
        assert result.empty
        _assert_signal_columns(result)

    def test_detects_lower_wick_fill_long(self) -> None:
        # Candle with big lower wick (body=2, wick=10) → next candle dips in
        rows = [
            _candle(_BASE_TIME + 0, 100, 103, 90, 102),  # lower wick=10 > 0.5×body=1
            _candle(_BASE_TIME + 1, 101, 103, 91, 102),  # low=91 ≤ zone_top=100
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=0.5)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["open_time"] == _BASE_TIME + 1

    def test_detects_upper_wick_fill_short(self) -> None:
        # Candle with big upper wick → next candle enters the zone
        rows = [
            _candle(_BASE_TIME + 0, 100, 115, 99, 101),  # upper wick=14 > 0.5×body=1
            _candle(_BASE_TIME + 1, 102, 114, 101, 103),  # high=114 ≥ zone_bot=101
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=0.5)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1

    def test_no_signal_when_wick_too_small(self) -> None:
        # Tiny wicks relative to body
        rows = [
            _candle(_BASE_TIME + 0, 100, 101, 99, 109),  # body=9, wick=0.1
            _candle(_BASE_TIME + 1, 105, 107, 98, 106),
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=2.0)
        assert result.empty

    def test_signal_columns(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 103, 90, 102) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Marubozu Retest
# ---------------------------------------------------------------------------


class TestDetectMarubozuRetest:
    def test_returns_empty_on_fewer_than_3_candles(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 99, 108)])
        result = detect_marubozu_retest(df)
        assert result.empty

    def test_detects_bullish_marubozu_retest_long(self) -> None:
        # Bullish Marubozu: open=100, high=110, low=99.5, close=110
        # body=10, upper_wick=0 (≤0.1×10), lower_wick=0.5 (≤0.1×10)
        rows = [
            _candle(_BASE_TIME + 0, 100, 110, 99.5, 110),  # marubozu
            _candle(_BASE_TIME + 1, 108, 112, 99, 101),  # retest: low≤100, close>100
        ]
        df = _make_ohlcv(rows)
        result = detect_marubozu_retest(df, max_wick_ratio=0.1)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"

    def test_detects_bearish_marubozu_retest_short(self) -> None:
        # Bearish Marubozu: open=110, high=110.5, low=100, close=100
        # body=10, upper_wick=0.5 (≤0.1×10), lower_wick=0 (≤0.1×10)
        rows = [
            _candle(_BASE_TIME + 0, 110, 110.5, 100, 100),  # bearish marubozu
            _candle(_BASE_TIME + 1, 105, 111, 104, 109),  # retest: high≥110, close<110
        ]
        df = _make_ohlcv(rows)
        result = detect_marubozu_retest(df, max_wick_ratio=0.1)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"

    def test_no_signal_when_wick_too_large(self) -> None:
        # Candle with large wicks → not a Marubozu
        rows = [
            _candle(_BASE_TIME + 0, 100, 115, 85, 110),  # big wicks
            _candle(_BASE_TIME + 1, 108, 112, 99, 101),
        ]
        df = _make_ohlcv(rows)
        result = detect_marubozu_retest(df, max_wick_ratio=0.1)
        assert result.empty


# ---------------------------------------------------------------------------
# ORB Breakout
# ---------------------------------------------------------------------------


class TestDetectOrbBreakout:
    """Tests for the 13:30 UTC RTH-session-anchor ORB implementation.

    Bars are partitioned into US trading sessions whose open is 13:30 UTC.
    Each bar's session date is its UTC timestamp shifted back by 13:30 hours
    and rounded to a date — so any bar in [13:30 UTC day N, 13:30 UTC day N+1)
    belongs to session N.

    _hourly_ts(N) returns 2024-01-01 00:00 UTC + N hours. Offsets 0–23 share
    one wall-clock day but they ALL precede the 13:30 UTC session open on
    2024-01-01, so they map to the prior session date (2023-12-31). This is
    intentional — the existing tests only care about within-session grouping
    and per-session dedup, not the absolute session label. Offsets 24+ cross
    into 2024-01-02 UTC and land in the next session.
    """

    def test_returns_empty_when_too_few_candles(self) -> None:
        # Default range_candles=1: candle 0 defines range [90, 110]; candle 1
        # closes at 108 (inside the range) → no breakout signal.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),
            _candle(_hourly_ts(1), 102, 112, 95, 108),
        ]
        df = _make_ohlcv(rows)
        assert detect_orb_breakout(df).empty

    def test_range_built_from_first_two_candles(self) -> None:
        # Candles at 00:00 and 01:00 define the range [88, 115].
        # Candle at 02:00 closes at 116 → long breakout above 115.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),  # high=110
            _candle(_hourly_ts(1), 105, 115, 88, 108),  # high=115, low=88
            _candle(_hourly_ts(2), 116, 120, 113, 116),  # close=116 > 115 → long
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["open_time"] == _hourly_ts(2)
        # SL should be range_low = 88
        assert float(result.iloc[0]["sl_price"]) == pytest.approx(88.0)

    def test_detects_long_breakout(self) -> None:
        # Range from candles 0–1: high=110, low=88.
        # Candle at hour 2 closes above range high.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),
            _candle(_hourly_ts(1), 104, 108, 88, 106),
            _candle(_hourly_ts(2), 111, 120, 108, 115),  # close=115 > 110 → long
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["open_time"] == _hourly_ts(2)

    def test_detects_short_breakout(self) -> None:
        # Range: high=110, low=88.  Candle 2 closes below range low.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),
            _candle(_hourly_ts(1), 104, 108, 88, 100),
            _candle(_hourly_ts(2), 89, 92, 80, 85),  # close=85 < 88 → short
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"
        # SL should be range_high = 110
        assert float(result.iloc[0]["sl_price"]) == pytest.approx(110.0)

    def test_no_signal_when_close_inside_range(self) -> None:
        # Range: [88, 110].  All subsequent candles stay inside.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),
            _candle(_hourly_ts(1), 104, 108, 88, 100),
            _candle(_hourly_ts(2), 103, 109, 89, 103),  # inside [88, 110]
            _candle(_hourly_ts(3), 100, 107, 91, 101),  # inside [88, 110]
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        assert result.empty

    def test_no_duplicate_signal_same_day_same_direction(self) -> None:
        # Two candles both break above range_high on the same day.
        # Only the first should produce a signal.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),
            _candle(_hourly_ts(1), 104, 108, 88, 100),
            _candle(_hourly_ts(2), 111, 115, 109, 112),  # 1st breakout → signal
            _candle(_hourly_ts(3), 113, 118, 110, 116),  # 2nd breakout → no dup
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 1
        assert long_signals.iloc[0]["open_time"] == _hourly_ts(2)

    def test_signals_reset_each_day(self) -> None:
        # Day 1 (hours 0–2): long breakout on hour 2.
        # Day 2 (hours 24–26): independent long breakout on hour 26.
        # Both should fire — different calendar days.
        rows = [
            # Day 1
            _candle(_hourly_ts(0), 100, 110, 90, 105),
            _candle(_hourly_ts(1), 104, 108, 88, 100),
            _candle(_hourly_ts(2), 111, 120, 109, 115),  # long breakout day 1
            # Day 2
            _candle(_hourly_ts(24), 200, 210, 190, 205),
            _candle(_hourly_ts(25), 204, 208, 188, 200),
            _candle(_hourly_ts(26), 211, 220, 209, 215),  # long breakout day 2
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 2

    def test_context_includes_tp(self) -> None:
        # Verify TP is embedded in the context string.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),  # high=110
            _candle(_hourly_ts(1), 104, 108, 88, 100),  # low=88  → range [88,110]
            _candle(_hourly_ts(2), 111, 120, 109, 115),  # close=115 → long
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        assert len(result) == 1
        assert "TP:" in str(result.iloc[0]["context"])

    def test_returns_empty_when_only_range_candles_present(self) -> None:
        # Exactly range_candles rows on one day — no breakout candle available.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),
        ]
        df = _make_ohlcv(rows)
        assert detect_orb_breakout(df, range_candles=1).empty


class TestOrbEquitySessionAnchor:
    """Tests that ORB groups bars by US RTH session (13:30 UTC open), not by UTC day."""

    def test_range_built_from_first_two_rth_bars(self) -> None:
        # 13:30, 14:30, 15:30 UTC — three bars inside one RTH session.
        # First two form the range [88, 115], third closes at 116 → long.
        rows = [
            _candle(
                _hourly_ts(13) + 30 * 60_000, 100, 110, 90, 105
            ),  # 13:30 UTC, h=110
            _candle(
                _hourly_ts(14) + 30 * 60_000, 105, 115, 88, 108
            ),  # 14:30 UTC, h=115, l=88
            _candle(
                _hourly_ts(15) + 30 * 60_000, 116, 120, 113, 116
            ),  # 15:30 UTC, close>115
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert float(result.iloc[0]["sl_price"]) == pytest.approx(88.0)

    def test_premarket_bar_belongs_to_prior_session(self) -> None:
        # A bar at 12:30 UTC on Jan 2 falls BEFORE the 13:30 UTC session-open on
        # Jan 2 → it belongs to the Jan 1 session. So grouping a 12:30 bar with
        # 13:30/14:30/15:30 bars on the same calendar day should split them into
        # two sessions: {12:30 alone, prior session} and {13:30, 14:30, 15:30}.
        # The 12:30 single bar cannot form a range; the 13:30+ session can.
        # Net: exactly one long signal from the 13:30 session.
        ms_30m = 30 * 60_000
        rows = [
            # Prior-session tail bar (premarket of Jan 2 wall-clock day):
            _candle(_hourly_ts(12) + ms_30m, 50, 60, 40, 55),  # 12:30 UTC Jan 1
            # New session opens 13:30 UTC Jan 1:
            _candle(_hourly_ts(13) + ms_30m, 100, 110, 90, 105),  # 13:30 UTC
            _candle(_hourly_ts(14) + ms_30m, 105, 115, 88, 108),  # 14:30 UTC
            _candle(_hourly_ts(15) + ms_30m, 116, 120, 113, 116),  # 15:30 UTC → long
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        # The lone 12:30 bar should NOT be folded into the 13:30 session's range.
        # If it were, the range high/low would include {60, 40} and the test would
        # see a different SL.
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert float(result.iloc[0]["sl_price"]) == pytest.approx(88.0)

    def test_sessions_split_at_13_30_utc_boundary(self) -> None:
        # Two independent RTH sessions, each with its own long breakout.
        # Session A: 2024-01-01 13:30, 14:30, 15:30 UTC
        # Session B: 2024-01-02 13:30, 14:30, 15:30 UTC (= +24h offsets)
        ms_30m = 30 * 60_000
        rows = [
            # Session A
            _candle(_hourly_ts(13) + ms_30m, 100, 110, 90, 105),
            _candle(_hourly_ts(14) + ms_30m, 104, 108, 88, 100),
            _candle(_hourly_ts(15) + ms_30m, 111, 120, 109, 115),  # long A
            # Session B (+24h)
            _candle(_hourly_ts(37) + ms_30m, 200, 210, 190, 205),
            _candle(_hourly_ts(38) + ms_30m, 204, 208, 188, 200),
            _candle(_hourly_ts(39) + ms_30m, 211, 220, 209, 215),  # long B
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, range_candles=2)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 2


# ---------------------------------------------------------------------------
# Liquidity Sweep
# ---------------------------------------------------------------------------


class TestDetectLiquiditySweep:
    def test_returns_empty_when_too_few_candles(self) -> None:
        # With swing_n=5 and lookback=20, need win+lookback = 11+20 = 31 candles minimum
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_liquidity_sweep(df, lookback=20, swing_n=5)
        assert result.empty

    def test_signal_columns(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(40)]
        df = _make_ohlcv(rows)
        result = detect_liquidity_sweep(df, lookback=20, swing_n=5)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Fair Value Gap (FVG)
# ---------------------------------------------------------------------------


class TestDetectFvg:
    def test_returns_empty_on_fewer_than_3_candles(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(2)]
        df = _make_ohlcv(rows)
        assert detect_fvg(df).empty

    def test_detects_bullish_fvg_fill_long(self) -> None:
        # Bullish FVG: candle[0].high=100, candle[2].low=105 → gap [100, 105]
        # Fill candle: low=101 enters gap
        rows = [
            _candle(_BASE_TIME + 0, 95, 100, 93, 99),
            _candle(_BASE_TIME + 1, 102, 108, 101, 107),  # impulse candle
            _candle(_BASE_TIME + 2, 107, 112, 105, 110),  # nxt.low=105 > prev.high=100
            _candle(_BASE_TIME + 3, 109, 112, 101, 103),  # low=101 ≤ gap_top=105
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["open_time"] == _BASE_TIME + 3

    def test_detects_bearish_fvg_fill_short(self) -> None:
        # Bearish FVG: candle[0].low=100, candle[2].high=95 → gap [95, 100], CE=97.5
        # Fill candle: high=98 ≥ CE=97.5 and close=92 < gap_top=100
        rows = [
            _candle(_BASE_TIME + 0, 105, 108, 100, 102),
            _candle(_BASE_TIME + 1, 98, 99, 92, 93),  # impulse candle
            _candle(_BASE_TIME + 2, 92, 95, 88, 90),  # nxt.high=95 < prev.low=100
            _candle(_BASE_TIME + 3, 91, 98, 88, 92),  # high=98 ≥ CE=97.5
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"

    def test_no_signal_when_no_gap(self) -> None:
        # Overlapping candles — no FVG
        rows = [
            _candle(_BASE_TIME + 0, 100, 105, 95, 103),
            _candle(_BASE_TIME + 1, 103, 108, 100, 106),
            _candle(_BASE_TIME + 2, 106, 110, 104, 108),
            _candle(_BASE_TIME + 3, 108, 112, 105, 109),
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty

    def test_signal_columns(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Market Structure (BOS / CHoCH)
# ---------------------------------------------------------------------------


class TestDetectMarketStructure:
    def _make_zigzag_up(self) -> pd.DataFrame:
        """Alternating peak/valley candles in an uptrend.

        Pattern: valley, PEAK1, valley, PEAK2>PEAK1, valley, PEAK3>PEAK2
        With swing_lookback=1, peaks are detected as swing highs because they
        are higher than their immediate neighbours.
        """
        valley_high = 98.0  # all valley candles have high below peaks
        peaks = [110.0, 120.0, 130.0]
        rows: list[dict[str, object]] = []
        t = _BASE_TIME
        for peak in peaks:
            rows.append(_candle(t, 90, valley_high, 88, 95))  # valley
            rows.append(_candle(t + 1, 102, peak, 100, peak - 1))  # peak
            rows.append(_candle(t + 2, 96, valley_high, 88, 94))  # valley
            t += 3
        return _make_ohlcv(rows)

    def _make_zigzag_down(self) -> pd.DataFrame:
        """Alternating peak/valley candles in a downtrend.

        Valleys: 95, 85, 75 — each lower than the previous.
        """
        peak_low = 103.0  # all peak candles have low above valleys
        valleys = [95.0, 85.0, 75.0]
        rows: list[dict[str, object]] = []
        t = _BASE_TIME
        for valley in valleys:
            rows.append(_candle(t, 105, 108, peak_low, 106))  # peak
            rows.append(_candle(t + 1, 100, peak_low, valley, valley + 1))  # valley
            rows.append(_candle(t + 2, 105, 108, peak_low, 106))  # peak
            t += 3
        return _make_ohlcv(rows)

    def test_returns_empty_on_too_few_candles(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_market_structure(df, swing_lookback=5)
        assert result.empty

    def test_detects_bos_long_in_uptrend(self) -> None:
        df = self._make_zigzag_up()
        result = detect_market_structure(df, swing_lookback=1)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1

    def test_detects_bos_short_in_downtrend(self) -> None:
        df = self._make_zigzag_down()
        result = detect_market_structure(df, swing_lookback=1)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1

    def test_signal_columns(self) -> None:
        df = self._make_zigzag_up()
        result = detect_market_structure(df, swing_lookback=1)
        _assert_signal_columns(result)

    def test_min_swing_pct_default_applied(self) -> None:
        """Default min_swing_pct=0.005 should match explicit 0.005."""
        df = self._make_zigzag_up()
        result_default = detect_market_structure(df, swing_lookback=1)
        result_explicit = detect_market_structure(
            df, swing_lookback=1, min_swing_pct=0.005
        )
        assert len(result_default) == len(result_explicit)
        assert list(result_default["direction"]) == list(result_explicit["direction"])

    def test_min_swing_pct_suppresses_small_swing_long(self) -> None:
        """Signals where (sh - sl) / sh < min_swing_pct must be suppressed (long)."""
        # zigzag_up: peak=130, sl=88 → swing_range = (130-88)/130 ≈ 0.323
        # threshold 0.35 > 0.323 → suppress
        df = self._make_zigzag_up()
        result = detect_market_structure(df, swing_lookback=1, min_swing_pct=0.35)
        long_signals = result[result["direction"] == "long"]
        assert long_signals.empty

    def test_min_swing_pct_passes_large_swing_long(self) -> None:
        """Signals where (sh - sl) / sh >= min_swing_pct must be emitted (long)."""
        # zigzag_up: peak=130, sl=88 → swing_range = (130-88)/130 ≈ 0.323
        # threshold 0.20 < 0.323 → pass
        df = self._make_zigzag_up()
        result = detect_market_structure(df, swing_lookback=1, min_swing_pct=0.20)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1

    def test_min_swing_pct_suppresses_small_swing_short(self) -> None:
        """Signals where (sh - sl) / sl < min_swing_pct must be suppressed (short)."""
        # zigzag_down: sh=108, valley=75 → swing_range = (108-75)/75 = 0.44
        # threshold 0.50 > 0.44 → suppress
        df = self._make_zigzag_down()
        result = detect_market_structure(df, swing_lookback=1, min_swing_pct=0.50)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty

    def test_min_swing_pct_passes_large_swing_short(self) -> None:
        """Signals where (sh - sl) / sl >= min_swing_pct must be emitted (short)."""
        # zigzag_down: sh=108, valley=75 → swing_range = (108-75)/75 = 0.44
        # threshold 0.20 < 0.44 → pass
        df = self._make_zigzag_down()
        result = detect_market_structure(df, swing_lookback=1, min_swing_pct=0.20)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1


# ---------------------------------------------------------------------------
# Funding Rate Mean Reversion
# ---------------------------------------------------------------------------


class TestDetectFundingExtreme:
    def _make_funding(self, rates: list[float]) -> pd.DataFrame:
        rows = [
            {
                "symbol": "BTCUSDT",
                "funding_time": _BASE_TIME + i * _MS_PER_HOUR * 8,
                "funding_rate": r,
            }
            for i, r in enumerate(rates)
        ]
        return pd.DataFrame(rows, columns=["symbol", "funding_time", "funding_rate"])

    def test_returns_empty_on_empty_ohlcv(self) -> None:
        funding = self._make_funding([0.001])
        result = detect_funding_extreme(pd.DataFrame(), funding)
        assert result.empty

    def test_returns_empty_on_empty_funding(self) -> None:
        ohlcv = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 90, 100)])
        result = detect_funding_extreme(ohlcv, pd.DataFrame())
        assert result.empty

    def test_detects_positive_extreme_short(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + i * _MS_PER_HOUR * 8, 100, 110, 90, 100)
                for i in range(5)
            ]
        )
        funding = self._make_funding([0.002, 0.003, 0.002, 0.002, 0.003])
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1

    def test_detects_negative_extreme_long(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + i * _MS_PER_HOUR * 8, 100, 110, 90, 100)
                for i in range(5)
            ]
        )
        funding = self._make_funding([-0.002, -0.003, -0.002, -0.002, -0.003])
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1

    def test_no_signal_when_funding_within_threshold(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + i * _MS_PER_HOUR * 8, 100, 110, 90, 100)
                for i in range(5)
            ]
        )
        funding = self._make_funding([0.0001, 0.0002, -0.0001, 0.0003, -0.0002])
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        assert result.empty

    def test_signal_columns(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + i * _MS_PER_HOUR * 8, 100, 110, 90, 100)
                for i in range(5)
            ]
        )
        funding = self._make_funding([0.002, 0.002, 0.002, 0.002, 0.002])
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Bug fix tests (TDD — written before fixes)
# ---------------------------------------------------------------------------


class TestFvgBugFixes:
    def test_fvg_long_entry_at_ce_not_gap_top(self) -> None:
        # Bullish FVG: gap_bot=100, gap_top=105, CE=102.5
        # Fill candle: low=103 (enters gap_top but NOT CE) → no signal
        rows = [
            _candle(_BASE_TIME + 0, 95, 100, 93, 99),
            _candle(_BASE_TIME + 1, 102, 108, 101, 107),
            _candle(_BASE_TIME + 2, 107, 112, 105, 110),  # gap [100, 105]
            _candle(
                _BASE_TIME + 3, 109, 112, 103, 111
            ),  # low=103 > CE=102.5 → no signal
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty

    def test_fvg_long_fires_when_price_reaches_ce(self) -> None:
        # Fill candle: low=102 ≤ CE=102.5 → signal fires
        rows = [
            _candle(_BASE_TIME + 0, 95, 100, 93, 99),
            _candle(_BASE_TIME + 1, 102, 108, 101, 107),
            _candle(_BASE_TIME + 2, 107, 112, 105, 110),  # gap [100, 105]
            _candle(_BASE_TIME + 3, 109, 112, 102, 111),  # low=102 ≤ CE=102.5 → signal
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"

    def test_fvg_long_does_not_fire_when_price_blasts_through_gap(self) -> None:
        # Fill candle closes BELOW gap_bot=100 → no signal (blasted through)
        rows = [
            _candle(_BASE_TIME + 0, 95, 100, 93, 99),
            _candle(_BASE_TIME + 1, 102, 108, 101, 107),
            _candle(_BASE_TIME + 2, 107, 112, 105, 110),  # gap [100, 105]
            _candle(_BASE_TIME + 3, 109, 112, 98, 99),  # low=98, close=99 < gap_bot=100
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty

    def test_fvg_short_entry_at_ce_not_gap_bot(self) -> None:
        # Bearish FVG: gap_top=100, gap_bot=95, CE=97.5
        # Fill candle: high=97 (above gap_bot but NOT CE) → no signal
        rows = [
            _candle(_BASE_TIME + 0, 105, 108, 100, 102),
            _candle(_BASE_TIME + 1, 98, 99, 92, 93),
            _candle(_BASE_TIME + 2, 92, 95, 88, 90),  # gap [95, 100]
            _candle(_BASE_TIME + 3, 91, 97, 88, 92),  # high=97 < CE=97.5 → no signal
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty

    def test_fvg_short_does_not_fire_when_price_blasts_through(self) -> None:
        # Fill candle closes ABOVE gap_top=100 → no signal
        rows = [
            _candle(_BASE_TIME + 0, 105, 108, 100, 102),
            _candle(_BASE_TIME + 1, 98, 99, 92, 93),
            _candle(_BASE_TIME + 2, 92, 95, 88, 90),  # gap [95, 100]
            _candle(
                _BASE_TIME + 3, 91, 102, 88, 101
            ),  # high=102, close=101 > gap_top=100
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty


class TestFvgTrendFilter:
    """Tests for detect_fvg trend_filter parameter."""

    def test_trend_filter_suppresses_long_fvg_below_ema50(self) -> None:
        # Build 55 candles trending down so EMA-50 is above close at signal time.
        # Bullish FVG gap forms at the end but price is below EMA-50 → suppressed.
        rows = []
        # 52 strongly bearish candles to push EMA-50 high
        for k in range(52):
            price = 200.0 - k * 1.5
            rows.append(
                _candle(_BASE_TIME + k, price, price + 1, price - 1, price - 0.5)
            )
        # FVG: candle[-3].high=125, candle[-1].low=130 → gap [125, 130]
        rows.append(
            _candle(_BASE_TIME + 52, 126, 125, 123, 124)
        )  # candle i-1: high=125
        rows.append(_candle(_BASE_TIME + 53, 124, 127, 122, 123))  # candle i (middle)
        rows.append(_candle(_BASE_TIME + 54, 128, 131, 130, 131))  # candle i+1: low=130
        # Fill candle: low=126 ≤ CE=127.5, close=128 > gap_bot=125 — but below EMA-50
        rows.append(_candle(_BASE_TIME + 55, 129, 130, 126, 128))
        df = _make_ohlcv(rows)
        result = detect_fvg(df, trend_filter=1)
        long_signals = result[result["direction"] == "long"]
        assert long_signals.empty

    def test_trend_filter_off_allows_long_fvg_below_ema50(self) -> None:
        # Same setup as above but trend_filter=0 — signal should fire.
        rows = []
        for k in range(52):
            price = 200.0 - k * 1.5
            rows.append(
                _candle(_BASE_TIME + k, price, price + 1, price - 1, price - 0.5)
            )
        df = _make_ohlcv(rows)
        # Confirm trend_filter=1 suppresses (the EMA-50 is above close by this point)
        result_on = detect_fvg(df, trend_filter=1)
        # trend_filter=0 should allow those same bearish signals through — we just verify
        # the absence of the filter doesn't crash and returns more results
        result_off = detect_fvg(df, trend_filter=0)
        assert len(result_off) >= len(result_on)

    def test_trend_filter_zero_allows_long_fvg_minimal(self) -> None:
        # Minimal bullish FVG with trend_filter=0 — signal fires regardless of EMA position.
        rows = [
            _candle(_BASE_TIME + 0, 99, 100, 98, 99),  # i-1: high=100
            _candle(_BASE_TIME + 1, 102, 104, 101, 103),  # i (middle)
            _candle(
                _BASE_TIME + 2, 106, 108, 105, 107
            ),  # i+1: low=105 > 100 → bullish FVG
            _candle(
                _BASE_TIME + 3, 104, 106, 101, 103
            ),  # fill: low=101 ≤ CE=102.5, close=103>100
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df, trend_filter=0)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 1

    def test_trend_filter_suppresses_short_fvg_above_ema50(self) -> None:
        # Build 52 strongly bullish candles so EMA-50 is below close at signal time.
        # Bearish FVG forms but price is above EMA-50 → suppressed.
        rows = []
        for k in range(52):
            price = 100.0 + k * 1.5
            rows.append(
                _candle(_BASE_TIME + k, price, price + 0.5, price - 0.5, price + 0.5)
            )
        # Bearish FVG: candle[-3].low=178, candle[-1].high=173 → gap [173, 178]
        rows.append(_candle(_BASE_TIME + 52, 179, 181, 178, 180))  # i-1: low=178
        rows.append(_candle(_BASE_TIME + 53, 180, 182, 177, 178))  # i (middle)
        rows.append(_candle(_BASE_TIME + 54, 176, 174, 172, 173))  # i+1: high=174 < 178
        # Fill candle: high=176 ≥ CE=175.5, close=174 < gap_top=178 — but above EMA-50
        rows.append(_candle(_BASE_TIME + 55, 174, 176, 173, 174))
        df = _make_ohlcv(rows)
        result = detect_fvg(df, trend_filter=1)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty

    def test_trend_filter_off_allows_short_fvg_above_ema50(self) -> None:
        # Same setup but trend_filter=0 — signal should fire.
        rows = []
        for k in range(52):
            price = 100.0 + k * 1.5
            rows.append(
                _candle(_BASE_TIME + k, price, price + 0.5, price - 0.5, price + 0.5)
            )
        rows.append(_candle(_BASE_TIME + 52, 179, 181, 178, 180))
        rows.append(_candle(_BASE_TIME + 53, 180, 182, 177, 178))
        rows.append(_candle(_BASE_TIME + 54, 176, 174, 172, 173))
        rows.append(_candle(_BASE_TIME + 55, 174, 176, 173, 174))
        df = _make_ohlcv(rows)
        result = detect_fvg(df, trend_filter=0)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1


class TestWickFillBugFixes:
    def test_wick_fill_long_does_not_fire_when_close_below_zone(self) -> None:
        # Lower wick zone: zone_bot=90, zone_top=100
        # Fill candle: low=91 enters zone but close=89 < zone_bot=90 → no signal
        rows = [
            _candle(_BASE_TIME + 0, 100, 103, 90, 102),  # lower wick=10 > 0.5×body=1
            _candle(
                _BASE_TIME + 1, 101, 103, 91, 89
            ),  # low=91 ≤ zone_top, close=89 < zone_bot
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=0.5)
        long_signals = result[result["direction"] == "long"]
        assert long_signals.empty

    def test_wick_fill_short_does_not_fire_when_close_above_zone(self) -> None:
        # Upper wick zone: zone_bot=101, zone_top=115
        # Fill candle: high=114 enters zone but close=116 > zone_top=115 → no signal
        rows = [
            _candle(_BASE_TIME + 0, 100, 115, 99, 101),  # upper wick=14 > 0.5×body=1
            _candle(
                _BASE_TIME + 1, 102, 114, 101, 116
            ),  # high=114 ≥ zone_bot, close=116 > zone_top
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=0.5)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty


class TestFundingReversionBugFixes:
    def _make_ohlcv_15m(self, n: int, base_time: int) -> pd.DataFrame:
        """15-minute candles."""
        rows = [
            _candle(base_time + i * 15 * 60 * 1000, 100, 110, 90, 100) for i in range(n)
        ]
        return _make_ohlcv(rows)

    def _make_funding_row(self, funding_time: int, rate: float) -> dict[str, object]:
        return {"symbol": "BTCUSDT", "funding_time": funding_time, "funding_rate": rate}

    def test_funding_reversion_fires_once_per_funding_period(self) -> None:
        # 8h window = 32 × 15m candles; same extreme funding rate → only 1 signal
        n = 32
        ohlcv = self._make_ohlcv_15m(n, _BASE_TIME)
        funding = pd.DataFrame(
            [self._make_funding_row(_BASE_TIME, 0.002)],
            columns=["symbol", "funding_time", "funding_rate"],
        )
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        assert len(result) == 1

    def test_funding_reversion_fires_again_on_new_funding_period(self) -> None:
        # Two separate 8h periods with extreme funding → 2 signals
        ms_per_8h = 8 * 60 * 60 * 1000
        ms_per_15m = 15 * 60 * 1000
        rows = [
            _candle(_BASE_TIME + i * ms_per_15m, 100, 110, 90, 100)
            for i in range(64)  # 2 full 8h periods
        ]
        ohlcv = _make_ohlcv(rows)
        funding = pd.DataFrame(
            [
                self._make_funding_row(_BASE_TIME, 0.002),
                self._make_funding_row(_BASE_TIME + ms_per_8h, 0.002),
            ],
            columns=["symbol", "funding_time", "funding_rate"],
        )
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        assert len(result) == 2


class TestOrbBackwardsCompatibility:
    """Verify that legacy keyword args (session_hour_utc, timeframe_minutes) are
    accepted without raising even though they are now ignored."""

    def test_legacy_kwargs_accepted_without_error(self) -> None:
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),
            _candle(_hourly_ts(1), 104, 108, 88, 100),
            _candle(_hourly_ts(2), 111, 120, 108, 115),
        ]
        df = _make_ohlcv(rows)
        # These kwargs are silently ignored; the call must not raise.
        result = detect_orb_breakout(df, session_hour_utc=13, timeframe_minutes=15)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"

    def test_orb_works_on_any_timeframe(self) -> None:
        # New impl has no TF guard — hourly candles are valid.
        rows = [
            _candle(_hourly_ts(0), 100, 110, 90, 105),
            _candle(_hourly_ts(1), 104, 108, 88, 100),
            _candle(_hourly_ts(2), 111, 120, 108, 115),
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, timeframe_minutes=60)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"


class TestBosNoLookaheadBias:
    def test_bos_does_not_use_future_candles(self) -> None:
        # Build a series where a swing high at candle i=5 is ONLY the rolling max
        # because of candles i=6..i+swing_lookback (future candles).
        # With center=False, candle i=5 should NOT be detected as a swing high
        # until we have enough trailing candles — and the signal should not fire
        # on the SAME candle that creates the new swing high.
        #
        # Pattern: flat 100s, then spike to 120 at i=5, then drop back
        # With center=True, i=5 is immediately a swing high (uses i=6..i+5)
        # With center=False, i=5 is only swing high after i=10 confirms (trailing)
        swing_lookback = 5
        rows = []
        for i in range(5):
            rows.append(_candle(_BASE_TIME + i, 100, 105, 95, 100))
        # spike at i=5
        rows.append(_candle(_BASE_TIME + 5, 110, 120, 108, 115))
        # drop back — these would "confirm" with center=True lookahead
        for i in range(6, 16):
            rows.append(_candle(_BASE_TIME + i, 100, 105, 95, 100))

        df = _make_ohlcv(rows)
        result = detect_market_structure(df, swing_lookback=swing_lookback)
        # With center=False, the spike candle (open_time=_BASE_TIME+5) must NOT
        # appear as a signal open_time — the signal can only fire AFTER trailing
        # candles confirm the swing.
        spike_time = _BASE_TIME + 5
        if not result.empty:
            assert spike_time not in result["open_time"].values


# ---------------------------------------------------------------------------
# Equal Highs / Equal Lows (EQH / EQL)
# ---------------------------------------------------------------------------


class TestDetectEqhEql:
    """Tests for detect_eqh_eql — EQH/EQL liquidity sweep detector."""

    # Minimum lookback + 1 signal candle needed: lookback=7 + 1 = 8 rows minimum
    _LOOKBACK = 10
    _MS = _MS_PER_HOUR

    def _base_df(self, n: int = 12, base: float = 100.0) -> pd.DataFrame:
        """Build n flat candles at base price (open=high=low=close=base)."""
        rows = [
            _candle(_BASE_TIME + i * self._MS, base, base, base, base) for i in range(n)
        ]
        return _make_ohlcv(rows)

    # ---- No-signal cases ----

    def test_returns_empty_when_not_enough_candles(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 105, 95, 100)])
        result = detect_eqh_eql(df, lookback=self._LOOKBACK)
        assert result.empty
        _assert_signal_columns(result)

    def test_no_signal_when_no_equal_highs(self) -> None:
        # All candles have strictly different highs → no EQH pair
        rows = [
            _candle(_BASE_TIME + i * self._MS, 100, 100 + i * 5, 90, 100)
            for i in range(self._LOOKBACK + 1)
        ]
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK)
        assert result.empty

    def test_no_signal_when_candle_breaks_out_above_eqh(self) -> None:
        # Signal candle wicks above EQH AND closes above it → breakout, not sweep
        rows = [
            _candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 118),
            _candle(_BASE_TIME + 1 * self._MS, 100, 120, 95, 117),  # equal high pair
        ]
        for i in range(2, self._LOOKBACK - 1):
            rows.append(_candle(_BASE_TIME + i * self._MS, 100, 110, 90, 100))
        # Signal candle: high > 120 and close > 120 → no sweep
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 118, 125, 115, 122))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.003)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty

    # ---- Short signal (EQH sweep) ----

    def test_short_signal_fires_when_candle_sweeps_eqh(self) -> None:
        # Two swing highs at ~120 in the lookback window.
        # Signal candle: high=121 (> 120), close=118 (< 120) → short.
        rows: list[dict[str, object]] = []
        # Candle 0: swing high at 120
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        # Candle 4: second swing high at 120
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        # Signal candle: wick above 120, closes below 120
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 115, 121, 110, 118))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) == 1

    def test_short_signal_open_time_is_signal_candle(self) -> None:
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        sig_ts = _BASE_TIME + self._LOOKBACK * self._MS
        rows.append(_candle(sig_ts, 115, 121, 110, 118))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        assert result.iloc[0]["open_time"] == sig_ts

    def test_short_sl_price_is_max_post_eqh_deviation(self) -> None:
        # Use lookback=16 so there's room between EQH pair (indices 4+8) and a
        # post-EQH deviation candle (index 12, high=130).
        # Pre-EQH candle at index 0 (high=125) must be IGNORED — SL only scans
        # from the later EQH candle onwards.
        lookback = 16
        rows: list[dict[str, object]] = []
        rows.append(
            _candle(_BASE_TIME + 0 * self._MS, 118, 125, 105, 120)
        )  # pre-EQH, high=125 (must be ignored)
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 112, 100, 111))
        rows.append(
            _candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115)
        )  # EQH swing high 1
        for i in range(5, 8):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 112, 100, 111))
        rows.append(
            _candle(_BASE_TIME + 8 * self._MS, 110, 120, 100, 115)
        )  # EQH swing high 2
        for i in range(9, 12):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 112, 100, 111))
        rows.append(
            _candle(_BASE_TIME + 12 * self._MS, 118, 130, 115, 126)
        )  # post-EQH deviation, high=130
        for i in range(13, lookback):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 112, 100, 111))
        rows.append(
            _candle(_BASE_TIME + lookback * self._MS, 115, 121, 110, 118)
        )  # signal candle
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=lookback, tolerance_pct=0.01, swing_n=2)
        sl = float(result.iloc[0]["sl_price"])
        assert (
            sl == 130.0
        )  # post-EQH deviation, not pre-EQH (125) or signal candle (121)

    def test_short_context_contains_two_timestamps(self) -> None:
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 115, 121, 110, 118))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        ctx = str(result.iloc[0]["context"])
        assert ctx.startswith("EQH:")
        # Two timestamps separated by ·
        assert " · " in ctx

    def test_no_eqh_signal_when_first_pivot_already_taken_out(self) -> None:
        # EQH pair at 120. Between pivot 1 (index 0) and pivot 2 (index 4), a candle
        # wicks above 120 (index 2, high=122) — the pool was already raided.
        # No short signal should fire even though the signal candle sweeps 120.
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))  # pivot 1
        rows.append(_candle(_BASE_TIME + 1 * self._MS, 110, 115, 100, 112))
        rows.append(
            _candle(_BASE_TIME + 2 * self._MS, 115, 122, 110, 116)  # breaks above 120!
        )
        rows.append(_candle(_BASE_TIME + 3 * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))  # pivot 2
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(
            _candle(_BASE_TIME + self._LOOKBACK * self._MS, 115, 121, 110, 118)
        )  # signal candle sweeps 120
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty, (
            "first pivot was already raided — EQH pair is invalid"
        )

    def test_no_eql_signal_when_first_pivot_already_taken_out(self) -> None:
        # EQL pair at 80. Between pivot 1 (index 0) and pivot 2 (index 4), a candle
        # wicks below 80 (index 2, low=78) — the pool was already raided.
        # No long signal should fire even though the signal candle sweeps 80.
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 105, 80, 85))  # pivot 1
        rows.append(_candle(_BASE_TIME + 1 * self._MS, 90, 95, 85, 92))
        rows.append(
            _candle(_BASE_TIME + 2 * self._MS, 85, 90, 78, 86)  # breaks below 80!
        )
        rows.append(_candle(_BASE_TIME + 3 * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 90, 95, 80, 85))  # pivot 2
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(
            _candle(_BASE_TIME + self._LOOKBACK * self._MS, 85, 90, 79, 82)
        )  # signal candle sweeps 80
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        long_signals = result[result["direction"] == "long"]
        assert long_signals.empty, (
            "first pivot was already raided — EQL pair is invalid"
        )

    # ---- Long signal (EQL sweep) ----

    def test_long_signal_fires_when_candle_sweeps_eql(self) -> None:
        # Two swing lows at ~80. Signal candle: low=79 (< 80), close=82 (> 80) → long.
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 105, 80, 85))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 90, 95, 80, 85))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        # Signal candle: wick below 80, closes above 80
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 85, 90, 79, 82))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 1

    def test_long_sl_price_is_min_post_eql_deviation(self) -> None:
        # Use lookback=16 so there's room between EQL pair (indices 4+8) and a
        # post-EQL deviation candle (index 12, low=70).
        # Pre-EQL candle at index 0 (low=75) must be IGNORED — SL only scans
        # from the later EQL candle onwards.
        lookback = 16
        rows: list[dict[str, object]] = []
        rows.append(
            _candle(_BASE_TIME + 0 * self._MS, 75, 84, 75, 74)
        )  # pre-EQL, low=75 (must be ignored)
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 88, 92, 85, 90))
        rows.append(
            _candle(_BASE_TIME + 4 * self._MS, 88, 92, 80, 85)
        )  # EQL swing low 1
        for i in range(5, 8):
            rows.append(_candle(_BASE_TIME + i * self._MS, 88, 92, 85, 90))
        rows.append(
            _candle(_BASE_TIME + 8 * self._MS, 88, 92, 80, 85)
        )  # EQL swing low 2
        for i in range(9, 12):
            rows.append(_candle(_BASE_TIME + i * self._MS, 88, 92, 85, 90))
        rows.append(
            _candle(_BASE_TIME + 12 * self._MS, 75, 84, 70, 74)
        )  # post-EQL deviation, low=70
        for i in range(13, lookback):
            rows.append(_candle(_BASE_TIME + i * self._MS, 88, 92, 85, 90))
        rows.append(
            _candle(_BASE_TIME + lookback * self._MS, 85, 90, 79, 82)
        )  # signal candle
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=lookback, tolerance_pct=0.01, swing_n=2)
        sl = float(result.iloc[0]["sl_price"])
        assert sl == 70.0  # post-EQL deviation, not pre-EQL (75) or signal candle (79)

    def test_long_context_contains_two_timestamps(self) -> None:
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 105, 80, 85))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 90, 95, 80, 85))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 85, 90, 79, 82))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        ctx = str(result.iloc[0]["context"])
        assert ctx.startswith("EQL:")
        assert " · " in ctx

    def test_no_long_signal_when_candle_closes_below_eql(self) -> None:
        # Signal candle wicks below EQL AND closes below it → not a reversal
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 105, 80, 85))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 90, 95, 80, 85))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        # Close = 78 < eql_level = 80 → no signal
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 85, 90, 75, 78))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        long_signals = result[result["direction"] == "long"]
        assert long_signals.empty

    def test_signal_columns_match(self) -> None:
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 115, 121, 110, 118))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        _assert_signal_columns(result)


class TestLiquiditySweep:
    """Tests for detect_liquidity_sweep with pivot+fib-extension logic.

    Data layout (swing_n=2, lookback=8, total=13 rows, signal at index 12):
      win = 2×2+1 = 5; guard: n >= win+lookback = 13 ✓

    SHORT setup — pivot_low @ index 4, pivot_high @ index 8:
      Neighbours of pivots shaped to prevent flat background candles from
      becoming spurious pivot highs/lows within the lookback window.

        idx  high  low   role
          0   102   92   background
          1   102   92   background
          2   102   92   background
          3   102   84   slope toward pivot_low
          4   102   80   PIVOT LOW  (min in window [2..6])
          5   102   83   slope away from pivot_low
          6   102   87   slope away
          7    98   90   slope toward pivot_high (lower high prevents it being a pivot)
          8   120   90   PIVOT HIGH (max in window [6..10])
          9    98   90   slope away from pivot_high
         10   102   90   background
         11   102   90   background
         12   (signal, appended per test)

      range = 120−80 = 40 → fib_1.13 = 125.2  fib_1.27 = 130.8

    LONG setup — pivot_high @ index 4, pivot_low @ index 8:
        idx  high  low   role
          0   102   92   background
          1   102   92   background
          2   102   92   background
          3   100   92   slope toward pivot_high
          4   120   90   PIVOT HIGH (max in window [2..6])
          5   100   87   slope away from pivot_high
          6    97   84   slope away
          7    99   82   slope toward pivot_low
          8    99   80   PIVOT LOW  (min in window [6..10])
          9    99   82   slope away from pivot_low
         10   102   88   background
         11   102   88   background
         12   (signal, appended per test)

      range = 120−80 = 40 → fib_1.13_l = 74.8  fib_1.27_l = 69.2
    """

    _SN = 2  # swing_n — keeps tests small (5-candle pivot window)
    _LB = 8  # lookback — ws = 12−8 = 4, so pivots at indices 4 and 8 are within ws ✓

    def _short_rows(self) -> list[dict[str, object]]:
        return [
            _candle(_BASE_TIME + 0, 100, 102, 92, 101),  # background
            _candle(_BASE_TIME + 1, 100, 102, 92, 101),  # background
            _candle(_BASE_TIME + 2, 100, 102, 92, 101),  # background
            _candle(_BASE_TIME + 3, 100, 102, 84, 100),  # slope → pivot_low
            _candle(_BASE_TIME + 4, 100, 102, 80, 90),  # PIVOT LOW  low=80
            _candle(_BASE_TIME + 5, 100, 102, 83, 95),  # slope ↑
            _candle(_BASE_TIME + 6, 100, 102, 87, 98),  # slope ↑
            _candle(_BASE_TIME + 7, 100, 98, 90, 97),  # slope → pivot_high
            _candle(_BASE_TIME + 8, 100, 120, 90, 110),  # PIVOT HIGH high=120
            _candle(_BASE_TIME + 9, 100, 98, 90, 97),  # slope ↓
            _candle(_BASE_TIME + 10, 100, 102, 90, 101),  # background
            _candle(_BASE_TIME + 11, 100, 102, 90, 101),  # background
        ]

    def _long_rows(self) -> list[dict[str, object]]:
        return [
            _candle(_BASE_TIME + 0, 100, 102, 92, 101),  # background
            _candle(_BASE_TIME + 1, 100, 102, 92, 101),  # background
            _candle(_BASE_TIME + 2, 100, 102, 92, 101),  # background
            _candle(_BASE_TIME + 3, 100, 100, 92, 99),  # slope → pivot_high
            _candle(_BASE_TIME + 4, 100, 120, 90, 110),  # PIVOT HIGH high=120
            _candle(_BASE_TIME + 5, 100, 100, 87, 95),  # slope ↓
            _candle(_BASE_TIME + 6, 100, 97, 84, 90),  # slope ↓
            _candle(_BASE_TIME + 7, 100, 99, 82, 88),  # slope → pivot_low
            _candle(_BASE_TIME + 8, 100, 99, 80, 87),  # PIVOT LOW  low=80
            _candle(_BASE_TIME + 9, 100, 99, 82, 88),  # slope ↑
            _candle(_BASE_TIME + 10, 100, 102, 88, 101),  # background
            _candle(_BASE_TIME + 11, 100, 102, 88, 101),  # background
        ]

    def test_short_signal_at_fib_113(self) -> None:
        # Wick reaches 126 > fib_1.13=125.2, close=124 < 125.2 → short at 1.13
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 126.0, 119, 124.0))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        short = result[result["direction"] == "short"]
        assert len(short) == 1
        assert "fib1.13" in str(short.iloc[0]["reason"])

    def test_short_signal_at_fib_127(self) -> None:
        # Wick reaches 132 > fib_1.27=130.8, close=129 < 130.8 → short at 1.27
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 132.0, 119, 129.0))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        short = result[result["direction"] == "short"]
        assert len(short) == 1
        assert "fib1.27" in str(short.iloc[0]["reason"])

    def test_no_short_when_close_above_fib_113(self) -> None:
        # Wick above 1.13 but close=125.5 > fib_1.13=125.2 → no rejection
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 126.0, 119, 125.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        assert result[result["direction"] == "short"].empty

    def test_short_fires_on_wick_when_close_rejection_disabled(self) -> None:
        # require_close_rejection=False: wick touch alone is sufficient.
        # The target signal candle (index 12) must fire; other earlier candles
        # may also fire (the same wick-touch rule applies to them).
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 126.0, 119, 125.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows),
            lookback=self._LB,
            swing_n=self._SN,
            require_close_rejection=False,
        )
        short = result[result["direction"] == "short"]
        sig12 = short[short["open_time"] == _BASE_TIME + 12]
        assert len(sig12) == 1  # signal candle fires
        assert "fib1.13" in str(sig12.iloc[0]["reason"])

    def test_short_sl_is_candle_wick_high(self) -> None:
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 127.5, 119, 124.0))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        short = result[result["direction"] == "short"]
        assert len(short) == 1
        assert float(short.iloc[0]["sl_price"]) == 127.5

    def test_long_signal_at_fib_113(self) -> None:
        # Wick dips to 74.0 < fib_1.13=74.8, close=75.5 > 74.8 → long at 1.13
        rows = self._long_rows()
        rows.append(_candle(_BASE_TIME + 12, 79, 83, 74.0, 75.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        long = result[result["direction"] == "long"]
        assert len(long) == 1
        assert "fib1.13" in str(long.iloc[0]["reason"])

    def test_long_sl_is_candle_wick_low(self) -> None:
        rows = self._long_rows()
        rows.append(_candle(_BASE_TIME + 12, 79, 83, 73.0, 75.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        long = result[result["direction"] == "long"]
        assert len(long) == 1
        assert float(long.iloc[0]["sl_price"]) == 73.0

    def test_no_signal_when_price_does_not_reach_fib(self) -> None:
        # High only reaches 122 < fib_1.13=125.2 → no signal
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 119, 122.0, 118, 120.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        assert result[result["direction"] == "short"].empty

    def test_reason_contains_swing_high_and_fib_level(self) -> None:
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 126.0, 119, 124.0))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        reason = str(result.iloc[0]["reason"])
        assert "sweep_high@120.00" in reason
        assert "fib1.13" in reason

    def test_context_contains_range_and_fib(self) -> None:
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 126.0, 119, 124.0))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows), lookback=self._LB, swing_n=self._SN
        )
        ctx = str(result.iloc[0]["context"])
        assert "range [" in ctx
        assert "fib1.13" in ctx

    # --- fib_require_range_close=True (strict: close must be back inside range) ---

    def test_fib_range_close_short_fires_when_close_inside_range(self) -> None:
        # Wick=126 reaches fib_1.13=125.2; close=119 < swing_high=120 → fires
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 126.0, 118, 119.0))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows),
            lookback=self._LB,
            swing_n=self._SN,
            fib_require_range_close=True,
        )
        short = result[result["direction"] == "short"]
        assert len(short) == 1
        assert "fib1.13" in str(short.iloc[0]["reason"])

    def test_fib_range_close_short_suppressed_when_close_above_swing_high(self) -> None:
        # Wick=126 reaches fib_1.13=125.2; close=120.5 > swing_high=120
        # Without range_close: would fire (120.5 < fib_1.13=125.2).
        # With range_close=True: no fire (close must be < 120).
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 121, 126.0, 119, 120.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows),
            lookback=self._LB,
            swing_n=self._SN,
            fib_require_range_close=True,
        )
        assert result[result["direction"] == "short"].empty

    def test_fib_range_close_long_fires_when_close_inside_range(self) -> None:
        # Wick=74.0 reaches fib_1.13_l=74.8; close=81 > swing_low=80 → fires
        rows = self._long_rows()
        rows.append(_candle(_BASE_TIME + 12, 79, 83, 74.0, 81.0))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows),
            lookback=self._LB,
            swing_n=self._SN,
            fib_require_range_close=True,
        )
        long = result[result["direction"] == "long"]
        assert len(long) == 1
        assert "fib1.13" in str(long.iloc[0]["reason"])

    def test_fib_range_close_long_suppressed_when_close_below_swing_low(self) -> None:
        # Wick=74.0 reaches fib_1.13_l=74.8; close=79.5 < swing_low=80
        # Without range_close: would fire (79.5 > fib_1.13_l=74.8).
        # With range_close=True: no fire (close must be > 80).
        rows = self._long_rows()
        rows.append(_candle(_BASE_TIME + 12, 78, 82, 74.0, 79.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows),
            lookback=self._LB,
            swing_n=self._SN,
            fib_require_range_close=True,
        )
        assert result[result["direction"] == "long"].empty

    # --- use_fib_extension=False (pivot-sweep mode) ---

    def test_pivot_sweep_short_fires_without_fib(self) -> None:
        # Wick reaches 121 (above pivot_high=120), close=119 < 120 — too small
        # for fib (fib_1.13=125.2) but fires in pivot-sweep mode
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 120.5, 121.0, 119, 119.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows),
            lookback=self._LB,
            swing_n=self._SN,
            use_fib_extension=False,
        )
        short = result[result["direction"] == "short"]
        sig12 = short[short["open_time"] == _BASE_TIME + 12]
        assert len(sig12) == 1
        assert "sweep_high@120.00" in str(sig12.iloc[0]["reason"])
        assert "fib" not in str(sig12.iloc[0]["reason"])

    def test_pivot_sweep_short_suppressed_in_fib_mode(self) -> None:
        # Same candle: wick=121 does NOT reach fib_1.13=125.2 → no signal in fib mode
        rows = self._short_rows()
        rows.append(_candle(_BASE_TIME + 12, 120.5, 121.0, 119, 119.5))
        result = detect_liquidity_sweep(
            _make_ohlcv(rows),
            lookback=self._LB,
            swing_n=self._SN,
            use_fib_extension=True,
        )
        assert result[result["open_time"] == _BASE_TIME + 12].empty


# ---------------------------------------------------------------------------
# Shared: empty DataFrame always returns correct columns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn",
    [
        lambda: detect_wick_fills(pd.DataFrame()),
        lambda: detect_marubozu_retest(pd.DataFrame()),
        lambda: detect_orb_breakout(pd.DataFrame()),
        lambda: detect_liquidity_sweep(pd.DataFrame()),
        lambda: detect_fvg(pd.DataFrame()),
        lambda: detect_market_structure(pd.DataFrame()),
        lambda: detect_funding_extreme(pd.DataFrame(), pd.DataFrame()),
        lambda: detect_eqh_eql(pd.DataFrame()),
        lambda: detect_order_block(pd.DataFrame()),
    ],
)
def test_empty_input_returns_signal_columns(fn: Callable[[], pd.DataFrame]) -> None:
    result = fn()
    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == SIGNAL_COLUMNS


# ---------------------------------------------------------------------------
# Order Block
# ---------------------------------------------------------------------------


class TestDetectOrderBlock:
    def test_returns_empty_on_too_few_candles(self) -> None:
        rows = [
            _candle(_BASE_TIME, 100, 110, 90, 105),
            _candle(_BASE_TIME + 1, 105, 112, 100, 108),
        ]
        result = detect_order_block(_make_ohlcv(rows))
        assert result.empty

    def test_signal_columns(self) -> None:
        result = detect_order_block(_make_ohlcv([]))
        assert list(result.columns) == SIGNAL_COLUMNS

    def test_detects_bearish_ob_short_signal(self) -> None:
        # Bearish OB setup:
        #   candle 0 (OB): bullish, open=100, close=110
        #   candle 1 (displacement): close=94 < ob_low(100) * (1 - 0.005=99.5) → bearish disp
        #   candle 2 (retest): high=108 >= ob_zone_bot(100), low=101 <= ob_zone_top(110),
        #                      close=103 < ob_zone_top(110) → short signal fires
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(
                _BASE_TIME + 1, 109, 110, 88, 93
            ),  # displacement: close 93 < 99*0.995=98.5
            _candle(
                _BASE_TIME + 2, 95, 108, 101, 103
            ),  # retest enters [100, 110], close<110
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) == 1
        assert short_signals.iloc[0]["sl_price"] == 112.0
        assert "ob_short" in short_signals.iloc[0]["reason"]

    def test_detects_bullish_ob_long_signal(self) -> None:
        # Bullish OB setup:
        #   candle 0 (OB): bearish, open=110, close=100
        #   candle 1 (displacement): close=117 > ob_high(112) * (1 + 0.005=112.56) → bullish disp
        #   candle 2 (retest): low=101 <= ob_zone_top(110), high=109 >= ob_zone_bot(100),
        #                      close=106 > ob_zone_bot(100) → long signal fires
        rows = [
            _candle(_BASE_TIME + 0, 110, 112, 99, 100),  # OB: bearish
            _candle(
                _BASE_TIME + 1, 101, 118, 100, 117
            ),  # displacement: close 117 > 112*1.005
            _candle(
                _BASE_TIME + 2, 115, 109, 101, 106
            ),  # retest enters [100, 110], close>100
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 1
        assert long_signals.iloc[0]["sl_price"] == 99.0
        assert "ob_long" in long_signals.iloc[0]["reason"]

    def test_no_signal_when_displacement_insufficient(self) -> None:
        # Displacement candle doesn't close far enough below ob_low
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(
                _BASE_TIME + 1, 109, 110, 98, 99
            ),  # close=99 ≥ 99*0.995=98.5 → no disp
            _candle(_BASE_TIME + 2, 95, 108, 95, 103),  # potential retest (won't fire)
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        assert result[result["direction"] == "short"].empty

    def test_no_signal_when_retest_close_breaks_zone(self) -> None:
        # Retest candle enters OB zone but closes above the zone top → not a valid retest
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),  # displacement
            _candle(
                _BASE_TIME + 2, 95, 115, 101, 112
            ),  # enters zone but close=112 >= zone_top(110)
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        assert result[result["direction"] == "short"].empty

    def test_one_signal_per_ob(self) -> None:
        # Multiple candles retest the OB — only first one fires
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),  # displacement
            _candle(_BASE_TIME + 2, 95, 108, 101, 103),  # first retest → signal
            _candle(
                _BASE_TIME + 3, 103, 109, 102, 104
            ),  # second entry into zone → no extra signal
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) == 1
        assert int(short_signals.iloc[0]["open_time"]) == _BASE_TIME + 2

    def test_full_history_scan_fires_early_ob(self) -> None:
        # OB at candle 0, retest at candle 2 — should fire even with large dataset.
        # Verifies start_idx=0 (all candles scanned for OB formation).
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),  # displacement
            _candle(_BASE_TIME + 2, 95, 108, 101, 103),  # retest within lookback
        ]
        # Pad with neutral candles so OB is far from the end
        for k in range(3, 60):
            rows.append(_candle(_BASE_TIME + k, 103, 104, 102, 103))
        df = _make_ohlcv(rows)
        # lookback=100 (default): OB at index 0, retest at index 2 → within retest window
        result = detect_order_block(df, lookback=100, displacement_pct=0.005)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1
        assert int(short_signals.iloc[0]["open_time"]) == _BASE_TIME + 2

    def test_lookback_retest_window_excludes_late_retest(self) -> None:
        # OB at candle 0, retest at candle 103 — beyond lookback=100 retest window.
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),  # displacement
        ]
        # Fill candles 2–102 that do NOT retest (price stays away from zone)
        for k in range(2, 103):
            rows.append(_candle(_BASE_TIME + k, 50, 55, 45, 50))
        # Candle 103: would be a valid retest but is beyond lookback=100
        rows.append(_candle(_BASE_TIME + 103, 95, 108, 101, 103))
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=100, displacement_pct=0.005)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty

    def test_context_contains_ob_type_and_time(self) -> None:
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),
            _candle(_BASE_TIME + 2, 95, 108, 101, 103),
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        assert not result.empty
        assert "Bearish OB" in result.iloc[0]["context"]


# ---------------------------------------------------------------------------
# Trend Day
# ---------------------------------------------------------------------------


class TestDetectTrendDay:
    """Tests for detect_trend_day()."""

    def test_returns_empty_on_empty_dataframe(self) -> None:
        result = detect_trend_day(pd.DataFrame())
        assert result.empty
        _assert_signal_columns(result)

    def test_detects_bullish_trend_day(self) -> None:
        # open=100, high=110, low=99, close=109
        # range=11, body=9, body_pct=9/11≈0.818, lower_wick=(100-99)/11≈0.091
        # With defaults body_pct_min=0.65, wick_max=0.15 → bullish signal
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 99, 109)])
        result = detect_trend_day(df)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["open_time"] == _BASE_TIME
        assert float(result.iloc[0]["sl_price"]) == 99.0

    def test_detects_bearish_trend_day(self) -> None:
        # open=109, high=110, low=99, close=100
        # range=11, body=9, body_pct=9/11≈0.818, upper_wick=(110-109)/11≈0.091
        # → bearish signal
        df = _make_ohlcv([_candle(_BASE_TIME, 109, 110, 99, 100)])
        result = detect_trend_day(df)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"
        assert result.iloc[0]["open_time"] == _BASE_TIME
        assert float(result.iloc[0]["sl_price"]) == 110.0

    def test_no_signal_for_doji(self) -> None:
        # open == close → body_pct=0 < 0.65
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 90, 100)])
        result = detect_trend_day(df)
        assert result.empty

    def test_no_signal_when_body_too_small(self) -> None:
        # range=20, body=5 → body_pct=0.25 < 0.65
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 90, 105)])
        result = detect_trend_day(df)
        assert result.empty

    def test_no_signal_when_leading_wick_too_large(self) -> None:
        # Bullish but large lower wick: open=105, high=110, low=90, close=109
        # range=20, body=4, body_pct=4/20=0.20 < 0.65 → filtered by body check already
        # Use a case that passes body but fails wick:
        # open=100, high=110, low=90, close=109
        # range=20, body=9, body_pct=9/20=0.45 < 0.65 → filtered by body
        # Design: open=100, high=108, low=90, close=107
        # range=18, body=7, body_pct=7/18≈0.389 < 0.65 → still filtered
        # Design explicit: body_pct=0.70 but lower_wick=0.20 > wick_max=0.15
        # range=10, body=7, lower_wick=2, upper_wick=1
        # open=102, high=109, low=100, close=109 → upper_wick=0, body=7, lower_wick=2
        # body_pct=7/9≈0.778, lower_wick=2/9≈0.222 > 0.15 → no bullish signal
        df = _make_ohlcv([_candle(_BASE_TIME, 102, 109, 100, 109)])
        result = detect_trend_day(df, body_pct_min=0.65, wick_max=0.15)
        assert result.empty

    def test_no_signal_for_zero_range_candle(self) -> None:
        # high == low — skip to avoid division by zero
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 100, 100, 100)])
        result = detect_trend_day(df)
        assert result.empty

    def test_multiple_candles_detects_both_directions(self) -> None:
        rows = [
            _candle(_BASE_TIME + 0, 100, 110, 99, 109),  # bullish trend day
            _candle(_BASE_TIME + 1, 100, 110, 90, 105),  # doji-ish, no signal
            _candle(_BASE_TIME + 2, 109, 110, 99, 100),  # bearish trend day
        ]
        df = _make_ohlcv(rows)
        result = detect_trend_day(df)
        assert len(result) == 2
        directions = set(result["direction"].tolist())
        assert directions == {"long", "short"}

    def test_signal_columns_correct(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 99, 109)])
        result = detect_trend_day(df)
        _assert_signal_columns(result)

    def test_custom_params_stricter_body(self) -> None:
        # body_pct≈0.818 passes default 0.65 but not strict 0.9
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 99, 109)])
        result = detect_trend_day(df, body_pct_min=0.9)
        assert result.empty

    def test_reason_string_format_bullish(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100.0, 110.0, 99.0, 109.0)])
        result = detect_trend_day(df)
        assert result.iloc[0]["reason"] == "trend_day_bull@100.00-109.00"

    def test_reason_string_format_bearish(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 109.0, 110.0, 99.0, 100.0)])
        result = detect_trend_day(df)
        assert result.iloc[0]["reason"] == "trend_day_bear@109.00-100.00"


# ---------------------------------------------------------------------------
# T7: cvd_divergence removal — yfinance OHLCV has no taker_buy_volume so the
# detector can never fire on equities. These assertions lock the strip in.
# ---------------------------------------------------------------------------


def test_cvd_divergence_not_in_strategy_registry() -> None:
    from analytics.strategies import STRATEGY_REGISTRY

    assert "cvd_divergence" not in STRATEGY_REGISTRY


def test_cvd_divergence_not_in_detector_registry() -> None:
    from analytics.strategies import DETECTOR_REGISTRY

    assert "cvd_divergence" not in DETECTOR_REGISTRY


def test_cvd_divergence_not_in_signal_registry() -> None:
    from signals.registry import SIGNAL_REGISTRY

    assert "cvd_divergence" not in SIGNAL_REGISTRY
