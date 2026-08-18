"""Tests for the backtest filter in signal_lib and related helpers."""

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from analytics.backtest_lib import BacktestResult, Trade
from analytics.signal.gates import passes_ev_gate
from analytics.signal_config import BacktestFilterConfig
from analytics.signal_lib import _backtest_summary, _compute_backtest
from analytics.store.backtest_cache import BacktestSnapshot

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
        # "too few to judge", not "n/a": the sample was checked and is thin,
        # which is a different claim from the lookup having failed.
        assert "3 trades — too few to judge" in summary

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
    """Verify the avg_r EV gate passes low-WR profitable strategies and blocks losers.

    Every test here calls the real ``passes_ev_gate``. Before 2026-08-07 the gate
    was a closure inside ``run_scan_cycle`` and therefore unreachable from a test,
    so these tests re-implemented the comparison inline and asserted on their own
    copy. That is why the directional-count defect survived: the old
    ``test_insufficient_trades_passes`` wrote ``len(result.closed_trades)`` — the
    combined count — into the test body, encoding the bug as the expectation.
    """

    def _cfg(
        self,
        min_trades: int = 5,
        min_avg_r: float = 0.0,
        min_avg_r_z: float = 0.0,
    ) -> BacktestFilterConfig:
        """Default z=0.0 keeps the pre-significance tests testing what they name.

        The shipped default is 1.64; the significance tests below set it explicitly.
        """
        return BacktestFilterConfig(
            mode="hard",
            days=90,
            min_trades=min_trades,
            min_avg_r=min_avg_r,
            min_avg_r_z=min_avg_r_z,
        )

    def test_low_winrate_positive_avg_r_passes(self) -> None:
        """25% WR at 4R is still +EV — must NOT be suppressed."""
        # 25% win rate, tp_r=4 → avg_r = 0.25*4 - 0.75*1 = +0.25 (positive EV)
        result = _make_result_with_avg_r(
            long_wins=5, long_losses=15, short_wins=0, short_losses=0, tp_r=4.0
        )
        assert result.long_avg_r is not None and result.long_avg_r > 0.0
        assert passes_ev_gate(
            result,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5),
        )

    def test_negative_avg_r_blocked(self) -> None:
        """Strategy with negative avg_r must be suppressed."""
        result = _make_result_with_avg_r(
            long_wins=2, long_losses=10, short_wins=0, short_losses=0, tp_r=2.0
        )
        assert result.long_avg_r is not None and result.long_avg_r < 0.0
        assert not passes_ev_gate(
            result,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5),
        )

    def test_short_direction_uses_short_avg_r(self) -> None:
        """Gate uses short_avg_r for SHORT signals, not long_avg_r."""
        # Long trades are losers, short trades are winners; both legs are fat
        # enough to be evaluated so the split is the only thing under test.
        result = _make_result_with_avg_r(
            long_wins=1, long_losses=10, short_wins=8, short_losses=1, tp_r=2.0
        )
        cfg = self._cfg(min_trades=5)
        assert not passes_ev_gate(
            result, direction="long", timeframe="4h", backtest_cfg=cfg
        )
        assert passes_ev_gate(
            result, direction="short", timeframe="4h", backtest_cfg=cfg
        )

    def test_none_result_always_passes(self) -> None:
        """No backtest data → signal must not be suppressed."""
        assert passes_ev_gate(
            None, direction="long", timeframe="4h", backtest_cfg=self._cfg()
        )

    def test_insufficient_trades_passes(self) -> None:
        """Below min_trades threshold → gate passes (insufficient data)."""
        result = _make_result_with_avg_r(
            long_wins=1, long_losses=3, short_wins=0, short_losses=0, tp_r=2.0
        )
        assert passes_ev_gate(
            result,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=20),
        )

    # -- regression: the sample-size guard must count the tested direction ----

    def test_thin_long_leg_not_judged_on_short_trades(self) -> None:
        """A long verdict must not rest on the short leg's sample size.

        The defect fixed 2026-08-07: the guard counted BOTH directions, so this
        result (1 long trade, 20 short) cleared ``min_trades=5`` on the combined
        count of 21 and the gate then BLOCKED long on a single trade's avg_r.
        Measured on the live path, 53 of 260 blocked ``signal_watch`` legs and 45
        of 429 on ``weekdays`` were of this shape; 19 and 68 rested on n_dir=1.
        """
        result = _make_result_with_avg_r(
            long_wins=0, long_losses=1, short_wins=10, short_losses=10, tp_r=2.0
        )
        assert len(result.closed_trades) == 21  # combined clears min_trades=5
        assert len(result.long_closed_trades) == 1  # the tested leg does not
        assert result.long_avg_r is not None and result.long_avg_r < 0.0
        assert passes_ev_gate(
            result,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5),
        ), "long leg has 1 trade — the gate must abstain, not block"

    def test_thin_short_leg_not_judged_on_long_trades(self) -> None:
        """Mirror of the above: a short verdict needs short trades."""
        result = _make_result_with_avg_r(
            long_wins=10, long_losses=10, short_wins=0, short_losses=1, tp_r=2.0
        )
        assert len(result.closed_trades) == 21
        assert len(result.short_closed_trades) == 1
        assert result.short_avg_r is not None and result.short_avg_r < 0.0
        assert passes_ev_gate(
            result,
            direction="short",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5),
        ), "short leg has 1 trade — the gate must abstain, not block"

    def test_fat_directional_leg_still_blocks(self) -> None:
        """The fix must not disarm the gate where the evidence IS directional."""
        result = _make_result_with_avg_r(
            long_wins=1, long_losses=19, short_wins=0, short_losses=0, tp_r=2.0
        )
        assert len(result.long_closed_trades) == 20
        assert not passes_ev_gate(
            result,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5),
        )

    def test_snapshot_counts_directionally_too(self) -> None:
        """The cached BacktestSnapshot path takes the same directional count.

        ``bt_results`` holds ``BacktestResult | BacktestSnapshot``; the snapshot
        exposes ``long_closed_trades`` as an ``[None] * n_long`` shim, so only
        its length is meaningful — which is all the guard reads.
        """
        snap = BacktestSnapshot(
            symbol="AAPL",
            timeframe="4h",
            strategy="fvg",
            n_closed=21,
            n_long=1,
            n_short=20,
            r_long_avg=-1.0,
            r_short_avg=0.5,
        )
        assert passes_ev_gate(
            snap,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5),
        ), "snapshot long leg has 1 trade — abstain, not block"

    # -- significance requirement on the block decision (min_avg_r_z) ---------

    def test_negative_but_insignificant_avg_r_is_not_blocked(self) -> None:
        """A shortfall inside the noise must abstain, not block.

        3 wins / 9 losses at tp_r=2 → avg_r −0.25, sd 1.357, SE 0.392, z 0.64.
        Below 1.64, so the gate must not suppress — and must not destroy the
        ledger row, which is the real cost of a wrong block.
        """
        result = _make_result_with_avg_r(
            long_wins=3, long_losses=9, short_wins=0, short_losses=0, tp_r=2.0
        )
        assert result.long_avg_r is not None and result.long_avg_r < 0.0
        assert passes_ev_gate(
            result,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5, min_avg_r_z=1.64),
        )

    def test_negative_and_significant_avg_r_is_blocked(self) -> None:
        """1 win / 20 losses at tp_r=2 → avg_r −0.857, z ≈ 6.0. Must block."""
        result = _make_result_with_avg_r(
            long_wins=1, long_losses=20, short_wins=0, short_losses=0, tp_r=2.0
        )
        assert not passes_ev_gate(
            result,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5, min_avg_r_z=1.64),
        )

    def test_z_zero_restores_point_estimate_behaviour(self) -> None:
        """`min_avg_r_z = 0.0` is the documented escape hatch to the old rule."""
        result = _make_result_with_avg_r(
            long_wins=3, long_losses=9, short_wins=0, short_losses=0, tp_r=2.0
        )
        cfg_legacy = self._cfg(min_trades=5, min_avg_r_z=0.0)
        cfg_sig = self._cfg(min_trades=5, min_avg_r_z=1.64)
        assert not passes_ev_gate(
            result, direction="long", timeframe="4h", backtest_cfg=cfg_legacy
        )
        assert passes_ev_gate(
            result, direction="long", timeframe="4h", backtest_cfg=cfg_sig
        )

    def test_zero_variance_blocks_only_on_a_long_enough_run(self) -> None:
        """All-identical trades break the t-test; the run length carries the evidence."""
        cfg = self._cfg(min_trades=2, min_avg_r_z=1.64)
        long_run = _make_result_with_avg_r(
            long_wins=0, long_losses=5, short_wins=0, short_losses=0, tp_r=2.0
        )
        short_run = _make_result_with_avg_r(
            long_wins=0, long_losses=3, short_wins=0, short_losses=0, tp_r=2.0
        )
        assert long_run.long_pnl_sd == 0.0 and short_run.long_pnl_sd == 0.0
        assert not passes_ev_gate(
            long_run, direction="long", timeframe="4h", backtest_cfg=cfg
        ), "5 straight full losses is evidence"
        assert passes_ev_gate(
            short_run, direction="long", timeframe="4h", backtest_cfg=cfg
        ), "3 identical trades is not"

    def test_snapshot_uses_its_stored_sd(self) -> None:
        """The cached path carries sd as a column; the same rule must apply there."""
        cfg = self._cfg(min_trades=5, min_avg_r_z=1.64)
        significant = BacktestSnapshot(
            symbol="AAPL",
            timeframe="4h",
            strategy="fvg",
            n_closed=21,
            n_long=21,
            r_long_avg=-0.857,
            r_long_sd=0.6547,
        )
        noisy = BacktestSnapshot(
            symbol="AAPL",
            timeframe="4h",
            strategy="fvg",
            n_closed=12,
            n_long=12,
            r_long_avg=-0.25,
            r_long_sd=1.357,
        )
        assert not passes_ev_gate(
            significant, direction="long", timeframe="4h", backtest_cfg=cfg
        )
        assert passes_ev_gate(noisy, direction="long", timeframe="4h", backtest_cfg=cfg)

    def test_snapshot_without_sd_abstains(self) -> None:
        """A row cached before the sd column existed must fail OPEN, not block.

        These age out within a candle (the cache key includes last_candle_ts), so
        the degraded window is one bar — but during it the gate must not block on
        a dispersion it cannot see.
        """
        stale = BacktestSnapshot(
            symbol="AAPL",
            timeframe="4h",
            strategy="fvg",
            n_closed=21,
            n_long=21,
            r_long_avg=-0.857,
            r_long_sd=None,
        )
        assert passes_ev_gate(
            stale,
            direction="long",
            timeframe="4h",
            backtest_cfg=self._cfg(min_trades=5, min_avg_r_z=1.64),
        )

    def test_per_tf_min_trades_is_honoured(self) -> None:
        """The guard reads the per-timeframe ladder, not the global fallback."""
        result = _make_result_with_avg_r(
            long_wins=0, long_losses=3, short_wins=0, short_losses=0, tp_r=2.0
        )
        cfg = BacktestFilterConfig(
            mode="hard",
            days=90,
            min_trades=12,
            min_trades_per_tf={"4h": 2, "1d": 10},
            min_avg_r=0.0,
            # z=0.0 isolates the ladder: these 3 trades are identical, so at the
            # shipped default (1.64) the zero-variance run-length rule would
            # abstain and this would stop testing the per-TF lookup.
            min_avg_r_z=0.0,
        )
        # 3 long trades: above 4h's 2 → evaluated (and blocked); below 1d's 10.
        assert not passes_ev_gate(
            result, direction="long", timeframe="4h", backtest_cfg=cfg
        )
        assert passes_ev_gate(
            result, direction="long", timeframe="1d", backtest_cfg=cfg
        )
