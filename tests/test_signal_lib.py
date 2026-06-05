"""Tests for signal_lib components — in-memory DuckDB, no real network calls."""

from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from analytics.data_store import init_schema
from analytics.signal_lib import (
    _backtest_summary,
    _fmt_hold,
    _reset_bt_cache,
    parse_timeframe_secs,
    run_scan_cycle,
    scan_symbol,
    secs_until_next_boundary,
)
from signals.alert_formatter import (
    SignalEvent,
    format_confluence_alert,
    format_signal_alert,
)
from signals.cooldown_store import CooldownStore


@pytest.fixture(autouse=True)
def reset_bt_cache() -> None:
    """Clear module-level L1 backtest cache before each test to prevent state bleed."""
    _reset_bt_cache()


# Far-future open_time used for synthetic "currently forming" bars in scanner
# tests. scan_symbol now drops the final bar only when its period has not yet
# elapsed (now < open_time + tf), so a forming bar must sit in the future to be
# excluded; a past timestamp would (correctly) be treated as a closed candle.
_FUTURE_FORMING_MS = 7258118400000  # ~year 2200


class TestParseTimeframeSecs:
    def test_minutes(self) -> None:
        assert parse_timeframe_secs("15m") == 900

    def test_hours(self) -> None:
        assert parse_timeframe_secs("4h") == 14400

    def test_one_hour(self) -> None:
        assert parse_timeframe_secs("1h") == 3600

    def test_days(self) -> None:
        assert parse_timeframe_secs("1d") == 86400


class TestSecsUntilNextBoundary:
    def test_wakes_at_next_4h_boundary(self) -> None:
        # now = 14:02:00 UTC → next 4h boundary = 16:00:00 + 10s buffer
        now = 14 * 3600 + 2 * 60  # 50520s since midnight
        with patch("analytics.signal.scanner.time.time", return_value=float(now)):
            secs, wake_ts = secs_until_next_boundary(["4h"])
        expected = (16 * 3600 + 10) - now  # 7090s
        assert secs == expected
        assert wake_ts == 16 * 3600 + 10

    def test_picks_earliest_boundary_across_timeframes(self) -> None:
        # now = 14:02:00 → next 1h boundary = 15:00:10, next 4h = 16:00:10
        now = 14 * 3600 + 2 * 60
        with patch("analytics.signal.scanner.time.time", return_value=float(now)):
            secs, wake_ts = secs_until_next_boundary(["4h", "1h"])
        expected = (15 * 3600 + 10) - now  # 3490s — the 1h boundary wins
        assert secs == expected
        assert wake_ts == 15 * 3600 + 10

    def test_never_returns_negative(self) -> None:
        # now is exactly on a boundary + buffer — result should be a full interval away
        now = 4 * 3600 + 10  # exactly at 04:00:10
        with patch("analytics.signal.scanner.time.time", return_value=float(now)):
            secs, _ = secs_until_next_boundary(["4h"])
        assert secs >= 0.0


class TestCooldownStore:
    def test_new_candle_returns_true_initially(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 1000) is True

    def test_mark_candle_deduplicates_same_open_time(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "4h", "fvg", 1000)
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 1000) is False

    def test_newer_candle_passes_after_mark(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "4h", "fvg", 1000)
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 2000) is True

    def test_state_persists_across_instances(self, tmp_path: Any) -> None:
        path = str(tmp_path / "state.json")
        store1 = CooldownStore(path)
        store1.mark_candle("BTCUSDT", "4h", "fvg", 5000)
        store2 = CooldownStore(path)
        assert store2.is_new_candle("BTCUSDT", "4h", "fvg", 5000) is False

    def test_different_strategies_are_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "4h", "fvg", 1000)
        assert store.is_new_candle("BTCUSDT", "4h", "bos", 1000) is True

    def test_different_symbols_are_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "4h", "fvg", 1000)
        assert store.is_new_candle("ETHUSDT", "4h", "fvg", 1000) is True

    def test_corrupted_state_file_starts_fresh(self, tmp_path: Any) -> None:
        path = tmp_path / "state.json"
        path.write_text("not valid json")
        store = CooldownStore(str(path))
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 1000) is True


