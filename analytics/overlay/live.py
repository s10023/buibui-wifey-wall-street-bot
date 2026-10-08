"""The survival core's state today: OV-1 × VM read off the latest ``^GSPC`` closes.

The core is OV-1 × VM (``docs/north-star.md``, ruled in #429): market exposure is
``pos × w``, where ``pos`` is OV-1's 200-session SMA signal and ``w`` is VM's
``min(1, σ_target / σ̂_20d)``. Both come from :mod:`analytics.overlay.rules`, so
the replay and this read-out share one rule each.

Two deliberate differences from the replay, both read-only and advisory:

* ``σ̂`` here is read from ``^GSPC`` price returns, because the French
  total-return file lags by weeks. A daily dividend is about 0.006%, too small to
  move a 20-session standard deviation.
* ``σ_target`` is the audit's pre-registered in-sample median, pinned as
  :data:`VM_SIGMA_TARGET` rather than recomputed, since the French file is not
  read here.

Every value describes the position to hold over the session after
``as_of``: the signal is decided at that close and earned on the next.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from analytics.overlay.rules import (
    MA_WINDOW,
    VOL_WINDOW,
    ma_signal,
    realized_vol,
    weight_from_sigma,
)

#: The index both legs read. No watchlist carries it, so ``make core-sync``
#: (``wifey analytics sync --core``) keeps it fresh.
CORE_SYMBOL = "^GSPC"

#: The median 20-session σ̂ over 1929-01-02 → 2026-08-31 (11.26% annualised).
#: Audit: ``docs/audits/2026-10-08-vm-overlay-increment.md``.
VM_SIGMA_TARGET = 0.00709

#: A ``1d`` bar dated ``d`` is complete only after the NYSE close, 20:00 UTC in
#: summer and 21:00 UTC in winter. Before this hour a bar dated today is forming.
SESSION_CLOSED_UTC_HOUR = 21

TRADING_DAYS = 252


@dataclass(frozen=True)
class CoreState:
    """OV-1 × VM at one close. ``exposure`` is the market fraction to hold next."""

    as_of: date
    close: float
    sma: float
    ma_in: bool
    sessions_in_state: int
    flip_level: float
    sigma_ann: float
    vm_weight: float

    @property
    def exposure(self) -> float:
        return self.vm_weight if self.ma_in else 0.0

    @property
    def sma_distance(self) -> float:
        """``close / SMA − 1``."""
        return self.close / self.sma - 1.0

    @property
    def flip_distance(self) -> float:
        """How far the next close must move, as a fraction, to flip the MA leg."""
        return self.flip_level / self.close - 1.0


def completed_closes(close: pd.Series, now: datetime) -> pd.Series:
    """``close`` without a bar dated today that has not closed yet (``now`` in UTC)."""
    if now.hour >= SESSION_CLOSED_UTC_HOUR:
        return close
    return close[close.index < pd.Timestamp(now.date())]


def missing_sessions(
    as_of: date, now: datetime, sessions_fn: Callable[[date, date], list[date]]
) -> list[date]:
    """Sessions after ``as_of`` that have closed by ``now`` (UTC) but have no bar.

    A bar dated today counts as closed from :data:`SESSION_CLOSED_UTC_HOUR`, the
    later of the two DST close times, so a pre-open read expects yesterday's
    close. Non-empty means the core describes an old position.
    """
    today = now.date()
    return [
        d
        for d in sessions_fn(as_of + timedelta(days=1), today)
        if d < today or now.hour >= SESSION_CLOSED_UTC_HOUR
    ]


def core_state(close: pd.Series, *, target: float = VM_SIGMA_TARGET) -> CoreState:
    """OV-1 × VM at the last close in ``close`` (daily ``^GSPC``, ascending).

    ``flip_level`` is the next close at which the MA leg changes state. A close
    ``c`` is above the 200-session SMA that includes it exactly when ``c``
    exceeds the mean of the 199 closes before it, so that mean is the level.

    Raises when there are too few closes for either leg, rather than reporting
    a warm-up NaN as a state.
    """
    need = max(MA_WINDOW, VOL_WINDOW + 1)
    if len(close) < need:
        raise ValueError(f"need {need} closes, have {len(close)}")
    signal = ma_signal(close).dropna()
    sigma = realized_vol(close.pct_change()).iloc[-1]
    weight = float(weight_from_sigma(pd.Series([sigma]), target).iloc[0])

    last = signal.iloc[-1]
    switched = signal.ne(signal.shift())
    since = signal.index[switched][-1]
    held = int((signal.index >= since).sum())

    return CoreState(
        as_of=close.index[-1].date(),
        close=float(close.iloc[-1]),
        sma=float(close.iloc[-MA_WINDOW:].mean()),
        ma_in=bool(last == 1.0),
        sessions_in_state=held,
        flip_level=float(close.iloc[-(MA_WINDOW - 1) :].mean()),
        sigma_ann=float(sigma) * math.sqrt(TRADING_DAYS),
        vm_weight=weight,
    )


def format_core(state: CoreState) -> str:
    """One line for the digest and the daily Telegram."""
    leg = "IN" if state.ma_in else "OUT"
    direction = "below" if state.ma_in else "above"
    return (
        f"Core OV-1×VM: exposure {state.exposure:.0%} · MA {leg} "
        f"{state.sessions_in_state} sessions, ^GSPC {state.close:,.0f} vs SMA200 "
        f"{state.sma:,.0f} ({state.sma_distance:+.1%}), flips {direction} "
        f"{state.flip_level:,.0f} ({state.flip_distance:+.1%}) · VM σ̂20 "
        f"{state.sigma_ann:.1%} → w {state.vm_weight:.2f} · as of {state.as_of}"
    )
