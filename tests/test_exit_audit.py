"""Tests for analytics.exits.audit — the exit-policy A/B driver.

Covers the effective-vs-declared TP target, the per-kind policy builder, the
ledger walk, the paired A/B, and the RTH forward-window regression that the
port's first run exposed.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from analytics.exits.audit import (
    POLICY_KINDS,
    TIME_STOP_FLOOR_BY_TF,
    _policy_for,
    baseline_agreement,
    effective_tp_r,
    resolve_ledger_under_policy,
    run_exit_ab,
)
from analytics.store import init_schema

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000


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


def _insert_alert(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str,
    symbol: str = "AAPL",
    tf: str = "4h",
    strategy: str = "pin_bar",
    direction: str = "long",
    candle_ts_ms: int,
    entry: float,
    sl: float,
    tp: float | None,
    rr: float,
    outcome: str = "loss",
    outcome_r: float = -1.0,
) -> None:
    conn.execute(
        "INSERT INTO signal_alert_outcomes (signal_id, symbol, tf, strategy, "
        "direction, fired_at_ms, candle_ts_ms, entry_price, sl_price, tp_price, "
        "rr_ratio, outcome, outcome_r) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            signal_id,
            symbol,
            tf,
            strategy,
            direction,
            candle_ts_ms,
            candle_ts_ms,
            entry,
            sl,
            tp,
            rr,
            outcome,
            outcome_r,
        ],
    )


def _rth_4h_bars(
    start_day_ms: int, n_bars: int, highs: list[float], lows: list[float]
) -> list[dict[str, float | int]]:
    """`n_bars` RTH `4h` bars — TWO per calendar day (13:30 / 17:30 UTC).

    This gap is the whole point: on a 24/7 tape `n` bars span `n * 4h` of
    wall-clock, here they span roughly `n * 12h`.
    """
    rows: list[dict[str, float | int]] = []
    for i in range(n_bars):
        day, slot = divmod(i, 2)
        ts = start_day_ms + day * _DAY_MS + (13 * _HOUR_MS + 1_800_000)
        if slot:
            ts += 4 * _HOUR_MS
        rows.append(
            {
                "open_time": ts,
                "high": highs[i],
                "low": lows[i],
                "close": (highs[i] + lows[i]) / 2,
            }
        )
    return rows


class TestEffectiveTpR:
    """The declared `rr_ratio` is not the effective target on a structural TP."""

    def test_structural_tp_overrides_declared_rr_long(self) -> None:
        # entry 100, sl 90 -> risk 10. tp 120 is +2R, but rr_ratio claims 5.
        assert effective_tp_r(
            direction="long", entry=100.0, sl_price=90.0, rr_ratio=5.0, tp_price=120.0
        ) == pytest.approx(2.0)

    def test_structural_tp_overrides_declared_rr_short(self) -> None:
        assert effective_tp_r(
            direction="short", entry=100.0, sl_price=110.0, rr_ratio=5.0, tp_price=75.0
        ) == pytest.approx(2.5)

    def test_agreeing_tp_returns_the_same_number(self) -> None:
        assert effective_tp_r(
            direction="long", entry=100.0, sl_price=90.0, rr_ratio=3.0, tp_price=130.0
        ) == pytest.approx(3.0)

    @pytest.mark.parametrize("tp", [None, 0.0, -5.0])
    def test_missing_or_nonpositive_tp_falls_back_to_declared(
        self, tp: float | None
    ) -> None:
        assert effective_tp_r(
            direction="long", entry=100.0, sl_price=90.0, rr_ratio=4.0, tp_price=tp
        ) == pytest.approx(4.0)

    def test_tp_on_the_wrong_side_falls_back_rather_than_going_negative(self) -> None:
        # A long whose "TP" sits below entry would imply a negative R target,
        # which `ExitPolicyConfig` rejects outright. Fall back instead.
        assert effective_tp_r(
            direction="long", entry=100.0, sl_price=90.0, rr_ratio=4.0, tp_price=80.0
        ) == pytest.approx(4.0)

    def test_zero_risk_falls_back(self) -> None:
        assert effective_tp_r(
            direction="long", entry=100.0, sl_price=100.0, rr_ratio=2.0, tp_price=120.0
        ) == pytest.approx(2.0)


class TestPolicyFor:
    def test_unknown_timeframe_returns_none_rather_than_guessing(self) -> None:
        assert (
            _policy_for(
                "fixed",
                tf="3d",
                rr=2.0,
                max_hold_by_tf={"4h": 30},
                time_stop_by_tf={"4h": 4},
            )
            is None
        )

    def test_fixed_has_no_early_stop_and_no_locks(self) -> None:
        p = _policy_for(
            "fixed",
            tf="4h",
            rr=2.0,
            max_hold_by_tf={"4h": 30},
            time_stop_by_tf={"4h": 4},
        )
        assert p is not None
        assert p.effective_time_stop_bars == 30
        assert not p.has_breakeven and not p.has_partial

    def test_time_only_stops_early_but_keeps_locks_off(self) -> None:
        p = _policy_for(
            "time_only",
            tf="4h",
            rr=2.0,
            max_hold_by_tf={"4h": 30},
            time_stop_by_tf={"4h": 4},
        )
        assert p is not None
        assert p.effective_time_stop_bars == 4
        assert not p.has_breakeven and not p.has_partial

    def test_be_partial_keeps_the_full_window(self) -> None:
        p = _policy_for(
            "be_partial",
            tf="4h",
            rr=2.0,
            max_hold_by_tf={"4h": 30},
            time_stop_by_tf={"4h": 4},
        )
        assert p is not None
        assert p.effective_time_stop_bars == 30
        assert p.has_breakeven and p.has_partial

    def test_composite_uses_both_levers(self) -> None:
        p = _policy_for(
            "composite",
            tf="4h",
            rr=2.0,
            max_hold_by_tf={"4h": 30},
            time_stop_by_tf={"4h": 4},
        )
        assert p is not None
        assert p.effective_time_stop_bars == 4
        assert p.has_breakeven and p.has_partial

    def test_a_timeframe_with_no_declared_floor_gets_no_early_stop(self) -> None:
        """The fallback is the CONSERVATIVE direction, and that is deliberate.

        An undeclared floor means the winner-timing p90 was never measured for
        that timeframe, so the policy declines to invent one rather than
        defaulting to somebody else's number.
        """
        p = _policy_for(
            "composite",
            tf="1wk",
            rr=2.0,
            max_hold_by_tf={"1wk": 7},
            time_stop_by_tf={},
        )
        assert p is not None
        assert p.effective_time_stop_bars == 7

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown policy kind"):
            _policy_for(
                "trailing",
                tf="4h",
                rr=2.0,
                max_hold_by_tf={"4h": 30},
                time_stop_by_tf={"4h": 4},
            )


class TestForwardWindowSpansRthGaps:
    """Regression: the fetch window must not assume a 24/7 tape.

    Upstream fetched `max(candle_ts) + (max_hold + 2) * tf_ms`, exact on crypto
    and short by ~4x on RTH `4h` bars, where 30 bars span ~132 `4h` units of
    wall-clock. The truncation silently marked would-be winners to market at the
    last fetched bar. This test puts the TP beyond the old horizon but inside
    the real 30-bar window, so it FAILS if the time-derived fetch comes back.
    """

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # 30 RTH bars. Flat until bar 20, which prints the +3R high.
        highs = [101.0] * 30
        lows = [99.0] * 30
        highs[20] = 131.0
        bars = _rth_4h_bars(1_700_000_000_000, 30, highs, lows)
        _insert_ohlcv(conn, "AAPL", "4h", bars)
        # Signal candle sits one 4h step before the first forward bar.
        _insert_alert(
            conn,
            signal_id="AAPL-4h-pin_bar-1-long",
            candle_ts_ms=int(bars[0]["open_time"]) - 4 * _HOUR_MS,
            entry=100.0,
            sl=90.0,
            tp=130.0,
            rr=3.0,
            outcome="win",
            outcome_r=3.0,
        )
        return conn

    def test_target_beyond_the_crypto_horizon_still_resolves_as_a_win(self) -> None:
        res = resolve_ledger_under_policy(self._conn(), "fixed")
        assert res.n == 1
        trade = res.trades[0]
        assert trade.outcome == "win"
        assert trade.realized_r == pytest.approx(3.0)
        assert trade.bars_held == 21

    def test_the_old_horizon_would_not_have_reached_it(self) -> None:
        """Pin the premise: bar 20 really is outside `(30 + 2) * 4h`."""
        conn = self._conn()
        row = conn.execute("SELECT candle_ts_ms FROM signal_alert_outcomes").fetchone()
        assert row is not None
        old_end = int(row[0]) + 32 * 4 * _HOUR_MS
        reachable = conn.execute(
            "SELECT count(*) FROM ohlcv WHERE open_time <= ?", [old_end]
        ).fetchone()
        assert reachable is not None
        assert reachable[0] < 21


class TestResolveLedgerUnderPolicy:
    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Long from 100, risk 10. Touches +1R on bar 1, then decays and stops.
        highs = [111.0, 105.0, 101.0, 99.0, 95.0] + [92.0] * 25
        lows = [99.0, 98.0, 97.0, 95.0, 92.0] + [89.0] * 25
        bars = _rth_4h_bars(1_700_000_000_000, 30, highs, lows)
        _insert_ohlcv(conn, "AAPL", "4h", bars)
        _insert_alert(
            conn,
            signal_id="AAPL-4h-pin_bar-1-long",
            candle_ts_ms=int(bars[0]["open_time"]) - 4 * _HOUR_MS,
            entry=100.0,
            sl=90.0,
            tp=130.0,
            rr=3.0,
            outcome="loss",
            outcome_r=-1.0,
        )
        return conn

    def test_fixed_takes_the_full_minus_one_r(self) -> None:
        res = resolve_ledger_under_policy(self._conn(), "fixed")
        assert res.trades[0].outcome == "loss"
        assert res.trades[0].realized_r == pytest.approx(-1.0)

    def test_be_partial_banks_half_at_1r_and_scratches_the_rest(self) -> None:
        """The #156 cohort in miniature: +1R touched, then reversal to the stop.

        Partial 50% at +1R = +0.5R banked; breakeven arms from the NEXT bar, so
        the remaining half exits at 0R rather than −1R.
        """
        res = resolve_ledger_under_policy(self._conn(), "be_partial")
        trade = res.trades[0]
        assert trade.outcome == "breakeven"
        assert trade.realized_r == pytest.approx(0.5)
        # And the baseline on the SAME bars takes the full loss — without this
        # the assertion above is satisfied by any policy that happens to scratch.
        base = resolve_ledger_under_policy(self._conn(), "fixed")
        assert base.trades[0].realized_r == pytest.approx(-1.0)

    def test_unresolvable_timeframe_is_skipped_not_guessed(self) -> None:
        conn = self._conn()
        conn.execute("UPDATE signal_alert_outcomes SET tf = '3d'")
        conn.execute("UPDATE ohlcv SET timeframe = '3d'")
        assert resolve_ledger_under_policy(conn, "fixed").n == 0

    def test_missing_ohlcv_is_skipped(self) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM ohlcv")
        assert resolve_ledger_under_policy(conn, "fixed").n == 0

    def test_zero_risk_row_is_skipped(self) -> None:
        conn = self._conn()
        conn.execute("UPDATE signal_alert_outcomes SET sl_price = entry_price")
        assert resolve_ledger_under_policy(conn, "fixed").n == 0

    def test_recorded_outcome_is_carried_for_the_positive_control(self) -> None:
        res = resolve_ledger_under_policy(self._conn(), "fixed")
        assert res.trades[0].recorded_outcome == "loss"
        assert baseline_agreement(res) == (1, 1)

    def test_baseline_agreement_counts_disagreements(self) -> None:
        conn = self._conn()
        conn.execute("UPDATE signal_alert_outcomes SET outcome = 'win'")
        res = resolve_ledger_under_policy(conn, "fixed")
        assert baseline_agreement(res) == (0, 1)


class TestRunExitAb:
    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        highs = [111.0, 105.0, 101.0, 99.0, 95.0] + [92.0] * 25
        lows = [99.0, 98.0, 97.0, 95.0, 92.0] + [89.0] * 25
        bars = _rth_4h_bars(1_700_000_000_000, 30, highs, lows)
        for sym in ("AAPL", "MSFT", "NVDA", "AMD"):
            _insert_ohlcv(conn, sym, "4h", bars)
            _insert_alert(
                conn,
                signal_id=f"{sym}-4h-pin_bar-1-long",
                symbol=sym,
                candle_ts_ms=int(bars[0]["open_time"]) - 4 * _HOUR_MS,
                entry=100.0,
                sl=90.0,
                tp=130.0,
                rr=3.0,
            )
        return conn

    def test_every_arm_scores_the_same_alerts(self) -> None:
        rows = run_exit_ab(self._conn(), n_boot=200)
        assert [r.name for r in rows] == list(POLICY_KINDS)
        assert {r.n for r in rows} == {4}
        assert {r.n_paired for r in rows} == {4}

    def test_baseline_row_carries_no_uplift(self) -> None:
        rows = run_exit_ab(self._conn(), n_boot=200)
        base = rows[0]
        assert base.name == "fixed"
        assert base.uplift == 0.0
        assert base.d_sharpe == 0.0
        assert base.uplift_ci is None

    def test_lock_arms_report_a_positive_uplift_with_a_ci(self) -> None:
        rows = {r.name: r for r in run_exit_ab(self._conn(), n_boot=200, seed=1)}
        arm = rows["be_partial"]
        assert arm.uplift == pytest.approx(1.5)  # -1.0 -> +0.5 on every alert
        assert arm.uplift_ci is not None
        assert arm.uplift_ci.lo <= arm.uplift <= arm.uplift_ci.hi

    def test_empty_kinds_returns_empty(self) -> None:
        assert run_exit_ab(self._conn(), kinds=()) == []

    def test_seed_makes_the_ci_reproducible(self) -> None:
        a = run_exit_ab(self._conn(), n_boot=200, seed=42)
        b = run_exit_ab(self._conn(), n_boot=200, seed=42)
        assert [r.uplift_ci for r in a] == [r.uplift_ci for r in b]


class TestTimeStopFloorsAreDeclaredForEveryLiveTimeframe:
    """A floor is a MEASURED quantity, so an undeclared one must stay undeclared.

    This asserts the shape of the map rather than its values: every declared
    floor must be a positive int that fits inside that timeframe's hold window.
    It deliberately does NOT require an entry for every configured timeframe —
    unlike `DEFAULT_MAX_HOLD_BARS`, a missing floor is safe here (no early stop),
    and requiring one would force a guessed number into the map.
    """

    def test_declared_floors_fit_inside_their_hold_window(self) -> None:
        from analytics.signal.outcome_backfill import DEFAULT_MAX_HOLD_BARS

        for tf, floor in TIME_STOP_FLOOR_BY_TF.items():
            assert tf in DEFAULT_MAX_HOLD_BARS, f"{tf} has a floor but no hold cap"
            assert isinstance(floor, int)
            assert 1 <= floor <= DEFAULT_MAX_HOLD_BARS[tf]
