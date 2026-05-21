"""Tests for D10 step 3: live co-firing confluence detection and alert formatting."""

import duckdb
import pandas as pd
import pytest

from analytics.data_store import get_combo_lookup, init_schema
from analytics.signal_lib import _find_live_cofire
from signals.alert_formatter import ConfluenceData, SignalEvent, format_confluence_alert

_BASE_TIME = 1_700_000_000_000  # arbitrary base ms timestamp
_4H_MS = 4 * 3600 * 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle(open_time: int, close: float = 100.0) -> dict:
    return {
        "open_time": open_time,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1000.0,
    }


def _make_ohlcv(n: int = 10, tf_ms: int = _4H_MS) -> pd.DataFrame:
    rows = [_candle(_BASE_TIME + i * tf_ms) for i in range(n)]
    return pd.DataFrame(rows)


def _make_event(
    strategy: str = "fvg",
    direction: str = "long",
    open_time: int = _BASE_TIME + 9 * _4H_MS,
) -> SignalEvent:
    return SignalEvent(
        symbol="ETHUSDT",
        timeframe="4h",
        strategy=strategy,
        direction=direction,
        reason=f"{strategy}_{direction}@100.00",
        open_time=open_time,
        price=100.0,
    )


def _make_combo_lookup(
    symbol: str = "ETHUSDT",
    tf: str = "4h",
    strategy_a: str = "fvg",
    strategy_b: str = "pin_bar",
    avg_r: float = 1.63,
    win_rate: float = 0.889,
    closed_trades: int = 9,
) -> dict:
    key = (symbol, tf, frozenset({strategy_a, strategy_b}))
    return {
        key: {
            "avg_r": avg_r,
            "win_rate": win_rate,
            "closed_trades": closed_trades,
            "strategy_a": strategy_a,
            "strategy_b": strategy_b,
        }
    }


