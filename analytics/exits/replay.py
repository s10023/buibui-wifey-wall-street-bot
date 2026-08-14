"""Pluggable exit-replay engine (exit spec §4).

Generalizes the fixed SL/TP/time-expiry walk (`engine.py` / `_scan_forward`)
into a single evaluator parameterized by an `ExitPolicyConfig`. Given the OHLCV
window strictly after entry and the alert's original entry + `sl_price`, it walks
bars and returns the re-resolved `(outcome, realized_r, exit_bar)` under the
policy. Both policy #0 (fixed) and the composite run through this one function,
so the A/B is apples-to-apples (same entries, same SL).

Conventions (anti-bias, exit spec §4):

  - **adverse-first** on any same-bar ambiguity: the stop is checked before the
    profit targets, so a bar that spans both SL and TP resolves as a stop-out.
  - **no look-ahead in the trail/BE level:** arming breakeven at bar *i* moves
    the stop to entry only from bar *i+1*; the bar that arms it cannot also be
    stopped at the new (BE) level.
  - **partials** accumulate position-weighted R: Σ legᵢ_frac × legᵢ_R.

R is measured in units of the original risk |entry − sl|, so the SL sits at
R = −1 and breakeven at R = 0 by construction. Excursions are gross of costs
(price-path geometry); cost-netting is a downstream concern.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from analytics.exits.policies import ExitPolicyConfig

_EPS = 1e-12


@dataclass(frozen=True)
class ExitOutcome:
    """Re-resolved exit for one alert under a policy.

    `outcome` is the mechanism that closed the *remaining* position:
    "win" (tp_r), "loss" (SL at −1R), "breakeven" (BE stop at 0R), or
    "expired" (time-stop mark-to-market). `realized_r` is position-weighted
    across the partial + remaining legs. `exit_bar` is the 0-based index into
    the window of the closing bar.
    """

    outcome: str
    realized_r: float
    exit_bar: int
    partial_taken: bool


def replay_exits(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    *,
    direction: str,
    entry: float,
    sl_price: float,
    policy: ExitPolicyConfig,
) -> ExitOutcome:
    """Re-resolve one alert's exit over its forward window under `policy`.

    `highs`/`lows`/`closes` are the bars strictly after the signal candle, in
    time order. Raises ValueError on zero risk or an empty window (the caller
    filters these, mirroring `PaperBook`'s zero-risk skip).
    """
    risk = abs(entry - sl_price)
    if risk <= 0.0:
        raise ValueError("risk (|entry - sl_price|) must be > 0")
    n = len(highs)
    if n == 0:
        raise ValueError("empty window")

    if direction == "long":
        fav = (highs - entry) / risk
        adv = (lows - entry) / risk
        close_r = (closes - entry) / risk
    else:
        fav = (entry - lows) / risk
        adv = (entry - highs) / risk
        close_r = (entry - closes) / risk

    ts = policy.effective_time_stop_bars
    arm_r = policy.breakeven_arm_r
    stop_r = -1.0
    be_armed = False
    partial_taken = False
    remaining = 1.0
    realized = 0.0

    for i in range(n):
        # 1. stop first (adverse-first)
        if adv[i] <= stop_r + _EPS:
            realized += remaining * stop_r
            outcome = "loss" if stop_r <= -1.0 + 1e-9 else "breakeven"
            return ExitOutcome(outcome, realized, i, partial_taken)

        # 2. partial scale-out
        if (
            policy.has_partial
            and not partial_taken
            and fav[i] >= policy.partial_r - _EPS
        ):
            realized += policy.partial_frac * policy.partial_r
            remaining -= policy.partial_frac
            partial_taken = True

        # 3. full take-profit on the remainder
        if fav[i] >= policy.tp_r - _EPS:
            realized += remaining * policy.tp_r
            return ExitOutcome("win", realized, i, partial_taken)

        # 4. arm breakeven (effective NEXT bar — no same-bar look-ahead)
        be_pending = arm_r is not None and not be_armed and fav[i] >= arm_r - _EPS

        # 5. time-stop -> mark remaining to this bar's close
        if (i + 1) >= ts:
            realized += remaining * close_r[i]
            return ExitOutcome("expired", realized, i, partial_taken)

        if be_pending:
            be_armed = True
            stop_r = max(stop_r, 0.0)

    # window exhausted before any exit -> mark to the last close (expired)
    last = n - 1
    realized += remaining * close_r[last]
    return ExitOutcome("expired", realized, last, partial_taken)
