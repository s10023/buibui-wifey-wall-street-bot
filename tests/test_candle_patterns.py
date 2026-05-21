"""Tests for candle pattern detectors and fibonacci retracement (R3 + R5)."""

import pandas as pd

from analytics.strategies import (
    SIGNAL_COLUMNS,
    STRATEGY_REGISTRY,
    detect_doji,
    detect_engulfing,
    detect_fibonacci_retracement,
    detect_hammer_hanging_man,
    detect_inside_bar,
    detect_morning_evening_star,
    detect_pin_bar,
)
from tests.conftest import _candle, _make_ohlcv

_BASE_TIME = 1_700_000_000_000
_STEP = 1_000


def _t(n: int) -> int:
    return _BASE_TIME + n * _STEP


def _assert_signal_columns(df: pd.DataFrame) -> None:
    assert list(df.columns) == SIGNAL_COLUMNS


# ---------------------------------------------------------------------------
# Engulfing
# ---------------------------------------------------------------------------


class TestDetectEngulfing:
    def test_empty_on_single_candle(self) -> None:
        df = _make_ohlcv([_candle(_t(0), 100, 110, 90, 95)])
        result = detect_engulfing(df)
        assert result.empty
        _assert_signal_columns(result)

    def test_bullish_engulfing(self) -> None:
        # Prior bearish: open=105, close=95 (body 95–105)
        # Current bullish: open=93, close=108 → engulfs [95, 105]
        rows = [
            _candle(_t(0), 105, 110, 90, 95),
            _candle(_t(1), 93, 115, 88, 108),
        ]
        df = _make_ohlcv(rows)
        result = detect_engulfing(df)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["direction"] == "long"
        assert row["reason"].startswith("bullish_engulfing@")
        assert row["open_time"] == _t(1)
        # sl_price < entry
        assert float(row["sl_price"]) < 108.0

    def test_bearish_engulfing(self) -> None:
        # Prior bullish: open=95, close=105
        # Current bearish: open=108, close=92 → engulfs [95, 105]
        rows = [
            _candle(_t(0), 95, 110, 90, 105),
            _candle(_t(1), 108, 115, 88, 92),
        ]
        df = _make_ohlcv(rows)
        result = detect_engulfing(df)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["direction"] == "short"
        assert row["reason"].startswith("bearish_engulfing@")
        # sl_price > entry
        assert float(row["sl_price"]) > 92.0

    def test_no_signal_when_not_engulfing(self) -> None:
        # Two bullish candles — no engulfing pattern
        rows = [
            _candle(_t(0), 100, 110, 95, 108),
            _candle(_t(1), 109, 115, 107, 113),
        ]
        df = _make_ohlcv(rows)
        result = detect_engulfing(df)
        assert result.empty

    def test_sl_tp_calculation(self) -> None:
        # Prior bearish: open=105, close=95 (body 95–105)
        # Current bullish: open=93, close=108 → engulfs [95, 105]; entry=108
        rows = [
            _candle(_t(0), 105, 110, 90, 95),
            _candle(_t(1), 93, 115, 88, 108.0),  # entry = 108
        ]
        df = _make_ohlcv(rows)
        result = detect_engulfing(df, sl_pct=0.02, tp_r=2.0)
        assert not result.empty
        row = result.iloc[0]
        # sl should be ~105.84 (108 * 0.98)
        assert abs(float(row["sl_price"]) - 108.0 * 0.98) < 0.1


# ---------------------------------------------------------------------------
# Pin Bar
# ---------------------------------------------------------------------------


