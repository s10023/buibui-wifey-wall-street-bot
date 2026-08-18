"""Detector: Fibonacci Retracement (legacy) — extracted from `analytics/indicators_lib.py` in strat-2.

Legacy detector — superseded by `fib_golden_zone` (which adds BOS confirmation
and a better SL/TP structure). Kept here because tests import it for behavioural
A/B comparison. NOT registered in `STRATEGY_REGISTRY` or `DETECTOR_REGISTRY`.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df


def detect_fibonacci_retracement(
    df: pd.DataFrame,
    swing_lookback: int = 20,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Fibonacci golden zone (0.5–0.618) retracement signals.

    Swing detection: scan the last `swing_lookback` bars (excluding the signal
    candle) for the most recent swing high and swing low using a 3-bar pivot
    (bar[i] is a pivot if it is strictly greater/less than bar[i-1] and bar[i+1]).

    LONG signal (bullish swing established — swing_low before swing_high):
    - Price retraces into the golden zone: fib_0.618 ≤ close ≤ fib_0.5.
    - SL: fib_0.786 price level.
    - TP: swing_high.

    SHORT signal (bearish swing — swing_high before swing_low):
    - Price retraces up into the golden zone from below: fib_0.5 ≤ close ≤ fib_0.618.
    - SL: fib_0.786 above swing_high.
    - TP: swing_low.

    reason: e.g. "fib_golden_zone@70000.00 (0.618=69500.00)"
    """
    n = len(df)
    if n < swing_lookback + 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for sig_i in range(swing_lookback + 1, n):
        # Scan window: indices [sig_i - swing_lookback, sig_i - 1] (no lookahead)
        win_start = sig_i - swing_lookback
        win_end = sig_i - 1  # inclusive

        # Find swing highs and lows in the window using 3-bar pivots
        # (need at least 1 bar on each side, so scan [win_start+1, win_end-1])
        swing_highs: list[tuple[int, float]] = []  # (idx_in_df, price)
        swing_lows: list[tuple[int, float]] = []
        for k in range(win_start + 1, win_end):
            if highs[k] > highs[k - 1] and highs[k] > highs[k + 1]:
                swing_highs.append((k, highs[k]))
            if lows[k] < lows[k - 1] and lows[k] < lows[k + 1]:
                swing_lows.append((k, lows[k]))

        if not swing_highs or not swing_lows:
            continue

        # Most recent swing high and low
        sh_idx, sh_price = swing_highs[-1]
        sl_idx, sl_price = swing_lows[-1]

        swing_range = sh_price - sl_price
        if swing_range <= 0.0:
            continue

        # Fibonacci levels: measured as retracement from swing_high down (standard convention).
        # 0% = swing_high, 100% = swing_low.
        # fib_0_5   = sh_price - 0.5 * swing_range   (50% retracement)
        # fib_0_618 = sh_price - 0.618 * swing_range  (61.8% retracement — golden zone bottom)
        # fib_0_786 = sh_price - 0.786 * swing_range  (78.6% retracement — SL invalidation)
        fib_0_5 = sh_price - 0.5 * swing_range
        fib_0_618 = sh_price - 0.618 * swing_range
        fib_0_786 = sh_price - 0.786 * swing_range

        curr_close = closes[sig_i]
        open_time = open_times[sig_i]

        # LONG: swing_low established before swing_high (upswing), price now retracing.
        # Enter long when price retraces into the golden zone (50%–61.8% retracement).
        # SL: fib_0.786 — if price retraces 78.6%+ the up-move is invalidated.
        if sl_idx < sh_idx:
            if fib_0_5 >= curr_close >= fib_0_618:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": f"fib_golden_zone@{curr_close:.2f} (0.618={fib_0_618:.2f})",
                        "sl_price": fib_0_786,
                        "context": (
                            f"Fib: swing_low={sl_price:.2f} swing_high={sh_price:.2f}"
                        ),
                    }
                )

        # SHORT: swing_high established before swing_low (downswing), price bouncing up.
        # Enter short when price retraces UP into the golden zone (50%–61.8% of the down-move).
        # Fib levels here measured from swing_low upward.
        # SL: fib_0.786 measured from swing_low upward (price recovers too much → invalidated).
        elif sh_idx < sl_idx:
            short_fib_0_5 = sl_price + 0.5 * swing_range
            short_fib_0_618 = sl_price + 0.618 * swing_range
            short_fib_0_786 = sl_price + 0.786 * swing_range

            if short_fib_0_618 >= curr_close >= short_fib_0_5:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": f"fib_golden_zone@{curr_close:.2f} (0.618={short_fib_0_618:.2f})",
                        "sl_price": short_fib_0_786,
                        "context": (
                            f"Fib: swing_high={sh_price:.2f} swing_low={sl_price:.2f}"
                        ),
                    }
                )

    return _signals_to_df(signals)