class TestFormatSignalAlert:
    def test_long_alert_contains_symbol_and_direction(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@43200.00-43350.00",
            open_time=1700000000000,
            price=43260.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02, tp_r=2.0)
        assert "BTCUSDT" in msg
        assert "fvg" in msg
        assert "LONG" in msg
        assert "43,260.00" in msg

    def test_short_alert_contains_symbol_and_direction(self) -> None:
        event = SignalEvent(
            symbol="ETHUSDT",
            timeframe="1h",
            strategy="bos",
            direction="short",
            reason="bos_short@2500.00",
            open_time=1700000000000,
            price=2500.0,
        )
        msg = format_signal_alert(event)
        assert "SHORT" in msg
        assert "ETHUSDT" in msg
        assert "bos" in msg

    def test_long_sl_is_below_price(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        # SL = 1000 * 0.98 = 980
        assert "980.00" in msg

    def test_short_sl_is_above_price(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="short",
            reason="fvg_short@110.00-100.00",
            open_time=1700000000000,
            price=1000.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        # SL = 1000 * 1.02 = 1020
        assert "1,020.00" in msg

    def test_tp_r_reflected_in_message(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02, tp_r=3.0)
        assert "3.0R" in msg

    def test_structural_sl_used_when_valid_long(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="5m",
            strategy="fvg",
            direction="long",
            reason="fvg_long@90.00-95.00",
            open_time=1700000000000,
            price=100.0,
            sl_price=90.0,  # structural: below gap_bot
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        # Structural SL = 90.0, not pct-based 98.0
        assert "90.00" in msg
        assert "980.00" not in msg

    def test_structural_sl_used_when_valid_short(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="5m",
            strategy="fvg",
            direction="short",
            reason="fvg_short@110.00-105.00",
            open_time=1700000000000,
            price=100.0,
            sl_price=110.0,  # structural: above gap_top
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        assert "110.00" in msg
        assert "1,020.00" not in msg

    def test_fallback_to_pct_when_sl_price_zero(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="bos",
            direction="long",
            reason="no_structural_sl",
            open_time=1700000000000,
            price=1000.0,
            sl_price=0.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        assert "980.00" in msg

    def test_context_appears_in_single_event_alert(self) -> None:
        event = SignalEvent(
            symbol="SOLUSDT",
            timeframe="5m",
            strategy="fvg",
            direction="long",
            reason="fvg_long@94.59-94.73",
            open_time=1700000000000,
            price=94.67,
            sl_price=94.59,
            context="Gap: 17-Nov 10:00 · 17-Nov 10:05 · 17-Nov 10:10",
        )
        msg = format_signal_alert(event)
        assert "Gap: 17-Nov 10:00" in msg

    def test_signal_time_shown_in_alert(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
        )
        msg = format_signal_alert(event)
        assert "MYT" in msg


class TestFormatConfluenceAlert:
    def _make_event(
        self,
        strategy: str,
        direction: str = "long",
        price: float = 100.0,
        sl_price: float = 0.0,
        context: str = "",
    ) -> SignalEvent:
        return SignalEvent(
            symbol="BTCUSDT",
            timeframe="5m",
            strategy=strategy,
            direction=direction,
            reason=f"{strategy}_{direction}@test",
            open_time=1700000000000,
            price=price,
            sl_price=sl_price,
            context=context,
        )

    def test_single_event_shows_strategy_in_code_tags(self) -> None:
        event = self._make_event("fvg", sl_price=90.0)
        msg = format_confluence_alert([event])
        assert "<code>fvg</code>" in msg
        assert "Confluence" not in msg

    def test_two_events_shows_confluence_header(self) -> None:
        events = [
            self._make_event("fvg", sl_price=90.0),
            self._make_event("pin_bar", sl_price=88.0),
        ]
        msg = format_confluence_alert(events)
        assert "Confluence: 2 strategies" in msg
        assert "fvg" in msg
        assert "pin_bar" in msg

    def test_confluence_uses_widest_sl_long(self) -> None:
        # Two longs: sl_price 90 and 85. Widest = lowest = 85 (most conservative).
        events = [
            self._make_event("fvg", sl_price=90.0),
            self._make_event("pin_bar", sl_price=85.0),
        ]
        msg = format_confluence_alert(events)
        assert "85.00" in msg
        assert "90.00" not in msg

    def test_confluence_uses_widest_sl_short(self) -> None:
        # Two shorts: sl_price 110 and 115. Widest = highest = 115 (most conservative).
        events = [
            self._make_event("fvg", direction="short", price=100.0, sl_price=110.0),
            self._make_event("bos", direction="short", price=100.0, sl_price=115.0),
        ]
        msg = format_confluence_alert(events)
        assert "115.00" in msg
        assert "110.00" not in msg

    def test_min_sl_pct_enforced_long(self) -> None:
        # sl_price 99.9 is only 0.1% below price 100. min_sl_pct=0.01 → SL = 99.0.
        event = self._make_event("wick_fill", sl_price=99.9)
        msg = format_confluence_alert([event], min_sl_pct=0.01)
        assert "99.00" in msg
        assert "99.90" not in msg

    def test_min_sl_pct_enforced_short(self) -> None:
        # sl_price 100.1 is only 0.1% above price 100. min_sl_pct=0.01 → SL = 101.0.
        event = self._make_event(
            "wick_fill", direction="short", price=100.0, sl_price=100.1
        )
        msg = format_confluence_alert([event], min_sl_pct=0.01)
        assert "101.00" in msg
        assert "100.10" not in msg

    def test_min_sl_pct_not_applied_when_sl_already_wide_enough(self) -> None:
        # sl_price 90 is 10% below price 100. min_sl_pct=0.01 → no override.
        event = self._make_event("fvg", sl_price=90.0)
        msg = format_confluence_alert([event], min_sl_pct=0.01)
        assert "90.00" in msg

    def test_context_shown_in_confluence_line(self) -> None:
        events = [
            self._make_event(
                "fvg", sl_price=90.0, context="Gap: 17-Nov 10:00 · 10:05 · 10:10"
            ),
            self._make_event("pin_bar", sl_price=88.0),
        ]
        msg = format_confluence_alert(events)
        assert "Gap: 17-Nov 10:00" in msg

    def test_confidence_stars_shown_for_single_signal(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
            confidence=4,
        )
        msg = format_signal_alert(event)
        assert "★★★★☆" in msg

    def test_no_stars_when_confidence_unset(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
            confidence=0,
        )
        msg = format_signal_alert(event)
        assert "★" not in msg

    def test_confidence_stars_shown_in_confluence_per_strategy(self) -> None:
        events = [
            SignalEvent(
                symbol="BTCUSDT",
                timeframe="15m",
                strategy="fvg",
                direction="long",
                reason="fvg_long@test",
                open_time=1700000000000,
                price=100.0,
                sl_price=90.0,
                confidence=4,
            ),
            SignalEvent(
                symbol="BTCUSDT",
                timeframe="15m",
                strategy="pin_bar",
                direction="long",
                reason="pin_bar_long@test",
                open_time=1700000000000,
                price=100.0,
                sl_price=88.0,
                confidence=4,
            ),
        ]
        msg = format_confluence_alert(events)
        assert msg.count("★★★★☆") == 2


class TestDayFilter:
    """Tests for the day_filter param in scan_symbol and run_scan_cycle.

    Timestamps used (all UTC):
      Monday    2024-01-01 00:00:00 UTC  → 1704067200000 ms  (weekday 0)
      Wednesday 2024-01-03 00:00:00 UTC  → 1704240000000 ms  (weekday 2)
      Friday    2024-01-05 00:00:00 UTC  → 1704412800000 ms  (weekday 4)
      Saturday  2024-01-06 00:00:00 UTC  → 1704499200000 ms  (weekday 5)
    """

    # Pre-computed UTC timestamps (ms)
    _MONDAY_MS = 1704067200000
    _WEDNESDAY_MS = 1704240000000
    _FRIDAY_MS = 1704412800000
    _SATURDAY_MS = 1704499200000

    def _make_ohlcv(self, open_time_ms: int) -> pd.DataFrame:
        """Minimal OHLCV DataFrame with 4 rows; second-to-last row has the given
        open_time (the latest *closed* candle); the final row is the forming candle."""
        rows = [
            {
                "open_time": open_time_ms - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": open_time_ms - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": open_time_ms,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": _FUTURE_FORMING_MS,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self, open_time_ms: int) -> pd.DataFrame:
        """Minimal signals DataFrame that matches the latest candle."""
        return pd.DataFrame(
            [
                {
                    "open_time": open_time_ms,
                    "direction": "long",
                    "reason": "fvg_long@100.00-102.00",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )

    def test_day_filter_false_passes_monday_signal(self) -> None:
        """With day_filter="off" (default), Monday signals are not suppressed."""
        ohlcv = self._make_ohlcv(self._MONDAY_MS)
        signals_df = self._make_signals_df(self._MONDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="off",
            )

        assert len(events) == 1

    def test_day_filter_true_suppresses_monday_signal(self) -> None:
        """With day_filter="tue_thu", a signal on Monday is filtered out."""
        ohlcv = self._make_ohlcv(self._MONDAY_MS)
        signals_df = self._make_signals_df(self._MONDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="tue_thu",
            )

        assert len(events) == 0

    def test_day_filter_true_suppresses_friday_signal(self) -> None:
        """With day_filter="tue_thu", a signal on Friday is filtered out."""
        ohlcv = self._make_ohlcv(self._FRIDAY_MS)
        signals_df = self._make_signals_df(self._FRIDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="tue_thu",
            )

        assert len(events) == 0

    def test_day_filter_true_passes_wednesday_signal(self) -> None:
        """With day_filter="tue_thu", a signal on Wednesday is NOT suppressed."""
        ohlcv = self._make_ohlcv(self._WEDNESDAY_MS)
        signals_df = self._make_signals_df(self._WEDNESDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="tue_thu",
            )

        assert len(events) == 1

    def test_day_filter_weekdays_passes_friday_signal(self) -> None:
        """With day_filter="weekdays", a signal on Friday is NOT suppressed."""
        ohlcv = self._make_ohlcv(self._FRIDAY_MS)
        signals_df = self._make_signals_df(self._FRIDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="weekdays",
            )

        assert len(events) == 1

    def test_day_filter_weekdays_suppresses_saturday_signal(self) -> None:
        """With day_filter="weekdays", a signal on Saturday is suppressed."""
        ohlcv = self._make_ohlcv(self._SATURDAY_MS)
        signals_df = self._make_signals_df(self._SATURDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="weekdays",
            )

        assert len(events) == 0

    def test_run_scan_cycle_day_filter_propagated(self, tmp_path: Any) -> None:
        """day_filter="tue_thu" passed to run_scan_cycle suppresses Monday signals."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        monday_ohlcv = self._make_ohlcv(self._MONDAY_MS)
        monday_signals = self._make_signals_df(self._MONDAY_MS)

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=monday_ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: monday_signals,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                day_filter="tue_thu",
            )

        assert alerts == [], (
            "Monday signals should be suppressed with day_filter=tue_thu"
        )

    def test_run_scan_cycle_day_filter_false_default_passes_monday(
        self, tmp_path: Any
    ) -> None:
        """day_filter defaults to "off" — Monday signals reach the alert stage."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        monday_ohlcv = self._make_ohlcv(self._MONDAY_MS)
        monday_signals = self._make_signals_df(self._MONDAY_MS)

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=monday_ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: monday_signals,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                day_filter="off",
            )

        assert len(alerts) == 1, "Monday signals should pass when day_filter=off"


class TestFormingBarExclusion:
    """scan_symbol must fire on the latest *closed* candle.

    The final row is dropped only when it is still forming (its period has not
    yet fully elapsed). For yfinance equities scanned pre-market / after-close,
    the last row is already a closed bar and must NOT be skipped — otherwise the
    alert fires one candle stale (the bug behind the stale 03-Jun META alert).
    """

    _DAY_MS = 86_400_000
    _CLOSED_MS = 1704067200000  # 2024-01-01 UTC — long past, so always closed
    _FORMING_MS = 7258118400000  # ~year 2200 — far future, so always still forming

    def _ohlcv(self, open_times: list[int]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": ot,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 98.0,
                    "close": 102.0,
                    "volume": 1.0,
                }
                for ot in open_times
            ]
        )

    def _scan(self, ohlcv: pd.DataFrame, signal_open_time: int) -> list[SignalEvent]:
        signals_df = pd.DataFrame(
            [
                {
                    "open_time": signal_open_time,
                    "direction": "long",
                    "reason": "fvg_long@100.00-102.00",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )
        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 4}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            return scan_symbol(
                ohlcv_df=ohlcv, symbol="AAPL", timeframe="1d", strategies=["fvg"]
            )

    def test_fires_on_last_bar_when_closed(self) -> None:
        """No forming bar present (pre-market): the last row is the latest closed."""
        c = self._CLOSED_MS
        ohlcv = self._ohlcv([c - 2 * self._DAY_MS, c - self._DAY_MS, c])
        events = self._scan(ohlcv, signal_open_time=c)
        assert len(events) == 1
        assert events[0].open_time == c

    def test_drops_last_bar_when_still_forming(self) -> None:
        """A genuinely-forming (future) final bar is dropped; fire on prior closed."""
        c = self._CLOSED_MS
        ohlcv = self._ohlcv([c - self._DAY_MS, c, self._FORMING_MS])
        events = self._scan(ohlcv, signal_open_time=c)
        assert len(events) == 1
        assert events[0].open_time == c


class TestStrategyParamsAlertTpR:
    """Per-strategy tp_r must reach the Telegram alert TP price, not just the backtest filter."""

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03

    def _make_ohlcv(self) -> pd.DataFrame:
        # Signal fires on second-to-last candle; last row is the forming candle.
        rows = [
            {
                "open_time": self._OPEN_TIME_MS - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 100.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS,
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 100.0,
                "volume": 1.0,
            },
            {
                "open_time": _FUTURE_FORMING_MS,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 0.5,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "engulfing_long@100.00",
                    "sl_price": 95.0,  # SL dist=5 → 2.0R TP=110, 3.0R TP=115
                    "context": "",
                }
            ]
        )

    def test_strategy_params_tp_r_used_in_alert(self, tmp_path: Any) -> None:
        """strategy_params tp_r=3.0 must produce 3.0R TP in the alert, not the 2.0R global."""
        from analytics.signal_config import StrategyOverride

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        signals = self._make_signals_df()

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "engulfing": {
                        "detector": lambda df: signals,
                        "confidence": 3,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "engulfing": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["1h"],
                strategies=["engulfing"],
                store=store,
                tp_r=2.0,
                sl_pct=0.02,
                strategy_params={"engulfing": StrategyOverride(tp_r=3.0)},
            )

        assert len(alerts) == 1
        # SL dist = 100 - 95 = 5; TP at 3.0R = 100 + 5*3 = 115; at 2.0R = 110
        assert "115" in alerts[0], (
            f"Expected 3.0R TP (115.0) in alert, got 2.0R. Alert: {alerts[0]}"
        )
        assert "3.0R" in alerts[0], f"Expected '3.0R' in alert, got: {alerts[0]}"


class TestStrategyTimeframes:
    """Tests for the strategy_timeframes param in scan_symbol.

    Verifies that strategies are skipped when the current TF is not in their
    allow-list, and still run when the TF is allowed or no restriction exists.
    """

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 — passes day_filter

    def _make_ohlcv(self) -> pd.DataFrame:
        rows = [
            {
                "open_time": self._OPEN_TIME_MS - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": _FUTURE_FORMING_MS,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "fvg_long@104.00",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )

    def _mock_registry(self, strategy: str) -> dict[str, Any]:
        signals_df = self._make_signals_df()
        return {
            strategy: {
                "detector": lambda df: signals_df,
                "confidence": 4,
            }
        }

    def _mock_spec_registry(self, strategy: str) -> dict[str, Any]:
        return {
            strategy: type(
                "S",
                (),
                {
                    "requires_funding": False,
                    "get_confidence": lambda self, tf: 3,
                },
            )()
        }

    def test_strategy_allowed_on_matching_tf(self) -> None:
        """strategy_timeframes allows the strategy when TF is in the list."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                self._mock_registry("fvg"),
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                self._mock_spec_registry("fvg"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                strategy_timeframes={"fvg": ["4h", "1d"]},
            )
        assert len(events) == 1

    def test_strategy_skipped_on_disallowed_tf(self) -> None:
        """strategy_timeframes skips the strategy when TF is not in the list."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                self._mock_registry("fvg"),
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                self._mock_spec_registry("fvg"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="15m",
                strategies=["fvg"],
                strategy_timeframes={"fvg": ["4h", "1d"]},
            )
        assert len(events) == 0

    def test_strategy_runs_on_all_tfs_when_not_listed(self) -> None:
        """A strategy not in strategy_timeframes runs on all timeframes."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                self._mock_registry("fvg"),
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                self._mock_spec_registry("fvg"),
            ),
        ):
            # fvg is not in strategy_timeframes → should run on any TF
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="15m",
                strategies=["fvg"],
                strategy_timeframes={
                    "trend_day": ["4h", "1d"]
                },  # only trend_day restricted
            )
        assert len(events) == 1

    def test_no_strategy_timeframes_runs_all(self) -> None:
        """When strategy_timeframes is None, no TF restrictions apply."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                self._mock_registry("fvg"),
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                self._mock_spec_registry("fvg"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="1m",
                strategies=["fvg"],
                strategy_timeframes=None,
            )
        assert len(events) == 1

    def test_trend_day_skipped_on_15m_via_toml_config(self) -> None:
        """trend_day restricted to 4h/1d via strategy_timeframes — 15m is skipped."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                self._mock_registry("trend_day"),
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                self._mock_spec_registry("trend_day"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="15m",
                strategies=["trend_day"],
                strategy_timeframes={"trend_day": ["4h", "1d"]},
            )
        assert len(events) == 0

    def test_trend_day_runs_on_4h_via_toml_config(self) -> None:
        """trend_day restricted to 4h/1d via strategy_timeframes — 4h is allowed."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                self._mock_registry("trend_day"),
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                self._mock_spec_registry("trend_day"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["trend_day"],
                strategy_timeframes={"trend_day": ["4h", "1d"]},
            )
        assert len(events) == 1


class TestConflictResolution:
    """Tests for the redesigned conflict suppression in run_scan_cycle.

    R10: When LONG + SHORT fire on same symbol/tf, pick the higher-confidence
    side. On a tie, send both — each with "⚠️ conflict" in reason.
    """

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 — passes day_filter

    def _make_ohlcv(self) -> pd.DataFrame:
        rows = [
            {
                "open_time": self._OPEN_TIME_MS - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": _FUTURE_FORMING_MS,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self, direction: str, reason_prefix: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": direction,
                    "reason": f"{reason_prefix}@104.00",
                    "sl_price": 98.0 if direction == "long" else 110.0,
                    "context": "",
                }
            ]
        )

    def test_higher_confidence_long_wins_over_lower_confidence_short(
        self, tmp_path: Any
    ) -> None:
        """LONG with confidence 4 wins over SHORT with confidence 2 — one alert, LONG."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")
        short_signals = self._make_signals_df("short", "bos_short")

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 4},
                    "bos": {"detector": lambda df: short_signals, "confidence": 2},
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 4,
                        },
                    )(),
                    "bos": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 2,
                        },
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg", "bos"],
                store=store,
            )

        assert len(alerts) == 1
        assert "LONG" in alerts[0]
        assert "SHORT" not in alerts[0]
        assert "⚠️ conflict" in alerts[0]

    def test_higher_confidence_short_wins_over_lower_confidence_long(
        self, tmp_path: Any
    ) -> None:
        """SHORT with confidence 5 wins over LONG with confidence 3 — one alert, SHORT."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")
        short_signals = self._make_signals_df("short", "bos_short")

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 3},
                    "bos": {
                        "detector": lambda df: short_signals,
                        "confidence": 5,
                    },
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                    "bos": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 5,
                        },
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg", "bos"],
                store=store,
            )

        assert len(alerts) == 1
        assert "SHORT" in alerts[0]
        assert "LONG" not in alerts[0]
        assert "⚠️ conflict" in alerts[0]

    def test_tied_confidence_sends_both_directions(self, tmp_path: Any) -> None:
        """When confidence is equal, both LONG and SHORT alerts are sent."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")
        short_signals = self._make_signals_df("short", "bos_short")

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 4},
                    "bos": {"detector": lambda df: short_signals, "confidence": 4},
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                    "bos": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg", "bos"],
                store=store,
            )

        assert len(alerts) == 2  # one per direction
        assert any("LONG 🟢" in a for a in alerts)
        assert any("SHORT 🔴" in a for a in alerts)
        for alert in alerts:
            assert "⚠️ conflict" in alert

    def test_conflict_tag_appears_in_alert(self, tmp_path: Any) -> None:
        """The conflict tag appears in the alert outside the reason backtick."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")
        short_signals = self._make_signals_df("short", "bos_short")

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 5},
                    "bos": {"detector": lambda df: short_signals, "confidence": 3},
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 5,
                        },
                    )(),
                    "bos": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg", "bos"],
                store=store,
            )

        assert len(alerts) == 1
        # Conflict tag appears outside the code-tagged reason field
        assert "fvg_long@104.00</code>" in alerts[0]
        assert "⚠️ conflict" in alerts[0]

    def test_no_conflict_no_tag(self, tmp_path: Any) -> None:
        """Signals without a conflict must NOT have ⚠️ conflict in the alert."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 4},
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        assert len(alerts) == 1
        assert "⚠️ conflict" not in alerts[0]


class TestSignalOutcomePersistence:
    """A4 P1 — verify run_scan_cycle writes a row to signal_alert_outcomes for each fired signal."""

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 00:00:00 UTC

    def _make_ohlcv(self) -> pd.DataFrame:
        t = self._OPEN_TIME_MS
        return pd.DataFrame(
            [
                {
                    "open_time": t - 2000,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 98.0,
                    "close": 102.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t - 1000,
                    "open": 102.0,
                    "high": 106.0,
                    "low": 100.0,
                    "close": 103.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t,
                    "open": 103.0,
                    "high": 107.0,
                    "low": 101.0,
                    "close": 104.0,
                    "volume": 1.0,
                },
                {
                    "open_time": _FUTURE_FORMING_MS,
                    "open": 104.0,
                    "high": 104.5,
                    "low": 103.5,
                    "close": 104.0,
                    "volume": 0.1,
                },
            ]
        )

    def _make_signals_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "fvg_long@100.00-102.00",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )

    def test_fired_signal_writes_outcome_row(self, tmp_path: Any) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = self._make_signals_df()

        with (
            patch(
                "analytics.signal.scanner.get_ohlcv", return_value=self._make_ohlcv()
            ),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        rows = conn.execute("SELECT * FROM signal_alert_outcomes").fetchall()
        assert len(rows) == 1

    def test_outcome_row_has_correct_fields(self, tmp_path: Any) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = self._make_signals_df()

        with (
            patch(
                "analytics.signal.scanner.get_ohlcv", return_value=self._make_ohlcv()
            ),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        row = conn.execute(
            "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
            "sl_price, tp_price, rr_ratio, confidence_at_fire, outcome "
            "FROM signal_alert_outcomes"
        ).fetchone()
        assert row is not None
        assert row[0] == f"BTCUSDT-4h-fvg-{self._OPEN_TIME_MS}-long"
        assert row[1] == "BTCUSDT"
        assert row[2] == "4h"
        assert row[3] == "fvg"
        assert row[4] == "long"
        assert row[5] == self._OPEN_TIME_MS
        # entry=104 (close of signal candle), sl=98, sl_dist=6, default tp_r=2.0
        # → tp_price = 104 + 6 × 2 = 116
        assert row[6] == 98.0
        assert row[7] == pytest.approx(116.0)
        assert row[8] == pytest.approx(2.0)
        assert row[9] == 3
        assert row[10] is None  # outcome not yet resolved

    def test_outcome_row_short_direction_tp_below_entry(self, tmp_path: Any) -> None:
        """Short signal: sl above entry, tp = entry − sl_dist × tp_r below entry."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "short",
                    "reason": "fvg_short@106.00-108.00",
                    "sl_price": 110.0,
                    "context": "",
                }
            ]
        )

        with (
            patch(
                "analytics.signal.scanner.get_ohlcv", return_value=self._make_ohlcv()
            ),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        row = conn.execute(
            "SELECT direction, entry_price, sl_price, tp_price, rr_ratio "
            "FROM signal_alert_outcomes"
        ).fetchone()
        assert row is not None
        # entry=104, sl=110, sl_dist=6, tp_r=2.0 → tp = 104 − 12 = 92
        assert row[0] == "short"
        assert row[1] == pytest.approx(104.0)
        assert row[2] == pytest.approx(110.0)
        assert row[3] == pytest.approx(92.0)
        assert row[4] == pytest.approx(2.0)

    def test_outcome_row_uses_structural_tp_when_present(self, tmp_path: Any) -> None:
        """When detector emits tp_price on the correct side, persist it verbatim."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "fvg_long@100.00-102.00",
                    "sl_price": 98.0,
                    "tp_price": 120.5,  # structural TP above entry
                    "context": "",
                }
            ]
        )

        with (
            patch(
                "analytics.signal.scanner.get_ohlcv", return_value=self._make_ohlcv()
            ),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        row = conn.execute(
            "SELECT tp_price, rr_ratio FROM signal_alert_outcomes"
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(120.5)  # structural TP wins
        assert row[1] == pytest.approx(2.0)  # rr_ratio = eff_alert_tp_r

    def test_outcome_row_uses_pct_fallback_when_no_structural_sl(
        self, tmp_path: Any
    ) -> None:
        """When sl_price=0 (no structural SL), the writer falls back to the same
        pct-based SL/TP the alert formatter renders, so the row is still scoreable
        (closes the outcome-ledger NULL hole)."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "fvg_long@100.00-102.00",
                    "sl_price": 0.0,  # no structural SL
                    "context": "",
                }
            ]
        )

        with (
            patch(
                "analytics.signal.scanner.get_ohlcv", return_value=self._make_ohlcv()
            ),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        row = conn.execute(
            "SELECT sl_price, tp_price, rr_ratio FROM signal_alert_outcomes"
        ).fetchone()
        assert row is not None
        # entry = 104.0 (signal candle close); default sl_pct=0.02, tp_r=2.0
        # → sl_dist = 104*0.02 = 2.08, sl = 101.92, tp = 104 + 2.08*2 = 108.16
        assert row[0] == pytest.approx(101.92)
        assert row[1] == pytest.approx(108.16)
        assert row[2] == pytest.approx(2.0)


class TestBacktestRunPersistence:
    """Verify run_scan_cycle writes to backtest_runs when backtest_cfg.save_results=True."""

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 00:00:00 UTC

    def _make_ohlcv(self) -> pd.DataFrame:
        t = self._OPEN_TIME_MS
        # Signal fires on t (last closed candle); t+1000 is the open/forming candle.
        return pd.DataFrame(
            [
                {
                    "open_time": t - 2000,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 98.0,
                    "close": 102.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t - 1000,
                    "open": 102.0,
                    "high": 106.0,
                    "low": 100.0,
                    "close": 103.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t,
                    "open": 103.0,
                    "high": 107.0,
                    "low": 101.0,
                    "close": 104.0,
                    "volume": 1.0,
                },
                {
                    "open_time": _FUTURE_FORMING_MS,
                    "open": 104.0,
                    "high": 104.5,
                    "low": 103.5,
                    "close": 104.0,
                    "volume": 0.1,
                },
            ]
        )

    def _make_signals_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )

    def _run(self, tmp_path: Any, save_results: bool) -> duckdb.DuckDBPyConnection:
        from analytics.signal_config import BacktestFilterConfig

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = self._make_signals_df()
        bt_cfg = BacktestFilterConfig(mode="soft", days=90, save_results=save_results)

        with (
            patch(
                "analytics.signal.scanner.get_ohlcv", return_value=self._make_ohlcv()
            ),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                backtest_cfg=bt_cfg,
            )
        return conn

    def test_writes_backtest_run_row_when_save_results_true(
        self, tmp_path: Any
    ) -> None:
        conn = self._run(tmp_path, save_results=True)
        count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()
        assert count is not None
        assert count[0] == 1

    def test_backtest_run_row_has_correct_strategy(self, tmp_path: Any) -> None:
        conn = self._run(tmp_path, save_results=True)
        row = conn.execute(
            "SELECT symbol, timeframe, strategy FROM backtest_runs"
        ).fetchone()
        assert row is not None
        assert row[0] == "BTCUSDT"
        assert row[1] == "4h"
        assert row[2] == "fvg"

    def test_no_row_written_when_save_results_false(self, tmp_path: Any) -> None:
        conn = self._run(tmp_path, save_results=False)
        count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()
        assert count is not None
        assert count[0] == 0

    def test_no_row_written_when_no_backtest_cfg(self, tmp_path: Any) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = self._make_signals_df()

        with (
            patch(
                "analytics.signal.scanner.get_ohlcv", return_value=self._make_ohlcv()
            ),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                backtest_cfg=None,  # no backtest cfg → no persistence
            )
        count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()
        assert count is not None
        assert count[0] == 0


