"""Tests for opt-in missed-day catch-up.

The scanner normally fires on only the single latest *closed* candle, so a
skipped run-day permanently loses that day's signals (no backlog replay). With
``catch_up=True``:

- ``scan_symbol`` emits a SignalEvent for *every* closed candle in the window
  (each carrying its own entry price), not just the latest.
- ``run_scan_cycle`` replays each missed candle independently — conflict
  resolution and confluence stacking stay per-candle correct — and the live
  candle watermark drops the candles already alerted on a prior run.
- A cold-start guard restricts the first run for an un-watermarked key to the
  latest candle only, so the whole window is not replayed as a burst.

Default (``catch_up=False``) keeps the latest-candle-only behaviour byte-for-byte.
See ``project_signal_watch_followups.md`` Task 1.
"""

from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd

from analytics.signal.scanner import run_scan_cycle, scan_symbol
from analytics.signal.types import SignalEvent
from analytics.store import init_schema
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
# run_scan_cycle — cold start, missed-candle replay, per-candle isolation      #
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
    events: list[SignalEvent], *, catch_up: bool, store: CooldownStore
) -> list[str]:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    df = _cycle_ohlcv()
    with (
        patch("analytics.signal.scanner.get_ohlcv", return_value=df),
        patch("analytics.signal.scanner.scan_symbol", return_value=events),
        patch(
            "utils.telegram_router.dispatch_to_channel",
            side_effect=lambda text, channel: True,
        ),
    ):
        return run_scan_cycle(
            conn=conn,
            symbols=["AAPL"],
            timeframes=["1d"],
            strategies=["bos", "fvg"],
            store=store,
            send_telegram=True,
            catch_up=catch_up,
        )


def _consumed(store: CooldownStore, symbol: str, tf: str, strat: str, ot: int) -> bool:
    return not store.is_new_candle(symbol, tf, strat, ot)


class TestRunScanCycleCatchUp:
    def test_off_default_unchanged(self, tmp_path: Any) -> None:
        """catch_up=False: only what scan_symbol returned (latest candle) fires."""
        store = CooldownStore(str(tmp_path / "s.json"))
        alerts = _drive_cycle([_event(_C2)], catch_up=False, store=store)
        assert len(alerts) == 1
        assert _consumed(store, "AAPL", "1d", "bos", _C2)

    def test_cold_start_fires_latest_candle_only(self, tmp_path: Any) -> None:
        """No prior watermark: replay must NOT burst the whole window — only the
        latest candle is alerted (and watermarked)."""
        store = CooldownStore(str(tmp_path / "s.json"))
        alerts = _drive_cycle(
            [_event(_C1), _event(_C2), _event(_C3)], catch_up=True, store=store
        )
        # Exactly one alert (the latest candle), and the watermark advanced to it.
        # The watermark is a monotonic high-water mark, so C1/C2 are not probed
        # directly — len==1 already proves they did not each fire.
        assert len(alerts) == 1
        assert _consumed(store, "AAPL", "1d", "bos", _C3)

    def test_replays_missed_candles_after_watermark(self, tmp_path: Any) -> None:
        """With a seeded watermark at C1, a later run catches up C2 and C3 but
        re-suppresses C1 (already alerted)."""
        store = CooldownStore(str(tmp_path / "s.json"))
        store.mark_candle("AAPL", "1d", "bos", _C1)
        alerts = _drive_cycle(
            [_event(_C1), _event(_C2), _event(_C3)], catch_up=True, store=store
        )
        assert len(alerts) == 2  # C2 and C3 only
        assert _consumed(store, "AAPL", "1d", "bos", _C2)
        assert _consumed(store, "AAPL", "1d", "bos", _C3)

    def test_conflict_resolution_is_per_candle(self, tmp_path: Any) -> None:
        """A long on one candle and an opposing short on a *different* candle must
        not be resolved against each other — each candle fires its own alert."""
        store = CooldownStore(str(tmp_path / "s.json"))
        # Seed both strategies so the cold-start guard does not restrict to latest.
        store.mark_candle("AAPL", "1d", "bos", _C0)
        store.mark_candle("AAPL", "1d", "fvg", _C0)
        events = [
            _event(_C2, direction="long", confidence=5),
            _event(_C3, direction="short", confidence=1),
        ]
        alerts = _drive_cycle(events, catch_up=True, store=store)
        assert len(alerts) == 2
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
