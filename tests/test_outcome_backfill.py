"""Tests for analytics.signal.outcome_backfill.

Covers the four resolution branches (win / loss / expired / still-open),
same-bar TP+SL tie-break, short direction, missing OHLCV, multi-row batching,
and the eligibility gate (only rows with non-NULL tp_price / sl_price /
entry_price / rr_ratio are inspected).
"""

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from analytics.signal.outcome_backfill import (
    DEFAULT_MAX_HOLD_BARS,
    _resolve_max_hold,
    backfill_outcomes,
)
from analytics.signal_config import load_signal_config
from analytics.store import init_schema, upsert_signal_outcome


def _insert_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    tf: str,
    rows: list[dict[str, float | int]],
) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": tf,
                "open_time": r["open_time"],
                "open": r.get("open", r["close"]),
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": 1.0,
            }
            for r in rows
        ]
    )
    conn.register("_o", df)
    conn.execute("INSERT INTO ohlcv SELECT * FROM _o")
    conn.unregister("_o")


def _insert_signal(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str = "sig1",
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    direction: str = "long",
    candle_ts_ms: int = 0,
    entry: float = 100.0,
    sl: float = 95.0,
    tp: float = 110.0,
    rr: float = 2.0,
) -> None:
    upsert_signal_outcome(
        conn,
        {
            "signal_id": signal_id,
            "symbol": symbol,
            "tf": tf,
            "strategy": "fvg",
            "direction": direction,
            "fired_at_ms": candle_ts_ms,
            "candle_ts_ms": candle_ts_ms,
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": tp,
            "rr_ratio": rr,
            "confidence_at_fire": 3,
            "tags": "",
        },
    )


def _fetch_one(conn: duckdb.DuckDBPyConnection, signal_id: str) -> tuple:
    row = conn.execute(
        "SELECT outcome, outcome_r, outcome_filled_at_ms "
        "FROM signal_alert_outcomes WHERE signal_id = ?",
        [signal_id],
    ).fetchone()
    assert row is not None
    return row


_HOUR = 3_600_000  # ms in 1h


