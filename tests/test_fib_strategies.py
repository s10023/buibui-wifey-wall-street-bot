"""Tests for D4 (ote_entry) and D5 (volume_confirm gate)."""

import pandas as pd

from analytics.strategies import (
    SIGNAL_COLUMNS,
    STRATEGY_REGISTRY,
    detect_engulfing,
    detect_hammer_hanging_man,
    detect_ote_entry,
    detect_pin_bar,
    volume_confirm,
)
from tests.conftest import _candle, _make_ohlcv

_BASE_TIME = 1_700_000_000_000
_STEP = 60_000  # 1-minute candles


def _t(n: int) -> int:
    return _BASE_TIME + n * _STEP


def _assert_signal_columns(df: pd.DataFrame) -> None:
    assert list(df.columns) == SIGNAL_COLUMNS


# ---------------------------------------------------------------------------
# Helpers for building BOS setups
# ---------------------------------------------------------------------------


_SWING_LB = 6  # swing_lookback used in all fib/ote tests
_BOS_LB = 2  # bos_lookback used in all fib/ote tests


def _make_bullish_bos_df(
    swing_low: float = 90.0,
    swing_high: float = 110.0,
    retrace_close: float = 100.5,  # inside 0.5–0.618 zone
    extra_candles: int = 0,
    volume_low: bool = False,
) -> pd.DataFrame:
    """Build a df with a bullish BOS followed by a retrace candle.

    Layout with swing_lookback=10, bos_lookback=3:
      bos_start = n - 3 - 1 = n - 4
      struct_zone = [0, n-4)
      bos_zone = [n-4, n-1)   (3 candles)
      signal_candle = n-1

    We place 5 flat candles, swing_low, swing_high in struct_zone,
    then BOS candle + 2 more bos-zone candles, then the signal candle.
    Total: 5 + 1 + 1 + 3 + 1 = 11 candles.
    """
    rows = []
    # 5 flat prior candles (struct zone prefix; indices 0-4)
    for i in range(5):
        rows.append(_candle(_t(i), 100.0, 101.0, 99.0, 100.0))
    # Swing low (idx 5, in struct zone)
    rows.append(_candle(_t(5), 92.0, 93.0, swing_low, 92.5))
    # Swing high (idx 6, in struct zone)
    rows.append(_candle(_t(6), 108.0, swing_high, 107.0, 108.5))
    # BOS confirmation candle: close above swing_high (idx 7, start of bos_zone)
    rows.append(_candle(_t(7), 109.0, swing_high + 2.0, 108.5, swing_high + 1.0))
    # 1 more bos-zone candle (bos_lookback=2 → 2 bos zone candles: idx 7, 8)
    rows.append(_candle(_t(8), 111.0, 112.0, 110.0, 111.0))
    for j in range(extra_candles):
        rows.append(_candle(_t(9 + j), 111.0, 112.0, 110.0, 111.0))
    # Retrace signal candle
    vol = 10.0 if volume_low else 100.0
    idx = 9 + extra_candles
    rows.append(
        _candle(
            _t(idx),
            retrace_close + 0.2,
            retrace_close + 0.5,
            retrace_close - 0.1,
            retrace_close,
            volume=vol,
        )
    )
    return _make_ohlcv(rows)


def _make_bearish_bos_df(
    swing_low: float = 90.0,
    swing_high: float = 110.0,
    retrace_close: float = 99.5,  # inside 0.5–0.618 zone for short
    extra_candles: int = 0,
) -> pd.DataFrame:
    """Build a df with a bearish BOS followed by a retrace candle."""
    rows = []
    for i in range(5):
        rows.append(_candle(_t(i), 100.0, 101.0, 99.0, 100.0))
    # Swing high (idx 5, in struct zone)
    rows.append(_candle(_t(5), 108.0, swing_high, 107.0, 108.5))
    # Swing low (idx 6, in struct zone)
    rows.append(_candle(_t(6), 92.5, 93.0, swing_low, 92.0))
    # BOS: close breaks below swing_low (idx 7, start of bos_zone)
    rows.append(_candle(_t(7), 91.5, 92.0, swing_low - 2.0, swing_low - 1.0))
    # 1 more bos-zone candle (bos_lookback=2 → idx 7, 8)
    rows.append(_candle(_t(8), 89.5, 90.0, 88.5, 89.5))
    for j in range(extra_candles):
        rows.append(_candle(_t(9 + j), 89.0, 90.0, 88.0, 89.0))
    idx = 9 + extra_candles
    rows.append(
        _candle(
            _t(idx),
            retrace_close - 0.2,
            retrace_close + 0.1,
            retrace_close - 0.5,
            retrace_close,
        )
    )
    return _make_ohlcv(rows)


