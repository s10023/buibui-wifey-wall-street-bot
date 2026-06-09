"""Tests for analytics/backtest_lib.py."""

import numpy as np
import pandas as pd
import pytest

from analytics.backtest_lib import (
    BacktestResult,
    Trade,
    format_result,
    format_seasonality,
    run_backtest,
)
from tests.conftest import _candle, _make_ohlcv

_BASE_TIME = 1_700_000_000_000


def _make_signals(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open_time", "direction", "reason"])


def _make_signals_with_sl(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Signals DataFrame that includes a per-row sl_price column."""
    return pd.DataFrame(rows, columns=["open_time", "direction", "reason", "sl_price"])


# ---------------------------------------------------------------------------
# Trade.pnl_r property
# ---------------------------------------------------------------------------


class TestTradePnlR:
    def test_long_win_returns_positive(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,  # risk = 2
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
        )
        assert t.pnl_r == pytest.approx(2.0)

    def test_long_loss_returns_negative(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_price=98.0,
            exit_time=2,
            outcome="loss",
        )
        assert t.pnl_r == pytest.approx(-1.0)

    def test_short_win_returns_positive(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="short",
            sl_price=102.0,  # risk = 2
            tp_price=96.0,
            exit_price=96.0,
            exit_time=2,
            outcome="win",
        )
        assert t.pnl_r == pytest.approx(2.0)

    def test_open_trade_returns_none(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
        )
        assert t.pnl_r is None

    def test_zero_risk_returns_none(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=100.0,  # sl == entry → zero risk
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
        )
        assert t.pnl_r is None


# ---------------------------------------------------------------------------
# BacktestResult properties
# ---------------------------------------------------------------------------


class TestBacktestResult:
    def _make_result(self, outcomes: list[str]) -> BacktestResult:
        trades = []
        for i, outcome in enumerate(outcomes):
            ep = 100.0
            sl = 98.0
            tp = 104.0
            exit_p = tp if outcome == "win" else sl if outcome == "loss" else None
            trades.append(
                Trade(
                    signal_time=i,
                    entry_time=i + 1,
                    entry_price=ep,
                    direction="long",
                    sl_price=sl,
                    tp_price=tp,
                    exit_time=i + 2 if outcome != "open" else None,
                    exit_price=exit_p,
                    outcome=outcome,
                )
            )
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        result.trades = trades
        return result

    def test_win_rate_two_wins_one_loss(self) -> None:
        result = self._make_result(["win", "win", "loss"])
        assert result.win_rate == pytest.approx(2 / 3)

    def test_win_rate_zero_on_no_closed_trades(self) -> None:
        result = self._make_result(["open", "open"])
        assert result.win_rate == 0.0

    def test_avg_r_with_wins_and_losses(self) -> None:
        result = self._make_result(["win", "loss"])
        # win = +2R, loss = -1R → avg = 0.5
        assert result.avg_r == pytest.approx(0.5)

    def test_total_r(self) -> None:
        result = self._make_result(["win", "win", "loss"])
        # 2R + 2R - 1R = 3R
        assert result.total_r == pytest.approx(3.0)

    def test_max_drawdown_r_all_wins(self) -> None:
        result = self._make_result(["win", "win", "win"])
        assert result.max_drawdown_r == 0.0

    def test_max_drawdown_r_with_losses(self) -> None:
        result = self._make_result(["win", "loss", "loss", "win"])
        # cumR: 2, 1, 0, 2 → peak=2, min after peak=0 → dd=2
        assert result.max_drawdown_r == pytest.approx(2.0)

    def test_closed_trades_excludes_open(self) -> None:
        result = self._make_result(["win", "open", "loss"])
        assert len(result.closed_trades) == 2

    def test_win_loss_counts(self) -> None:
        result = self._make_result(["win", "win", "loss", "open"])
        assert result.win_count == 2
        assert result.loss_count == 1


# ---------------------------------------------------------------------------
# BacktestResult — long/short split properties
# ---------------------------------------------------------------------------


class TestBacktestResultDirectionSplit:
    def _make_mixed_result(self) -> BacktestResult:
        """3 longs (2W 1L) + 2 shorts (1W 1L) + 1 open long."""

        def _trade(i: int, direction: str, outcome: str) -> Trade:
            ep = 100.0
            if direction == "long":
                sl, tp = 98.0, 104.0  # risk=2, TP=+2R
                exit_p = tp if outcome == "win" else sl if outcome == "loss" else None
            else:
                sl, tp = 102.0, 96.0  # short: sl above entry, tp below; risk=2, TP=+2R
                exit_p = tp if outcome == "win" else sl if outcome == "loss" else None
            return Trade(
                signal_time=i,
                entry_time=i + 1,
                entry_price=ep,
                direction=direction,
                sl_price=sl,
                tp_price=tp,
                exit_time=i + 2 if outcome != "open" else None,
                exit_price=exit_p,
                outcome=outcome,
            )

        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        result.trades = [
            _trade(0, "long", "win"),
            _trade(1, "long", "win"),
            _trade(2, "long", "loss"),
            _trade(3, "long", "open"),  # excluded from closed
            _trade(4, "short", "win"),
            _trade(5, "short", "loss"),
        ]
        return result

    def test_long_closed_trades_count(self) -> None:
        result = self._make_mixed_result()
        assert len(result.long_closed_trades) == 3

    def test_short_closed_trades_count(self) -> None:
        result = self._make_mixed_result()
        assert len(result.short_closed_trades) == 2

    def test_long_win_count(self) -> None:
        result = self._make_mixed_result()
        assert result.long_win_count == 2

    def test_long_win_rate(self) -> None:
        result = self._make_mixed_result()
        assert result.long_win_rate == pytest.approx(2 / 3)

    def test_short_win_count(self) -> None:
        result = self._make_mixed_result()
        assert result.short_win_count == 1

    def test_short_win_rate(self) -> None:
        result = self._make_mixed_result()
        assert result.short_win_rate == pytest.approx(0.5)

    def test_long_avg_r(self) -> None:
        result = self._make_mixed_result()
        # 2 wins (+2R each) + 1 loss (−1R) = avg 1R
        assert result.long_avg_r == pytest.approx(1.0)

    def test_short_avg_r(self) -> None:
        result = self._make_mixed_result()
        # 1 win (+2R) + 1 loss (−1R) = avg 0.5R
        assert result.short_avg_r == pytest.approx(0.5)

    def test_long_win_rate_none_when_no_longs(self) -> None:
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        result.trades = [
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
        ]
        assert result.long_win_rate is None
        assert result.long_avg_r is None

    def test_short_win_rate_none_when_no_shorts(self) -> None:
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        result.trades = [
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
        ]
        assert result.short_win_rate is None
        assert result.short_avg_r is None


# ---------------------------------------------------------------------------
# run_backtest
# ---------------------------------------------------------------------------


class TestRunBacktest:
    def test_empty_signals_returns_empty_result(self) -> None:
        ohlcv = _make_ohlcv(
            [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(10)]
        )
        signals = _make_signals([])
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")
        assert len(result.trades) == 0

    def test_long_trade_hits_tp(self) -> None:
        # Signal on candle 0, entry on candle 1 at open=100
        # sl=2% → 98, tp=2R → 104
        # Candle 1: safe (low=99 > sl=98, high=103 < tp=104)
        # Candle 2: high=106 → hits TP
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),  # entry at open=100; safe
                _candle(_BASE_TIME + 2, 101, 106, 99, 105),  # high=106 ≥ tp=104 → win
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert len(result.trades) == 1
        assert result.trades[0].outcome == "win"
        assert result.trades[0].exit_price == pytest.approx(104.0)

    def test_long_trade_hits_sl(self) -> None:
        # Signal on candle 0, entry on candle 1 at open=100
        # sl=2% → 98, tp=2R → 104
        # Candle 2: low=96 → hits SL
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 103, 97, 101),  # entry
                _candle(_BASE_TIME + 2, 99, 100, 96, 97),  # low=96 ≤ sl=98 → loss
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "loss"
        assert result.trades[0].exit_price == pytest.approx(98.0)

    def test_short_trade_hits_tp(self) -> None:
        # Entry at open=100, sl=2% → 102, tp=2R → 96
        # Candle 2: low=95 → hits TP
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 101, 97, 98),  # entry
                _candle(_BASE_TIME + 2, 99, 100, 95, 96),  # low=95 ≤ tp=96 → win
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "short", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "win"
        assert result.trades[0].exit_price == pytest.approx(96.0)

    def test_short_trade_hits_sl(self) -> None:
        # Entry at open=100, sl=2% → 102
        # Candle 2: high=104 → hits SL
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 101, 97, 98),  # entry
                _candle(_BASE_TIME + 2, 100, 104, 96, 103),  # high=104 ≥ sl=102 → loss
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "short", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "loss"

    def test_trade_open_at_end_of_data(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 101, 99, 100),  # entry, no exit
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "open"

    def test_signal_at_last_candle_is_skipped(self) -> None:
        ohlcv = _make_ohlcv([_candle(_BASE_TIME + 0, 100, 105, 95, 102)])
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")
        assert len(result.trades) == 0

    def test_result_metadata(self) -> None:
        ohlcv = _make_ohlcv(
            [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        )
        signals = _make_signals([])
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")
        assert result.symbol == "BTCUSDT"
        assert result.timeframe == "4h"
        assert result.strategy == "fvg"


# ---------------------------------------------------------------------------
# run_backtest — per-signal structural sl_price
# ---------------------------------------------------------------------------


class TestRunBacktestStructuralSL:
    """Tests for the per-signal sl_price path in run_backtest."""

    def test_long_sl_price_used_directly(self) -> None:
        """When signals have sl_price, long SL triggers at the structural level."""
        # Entry at candle 1 open=100; structural SL at 95 (not 98 from 2% sl_pct)
        # Candle 2: low=94 → hits structural SL at 95
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 97, 101),  # entry open=100
                _candle(_BASE_TIME + 2, 98, 99, 94, 96),  # low=94 ≤ sl=95 → loss
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 95.0,
                }
            ]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02)
        assert len(result.trades) == 1
        assert result.trades[0].outcome == "loss"
        assert result.trades[0].sl_price == pytest.approx(95.0)
        assert result.trades[0].exit_price == pytest.approx(95.0)

    def test_long_sl_price_tp_computed_from_risk_distance(self) -> None:
        """For long with structural SL, TP = entry + tp_r * abs(entry - sl_price)."""
        # Entry open=100, structural SL=95 → risk=5 → TP at 100 + 2*5 = 110
        # Candle 2: high=111 → hits TP at 110
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),  # entry open=100
                _candle(_BASE_TIME + 2, 101, 111, 100, 110),  # high=111 ≥ tp=110 → win
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 95.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "win"
        assert result.trades[0].tp_price == pytest.approx(110.0)
        assert result.trades[0].exit_price == pytest.approx(110.0)

    def test_short_sl_price_used_directly(self) -> None:
        """When signals have sl_price, short SL triggers at the structural level."""
        # Entry at candle 1 open=100; structural SL at 106 (not 102 from 2% sl_pct)
        # Candle 2: high=107 → hits structural SL at 106
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 101, 98, 99),  # entry open=100
                _candle(_BASE_TIME + 2, 101, 107, 99, 106),  # high=107 ≥ sl=106 → loss
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "short",
                    "reason": "test",
                    "sl_price": 106.0,
                }
            ]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02)
        assert result.trades[0].outcome == "loss"
        assert result.trades[0].sl_price == pytest.approx(106.0)
        assert result.trades[0].exit_price == pytest.approx(106.0)

    def test_short_sl_price_tp_computed_from_risk_distance(self) -> None:
        """For short with structural SL, TP = entry - tp_r * abs(entry - sl_price)."""
        # Entry open=100, structural SL=106 → risk=6 → TP at 100 - 2*6 = 88
        # Candle 2: low=87 → hits TP at 88
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 101, 98, 99),  # entry open=100
                _candle(_BASE_TIME + 2, 95, 96, 87, 88),  # low=87 ≤ tp=88 → win
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "short",
                    "reason": "test",
                    "sl_price": 106.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "win"
        assert result.trades[0].tp_price == pytest.approx(88.0)
        assert result.trades[0].exit_price == pytest.approx(88.0)

    def test_no_sl_price_column_falls_back_to_sl_pct(self) -> None:
        """Signals without sl_price column use sl_pct fallback (backward compat)."""
        # Entry open=100, sl_pct=0.02 → SL at 98
        # Candle 2: low=96 ≤ 98 → loss
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 103, 97, 101),
                _candle(_BASE_TIME + 2, 99, 100, 96, 97),
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02)
        assert result.trades[0].outcome == "loss"
        assert result.trades[0].sl_price == pytest.approx(98.0)


# ---------------------------------------------------------------------------
# min_sl_pct — structural SL widening
# ---------------------------------------------------------------------------


class TestMinSlPct:
    def test_tight_sl_widened_for_long(self) -> None:
        """min_sl_pct widens a structural SL that lands too close to entry."""
        # Entry open=100, structural sl=99.9 (0.1% away), min_sl_pct=0.01 → SL at 99
        # Candle 2: low=98 → hits widened SL at 99 → loss
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 99, 102),
                _candle(_BASE_TIME + 1, 100, 101, 99, 100),
                _candle(_BASE_TIME + 2, 99, 100, 97, 98),
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 99.9,
                }
            ]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", min_sl_pct=0.01)
        assert result.trades[0].sl_price == pytest.approx(99.0)
        assert result.trades[0].outcome == "loss"

    def test_tight_sl_widened_for_short(self) -> None:
        """min_sl_pct widens a structural SL above entry for a short."""
        # Entry open=100, structural sl=100.1 (0.1% above), min_sl_pct=0.01 → SL at 101
        # Candle 2: high=102 → hits widened SL at 101 → loss
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 99, 102),
                _candle(_BASE_TIME + 1, 100, 101, 99, 100),
                _candle(_BASE_TIME + 2, 99, 102, 97, 98),
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "short",
                    "reason": "test",
                    "sl_price": 100.1,
                }
            ]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", min_sl_pct=0.01)
        assert result.trades[0].sl_price == pytest.approx(101.0)
        assert result.trades[0].outcome == "loss"

    def test_wide_sl_not_affected(self) -> None:
        """Structural SL already wider than min_sl_pct is left unchanged."""
        # Entry=100, sl=95 (5% away), min_sl_pct=0.01 → sl stays at 95
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 99, 102),
                _candle(_BASE_TIME + 1, 100, 110, 90, 108),
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 95.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", tp_r=2.0, min_sl_pct=0.01
        )
        assert result.trades[0].sl_price == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# fee_pct — Trade.pnl_r with fees
# ---------------------------------------------------------------------------


class TestTradePnlRWithFees:
    def test_fee_pct_zero_unchanged(self) -> None:
        """fee_pct=0.0 must reproduce original pnl_r behaviour (win = +2R)."""
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,  # risk = 2
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
            fee_pct=0.0,
        )
        assert t.pnl_r == pytest.approx(2.0)

    def test_fee_pct_reduces_winning_trade_pnl(self) -> None:
        """With fees, a winning trade's pnl_r is lower than the fee-free case."""
        # entry=100, sl=98 → risk=2, exit=104 (raw +2R)
        # fee_drag = 2 * 0.0005 * 100 / 2 = 0.05R
        # net pnl_r = 2.0 - 0.05 = 1.95
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
            fee_pct=0.0005,
        )
        assert t.pnl_r == pytest.approx(1.95)

    def test_fee_pct_increases_losing_trade_loss(self) -> None:
        """With fees, a losing trade loses more than -1R."""
        # entry=100, sl=98 → risk=2, exit=98 (raw -1R)
        # fee_drag = 2 * 0.0005 * 100 / 2 = 0.05R
        # net pnl_r = -1.0 - 0.05 = -1.05
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_price=98.0,
            exit_time=2,
            outcome="loss",
            fee_pct=0.0005,
        )
        assert t.pnl_r == pytest.approx(-1.05)

    def test_run_backtest_fee_pct_stored_on_result(self) -> None:
        """fee_pct passed to run_backtest is stored on BacktestResult."""
        ohlcv = _make_ohlcv(
            [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        )
        signals = _make_signals([])
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", fee_pct=0.0005)
        assert result.fee_pct == pytest.approx(0.0005)

    def test_run_backtest_with_fee_pct_lowers_avg_r(self) -> None:
        """A winning trade's avg_r is lower when fee_pct > 0 vs fee_pct = 0."""
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),  # entry open=100
                _candle(_BASE_TIME + 2, 101, 106, 99, 105),  # high=106 ≥ tp=104 → win
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        no_fee = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0, fee_pct=0.0
        )
        with_fee = run_backtest(
            ohlcv,
            signals,
            "BTCUSDT",
            "4h",
            "fvg",
            sl_pct=0.02,
            tp_r=2.0,
            fee_pct=0.0005,
        )
        assert no_fee.avg_r == pytest.approx(2.0)
        assert with_fee.avg_r is not None
        assert with_fee.avg_r < no_fee.avg_r


