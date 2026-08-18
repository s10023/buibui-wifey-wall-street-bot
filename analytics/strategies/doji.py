"""Detector: Doji + Confirmation — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df


def detect_doji(
    df: pd.DataFrame,
    body_threshold: float = 0.1,
    confirm_body_pct: float = 0.6,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Doji + directional confirmation signals.

    A Doji is a candle where open ≈ close (body ≤ body_threshold × range).
    Signal fires when the NEXT candle is strongly directional
    (body ≥ confirm_body_pct × range).

    Long: confirmation candle is bullish (close > open).
    Short: confirmation candle is bearish (close < open).

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(n - 1):
        row = df.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])

        candle_range = h - lo
        if candle_range == 0.0:
            continue

        body = abs(c - o)
        if body > body_threshold * candle_range:
            continue

        # Check confirmation candle
        nxt = df.iloc[i + 1]
        nxt_o = float(nxt["open"])
        nxt_h = float(nxt["high"])
        nxt_lo = float(nxt["low"])
        nxt_c = float(nxt["close"])
        nxt_range = nxt_h - nxt_lo
        if nxt_range == 0.0:
            continue

        nxt_body = abs(nxt_c - nxt_o)
        if nxt_body < confirm_body_pct * nxt_range:
            continue

        open_time = int(nxt["open_time"])

        if nxt_c > nxt_o:
            entry = nxt_c
            sl = entry * (1 - sl_pct)
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"doji_bull@{entry:.2f}",
                    "sl_price": sl,
                    "context": "",
                }
            )
        else:
            entry = nxt_c
            sl = entry * (1 + sl_pct)
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"doji_bear@{entry:.2f}",
                    "sl_price": sl,
                    "context": "",
                }
            )

    return _signals_to_df(signals)
