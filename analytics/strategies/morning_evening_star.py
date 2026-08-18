"""Detector: Morning Star / Evening Star — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df


def detect_morning_evening_star(
    df: pd.DataFrame,
    star_body_max: float = 0.3,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Morning Star (3-candle bullish) and Evening Star (3-candle bearish) patterns.

    Morning Star:
    - Candle[i-2]: large bearish candle (close < open).
    - Candle[i-1]: small-body star candle (body ≤ star_body_max × range), gaps lower.
    - Candle[i]:   large bullish candle (close > open) closing above midpoint of candle[i-2] body.

    Evening Star (mirror):
    - Candle[i-2]: large bullish candle.
    - Candle[i-1]: small-body star.
    - Candle[i]:   large bearish candle closing below midpoint of candle[i-2] body.

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for i in range(2, n):
        a_o, _, _, a_c = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        s_o, s_h, s_l, s_c = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        b_o, _, _, b_c = opens[i], highs[i], lows[i], closes[i]

        star_range = s_h - s_l
        if star_range == 0.0:
            continue
        star_body = abs(s_c - s_o)

        # Star candle must have small body
        if star_body > star_body_max * star_range:
            continue

        # Morning Star: A bearish, B bullish, B closes above midpoint of A
        if a_c < a_o and b_c > b_o:
            a_mid = (a_o + a_c) / 2
            if b_c > a_mid:
                open_time = open_times[i]
                entry = b_c
                sl = entry * (1 - sl_pct)
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": f"morning_star@{entry:.2f}",
                        "sl_price": sl,
                        "context": "",
                    }
                )

        # Evening Star: A bullish, B bearish, B closes below midpoint of A
        elif a_c > a_o and b_c < b_o:
            a_mid = (a_o + a_c) / 2
            if b_c < a_mid:
                open_time = open_times[i]
                entry = b_c
                sl = entry * (1 + sl_pct)
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": f"evening_star@{entry:.2f}",
                        "sl_price": sl,
                        "context": "",
                    }
                )

    return _signals_to_df(signals)
