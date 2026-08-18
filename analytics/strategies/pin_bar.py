"""Detector: Pin Bar — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df, volume_confirm


def detect_pin_bar(
    df: pd.DataFrame,
    wick_ratio: float = 2.0,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Pin Bar patterns (hammer / shooting star shape).

    Bullish Pin Bar (hammer shape): small body at the top of the range with
    a long lower wick ≥ wick_ratio × body.
    Bearish Pin Bar (shooting star shape): small body at the bottom of the
    range with a long upper wick ≥ wick_ratio × body.

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < 1:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(n):
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
        open_time = int(row["open_time"])

        # Bullish pin bar: long lower wick, small upper wick (≤ body)
        if lower_wick >= wick_ratio * body and upper_wick <= body:
            entry = c
            sl = entry * (1 - sl_pct)
            vol_ok = volume_confirm(df, i)
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"pin_bar_bull@{entry:.2f}",
                    "sl_price": sl,
                    "context": "",
                    "low_volume": not vol_ok,
                }
            )

        # Bearish pin bar: long upper wick, small lower wick (≤ body)
        elif upper_wick >= wick_ratio * body and lower_wick <= body:
            entry = c
            sl = entry * (1 + sl_pct)
            vol_ok = volume_confirm(df, i)
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"pin_bar_bear@{entry:.2f}",
                    "sl_price": sl,
                    "context": "",
                    "low_volume": not vol_ok,
                }
            )

    return _signals_to_df(signals)
