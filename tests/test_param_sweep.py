"""Tests for analytics/param_sweep.py — directional OOS metrics (Gate 3)."""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from analytics.backtest.cv_splits import CvConfig, FoldSplit
from analytics.backtest_lib import BacktestResult, Trade
from analytics.param_sweep import (
    AuditRow,
    ParamRange,
    SweepRow,
    _audit_strategy_worker,
    _dedup_trades,
    _directional_split_hint,
    _row_from_results,
    _score,
    _sweep_grid_worker,
    _sweep_grid_worker_cv,
    format_audit_results,
    format_sweep_results,
    run_param_sweep,
)
from tests.conftest import _candle, _make_ohlcv

_BASE_TIME = 1_700_000_000_000


def _make_result(
    long_trades: list[Trade] | None = None,
    short_trades: list[Trade] | None = None,
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    strategy: str = "fvg",
) -> BacktestResult:
    """Build a BacktestResult with specific long/short trades for unit testing."""
    result = BacktestResult(symbol=symbol, timeframe=timeframe, strategy=strategy)
    for t in long_trades or []:
        result.trades.append(t)
    for t in short_trades or []:
        result.trades.append(t)
    return result


def _win(direction: str, r: float = 1.0, signal_time: int = _BASE_TIME) -> Trade:
    entry = 100.0
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tp = entry + r * 5.0 if direction == "long" else entry - r * 5.0
    t = Trade(
        signal_time=signal_time,
        entry_time=signal_time + 1,
        entry_price=entry,
        direction=direction,
        sl_price=sl,
        tp_price=tp,
        fee_pct=0.0,
    )
    t.outcome = "win"
    t.exit_price = tp
    return t


def _loss(direction: str, signal_time: int = _BASE_TIME) -> Trade:
    entry = 100.0
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tp = entry + 10.0 if direction == "long" else entry - 10.0
    t = Trade(
        signal_time=signal_time,
        entry_time=signal_time + 1,
        entry_price=entry,
        direction=direction,
        sl_price=sl,
        tp_price=tp,
        fee_pct=0.0,
    )
    t.outcome = "loss"
    t.exit_price = sl
    return t


def _open_trade(direction: str, signal_time: int = _BASE_TIME) -> Trade:
    """An unresolved trade (outcome='open') — what segment truncation produces."""
    entry = 100.0
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tp = entry + 10.0 if direction == "long" else entry - 10.0
    return Trade(
        signal_time=signal_time,
        entry_time=signal_time + 1,
        entry_price=entry,
        direction=direction,
        sl_price=sl,
        tp_price=tp,
        fee_pct=0.0,
    )


def _make_sweep_row(
    tp_r: float = 2.0,
    long_trades: list[Trade] | None = None,
    short_trades: list[Trade] | None = None,
    overfit: bool = False,
) -> SweepRow:
    is_result = _make_result()
    oos_result = _make_result(long_trades=long_trades, short_trades=short_trades)
    return SweepRow(
        params={"tp_r": tp_r},
        is_result=is_result,
        oos_result=oos_result,
        is_score=1.0,
        oos_score=0.5,
        decay=0.5,
        overfit=overfit,
    )


# ---------------------------------------------------------------------------
# SweepRow directional properties
# ---------------------------------------------------------------------------