class TestBacktestSummary:
    """Unit tests for _backtest_summary — direction-aware win rate and avg R."""

    def _make_result(
        self,
        strategy: str = "fvg",
        long_wins: int = 0,
        long_losses: int = 0,
        short_wins: int = 0,
        short_losses: int = 0,
    ) -> Any:
        from analytics.backtest_lib import BacktestResult, Trade

        trades: list[Trade] = []
        # Long win: entry=100 sl=98 exit=104 → risk=2 pnl_r=+2.0R
        for _ in range(long_wins):
            trades.append(
                Trade(
                    signal_time=0,
                    entry_time=1,
                    entry_price=100.0,
                    direction="long",
                    sl_price=98.0,
                    tp_price=104.0,
                    exit_time=2,
                    exit_price=104.0,
                    outcome="win",
                )
            )
        # Long loss: entry=100 sl=98 exit=98 → pnl_r=-1.0R
        for _ in range(long_losses):
            trades.append(
                Trade(
                    signal_time=0,
                    entry_time=1,
                    entry_price=100.0,
                    direction="long",
                    sl_price=98.0,
                    tp_price=104.0,
                    exit_time=2,
                    exit_price=98.0,
                    outcome="loss",
                )
            )
        # Short win: entry=100 sl=102 exit=96 → risk=2 pnl_r=+2.0R
        for _ in range(short_wins):
            trades.append(
                Trade(
                    signal_time=0,
                    entry_time=1,
                    entry_price=100.0,
                    direction="short",
                    sl_price=102.0,
                    tp_price=96.0,
                    exit_time=2,
                    exit_price=96.0,
                    outcome="win",
                )
            )
        # Short loss: entry=100 sl=102 exit=102 → pnl_r=-1.0R
        for _ in range(short_losses):
            trades.append(
                Trade(
                    signal_time=0,
                    entry_time=1,
                    entry_price=100.0,
                    direction="short",
                    sl_price=102.0,
                    tp_price=96.0,
                    exit_time=2,
                    exit_price=102.0,
                    outcome="loss",
                )
            )
        return BacktestResult(
            symbol="BTCUSDT", timeframe="1h", strategy=strategy, trades=trades
        )

    def _cfg(self, min_trades: int = 3) -> Any:
        from analytics.signal_config import BacktestFilterConfig

        return BacktestFilterConfig(mode="soft", days=90, min_trades=min_trades)

    def test_long_direction_shows_long_win_rate_and_avg_r(self) -> None:
        """Single strategy LONG alert: shows long win rate + avg R, not overall."""
        # 4 long wins (+2R each), 1 long loss (-1R) → long_win_rate=80%, long_avg_r=+1.4R
        # 2 short losses to confirm shorts are excluded from the stat
        result = self._make_result(long_wins=4, long_losses=1, short_losses=2)
        summary = _backtest_summary(
            {"fvg": result}, ["fvg"], self._cfg(), direction="long"
        )
        assert "[↑]" in summary
        assert "80%" in summary
        assert "+1.4R" in summary
        assert "5 longs" in summary
        assert "[↓]" not in summary

    def test_short_direction_shows_short_win_rate_and_avg_r(self) -> None:
        """Single strategy SHORT alert: shows short win rate + avg R, not overall."""
        # 3 short wins (+2R each), 1 short loss (-1R) → short_win_rate=75%, short_avg_r=+1.25R
        # 3 long losses to confirm longs are excluded
        result = self._make_result(short_wins=3, short_losses=1, long_losses=3)
        summary = _backtest_summary(
            {"fvg": result}, ["fvg"], self._cfg(), direction="short"
        )
        assert "[↓]" in summary
        assert "75%" in summary
        assert "+1.2R" in summary
        assert "4 shorts" in summary
        assert "[↑]" not in summary

    def test_directional_n_a_when_below_min_trades(self) -> None:
        """Shows n/a when directional trade count is below min_trades threshold."""
        # Only 1 long trade but min_trades=3 → n/a
        result = self._make_result(long_wins=1, short_wins=5)
        summary = _backtest_summary(
            {"fvg": result}, ["fvg"], self._cfg(min_trades=3), direction="long"
        )
        assert "n/a" in summary
        assert "1 long" in summary

    def test_no_direction_shows_overall_win_rate(self) -> None:
        """Backward compat: no direction arg → overall win rate, no arrow."""
        result = self._make_result(long_wins=3, long_losses=1)
        summary = _backtest_summary({"fvg": result}, ["fvg"], self._cfg())
        assert "[↑]" not in summary
        assert "[↓]" not in summary
        assert "75%" in summary
        assert "4 trades" in summary

    def test_long_arrow_in_header(self) -> None:
        """Arrow is [↑] for long, [↓] for short, absent when no direction."""
        result = self._make_result(long_wins=5)
        long_s = _backtest_summary(
            {"fvg": result}, ["fvg"], self._cfg(), direction="long"
        )
        short_s = _backtest_summary(
            {"fvg": result}, ["fvg"], self._cfg(), direction="short"
        )
        none_s = _backtest_summary({"fvg": result}, ["fvg"], self._cfg())
        assert "Backtest 90d [↑]:" in long_s
        assert "Backtest 90d [↓]:" in short_s
        assert "Backtest 90d:" in none_s

    def test_multi_strategy_confluence_shows_per_strategy_directional_stats(
        self,
    ) -> None:
        """Confluence alert: each strategy prefixed, direction-specific stats."""
        fvg = self._make_result("fvg", long_wins=4, long_losses=1)
        bos = self._make_result("bos", long_wins=3, long_losses=1)
        summary = _backtest_summary(
            {"fvg": fvg, "bos": bos}, ["fvg", "bos"], self._cfg(), direction="long"
        )
        assert "[↑]" in summary
        assert "fvg:" in summary
        assert "bos:" in summary
        assert "80%" in summary
        assert "75%" in summary

    def test_none_result_shows_n_a(self) -> None:
        """Strategy with no backtest result shows n/a."""
        summary = _backtest_summary(
            {"fvg": None}, ["fvg"], self._cfg(), direction="long"
        )
        assert "n/a" in summary
        assert "[↑]" in summary

    def test_hold_time_appended_single_strategy_long(self) -> None:
        """Single strategy: median long hold time appended as '· hold ~Xh'."""
        from analytics.backtest_lib import BacktestResult, Trade

        base = 1_700_000_000_000
        trades = [
            Trade(
                signal_time=base,
                entry_time=base,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=104.0,
                exit_time=base + 16 * 3_600_000,
                exit_price=104.0,
                outcome="win",
            )
            for _ in range(4)
        ]
        result = BacktestResult(
            symbol="BTCUSDT", timeframe="1h", strategy="fvg", trades=trades
        )
        summary = _backtest_summary(
            {"fvg": result}, ["fvg"], self._cfg(), direction="long"
        )
        assert "hold ~16h" in summary

    def test_hold_time_days_format_when_gte_48h(self) -> None:
        """Hold time ≥ 48h shown as '~Xd'."""
        from analytics.backtest_lib import BacktestResult, Trade

        base = 1_700_000_000_000
        trades = [
            Trade(
                signal_time=base,
                entry_time=base,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=104.0,
                exit_time=base + 72 * 3_600_000,
                exit_price=104.0,
                outcome="win",
            )
            for _ in range(4)
        ]
        result = BacktestResult(
            symbol="BTCUSDT", timeframe="1d", strategy="fvg", trades=trades
        )
        summary = _backtest_summary(
            {"fvg": result}, ["fvg"], self._cfg(), direction="long"
        )
        assert "hold ~3d" in summary

    def test_hold_time_shown_per_strategy_for_multi_strategy(self) -> None:
        """Multi-strategy confluence: hold time appended inline per strategy."""
        fvg = self._make_result("fvg", long_wins=4, long_losses=1)
        bos = self._make_result("bos", long_wins=3, long_losses=1)
        summary = _backtest_summary(
            {"fvg": fvg, "bos": bos}, ["fvg", "bos"], self._cfg(), direction="long"
        )
        # Each strategy entry should have an inline hold time (~0h because _make_result
        # uses entry_time=1, exit_time=2 → duration ≈ 0ms)
        assert "fvg:" in summary
        assert "bos:" in summary
        assert summary.count("~0h") == 2

    def test_hold_time_not_shown_when_below_min_trades(self) -> None:
        """No hold time when trade count < min_trades (n/a path)."""
        from analytics.backtest_lib import BacktestResult, Trade

        base = 1_700_000_000_000
        trades = [
            Trade(
                signal_time=base,
                entry_time=base,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=104.0,
                exit_time=base + 8 * 3_600_000,
                exit_price=104.0,
                outcome="win",
            )
        ]
        result = BacktestResult(
            symbol="BTCUSDT", timeframe="1h", strategy="fvg", trades=trades
        )
        # min_trades=3 but only 1 long → n/a path
        summary = _backtest_summary(
            {"fvg": result}, ["fvg"], self._cfg(min_trades=3), direction="long"
        )
        assert "n/a" in summary
        assert "hold" not in summary