class TestDetectPinBar:
    def test_empty_on_empty_df(self) -> None:
        df = _make_ohlcv([])
        result = detect_pin_bar(df)
        assert result.empty
        _assert_signal_columns(result)

    def test_bullish_pin_bar(self) -> None:
        # Body at top: open=99, close=100, high=101, low=88 (lower wick=11, body=1)
        rows = [_candle(_t(0), 99, 101, 88, 100)]
        df = _make_ohlcv(rows)
        result = detect_pin_bar(df, wick_ratio=2.0)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["reason"].startswith("pin_bar_bull@")

    def test_bearish_pin_bar(self) -> None:
        # Body at bottom: open=101, close=100, high=113, low=99 (upper wick=12, body=1)
        rows = [_candle(_t(0), 101, 113, 99, 100)]
        df = _make_ohlcv(rows)
        result = detect_pin_bar(df, wick_ratio=2.0)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"
        assert result.iloc[0]["reason"].startswith("pin_bar_bear@")

    def test_no_signal_on_big_body(self) -> None:
        # Large body — not a pin bar
        rows = [_candle(_t(0), 90, 110, 85, 108)]
        df = _make_ohlcv(rows)
        result = detect_pin_bar(df, wick_ratio=2.0)
        assert result.empty

    def test_sl_below_entry_for_long(self) -> None:
        rows = [_candle(_t(0), 99, 101, 88, 100)]
        df = _make_ohlcv(rows)
        result = detect_pin_bar(df, sl_pct=0.02)
        assert float(result.iloc[0]["sl_price"]) < 100.0

    def test_sl_above_entry_for_short(self) -> None:
        rows = [_candle(_t(0), 101, 113, 99, 100)]
        df = _make_ohlcv(rows)
        result = detect_pin_bar(df, sl_pct=0.02)
        assert float(result.iloc[0]["sl_price"]) > 100.0


# ---------------------------------------------------------------------------
# Inside Bar
# ---------------------------------------------------------------------------


class TestDetectInsideBar:
    def test_empty_on_fewer_than_3_candles(self) -> None:
        rows = [
            _candle(_t(0), 100, 110, 90, 105),
            _candle(_t(1), 102, 108, 92, 104),
        ]
        df = _make_ohlcv(rows)
        result = detect_inside_bar(df)
        assert result.empty
        _assert_signal_columns(result)

    def test_inside_bar_long_breakout(self) -> None:
        # Mother: open=100, close=110 (body 100–110)
        # Inside: open=103, close=107 (body 103–107 — inside mother)
        # Breakout: close=115 — above mother top (110)
        rows = [
            _candle(_t(0), 100, 115, 95, 110),
            _candle(_t(1), 103, 109, 101, 107),
            _candle(_t(2), 109, 120, 108, 115),
        ]
        df = _make_ohlcv(rows)
        result = detect_inside_bar(df)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["direction"] == "long"
        assert row["reason"].startswith("inside_bar_long@")
        assert row["open_time"] == _t(2)

    def test_inside_bar_short_breakout(self) -> None:
        # Mother: open=110, close=100 (body 100–110)
        # Inside: open=107, close=103 (inside mother)
        # Breakout: close=95 — below mother bottom (100)
        rows = [
            _candle(_t(0), 110, 115, 95, 100),
            _candle(_t(1), 107, 109, 101, 103),
            _candle(_t(2), 101, 102, 90, 95),
        ]
        df = _make_ohlcv(rows)
        result = detect_inside_bar(df)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["direction"] == "short"
        assert row["reason"].startswith("inside_bar_short@")

    def test_no_signal_when_breakout_stays_inside(self) -> None:
        # Breakout candle closes within mother range
        rows = [
            _candle(_t(0), 100, 115, 95, 110),
            _candle(_t(1), 103, 109, 101, 107),
            _candle(_t(2), 106, 112, 103, 109),  # still inside
        ]
        df = _make_ohlcv(rows)
        result = detect_inside_bar(df)
        assert result.empty


# ---------------------------------------------------------------------------
# Hammer / Hanging Man
# ---------------------------------------------------------------------------


class TestDetectHammerHangingMan:
    def test_empty_on_insufficient_data(self) -> None:
        rows = [_candle(_t(0), 100, 110, 90, 95)]
        df = _make_ohlcv(rows)
        result = detect_hammer_hanging_man(df, context_lookback=5)
        assert result.empty
        _assert_signal_columns(result)

    def test_hammer_after_downtrend(self) -> None:
        # Build a downtrend over 10 bars then a hammer candle
        rows = []
        for i in range(10):
            price = 200 - i * 5  # falling from 200 to 155
            rows.append(_candle(_t(i), price, price + 2, price - 2, price - 1))
        # Hammer at bar 10: close=148, prior_close (bar 0) = 199 → downtrend confirmed
        rows.append(_candle(_t(10), 149, 150, 135, 148))  # big lower wick, small body
        df = _make_ohlcv(rows)
        result = detect_hammer_hanging_man(df, wick_ratio=2.0, context_lookback=10)
        # Should fire hammer (long) since close[10]=148 < close[0]=199
        assert len(result) >= 1
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1
        assert long_signals.iloc[-1]["reason"].startswith("hammer@")

    def test_hanging_man_after_uptrend(self) -> None:
        # Build an uptrend over 10 bars then a hanging man candle
        rows = []
        for i in range(10):
            price = 100 + i * 5
            rows.append(_candle(_t(i), price, price + 2, price - 2, price + 1))
        # Hanging man at bar 10: prior_close (bar 0) = 101 → uptrend confirmed (148 > 101)
        rows.append(_candle(_t(10), 149, 150, 135, 148))  # hammer shape
        df = _make_ohlcv(rows)
        result = detect_hammer_hanging_man(df, wick_ratio=2.0, context_lookback=10)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1
        assert short_signals.iloc[-1]["reason"].startswith("hanging_man@")


