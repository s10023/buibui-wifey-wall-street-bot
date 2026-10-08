"""Regime classifier — §6 of docs/redesign/buibui-redesign.md.

Labels each candle on a (symbol, timeframe) series as `trend`, `range`, or
`high_vol`. The §6 priority is high_vol > trend > range; bars without enough
history to compute either signal are labelled `unknown`.

Used by Phase 0 strategy edge audit (per-trade regime slicing) and intended
to land as the regime gate in Phase 2 of the redesign.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

# The ONE bar-count definition. This module carried a private crypto-era copy
# (`4h: 6`, `1h: 24`) against cost_model's correct RTH counts for the whole life
# of the fork, so the "90-day" ATR window below really spanned ~270 sessions on
# 4h. `_ATR_HISTORY_DAYS` is in TRADING days, hence `1wk` = 0.2 of one.
from analytics.backtest.cost_model import BARS_PER_DAY
from analytics.strategies._shared import compute_ema

Regime = Literal["trend", "range", "high_vol", "unknown"]

_SLOPE_LOOKBACK = 10
_SLOPE_TREND_THRESHOLD = 0.005
_ATR_PERIOD = 14
_ATR_PERCENTILE = 0.80
_ATR_HISTORY_DAYS = 90
_MIN_HISTORY_DAYS = 7


def atr_window_bars(bars_per_day: float) -> tuple[int, int]:
    """`(history_window, min_history)` in BARS for a timeframe's bar count.

    Extracted so a test can observe it rather than re-implement it. The 50-bar
    floor was calibrated on intraday counts, where the window is always far
    larger; on a coarse timeframe it can EXCEED the window (`1wk`: floor 50 vs
    an 18-bar window), which `pandas.rolling` rejects outright — hence the
    clamp. The 4h/1h/1d timeframes all keep 50.
    """
    history_window = int(bars_per_day * _ATR_HISTORY_DAYS)
    min_history = min(max(50, int(bars_per_day * _MIN_HISTORY_DAYS)), history_window)
    return history_window, min_history


def _atr_wilder(df: pd.DataFrame, period: int = _ATR_PERIOD) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def classify_series(
    df: pd.DataFrame,
    timeframe: str,
    slope_threshold: float | None = None,
) -> pd.Series:
    """Return a regime label per row.

    df must be sorted ascending by `open_time` and contain `high`, `low`, `close`.
    Returns a string series aligned with df.index.

    `slope_threshold` overrides the module-level `_SLOPE_TREND_THRESHOLD` for
    research / sweeps (see `tools/regime_threshold_sweep.py`). When None, the
    live default is used.
    """
    bars_per_day = BARS_PER_DAY.get(timeframe)
    if bars_per_day is None:
        # Falls CLOSED, unlike cost_model's own `bars_per_day_for_tf`, which
        # falls open to 1.0 for its purposes. Do not swap one for the other.
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    threshold = (
        _SLOPE_TREND_THRESHOLD if slope_threshold is None else float(slope_threshold)
    )
    history_window, min_history = atr_window_bars(bars_per_day)

    close = df["close"].astype(float)
    ema50 = compute_ema(close, 50)
    slope = (ema50 - ema50.shift(_SLOPE_LOOKBACK)) / ema50.shift(_SLOPE_LOOKBACK)

    atr = _atr_wilder(df)
    atr_pct = atr / close
    atr_p80 = atr_pct.rolling(window=history_window, min_periods=min_history).quantile(
        _ATR_PERCENTILE
    )

    regime = pd.Series("range", index=df.index, dtype="object")
    regime[slope.abs() >= threshold] = "trend"
    regime[atr_pct >= atr_p80] = "high_vol"
    regime[atr_pct.isna() | atr_p80.isna() | slope.isna()] = "unknown"
    return regime
