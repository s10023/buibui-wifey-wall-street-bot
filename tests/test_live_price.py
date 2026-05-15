"""Tests for monitor/live_price.py."""

import contextlib
from typing import Any
from unittest.mock import MagicMock, patch

from monitor.live_price import (
    _build_table,
    _handle_ws_msg,
    _kline_refresh_loop,
    _refresh_klines,
    _ws_watchdog,
    run,
)

from utils.live_store import LiveDataStore


def _miniticker_msg(symbol: str, last: str, open_24h: str) -> dict[str, Any]:
    return {
        "stream": f"{symbol.lower()}@miniTicker",
        "data": {
            "e": "24hrMiniTicker",
            "s": symbol,
            "c": last,
            "o": open_24h,
        },
    }


class TestHandleWsMsg:
    def test_valid_miniticker_updates_store(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg(_miniticker_msg("BTCUSDT", "67000.00", "65000.00"), store)
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is not None
        assert result.data["BTCUSDT"].ticker.last_price == 67000.0
        assert result.ws_connected is True

    def test_valid_msg_sets_ws_connected(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg(_miniticker_msg("BTCUSDT", "100.00", "90.00"), store)
        assert store.snapshot(["BTCUSDT"]).ws_connected is True

    def test_error_msg_sets_disconnected(self) -> None:
        store = LiveDataStore()
        store.set_ws_status(connected=True)
        _handle_ws_msg({"e": "error", "m": "stream error"}, store)
        assert store.snapshot(["BTCUSDT"]).ws_connected is False

    def test_error_msg_does_not_write_ticker(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg({"e": "error", "m": "stream error"}, store)
        assert store.snapshot(["BTCUSDT"]).data["BTCUSDT"].ticker is None

    def test_unknown_event_type_is_ignored(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg({"data": {"e": "trade", "s": "BTCUSDT"}}, store)
        assert store.snapshot(["BTCUSDT"]).data["BTCUSDT"].ticker is None

    def test_malformed_message_does_not_raise(self) -> None:
        store = LiveDataStore()
        # Missing 'c' and 'o' keys
        bad_msg = {"data": {"e": "24hrMiniTicker", "s": "BTCUSDT"}}
        _handle_ws_msg(bad_msg, store)  # must not raise
        assert store.snapshot(["BTCUSDT"]).data["BTCUSDT"].ticker is None


class TestRefreshKlines:
    def test_writes_klines_to_store(self) -> None:
        store = LiveDataStore()
        mock_client = MagicMock()
        with (
            patch("monitor.live_price.batch_get_klines") as mock_klines,
            patch("monitor.live_price.batch_get_asia_open") as mock_asia,
        ):
            mock_klines.return_value = {
                ("BTCUSDT", "15m"): [None, "66000.00"],
                ("BTCUSDT", "1h"): [None, "64000.00"],
                ("BTCUSDT", "4h"): [None, "62000.00"],
            }
            mock_asia.return_value = {"BTCUSDT": 63000.0}
            _refresh_klines(mock_client, ["BTCUSDT"], store)

        result = store.snapshot(["BTCUSDT"])
        klines = result.data["BTCUSDT"].klines
        assert klines is not None
        assert klines.open_15m == 66000.0
        assert klines.open_1h == 64000.0
        assert klines.open_4h == 62000.0
        assert klines.asia_open == 63000.0

    def test_missing_kline_result_stores_none(self) -> None:
        store = LiveDataStore()
        mock_client = MagicMock()
        with (
            patch("monitor.live_price.batch_get_klines") as mock_klines,
            patch("monitor.live_price.batch_get_asia_open") as mock_asia,
        ):
            mock_klines.return_value = {}
            mock_asia.return_value = {}
            _refresh_klines(mock_client, ["BTCUSDT"], store)

        result = store.snapshot(["BTCUSDT"])
        klines = result.data["BTCUSDT"].klines
        assert klines is not None
        assert klines.open_15m is None
        assert klines.open_1h is None
        assert klines.open_4h is None
        assert klines.asia_open is None


class TestBuildTable:
    def test_no_ticker_shows_dots_in_price_column(self) -> None:
        store = LiveDataStore()
        table = _build_table(["BTCUSDT"], store)
        assert table.row_count == 1

    def test_ticker_no_klines_shows_row(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=67000.0, open_24h=65000.0)
        table = _build_table(["BTCUSDT"], store)
        assert table.row_count == 1

    def test_title_shows_stale_when_disconnected(self) -> None:
        store = LiveDataStore()
        table = _build_table(["BTCUSDT"], store)
        assert table.title is not None
        assert "reconnecting" in str(table.title).lower()

    def test_title_shows_last_update_when_connected(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=67000.0, open_24h=65000.0)
        store.set_ws_status(connected=True)
        table = _build_table(["BTCUSDT"], store)
        assert table.title is not None
        assert "last update" in str(table.title).lower()
        assert "reconnecting" not in str(table.title).lower()

    def test_sort_col_applied(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=100.0, open_24h=90.0)
        store.update_ticker("ETHUSDT", last=100.0, open_24h=95.0)
        store.update_klines(
            "BTCUSDT", open_15m=98.0, open_1h=95.0, open_4h=93.0, asia_open=90.0
        )
        store.update_klines(
            "ETHUSDT", open_15m=99.0, open_1h=97.0, open_4h=95.0, asia_open=92.0
        )
        table = _build_table(
            ["BTCUSDT", "ETHUSDT"], store, sort_col="change_24h", sort_order=True
        )
        assert table.row_count == 2

    def test_multiple_symbols(self) -> None:
        store = LiveDataStore()
        table = _build_table(["BTCUSDT", "ETHUSDT", "SOLUSDT"], store)
        assert table.row_count == 3


class TestWsWatchdog:
    def test_no_abort_while_messages_arrive(self) -> None:
        """Watchdog must not abort when last_update advances before timeout."""
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=100.0, open_24h=90.0)
        call_count = 0

        def fake_sleep(n: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise StopIteration

        with (
            patch("monitor.live_price.time.sleep", side_effect=fake_sleep),
            patch("monitor.live_price.sys.exit") as mock_exit,
            patch("monitor.live_price.time.monotonic", return_value=0.0),
            contextlib.suppress(StopIteration),
        ):
            _ws_watchdog(store, timeout=120, poll=10)

        mock_exit.assert_not_called()

    def test_aborts_after_silence_exceeds_timeout(self) -> None:
        """Watchdog must call sys.exit(1) when no message arrives within timeout."""
        store = LiveDataStore()
        # store has no last_update — simulates complete WS silence
        monotonic_values = [0.0, 10.0, 200.0]  # baseline=0, elapsed=200 on 3rd poll
        monotonic_iter = iter(monotonic_values)

        def fake_monotonic() -> float:
            return next(monotonic_iter)

        sleep_calls = 0

        def fake_sleep(n: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1

        with (
            patch("monitor.live_price.time.sleep", side_effect=fake_sleep),
            patch("monitor.live_price.sys.exit") as mock_exit,
            patch("monitor.live_price.time.monotonic", side_effect=fake_monotonic),
        ):
            # Run one iteration manually by stopping after exit is called
            mock_exit.side_effect = SystemExit(1)
            with contextlib.suppress(SystemExit):
                _ws_watchdog(store, timeout=120, poll=10)

        mock_exit.assert_called_once_with(1)

    def test_baseline_resets_when_message_arrives(self) -> None:
        """Watchdog resets its baseline whenever last_update is present in store."""
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=100.0, open_24h=90.0)
        # Elapsed keeps returning a large number, but last_update resets baseline each loop
        call_count = 0

        def fake_monotonic() -> float:
            return 0.0  # elapsed is always 0 after baseline reset

        def fake_sleep(n: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise StopIteration

        with (
            patch("monitor.live_price.time.sleep", side_effect=fake_sleep),
            patch("monitor.live_price.sys.exit") as mock_exit,
            patch("monitor.live_price.time.monotonic", side_effect=fake_monotonic),
            contextlib.suppress(StopIteration),
        ):
            _ws_watchdog(store, timeout=120, poll=10)

        mock_exit.assert_not_called()


class TestKlineRefreshLoop:
    def test_exception_in_refresh_is_swallowed_and_loop_continues(self) -> None:
        """Daemon thread must not die on a transient error."""
        store = LiveDataStore()
        client = MagicMock()
        call_count = 0

        def fake_refresh(c: Any, syms: list[str], s: LiveDataStore) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("network error")

        with (
            patch("monitor.live_price._refresh_klines", side_effect=fake_refresh),
            patch("monitor.live_price.time.sleep") as mock_sleep,
        ):
            # Make sleep raise StopIteration on third call to exit the while-loop
            mock_sleep.side_effect = [None, None, StopIteration]
            with contextlib.suppress(StopIteration):
                _kline_refresh_loop(client, ["BTCUSDT"], store, interval=60)

        assert call_count == 2  # ran twice despite first raising


class TestRun:
    def test_run_wires_components_and_exits_on_keyboard_interrupt(self) -> None:
        """run() must start TWM, subscribe streams, start daemon thread, and stop TWM on exit."""
        client = MagicMock()

        mock_twm = MagicMock()
        mock_live_ctx = MagicMock()
        mock_live_ctx.__enter__ = MagicMock(return_value=mock_live_ctx)
        mock_live_ctx.__exit__ = MagicMock(return_value=False)
        mock_live_ctx.update.side_effect = KeyboardInterrupt

        with (
            patch("monitor.live_price.ThreadedWebsocketManager", return_value=mock_twm),
            patch("monitor.live_price._refresh_klines"),
            patch("monitor.live_price.threading.Thread") as mock_thread,
            patch("utils.live_loop.Live", return_value=mock_live_ctx),
            patch("utils.live_loop.Console"),
        ):
            mock_thread.return_value = MagicMock()
            run(client, ["BTCUSDT", "ETHUSDT"], sort_col="change_24h", sort_order=True)

        mock_twm.start.assert_called_once()
        mock_twm.start_multiplex_socket.assert_called_once()
        mock_twm.stop.assert_called_once()
        # Two daemon threads are started: kline refresh + WS watchdog
        assert mock_thread.return_value.start.call_count == 2

    def test_run_stops_twm_even_if_multiplex_raises(self) -> None:
        """twm.stop() must be called even when setup fails."""
        client = MagicMock()
        mock_twm = MagicMock()
        mock_twm.start_multiplex_socket.side_effect = RuntimeError("ws error")

        with (
            patch("monitor.live_price.ThreadedWebsocketManager", return_value=mock_twm),
            patch("monitor.live_price._refresh_klines"),
            contextlib.suppress(RuntimeError),
        ):
            run(client, ["BTCUSDT"])

        mock_twm.stop.assert_called_once()
