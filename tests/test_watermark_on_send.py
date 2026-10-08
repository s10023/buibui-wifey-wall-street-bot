"""Tests for watermark-on-send.

The primary candle watermark must be stamped only when the primary alert is
actually dispatched live — never on a silent / non-sending run. Otherwise a
dry run (no ``--telegram``, or an unconfigured primary channel) "consumes" the
candle in ``signal_state.json`` and the real alert is deduped away on the next
run. See ``project_signal_watch_followups.md`` (the footgun that ate an ADBE
1d eqh_eql alert mid-session on 2026-06-05). Mirrors the wife channel, which
already marks only on a successful ``dispatch_to_channel``.
"""

from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd

from analytics.signal.scanner import run_scan_cycle
from analytics.signal.types import SignalEvent
from analytics.store import init_schema
from signals.cooldown_store import CooldownStore

_TS = 1704067200000  # Monday 2024-01-01 00:00:00 UTC


def _event() -> SignalEvent:
    return SignalEvent(
        symbol="AAPL",
        timeframe="4h",
        strategy="bos",
        direction="long",
        reason="x",
        open_time=_TS,
        price=100.0,
        sl_price=98.0,
        tp_price=0.0,
    )


def _ohlcv_df(open_time_ms: int) -> pd.DataFrame:
    """Minimal 3-row OHLCV frame; second-to-last row is the latest closed candle."""
    rows = [
        {
            "open_time": open_time_ms - 1000,
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 102.0,
            "volume": 1.0,
        },
        {
            "open_time": open_time_ms,
            "open": 102.0,
            "high": 106.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0,
        },
        {
            "open_time": open_time_ms + 1000,
            "open": 100.0,
            "high": 100.5,
            "low": 99.5,
            "close": 100.2,
            "volume": 0.1,
        },
    ]
    return pd.DataFrame(rows)


def _drive(
    *, send_telegram: bool, dispatch_return: bool, tmp_path: Any
) -> tuple[CooldownStore, SignalEvent]:
    """Drive one scan cycle with scan_symbol stubbed to emit a single event.

    ``dispatch_to_channel`` is patched to return ``dispatch_return`` so the test
    controls whether the primary send is treated as a live dispatch.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    store = CooldownStore(str(tmp_path / "state.json"))
    event = _event()
    df = _ohlcv_df(event.open_time)

    with (
        patch("analytics.signal.scanner.get_ohlcv", return_value=df),
        patch("analytics.signal.scanner.scan_symbol", return_value=[event]),
        patch("utils.telegram.send_telegram_message"),
        patch(
            "utils.telegram_router.dispatch_to_channel",
            side_effect=lambda text, channel: dispatch_return,
        ),
    ):
        run_scan_cycle(
            conn=conn,
            symbols=[event.symbol],
            timeframes=[event.timeframe],
            strategies=[event.strategy],
            store=store,
            send_telegram=send_telegram,
        )
    return store, event


def _candle_consumed(store: CooldownStore, event: SignalEvent) -> bool:
    """True iff the primary watermark was stamped (candle no longer 'new')."""
    return not store.is_new_candle(
        event.symbol, event.timeframe, event.strategy, event.open_time
    )


class TestWatermarkOnSend:
    def test_non_sending_run_does_not_consume_candle(self, tmp_path: Any) -> None:
        """send_telegram=False must not stamp the primary watermark."""
        store, event = _drive(
            send_telegram=False, dispatch_return=False, tmp_path=tmp_path
        )
        assert not _candle_consumed(store, event)

    def test_undispatched_send_does_not_consume_candle(self, tmp_path: Any) -> None:
        """send_telegram=True but no live dispatch (e.g. unconfigured primary)
        must NOT stamp the watermark."""
        store, event = _drive(
            send_telegram=True, dispatch_return=False, tmp_path=tmp_path
        )
        assert not _candle_consumed(store, event)

    def test_dispatched_run_consumes_candle(self, tmp_path: Any) -> None:
        """A successful live primary dispatch stamps the watermark so the next
        run dedups the duplicate."""
        store, event = _drive(
            send_telegram=True, dispatch_return=True, tmp_path=tmp_path
        )
        assert _candle_consumed(store, event)