class TestFmtHold:
    def test_hours_below_48(self) -> None:
        assert _fmt_hold(16.0) == "~16h"

    def test_hours_rounds_to_nearest(self) -> None:
        assert _fmt_hold(15.6) == "~16h"

    def test_zero_hours(self) -> None:
        assert _fmt_hold(0.0) == "~0h"

    def test_exactly_48h_shows_days(self) -> None:
        assert _fmt_hold(48.0) == "~2d"

    def test_72h_shows_3d(self) -> None:
        assert _fmt_hold(72.0) == "~3d"


# ---------------------------------------------------------------------------
# confidence_override in scan_symbol
# ---------------------------------------------------------------------------

_WEDNESDAY_MS = 1704240000000  # 2024-01-03 00:00:00 UTC


def _make_ohlcv_4rows(open_time_ms: int) -> pd.DataFrame:
    rows = [
        {
            "open_time": open_time_ms - 2000,
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 102.0,
            "volume": 1.0,
        },
        {
            "open_time": open_time_ms - 1000,
            "open": 102.0,
            "high": 106.0,
            "low": 100.0,
            "close": 103.0,
            "volume": 1.0,
        },
        {
            "open_time": open_time_ms,
            "open": 103.0,
            "high": 107.0,
            "low": 101.0,
            "close": 104.0,
            "volume": 1.0,
        },
        {
            "open_time": _FUTURE_FORMING_MS,
            "open": 104.0,
            "high": 104.5,
            "low": 103.5,
            "close": 104.2,
            "volume": 0.1,
        },
    ]
    return pd.DataFrame(rows)