# ---------------------------------------------------------------------------
# format_result
# ---------------------------------------------------------------------------


class TestFormatResult:
    def test_contains_symbol_and_strategy(self) -> None:
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        output = format_result(result)
        assert "BTCUSDT" in output
        assert "fvg" in output

    def test_shows_zero_trades(self) -> None:
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        output = format_result(result)
        assert "0" in output


# ---------------------------------------------------------------------------
# format_seasonality
# ---------------------------------------------------------------------------


class TestFormatSeasonality:
    def test_empty_stats_returns_message(self) -> None:
        output = format_seasonality(pd.DataFrame())
        assert "No seasonality data" in output

    def test_contains_day_of_week_section(self) -> None:
        rows = [
            {
                "period_type": "day_of_week",
                "period_value": i,
                "avg_return_pct": 0.5,
                "win_rate": 0.6,
                "count": 10,
            }
            for i in range(7)
        ]
        stats = pd.DataFrame(rows)
        output = format_seasonality(stats)
        assert "Day Of Week" in output
        assert "Mon" in output


class TestIsLowVolume:
    """Tests for the _is_low_volume helper."""

    def test_low_volume_returns_true(self) -> None:
        from analytics.backtest_lib import _is_low_volume

        rows = [
            _candle(_BASE_TIME + i * 1000, 100, 110, 90, 105, volume=100.0)
            for i in range(20)
        ]
        # Signal candle at idx=20 with volume well below 1.5× mean (100)
        rows.append(_candle(_BASE_TIME + 20 * 1000, 100, 110, 90, 105, volume=10.0))
        ohlcv = _make_ohlcv(rows)
        assert _is_low_volume(ohlcv, 20) is True

    def test_high_volume_returns_false(self) -> None:
        from analytics.backtest_lib import _is_low_volume

        rows = [
            _candle(_BASE_TIME + i * 1000, 100, 110, 90, 105, volume=100.0)
            for i in range(20)
        ]
        rows.append(_candle(_BASE_TIME + 20 * 1000, 100, 110, 90, 105, volume=300.0))
        ohlcv = _make_ohlcv(rows)
        assert _is_low_volume(ohlcv, 20) is False

    def test_no_volume_column_returns_false(self) -> None:
        from analytics.backtest_lib import _is_low_volume

        rows = [_candle(_BASE_TIME + i * 1000, 100, 110, 90, 105) for i in range(5)]
        ohlcv = _make_ohlcv(rows).drop(columns=["volume"])
        assert _is_low_volume(ohlcv, 4) is False

    def test_idx_zero_returns_false(self) -> None:
        from analytics.backtest_lib import _is_low_volume

        rows = [_candle(_BASE_TIME, 100, 110, 90, 105, volume=1.0)]
        ohlcv = _make_ohlcv(rows)
        assert _is_low_volume(ohlcv, 0) is False


