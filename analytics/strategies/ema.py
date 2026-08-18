"""Detector: EMA pullback continuation (Variant A).

See `docs/superpowers/specs/2026-05-02-ema-strategy-design.md` for the full
design — the rules below mirror §3 of the spec verbatim.
"""

import pandas as pd

from analytics.strategies._shared import (
    _empty_signals,
    _signals_to_df,
    compute_ema,
    is_trending,
    volume_confirm,
)


def detect_ema(
    df: pd.DataFrame,
    fast_period: int = 20,
    slow_period: int = 50,
    slope_lookback: int = 10,
    regime_lookback: int = 20,
    max_crosses: int = 2,
    min_slope_pct: float = 0.003,
    pullback_lookback: int = 5,
    min_body_pct: float = 0.5,
    tp_r: float = 3.0,
) -> pd.DataFrame:
    """Detect EMA pullback continuation signals (long + short).

    Algorithm (per spec §3):
    1. Trend filter — close vs slow EMA + slow EMA slope sign decide direction.
    2. Regime gate — `is_trending()` (cross count + |slope| %) suppresses chop.
    3. Pullback — within `pullback_lookback` prior candles, at least one wick
       touched the fast EMA from the trend side AND closed back on the trend side.
    4. Trigger — current candle bullish/bearish, closes on trend side of fast EMA,
       body >= `min_body_pct` of high-low range.
    5. SL = lowest low (long) / highest high (short) among qualifying pullback
       candles. TP = entry +/- tp_r * sl_distance.

    Entry is next candle open (handled by the backtest engine); the signal
    `open_time` is the trigger candle's open_time.
    """
    n = len(df)
    if fast_period >= slow_period:
        return _empty_signals()
    min_bars = max(slow_period, regime_lookback, slope_lookback, pullback_lookback) + 1
    if n < min_bars:
        return _empty_signals()

    closes = df["close"].astype(float)
    ema_fast = compute_ema(closes, fast_period)
    ema_slow = compute_ema(closes, slow_period)

    opens_arr = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes_arr = closes.to_numpy()
    fast_arr = ema_fast.to_numpy()
    slow_arr = ema_slow.to_numpy()
    open_times = df["open_time"].to_numpy(dtype=int)

    signals: list[dict[str, object]] = []

    for i in range(min_bars - 1, n):
        slow_now = slow_arr[i]
        slow_then = slow_arr[i - slope_lookback]
        if slow_then == 0.0:
            continue
        slope = (slow_now - slow_then) / slow_then
        close_i = closes_arr[i]

        if close_i > slow_now and slope > 0:
            trend = "up"
        elif close_i < slow_now and slope < 0:
            trend = "down"
        else:
            continue

        if not is_trending(
            closes,
            ema_fast,
            ema_slow,
            i,
            slope_lookback=slope_lookback,
            regime_lookback=regime_lookback,
            max_crosses=max_crosses,
            min_slope_pct=min_slope_pct,
        ):
            continue

        pb_start = max(0, i - pullback_lookback)
        pb_end = i  # current candle is the trigger, not part of the pullback window
        if pb_end <= pb_start:
            continue

        if trend == "up":
            qualifying_lows = [
                lows[j]
                for j in range(pb_start, pb_end)
                if lows[j] <= fast_arr[j] and closes_arr[j] > fast_arr[j]
            ]
            if not qualifying_lows:
                continue
            sl = float(min(qualifying_lows))
        else:
            qualifying_highs = [
                highs[j]
                for j in range(pb_start, pb_end)
                if highs[j] >= fast_arr[j] and closes_arr[j] < fast_arr[j]
            ]
            if not qualifying_highs:
                continue
            sl = float(max(qualifying_highs))

        rng = highs[i] - lows[i]
        if rng <= 0:
            continue
        body = abs(closes_arr[i] - opens_arr[i])
        if body / rng < min_body_pct:
            continue

        if trend == "up":
            if not (closes_arr[i] > opens_arr[i] and closes_arr[i] > fast_arr[i]):
                continue
            entry = float(closes_arr[i])
            sl_dist = entry - sl
            if sl_dist <= 0:
                continue
            tp = entry + sl_dist * tp_r
            direction = "long"
            reason = f"ema_pullback_long@{entry:.2f}"
        else:
            if not (closes_arr[i] < opens_arr[i] and closes_arr[i] < fast_arr[i]):
                continue
            entry = float(closes_arr[i])
            sl_dist = sl - entry
            if sl_dist <= 0:
                continue
            tp = entry - sl_dist * tp_r
            direction = "short"
            reason = f"ema_pullback_short@{entry:.2f}"

        vol_ok = volume_confirm(df, i)
        signals.append(
            {
                "open_time": int(open_times[i]),
                "direction": direction,
                "reason": reason,
                "sl_price": sl,
                "context": "",
                "low_volume": not vol_ok,
                "tp_price": tp,
            }
        )

    return _signals_to_df(signals)