# ---------------------------------------------------------------------------
# Doji
# ---------------------------------------------------------------------------


class TestDetectDoji:
    def test_empty_on_single_candle(self) -> None:
        df = _make_ohlcv([_candle(_t(0), 100, 110, 90, 100)])
        result = detect_doji(df)
        assert result.empty
        _assert_signal_columns(result)

    def test_doji_bull_confirmation(self) -> None:
        # Doji: open=100, close=100.5, high=110, low=90 — body=0.5, range=20 (2.5%)
        # Confirmation: bullish, body=8, range=9 (89%)
        rows = [
            _candle(_t(0), 100, 110, 90, 100.5),  # doji
            _candle(_t(1), 100, 109, 100, 108),  # strong bullish confirmation
        ]
        df = _make_ohlcv(rows)
        result = detect_doji(df, body_threshold=0.1, confirm_body_pct=0.6)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["direction"] == "long"
        assert row["reason"].startswith("doji_bull@")
        assert row["open_time"] == _t(1)

    def test_doji_bear_confirmation(self) -> None:
        rows = [
            _candle(_t(0), 100, 110, 90, 100.5),  # doji
            _candle(_t(1), 108, 109, 100, 100.5),  # strong bearish confirmation
        ]
        df = _make_ohlcv(rows)
        result = detect_doji(df, body_threshold=0.1, confirm_body_pct=0.6)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"
        assert result.iloc[0]["reason"].startswith("doji_bear@")

    def test_no_signal_when_confirmation_weak(self) -> None:
        rows = [
            _candle(_t(0), 100, 110, 90, 100.5),  # doji
            _candle(
                _t(1), 100, 108, 99, 104
            ),  # weak confirmation (body=4, range=9 ≈ 44%)
        ]
        df = _make_ohlcv(rows)
        result = detect_doji(df, body_threshold=0.1, confirm_body_pct=0.6)
        assert result.empty

    def test_no_signal_when_no_doji(self) -> None:
        rows = [
            _candle(_t(0), 100, 110, 90, 108),  # large body — not a doji
            _candle(_t(1), 108, 115, 107, 114),
        ]
        df = _make_ohlcv(rows)
        result = detect_doji(df)
        assert result.empty


# ---------------------------------------------------------------------------
# Morning Star / Evening Star
# ---------------------------------------------------------------------------


