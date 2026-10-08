"""TOM's gate: an edge claim on beta-hedged returns (spec § Amendment 2).

TOM is long-only, so its raw Sharpe is partly market exposure (#336, the
velocity lesson). The gate reads the hedged series ``h = (tom − RF) −
β (mkt − RF)`` with ``β`` from :func:`analytics.overlay.report.beta_attribution`,
held at its panel value, and asks three things of it: an annualized Sharpe of
at least ``GATE_SHARPE``, a DSR of at least 0.95 at four trials, and a
bootstrap lower bound above zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.overlay.report import (
    BLOCK,
    N_BOOT,
    SEED,
    TRADING_DAYS,
    beta_attribution,
)
from analytics.research_guards import GATE_SHARPE
from analytics.research_guards.bootstrap import BootstrapCI, block_bootstrap_ci
from analytics.research_guards.dsr import deflated_sharpe_ratio
from analytics.research_guards.gate import GATE_DSR
from analytics.research_guards.sharpe import per_period_sharpe

#: TOM plus the three calendar rows already tested (H-001, H-002, H-021).
N_TRIALS = 4
#: The filed sleeve family's annualized Sharpe variance (2026-09-27 retraction audit).
SR_VARIANCE_ANNUAL = 0.0652


def _ann(x: npt.NDArray[np.float64]) -> float:
    return per_period_sharpe(x) * math.sqrt(TRADING_DAYS)


def hedged_returns(arms: pd.DataFrame, arm: str = "tom") -> tuple[pd.Series, float]:
    """``(tom − RF) − β (mkt − RF)`` and the panel ``β`` it was hedged with."""
    beta = beta_attribution(arms, arm=arm).beta
    h = (arms[arm] - arms["rf"]) - beta * (arms["bh"] - arms["rf"])
    return h.rename("h"), beta


def tom_verdict(sharpe: float, dsr: float, ci: BootstrapCI) -> str:
    """The pre-registered order: FOUND, EXCLUDED, BOUNDED, else INSUFFICIENT."""
    if sharpe >= GATE_SHARPE and dsr >= GATE_DSR and ci.lo > 0.0:
        return "FOUND"
    if ci.hi < GATE_SHARPE:
        return "EXCLUDED"
    if ci.lo > 0.0:
        return "BOUNDED"
    return "INSUFFICIENT"


def premise_reading(ci: BootstrapCI) -> str:
    """Read separately from the verdict, as the spec asks."""
    if ci.hi <= 0.0:
        return "refuted"
    if ci.lo > 0.0:
        return "positive"
    return "not refuted"


@dataclass(frozen=True)
class TomGate:
    beta: float
    hedged_sharpe: float
    dsr: float
    ci: BootstrapCI
    n_obs: int

    @property
    def verdict(self) -> str:
        return tom_verdict(self.hedged_sharpe, self.dsr, self.ci)

    @property
    def premise(self) -> str:
        return premise_reading(self.ci)


def hedged_ci(
    h: pd.Series, *, n_boot: int = N_BOOT, block: int = BLOCK, seed: int = SEED
) -> BootstrapCI:
    """Stationary-bootstrap CI of the hedged annualized Sharpe, ``β`` fixed."""
    return block_bootstrap_ci(
        h.to_numpy(dtype=np.float64),
        stat_fn=_ann,
        n_boot=n_boot,
        block=block,
        method="stationary",
        seed=seed,
    )


def evaluate_tom(
    arms: pd.DataFrame,
    arm: str = "tom",
    *,
    n_boot: int = N_BOOT,
    block: int = BLOCK,
    seed: int = SEED,
) -> TomGate:
    """The three legs on ``arms`` (``bh``, ``rf`` and ``arm``, from ``arm_returns``)."""
    h, beta = hedged_returns(arms, arm)
    x = h.to_numpy(dtype=np.float64)
    dsr = deflated_sharpe_ratio(
        per_period_sharpe(x),
        len(x),
        n_trials=N_TRIALS,
        sr_variance=SR_VARIANCE_ANNUAL / TRADING_DAYS,
        skew=float(pd.Series(x).skew()),  # type: ignore[arg-type]
        kurtosis=float(pd.Series(x).kurt()) + 3.0,  # type: ignore[arg-type]
    )
    ci = hedged_ci(h, n_boot=n_boot, block=block, seed=seed)
    return TomGate(beta=beta, hedged_sharpe=_ann(x), dsr=dsr, ci=ci, n_obs=len(x))


@dataclass(frozen=True)
class WindowSpread:
    """Mean daily market excess return inside and outside the window."""

    inside: float
    outside: float
    diff: BootstrapCI


def window_spread(
    frame: pd.DataFrame, *, n_boot: int = N_BOOT, block: int = BLOCK, seed: int = SEED
) -> WindowSpread:
    """The literature's own statistic, reported, not gated.

    The difference is bootstrapped on paired sessions: each resample keeps a
    session's excess return together with its window label.
    """
    ex = (frame["mkt"] - frame["rf"]).to_numpy(dtype=np.float64)
    inside = frame["pos"].to_numpy(dtype=np.float64) == 1.0
    pairs = np.column_stack([ex, inside.astype(np.float64)])

    def diff(idx_rows: npt.NDArray[np.float64]) -> float:
        rows = pairs[idx_rows.astype(np.int64)]
        mask = rows[:, 1] == 1.0
        if mask.all() or not mask.any():
            return float("nan")
        return float(rows[mask, 0].mean() - rows[~mask, 0].mean())

    ci = block_bootstrap_ci(
        np.arange(len(ex), dtype=np.float64),
        stat_fn=diff,
        n_boot=n_boot,
        block=block,
        method="stationary",
        seed=seed,
    )
    return WindowSpread(
        inside=float(ex[inside].mean()), outside=float(ex[~inside].mean()), diff=ci
    )
