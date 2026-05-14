"""Overnight gap detection for US equities.

Every US equity trading session leaves an overnight gap between the prior
session's close and the next session's open. Unfilled gaps act as price
magnets and can invalidate trade setups.

Replaces cme_gap_lib.py from buibui (crypto-specific weekend gap).
No module-level side effects.
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class OvernightGap:
    gap_pct: float
    gap_up: bool
    filled: bool
    prev_close: float
    today_open: float


def get_overnight_gap(ohlcv_df: pd.DataFrame) -> OvernightGap | None:
    """Compute the overnight gap from the two most recent rows of OHLCV data.

    ohlcv_df must have columns: open_time, open, high, low, close.
    Returns None if fewer than two rows are available.
    """
    if len(ohlcv_df) < 2:
        return None

    prev = ohlcv_df.iloc[-2]
    curr = ohlcv_df.iloc[-1]

    prev_close = float(prev["close"])
    today_open = float(curr["open"])
    gap_pct = (today_open - prev_close) / prev_close
    gap_up = today_open > prev_close

    if gap_up:
        filled = float(curr["low"]) <= prev_close
    else:
        filled = float(curr["high"]) >= prev_close

    return OvernightGap(
        gap_pct=gap_pct,
        gap_up=gap_up,
        filled=filled,
        prev_close=prev_close,
        today_open=today_open,
    )


def gap_fill_warning(
    gap: OvernightGap,
    direction: str,
    entry: float,
) -> str | None:
    """Return a warning string if the unfilled gap threatens the trade.

    For a LONG trade: an unfilled gap-down above entry may act as resistance
    as price attempts to fill the gap (pulling price down).
    For a SHORT trade: an unfilled gap-up below entry may act as support
    as price attempts to fill the gap (pushing price up).

    Returns None when the gap is already filled or poses no threat.
    """
    if gap.filled:
        return None

    if direction == "long" and not gap.gap_up and entry < gap.prev_close:
        return (
            f"⚠️ Unfilled gap-down at ${gap.prev_close:.2f} "
            f"({abs(gap.gap_pct):.1%} above open) — potential overhead resistance"
        )

    if direction == "short" and gap.gap_up and entry > gap.prev_close:
        return (
            f"⚠️ Unfilled gap-up at ${gap.prev_close:.2f} "
            f"({gap.gap_pct:.1%} below open) — potential downside support"
        )

    return None