def _make_signals_df_simple(open_time_ms: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "open_time": open_time_ms,
                "direction": "long",
                "reason": "fvg_long@100.00-102.00",
                "sl_price": 98.0,
                "context": "",
            }
        ]
    )


def _fake_registry_entry(get_confidence_val: int = 3) -> Any:
    return type(
        "S",
        (),
        {
            "requires_funding": False,
            "get_confidence": lambda self, tf: get_confidence_val,
        },
    )()


class TestConfidenceOverrideInScanSymbol:
    def test_override_replaces_registry_confidence(self) -> None:
        ohlcv = _make_ohlcv_4rows(_WEDNESDAY_MS)
        signals_df = _make_signals_df_simple(_WEDNESDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {"fvg": _fake_registry_entry(get_confidence_val=2)},
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                confidence_override={"fvg": {"4h": 5}},
            )

        assert len(events) == 1
        assert events[0].confidence == 5  # override wins over registry value of 2

    def test_falls_back_to_registry_when_override_missing_strategy(self) -> None:
        ohlcv = _make_ohlcv_4rows(_WEDNESDAY_MS)
        signals_df = _make_signals_df_simple(_WEDNESDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {"fvg": _fake_registry_entry(get_confidence_val=3)},
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                confidence_override={"bos": {"4h": 5}},  # fvg not in override
            )

        assert len(events) == 1
        assert events[0].confidence == 3  # falls back to registry

    def test_falls_back_to_registry_when_override_missing_tf(self) -> None:
        ohlcv = _make_ohlcv_4rows(_WEDNESDAY_MS)
        signals_df = _make_signals_df_simple(_WEDNESDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {"fvg": _fake_registry_entry(get_confidence_val=3)},
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                confidence_override={"fvg": {"1h": 5}},  # 4h not in override
            )

        assert len(events) == 1
        assert events[0].confidence == 3  # falls back to registry

    def test_none_override_uses_registry(self) -> None:
        ohlcv = _make_ohlcv_4rows(_WEDNESDAY_MS)
        signals_df = _make_signals_df_simple(_WEDNESDAY_MS)

        with (
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df}},
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {"fvg": _fake_registry_entry(get_confidence_val=4)},
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                confidence_override=None,
            )

        assert len(events) == 1
        assert events[0].confidence == 4