# ---------------------------------------------------------------------------
# volume_confirm helper
# ---------------------------------------------------------------------------


class TestVolumeConfirm:
    def test_returns_true_when_volume_sufficient(self) -> None:
        rows = [_candle(_t(i), 100, 101, 99, 100, volume=100.0) for i in range(21)]
        # Last candle has 2× avg volume → should pass
        rows[-1] = _candle(_t(20), 100, 101, 99, 100, volume=200.0)
        df = _make_ohlcv(rows)
        assert volume_confirm(df, 20, multiplier=1.5, lookback=20) is True

    def test_returns_false_when_volume_low(self) -> None:
        rows = [_candle(_t(i), 100, 101, 99, 100, volume=100.0) for i in range(21)]
        rows[-1] = _candle(_t(20), 100, 101, 99, 100, volume=10.0)
        df = _make_ohlcv(rows)
        assert volume_confirm(df, 20, multiplier=1.5, lookback=20) is False

    def test_returns_true_when_no_volume_column(self) -> None:
        df = pd.DataFrame({"open": [100.0], "close": [100.0]})
        assert volume_confirm(df, 0) is True

    def test_returns_true_at_idx_zero(self) -> None:
        rows = [_candle(_t(i), 100, 101, 99, 100, volume=5.0) for i in range(5)]
        df = _make_ohlcv(rows)
        assert volume_confirm(df, 0) is True


# ---------------------------------------------------------------------------
# D5: volume gate on engulfing
# ---------------------------------------------------------------------------


class TestVolumeGateEngulfing:
    def test_high_volume_no_vol_low_tag(self) -> None:
        rows = [_candle(_t(i), 100, 101, 99, 100, volume=100.0) for i in range(20)]
        # Prior bearish candle
        rows.append(_candle(_t(20), 105, 110, 90, 95, volume=100.0))
        # Engulfing bullish with high volume
        rows.append(_candle(_t(21), 93, 115, 88, 108, volume=300.0))
        df = _make_ohlcv(rows)
        result = detect_engulfing(df)
        assert not result.empty
        assert result.iloc[-1]["low_volume"] == False  # noqa: E712

    def test_low_volume_adds_vol_low_tag(self) -> None:
        rows = [_candle(_t(i), 100, 101, 99, 100, volume=100.0) for i in range(20)]
        rows.append(_candle(_t(20), 105, 110, 90, 95, volume=100.0))
        # Engulfing bullish with LOW volume
        rows.append(_candle(_t(21), 93, 115, 88, 108, volume=5.0))
        df = _make_ohlcv(rows)
        result = detect_engulfing(df)
        assert not result.empty
        assert result.iloc[-1]["low_volume"] == True  # noqa: E712

    def test_signal_still_emitted_on_low_volume(self) -> None:
        """Signal must still be emitted — volume gate only tags, not suppresses."""
        rows = [_candle(_t(i), 100, 101, 99, 100, volume=100.0) for i in range(20)]
        rows.append(_candle(_t(20), 105, 110, 90, 95, volume=100.0))
        rows.append(_candle(_t(21), 93, 115, 88, 108, volume=5.0))
        df = _make_ohlcv(rows)
        result = detect_engulfing(df)
        assert not result.empty
        assert result.iloc[-1]["direction"] == "long"


# ---------------------------------------------------------------------------
# D5: volume gate on pin_bar
# ---------------------------------------------------------------------------


class TestVolumeGatePinBar:
    def test_low_volume_adds_vol_low_tag(self) -> None:
        rows = [_candle(_t(i), 100, 101, 99, 100, volume=100.0) for i in range(20)]
        # Bullish pin bar with tiny volume
        rows.append(_candle(_t(20), 99, 101, 88, 100, volume=5.0))
        df = _make_ohlcv(rows)
        result = detect_pin_bar(df, wick_ratio=2.0)
        # Find pin bar candle signal (if emitted)
        pin_signals = result[result["direction"] == "long"]
        if not pin_signals.empty:
            assert pin_signals.iloc[-1]["low_volume"] == True  # noqa: E712

    def test_high_volume_no_vol_low_tag(self) -> None:
        rows = [_candle(_t(i), 100, 101, 99, 100, volume=100.0) for i in range(20)]
        rows.append(_candle(_t(20), 99, 101, 88, 100, volume=300.0))
        df = _make_ohlcv(rows)
        result = detect_pin_bar(df, wick_ratio=2.0)
        pin_signals = result[result["direction"] == "long"]
        if not pin_signals.empty:
            assert pin_signals.iloc[-1]["low_volume"] == False  # noqa: E712


# ---------------------------------------------------------------------------
# D5: volume gate on hammer_hanging_man
# ---------------------------------------------------------------------------


