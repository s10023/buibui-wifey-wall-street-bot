"""Causal exponentially-weighted volatility estimators for the trend sleeve.

All estimators are shifted so the value at day `d` uses only returns through
day `d-1` — the position held during day `d` is sized on yesterday's information.
"""

from __future__ import annotations

import math

import pandas as pd


def ew_return_vol(close: pd.Series, span: int) -> pd.Series:
    """Causal EW std of daily simple returns (decimal, e.g. 0.03 = 3%/day)."""
    returns = close.pct_change()
    return returns.ewm(span=span, min_periods=span).std().shift(1)


def price_vol(close: pd.Series, span: int) -> pd.Series:
    """Causal price volatility in price units = return-vol x price."""
    return ew_return_vol(close, span) * close


def annualize(daily_vol: float, days: float = 252.0) -> float:
    return daily_vol * math.sqrt(days)
