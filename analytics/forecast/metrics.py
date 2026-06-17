"""Risk-adjusted metrics on a daily equity curve (equity port, 252 sessions/yr).

Pure functions over a pandas daily curve (Series indexed by UTC day). Degenerate
inputs (flat / single-point curves) return 0.0 rather than NaN. Curve slice of
the parent's portfolio.metrics — the book-dependent attribution functions are
intentionally not ported (see spec D7).
"""

from __future__ import annotations

import math

import pandas as pd

_PPY = 252.0


def daily_returns(curve: pd.Series) -> pd.Series:
    return curve.pct_change().dropna()


def sharpe(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    if sd < 1e-10:
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sortino(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    if len(r) < 2:
        return 0.0
    downside = r[r < 0.0]
    dd = float(math.sqrt(float((downside**2).mean()))) if len(downside) > 0 else 0.0
    if dd <= 0.0:
        return 0.0
    return float(r.mean() / dd * math.sqrt(periods_per_year))


def max_drawdown(curve: pd.Series) -> float:
    if len(curve) < 2:
        return 0.0
    roll_max = curve.cummax()
    dd = (curve - roll_max) / roll_max
    return float(dd.min())


def annual_return(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    if len(curve) < 2 or curve.iloc[0] <= 0.0:
        return 0.0
    total = curve.iloc[-1] / curve.iloc[0]
    if total <= 0.0:
        return -1.0
    return float(total ** (periods_per_year / len(curve)) - 1.0)


def annual_vol(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    return sd * math.sqrt(periods_per_year)


def calmar(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    mdd = abs(max_drawdown(curve))
    if mdd <= 0.0:
        return 0.0
    return annual_return(curve, periods_per_year) / mdd
