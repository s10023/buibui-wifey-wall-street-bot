"""Detector: Hammer / Hanging Man — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df, volume_confirm


def detect_hammer_hanging_man(
    df: pd.DataFrame,
    wick_ratio: float = 2.0,
    context_lookback: int = 10,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Hammer (bullish reversal) and Hanging Man (bearish reversal).

    Same shape as a bullish pin bar (small body, long lower wick ≥ wick_ratio × body),
    but context-aware:
    - Hammer: shape appears after a downtrend (close[i] < close[i - context_lookback]).
    - Hanging Man: same shape appears after an uptrend (close[i] > close[i - context_lookback]).

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < context_lookback + 1:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(context_lookback, n):
        row = df.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])

        body = abs(c - o)
        if body == 0.0:
            continue

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - lo

        # Must have long lower wick and short upper wick (upper wick ≤ body)
        if lower_wick < wick_ratio * body or upper_wick > body:
            continue

        open_time = int(row["open_time"])
        prior_close = float(df.iloc[i - context_lookback]["close"])
        vol_ok = volume_confirm(df, i)

        if c < prior_close:
            # Downtrend context → Hammer (bullish)
            entry = c
            sl = entry * (1 - sl_pct)
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"hammer@{entry:.2f}",
                    "sl_price": sl,
                    "context": "",
                    "low_volume": not vol_ok,
                }
            )
        else:
            # Uptrend context → Hanging Man (bearish)
            entry = c
            sl = entry * (1 + sl_pct)
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"hanging_man@{entry:.2f}",
                    "sl_price": sl,
                    "context": "",
                    "low_volume": not vol_ok,
                }
            )

    return _signals_to_df(signals)