class TestBackfillResolution:
    def test_long_win_records_tp_hit_first(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0, rr=2.0)
        # Bar 1: chop. Bar 2: pierces TP.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
                {
                    "open_time": 2 * _HOUR,
                    "high": 111.0,
                    "low": 100.0,
                    "close": 110.5,
                },
            ],
        )

        counts = backfill_outcomes(conn, now_ms=3 * _HOUR)
        assert counts["win"] == 1
        outcome, outcome_r, filled = _fetch_one(conn, "sig1")
        assert outcome == "win"
        assert outcome_r == pytest.approx(2.0)
        assert filled == 2 * _HOUR

    def test_long_loss_records_sl_hit_first(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 101.0, "low": 94.0, "close": 96.0},
            ],
        )

        counts = backfill_outcomes(conn, now_ms=2 * _HOUR)
        assert counts["loss"] == 1
        outcome, outcome_r, filled = _fetch_one(conn, "sig1")
        assert outcome == "loss"
        assert outcome_r == pytest.approx(-1.0)
        assert filled == _HOUR

    def test_same_bar_tp_and_sl_resolves_to_loss(self) -> None:
        """SL takes priority on a tie — mirrors backtest engine."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                # One huge bar that touches both
                {"open_time": _HOUR, "high": 112.0, "low": 94.0, "close": 100.0},
            ],
        )

        counts = backfill_outcomes(conn, now_ms=2 * _HOUR)
        assert counts["loss"] == 1
        outcome, _r, _f = _fetch_one(conn, "sig1")
        assert outcome == "loss"

    def test_short_win_uses_low_below_tp(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(
            conn,
            direction="short",
            candle_ts_ms=0,
            entry=100.0,
            sl=105.0,
            tp=90.0,
            rr=2.0,
        )
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 89.0, "close": 91.0},
            ],
        )

        counts = backfill_outcomes(conn, now_ms=2 * _HOUR)
        assert counts["win"] == 1
        outcome, outcome_r, _ = _fetch_one(conn, "sig1")
        assert outcome == "win"
        assert outcome_r == pytest.approx(2.0)

    def test_short_loss_uses_high_above_sl(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(
            conn, direction="short", candle_ts_ms=0, entry=100.0, sl=105.0, tp=90.0
        )
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 106.0, "low": 99.0, "close": 104.0},
            ],
        )

        counts = backfill_outcomes(conn, now_ms=2 * _HOUR)
        assert counts["loss"] == 1
        outcome, outcome_r, _ = _fetch_one(conn, "sig1")
        assert outcome == "loss"
        assert outcome_r == pytest.approx(-1.0)


class TestBackfillHoldWindow:
    def test_expired_long_records_mtm_r(self) -> None:
        """Past max_hold_bars without hit → expired with MTM R."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0, rr=2.0)
        # 3 chop bars, last close at 103 → MTM (103-100)/5 = 0.6R
        bars = [
            {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
            {"open_time": 2 * _HOUR, "high": 103.0, "low": 98.0, "close": 102.0},
            {"open_time": 3 * _HOUR, "high": 104.0, "low": 99.0, "close": 103.0},
        ]
        _insert_ohlcv(conn, "BTCUSDT", "1h", bars)

        counts = backfill_outcomes(
            conn, now_ms=4 * _HOUR, max_hold_bars_by_tf={"1h": 3}
        )
        assert counts["expired"] == 1
        outcome, outcome_r, filled = _fetch_one(conn, "sig1")
        assert outcome == "expired"
        assert outcome_r == pytest.approx(0.6)
        assert filled == 3 * _HOUR

    def test_still_open_inside_window_leaves_null(self) -> None:
        """Bars < max_hold and no hit → outcome stays NULL, counted as open."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 101.0, "low": 99.0, "close": 100.5},
            ],
        )

        counts = backfill_outcomes(
            conn, now_ms=2 * _HOUR, max_hold_bars_by_tf={"1h": 10}
        )
        assert counts["open"] == 1
        outcome, outcome_r, filled = _fetch_one(conn, "sig1")
        assert outcome is None
        assert outcome_r is None
        assert filled is None

    def test_expired_short_records_mtm_r(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(
            conn,
            direction="short",
            candle_ts_ms=0,
            entry=100.0,
            sl=105.0,
            tp=90.0,
            rr=2.0,
        )
        # 2 chop bars closing at 97 → MTM = (100-97)/5 = 0.6R for a short
        bars = [
            {"open_time": _HOUR, "high": 102.0, "low": 96.0, "close": 98.0},
            {"open_time": 2 * _HOUR, "high": 101.0, "low": 96.0, "close": 97.0},
        ]
        _insert_ohlcv(conn, "BTCUSDT", "1h", bars)

        counts = backfill_outcomes(
            conn, now_ms=3 * _HOUR, max_hold_bars_by_tf={"1h": 2}
        )
        assert counts["expired"] == 1
        outcome, outcome_r, _ = _fetch_one(conn, "sig1")
        assert outcome == "expired"
        assert outcome_r == pytest.approx(0.6)


class TestBackfillEligibility:
    def test_skips_rows_without_tp_price(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Missing tp_price + sl_price → should be skipped entirely
        upsert_signal_outcome(
            conn,
            {
                "signal_id": "sig_no_tp",
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "fvg",
                "direction": "long",
                "fired_at_ms": 0,
                "candle_ts_ms": 0,
                "entry_price": 100.0,
                "sl_price": None,
                "tp_price": None,
                "rr_ratio": None,
                "confidence_at_fire": 3,
                "tags": "",
            },
        )
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 120.0, "low": 80.0, "close": 100.0}],
        )

        counts = backfill_outcomes(conn, now_ms=2 * _HOUR)
        assert counts == {
            "win": 0,
            "loss": 0,
            "expired": 0,
            "open": 0,
            "no_ohlcv": 0,
            "no_hold_cap": 0,
        }
        outcome, _, _ = _fetch_one(conn, "sig_no_tp")
        assert outcome is None

    def test_no_ohlcv_after_signal(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=10 * _HOUR)
        # OHLCV that only exists BEFORE the signal
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 100.0}],
        )

        counts = backfill_outcomes(conn, now_ms=11 * _HOUR)
        assert counts["no_ohlcv"] == 1
        outcome, _, _ = _fetch_one(conn, "sig1")
        assert outcome is None

    def test_resolved_rows_are_not_re_resolved(self) -> None:
        """Second call must not touch already-resolved rows."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 111.0, "low": 99.0, "close": 110.5}],
        )

        first = backfill_outcomes(conn, now_ms=2 * _HOUR)
        assert first["win"] == 1
        second = backfill_outcomes(conn, now_ms=3 * _HOUR)
        assert second == {
            "win": 0,
            "loss": 0,
            "expired": 0,
            "open": 0,
            "no_ohlcv": 0,
            "no_hold_cap": 0,
        }


