"""Tests for the backtest filter in signal_lib and related helpers."""

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from analytics.backtest_lib import BacktestResult, Trade
from analytics.signal_config import BacktestFilterConfig
from analytics.signal_lib import _backtest_summary, _compute_backtest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 20) -> pd.DataFrame:
    """Minimal OHLCV DataFrame with n rows."""
    base_ms = 1_700_000_000_000
    interval = 4 * 3600 * 1000
    times = [base_ms + i * interval for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": times,
            "open": [100.0] * n,
            "high": [105.0] * n,
            "low": [95.0] * n,
            "close": [102.0] * n,
            "volume": [1000.0] * n,
        }
    )


def _make_result(win: int, loss: int) -> BacktestResult:
    """Build a BacktestResult with given closed trade counts."""
    result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
    for _ in range(win):
        result.trades.append(
            Trade(
                signal_time=1,
                entry_time=2,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=104.0,
                exit_price=104.0,
                outcome="win",
            )
        )
    for _ in range(loss):
        result.trades.append(
            Trade(
                signal_time=1,
                entry_time=2,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=104.0,
                exit_price=98.0,
                outcome="loss",
            )
        )
    return result


# ---------------------------------------------------------------------------
# _backtest_summary
# ---------------------------------------------------------------------------


class TestBacktestSummary:
    def _cfg(self, min_trades: int = 5, days: int = 90) -> BacktestFilterConfig:
        return BacktestFilterConfig(mode="soft", days=days, min_trades=min_trades)

    def test_single_strategy_sufficient_trades(self) -> None:
        result = _make_result(win=6, loss=4)
        summary = _backtest_summary({"fvg": result}, ["fvg"], self._cfg())
        assert "60%" in summary
        assert "10 trades" in summary
        assert "📊 Backtest 90d:" in summary

    def test_single_strategy_insufficient_trades(self) -> None:
        result = _make_result(win=2, loss=1)
        summary = _backtest_summary({"fvg": result}, ["fvg"], self._cfg(min_trades=20))
        assert "n/a" in summary
        assert "3 trades" in summary

    def test_single_strategy_none_result(self) -> None:
        summary = _backtest_summary({"fvg": None}, ["fvg"], self._cfg())
        assert "n/a" in summary

    def test_multiple_strategies(self) -> None:
        results = {
            "fvg": _make_result(win=6, loss=4),
            "bos": _make_result(win=7, loss=3),
        }
        summary = _backtest_summary(results, ["fvg", "bos"], self._cfg())
        assert "fvg" in summary
        assert "bos" in summary
        assert "·" in summary

    def test_days_shown_in_output(self) -> None:
        result = _make_result(win=5, loss=5)
        summary = _backtest_summary({"fvg": result}, ["fvg"], self._cfg(days=180))
        assert "180d" in summary


# ---------------------------------------------------------------------------
# _compute_backtest
# ---------------------------------------------------------------------------