class TestDetectMorningEveningStar:
    def test_empty_on_fewer_than_3_candles(self) -> None:
        rows = [
            _candle(_t(0), 100, 110, 90, 95),
            _candle(_t(1), 94, 96, 92, 93),
        ]
        df = _make_ohlcv(rows)
        result = detect_morning_evening_star(df)
        assert result.empty
        _assert_signal_columns(result)

    def test_morning_star(self) -> None:
        # A: large bearish (open=120, close=100 → body=20)
        # Star: small body (open=99, close=98, range=5 → body=1, 20%)
        # B: large bullish (open=99, close=115) closing above midpoint of A (110)
        rows = [
            _candle(_t(0), 120, 122, 98, 100),  # large bearish
            _candle(_t(1), 99, 101, 96, 98),  # small star
            _candle(_t(2), 99, 118, 98, 115),  # large bullish
        ]
        df = _make_ohlcv(rows)
        result = detect_morning_evening_star(df, star_body_max=0.3)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["direction"] == "long"
        assert row["reason"].startswith("morning_star@")
        assert row["open_time"] == _t(2)

    def test_evening_star(self) -> None:
        # A: large bullish (open=100, close=120)
        # Star: small body
        # B: large bearish (open=121, close=105) closing below midpoint of A (110)
        rows = [
            _candle(_t(0), 100, 122, 98, 120),  # large bullish
            _candle(_t(1), 121, 123, 119, 122),  # small star
            _candle(_t(2), 121, 122, 102, 105),  # large bearish
        ]
        df = _make_ohlcv(rows)
        result = detect_morning_evening_star(df, star_body_max=0.3)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["direction"] == "short"
        assert row["reason"].startswith("evening_star@")

    def test_no_signal_when_star_body_too_large(self) -> None:
        rows = [
            _candle(_t(0), 120, 122, 98, 100),
            _candle(_t(1), 99, 108, 96, 106),  # large star body
            _candle(_t(2), 99, 118, 98, 115),
        ]
        df = _make_ohlcv(rows)
        result = detect_morning_evening_star(df, star_body_max=0.1)
        assert result.empty

    def test_no_signal_when_b_does_not_close_past_midpoint(self) -> None:
        # A: open=120, close=100, midpoint=110
        # B: close=109 — does not exceed midpoint 110
        rows = [
            _candle(_t(0), 120, 122, 98, 100),
            _candle(_t(1), 99, 101, 96, 98),
            _candle(_t(2), 99, 112, 98, 109),  # closes below midpoint of A (110)
        ]
        df = _make_ohlcv(rows)
        result = detect_morning_evening_star(df, star_body_max=0.3)
        assert result.empty


# ---------------------------------------------------------------------------
# Fibonacci Retracement
# ---------------------------------------------------------------------------


