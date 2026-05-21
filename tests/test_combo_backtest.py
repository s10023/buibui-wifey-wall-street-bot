"""Tests for co-firing confluence backtest (D10 Step 2)."""

import pandas as pd

from analytics.backtest_lib import (
    ComboBacktestResult,
    _find_cofire_signals,
    run_combo_backtest,
)
from analytics.strategies import INCOMPATIBLE_PAIRS, SIGNAL_COLUMNS


def _make_ohlcv(n: int = 20) -> pd.DataFrame:
    """Minimal OHLCV DataFrame with n candles (1h intervals from ts=0)."""
    ms_per_hour = 3_600_000
    return pd.DataFrame(
        {
            "open_time": [i * ms_per_hour for i in range(n)],
            "open": [100.0] * n,
            "high": [102.0] * n,
            "low": [98.0] * n,
            "close": [101.0] * n,
            "volume": [1000.0] * n,
        }
    )


def _make_signals(
    candle_indices: list[int],
    direction: str = "long",
    sl_price: float = 98.0,
    ohlcv: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build a minimal signals DataFrame at the given candle positions."""
    ms_per_hour = 3_600_000
    if ohlcv is not None:
        times = [int(ohlcv["open_time"].iloc[i]) for i in candle_indices]
    else:
        times = [i * ms_per_hour for i in candle_indices]
    rows = [
        {
            "open_time": t,
            "direction": direction,
            "reason": f"test signal at {t}",
            "sl_price": sl_price,
            "context": "test",
            "low_volume": False,
            "tp_price": 0.0,
        }
        for t in times
    ]
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


# ---------------------------------------------------------------------------
# _find_cofire_signals
# ---------------------------------------------------------------------------


def test_cofire_same_candle() -> None:
    ohlcv = _make_ohlcv(20)
    sigs_a = _make_signals([5], ohlcv=ohlcv)
    sigs_b = _make_signals([5], ohlcv=ohlcv)
    result = _find_cofire_signals(sigs_a, sigs_b, ohlcv, window=5, min_signals=1)
    assert len(result) == 1
    assert int(result["open_time"].iloc[0]) == int(ohlcv["open_time"].iloc[5])


def test_cofire_within_window() -> None:
    ohlcv = _make_ohlcv(20)
    sigs_a = _make_signals([5], ohlcv=ohlcv)
    sigs_b = _make_signals([8], ohlcv=ohlcv)  # 3 candles later — within ±5
    result = _find_cofire_signals(sigs_a, sigs_b, ohlcv, window=5, min_signals=1)
    assert len(result) == 1
    # Entry uses the later candle (index 8)
    assert int(result["open_time"].iloc[0]) == int(ohlcv["open_time"].iloc[8])


def test_cofire_outside_window_no_match() -> None:
    ohlcv = _make_ohlcv(20)
    sigs_a = _make_signals([2], ohlcv=ohlcv)
    sigs_b = _make_signals([10], ohlcv=ohlcv)  # 8 candles apart — outside ±5
    result = _find_cofire_signals(sigs_a, sigs_b, ohlcv, window=5, min_signals=1)
    assert result.empty


def test_cofire_direction_mismatch_no_match() -> None:
    ohlcv = _make_ohlcv(20)
    sigs_a = _make_signals([5], direction="long", ohlcv=ohlcv)
    sigs_b = _make_signals([5], direction="short", ohlcv=ohlcv)
    result = _find_cofire_signals(sigs_a, sigs_b, ohlcv, window=5, min_signals=1)
    assert result.empty


def test_cofire_each_b_used_once() -> None:
    """One B signal matched to closest A; second A gets no match."""
    ohlcv = _make_ohlcv(20)
    sigs_a = _make_signals([4, 5], ohlcv=ohlcv)  # two A signals
    sigs_b = _make_signals([5], ohlcv=ohlcv)  # only one B
    result = _find_cofire_signals(sigs_a, sigs_b, ohlcv, window=5, min_signals=1)
    assert len(result) == 1  # B can only match one A


def test_cofire_min_signals_skips_dead_strategy() -> None:
    ohlcv = _make_ohlcv(20)
    sigs_a = _make_signals([5, 10, 15], ohlcv=ohlcv)
    sigs_b = _make_signals([5], ohlcv=ohlcv)  # only 1 signal — below min_signals=3
    result = _find_cofire_signals(sigs_a, sigs_b, ohlcv, window=5, min_signals=3)
    assert result.empty


def test_cofire_empty_input() -> None:
    ohlcv = _make_ohlcv(20)
    empty = pd.DataFrame(columns=SIGNAL_COLUMNS)
    sigs = _make_signals([5], ohlcv=ohlcv)
    assert _find_cofire_signals(empty, sigs, ohlcv).empty
    assert _find_cofire_signals(sigs, empty, ohlcv).empty


def test_cofire_entry_uses_later_signal() -> None:
    """When A fires first, entry should be at B's candle."""
    ohlcv = _make_ohlcv(20)
    sigs_a = _make_signals([3], ohlcv=ohlcv)
    sigs_b = _make_signals([6], ohlcv=ohlcv)
    result = _find_cofire_signals(sigs_a, sigs_b, ohlcv, window=5, min_signals=1)
    assert int(result["open_time"].iloc[0]) == int(ohlcv["open_time"].iloc[6])


# ---------------------------------------------------------------------------
# run_combo_backtest
# ---------------------------------------------------------------------------


def test_run_combo_backtest_returns_combo_result() -> None:
    ohlcv = _make_ohlcv(30)
    sigs_a = _make_signals([3, 10, 18], ohlcv=ohlcv)
    sigs_b = _make_signals([4, 11, 19], ohlcv=ohlcv)
    combo = run_combo_backtest(
        ohlcv, sigs_a, sigs_b, "BTCUSDT", "1h", "bos", "fvg", window=3
    )
    assert isinstance(combo, ComboBacktestResult)
    assert combo.strategy_a == "bos"
    assert combo.strategy_b == "fvg"
    assert combo.window == 3
    assert combo.result.symbol == "BTCUSDT"
    assert combo.result.timeframe == "1h"
    assert combo.result.strategy == "bos+fvg"


def test_run_combo_backtest_zero_trades_when_no_cofire() -> None:
    ohlcv = _make_ohlcv(30)
    sigs_a = _make_signals([3, 10, 18], ohlcv=ohlcv)
    sigs_b = _make_signals([0, 7, 14], ohlcv=ohlcv)  # all outside window=1
    combo = run_combo_backtest(
        ohlcv, sigs_a, sigs_b, "BTCUSDT", "1h", "bos", "fvg", window=1
    )
    assert len(combo.result.trades) == 0


# ---------------------------------------------------------------------------
# INCOMPATIBLE_PAIRS
# ---------------------------------------------------------------------------


def test_incompatible_pairs_contains_bos_ote() -> None:
    assert frozenset({"ote_entry", "bos"}) in INCOMPATIBLE_PAIRS


def test_compatible_pair_not_in_incompatible() -> None:
    assert frozenset({"bos", "fvg"}) not in INCOMPATIBLE_PAIRS
    assert frozenset({"engulfing", "ote_entry"}) not in INCOMPATIBLE_PAIRS