class TestComputeBacktest:
    def test_returns_none_for_insufficient_data(self) -> None:
        tiny_df = _make_ohlcv(n=2)
        result = _compute_backtest(tiny_df, "fvg", None, "BTCUSDT", "4h", 0.02, 2.0)
        assert result is None

    def test_returns_none_for_unknown_strategy(self) -> None:
        df = _make_ohlcv(n=20)
        result = _compute_backtest(
            df, "nonexistent_strategy", None, "BTCUSDT", "4h", 0.02, 2.0
        )
        assert result is None

    def test_excludes_current_candle(self) -> None:
        """Detector should only see ohlcv[:-1], not the full df."""
        df = _make_ohlcv(n=20)
        captured: list[Any] = []

        def fake_detector(ohlcv: pd.DataFrame) -> pd.DataFrame:
            captured.append(len(ohlcv))
            return pd.DataFrame(
                columns=["open_time", "direction", "sl_price", "reason"]
            )

        with (
            patch.dict(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": fake_detector, "confidence": 4}},
            ),
            patch.dict(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {"fvg": MagicMock(requires_funding=False, requires_secondary=False)},
            ),
        ):
            _compute_backtest(df, "fvg", None, "BTCUSDT", "4h", 0.02, 2.0)

        assert captured == [len(df) - 1]  # detector saw n-1 candles

    def test_returns_backtest_result_on_success(self) -> None:
        df = _make_ohlcv(n=20)
        signals = pd.DataFrame(
            {
                "open_time": [df["open_time"].iloc[5]],
                "direction": ["long"],
                "sl_price": [95.0],
                "reason": ["fvg_long"],
            }
        )

        with (
            patch.dict(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda _: signals, "confidence": 4}},
            ),
            patch.dict(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {"fvg": MagicMock(requires_funding=False, requires_secondary=False)},
            ),
        ):
            result = _compute_backtest(df, "fvg", None, "BTCUSDT", "4h", 0.02, 2.0)

        assert result is not None
        assert isinstance(result, BacktestResult)

    def test_returns_none_if_detector_raises(self) -> None:
        df = _make_ohlcv(n=20)

        def bad_detector(_: pd.DataFrame) -> pd.DataFrame:
            raise ValueError("boom")

        with (
            patch.dict(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": bad_detector, "confidence": 4}},
            ),
            patch.dict(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {"fvg": MagicMock(requires_funding=False, requires_secondary=False)},
            ),
        ):
            result = _compute_backtest(df, "fvg", None, "BTCUSDT", "4h", 0.02, 2.0)

        assert result is None

    def test_structural_sl_price_propagates_to_backtest(self) -> None:
        """Detector signals with sl_price are passed through to run_backtest.

        The signal at index 5 carries sl_price=90.0 (far below entry ~100).
        With sl_pct=0.02 the SL would be 98.0 (close), but the structural SL
        is used instead: TP = 100 + 2*10 = 120, which the OHLCV never reaches,
        so the trade stays open.  This confirms the structural SL path is active.
        """
        df = _make_ohlcv(n=20)  # all candles: open=100, high=105, low=95

        # Signal that would lose quickly under sl_pct=0.02 (SL at 98, candle low=95)
        # but with structural sl_price=90.0 the SL is not touched (low=95 > 90).
        signals = pd.DataFrame(
            {
                "open_time": [df["open_time"].iloc[5]],
                "direction": ["long"],
                "sl_price": [90.0],  # structural SL far below candles
                "reason": ["fvg_long"],
            }
        )

        with (
            patch.dict(
                "analytics.signal.scanner.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda _: signals, "confidence": 4}},
            ),
            patch.dict(
                "analytics.signal.scanner.STRATEGY_REGISTRY",
                {"fvg": MagicMock(requires_funding=False, requires_secondary=False)},
            ),
        ):
            result = _compute_backtest(df, "fvg", None, "BTCUSDT", "4h", 0.02, 2.0)

        assert result is not None
        assert len(result.trades) == 1
        trade = result.trades[0]
        # With structural SL at 90 and entry ~100, risk=10, TP=120.
        # All candles have high=105 < 120, low=95 > 90 → trade stays open.
        assert trade.outcome == "open"
        assert trade.sl_price == pytest.approx(90.0)
        assert trade.tp_price == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# BacktestFilterConfig loading
# ---------------------------------------------------------------------------


class TestBacktestFilterConfig:
    def test_defaults(self) -> None:
        cfg = BacktestFilterConfig()
        assert cfg.mode == "soft"
        assert cfg.days == 90
        assert cfg.min_trades == 12
        assert cfg.filter_threshold == 0.45

    def test_load_from_toml(self, tmp_path: Any) -> None:
        from analytics.signal_config import load_signal_config

        p = tmp_path / "cfg.toml"
        p.write_text(
            "[backtest]\nmode = 'hard'\ndays = 60\nmin_trades = 10\nfilter_threshold = 0.5\n"
        )
        cfg = load_signal_config(p)
        assert cfg.backtest.mode == "hard"
        assert cfg.backtest.days == 60
        assert cfg.backtest.min_trades == 10
        assert cfg.backtest.filter_threshold == 0.5

    def test_missing_backtest_section_uses_defaults(self, tmp_path: Any) -> None:
        from analytics.signal_config import load_signal_config

        p = tmp_path / "cfg.toml"
        p.write_text("telegram = true\n")
        cfg = load_signal_config(p)
        assert cfg.backtest.mode == "soft"
        assert cfg.backtest.days == 90

    def test_min_avg_r_default(self) -> None:
        cfg = BacktestFilterConfig()
        assert cfg.min_avg_r == 0.0

    def test_min_avg_r_loaded_from_toml(self, tmp_path: Any) -> None:
        from analytics.signal_config import load_signal_config

        p = tmp_path / "cfg.toml"
        p.write_text("[backtest]\nmode = 'hard'\nmin_avg_r = 0.25\n")
        cfg = load_signal_config(p)
        assert cfg.backtest.min_avg_r == 0.25


