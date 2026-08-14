"""Exit-policy configuration layer (exit spec §3 / §7).

A policy is a frozen parameter bundle interpreted by `replay_exits`
(`analytics/exits/replay.py`); there is one evaluator, not a class hierarchy —
each named policy is just a different config. v1 ships two:

  - `fixed`     — policy #0, today's behaviour: fixed `tp_r` + time-expiry at
                  `max_hold_bars`, hard `sl_price`. The A/B baseline.
  - `composite` — #1 time-stop + #2 breakeven-at-1R + #6 partial-50%-at-1R
                  (runner → `tp_r`). The lock level (≈1R) comes from the N2
                  MFE/MAE oracle + the 2026-06-15 MFE-timing study.

All policies share the alert's original entry + `sl_price` (apples-to-apples).
Pure data; no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitPolicyConfig:
    """Parameters for one exit policy.

    `time_stop_bars` defaults to the full `max_hold_bars` window when unset
    (i.e. no early time-stop). `breakeven_arm_r` None disables breakeven.
    `partial_frac` 0.0 disables the partial scale-out.
    """

    name: str
    tp_r: float
    max_hold_bars: int
    time_stop_bars: int | None = None
    breakeven_arm_r: float | None = None
    partial_frac: float = 0.0
    partial_r: float = 0.0

    def __post_init__(self) -> None:
        if self.tp_r <= 0.0:
            raise ValueError(f"tp_r must be > 0, got {self.tp_r}")
        if self.max_hold_bars <= 0:
            raise ValueError(f"max_hold_bars must be > 0, got {self.max_hold_bars}")
        if self.time_stop_bars is not None and not (
            1 <= self.time_stop_bars <= self.max_hold_bars
        ):
            raise ValueError(
                f"time_stop_bars must be in [1, {self.max_hold_bars}], "
                f"got {self.time_stop_bars}"
            )
        if self.breakeven_arm_r is not None and self.breakeven_arm_r <= 0.0:
            raise ValueError(f"breakeven_arm_r must be > 0, got {self.breakeven_arm_r}")
        if not (0.0 <= self.partial_frac < 1.0):
            raise ValueError(f"partial_frac must be in [0, 1), got {self.partial_frac}")
        if self.partial_frac > 0.0 and self.partial_r <= 0.0:
            raise ValueError(
                f"partial_r must be > 0 when partial_frac > 0, got {self.partial_r}"
            )

    @property
    def effective_time_stop_bars(self) -> int:
        """Bars after which an unresolved trade is marked-to-market and closed."""
        return (
            self.time_stop_bars
            if self.time_stop_bars is not None
            else self.max_hold_bars
        )

    @property
    def has_partial(self) -> bool:
        return self.partial_frac > 0.0

    @property
    def has_breakeven(self) -> bool:
        return self.breakeven_arm_r is not None


def fixed(*, tp_r: float, max_hold_bars: int) -> ExitPolicyConfig:
    """Policy #0 — today's fixed tp_r + time-expiry, hard SL. The A/B baseline."""
    return ExitPolicyConfig(name="fixed", tp_r=tp_r, max_hold_bars=max_hold_bars)


def composite(
    *,
    tp_r: float,
    max_hold_bars: int,
    time_stop_bars: int,
    breakeven_arm_r: float = 1.0,
    partial_frac: float = 0.5,
    partial_r: float = 1.0,
) -> ExitPolicyConfig:
    """Composite #1 time-stop + #2 breakeven + #6 partial; defaults lock at 1R."""
    return ExitPolicyConfig(
        name="composite",
        tp_r=tp_r,
        max_hold_bars=max_hold_bars,
        time_stop_bars=time_stop_bars,
        breakeven_arm_r=breakeven_arm_r,
        partial_frac=partial_frac,
        partial_r=partial_r,
    )