class TestLowVolumeTradeTracking:
    """run_backtest sets low_volume on each Trade from OHLCV volume."""

    def _make_signals(self, open_time: int, direction: str = "long") -> pd.DataFrame:
        return pd.DataFrame(
            [{"open_time": open_time, "direction": direction, "reason": "test"}]
        )

    def test_trade_marked_low_volume_when_signal_candle_is_quiet(self) -> None:
        # 21 candles: first 20 at volume=100, signal candle at volume=10 (low)
        rows = [
            _candle(_BASE_TIME + i * 1000, 100, 110, 90, 105, volume=100.0)
            for i in range(20)
        ]
        rows.append(_candle(_BASE_TIME + 20 * 1000, 100, 110, 90, 105, volume=10.0))
        # Entry candle (candle after signal)
        rows.append(_candle(_BASE_TIME + 21 * 1000, 100, 200, 90, 150, volume=100.0))
        ohlcv = _make_ohlcv(rows)
        signals = self._make_signals(_BASE_TIME + 20 * 1000)
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "test")
        assert len(result.trades) == 1
        assert result.trades[0].low_volume is True

    def test_trade_marked_normal_volume(self) -> None:
        rows = [
            _candle(_BASE_TIME + i * 1000, 100, 110, 90, 105, volume=100.0)
            for i in range(20)
        ]
        rows.append(_candle(_BASE_TIME + 20 * 1000, 100, 110, 90, 105, volume=300.0))
        rows.append(_candle(_BASE_TIME + 21 * 1000, 100, 200, 90, 150, volume=100.0))
        ohlcv = _make_ohlcv(rows)
        signals = self._make_signals(_BASE_TIME + 20 * 1000)
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "test")
        assert len(result.trades) == 1
        assert result.trades[0].low_volume is False


