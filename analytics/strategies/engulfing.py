"""Detector: Engulfing — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df, volume_confirm


def detect_engulfing(
    df: pd.DataFrame,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Bullish and Bearish Engulfing 2-candle patterns.

    Bullish Engulfing: current bullish candle body fully engulfs the prior
    bearish candle body (current open < prior close AND current close > prior open).

    Bearish Engulfing: current bearish candle body fully engulfs the prior
    bullish candle body (current open > prior close AND current close < prior open).

    SL: entry_price * (1 - sl_pct) for long, * (1 + sl_pct) for short.
    No structural TP: the detector sets no `tp_price`, so the alert and the
    backtest engine each derive the target from the config-resolved tp_r.
    Signal open_time is the engulfing candle's open_time.
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for i in range(1, n):
        prev_open = opens[i - 1]
        prev_close = closes[i - 1]
        curr_open = opens[i]
        curr_close = closes[i]
        open_time = open_times[i]

        prev_body_top = max(prev_open, prev_close)
        prev_body_bot = min(prev_open, prev_close)

        # Bullish engulfing: prev bearish, curr bullish, curr body engulfs prev body
        if (
            prev_close < prev_open  # prev bearish
            and curr_close > curr_open  # curr bullish
            and curr_open < prev_body_bot
            and curr_close > prev_body_top
        ):
            entry = curr_close
            sl = entry * (1 - sl_pct)
            vol_ok = volume_confirm(df, i)
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"bullish_engulfing@{entry:.2f}",
                    "sl_price": sl,
                    "context": "",
                    "low_volume": not vol_ok,
                }
            )

        # Bearish engulfing: prev bullish, curr bearish, curr body engulfs prev body
        elif (
            prev_close > prev_open  # prev bullish
            and curr_close < curr_open  # curr bearish
            and curr_open > prev_body_top
            and curr_close < prev_body_bot
        ):
            entry = curr_close
            sl = entry * (1 + sl_pct)
            vol_ok = volume_confirm(df, i)
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"bearish_engulfing@{entry:.2f}",
                    "sl_price": sl,
                    "context": "",
                    "low_volume": not vol_ok,
                }
            )

    return _signals_to_df(signals)
