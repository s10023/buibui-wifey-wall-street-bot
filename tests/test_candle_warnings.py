"""Tests for candle-level warning helpers and consolidated warnings block."""

from datetime import UTC, datetime

import pandas as pd

from signals.alert_formatter import (
    SignalEvent,
    _build_candle_warnings,
    _has_consecutive_candles,
    _has_equal_levels,
    _is_doji,
    _is_inside_bar,
    _is_marubozu,
    _wick_rejection_against,
    format_signal_alert,
)

_TS_MS = int(datetime(2024, 1, 15, 11, 0, tzinfo=UTC).timestamp() * 1000)


def _event(
    direction: str = "long", low_volume: bool = False, volume_spike: bool = False
) -> SignalEvent:
    return SignalEvent(
        symbol="BTCUSDT",
        timeframe="1h",
        strategy="fvg",
        direction=direction,
        reason="fvg_long@43000.00",
        open_time=_TS_MS,
        price=43000.0,
        sl_price=42000.0,
        low_volume=low_volume,
        volume_spike=volume_spike,
    )


def _ohlcv(*rows: tuple[float, float, float, float]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from (open, high, low, close) tuples."""
    data = [{"open": o, "high": h, "low": lo, "close": c} for o, h, lo, c in rows]
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# W1: _is_marubozu
# ---------------------------------------------------------------------------


class TestIsMarubozu:
    def test_clean_marubozu_bullish(self) -> None:
        # open=100, high=110, low=100, close=110 → no wicks
        assert _is_marubozu(100.0, 110.0, 100.0, 110.0)

    def test_small_wicks_pass(self) -> None:
        # body=10, wicks=0.5 each (5%) → within 10% threshold
        assert _is_marubozu(100.0, 110.5, 99.5, 110.0)

    def test_large_upper_wick_fails(self) -> None:
        # body=10, upper wick=2 (20%) → fails
        assert not _is_marubozu(100.0, 112.0, 100.0, 110.0)

    def test_large_lower_wick_fails(self) -> None:
        # body=10, lower wick=2 (20%) → fails
        assert not _is_marubozu(100.0, 110.0, 98.0, 110.0)

    def test_zero_body_returns_false(self) -> None:
        # doji-like: open==close
        assert not _is_marubozu(100.0, 105.0, 95.0, 100.0)


# ---------------------------------------------------------------------------
# W7: _is_doji
# ---------------------------------------------------------------------------


class TestIsDoji:
    def test_perfect_doji(self) -> None:
        # open==close, range=10
        assert _is_doji(100.0, 105.0, 95.0, 100.0)

    def test_small_body_is_doji(self) -> None:
        # body=0.9, range=10 → 9% < 10% threshold
        assert _is_doji(100.0, 105.0, 95.0, 100.9)

    def test_normal_candle_not_doji(self) -> None:
        # body=5, range=10 → 50%
        assert not _is_doji(100.0, 105.0, 95.0, 105.0)

    def test_zero_range_returns_false(self) -> None:
        assert not _is_doji(100.0, 100.0, 100.0, 100.0)


# ---------------------------------------------------------------------------
# W8: _is_inside_bar
# ---------------------------------------------------------------------------


class TestIsInsideBar:
    def test_inside_bar(self) -> None:
        # signal candle (h=104, l=101) inside prior (h=105, l=100)
        assert _is_inside_bar(104.0, 101.0, 105.0, 100.0)

    def test_exact_match_is_inside(self) -> None:
        assert _is_inside_bar(105.0, 100.0, 105.0, 100.0)

    def test_high_outside_prior(self) -> None:
        assert not _is_inside_bar(106.0, 101.0, 105.0, 100.0)

    def test_low_outside_prior(self) -> None:
        assert not _is_inside_bar(104.0, 99.0, 105.0, 100.0)


# ---------------------------------------------------------------------------
# W5: _wick_rejection_against
# ---------------------------------------------------------------------------


class TestWickRejectionAgainst:
    def test_long_upper_wick_warns(self) -> None:
        # range=10, upper wick=5 (50%) → warns on LONG
        assert _wick_rejection_against(100.0, 110.0, 100.0, 105.0, "long")

    def test_long_upper_wick_small_no_warn(self) -> None:
        # range=10, upper wick=2 (20%) → no warn
        assert not _wick_rejection_against(100.0, 107.0, 100.0, 105.0, "long")

    def test_short_lower_wick_warns(self) -> None:
        # range=10, lower wick=5 (50%) → warns on SHORT
        assert _wick_rejection_against(105.0, 105.0, 95.0, 100.0, "short")

    def test_short_lower_wick_small_no_warn(self) -> None:
        # range=10, lower wick=2 (20%) → no warn
        assert not _wick_rejection_against(105.0, 105.0, 98.0, 100.0, "short")

    def test_zero_range_returns_false(self) -> None:
        assert not _wick_rejection_against(100.0, 100.0, 100.0, 100.0, "long")


# ---------------------------------------------------------------------------
# W2: _has_equal_levels
# ---------------------------------------------------------------------------


class TestHasEqualLevels:
    def _df_with_lows(self, lows: list[float], price: float) -> pd.DataFrame:
        rows = []
        for low in lows:
            rows.append({"open": price, "high": price + 1, "low": low, "close": price})
        # last row = signal candle (excluded from check)
        rows.append(
            {"open": price, "high": price + 1, "low": price - 0.5, "close": price}
        )
        return pd.DataFrame(rows)

    def _df_with_highs(self, highs: list[float], price: float) -> pd.DataFrame:
        rows = []
        for high in highs:
            # low == price so lows are NOT below price → won't trigger LONG equal-lows check
            rows.append({"open": price, "high": high, "low": price, "close": price})
        rows.append({"open": price, "high": price + 0.5, "low": price, "close": price})
        return pd.DataFrame(rows)

    def test_equal_lows_warns_on_long(self) -> None:
        # Two lows at ~42800, price=43000 → equal lows below
        df = self._df_with_lows([42800.0, 42802.0, 42900.0], price=43000.0)
        assert _has_equal_levels(df, 43000.0, "long")

    def test_no_equal_lows_no_warn(self) -> None:
        # Three distinct lows, none within 0.15% of each other
        df = self._df_with_lows([42000.0, 42500.0, 42800.0], price=43000.0)
        assert not _has_equal_levels(df, 43000.0, "long")

    def test_equal_highs_warns_on_short(self) -> None:
        # Two highs at ~43200, price=43000 → equal highs above
        df = self._df_with_highs([43200.0, 43202.0, 43100.0], price=43000.0)
        assert _has_equal_levels(df, 43000.0, "short")

    def test_no_equal_highs_no_warn(self) -> None:
        df = self._df_with_highs([43100.0, 43500.0, 44000.0], price=43000.0)
        assert not _has_equal_levels(df, 43000.0, "short")

    def test_equal_highs_ignored_for_long(self) -> None:
        # equal highs exist but direction is long → no warn (wrong direction)
        df = self._df_with_highs([43200.0, 43202.0], price=43000.0)
        assert not _has_equal_levels(df, 43000.0, "long")

    def test_levels_above_price_ignored_for_lows(self) -> None:
        # equal levels only above price — long checks lows below price → no warn
        df = self._df_with_lows([43100.0, 43102.0], price=43000.0)
        assert not _has_equal_levels(df, 43000.0, "long")

    def test_not_enough_history(self) -> None:
        df = pd.DataFrame(
            [
                {"open": 43000, "high": 43100, "low": 42800, "close": 43000},
            ]
        )
        assert not _has_equal_levels(df, 43000.0, "long")


# ---------------------------------------------------------------------------
# W6: _has_consecutive_candles
# ---------------------------------------------------------------------------


class TestHasConsecutiveCandles:
    def test_three_bullish_warns_long(self) -> None:
        df = _ohlcv(
            (100, 105, 99, 104),
            (104, 108, 103, 107),
            (107, 112, 106, 111),
        )
        assert _has_consecutive_candles(df, "long", n=3)

    def test_three_bearish_warns_short(self) -> None:
        df = _ohlcv(
            (111, 112, 106, 107),
            (107, 108, 103, 104),
            (104, 105, 99, 100),
        )
        assert _has_consecutive_candles(df, "short", n=3)

    def test_mixed_candles_no_warn(self) -> None:
        df = _ohlcv(
            (100, 105, 99, 104),
            (104, 105, 99, 100),  # bearish
            (100, 106, 99, 105),
        )
        assert not _has_consecutive_candles(df, "long", n=3)

    def test_not_enough_candles(self) -> None:
        df = _ohlcv(
            (100, 105, 99, 104),
            (104, 108, 103, 107),
        )
        assert not _has_consecutive_candles(df, "long", n=3)

    def test_bearish_run_does_not_warn_long(self) -> None:
        df = _ohlcv(
            (111, 112, 106, 107),
            (107, 108, 103, 104),
            (104, 105, 99, 100),
        )
        assert not _has_consecutive_candles(df, "long", n=3)


# ---------------------------------------------------------------------------
# _build_candle_warnings integration
# ---------------------------------------------------------------------------


class TestBuildCandleWarnings:
    def test_volume_spike_note(self) -> None:
        ev = _event(volume_spike=True)
        notes = _build_candle_warnings([ev], None)
        assert any("Volume spike" in n for n in notes)

    def test_low_volume_note(self) -> None:
        ev = _event(low_volume=True)
        notes = _build_candle_warnings([ev], None)
        assert any("Low volume" in n for n in notes)

    def test_no_volume_note_when_normal(self) -> None:
        ev = _event()
        notes = _build_candle_warnings([ev], None)
        assert not any("volume" in n.lower() for n in notes)

    def test_no_ohlcv_returns_only_volume(self) -> None:
        ev = _event(low_volume=True)
        notes = _build_candle_warnings([ev], None)
        assert len(notes) == 1

    def test_marubozu_warning_appears(self) -> None:
        # signal candle: no wicks
        df = _ohlcv(
            (100, 105, 100, 105),  # prior
            (105, 115, 105, 115),  # signal: pure body
        )
        notes = _build_candle_warnings([_event()], df)
        assert any("Wickless" in n for n in notes)

    def test_doji_takes_priority_over_marubozu(self) -> None:
        # Doji: open≈close, small body relative to range
        df = _ohlcv(
            (100, 105, 100, 105),
            (100, 110, 90, 100),  # doji: open=100, close=100, range=20
        )
        notes = _build_candle_warnings([_event()], df)
        assert any("Doji" in n for n in notes)
        assert not any("Wickless" in n for n in notes)

    def test_inside_bar_warning(self) -> None:
        df = _ohlcv(
            (100, 110, 90, 105),  # prior: range 90–110
            (102, 108, 92, 104),  # signal: inside prior
        )
        notes = _build_candle_warnings([_event()], df)
        assert any("prior range" in n for n in notes)

    def test_wick_rejection_long(self) -> None:
        # Long signal but big upper wick
        df = _ohlcv(
            (100, 105, 99, 104),
            (100, 110, 100, 102),  # upper wick = 8, range = 10 → 80%
        )
        notes = _build_candle_warnings([_event(direction="long")], df)
        assert any("Upper wick" in n for n in notes)

    def test_wick_rejection_short(self) -> None:
        # Short signal but big lower wick
        df = _ohlcv(
            (105, 106, 100, 102),
            (105, 105, 95, 103),  # lower wick = 8, range = 10 → 80%
        )
        notes = _build_candle_warnings([_event(direction="short")], df)
        assert any("Lower wick" in n for n in notes)

    def test_consecutive_overextension_long(self) -> None:
        df = _ohlcv(
            (100, 105, 99, 104),
            (104, 108, 103, 107),
            (107, 112, 106, 111),
        )
        notes = _build_candle_warnings([_event(direction="long")], df)
        assert any("bullish candles" in n for n in notes)

    def test_no_warning_on_clean_candle(self) -> None:
        # Normal candle, no special conditions
        df = _ohlcv(
            (100, 106, 98, 104),  # prior: wide range
            (104, 107, 103, 106),  # signal: normal
        )
        notes = _build_candle_warnings([_event()], df)
        # only possible note is volume (which is off) — should be empty
        assert not notes


# ---------------------------------------------------------------------------
# Layout: warnings appear after SL/TP and before backtest
# ---------------------------------------------------------------------------


class TestWarningsPosition:
    _TS_MS = int(datetime(2024, 1, 15, 11, 0, tzinfo=UTC).timestamp() * 1000)

    def test_volume_warning_no_longer_in_header(self) -> None:
        ev = SignalEvent(
            symbol="BTCUSDT",
            timeframe="1h",
            strategy="engulfing",
            direction="long",
            reason="bullish_engulfing@43000.00",
            open_time=self._TS_MS,
            price=43000.0,
            sl_price=42000.0,
            low_volume=True,
        )
        msg = format_signal_alert(ev)
        lines = msg.split("\n")
        # header is first few lines, warning should appear AFTER the TP line
        tp_idx = next(i for i, line in enumerate(lines) if line.startswith("TP:"))
        warn_idx = next(i for i, line in enumerate(lines) if "Low volume" in line)
        assert warn_idx > tp_idx

    def test_gap_warning_in_warnings_block(self) -> None:
        ev = SignalEvent(
            symbol="AAPL",
            timeframe="1d",
            strategy="fvg",
            direction="long",
            reason="fvg_long@175.00",
            open_time=self._TS_MS,
            price=175.0,
            sl_price=170.0,
        )
        msg = format_signal_alert(ev, gap_warning="⚠️ Unfilled gap-down at $178.00")
        lines = msg.split("\n")
        tp_idx = next(i for i, line in enumerate(lines) if line.startswith("TP:"))
        gap_idx = next(i for i, line in enumerate(lines) if "gap-down" in line)
        assert gap_idx > tp_idx

    def test_warnings_before_backtest_summary(self) -> None:
        from signals.alert_formatter import format_confluence_alert

        ev = SignalEvent(
            symbol="BTCUSDT",
            timeframe="1h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@43000.00",
            open_time=self._TS_MS,
            price=43000.0,
            sl_price=42000.0,
            low_volume=True,
        )
        msg = format_confluence_alert([ev], backtest_summary="📊 backtest line")
        warn_idx = msg.index("Low volume")
        bt_idx = msg.index("backtest line")
        assert warn_idx < bt_idx
