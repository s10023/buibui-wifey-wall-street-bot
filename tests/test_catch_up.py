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

import time
from typing import Any
from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd

from analytics.signal.scanner import (
    may_dispatch_candle,
    run_scan_cycle,
    scan_symbol,
)
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
    max_alert_age_hours: float = 0.0,
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
            max_alert_age_hours=max_alert_age_hours,
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


# --------------------------------------------------------------------------- #
# max_alert_age_hours — the dispatch recency window (2026-08-25)              #
# --------------------------------------------------------------------------- #
#
# Under one pre-open run a day the session's FIRST 4h bar can never BE the
# newest closed candle, so at a window of 0.0 it was structurally undeliverable
# — 120 of 351 ledger candles, 34%, every one the 13:30 UTC bar. These candles
# are anchored to the REAL clock (unlike _C0.._C3 above, which sit in 2024 and
# are therefore ancient under any window — that is what keeps the tests above
# pinning the exclusion side for free).


def _aged_open(hours_since_close: float) -> int:
    """open_time of a 1d candle whose CLOSE is `hours_since_close` hours ago."""
    return int(time.time() * 1000) - int(hours_since_close * 3_600_000) - _DAY_MS


def _aged_frame() -> tuple[pd.DataFrame, int, int, int]:
    """Three closed 1d candles at ~49h / ~25h / ~1h since close, oldest first."""
    old, mid, latest = _aged_open(49), _aged_open(25), _aged_open(1)
    return _ohlcv([(old, 100.0), (mid, 101.0), (latest, 102.0)]), old, mid, latest


class TestDispatchRecencyWindow:
    """Pure-function rules for may_dispatch_candle."""

    def test_newest_closed_always_dispatches_even_at_zero(self) -> None:
        assert may_dispatch_candle(500, 500, 100, 10_000, 0.0) is True

    def test_zero_window_excludes_every_older_candle(self) -> None:
        """0.0 is the pre-2026-08-25 rule and must stay reachable exactly."""
        assert may_dispatch_candle(400, 500, 100, 10_000, 0.0) is False

    def test_older_candle_inside_the_window_dispatches(self) -> None:
        now, tf = 10_000_000, 4 * 3_600_000
        ot = now - tf - 3_600_000  # closed one hour ago
        assert may_dispatch_candle(ot, now, tf, now, 24.0) is True

    def test_boundary_is_inclusive(self) -> None:
        now, tf = 10_000_000, 4 * 3_600_000
        ot = now - tf - int(24 * 3_600_000)  # closed exactly 24h ago
        assert may_dispatch_candle(ot, now, tf, now, 24.0) is True
        assert may_dispatch_candle(ot - 1, now, tf, now, 24.0) is False

    def test_negative_age_is_not_dispatchable(self) -> None:
        """A candle closing in the future is still forming, never an alert."""
        now, tf = 10_000_000, 4 * 3_600_000
        assert may_dispatch_candle(now, now + tf, tf, now, 24.0) is False


class TestRunScanCycleRecencyWindow:
    def test_window_admits_the_previous_sessions_older_candle(
        self, tmp_path: Any
    ) -> None:
        """THE FIX: a ~25h-old candle now alerts alongside the newest closed one."""
        df, old, mid, latest = _aged_frame()
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", old - _DAY_MS)  # clear cold-start
        alerts, dispatch, _conn = _drive_cycle(
            [_event(mid), _event(latest)],
            catch_up=True,
            store=store,
            ohlcv=df,
            max_alert_age_hours=30.0,
        )
        assert _primary_sends(dispatch) == 2, (
            "the ~25h candle must dispatch alongside the newest closed one"
        )
        assert len(alerts) == 2

    def test_same_setup_at_zero_window_sends_only_the_newest(
        self, tmp_path: Any
    ) -> None:
        """CONTROL for the test above — identical inputs, window the ONLY change.

        Without this, a passing fix-test could be observing the fixture rather
        than the window. It also pins that 0.0 still reproduces the old rule.
        """
        df, old, mid, latest = _aged_frame()
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", old - _DAY_MS)
        alerts, dispatch, conn = _drive_cycle(
            [_event(mid), _event(latest)],
            catch_up=True,
            store=store,
            ohlcv=df,
            max_alert_age_hours=0.0,
        )
        assert _primary_sends(dispatch) == 1
        assert len(alerts) == 1
        recorded = {
            int(r[0])
            for r in conn.execute(
                "SELECT candle_ts_ms FROM signal_alert_outcomes"
            ).fetchall()
        }
        assert {mid, latest} <= recorded, (
            "the suppressed candle must still reach the ledger — record-not-"
            "dispatch is what the window changes the verdict of, not the record"
        )

    def test_candle_beyond_the_window_stays_backfill(self, tmp_path: Any) -> None:
        """~49h old against a 30h window: recorded, watermarked, never alerted."""
        df, old, mid, latest = _aged_frame()
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", old - _DAY_MS)
        _alerts, dispatch, conn = _drive_cycle(
            [_event(old), _event(mid), _event(latest)],
            catch_up=True,
            store=store,
            ohlcv=df,
            max_alert_age_hours=30.0,
        )
        assert _primary_sends(dispatch) == 2, "the ~49h candle must not alert"
        recorded = {
            int(r[0])
            for r in conn.execute(
                "SELECT candle_ts_ms FROM signal_alert_outcomes"
            ).fetchall()
        }
        assert old in recorded
        assert _consumed(store, "AAPL", "1d", "bos", old)

    def test_a_consumed_watermark_is_never_resent_by_a_wide_window(
        self, tmp_path: Any
    ) -> None:
        """Widening the window must not replay history.

        This is the safety property the config comment claims, constructed
        rather than assumed: a candle a previous backfill already consumed is
        dropped upstream at the is_new_candle filter, so no window can revive
        it. Seed the watermark AT `mid`, then run with an absurd window.
        """
        df, _old, mid, latest = _aged_frame()
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", mid)
        _alerts, dispatch, conn = _drive_cycle(
            [_event(mid), _event(latest)],
            catch_up=True,
            store=store,
            ohlcv=df,
            max_alert_age_hours=10_000.0,
        )
        recorded = {
            int(r[0])
            for r in conn.execute(
                "SELECT candle_ts_ms FROM signal_alert_outcomes"
            ).fetchall()
        }
        assert mid not in recorded, "an already-consumed candle must not reappear"
        assert _primary_sends(dispatch) == 1, "only the newest closed candle is left"

    def test_window_is_inert_without_catch_up(self, tmp_path: Any) -> None:
        """The docs claim the window does nothing unless catch_up is on.

        Constructed rather than asserted from reading: the non-catch-up branch
        appends is_backfill=False unconditionally, so may_dispatch_candle is
        never consulted there. Identical inputs, only the window changes — the
        two runs must agree exactly.
        """
        df, old, mid, latest = _aged_frame()
        sends = []
        for window in (0.0, 10_000.0):
            store = CooldownStore(str(tmp_path / f"s{window}.json"))
            store.mark_candle("AAPL", "1d", "bos", old - _DAY_MS)
            _alerts, dispatch, _conn = _drive_cycle(
                [_event(mid), _event(latest)],
                catch_up=False,
                store=store,
                ohlcv=df,
                max_alert_age_hours=window,
            )
            sends.append(_primary_sends(dispatch))
        assert sends[0] == sends[1], (
            f"catch_up=False must ignore max_alert_age_hours entirely, got {sends}"
        )