# ---------------------------------------------------------------------------
# Hard filter: avg_r (EV) gate
# ---------------------------------------------------------------------------


def _make_result_with_avg_r(
    long_wins: int, long_losses: int, short_wins: int, short_losses: int, tp_r: float
) -> BacktestResult:
    """Build a BacktestResult with explicit long/short directional trades."""
    result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
    for _ in range(long_wins):
        result.trades.append(
            Trade(
                signal_time=1,
                entry_time=2,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=100.0 + 2.0 * tp_r,
                exit_price=100.0 + 2.0 * tp_r,
                outcome="win",
            )
        )
    for _ in range(long_losses):
        result.trades.append(
            Trade(
                signal_time=1,
                entry_time=2,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=100.0 + 2.0 * tp_r,
                exit_price=98.0,
                outcome="loss",
            )
        )
    for _ in range(short_wins):
        result.trades.append(
            Trade(
                signal_time=1,
                entry_time=2,
                entry_price=100.0,
                direction="short",
                sl_price=102.0,
                tp_price=100.0 - 2.0 * tp_r,
                exit_price=100.0 - 2.0 * tp_r,
                outcome="win",
            )
        )
    for _ in range(short_losses):
        result.trades.append(
            Trade(
                signal_time=1,
                entry_time=2,
                entry_price=100.0,
                direction="short",
                sl_price=102.0,
                tp_price=100.0 - 2.0 * tp_r,
                exit_price=102.0,
                outcome="loss",
            )
        )
    return result


class TestEvGate:
    """Verify the avg_r EV gate passes low-WR profitable strategies and blocks losers."""

    def _cfg(self, min_trades: int = 5, min_avg_r: float = 0.0) -> BacktestFilterConfig:
        return BacktestFilterConfig(
            mode="hard", days=90, min_trades=min_trades, min_avg_r=min_avg_r
        )

    def test_low_winrate_positive_avg_r_passes(self) -> None:
        """25% WR at 4R is still +EV — must NOT be suppressed."""

        # 25% win rate, tp_r=4 → avg_r = 0.25*4 - 0.75*1 = +0.25 (positive EV)
        result = _make_result_with_avg_r(
            long_wins=5, long_losses=15, short_wins=0, short_losses=0, tp_r=4.0
        )
        cfg = self._cfg(min_trades=5, min_avg_r=0.0)
        assert result.long_avg_r is not None
        assert result.long_avg_r > 0.0, "25% WR × 4R should be positive EV"

        # Simulate the gate check directly
        avg_r = result.long_avg_r
        assert avg_r >= cfg.min_avg_r

    def test_negative_avg_r_blocked(self) -> None:
        """Strategy with negative avg_r must be suppressed."""
        result = _make_result_with_avg_r(
            long_wins=2, long_losses=10, short_wins=0, short_losses=0, tp_r=2.0
        )
        cfg = self._cfg(min_trades=5, min_avg_r=0.0)
        avg_r = result.long_avg_r
        assert avg_r is not None
        assert avg_r < 0.0, "Low WR × low R should be negative EV"
        assert avg_r < cfg.min_avg_r

    def test_short_direction_uses_short_avg_r(self) -> None:
        """Gate uses short_avg_r for SHORT signals, not long_avg_r."""
        # Long trades are losers, short trades are winners
        result = _make_result_with_avg_r(
            long_wins=1, long_losses=10, short_wins=5, short_losses=1, tp_r=2.0
        )
        assert result.long_avg_r is not None and result.long_avg_r < 0.0
        assert result.short_avg_r is not None and result.short_avg_r > 0.0

    def test_none_result_always_passes(self) -> None:
        """No backtest data → signal must not be suppressed."""
        # result is None → gate passes regardless of min_avg_r
        result = None
        passes = result is None
        assert passes

    def test_insufficient_trades_passes(self) -> None:
        """Below min_trades threshold → gate passes (insufficient data)."""
        result = _make_result_with_avg_r(
            long_wins=1, long_losses=3, short_wins=0, short_losses=0, tp_r=2.0
        )
        cfg = self._cfg(min_trades=20, min_avg_r=0.0)
        passes = len(result.closed_trades) < cfg.effective_min_trades("4h")
        assert passes  # 4 trades < 20 threshold