class TestVolumeSplitProperties:
    """BacktestResult splits closed trades by low_volume flag."""

    def _make_trade(self, pnl_r: float, low_volume: bool) -> Trade:
        risk = 1.0
        entry = 100.0
        if pnl_r >= 0:
            exit_price = entry + pnl_r * risk
            outcome = "win"
        else:
            exit_price = entry + pnl_r * risk
            outcome = "loss"
        t = Trade(
            signal_time=_BASE_TIME,
            entry_time=_BASE_TIME + 1000,
            entry_price=entry,
            direction="long",
            sl_price=entry - risk,
            tp_price=entry + 2 * risk,
            exit_time=_BASE_TIME + 2000,
            exit_price=exit_price,
            outcome=outcome,
            low_volume=low_volume,
        )
        return t

    def test_low_vol_and_normal_split(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="test")
        result.trades = [
            self._make_trade(0.5, low_volume=True),
            self._make_trade(-0.3, low_volume=True),
            self._make_trade(1.0, low_volume=False),
        ]
        assert len(result.low_vol_closed_trades) == 2
        assert len(result.normal_vol_closed_trades) == 1

    def test_low_vol_avg_r(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="test")
        result.trades = [
            self._make_trade(0.5, low_volume=True),
            self._make_trade(-0.5, low_volume=True),
        ]
        avg = result.low_vol_avg_r
        assert avg is not None
        assert abs(avg) < 0.01  # ~0.0

    def test_normal_vol_avg_r_none_when_no_normal_trades(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="test")
        result.trades = [self._make_trade(0.5, low_volume=True)]
        assert result.normal_vol_avg_r is None


class TestFormatVolumeSplit:
    """format_volume_split produces a readable table."""

    def test_contains_header(self) -> None:
        from analytics.backtest_lib import format_volume_split

        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="engulfing")
        output = format_volume_split([result])
        assert "Volume Impact" in output

    def test_shows_strategy_name(self) -> None:
        from analytics.backtest_lib import format_volume_split

        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="pin_bar")
        output = format_volume_split([result])
        assert "pin_bar" in output

    def test_shows_delta_explanation(self) -> None:
        from analytics.backtest_lib import format_volume_split

        output = format_volume_split([])
        assert "Delta" in output