class TestSweepRowDirectional:
    def test_long_oos_avg_r_computed_from_oos_result(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long", 2.0), _win("long", 2.0)],
            short_trades=[_loss("short")],
        )
        assert row.long_oos_avg_r is not None
        assert row.long_oos_avg_r > 0

    def test_short_oos_avg_r_computed_from_oos_result(self) -> None:
        row = _make_sweep_row(
            long_trades=[_loss("long")],
            short_trades=[_win("short", 3.0), _win("short", 3.0)],
        )
        assert row.short_oos_avg_r is not None
        assert row.short_oos_avg_r > 0

    def test_long_oos_n_counts_long_closed_trades(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long"), _loss("long"), _win("long")])
        assert row.long_oos_n == 3

    def test_short_oos_n_counts_short_closed_trades(self) -> None:
        row = _make_sweep_row(short_trades=[_win("short"), _win("short")])
        assert row.short_oos_n == 2

    def test_no_trades_returns_none_avg_r(self) -> None:
        row = _make_sweep_row()
        assert row.long_oos_avg_r is None
        assert row.short_oos_avg_r is None
        assert row.long_oos_n == 0
        assert row.short_oos_n == 0


# ---------------------------------------------------------------------------
# _directional_split_hint
# ---------------------------------------------------------------------------


class TestDirectionalSplitHint:
    def test_returns_empty_when_no_trades(self) -> None:
        row = _make_sweep_row()
        assert _directional_split_hint(row) == ""

    def test_returns_empty_when_insufficient_trades(self) -> None:
        # Only 2 long trades — below threshold of 3
        row = _make_sweep_row(
            long_trades=[_win("long"), _win("long")],
            short_trades=[_win("short"), _win("short"), _win("short")],
        )
        assert _directional_split_hint(row) == ""

    def test_returns_empty_when_delta_below_threshold(self) -> None:
        """If long and short OOS avg_r are very close, no hint."""
        row = _make_sweep_row(
            long_trades=[_win("long", 2.0)] * 5,
            short_trades=[_win("short", 2.0)] * 5,
        )
        # Both are the same avg_r — delta = 0, below 0.1 threshold
        assert _directional_split_hint(row) == ""

    def test_returns_hint_when_delta_large(self) -> None:
        """Longs winning at 3R, shorts losing: hint should fire."""
        row = _make_sweep_row(
            long_trades=[_win("long", 3.0)] * 5,
            short_trades=[_loss("short")] * 5,
        )
        hint = _directional_split_hint(row)
        assert "↕" in hint
        assert "↑" in hint
        assert "↓" in hint

    def test_hint_identifies_worse_direction(self) -> None:
        """When longs underperform, hint says 'consider tp_r_long override'."""
        row = _make_sweep_row(
            long_trades=[_loss("long")] * 5,
            short_trades=[_win("short", 3.0)] * 5,
        )
        hint = _directional_split_hint(row)
        assert "tp_r_long" in hint

    def test_hint_identifies_worse_short(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long", 3.0)] * 5,
            short_trades=[_loss("short")] * 5,
        )
        hint = _directional_split_hint(row)
        assert "tp_r_short" in hint


# ---------------------------------------------------------------------------
# format_sweep_results — directional columns present
# ---------------------------------------------------------------------------


class TestFormatSweepResultsDirectional:
    def test_header_contains_directional_columns(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long")] * 3,
            short_trades=[_win("short")] * 3,
        )
        output = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "↑OOS" in output
        assert "↓OOS" in output

    def test_directional_split_hint_shown_in_recommendation(self) -> None:
        row = _make_sweep_row(
            long_trades=[_loss("long")] * 5,
            short_trades=[_win("short", 3.0)] * 5,
        )
        output = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "↕" in output

    def test_no_hint_when_directions_agree(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long", 2.0)] * 5,
            short_trades=[_win("short", 2.0)] * 5,
        )
        output = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "↕" not in output

    def test_empty_rows_returns_no_results(self) -> None:
        assert format_sweep_results([], "fvg", "BTCUSDT", "4h") == "  No results."


# ---------------------------------------------------------------------------
# AuditRow directional fields
# ---------------------------------------------------------------------------


class TestAuditRowDirectional:
    def _make_audit_row(
        self,
        long_oos: float | None = None,
        short_oos: float | None = None,
        long_n: int = 0,
        short_n: int = 0,
        verdict: str = "good",
    ) -> AuditRow:
        return AuditRow(
            strategy="bos",
            best_is_avg_r=0.5,
            best_oos_avg_r=0.3,
            best_tp_r=2.0,
            oos_trades=long_n + short_n,
            is_trades=10,
            verdict=verdict,
            best_long_oos_avg_r=long_oos,
            best_short_oos_avg_r=short_oos,
            long_oos_n=long_n,
            short_oos_n=short_n,
        )

    def test_fields_default_to_none_and_zero(self) -> None:
        row = AuditRow(
            strategy="bos",
            best_is_avg_r=0.5,
            best_oos_avg_r=0.3,
            best_tp_r=2.0,
            oos_trades=5,
            is_trades=10,
            verdict="good",
        )
        assert row.best_long_oos_avg_r is None
        assert row.best_short_oos_avg_r is None
        assert row.long_oos_n == 0
        assert row.short_oos_n == 0

    def test_format_audit_shows_directional_columns(self) -> None:
        row = self._make_audit_row(long_oos=0.4, short_oos=0.1, long_n=8, short_n=6)
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "↑OOS" in output
        assert "↓OOS" in output

    def test_split_candidates_shown_when_delta_large(self) -> None:
        """When |long - short| >= 0.1 and n >= 3 each, show split candidate section."""
        row = self._make_audit_row(long_oos=0.5, short_oos=0.1, long_n=5, short_n=4)
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "Directional split candidates" in output
        assert "bos" in output

    def test_no_split_candidates_when_delta_small(self) -> None:
        row = self._make_audit_row(long_oos=0.3, short_oos=0.25, long_n=5, short_n=5)
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "Directional split candidates" not in output

    def test_no_split_candidates_when_insufficient_n(self) -> None:
        row = self._make_audit_row(long_oos=0.5, short_oos=0.1, long_n=2, short_n=5)
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "Directional split candidates" not in output

    def test_no_split_candidates_for_no_edge_strategies(self) -> None:
        row = self._make_audit_row(
            long_oos=0.5, short_oos=0.1, long_n=5, short_n=5, verdict="no_edge"
        )
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "Directional split candidates" not in output


class TestAtrFloorForwarding:
    """F9 wiring: workers must forward atr_sl_multiplier/atr_sl_floor to run_backtest."""

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["open_time", "open", "high", "low", "close", "volume"]
        )

    @staticmethod
    def _empty_result() -> BacktestResult:
        return BacktestResult(symbol="BTCUSDT", timeframe="1h", strategy="bos")

    def test_sweep_grid_worker_forwards_floor_flags(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
            captured.append(kwargs)
            return self._empty_result()

        with patch("analytics.param_sweep.run_backtest", side_effect=fake_run_backtest):
            _sweep_grid_worker(
                params={"tp_r": 2.5},
                ohlcv_is=self._empty_df(),
                signals_is=self._empty_df(),
                ohlcv_oos=self._empty_df(),
                signals_oos=self._empty_df(),
                symbol="BTCUSDT",
                timeframe="1h",
                strategy="bos",
                fee_pct=0.0005,
                is_min=1,
                atr_sl_multiplier=2.5,
                atr_sl_floor=True,
            )

        assert len(captured) == 2  # IS + OOS
        for kwargs in captured:
            assert kwargs["atr_sl_multiplier"] == 2.5
            assert kwargs["atr_sl_floor"] is True
            assert kwargs["tp_r"] == 2.5

    def test_sweep_grid_worker_defaults_floor_off(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
            captured.append(kwargs)
            return self._empty_result()

        with patch("analytics.param_sweep.run_backtest", side_effect=fake_run_backtest):
            _sweep_grid_worker(
                params={"tp_r": 2.0},
                ohlcv_is=self._empty_df(),
                signals_is=self._empty_df(),
                ohlcv_oos=self._empty_df(),
                signals_oos=self._empty_df(),
                symbol="BTCUSDT",
                timeframe="1h",
                strategy="bos",
                fee_pct=0.0005,
                is_min=1,
            )

        assert len(captured) == 2
        for kwargs in captured:
            assert kwargs["atr_sl_multiplier"] is None
            assert kwargs["atr_sl_floor"] is False

    def test_audit_strategy_worker_forwards_floor_flags(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
            captured.append(kwargs)
            return self._empty_result()

        with patch("analytics.param_sweep.run_backtest", side_effect=fake_run_backtest):
            _audit_strategy_worker(
                strat="bos",
                signals_is=self._empty_df(),
                signals_oos=self._empty_df(),
                ohlcv_is=self._empty_df(),
                ohlcv_oos=self._empty_df(),
                symbol="BTCUSDT",
                timeframe="1h",
                tp_values=[1.0, 2.0],
                is_min=1,
                fee_pct=0.0005,
                atr_sl_multiplier=2.0,
                atr_sl_floor=True,
            )

        # 2 tp_values × (IS + OOS) = 4 calls
        assert len(captured) == 4
        for kwargs in captured:
            assert kwargs["atr_sl_multiplier"] == 2.0
            assert kwargs["atr_sl_floor"] is True


class TestLiveParityForwarding:
    """Task 1: _sweep_grid_worker must thread the live-parity inputs through to
    both IS + OOS run_backtest calls so WFO cells can replay the live gate stack.
    """

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["open_time", "open", "high", "low", "close", "volume"]
        )

    @staticmethod
    def _empty_result() -> BacktestResult:
        return BacktestResult(symbol="BTCUSDT", timeframe="1d", strategy="eqh_eql")

    def test_sweep_grid_worker_forwards_live_parity_inputs(self) -> None:
        from analytics.backtest.live_parity_config import LiveParityConfig

        lp = LiveParityConfig(
            enabled=True,
            regime=True,
            direction_filter=True,
            f8_htf_ema=True,
            adr_bias=True,
            cooldown=True,
        )
        regime = pd.Series(["trend"], index=[_BASE_TIME])
        slope = {("4h", 50, 3): pd.Series([0.01], index=[_BASE_TIME])}
        captured: list[dict[str, Any]] = []

        def fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
            captured.append(kwargs)
            return self._empty_result()

        with patch("analytics.param_sweep.run_backtest", side_effect=fake_run_backtest):
            _sweep_grid_worker(
                params={"tp_r": 2.0},
                ohlcv_is=self._empty_df(),
                signals_is=self._empty_df(),
                ohlcv_oos=self._empty_df(),
                signals_oos=self._empty_df(),
                symbol="AAPL",
                timeframe="1d",
                strategy="eqh_eql",
                fee_pct=0.0,
                is_min=1,
                live_parity=lp,
                bias_cfg=None,
                regime_series=regime,
                strategy_params=None,
                htf_slope_series_by_anchor=slope,
            )

        assert len(captured) == 2  # IS + OOS
        for kwargs in captured:
            assert kwargs["live_parity"] is lp
            assert kwargs["regime_series"] is regime
            assert kwargs["htf_slope_series_by_anchor"] is slope

    def test_sweep_grid_worker_defaults_live_parity_off(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
            captured.append(kwargs)
            return self._empty_result()

        with patch("analytics.param_sweep.run_backtest", side_effect=fake_run_backtest):
            _sweep_grid_worker(
                params={"tp_r": 2.0},
                ohlcv_is=self._empty_df(),
                signals_is=self._empty_df(),
                ohlcv_oos=self._empty_df(),
                signals_oos=self._empty_df(),
                symbol="AAPL",
                timeframe="1d",
                strategy="eqh_eql",
                fee_pct=0.0,
                is_min=1,
            )

        assert len(captured) == 2
        for kwargs in captured:
            assert kwargs["live_parity"] is None
            assert kwargs["bias_cfg"] is None
            assert kwargs["regime_series"] is None
            assert kwargs["strategy_params"] is None
            assert kwargs["htf_slope_series_by_anchor"] is None


# ---------------------------------------------------------------------------
# Overfit-controls wiring (Phase 0.3a/b — DSR + PBO)
# ---------------------------------------------------------------------------


class TestOverfitStatsWiring:
    def test_sweep_row_carries_overfit_stats(self) -> None:
        from analytics.backtest.stats_overfit import OverfitStats

        row = SweepRow(
            params={"tp_r": 2.0},
            is_result=_make_result(),
            oos_result=_make_result(),
            is_score=0.0,
            oos_score=0.0,
            decay=float("nan"),
            overfit=False,
            overfit_stats=OverfitStats(0.3, 0.2, 0.6, n_trials=9, pbo=0.4),
        )
        assert row.overfit_stats is not None
        assert row.overfit_stats.deflated_sharpe == 0.6
        assert row.overfit_stats.n_trials == 9

    def test_sweep_row_overfit_stats_defaults_none(self) -> None:
        # existing constructors that omit overfit_stats stay valid
        assert _make_sweep_row().overfit_stats is None

    def test_format_renders_dsr_and_pbo(self) -> None:
        from analytics.backtest.stats_overfit import OverfitStats

        wins = [_win("long", r=3.0) for _ in range(8)]
        res = _make_result(long_trades=wins)
        row = SweepRow(
            params={"tp_r": 2.0},
            is_result=res,
            oos_result=res,
            is_score=1.0,
            oos_score=1.0,
            decay=1.0,
            overfit=False,
            overfit_stats=OverfitStats(0.5, 0.4, 0.7, n_trials=9, pbo=0.33),
        )
        out = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "Deflated Sharpe" in out
        assert "PBO" in out
        assert "N=9" in out

    def test_pbo_is_order_independent(self) -> None:
        # PBO must be reproducible regardless of the order rows arrive in from
        # the process pool (deterministic column ordering inside the matrix).
        from analytics.param_sweep import _attach_overfit_stats

        def _rows() -> list[SweepRow]:
            out: list[SweepRow] = []
            for i in range(6):
                wins = [_win("long", r=1.0 + i)] * (3 + i)
                losses = [_loss("long")] * 2
                out.append(
                    SweepRow(
                        params={"tp_r": 1.0 + 0.5 * i},
                        is_result=_make_result(long_trades=wins + losses),
                        oos_result=_make_result(long_trades=wins + losses),
                        is_score=1.0,
                        oos_score=1.0,
                        decay=1.0,
                        overfit=False,
                    )
                )
            return out

        forward = _rows()
        reverse = list(reversed(_rows()))
        _attach_overfit_stats(forward)
        _attach_overfit_stats(reverse)
        assert forward[0].overfit_stats is not None
        assert reverse[0].overfit_stats is not None
        assert forward[0].overfit_stats.pbo == reverse[0].overfit_stats.pbo

    def test_format_renders_na_for_nan_pbo(self) -> None:
        from analytics.backtest.stats_overfit import OverfitStats

        wins = [_win("long", r=3.0) for _ in range(8)]
        res = _make_result(long_trades=wins)
        row = SweepRow(
            params={"tp_r": 2.0},
            is_result=res,
            oos_result=res,
            is_score=1.0,
            oos_score=1.0,
            decay=1.0,
            overfit=False,
            overfit_stats=OverfitStats(0.5, 0.4, 0.7, n_trials=1, pbo=float("nan")),
        )
        out = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "n/a" in out


# ---------------------------------------------------------------------------
# _dedup_trades + _row_from_results (Phase 0.3c CV building blocks)
# ---------------------------------------------------------------------------


class TestDedupTrades:
    def test_keeps_distinct_signal_times(self) -> None:
        a = _win("long", signal_time=_BASE_TIME + 1)
        b = _win("long", signal_time=_BASE_TIME + 2)
        assert _dedup_trades([a, b]) == [a, b]

    def test_keeps_both_directions_at_same_signal_time(self) -> None:
        a, b = _win("long"), _win("short")
        assert _dedup_trades([a, b]) == [a, b]

    def test_collapses_duplicate_resolved_instances(self) -> None:
        assert len(_dedup_trades([_win("long"), _win("long")])) == 1

    def test_prefers_resolved_over_earlier_open(self) -> None:
        o, w = _open_trade("long"), _win("long")
        assert _dedup_trades([o, w]) == [w]

    def test_keeps_resolved_over_later_open(self) -> None:
        w, o = _win("long"), _open_trade("long")
        assert _dedup_trades([w, o]) == [w]

    def test_preserves_first_appearance_order(self) -> None:
        t3 = _win("long", signal_time=_BASE_TIME + 3)
        t1 = _win("long", signal_time=_BASE_TIME + 1)
        t2 = _win("long", signal_time=_BASE_TIME + 2)
        assert _dedup_trades([t3, t1, t2]) == [t3, t1, t2]


class TestRowFromResults:
    def test_scores_and_decay_match_score_function(self) -> None:
        bt_is = _make_result(long_trades=[_win("long", 2.0), _win("long", 2.0)])
        bt_oos = _make_result(long_trades=[_win("long", 2.0)])
        row = _row_from_results({"tp_r": 2.0}, bt_is, bt_oos, is_min=1)
        assert row.is_score == pytest.approx(_score(bt_is, 1))
        assert row.oos_score == pytest.approx(_score(bt_oos, 1))
        assert row.decay == pytest.approx(row.oos_score / row.is_score)
        assert not row.overfit

    def test_negative_oos_flags_overfit(self) -> None:
        bt_is = _make_result(long_trades=[_win("long", 2.0), _win("long", 2.0)])
        bt_oos = _make_result(long_trades=[_loss("long")])
        row = _row_from_results({"tp_r": 2.0}, bt_is, bt_oos, is_min=1)
        assert row.overfit

    def test_zero_is_score_gives_nan_decay(self) -> None:
        bt_is = _make_result()  # no trades → score 0
        bt_oos = _make_result(long_trades=[_win("long", 2.0)])
        row = _row_from_results({"tp_r": 2.0}, bt_is, bt_oos, is_min=1)
        assert math.isnan(row.decay)
        assert (
            not row.overfit
        )  # positive OOS + NaN decay → not flagged (legacy semantics)


class TestSweepGridWorkerCv:
    def test_pools_test_folds_and_dedups_train_segments(self) -> None:
        ohlcv = pd.DataFrame({"open_time": [0]})
        sigs = pd.DataFrame({"open_time": [0]})
        seg = (ohlcv, sigs)
        folds = [
            FoldSplit(
                fold=0, test_ohlcv=ohlcv, test_signals=sigs, train_segments=(seg,)
            ),
            FoldSplit(
                fold=1, test_ohlcv=ohlcv, test_signals=sigs, train_segments=(seg,)
            ),
        ]
        dup_open = _open_trade("long")  # censored instance of a train signal
        dup_resolved = _win("long")  # resolved instance of the same (signal_time, dir)
        test_a = _win("long", signal_time=_BASE_TIME + 10)
        test_b = _loss("long", signal_time=_BASE_TIME + 20)
        # worker call order: fold0 train seg → fold0 test → fold1 train seg → fold1 test
        results = [
            _make_result(long_trades=[dup_open]),
            _make_result(long_trades=[test_a]),
            _make_result(long_trades=[dup_resolved]),
            _make_result(long_trades=[test_b]),
        ]
        with patch(
            "analytics.param_sweep.run_backtest", side_effect=results
        ) as mock_bt:
            row = _sweep_grid_worker_cv(
                {"tp_r": 2.0},
                folds,
                "AAPL",
                "1d",
                "bos",
                0.0,
                1,
            )
        assert mock_bt.call_count == 4
        assert row.params == {"tp_r": 2.0}
        assert row.is_result.symbol == "AAPL"
        # open+resolved duplicates collapse to the single resolved instance
        assert row.is_trades == 1
        assert row.is_result.trades == [dup_resolved]
        # disjoint test folds pool directly — no dedup
        assert row.oos_trades == 2


class TestRunParamSweepCv:
    """Integration: real engine + ProcessPoolExecutor; DB + detection mocked out.

    Synthetic series: every candle opens at 100 with high 103 / low 99, so a
    long with sl_pct=0.02 (sl 98) and tp_r ≤ 1.0 (tp ≤ 102) wins on its entry
    bar and never touches the SL — fully deterministic fills.
    """

    def _ohlcv(self, n: int = 60) -> pd.DataFrame:
        return _make_ohlcv(
            [
                _candle(_BASE_TIME + i * 3_600_000, 100.0, 103.0, 99.0, 100.0)
                for i in range(n)
            ]
        )

    def _signals(self, rows: list[int]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "open_time": [_BASE_TIME + r * 3_600_000 for r in rows],
                "direction": ["long"] * len(rows),
                "reason": ["test"] * len(rows),
            }
        )

    @patch("analytics.param_sweep.detect_signals_for_strategy")
    @patch("analytics.param_sweep.get_ohlcv")
    def test_purged_cv_pools_train_and_test_folds(
        self, mock_ohlcv: Any, mock_detect: Any
    ) -> None:
        mock_ohlcv.return_value = self._ohlcv()
        mock_detect.return_value = self._signals([5, 12, 19, 26, 33, 40, 46, 54])
        rows = run_param_sweep(
            conn=MagicMock(),
            strategy="bos",
            symbol="AAPL",
            timeframe="1h",
            days=30,
            param_ranges=[ParamRange("tp_r", [1.0])],
            wfo_split=0.7,
            min_trades=2,
            fee_pct=0.0,
            top_n=5,
            cv=CvConfig(mode="purged", n_folds=5, embargo_bars=1),
        )
        assert len(rows) == 1
        row = rows[0]
        # ≥6 of the 8 signals resolve inside their fold/segment (a signal whose
        # entry or resolution bar falls past a slice end is censored — fine)
        assert row.oos_trades >= 6
        assert row.oos_win_rate == 1.0
        assert row.is_trades >= 6
        assert row.is_win_rate == 1.0
        assert not row.overfit
        assert row.overfit_stats is not None

    @patch("analytics.param_sweep.detect_signals_for_strategy")
    @patch("analytics.param_sweep.get_ohlcv")
    def test_contiguous_cv_config_matches_cv_none(
        self, mock_ohlcv: Any, mock_detect: Any
    ) -> None:
        mock_ohlcv.return_value = self._ohlcv()
        mock_detect.return_value = self._signals([5, 12, 19, 26, 33, 40, 46, 54])
        kwargs: dict[str, Any] = {
            "strategy": "bos",
            "symbol": "AAPL",
            "timeframe": "1h",
            "days": 30,
            "param_ranges": [ParamRange("tp_r", [0.5, 1.0])],
            "wfo_split": 0.7,
            "min_trades": 2,
            "fee_pct": 0.0,
            "top_n": 5,
        }
        legacy = run_param_sweep(conn=MagicMock(), **kwargs)
        contiguous = run_param_sweep(
            conn=MagicMock(), cv=CvConfig(mode="contiguous"), **kwargs
        )
        assert [r.params for r in legacy] == [r.params for r in contiguous]
        assert [r.is_score for r in legacy] == [r.is_score for r in contiguous]
        assert [r.oos_score for r in legacy] == [r.oos_score for r in contiguous]
        assert [r.decay for r in legacy] == [r.decay for r in contiguous]