class TestBackfillBatching:
    def test_one_ohlcv_fetch_resolves_many_signals(self) -> None:
        """Multiple signals on the same (symbol, tf) should all resolve in one pass."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(
            conn, signal_id="s_win", candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0
        )
        _insert_signal(
            conn,
            signal_id="s_loss",
            candle_ts_ms=_HOUR,
            entry=100.0,
            sl=95.0,
            tp=110.0,
        )
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 111.0, "low": 99.0, "close": 110.5},
                {"open_time": 2 * _HOUR, "high": 101.0, "low": 94.0, "close": 96.0},
            ],
        )

        counts = backfill_outcomes(conn, now_ms=3 * _HOUR)
        assert counts["win"] == 1
        assert counts["loss"] == 1
        win_row = _fetch_one(conn, "s_win")
        loss_row = _fetch_one(conn, "s_loss")
        assert win_row[0] == "win"
        assert loss_row[0] == "loss"


class TestStillFormingFinalBar:
    """The resolver must never book an outcome off a bar that has not closed.

    `get_ohlcv` filters on `open_time`, so an unbounded `now_ms` admits the
    current candle: its open_time has passed but its OHLC is provisional and the
    next sync replaces it. Since `backfill_outcomes` only ever revisits rows
    where `outcome IS NULL`, anything written off that bar is frozen wrong
    forever.

    Each test below is paired with the SAME fixture one bar later, because an
    assertion that "nothing resolved" passes trivially if nothing *could* have
    resolved.
    """

    @staticmethod
    def _two_bar_setup(second_bar: dict[str, float | int]) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0, rr=2.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
                second_bar,
            ],
        )
        return conn

    def test_expiry_mark_waits_for_the_final_bar_to_close(self) -> None:
        # Hold window is 2 bars. The 2nd bar has OPENED (now is 1ms into it)
        # but closes at 3h, so the window is not really complete yet.
        conn = self._two_bar_setup(
            {"open_time": 2 * _HOUR, "high": 103.0, "low": 98.0, "close": 102.0}
        )
        counts = backfill_outcomes(
            conn, now_ms=2 * _HOUR + 1, max_hold_bars_by_tf={"1h": 2}
        )
        assert counts["open"] == 1
        assert counts["expired"] == 0
        outcome, outcome_r, _ = _fetch_one(conn, "sig1")
        assert outcome is None
        assert outcome_r is None

    def test_expiry_mark_lands_once_that_bar_has_closed(self) -> None:
        # POSITIVE CONTROL for the test above: identical fixture, clock moved
        # past the 2nd bar's close. If this did not resolve, the assertion
        # above would be vacuous.
        conn = self._two_bar_setup(
            {"open_time": 2 * _HOUR, "high": 103.0, "low": 98.0, "close": 102.0}
        )
        counts = backfill_outcomes(
            conn, now_ms=3 * _HOUR, max_hold_bars_by_tf={"1h": 2}
        )
        assert counts["expired"] == 1
        outcome, outcome_r, _ = _fetch_one(conn, "sig1")
        assert outcome == "expired"
        # mark-to-market at the closed bar: (102 - 100) / (100 - 95) = +0.4R
        assert outcome_r == pytest.approx(0.4)

    def test_win_is_not_booked_off_a_forming_bar(self) -> None:
        # The provisional bar's high already pierces TP. That touch is real,
        # but the bar can still be rewritten by the next sync, so the row must
        # wait rather than resolve early.
        conn = self._two_bar_setup(
            {"open_time": 2 * _HOUR, "high": 111.0, "low": 100.0, "close": 110.5}
        )
        counts = backfill_outcomes(
            conn, now_ms=2 * _HOUR + 1, max_hold_bars_by_tf={"1h": 2}
        )
        assert counts["win"] == 0
        assert counts["open"] == 1
        assert _fetch_one(conn, "sig1")[0] is None

    def test_win_is_booked_once_that_bar_has_closed(self) -> None:
        # POSITIVE CONTROL for the test above.
        conn = self._two_bar_setup(
            {"open_time": 2 * _HOUR, "high": 111.0, "low": 100.0, "close": 110.5}
        )
        counts = backfill_outcomes(
            conn, now_ms=3 * _HOUR, max_hold_bars_by_tf={"1h": 2}
        )
        assert counts["win"] == 1
        assert _fetch_one(conn, "sig1")[0] == "win"


class TestMaxHoldCalibrationCoverage:
    """Every timeframe a config scans must have a calibrated hold cap.

    This guard could not be written before 2026-08-12: `1wk` had no entry, the
    fallback silently handed it the `15m` value (96 bars = 96 WEEKS), and the
    `weekdays` config scans `1wk` — so the assertion would have been red with no
    correct value to make it green. Choosing 7 (user, 2026-08-12) is what made
    the guard writable, which is the point worth remembering: a guard whose only
    fix is a calibration decision belongs WITH that decision, not before it.
    """

    def test_max_hold_covers_every_configured_timeframe(self) -> None:
        config_dir = Path(__file__).parent.parent / "config"
        configs = sorted(config_dir.glob("signal_watch*.toml"))
        assert configs, "no signal_watch configs found — glob or layout changed"

        missing: list[str] = []
        for cfg_path in configs:
            cfg = load_signal_config(cfg_path)
            for tf in cfg.timeframes:
                if tf not in DEFAULT_MAX_HOLD_BARS:
                    missing.append(f"{cfg_path.name}:{tf}")

        assert not missing, (
            "timeframe(s) scanned by a config but absent from "
            f"DEFAULT_MAX_HOLD_BARS: {missing}. Rows on those timeframes will be "
            "left unresolved (counts['no_hold_cap']). Add a calibrated entry — "
            "match the fraction of backtest_trades that resolve within it, the "
            "way 4h (91.3%) / 1d (86.3%) / 1wk (91.1%) are calibrated."
        )

    def test_unlisted_timeframe_is_refused_not_guessed(self) -> None:
        """A timeframe with no cap must leave rows NULL, not invent a window.

        Mutation check: reverting `_resolve_max_hold` to
        `hold_map.get(tf, max(hold_map.values()))` makes this fail, because the
        row would be scored against the 96-bar `15m` cap instead of skipped.
        """
        assert _resolve_max_hold("1wk", DEFAULT_MAX_HOLD_BARS) == 7
        assert _resolve_max_hold("30m", DEFAULT_MAX_HOLD_BARS) is None
        # The old fallback would have returned this instead of None.
        assert max(DEFAULT_MAX_HOLD_BARS.values()) == 96