def _conn_with_signals(rows: list[dict]) -> duckdb.DuckDBPyConnection:
    """In-memory DB with signals table pre-populated."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    if rows:
        df = pd.DataFrame(rows)
        # Ensure all required columns are present
        for col in [
            "symbol",
            "timeframe",
            "strategy",
            "open_time",
            "direction",
            "entry_price",
            "sl_price",
            "reason",
            "confidence",
            "fired_at",
        ]:
            if col not in df.columns:
                df[col] = None
        conn.register("_sig_df", df)
        conn.execute(
            "INSERT OR IGNORE INTO signals SELECT "
            "symbol, timeframe, strategy, open_time, direction, "
            "entry_price, sl_price, reason, confidence, fired_at FROM _sig_df"
        )
        conn.unregister("_sig_df")
    return conn


# ---------------------------------------------------------------------------
# _find_live_cofire — unit tests
# ---------------------------------------------------------------------------


class TestFindLiveCofire:
    def test_same_cycle_cofire(self) -> None:
        """Two strategies fire on the same candle → candles_ago=0."""
        ohlcv = _make_ohlcv(10)
        current_time = int(ohlcv["open_time"].iloc[-1])
        events = [
            _make_event("fvg", open_time=current_time),
            _make_event("pin_bar", open_time=current_time),
        ]
        lookup = _make_combo_lookup()
        conn = _conn_with_signals([])
        result = _find_live_cofire(events, ohlcv, conn, lookup, "ETHUSDT", "4h", 5, 1.0)
        assert result is not None
        assert result.candles_ago == 0
        assert result.avg_r == pytest.approx(1.63)
        assert result.trades == 9
        assert result.win_rate == pytest.approx(0.889)
        conn.close()

    def test_cross_cycle_cofire(self) -> None:
        """Co-strategy fired 2 candles ago in DB → candles_ago=2."""
        ohlcv = _make_ohlcv(10)
        current_time = int(ohlcv["open_time"].iloc[-1])
        co_time = int(ohlcv["open_time"].iloc[-3])  # 2 candles before current
        events = [_make_event("fvg", open_time=current_time)]
        lookup = _make_combo_lookup()
        conn = _conn_with_signals(
            [
                {
                    "symbol": "ETHUSDT",
                    "timeframe": "4h",
                    "strategy": "pin_bar",
                    "open_time": co_time,
                    "direction": "long",
                    "entry_price": 100.0,
                    "sl_price": 95.0,
                    "reason": "fib_long@100.0",
                    "confidence": 3,
                    "fired_at": co_time + 1000,
                }
            ]
        )
        result = _find_live_cofire(events, ohlcv, conn, lookup, "ETHUSDT", "4h", 5, 1.0)
        assert result is not None
        assert result.co_strategy == "pin_bar"
        assert result.candles_ago == 2
        conn.close()

    def test_outside_window_returns_none(self) -> None:
        """Co-strategy fired 6 candles ago with window=5 → None."""
        ohlcv = _make_ohlcv(10)
        current_time = int(ohlcv["open_time"].iloc[-1])
        co_time = int(ohlcv["open_time"].iloc[-7])  # 6 candles before
        events = [_make_event("fvg", open_time=current_time)]
        lookup = _make_combo_lookup()
        conn = _conn_with_signals(
            [
                {
                    "symbol": "ETHUSDT",
                    "timeframe": "4h",
                    "strategy": "pin_bar",
                    "open_time": co_time,
                    "direction": "long",
                    "entry_price": 100.0,
                    "sl_price": 95.0,
                    "reason": "fib_long@100.0",
                    "confidence": 3,
                    "fired_at": co_time + 1000,
                }
            ]
        )
        result = _find_live_cofire(events, ohlcv, conn, lookup, "ETHUSDT", "4h", 5, 1.0)
        assert result is None
        conn.close()

    def test_pair_not_in_lookup_returns_none(self) -> None:
        """Pair not present in combo_lookup → None."""
        ohlcv = _make_ohlcv(10)
        current_time = int(ohlcv["open_time"].iloc[-1])
        events = [
            _make_event("fvg", open_time=current_time),
            _make_event("engulfing", open_time=current_time),
        ]
        lookup = _make_combo_lookup(strategy_a="fvg", strategy_b="pin_bar")
        conn = _conn_with_signals([])
        result = _find_live_cofire(events, ohlcv, conn, lookup, "ETHUSDT", "4h", 5, 1.0)
        assert result is None  # fvg+engulfing not in lookup
        conn.close()

    def test_avg_r_below_threshold_returns_none(self) -> None:
        """Combo avg_r=0.8 < min_avg_r=1.0 → None."""
        ohlcv = _make_ohlcv(10)
        current_time = int(ohlcv["open_time"].iloc[-1])
        events = [
            _make_event("fvg", open_time=current_time),
            _make_event("pin_bar", open_time=current_time),
        ]
        lookup = _make_combo_lookup(avg_r=0.8)
        conn = _conn_with_signals([])
        result = _find_live_cofire(events, ohlcv, conn, lookup, "ETHUSDT", "4h", 5, 1.0)
        assert result is None
        conn.close()

    def test_multiple_pairs_returns_best(self) -> None:
        """Two valid co-fires → returns the one with higher avg_r."""
        ohlcv = _make_ohlcv(10)
        current_time = int(ohlcv["open_time"].iloc[-1])
        events = [
            _make_event("fvg", open_time=current_time),
            _make_event("pin_bar", open_time=current_time),
            _make_event("bos", open_time=current_time),
        ]
        lookup: dict = {
            ("ETHUSDT", "4h", frozenset({"fvg", "pin_bar"})): {
                "avg_r": 1.63,
                "win_rate": 0.889,
                "closed_trades": 9,
                "strategy_a": "fvg",
                "strategy_b": "pin_bar",
            },
            ("ETHUSDT", "4h", frozenset({"fvg", "bos"})): {
                "avg_r": 1.20,
                "win_rate": 0.75,
                "closed_trades": 12,
                "strategy_a": "fvg",
                "strategy_b": "bos",
            },
        }
        conn = _conn_with_signals([])
        result = _find_live_cofire(events, ohlcv, conn, lookup, "ETHUSDT", "4h", 5, 1.0)
        assert result is not None
        assert result.avg_r == pytest.approx(1.63)
        conn.close()

    def test_opposing_direction_ignored(self) -> None:
        """DB signal in opposite direction not matched."""
        ohlcv = _make_ohlcv(10)
        current_time = int(ohlcv["open_time"].iloc[-1])
        co_time = int(ohlcv["open_time"].iloc[-2])
        events = [_make_event("fvg", direction="long", open_time=current_time)]
        lookup = _make_combo_lookup()
        conn = _conn_with_signals(
            [
                {
                    "symbol": "ETHUSDT",
                    "timeframe": "4h",
                    "strategy": "pin_bar",
                    "open_time": co_time,
                    "direction": "short",  # wrong direction
                    "entry_price": 100.0,
                    "sl_price": 105.0,
                    "reason": "fib_short@100.0",
                    "confidence": 3,
                    "fired_at": co_time + 1000,
                }
            ]
        )
        result = _find_live_cofire(events, ohlcv, conn, lookup, "ETHUSDT", "4h", 5, 1.0)
        assert result is None
        conn.close()

    def test_empty_lookup_returns_none(self) -> None:
        """Empty combo_lookup → None immediately."""
        ohlcv = _make_ohlcv(10)
        current_time = int(ohlcv["open_time"].iloc[-1])
        events = [_make_event("fvg", open_time=current_time)]
        conn = _conn_with_signals([])
        result = _find_live_cofire(events, ohlcv, conn, {}, "ETHUSDT", "4h", 5, 1.0)
        assert result is None
        conn.close()


# ---------------------------------------------------------------------------
# format_confluence_alert — confluence blockquote rendering
# ---------------------------------------------------------------------------


class TestConfluenceAlertFormatting:
    def _base_event(self, strategy: str = "fvg") -> SignalEvent:
        return SignalEvent(
            symbol="ETHUSDT",
            timeframe="4h",
            strategy=strategy,
            direction="long",
            reason="fvg_long@2450.00-2480.00",
            open_time=1_700_000_000_000,
            price=2450.0,
            sl_price=2380.0,
        )

    def test_blockquote_present_when_cofire_set(self) -> None:
        ev = self._base_event()
        ev.confluence_combo = ConfluenceData(
            co_strategy="pin_bar",
            candles_ago=2,
            avg_r=1.63,
            trades=9,
            win_rate=0.889,
            type_a="fib",
            type_b="structural",
        )
        msg = format_confluence_alert([ev])
        assert "> ⚡⚡ CONFLUENCE" in msg
        assert "pin_bar co-fired 2 candles ago" in msg
        assert "+1.63R" in msg
        assert "9 trades" in msg
        assert "88.9% win" in msg
        assert "fib + structural" in msg

    def test_blockquote_absent_when_no_cofire(self) -> None:
        ev = self._base_event()
        msg = format_confluence_alert([ev])
        assert "CONFLUENCE" not in msg
        assert "⚡⚡" not in msg

    def test_candles_ago_zero_shows_this_candle(self) -> None:
        ev = self._base_event()
        ev.confluence_combo = ConfluenceData(
            co_strategy="engulfing",
            candles_ago=0,
            avg_r=1.1,
            trades=5,
            win_rate=0.8,
            type_a="candlestick",
            type_b="structural",
        )
        msg = format_confluence_alert([ev])
        assert "this candle" in msg

    def test_candles_ago_one_singular(self) -> None:
        ev = self._base_event()
        ev.confluence_combo = ConfluenceData(
            co_strategy="engulfing",
            candles_ago=1,
            avg_r=1.1,
            trades=5,
            win_rate=0.8,
            type_a="candlestick",
            type_b="structural",
        )
        msg = format_confluence_alert([ev])
        assert "1 candle ago" in msg
        assert "1 candles ago" not in msg

    def test_blockquote_after_backtest_summary_before_stats(self) -> None:
        ev = self._base_event()
        ev.confluence_combo = ConfluenceData(
            co_strategy="pin_bar",
            candles_ago=2,
            avg_r=1.63,
            trades=9,
            win_rate=0.889,
            type_a="fib",
            type_b="structural",
        )
        msg = format_confluence_alert(
            [ev],
            backtest_summary="BT: 9 trades · 88.9% · +1.63R avg",
        )
        bt_pos = msg.find("BT:")
        conf_pos = msg.find("CONFLUENCE")
        assert bt_pos < conf_pos  # backtest summary before confluence

    def test_best_cofire_chosen_across_events(self) -> None:
        """Formatter picks the ConfluenceData with highest avg_r across events."""
        ev1 = self._base_event("fvg")
        ev1.confluence_combo = ConfluenceData(
            co_strategy="pin_bar",
            candles_ago=2,
            avg_r=1.63,
            trades=9,
            win_rate=0.889,
            type_a="fib",
            type_b="structural",
        )
        ev2 = self._base_event("bos")
        ev2.confluence_combo = ConfluenceData(
            co_strategy="engulfing",
            candles_ago=1,
            avg_r=1.1,
            trades=5,
            win_rate=0.8,
            type_a="candlestick",
            type_b="structural",
        )
        msg = format_confluence_alert([ev1, ev2])
        assert "pin_bar" in msg
        assert "1.63" in msg

    def test_orderflow_signals_appended(self) -> None:
        ev = self._base_event()
        ev.confluence_combo = ConfluenceData(
            co_strategy="pin_bar",
            candles_ago=0,
            avg_r=1.63,
            trades=9,
            win_rate=0.889,
            type_a="fib",
            type_b="structural",
            orderflow_signals=["📊 OI +8.3% last 4h — accumulation"],
        )
        msg = format_confluence_alert([ev])
        assert "📊 OI +8.3%" in msg


# ---------------------------------------------------------------------------
# get_combo_lookup — data_store integration
# ---------------------------------------------------------------------------


class TestGetComboLookup:
    def test_empty_returns_empty_dict(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        result = get_combo_lookup(conn)
        assert result == {}
        conn.close()

    def test_best_avg_r_per_pair(self) -> None:
        """When two rows exist for the same pair (different day_filter), pick best avg_r."""

        conn = duckdb.connect(":memory:")
        init_schema(conn)

        # Build two minimal ComboBacktestResult-like objects to upsert via SQL directly.
        # It's simpler to INSERT directly than to construct ComboBacktestResult.
        def _insert(day_filter: str, avg_r: float) -> None:
            combo_id = f"ETHUSDT|4h|fvg+pin_bar|w5|{day_filter}"
            conn.execute(
                """
                INSERT OR REPLACE INTO backtest_combos VALUES (
                    ?, 'ETHUSDT', '4h', 'fvg', 'pin_bar', 5,
                    0, 0, 200, 0.02, 2.0, 0.0005, ?,
                    30, 20, 16, 0.80, ?, 10.0, 2.0, 1.0,
                    10, 0.80, ?, 10, 0.80, ?, epoch_ms(now())
                )
                """,
                [combo_id, day_filter, avg_r, avg_r, avg_r],
            )

        _insert("weekdays", 1.20)
        _insert("tue_thu", 1.63)  # higher avg_r — should win

        result = get_combo_lookup(conn)
        key = ("ETHUSDT", "4h", frozenset({"fvg", "pin_bar"}))
        assert key in result
        assert result[key]["avg_r"] == pytest.approx(1.63)
        conn.close()
