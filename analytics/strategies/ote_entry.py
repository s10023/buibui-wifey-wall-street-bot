"""Detector: OTE Entry (0.618–0.786 retracement after BOS) — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _find_bos_swing, _signals_to_df


def detect_ote_entry(
    df: pd.DataFrame,
    swing_lookback: int = 20,
    bos_lookback: int = 5,
) -> pd.DataFrame:
    """Detect OTE (Optimal Trade Entry) — 0.618–0.786 retracement after a confirmed BOS.

    BOS-anchored retracement entry using the deeper OTE zone
    (61.8%–78.6% retracement). Targets the high-probability ICT OTE level.

    LONG (bullish BOS):
    - Entry zone: fib 0.786 ≤ close ≤ fib 0.618.
    - SL: below the swing_low.
    - TP: 1.618 extension above swing_high.

    SHORT (bearish BOS):
    - Entry zone: fib 0.618 ≤ close ≤ fib 0.786 (measured from swing_low upward).
    - SL: above the swing_high.
    - TP: 1.618 extension below swing_low.
    """
    n = len(df)
    if n < swing_lookback + bos_lookback + 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for sig_i in range(swing_lookback + bos_lookback + 1, n):
        bos = _find_bos_swing(df.iloc[: sig_i + 1], swing_lookback, bos_lookback)
        if bos is None:
            continue

        sl_price_bos, sh_price_bos, direction = bos
        swing_range = sh_price_bos - sl_price_bos
        if swing_range <= 0.0:
            continue

        curr_close = closes[sig_i]
        open_time = open_times[sig_i]

        if direction == "long":
            fib_0_618 = sh_price_bos - 0.618 * swing_range
            fib_0_786 = sh_price_bos - 0.786 * swing_range
            sl_out = sl_price_bos
            tp = sh_price_bos + 0.618 * swing_range
            if fib_0_618 >= curr_close >= fib_0_786:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": (
                            f"ote_long@{curr_close:.2f} (0.786={fib_0_786:.2f})"
                        ),
                        "sl_price": sl_out,
                        "tp_price": tp,
                        "context": (
                            f"OTE: swing_low={sl_price_bos:.2f} "
                            f"swing_high={sh_price_bos:.2f} "
                            f"TP={tp:.2f} (1.618 ext)"
                        ),
                    }
                )

        else:  # short
            fib_0_618 = sl_price_bos + 0.618 * swing_range
            fib_0_786 = sl_price_bos + 0.786 * swing_range
            sl_out = sh_price_bos
            tp = sl_price_bos - 0.618 * swing_range
            if fib_0_786 >= curr_close >= fib_0_618:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": (
                            f"ote_short@{curr_close:.2f} (0.786={fib_0_786:.2f})"
                        ),
                        "sl_price": sl_out,
                        "tp_price": tp,
                        "context": (
                            f"OTE: swing_high={sh_price_bos:.2f} "
                            f"swing_low={sl_price_bos:.2f} "
                            f"TP={tp:.2f} (1.618 ext)"
                        ),
                    }
                )

    return _signals_to_df(signals)
