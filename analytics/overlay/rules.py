"""Overlay rules: a signal on the ``^GSPC`` calendar, mapped onto a return calendar.

OV-1's rule is H1's ``ma200d`` unchanged: long while the close is above its
200-session simple moving average. The signal and the returns live on different
calendars (``^GSPC`` has no pre-1952 Saturdays; the French file does), so the
mapping from one to the other is where a look-ahead could enter, and it lives in
one function, :func:`position_on`.

VM's rule (#421, the spec's Amendment 1) is a fractional weight
``min(1, σ_target / σ̂_20d)`` read from the return series itself, so it needs no
calendar mapping: :func:`vol_weight` shifts it onto the next session.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MA_WINDOW = 200


def ma_signal(close: pd.Series, window: int = MA_WINDOW) -> pd.Series:
    """1.0 while ``close_t`` is above its ``window``-close SMA at ``t``, else 0.0.

    NaN until ``window`` closes exist, so a warm-up session can never read as
    flat. Computed from closes up to and including ``t``.
    """
    sma = close.rolling(window).mean()
    return (close > sma).astype(float).where(sma.notna())


def position_on(
    signal: pd.Series, dates: pd.DatetimeIndex, *, lag: int = 1
) -> pd.Series:
    """The position held over each session in ``dates``.

    With ``lag >= 1`` the position for session ``d`` is the signal at the
    ``lag``-th latest signal date strictly before ``d``: ``lag=1`` is "decide at
    the prior close, earn the next session", and ``lag=2`` adds one session of
    execution delay. Where no such signal date exists the position is NaN.

    ``lag=0`` admits the signal dated ``d`` itself, which reads the close that
    ends the session being earned. It is non-causal by construction and exists
    only as the causality test's positive control.
    """
    if lag < 0:
        raise ValueError("lag must be >= 0")
    idx = signal.index
    if lag == 0:
        k = np.searchsorted(idx.values, dates.values, side="right") - 1
    else:
        k = np.searchsorted(idx.values, dates.values, side="left") - lag
    values = signal.to_numpy(dtype=float)
    out = np.where(k >= 0, values[np.clip(k, 0, None)], np.nan)
    return pd.Series(out, index=dates, name="pos")


VOL_WINDOW = 20


def realized_vol(returns: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    """Sample standard deviation (ddof 1) of the ``window`` returns ending at ``t``.

    NaN until ``window`` returns exist. Read from returns up to and including
    ``t``, so the weight built on it must be shifted before it is held.
    """
    return returns.rolling(window).std(ddof=1)


def vol_weight(
    returns: pd.Series,
    target: float | pd.Series,
    *,
    window: int = VOL_WINDOW,
    lag: int = 1,
) -> pd.Series:
    """VM's weight ``min(1, target / σ̂)``, held over each session of ``returns``.

    ``returns`` is the return calendar itself, so the mapping is a plain shift:
    ``lag=1`` holds over session ``t+1`` the weight set from ``σ̂_t``, and
    ``lag=2`` adds one session of execution delay. A zero ``σ̂`` gives full
    weight. ``target`` may be a series aligned to ``returns`` (the real-time
    sensitivity's expanding median).

    ``lag=0`` holds the weight read from the session being earned. It is
    non-causal by construction and exists only as the causality test's
    positive control.
    """
    if lag < 0:
        raise ValueError("lag must be >= 0")
    sigma = realized_vol(returns, window)
    w = (target / sigma).clip(upper=1.0)
    return w.shift(lag).rename("w")