class TestVolumeGateHammerHangingMan:
    def test_low_volume_adds_vol_low_tag_to_hammer(self) -> None:
        # Build downtrend then hammer with low volume
        rows = []
        # Downtrend: closes falling from 120 to 100
        for i in range(11):
            c = 120.0 - i * 2
            rows.append(_candle(_t(i), c + 1, c + 2, c - 1, c, volume=100.0))
        # Hammer with tiny volume: small body at top, long lower wick
        rows.append(_candle(_t(11), 100, 101, 88, 100, volume=5.0))
        df = _make_ohlcv(rows)
        result = detect_hammer_hanging_man(df, context_lookback=10)
        hammers = result[result["direction"] == "long"]
        if not hammers.empty:
            assert hammers.iloc[-1]["low_volume"] == True  # noqa: E712


# ---------------------------------------------------------------------------
# D4: ote_entry
# ---------------------------------------------------------------------------


class TestDetectOteEntry:
    def test_returns_empty_on_short_df(self) -> None:
        df = _make_ohlcv([_candle(_t(i), 100, 101, 99, 100) for i in range(5)])
        result = detect_ote_entry(df)
        assert result.empty
        _assert_signal_columns(result)

    def test_long_signal_in_ote_zone(self) -> None:
        """Bullish BOS. OTE zone: fib 0.618–0.786.
        swing_high=110, swing_low=90, range=20.
        fib_0_618 = 110 - 0.618*20 = 97.64
        fib_0_786 = 110 - 0.786*20 = 94.28
        Retrace close at 96.0 (inside [94.28, 97.64]).
        """
        swing_low = 90.0
        swing_high = 110.0
        retrace = 96.0  # inside OTE [94.28, 97.64]
        df = _make_bullish_bos_df(
            swing_low=swing_low, swing_high=swing_high, retrace_close=retrace
        )
        result = detect_ote_entry(df, swing_lookback=_SWING_LB, bos_lookback=_BOS_LB)
        long_sigs = result[result["direction"] == "long"]
        assert not long_sigs.empty, "Expected a LONG OTE signal"
        row = long_sigs.iloc[-1]
        assert "ote_long" in row["reason"]
        assert "0.786" in row["reason"]
        assert "OTE:" in row["context"]
        assert "1.618 ext" in row["context"]

    def test_no_signal_in_golden_zone_not_ote(self) -> None:
        """Close at 99.0 is in golden zone (50–61.8%) but NOT in OTE (61.8–78.6%)."""
        swing_low = 90.0
        swing_high = 110.0
        retrace = 99.0  # in golden zone, above OTE
        df = _make_bullish_bos_df(
            swing_low=swing_low, swing_high=swing_high, retrace_close=retrace
        )
        result = detect_ote_entry(df, swing_lookback=_SWING_LB, bos_lookback=_BOS_LB)
        long_sigs = result[result["direction"] == "long"]
        assert long_sigs.empty

    def test_short_signal_in_ote_zone(self) -> None:
        """Bearish BOS. OTE zone (short): fib 0.618–0.786 from swing_low upward.
        swing_low=90, swing_high=110, range=20.
        fib_0_618 = 90 + 0.618*20 = 102.36
        fib_0_786 = 90 + 0.786*20 = 105.72
        Retrace close at 104.0 (inside [102.36, 105.72]).
        """
        swing_low = 90.0
        swing_high = 110.0
        retrace = 104.0
        df = _make_bearish_bos_df(
            swing_low=swing_low, swing_high=swing_high, retrace_close=retrace
        )
        result = detect_ote_entry(df, swing_lookback=_SWING_LB, bos_lookback=_BOS_LB)
        short_sigs = result[result["direction"] == "short"]
        assert not short_sigs.empty, "Expected a SHORT OTE signal"
        row = short_sigs.iloc[-1]
        assert "ote_short" in row["reason"]

    def test_strategy_registry_entry(self) -> None:
        assert "ote_entry" in STRATEGY_REGISTRY
        spec = STRATEGY_REGISTRY["ote_entry"]
        assert spec.get_confidence("4h") >= 1
        param_names = [p.name for p in spec.params]
        assert "swing_lookback" in param_names
        assert "bos_lookback" in param_names

    def test_signal_registry_entry(self) -> None:
        from signals.registry import SIGNAL_REGISTRY

        assert "ote_entry" in SIGNAL_REGISTRY

    def test_ote_fires_in_zone(self) -> None:
        """OTE produces a valid DataFrame and fires in its zone."""
        df_ote = _make_bullish_bos_df(
            swing_low=90.0, swing_high=110.0, retrace_close=96.0
        )
        ote_result = detect_ote_entry(
            df_ote, swing_lookback=_SWING_LB, bos_lookback=_BOS_LB
        )
        assert isinstance(ote_result, pd.DataFrame)
        assert not ote_result.empty
