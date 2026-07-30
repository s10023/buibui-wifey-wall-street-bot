"""Tests for opt-in missed-day catch-up (record-not-dispatch semantics).

The scanner normally fires on only the single latest *closed* candle, so a
skipped run-day permanently loses that day's signals (no backlog replay). With
``catch_up=True``:

- ``scan_symbol`` emits a SignalEvent for *every* closed candle in the window
  (each carrying its own entry price), not just the latest.
- ``run_scan_cycle`` replays each missed candle independently — conflict
  resolution and confluence stacking stay per-candle correct.
- The newest CLOSED candle is derived from OHLCV, not from the events, so a
  bar that produced no signal cannot promote an older candle to "live".
- Backfilled candles are RECORDED (DB signals + outcome ledger + watermark)
  but never dispatched to Telegram — only the newest closed candle may alert.
  A stale signal is untradeable noise in the chat but real ledger evidence.
- A cold-start guard restricts a fresh-watermark key to the latest candle
  only, so the whole window is not replayed as a burst.

Default (``catch_up=False``) keeps the latest-candle-only behaviour byte-for-byte.
Record-not-dispatch ported from parent #504 (itself a port of wifey #68/#69);
closes ``project_signal_watch_followups.md`` item 4 (stale-alert flood).
"""

from typing import Any
from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd

from analytics.signal.scanner import run_scan_cycle, scan_symbol
from analytics.signal.types import SignalEvent
from analytics.store import get_signals_history, init_schema
from signals.cooldown_store import CooldownStore

_DAY_MS = 86_400_000
_C0 = 1704067200000  # 2024-01-01 UTC — long past, always closed
_C1 = _C0 + _DAY_MS
_C2 = _C0 + 2 * _DAY_MS
_C3 = _C0 + 3 * _DAY_MS
_FORMING_MS = 7258118400000  # ~year 2200 — always still forming


# --------------------------------------------------------------------------- #
# scan_symbol — multi-candle emission                                         #
# --------------------------------------------------------------------------- #


def _ohlcv(rows: list[tuple[int, float]]) -> pd.DataFrame:
    """Build an OHLCV frame from (open_time, close) pairs."""
    return pd.DataFrame(
        [
            {
                "open_time": ot,
                "open": close,
                "high": close + 5.0,
                "low": close - 5.0,
                "close": close,
                "volume": 1.0,
            }
            for ot, close in rows
        ]
    )


