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

from analytics.backtest.cost_model import CostModel
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
                "volume": r.get("volume", 1.0),
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


def _fetch_cost(conn: duckdb.DuckDBPyConnection, signal_id: str) -> tuple:
    row = conn.execute(
        "SELECT outcome_r, outcome_cost_r FROM signal_alert_outcomes "
        "WHERE signal_id = ?",
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


class TestWinCreditsTheEffectiveTarget:
    """A win books the R implied by `tp_price`, not the declared `rr_ratio`.

    `alert_formatter` (mirrored by `_resolve_outcome_sl_tp`) prefers a detector's
    structural TP over `entry ± sl_dist × tp_r`, but the ledger recorded the
    configured `tp_r` in `rr_ratio` — so a TP sitting 2R away was booked at 5R.
    30 of 267 resolved rows diverged and 8 of them were wins, worth +13.50R of
    phantom credit; the resolver now derives the credit from the level it walks.
    """

    def test_structural_tp_win_credits_implied_not_declared(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # risk = 5, TP at 110 = +2R — but the config declared tp_r = 5.0.
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0, rr=5.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 111.0, "low": 100.0, "close": 110.5}],
        )

        assert backfill_outcomes(conn, now_ms=2 * _HOUR)["win"] == 1
        outcome, outcome_r, _ = _fetch_one(conn, "sig1")
        assert outcome == "win"
        assert outcome_r == pytest.approx(2.0)  # NOT the declared 5.0

    def test_short_structural_tp_win_credits_implied(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # short: risk = 10, TP at 75 = +2.5R, declared 5.0.
        _insert_signal(
            conn,
            candle_ts_ms=0,
            direction="short",
            entry=100.0,
            sl=110.0,
            tp=75.0,
            rr=5.0,
        )
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 74.0, "close": 75.0}],
        )

        assert backfill_outcomes(conn, now_ms=2 * _HOUR)["win"] == 1
        outcome, outcome_r, _ = _fetch_one(conn, "sig1")
        assert outcome == "win"
        assert outcome_r == pytest.approx(2.5)

    def test_pct_fallback_win_still_credits_the_declared_value(self) -> None:
        """The other direction: an agreeing row must not move.

        On a pct-fallback alert `tp_price` IS `entry + sl_dist × tp_r`, so the
        implied value reproduces `rr_ratio` exactly. A fix that only ever
        lowered the credit would pass the two tests above and silently break
        every ordinary alert.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=115.0, rr=3.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 116.0, "low": 100.0, "close": 115.5}],
        )

        assert backfill_outcomes(conn, now_ms=2 * _HOUR)["win"] == 1
        assert _fetch_one(conn, "sig1")[1] == pytest.approx(3.0)

    def test_loss_and_expired_are_untouched_by_the_declared_target(self) -> None:
        """Only a win ever read `rr_ratio` — the other two branches must not move."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(
            conn,
            signal_id="loss1",
            candle_ts_ms=0,
            entry=100.0,
            sl=95.0,
            tp=110.0,
            rr=5.0,
        )
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 94.0, "close": 95.0}],
        )
        assert backfill_outcomes(conn, now_ms=2 * _HOUR)["loss"] == 1
        assert _fetch_one(conn, "loss1")[1] == pytest.approx(-1.0)

        conn2 = duckdb.connect(":memory:")
        init_schema(conn2)
        _insert_signal(
            conn2,
            signal_id="exp1",
            candle_ts_ms=0,
            entry=100.0,
            sl=95.0,
            tp=110.0,
            rr=5.0,
        )
        _insert_ohlcv(
            conn2,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 103.0, "low": 99.0, "close": 103.0}],
        )
        counts = backfill_outcomes(
            conn2, now_ms=3 * _HOUR, max_hold_bars_by_tf={"1h": 1}
        )
        assert counts["expired"] == 1
        # mark-to-market off sl_dist: (103 - 100) / 5 = +0.6, no rr_ratio in sight.
        assert _fetch_one(conn2, "exp1")[1] == pytest.approx(0.6)

    def test_unusable_tp_price_falls_back_to_the_declared_value(self) -> None:
        """A TP on the wrong side of entry is not a target — keep `rr_ratio`.

        `implied_tp_r` guards this the way `alert_formatter` does. Without the
        guard the implied value would be negative and a win would book a loss.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=90.0, rr=2.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 120.0, "low": 99.0, "close": 119.0}],
        )

        assert backfill_outcomes(conn, now_ms=2 * _HOUR)["win"] == 1
        assert _fetch_one(conn, "sig1")[1] == pytest.approx(2.0)


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


class TestNetOfCostResolution:
    """`outcome_r` is charged what a backtest of the same signal would be charged.

    The ledger was gross while `run_backtest` was net, so every backtest-vs-live
    comparison was biased IN FAVOUR OF LIVE. These pin the charge itself, the
    NULL-vs-0.0 distinction that keeps "unpriced" separable from "cost nothing",
    and the counting invariant that the widened OHLCV fetch must not disturb.

    Cost here is spread-only by construction — `impact_coef`, `borrow_rate_annual`
    and `commission_bps` are all zeroed — so every expected number below is
    `2 * half_spread * entry / risk` and is exact rather than approximate.
    """

    # entry 100 / sl 95 -> risk 5 -> notional_to_r 20.
    # Bucket 0 (20bps): 2 * 0.0020 * 20 = 0.08R. Bucket 3 (1bp): 2 * 0.0001 * 20
    # = 0.004R. A default-volume bar (1.0) prices at ~$700 ADV -> bucket 0.
    SPREAD_ONLY = CostModel(impact_coef=0.0, borrow_rate_annual=0.0, commission_bps=0.0)
    ILLIQUID_R = 0.08
    LIQUID_R = 0.004

    def _win_conn(
        self, *, signal_bar: bool = True, volume: float = 1.0
    ) -> duckdb.DuckDBPyConnection:
        """A long that wins at +2.0R gross, optionally with its signal bar present.

        Omitting the signal bar is how a missing cost context is produced: the
        forward walk is unaffected (it only reads bars AFTER the signal), but
        `cost_context_at` has no bar to end its trailing window on.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0, rr=2.0)
        bars: list[dict[str, float | int]] = []
        if signal_bar:
            bars.append(
                {
                    "open_time": 0,
                    "high": 100.5,
                    "low": 99.5,
                    "close": 100.0,
                    "volume": volume,
                }
            )
        bars += [
            {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
            {"open_time": 2 * _HOUR, "high": 111.0, "low": 100.0, "close": 110.5},
        ]
        _insert_ohlcv(conn, "BTCUSDT", "1h", bars)
        return conn

    def test_unpriced_run_leaves_gross_r_and_a_null_cost(self) -> None:
        """No cost basis configured -> gross R, and NULL rather than 0.0.

        Paired with `test_cost_model_charges_and_records_the_drag` below, which
        is its positive control: without that pairing this assertion is satisfied
        both by "costs were correctly not charged" and by "costs never work".
        """
        conn = self._win_conn()
        counts = backfill_outcomes(conn, now_ms=3 * _HOUR)
        assert counts["win"] == 1
        outcome_r, cost_r = _fetch_cost(conn, "sig1")
        assert outcome_r == pytest.approx(2.0)
        assert cost_r is None

    def test_cost_model_charges_and_records_the_drag(self) -> None:
        """Positive control for the test above, and the round-trip to gross."""
        conn = self._win_conn()
        counts = backfill_outcomes(conn, now_ms=3 * _HOUR, cost_model=self.SPREAD_ONLY)
        assert counts["win"] == 1
        outcome_r, cost_r = _fetch_cost(conn, "sig1")
        assert cost_r == pytest.approx(self.ILLIQUID_R)
        assert outcome_r == pytest.approx(2.0 - self.ILLIQUID_R)
        # Gross stays recoverable — that is what the second column buys.
        assert outcome_r + cost_r == pytest.approx(2.0)

    def test_a_loss_is_charged_too(self) -> None:
        """A stop-out books -1.0 MINUS costs, not a flat -1.0.

        All 218 losses in the live ledger were exactly -1.0000R before this,
        which is what made the gross basis easy to miss: the column looked
        canonical rather than uncharged.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=0, entry=100.0, sl=95.0, tp=110.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": 0, "high": 100.5, "low": 99.5, "close": 100.0},
                {"open_time": _HOUR, "high": 101.0, "low": 94.0, "close": 96.0},
            ],
        )
        counts = backfill_outcomes(conn, now_ms=2 * _HOUR, cost_model=self.SPREAD_ONLY)
        assert counts["loss"] == 1
        outcome_r, cost_r = _fetch_cost(conn, "sig1")
        assert cost_r == pytest.approx(self.ILLIQUID_R)
        assert outcome_r == pytest.approx(-1.0 - self.ILLIQUID_R)

    def test_flat_fee_path_matches_the_engine_formula(self) -> None:
        """No cost model -> `2 * fee_pct * entry / risk`, exactly as `pnl_r`."""
        conn = self._win_conn()
        backfill_outcomes(conn, now_ms=3 * _HOUR, fee_pct=0.001)
        outcome_r, cost_r = _fetch_cost(conn, "sig1")
        expected = 2.0 * 0.001 * 100.0 / 5.0
        assert cost_r == pytest.approx(expected)
        assert outcome_r == pytest.approx(2.0 - expected)

    def test_a_cost_model_ignores_fee_pct(self) -> None:
        """Mirrors `Trade.pnl_r`: the model REPLACES the flat fee, never adds.

        Summing the two would be the easy misreading, and it would silently
        double-charge every live row the moment a config set both.
        """
        conn = self._win_conn()
        backfill_outcomes(
            conn, now_ms=3 * _HOUR, cost_model=self.SPREAD_ONLY, fee_pct=0.001
        )
        _outcome_r, cost_r = _fetch_cost(conn, "sig1")
        assert cost_r == pytest.approx(self.ILLIQUID_R)

    def test_a_liquid_signal_bar_is_charged_less_than_a_missing_one(self) -> None:
        """A missing cost context OVERCHARGES; it must never charge nothing.

        Same fixture twice, differing only in whether the signal bar is in
        OHLCV. The liquid reading is the 1bp bucket; the fallback is the widest.
        A regression that returned a free trade on a missing context would pass
        an `is not None` assertion, so both magnitudes are pinned.
        """
        liquid = self._win_conn(volume=1e7)
        backfill_outcomes(liquid, now_ms=3 * _HOUR, cost_model=self.SPREAD_ONLY)
        _r, liquid_cost = _fetch_cost(liquid, "sig1")

        missing = self._win_conn(signal_bar=False, volume=1e7)
        backfill_outcomes(missing, now_ms=3 * _HOUR, cost_model=self.SPREAD_ONLY)
        _r2, fallback_cost = _fetch_cost(missing, "sig1")

        assert liquid_cost == pytest.approx(self.LIQUID_R)
        assert fallback_cost == pytest.approx(self.ILLIQUID_R)
        assert fallback_cost > liquid_cost

    def test_widening_the_fetch_does_not_reclassify_no_ohlcv(self) -> None:
        """A cost model reaches BACKWARDS; `no_ohlcv` must still mean the same.

        The fetch is widened by the trailing ADV window, so a signal whose only
        bars sit BEFORE it now returns a non-empty frame. Without the slice back
        to the post-signal frame, `bars.empty` would be False and these rows
        would silently move from `no_ohlcv` into `open` — a count changing
        meaning because an unrelated feature was switched on.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_signal(conn, candle_ts_ms=10 * _HOUR, entry=100.0, sl=95.0, tp=110.0)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": 8 * _HOUR, "high": 101.0, "low": 99.0, "close": 100.0},
                {"open_time": 9 * _HOUR, "high": 101.0, "low": 99.0, "close": 100.0},
            ],
        )
        counts = backfill_outcomes(conn, now_ms=20 * _HOUR, cost_model=self.SPREAD_ONLY)
        assert counts["no_ohlcv"] == 1
        assert counts["open"] == 0