class TestDirectionalVolumeCrosstab:
    """BacktestResult directional × volume cross-tab properties."""

    def _make_trade(
        self,
        direction: str,
        low_volume: bool = False,
        volume_spike: bool = False,
        pnl_r: float = 0.5,
    ) -> Trade:
        entry = 100.0
        risk = 1.0
        exit_price = (
            entry + pnl_r * risk if direction == "long" else entry - pnl_r * risk
        )
        outcome = "win" if pnl_r >= 0 else "loss"
        return Trade(
            signal_time=_BASE_TIME,
            entry_time=_BASE_TIME + 1000,
            entry_price=entry,
            direction=direction,
            sl_price=entry - risk if direction == "long" else entry + risk,
            tp_price=entry + 2 * risk if direction == "long" else entry - 2 * risk,
            exit_time=_BASE_TIME + 2000,
            exit_price=exit_price,
            outcome=outcome,
            low_volume=low_volume,
            volume_spike=volume_spike,
        )

    def test_long_low_vol_trades(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="test")
        result.trades = [
            self._make_trade("long", low_volume=True),
            self._make_trade("short", low_volume=True),
            self._make_trade("long", low_volume=False),
        ]
        assert len(result.long_low_vol_closed_trades) == 1
        assert len(result.short_low_vol_closed_trades) == 1

    def test_long_normal_vol_trades(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="test")
        result.trades = [
            self._make_trade("long"),
            self._make_trade("long", volume_spike=True),
            self._make_trade("short"),
        ]
        assert len(result.long_normal_vol_closed_trades) == 1
        assert len(result.long_spike_vol_closed_trades) == 1
        assert len(result.short_normal_vol_closed_trades) == 1

    def test_long_spike_avg_r(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="test")
        result.trades = [
            self._make_trade("long", volume_spike=True, pnl_r=1.0),
            self._make_trade("short", volume_spike=True, pnl_r=0.5),
        ]
        assert result.long_spike_vol_avg_r is not None
        assert abs(result.long_spike_vol_avg_r - 1.0) < 0.01
        assert result.short_spike_vol_avg_r is not None
        assert abs(result.short_spike_vol_avg_r - 0.5) < 0.01

    def test_short_low_vol_avg_r_none_when_empty(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="test")
        result.trades = [self._make_trade("long", low_volume=True)]
        assert result.short_low_vol_avg_r is None


class TestFormatDirectionalVolumeSplit:
    """format_directional_volume_split produces a readable directional table."""

    def test_contains_header(self) -> None:
        from analytics.backtest_lib import format_directional_volume_split

        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="engulfing")
        output = format_directional_volume_split([result])
        assert "Directional Volume Split" in output

    def test_shows_strategy_and_arrows(self) -> None:
        from analytics.backtest_lib import format_directional_volume_split

        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="pin_bar")
        output = format_directional_volume_split([result])
        assert "pin_bar" in output
        assert "↑" in output
        assert "↓" in output

    def test_empty_results(self) -> None:
        from analytics.backtest_lib import format_directional_volume_split

        output = format_directional_volume_split([])
        assert "Directional Volume Split" in output


class TestDurationProperties:
    """BacktestResult duration stats from entry/exit times."""

    def _make_trade_with_duration(self, duration_h: float) -> Trade:
        entry_time = _BASE_TIME
        exit_time = int(entry_time + duration_h * 3_600_000)
        return Trade(
            signal_time=entry_time,
            entry_time=entry_time,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_time=exit_time,
            exit_price=104.0,
            outcome="win",
        )

    def test_durations_h_computed_correctly(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="bos")
        result.trades = [self._make_trade_with_duration(4.0)]
        assert abs(result.durations_h[0] - 4.0) < 0.01

    def test_avg_duration_h(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="bos")
        result.trades = [
            self._make_trade_with_duration(2.0),
            self._make_trade_with_duration(6.0),
        ]
        assert abs(result.avg_duration_h - 4.0) < 0.01  # type: ignore[operator]

    def test_median_duration_h_odd(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="bos")
        result.trades = [
            self._make_trade_with_duration(1.0),
            self._make_trade_with_duration(3.0),
            self._make_trade_with_duration(8.0),
        ]
        assert abs(result.median_duration_h - 3.0) < 0.01  # type: ignore[operator]

    def test_median_duration_h_even(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="bos")
        result.trades = [
            self._make_trade_with_duration(2.0),
            self._make_trade_with_duration(4.0),
        ]
        assert abs(result.median_duration_h - 3.0) < 0.01  # type: ignore[operator]

    def test_no_closed_trades_returns_none(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="bos")
        assert result.avg_duration_h is None
        assert result.median_duration_h is None

    def test_long_median_duration_h(self) -> None:
        long_trade = self._make_trade_with_duration(8.0)
        short_trade = Trade(
            signal_time=_BASE_TIME,
            entry_time=_BASE_TIME,
            entry_price=100.0,
            direction="short",
            sl_price=102.0,
            tp_price=96.0,
            exit_time=int(_BASE_TIME + 2.0 * 3_600_000),
            exit_price=96.0,
            outcome="win",
        )
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="bos")
        result.trades = [long_trade, short_trade]
        assert abs(result.long_median_duration_h - 8.0) < 0.01  # type: ignore[operator]

    def test_short_median_duration_h(self) -> None:
        long_trade = self._make_trade_with_duration(8.0)
        short_trade = Trade(
            signal_time=_BASE_TIME,
            entry_time=_BASE_TIME,
            entry_price=100.0,
            direction="short",
            sl_price=102.0,
            tp_price=96.0,
            exit_time=int(_BASE_TIME + 2.0 * 3_600_000),
            exit_price=96.0,
            outcome="win",
        )
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="bos")
        result.trades = [long_trade, short_trade]
        assert abs(result.short_median_duration_h - 2.0) < 0.01  # type: ignore[operator]

    def test_directional_median_duration_none_when_no_trades(self) -> None:
        result = BacktestResult(symbol="BTC", timeframe="4h", strategy="bos")
        assert result.long_median_duration_h is None
        assert result.short_median_duration_h is None