class TestDetectFibonacciRetracement:
    def _make_long_swing_df(self, entry_close: float) -> pd.DataFrame:
        """10-row DF with a pivot low at bar 2 (low=100) and pivot high at bar 6 (high=200).

        Bars arranged so the 3-bar pivot conditions are met:
          pivot low at k=2: lows[2]=100 < lows[1] and lows[2] < lows[3]
          pivot high at k=6: highs[6]=200 > highs[5] and highs[6] > highs[7]

        With swing_lookback=9, sig_i=9, win=[0..8], scan=[1..7].
        """
        #           open   high   low   close
        data = [
            (150, 152, 148, 151),  # 0  — pad
            (120, 122, 115, 119),  # 1  — above pivot low (low=115)
            (102, 105, 100, 103),  # 2  — PIVOT LOW (low=100)
            (110, 115, 108, 113),  # 3  — above pivot low (low=108)
            (150, 155, 148, 153),  # 4  — pad
            (185, 190, 182, 188),  # 5  — below pivot high (high=190)
            (195, 200, 192, 198),  # 6  — PIVOT HIGH (high=200)
            (175, 180, 172, 177),  # 7  — below pivot high (high=180)
            (165, 168, 162, 166),  # 8  — pad (this is win_end so NOT scanned as pivot)
            (
                entry_close - 2,
                entry_close + 1,
                entry_close - 3,
                entry_close,
            ),  # 9 — signal
        ]
        rows = [_candle(_t(i), o, h, lo, c) for i, (o, h, lo, c) in enumerate(data)]
        return _make_ohlcv(rows)

    def _make_short_swing_df(self, entry_close: float) -> pd.DataFrame:
        """10-row DF with pivot high at bar 2 (high=200) then pivot low at bar 6 (low=100)."""
        #           open   high   low   close
        data = [
            (150, 152, 148, 151),  # 0
            (195, 198, 192, 196),  # 1  — below pivot high
            (198, 200, 194, 199),  # 2  — PIVOT HIGH (high=200)
            (185, 188, 182, 186),  # 3  — below pivot high
            (150, 155, 148, 153),  # 4
            (108, 112, 105, 110),  # 5  — above pivot low
            (103, 107, 100, 104),  # 6  — PIVOT LOW (low=100)
            (115, 120, 112, 118),  # 7  — above pivot low
            (130, 135, 128, 133),  # 8
            (
                entry_close - 2,
                entry_close + 1,
                entry_close - 3,
                entry_close,
            ),  # 9 — signal
        ]
        rows = [_candle(_t(i), o, h, lo, c) for i, (o, h, lo, c) in enumerate(data)]
        return _make_ohlcv(rows)

    def test_long_signal_in_golden_zone(self) -> None:
        # Swing: low=100, high=200, range=100.
        # Golden zone (from swing_high down): fib_0.5=150, fib_0.618=138.2.
        # 55% retracement = 200 - 55 = 145 → inside [138.2, 150].
        # With swing_lookback=8: n=10, sig_i starts at 9, win_start=1, win_end=8, scan [2..7]
        # Pivot low at bar 2 (low=100), pivot high at bar 6 (high=200) — both in scan range.
        entry_close = 145.0
        df = self._make_long_swing_df(entry_close)
        result = detect_fibonacci_retracement(df, swing_lookback=8)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1
        last = long_signals.iloc[-1]
        assert last["reason"].startswith("fib_golden_zone@")
        assert "0.618=" in last["reason"]
        # SL = fib_0.786 from swing_high down = 200 - 0.786*100 = 121.4
        assert abs(float(last["sl_price"]) - (200.0 - 0.786 * 100.0)) < 1.0

    def test_short_signal_in_golden_zone(self) -> None:
        # Swing: high=200, low=100. Short golden zone (bounce from low): 50%–61.8% of range from low.
        # fib_0_5_short = 100 + 0.5*100 = 150, fib_0_618_short = 100 + 0.618*100 = 161.8.
        # Entry at 155 → inside [150, 161.8].
        entry_close = 155.0
        df = self._make_short_swing_df(entry_close)
        result = detect_fibonacci_retracement(df, swing_lookback=8)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1
        assert short_signals.iloc[-1]["reason"].startswith("fib_golden_zone@")

    def test_empty_on_insufficient_data(self) -> None:
        rows = [_candle(_t(i), 100 + i, 110 + i, 90 + i, 105 + i) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_fibonacci_retracement(df, swing_lookback=20)
        assert result.empty
        _assert_signal_columns(result)

    def test_no_signal_outside_golden_zone(self) -> None:
        # Entry at 20% retracement from swing_high = 200 - 20 = 180 → above golden zone top (150).
        entry_close = 180.0
        df = self._make_long_swing_df(entry_close)
        result = detect_fibonacci_retracement(df, swing_lookback=8)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 0


# ---------------------------------------------------------------------------
# STRATEGY_REGISTRY registration checks
# ---------------------------------------------------------------------------


class TestStrategyRegistration:
    def test_all_new_strategies_in_registry(self) -> None:
        new_strategies = [
            "engulfing",
            "pin_bar",
            "inside_bar",
            "hammer_hanging_man",
            "doji",
            "morning_evening_star",
            # "fibonacci_retracement" — legacy, not in STRATEGY_REGISTRY
        ]
        for name in new_strategies:
            assert name in STRATEGY_REGISTRY, f"{name!r} missing from STRATEGY_REGISTRY"

    def test_confidence_values_in_range(self) -> None:
        new_strategies = [
            "engulfing",
            "pin_bar",
            "inside_bar",
            "hammer_hanging_man",
            "doji",
            "morning_evening_star",
            # "fibonacci_retracement" — legacy, not in STRATEGY_REGISTRY
        ]
        for name in new_strategies:
            spec = STRATEGY_REGISTRY[name]
            for tf in ("15m", "1h", "4h", "1d"):
                c = spec.get_confidence(tf)
                assert 1 <= c <= 5, f"{name}/{tf} confidence {c} out of range"

    def test_new_strategies_not_require_funding_or_secondary(self) -> None:
        new_strategies = [
            "engulfing",
            "pin_bar",
            "inside_bar",
            "hammer_hanging_man",
            "doji",
            "morning_evening_star",
            # "fibonacci_retracement" — legacy, not in STRATEGY_REGISTRY
        ]
        for name in new_strategies:
            spec = STRATEGY_REGISTRY[name]
            assert not spec.requires_funding, f"{name} should not require funding"

    def test_signal_registry_contains_new_strategies(self) -> None:
        from signals.registry import SIGNAL_REGISTRY

        new_strategies = [
            "engulfing",
            "pin_bar",
            "inside_bar",
            "hammer_hanging_man",
            "doji",
            "morning_evening_star",
            # "fibonacci_retracement" — legacy, not in STRATEGY_REGISTRY
        ]
        for name in new_strategies:
            assert name in SIGNAL_REGISTRY, f"{name!r} missing from SIGNAL_REGISTRY"
