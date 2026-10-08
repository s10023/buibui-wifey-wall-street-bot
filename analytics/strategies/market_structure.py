"""Detector: Market Structure Break (BOS / CHoCH) — extracted from `analytics/indicators_lib.py` in strat-2.

The signal `open_time` is stamped at the
swing *confirmation* bar (`row_idx + swing_lookback`) rather than the swing bar
itself, because the centered swing window reads `swing_lookback` future bars to
confirm the swing. The swing *price* level is still taken from the swing bar.
See docs/redesign/phase0-lookahead-audit.md.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df


def detect_market_structure(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    min_swing_pct: float = 0.005,
) -> pd.DataFrame:
    """Detect Break of Structure (BOS) and Change of Character (CHoCH).

    Swing highs and lows are identified using a rolling window of size
    2 × swing_lookback + 1.

    BOS long:   higher swing high in established uptrend.
    BOS short:  lower swing low in established downtrend.
    CHoCH long: first higher swing high after a downtrend (trend reversal).
    CHoCH short: first lower swing low after an uptrend (trend reversal).

    min_swing_pct: suppress signals where the structural level (swing_high -
    swing_low) / swing_high is smaller than this fraction.  Default 0.0
    keeps the original behaviour (no filter).
    """
    n = len(df)
    if n < swing_lookback * 3:
        return _empty_signals()

    high_series = df["high"].astype(float)
    low_series = df["low"].astype(float)

    window = 2 * swing_lookback + 1
    rolling_max = high_series.rolling(
        window=window, center=True, min_periods=window
    ).max()
    rolling_min = low_series.rolling(
        window=window, center=True, min_periods=window
    ).min()

    is_swing_high = high_series == rolling_max
    is_swing_low = low_series == rolling_min

    swings: list[tuple[int, float, str]] = []
    for i in range(n):
        if bool(is_swing_high.iloc[i]):
            swings.append((i, float(high_series.iloc[i]), "H"))
        if bool(is_swing_low.iloc[i]):
            swings.append((i, float(low_series.iloc[i]), "L"))
    swings.sort(key=lambda x: x[0])

    signals: list[dict[str, object]] = []
    last_sh: float | None = None
    last_sl: float | None = None
    trend: str = "unknown"

    for row_idx, price, typ in swings:
        # The swing at `row_idx` is confirmed by a centered window that reads
        # `swing_lookback` bars *after* it, so it only becomes knowable at bar
        # `row_idx + swing_lookback`. Stamp the signal there (the first causal
        # open_time) while keeping the swing *price* level from `row_idx`. The
        # centered `min_periods=window` guarantees every swing bar satisfies
        # `row_idx <= n-1-swing_lookback`, so `confirm_idx` is always in-bounds;
        # the guard is belt-and-braces and stays causal if that ever changes.
        confirm_idx = row_idx + swing_lookback
        if confirm_idx >= n:
            continue
        open_time = int(df.iloc[confirm_idx]["open_time"])

        if typ == "H":
            if last_sh is not None and price > last_sh:
                sl_val = last_sl if last_sl is not None else 0.0
                swing_range = (price - sl_val) / price if price > 0 else 0.0
                if swing_range >= min_swing_pct:
                    label = "choch_long" if trend == "down" else "bos_long"
                    signals.append(
                        {
                            "open_time": open_time,
                            "direction": "long",
                            "reason": f"{label}@{price:.2f}",
                            "sl_price": sl_val,
                            "context": "",
                        }
                    )
                trend = "up"
            last_sh = price

        else:  # "L"
            if last_sl is not None and price < last_sl:
                sh_val = last_sh if last_sh is not None else 0.0
                swing_range = (sh_val - price) / price if price > 0 else 0.0
                if swing_range >= min_swing_pct:
                    label = "choch_short" if trend == "up" else "bos_short"
                    signals.append(
                        {
                            "open_time": open_time,
                            "direction": "short",
                            "reason": f"{label}@{price:.2f}",
                            "sl_price": sh_val,
                            "context": "",
                        }
                    )
                trend = "down"
            last_sl = price

    return _signals_to_df(signals)