class TestFormatDurationTable:
    """format_duration_table produces a readable table."""

    def _make_result(self, strategy: str, duration_h: float) -> BacktestResult:
        entry_time = _BASE_TIME
        exit_time = int(entry_time + duration_h * 3_600_000)
        t = Trade(
            signal_time=entry_time,
            entry_time=entry_time,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_time=exit_time,
            exit_price=104.0,
            outcome="win",
        )
        r = BacktestResult(symbol="BTC", timeframe="4h", strategy=strategy)
        r.trades = [t]
        return r

    def test_contains_header(self) -> None:
        from analytics.backtest_lib import format_duration_table

        output = format_duration_table([self._make_result("bos", 4.0)])
        assert "Trade Duration" in output

    def test_shows_strategy(self) -> None:
        from analytics.backtest_lib import format_duration_table

        output = format_duration_table([self._make_result("engulfing", 2.0)])
        assert "engulfing" in output

    def test_hours_format_under_24h(self) -> None:
        from analytics.backtest_lib import format_duration_table

        output = format_duration_table([self._make_result("bos", 6.0)])
        assert "6.0h" in output

    def test_days_format_over_24h(self) -> None:
        from analytics.backtest_lib import format_duration_table

        output = format_duration_table([self._make_result("trend_day", 48.0)])
        assert "2.0d" in output


class TestFormatTpSweepTable:
    """format_tp_sweep_table produces a comparison table."""

    def _make_result(self, strategy: str, tp_r: float) -> BacktestResult:
        risk = 1.0
        entry = 100.0
        t = Trade(
            signal_time=_BASE_TIME,
            entry_time=_BASE_TIME + 1000,
            entry_price=entry,
            direction="long",
            sl_price=entry - risk,
            tp_price=entry + tp_r * risk,
            exit_time=_BASE_TIME + 2000,
            exit_price=entry + tp_r * risk,
            outcome="win",
        )
        r = BacktestResult(symbol="BTC", timeframe="4h", strategy=strategy)
        r.trades = [t]
        return r

    def test_contains_header(self) -> None:
        from analytics.backtest_lib import format_tp_sweep_table

        results_by_tp = {
            1.0: [self._make_result("bos", 1.0)],
            2.0: [self._make_result("bos", 2.0)],
        }
        output = format_tp_sweep_table(results_by_tp)
        assert "TP Ratio" in output

    def test_shows_tp_columns(self) -> None:
        from analytics.backtest_lib import format_tp_sweep_table

        results_by_tp = {
            1.0: [self._make_result("bos", 1.0)],
            2.0: [self._make_result("bos", 2.0)],
        }
        output = format_tp_sweep_table(results_by_tp)
        assert "1.0R" in output
        assert "2.0R" in output

    def test_shows_strategy(self) -> None:
        from analytics.backtest_lib import format_tp_sweep_table

        results_by_tp = {2.0: [self._make_result("pin_bar", 2.0)]}
        output = format_tp_sweep_table(results_by_tp)
        assert "pin_bar" in output


# ---------------------------------------------------------------------------
# _compute_atr14 helper
# ---------------------------------------------------------------------------


class TestComputeAtr14:
    def test_returns_none_when_idx_is_zero(self) -> None:
        from analytics.backtest_lib import _compute_atr14

        h = np.array([110.0, 105.0])
        lo = np.array([90.0, 95.0])
        c = np.array([100.0, 100.0])
        assert _compute_atr14(h, lo, c, 0) is None

    def test_simple_two_candle_case(self) -> None:
        from analytics.backtest_lib import _compute_atr14

        # candle 0: high=110, low=90, close=100
        # candle 1: high=108, low=92, close=100
        # TR at candle 1: max(108-92, |108-100|, |92-100|) = max(16, 8, 8) = 16
        h = np.array([110.0, 108.0])
        lo = np.array([90.0, 92.0])
        c = np.array([100.0, 100.0])
        result = _compute_atr14(h, lo, c, 1)
        assert result == pytest.approx(16.0)

    def test_uses_up_to_14_candles(self) -> None:
        from analytics.backtest_lib import _compute_atr14

        # Build 20 candles with constant range of 10 (high-low=10, no gap)
        n = 20
        h = np.array([105.0] * n)
        lo = np.array([95.0] * n)
        c = np.array([100.0] * n)
        # ATR14 should be 10.0 (constant range)
        result = _compute_atr14(h, lo, c, n - 1)
        assert result == pytest.approx(10.0)

    def test_returns_none_when_atr_is_zero(self) -> None:
        from analytics.backtest_lib import _compute_atr14

        # All candles identical → TR = 0
        h = np.array([100.0, 100.0, 100.0])
        lo = np.array([100.0, 100.0, 100.0])
        c = np.array([100.0, 100.0, 100.0])
        assert _compute_atr14(h, lo, c, 2) is None


# ---------------------------------------------------------------------------
# ATR-based SL in run_backtest
# ---------------------------------------------------------------------------