class TestBiasLayer:
    """Tests for the F8 bias layer in run_scan_cycle.

    Uses _compute_stats_context patched to return a controlled StatsContext,
    so we can verify ADR suppress and DOW soft suppress without real DB stats.
    """

    # Wednesday 2024-01-03 — passes day_filter=off
    _OPEN_TIME_MS = 1704240000000

    def _make_ohlcv(self) -> pd.DataFrame:
        rows = [
            {
                "open_time": self._OPEN_TIME_MS - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": _FUTURE_FORMING_MS,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self, direction: str = "long") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": direction,
                    "reason": f"fvg_{direction}@100.00-102.00",
                    "sl_price": 98.0 if direction == "long" else 106.0,
                    "context": "",
                }
            ]
        )

    def _make_stats_context(
        self,
        adr_consumed_pct: float | None = 0.50,
        avg_return_today: float = 0.01,
        adr_move_up: bool | None = None,
    ) -> Any:
        from signals.alert_formatter import StatsContext

        return StatsContext(
            today_dow="Wednesday",
            p1_low_pct_today=0.55,
            adr_14=0.025,
            adr_consumed_pct=adr_consumed_pct,
            peak_high_hour_myt=14,
            peak_low_hour_myt=8,
            bull_pct_today=0.60,
            avg_return_today=avg_return_today,
            adr_move_up=adr_move_up,
        )

    def _run_with_bias(
        self,
        tmp_path: Any,
        signals_df: Any,
        stats_ctx: Any,
        bias_cfg: Any,
    ) -> list[str]:
        from analytics.signal_lib import run_scan_cycle

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 3,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 3,
                        },
                    )(),
                },
            ),
            patch(
                "analytics.signal.scanner._compute_stats_context",
                return_value=stats_ctx,
            ),
        ):
            return run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                bias_cfg=bias_cfg,
            )

    # --- ADR hard suppress ---

    def test_adr_gate_suppresses_when_consumed_above_threshold(
        self, tmp_path: Any
    ) -> None:
        """ADR gate drops signal when adr_consumed_pct >= threshold."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(adr_consumed_pct=0.85)
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert alerts == [], "Signal should be suppressed when ADR >= threshold"

    def test_adr_gate_passes_when_consumed_below_threshold(self, tmp_path: Any) -> None:
        """ADR gate allows signal when adr_consumed_pct < threshold."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(adr_consumed_pct=0.60)
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1, "Signal should pass when ADR < threshold"

    def test_adr_gate_disabled_when_threshold_is_none(self, tmp_path: Any) -> None:
        """ADR gate is skipped when adr_suppress_threshold=None (default)."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(adr_consumed_pct=0.99)
        bias_cfg = BiasConfig(adr_suppress_threshold=None)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1, "ADR gate off when threshold is None"

    def test_adr_gate_passes_when_adr_consumed_is_none(self, tmp_path: Any) -> None:
        """ADR gate skips suppression when adr_consumed_pct is unknown."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(adr_consumed_pct=None)
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1, "ADR gate must not suppress when consumed pct unknown"

    def test_adr_gate_passes_when_stats_ctx_is_none(self, tmp_path: Any) -> None:
        """ADR gate skips suppression when stats context is unavailable."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, None, bias_cfg)
        assert len(alerts) == 1, "ADR gate must not suppress when stats ctx is None"

    def test_bias_cfg_none_is_a_noop(self, tmp_path: Any) -> None:
        """bias_cfg=None means the entire bias layer is skipped."""
        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(adr_consumed_pct=0.99)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, None)
        assert len(alerts) == 1, "No bias_cfg → signal must pass unaffected"

    # --- ADR directional suppress ---

    def test_adr_gate_suppresses_long_when_move_up(self, tmp_path: Any) -> None:
        """LONG suppressed when ADR consumed and today's move was upward."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(adr_consumed_pct=0.85, adr_move_up=True)
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert alerts == [], (
            "LONG should be suppressed when move was up and ADR consumed"
        )

    def test_adr_gate_keeps_short_when_move_up(self, tmp_path: Any) -> None:
        """SHORT (reversal) passes when ADR consumed but today's move was upward."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("short")
        ctx = self._make_stats_context(adr_consumed_pct=0.85, adr_move_up=True)
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1, "SHORT should pass — it's a reversal at the day high"

    def test_adr_gate_suppresses_short_when_move_down(self, tmp_path: Any) -> None:
        """SHORT suppressed when ADR consumed and today's move was downward."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("short")
        ctx = self._make_stats_context(adr_consumed_pct=0.85, adr_move_up=False)
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert alerts == [], (
            "SHORT should be suppressed when move was down and ADR consumed"
        )

    def test_adr_gate_keeps_long_when_move_down(self, tmp_path: Any) -> None:
        """LONG (reversal) passes when ADR consumed but today's move was downward."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(adr_consumed_pct=0.85, adr_move_up=False)
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1, "LONG should pass — it's a reversal at the day low"

    def test_adr_gate_blanket_when_direction_unknown(self, tmp_path: Any) -> None:
        """When adr_move_up=None, gate falls back to blanket suppress."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("short")
        ctx = self._make_stats_context(adr_consumed_pct=0.85, adr_move_up=None)
        bias_cfg = BiasConfig(adr_suppress_threshold=0.80)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert alerts == [], (
            "All signals suppressed when direction unknown (safe fallback)"
        )

    # --- DOW soft suppress ---

    def test_dow_suppress_reduces_confidence_for_opposing_long(
        self, tmp_path: Any
    ) -> None:
        """LONG on a bearish DOW day → confidence reduced by 1."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        # avg_return_today < 0 → bearish DOW → LONG is opposing → reduce confidence
        ctx = self._make_stats_context(avg_return_today=-0.02)
        bias_cfg = BiasConfig(dow_soft_suppress=True, dow_suppress_min_abs_return=0.005)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1, "Signal must still fire (soft suppress, not hard gate)"
        assert "★★☆☆☆" in alerts[0], "Confidence reduced from 3★ to 2★"

    def test_dow_suppress_reduces_confidence_for_opposing_short(
        self, tmp_path: Any
    ) -> None:
        """SHORT on a bullish DOW day → confidence reduced by 1."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("short")
        # avg_return_today > 0 → bullish DOW → SHORT is opposing → reduce confidence
        ctx = self._make_stats_context(avg_return_today=0.02)
        bias_cfg = BiasConfig(dow_soft_suppress=True, dow_suppress_min_abs_return=0.005)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1
        assert "★★☆☆☆" in alerts[0], "Confidence reduced from 3★ to 2★"

    def test_dow_suppress_does_not_affect_aligned_direction(
        self, tmp_path: Any
    ) -> None:
        """LONG on a bullish DOW day → confidence unchanged."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        # avg_return_today > 0 → bullish DOW → LONG is aligned → no change
        ctx = self._make_stats_context(avg_return_today=0.02)
        bias_cfg = BiasConfig(dow_soft_suppress=True, dow_suppress_min_abs_return=0.005)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1
        assert "★★★☆☆" in alerts[0], (
            "Confidence should remain 3★ when direction aligned"
        )

    def test_dow_suppress_respects_dead_band(self, tmp_path: Any) -> None:
        """No confidence change when abs(avg_return) < min_abs_return dead-band."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        # avg_return near zero → within dead-band → no confidence adjustment
        ctx = self._make_stats_context(avg_return_today=-0.002)
        bias_cfg = BiasConfig(dow_soft_suppress=True, dow_suppress_min_abs_return=0.005)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1
        assert "★★★☆☆" in alerts[0], "Confidence unchanged within dead-band"

    def test_dow_suppress_clamps_confidence_to_one(self, tmp_path: Any) -> None:
        """Confidence never drops below 1 even when already at 1."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(avg_return_today=-0.05)
        bias_cfg = BiasConfig(dow_soft_suppress=True, dow_suppress_min_abs_return=0.005)

        # Patch get_confidence to return 1 (already minimum)
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()

        with (
            patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 1,
                    }
                },
            ),
            patch(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {
                            "requires_funding": False,
                            "get_confidence": lambda self, tf: 1,
                        },
                    )(),
                },
            ),
            patch(
                "analytics.signal.scanner._compute_stats_context",
                return_value=ctx,
            ),
        ):
            from analytics.signal_lib import run_scan_cycle

            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                bias_cfg=bias_cfg,
            )

        assert len(alerts) == 1
        assert "★☆☆☆☆" in alerts[0], "Confidence clamped at 1★ minimum"

    def test_dow_suppress_disabled_by_default(self, tmp_path: Any) -> None:
        """dow_soft_suppress=False (default) → no confidence change."""
        from analytics.signal_config import BiasConfig

        signals_df = self._make_signals_df("long")
        ctx = self._make_stats_context(avg_return_today=-0.05)
        bias_cfg = BiasConfig(dow_soft_suppress=False)

        alerts = self._run_with_bias(tmp_path, signals_df, ctx, bias_cfg)
        assert len(alerts) == 1
        assert "★★★☆☆" in alerts[0], "Confidence unchanged when dow_soft_suppress=False"


# ---------------------------------------------------------------------------
# Gate 3: _resolve_tp_r with direction
# ---------------------------------------------------------------------------


class TestResolveTpRDirectional:
    """Unit tests for _resolve_tp_r direction parameter."""

    def test_returns_global_when_no_strategy_params(self) -> None:
        from analytics.signal_lib import _resolve_tp_r

        assert _resolve_tp_r(None, "bos", "BTCUSDT", "1h", 2.0, "long") == 2.0

    def test_returns_tp_r_long_when_set(self) -> None:
        from analytics.signal_config import StrategyOverride
        from analytics.signal_lib import _resolve_tp_r

        params = {"bos": StrategyOverride(tp_r=2.5, tp_r_long=1.8)}
        assert _resolve_tp_r(params, "bos", "BTCUSDT", "1h", 2.0, "long") == 1.8

    def test_returns_tp_r_short_when_set(self) -> None:
        from analytics.signal_config import StrategyOverride
        from analytics.signal_lib import _resolve_tp_r

        params = {"bos": StrategyOverride(tp_r=2.5, tp_r_short=3.0)}
        assert _resolve_tp_r(params, "bos", "BTCUSDT", "1h", 2.0, "short") == 3.0

    def test_tf_specific_beats_directional(self) -> None:
        from analytics.signal_config import StrategyOverride
        from analytics.signal_lib import _resolve_tp_r

        params = {
            "bos": StrategyOverride(tp_r=2.5, tp_r_long=1.8, tp_r_per_tf={"1h": 4.0})
        }
        # TF-specific wins over directional
        assert _resolve_tp_r(params, "bos", "BTCUSDT", "1h", 2.0, "long") == 4.0
        # No TF override → directional applies
        assert _resolve_tp_r(params, "bos", "BTCUSDT", "4h", 2.0, "long") == 1.8

    def test_no_direction_falls_back_to_strategy_wide(self) -> None:
        from analytics.signal_config import StrategyOverride
        from analytics.signal_lib import _resolve_tp_r

        params = {"bos": StrategyOverride(tp_r=2.5, tp_r_long=1.8, tp_r_short=3.0)}
        # direction="" → skip directional, return strategy-wide tp_r
        assert _resolve_tp_r(params, "bos", "BTCUSDT", "1h", 2.0, "") == 2.5

    def test_direction_not_set_returns_strategy_wide_tp_r(self) -> None:
        from analytics.signal_config import StrategyOverride
        from analytics.signal_lib import _resolve_tp_r

        # tp_r_long not set, direction=long → fall through to strategy-wide
        params = {"bos": StrategyOverride(tp_r=2.5)}
        assert _resolve_tp_r(params, "bos", "BTCUSDT", "1h", 2.0, "long") == 2.5
