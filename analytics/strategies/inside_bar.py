"""Detector: Inside Bar — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df


def detect_inside_bar(
    df: pd.DataFrame,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Inside Bar breakout patterns.

    An inside bar forms when the current candle's body is fully within the
    prior candle's body (high of inside ≤ high of mother, low of inside ≥ low
    of mother using body extremes, not wicks).

    Note: this is a *body-only* containment, which deviates from the canonical
    inside-bar definition (high ≤ prev high AND low ≥ prev low). Chosen to
    suppress wick-noise; tracked for an A/B backtest in Phase 3.

    Signal fires on the breakout candle (the candle AFTER the inside bar) that
    closes above/below the mother bar body:
    - Long: close > mother bar body top.
    - Short: close < mother bar body bottom.

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for i in range(1, n - 1):
        mother_top = max(opens[i - 1], closes[i - 1])
        mother_bot = min(opens[i - 1], closes[i - 1])
        inside_top = max(opens[i], closes[i])
        inside_bot = min(opens[i], closes[i])

        # Check inside bar: body of candle i fully inside body of candle i-1
        if inside_top <= mother_top and inside_bot >= mother_bot:
            # Breakout candle is i+1
            breakout_close = closes[i + 1]
            open_time = open_times[i + 1]
            if breakout_close > mother_top:
                entry = breakout_close
                sl = entry * (1 - sl_pct)
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": f"inside_bar_long@{entry:.2f}",
                        "sl_price": sl,
                        "context": "",
                    }
                )
            elif breakout_close < mother_bot:
                entry = breakout_close
                sl = entry * (1 + sl_pct)
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": f"inside_bar_short@{entry:.2f}",
                        "sl_price": sl,
                        "context": "",
                    }
                )

    return _signals_to_df(signals)