def _scan(
    ohlcv: pd.DataFrame, signal_open_times: list[int], *, catch_up: bool
) -> list[SignalEvent]:
    signals_df = pd.DataFrame(
        [
            {
                "open_time": ot,
                "direction": "long",
                "reason": f"fvg_long@{ot}",
                "sl_price": 95.0,
                "context": "",
            }
            for ot in signal_open_times
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
            ohlcv_df=ohlcv,
            symbol="AAPL",
            timeframe="1d",
            strategies=["fvg"],
            catch_up=catch_up,
        )


class TestScanSymbolCatchUp:
    def test_off_fires_latest_closed_candle_only(self) -> None:
        ohlcv = _ohlcv([(_C0, 100.0), (_C1, 101.0), (_C2, 102.0)])
        events = _scan(ohlcv, [_C0, _C1, _C2], catch_up=False)
        assert [e.open_time for e in events] == [_C2]
        assert events[0].price == 102.0

    def test_on_fires_all_closed_candles(self) -> None:
        ohlcv = _ohlcv([(_C0, 100.0), (_C1, 101.0), (_C2, 102.0)])
        events = _scan(ohlcv, [_C0, _C1, _C2], catch_up=True)
        assert [e.open_time for e in events] == [_C0, _C1, _C2]

    def test_on_uses_each_candles_own_close_as_price(self) -> None:
        ohlcv = _ohlcv([(_C0, 100.0), (_C1, 101.0), (_C2, 102.0)])
        events = _scan(ohlcv, [_C0, _C1, _C2], catch_up=True)
        assert {e.open_time: e.price for e in events} == {
            _C0: 100.0,
            _C1: 101.0,
            _C2: 102.0,
        }

    def test_on_excludes_still_forming_final_bar(self) -> None:
        ohlcv = _ohlcv([(_C1, 101.0), (_C2, 102.0), (_FORMING_MS, 103.0)])
        # Detector "returns" a signal on the forming bar too — it must be dropped.
        events = _scan(ohlcv, [_C1, _C2, _FORMING_MS], catch_up=True)
        assert [e.open_time for e in events] == [_C1, _C2]


# --------------------------------------------------------------------------- #
# run_scan_cycle — cold start, backfill record-not-dispatch, per-candle        #
# --------------------------------------------------------------------------- #


def _cycle_ohlcv() -> pd.DataFrame:
    """OHLCV containing every candle an event may reference, plus a forming bar."""
    return _ohlcv(
        [(_C0, 100.0), (_C1, 101.0), (_C2, 102.0), (_C3, 103.0), (_FORMING_MS, 104.0)]
    )


def _event(open_time: int, direction: str = "long", confidence: int = 3) -> SignalEvent:
    return SignalEvent(
        symbol="AAPL",
        timeframe="1d",
        strategy="bos" if direction == "long" else "fvg",
        direction=direction,
        reason="x",
        open_time=open_time,
        price=100.0,
        sl_price=95.0 if direction == "long" else 105.0,
        tp_price=0.0,
        confidence=confidence,
    )


def _drive_cycle(
    events: list[SignalEvent],
    *,
    catch_up: bool,
    store: CooldownStore,
    ohlcv: pd.DataFrame | None = None,
) -> tuple[list[str], MagicMock, duckdb.DuckDBPyConnection]:
    """Run one cycle; returns (alerts, dispatch mock, DB conn) for inspection."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    df = ohlcv if ohlcv is not None else _cycle_ohlcv()
    dispatch = MagicMock(side_effect=lambda text, channel: True)
    with (
        patch("analytics.signal.scanner.get_ohlcv", return_value=df),
        patch("analytics.signal.scanner.scan_symbol", return_value=events),
        patch("utils.telegram_router.dispatch_to_channel", dispatch),
    ):
        alerts = run_scan_cycle(
            conn=conn,
            symbols=["AAPL"],
            timeframes=["1d"],
            strategies=["bos", "fvg"],
            store=store,
            send_telegram=True,
            catch_up=catch_up,
        )
    return alerts, dispatch, conn


def _consumed(store: CooldownStore, symbol: str, tf: str, strat: str, ot: int) -> bool:
    return not store.is_new_candle(symbol, tf, strat, ot)


def _primary_sends(dispatch: MagicMock) -> int:
    return sum(1 for c in dispatch.call_args_list if c.args[1] == "primary")


def _recorded(conn: duckdb.DuckDBPyConnection) -> list[int]:
    """open_times persisted to the signals table this cycle, ascending."""
    rows = get_signals_history(conn, "AAPL", "1d", 0, _FORMING_MS)
    return sorted(int(t) for t in rows["open_time"].tolist())


class TestRunScanCycleCatchUp:
    def test_off_default_unchanged(self, tmp_path: Any) -> None:
        """catch_up=False: only what scan_symbol returned (latest candle) fires."""
        store = CooldownStore(str(tmp_path / "s.json"))
        alerts, dispatch, _conn = _drive_cycle(
            [_event(_C2)], catch_up=False, store=store
        )
        assert len(alerts) == 1
        assert _primary_sends(dispatch) == 1
        assert _consumed(store, "AAPL", "1d", "bos", _C2)

    def test_cold_start_fires_latest_candle_only(self, tmp_path: Any) -> None:
        """No prior watermark: replay must NOT burst the whole window — only the
        latest candle is recorded, alerted and watermarked."""
        store = CooldownStore(str(tmp_path / "s.json"))
        alerts, dispatch, conn = _drive_cycle(
            [_event(_C1), _event(_C2), _event(_C3)], catch_up=True, store=store
        )
        assert len(alerts) == 1
        assert _primary_sends(dispatch) == 1
        assert _recorded(conn) == [_C3]
        assert _consumed(store, "AAPL", "1d", "bos", _C3)

    def test_replays_missed_candles_after_watermark(self, tmp_path: Any) -> None:
        """With a seeded watermark at C1, a later run recovers C2 and C3 into the
        ledger and re-suppresses C1 (already alerted). Only C3 — the newest
        closed candle — may alert; C2 is backfill, recorded silently."""
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", _C1)
        alerts, dispatch, conn = _drive_cycle(
            [_event(_C1), _event(_C2), _event(_C3)], catch_up=True, store=store
        )
        assert _recorded(conn) == [_C2, _C3]
        assert len(alerts) == 1
        assert _primary_sends(dispatch) == 1
        assert _consumed(store, "AAPL", "1d", "bos", _C2)
        assert _consumed(store, "AAPL", "1d", "bos", _C3)

    def test_backfilled_candle_is_recorded_but_not_alerted(self, tmp_path: Any) -> None:
        """The outcome ledger gains the backfilled row even though Telegram never
        fires for it — that asymmetry is the whole point of catch-up."""
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", _C1)
        _alerts, dispatch, conn = _drive_cycle(
            [_event(_C2), _event(_C3)], catch_up=True, store=store
        )
        outcome_ts = {
            int(r[0])
            for r in conn.execute(
                "SELECT candle_ts_ms FROM signal_alert_outcomes"
            ).fetchall()
        }
        assert {_C2, _C3} <= outcome_ts
        assert _primary_sends(dispatch) == 1, (
            "backfilled candles must not fire Telegram; only the latest "
            "closed candle may alert"
        )

    def test_latest_closed_comes_from_ohlcv_not_events(self, tmp_path: Any) -> None:
        """A newest bar that produced NO signal must not promote an older candle
        to "live". Here C3 is the newest closed candle but only C1/C2 carry
        events — both are backfill: recorded, watermarked, zero alerts."""
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", _C0)
        alerts, dispatch, conn = _drive_cycle(
            [_event(_C1), _event(_C2)], catch_up=True, store=store
        )
        assert alerts == []
        assert _primary_sends(dispatch) == 0
        assert _recorded(conn) == [_C1, _C2]
        assert _consumed(store, "AAPL", "1d", "bos", _C2)

    def test_latest_closed_when_final_bar_already_closed(self, tmp_path: Any) -> None:
        """yfinance adaptation: scanned pre-market/after-close the final OHLCV row
        is already closed — the latest closed candle is then iloc[-1], not the
        parent's unconditional iloc[-2]. C3 must alert as live here."""
        ohlcv = _ohlcv([(_C0, 100.0), (_C1, 101.0), (_C2, 102.0), (_C3, 103.0)])
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", _C1)
        alerts, dispatch, conn = _drive_cycle(
            [_event(_C2), _event(_C3)], catch_up=True, store=store, ohlcv=ohlcv
        )
        assert len(alerts) == 1
        assert _primary_sends(dispatch) == 1
        assert _recorded(conn) == [_C2, _C3]

    def test_conflict_resolution_is_per_candle(self, tmp_path: Any) -> None:
        """A long on one candle and an opposing short on a *different* candle must
        not be resolved against each other. Pooled resolution would let the
        confidence-5 long kill the confidence-1 short; per-candle, both survive —
        C2 records silently (backfill), C3 (the latest) alerts."""
        store = CooldownStore(str(tmp_path / "s.json"))
        # Seed both strategies so the cold-start guard does not restrict to latest.
        store.mark_candle("AAPL", "1d", "bos", _C0)
        store.mark_candle("AAPL", "1d", "fvg", _C0)
        events = [
            _event(_C2, direction="long", confidence=5),
            _event(_C3, direction="short", confidence=1),
        ]
        alerts, dispatch, conn = _drive_cycle(events, catch_up=True, store=store)
        assert len(alerts) == 1
        assert _primary_sends(dispatch) == 1
        assert _recorded(conn) == [_C2, _C3]
        assert _consumed(store, "AAPL", "1d", "bos", _C2)
        assert _consumed(store, "AAPL", "1d", "fvg", _C3)


class TestCooldownStoreLastMarked:
    def test_returns_none_when_absent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "s.json"))
        assert store.last_marked("AAPL", "1d", "bos") is None

    def test_returns_value_after_mark(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", _C2)
        assert store.last_marked("AAPL", "1d", "bos") == _C2

    def test_channel_isolated(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", _C2, channel="wife")
        assert store.last_marked("AAPL", "1d", "bos") is None
        assert store.last_marked("AAPL", "1d", "bos", channel="wife") == _C2
