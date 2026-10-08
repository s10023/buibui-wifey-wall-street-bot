"""Overlay rules: a signal on the ``^GSPC`` calendar, mapped onto a return calendar.

OV-1's rule is H1's ``ma200d`` unchanged: long while the close is above its
200-session simple moving average. The signal and the returns live on different
calendars (``^GSPC`` has no pre-1952 Saturdays; the French file does), so the
mapping from one to the other is where a look-ahead could enter, and it lives in
one function, :func:`position_on`.
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