class TestATRBasedSL:
    """Tests for the atr_sl_multiplier path in run_backtest."""

    def _make_ohlcv_flat(self, n: int = 20, base: float = 100.0) -> pd.DataFrame:
        """n candles with constant range of 10 (ATR14 = 10)."""
        interval = 3_600_000
        rows = [
            _candle(
                _BASE_TIME + i * interval,
                base,
                base + 5.0,
                base - 5.0,
                base,
            )
            for i in range(n)
        ]
        return _make_ohlcv(rows)

    def test_atr_sl_used_instead_of_sl_pct(self) -> None:
        """ATR-based SL distance should equal atr_sl_multiplier × ATR14."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        # Signal at candle 10 (well after ATR has enough data)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals(
            [{"open_time": sig_time, "direction": "long", "reason": "x"}]
        )
        # ATR14 = 10 (constant high-low range). Multiplier = 1.5 → SL dist = 15
        # Entry at next candle open = 1000. Expected sl = 1000 - 15 = 985.
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,  # would give sl_dist = 1 if used — very different
            tp_r=2.0,
            atr_sl_multiplier=1.5,
        )
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.sl_price == pytest.approx(1000.0 - 1.5 * 10.0, rel=1e-6)

    def test_atr_sl_short_direction(self) -> None:
        """ATR SL for short should be above entry."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals(
            [{"open_time": sig_time, "direction": "short", "reason": "x"}]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            atr_sl_multiplier=1.5,
        )
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.sl_price == pytest.approx(1000.0 + 1.5 * 10.0, rel=1e-6)

    def test_fallback_to_sl_pct_when_atr_unavailable(self) -> None:
        """Signal at candle 0 has no prior close — must fall back to sl_pct."""
        ohlcv = self._make_ohlcv_flat(n=5, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[0])
        sigs = _make_signals(
            [{"open_time": sig_time, "direction": "long", "reason": "x"}]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.02,  # 2% → sl = 1000 * (1 - 0.02) = 980
            tp_r=2.0,
            atr_sl_multiplier=1.5,
        )
        assert len(result.trades) == 1
        trade = result.trades[0]
        # Entry is at candle 1 open (1000). SL via sl_pct = 980.
        assert trade.sl_price == pytest.approx(1000.0 * (1.0 - 0.02), rel=1e-6)

    def test_none_multiplier_uses_sl_pct(self) -> None:
        """When atr_sl_multiplier is None, sl_pct path is taken unchanged."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals(
            [{"open_time": sig_time, "direction": "long", "reason": "x"}]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.02,
            tp_r=2.0,
            atr_sl_multiplier=None,
        )
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.sl_price == pytest.approx(1000.0 * (1.0 - 0.02), rel=1e-6)

    def test_atr_sl_respects_min_sl_pct(self) -> None:
        """ATR SL distance is floored by min_sl_pct when ATR would be tighter."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals(
            [{"open_time": sig_time, "direction": "long", "reason": "x"}]
        )
        # ATR=10, multiplier=0.5 → sl_dist=5, but min_sl_pct=0.02 → min_dist=20
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            atr_sl_multiplier=0.5,
            min_sl_pct=0.02,
        )
        assert len(result.trades) == 1
        trade = result.trades[0]
        # Floor kicks in: sl = 1000 - 20 = 980
        assert trade.sl_price == pytest.approx(1000.0 - 1000.0 * 0.02, rel=1e-6)

    def test_structural_sl_takes_priority_over_atr(self) -> None:
        """Per-signal sl_price (structural) overrides ATR SL mode."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals_with_sl(
            [
                {
                    "open_time": sig_time,
                    "direction": "long",
                    "reason": "x",
                    "sl_price": 950.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            atr_sl_multiplier=1.5,
        )
        assert len(result.trades) == 1
        assert result.trades[0].sl_price == pytest.approx(950.0)


# ---------------------------------------------------------------------------
# ATR SL floor (F9): widen structural SLs when ATR-derived distance is larger
# ---------------------------------------------------------------------------


class TestATRSLFloor:
    """Tests for the atr_sl_floor parameter on the structural SL branch."""

    def _make_ohlcv_flat(self, n: int = 20, base: float = 100.0) -> pd.DataFrame:
        """n candles with constant range of 10 (ATR14 = 10)."""
        interval = 3_600_000
        rows = [
            _candle(
                _BASE_TIME + i * interval,
                base,
                base + 5.0,
                base - 5.0,
                base,
            )
            for i in range(n)
        ]
        return _make_ohlcv(rows)

    def test_floor_off_preserves_structural_sl(self) -> None:
        """Default behaviour: structural sl_price wins even when ATR is wider."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        # Tight structural SL: 998 (2 below entry). ATR14=10, mult=1.5 → atr_dist=15.
        sigs = _make_signals_with_sl(
            [
                {
                    "open_time": sig_time,
                    "direction": "long",
                    "reason": "x",
                    "sl_price": 998.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            atr_sl_multiplier=1.5,
            atr_sl_floor=False,
        )
        assert len(result.trades) == 1
        assert result.trades[0].sl_price == pytest.approx(998.0)

    def test_floor_on_widens_tight_long_sl(self) -> None:
        """Floor on + tight structural: ATR widens sl to entry - atr*mult."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals_with_sl(
            [
                {
                    "open_time": sig_time,
                    "direction": "long",
                    "reason": "x",
                    "sl_price": 998.0,  # structural_dist = 2 < atr_dist = 15
                }
            ]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            atr_sl_multiplier=1.5,
            atr_sl_floor=True,
        )
        assert len(result.trades) == 1
        trade = result.trades[0]
        # entry=1000, atr=10, mult=1.5 → sl=985, tp=1000+2*15=1030
        assert trade.sl_price == pytest.approx(985.0)
        assert trade.tp_price == pytest.approx(1030.0)

    def test_floor_on_widens_tight_short_sl(self) -> None:
        """Floor on + tight short structural: ATR widens sl to entry + atr*mult."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals_with_sl(
            [
                {
                    "open_time": sig_time,
                    "direction": "short",
                    "reason": "x",
                    "sl_price": 1002.0,  # structural_dist = 2 < atr_dist = 15
                }
            ]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            atr_sl_multiplier=1.5,
            atr_sl_floor=True,
        )
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.sl_price == pytest.approx(1015.0)
        assert trade.tp_price == pytest.approx(970.0)

    def test_floor_on_keeps_wide_structural_sl(self) -> None:
        """Floor on + wide structural: structural wins, ATR no-ops."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals_with_sl(
            [
                {
                    "open_time": sig_time,
                    "direction": "long",
                    "reason": "x",
                    "sl_price": 950.0,  # structural_dist = 50 > atr_dist = 15
                }
            ]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            atr_sl_multiplier=1.5,
            atr_sl_floor=True,
        )
        assert len(result.trades) == 1
        assert result.trades[0].sl_price == pytest.approx(950.0)

    def test_floor_on_without_multiplier_is_noop(self) -> None:
        """Floor flag alone (no multiplier set) does nothing."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        sigs = _make_signals_with_sl(
            [
                {
                    "open_time": sig_time,
                    "direction": "long",
                    "reason": "x",
                    "sl_price": 998.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            atr_sl_multiplier=None,
            atr_sl_floor=True,
        )
        assert len(result.trades) == 1
        assert result.trades[0].sl_price == pytest.approx(998.0)

    def test_floor_composes_with_min_sl_pct(self) -> None:
        """min_sl_pct still acts as absolute minimum after ATR floor."""
        ohlcv = self._make_ohlcv_flat(n=20, base=1000.0)
        sig_time = int(ohlcv["open_time"].iloc[10])
        # Tight structural (998). min_sl_pct=0.05 widens to 950 first (dist=50).
        # ATR=10, mult=1.5 → atr_dist=15 < 50 → ATR floor is a no-op.
        sigs = _make_signals_with_sl(
            [
                {
                    "open_time": sig_time,
                    "direction": "long",
                    "reason": "x",
                    "sl_price": 998.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv,
            sigs,
            "BTCUSDT",
            "1h",
            "test",
            sl_pct=0.001,
            tp_r=2.0,
            min_sl_pct=0.05,
            atr_sl_multiplier=1.5,
            atr_sl_floor=True,
        )
        assert len(result.trades) == 1
        # min_sl_pct=0.05 of 1000 = 50 → sl = 950
        assert result.trades[0].sl_price == pytest.approx(950.0)


# ---------------------------------------------------------------------------
# Directional tp_r (Gate 3)
# ---------------------------------------------------------------------------


class TestDirectionalTpR:
    """run_backtest honours tp_r_long / tp_r_short per trade direction."""

    def _make_ohlcv_flat(self, n: int = 5, base: float = 100.0) -> pd.DataFrame:
        return _make_ohlcv(
            [_candle(_BASE_TIME + i, base, base + 10, base - 5, base) for i in range(n)]
        )

    def test_long_uses_tp_r_long(self) -> None:
        """Long trade TP uses tp_r_long when set."""
        # Entry 100, sl=95 (sl_pct=0.05), tp_r=2.0, tp_r_long=1.0
        # Combined tp = 100 + 2.0 * 5 = 110
        # Long tp with tp_r_long = 100 + 1.0 * 5 = 105
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 104, 96, 102),  # signal candle
                _candle(
                    _BASE_TIME + 1, 100, 106, 96, 103
                ),  # entry; high=106 > 105=tp_long → win
                _candle(_BASE_TIME + 2, 100, 103, 96, 102),
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "x"}]
        )
        result = run_backtest(
            ohlcv,
            signals,
            "BTCUSDT",
            "4h",
            "fvg",
            sl_pct=0.05,
            tp_r=2.0,
            tp_r_long=1.0,
        )
        assert len(result.closed_trades) == 1
        assert result.closed_trades[0].outcome == "win"
        # TP distance should reflect tp_r_long=1.0, not tp_r=2.0
        trade = result.closed_trades[0]
        expected_tp = trade.entry_price + 1.0 * (trade.entry_price - trade.sl_price)
        assert trade.tp_price == pytest.approx(expected_tp)

    def test_short_uses_tp_r_short(self) -> None:
        """Short trade TP uses tp_r_short when set."""
        # Entry 100, sl=105 (sl_pct=0.05), tp_r_short=1.0 → tp = 100 - 5 = 95
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 104, 96, 102),  # signal candle
                _candle(
                    _BASE_TIME + 1, 100, 103, 94, 97
                ),  # entry; low=94 < 95=tp_short → win
                _candle(_BASE_TIME + 2, 100, 103, 96, 102),
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "short", "reason": "x"}]
        )
        result = run_backtest(
            ohlcv,
            signals,
            "BTCUSDT",
            "4h",
            "fvg",
            sl_pct=0.05,
            tp_r=2.0,
            tp_r_short=1.0,
        )
        assert len(result.closed_trades) == 1
        assert result.closed_trades[0].outcome == "win"
        trade = result.closed_trades[0]
        expected_tp = trade.entry_price - 1.0 * (trade.sl_price - trade.entry_price)
        assert trade.tp_price == pytest.approx(expected_tp)

    def test_no_directional_override_uses_tp_r(self) -> None:
        """When tp_r_long/short are None, tp_r is used unchanged."""
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 104, 96, 102),
                _candle(_BASE_TIME + 1, 100, 120, 96, 110),  # high far above any TP
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "x"}]
        )
        result_base = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.05, tp_r=2.0
        )
        result_dir = run_backtest(
            ohlcv,
            signals,
            "BTCUSDT",
            "4h",
            "fvg",
            sl_pct=0.05,
            tp_r=2.0,
            tp_r_long=None,
            tp_r_short=None,
        )
        assert result_base.closed_trades[0].tp_price == pytest.approx(
            result_dir.closed_trades[0].tp_price
        )

    def test_long_tp_r_long_does_not_affect_short(self) -> None:
        """tp_r_long only applies to long trades; shorts use tp_r."""
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 104, 96, 102),
                _candle(
                    _BASE_TIME + 1, 100, 103, 85, 90
                ),  # low=85 reaches both tp_r_short candidates
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "short", "reason": "x"}]
        )
        result = run_backtest(
            ohlcv,
            signals,
            "BTCUSDT",
            "4h",
            "fvg",
            sl_pct=0.05,
            tp_r=2.0,
            tp_r_long=0.5,  # should not affect short
        )
        trade = result.closed_trades[0]
        # TP for short = entry - tp_r * sl_dist (tp_r=2.0, not tp_r_long=0.5)
        expected_tp = trade.entry_price - 2.0 * (trade.sl_price - trade.entry_price)
        assert trade.tp_price == pytest.approx(expected_tp)


def test_backtest_result_sharpe_matches_helper() -> None:
    from analytics.backtest.engine import BacktestResult, Trade
    from analytics.backtest.stats_overfit import sharpe_ratio

    trades = [
        Trade(0, 1, 100.0, "long", 99.0, 102.0, exit_time=2, exit_price=102.0, outcome="win"),
        Trade(0, 1, 100.0, "long", 99.0, 102.0, exit_time=2, exit_price=98.0, outcome="loss"),
        Trade(0, 1, 100.0, "long", 99.0, 102.0, exit_time=2, exit_price=101.0, outcome="win"),
    ]
    res = BacktestResult("AAPL", "1d", "bos", trades=trades)
    expected = sharpe_ratio([t.pnl_r for t in res.closed_trades if t.pnl_r is not None])
    assert res.sharpe == expected


def test_backtest_result_sharpe_empty_is_zero() -> None:
    from analytics.backtest.engine import BacktestResult

    assert BacktestResult("AAPL", "1d", "bos").sharpe == 0.0
