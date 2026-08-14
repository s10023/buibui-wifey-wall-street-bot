"""Unit tests for `analytics.regime.classify_series`.

Covers the `slope_threshold` override used by `tools/regime_threshold_sweep.py`,
plus `TestBarsPerDayIsShared` — the guard against re-introducing a second,
crypto-valued bar-count table (see that class's docstring).
The default-threshold behaviour is exercised end-to-end by the replay tests
in `tests/test_regime_gate_replay.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.backtest.cost_model import BARS_PER_DAY as COST_MODEL_BARS_PER_DAY
from analytics.regime import BARS_PER_DAY, atr_window_bars, classify_series


def _series_4h(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    arr = np.array(closes, dtype=float)
    return pd.DataFrame(
        {
            "open_time": np.arange(n, dtype=int) * 4 * 60 * 60 * 1000,
            "high": arr * 1.001,
            "low": arr * 0.999,
            "close": arr,
        }
    )


def test_slope_threshold_higher_yields_fewer_trend_labels() -> None:
    """Tighter threshold → fewer bars labelled `trend`. Monotonic in threshold.

    Robust to high_vol overlay: only counts bars labelled exactly `trend`.
    """
    n = 200
    closes = [100.0 + 0.1 * i for i in range(n)]
    df = _series_4h(closes)

    loose = classify_series(df, "4h", slope_threshold=0.001)
    default = classify_series(df, "4h")
    strict = classify_series(df, "4h", slope_threshold=0.02)

    n_trend_loose = int((loose == "trend").sum())
    n_trend_default = int((default == "trend").sum())
    n_trend_strict = int((strict == "trend").sum())

    assert n_trend_loose >= n_trend_default >= n_trend_strict
    assert n_trend_loose > n_trend_strict


def test_slope_threshold_zero_labels_every_nonflat_as_trend() -> None:
    n = 200
    closes = (100.0 + np.arange(n) * 0.001).tolist()
    df = _series_4h(closes)
    aggressive = classify_series(df, "4h", slope_threshold=0.0)
    tail = aggressive.iloc[-50:]
    assert (tail.isin({"trend", "high_vol"})).all()


def test_slope_threshold_default_matches_explicit_default() -> None:
    n = 200
    rng = np.random.default_rng(42)
    closes = (100.0 + rng.normal(scale=0.5, size=n).cumsum()).tolist()
    df = _series_4h(closes)
    a = classify_series(df, "4h")
    b = classify_series(df, "4h", slope_threshold=0.005)
    pd.testing.assert_series_equal(a, b)


class TestBarsPerDayIsShared:
    """This module must never own a second bar-count table.

    It carried a private crypto-era copy (`{"4h": 6, "1h": 24}`) for the whole
    life of the fork while `cost_model` carried the correct RTH counts — one
    repo, both values, which is the duplication that let the divergence hide.
    The counts now come from the single definition, so these pin the properties
    an importer could still break: the RTH values themselves, the fall-CLOSED
    behaviour (unlike `bars_per_day_for_tf`, which falls open to 1.0), and the
    int conversion that `pandas.rolling(window=)` requires of a float count.
    """

    def test_is_the_same_object_as_the_cost_model_definition(self) -> None:
        assert BARS_PER_DAY is COST_MODEL_BARS_PER_DAY

    def test_carries_rth_counts_not_crypto_counts(self) -> None:
        # 4h RTH is 2 bars/day, not 6; 1h is 7, not 24. Per CLAUDE.md's
        # ADR-gate footgun — the first instance of this same constant class.
        assert BARS_PER_DAY["4h"] == 2.0
        assert BARS_PER_DAY["1h"] == 7.0
        assert BARS_PER_DAY["1d"] == 1.0
        assert BARS_PER_DAY["1wk"] == 0.2

    def test_unknown_timeframe_falls_closed(self) -> None:
        df = _series_4h([100.0 + 0.1 * i for i in range(60)])
        with pytest.raises(ValueError, match="Unsupported timeframe: 3h"):
            classify_series(df, "3h")

    @pytest.mark.parametrize("timeframe", ["1h", "4h", "1d", "1wk"])
    def test_every_declared_timeframe_classifies(self, timeframe: str) -> None:
        """A declared timeframe must not raise — `1wk` did until this fix.

        `backtest_trades` holds 1,056 `1wk` rows and `tools/strategy_edge_audit.py`
        groups by (symbol, timeframe), so the missing key was a live crash on
        every run against the real DB, not a latent gap.
        """
        df = _series_4h([100.0 + 0.1 * i for i in range(60)])
        labels = classify_series(df, timeframe)
        assert len(labels) == len(df)

    @pytest.mark.parametrize("timeframe", ["1h", "4h", "1d", "1wk"])
    def test_min_history_never_exceeds_the_window(self, timeframe: str) -> None:
        """`pandas.rolling` rejects `min_periods > window`, and the 50-bar floor
        is large enough to trip it on any timeframe coarser than `1d`.

        Calls the production helper rather than re-deriving it — a test that
        re-implements its subject passes against any implementation.
        """
        history_window, min_history = atr_window_bars(BARS_PER_DAY[timeframe])
        assert history_window >= 1
        assert min_history <= history_window

    def test_intraday_timeframes_keep_the_fifty_bar_floor(self) -> None:
        """The clamp must not weaken the floor where it was already satisfied."""
        for timeframe in ("1h", "4h", "1d"):
            _, min_history = atr_window_bars(BARS_PER_DAY[timeframe])
            assert min_history == 50, timeframe
