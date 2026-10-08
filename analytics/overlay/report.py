"""The overlay yardstick: survival against buy-and-hold, with Sharpe non-inferiority.

``docs/north-star.md`` § Two yardsticks, as frozen for OV-1 in the edge-pillars
spec § Phase 2. FOUND needs all three legs:

1. ``ΔUI = UI(ov) − UI(bh)`` has a bootstrap 95% CI upper bound below 0;
2. ``UI(ov) / UI(bh) <= 0.75`` on the point estimate;
3. ``SR(ov) − SR(bh)`` (net, annualised) has a bootstrap 95% CI lower bound
   above −0.10.

EXCLUDED if the leg-1 CI lies wholly at or above 0, or the leg-3 CI upper bound
is below −0.10; that clause is checked first, because a definitive failure on
either leg is not rescued by the other. BOUNDED if leg 1 passes and leg 2 or 3
does not. INSUFFICIENT otherwise: the leg-1 CI contains 0, so the survival
change is not distinguishable from zero. OV-1's pre-registration named no
verdict for that case; the spec's Amendment 1 (VM, #421) named it, which
changes none of OV-1's readings.

Every function takes the benchmark ``base`` and the candidate ``arm``, so VM's
increment test reuses the yardstick with OV-1 as the benchmark.

Sharpe here is on returns in excess of ``rf``. Both legs are bootstrapped from
the same paired resamples: each call to ``block_bootstrap_ci`` reseeds, and the
statistic draws nothing from the generator, so equal seeds give equal indices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.research_guards.bootstrap import BootstrapCI, block_bootstrap_ci
from analytics.research_guards.sharpe import per_period_sharpe
from analytics.research_guards.survival import (
    max_drawdown,
    time_under_water,
    ulcer_index,
)

TRADING_DAYS = 252
UI_RATIO_BAR = 0.75
SHARPE_MARGIN = 0.10
# Pre-registered bootstrap design. Changing any of these re-opens the trial count.
N_BOOT = 5_000
BLOCK = 252
SEED = 20261008


def calendar_years(index: pd.Index) -> float:
    """Span in calendar years, first to last session.

    Not ``len / 252``: the French calendar carries ~1,040 pre-1952 Saturday
    sessions, so a session count overstates the span by about 4% on the OV-1
    panel. Sharpe and volatility keep the repo's ``sqrt(252)`` factor, which on
    a paired difference moves the result by about 2%.
    """
    span = pd.DatetimeIndex(index)
    return float((span[-1] - span[0]).days) / 365.25


def excess_sharpe(r: npt.NDArray[np.float64], rf: npt.NDArray[np.float64]) -> float:
    """Annualised Sharpe of ``r`` in excess of ``rf``."""
    return per_period_sharpe(r - rf) * math.sqrt(TRADING_DAYS)


def _d_ui(a: npt.NDArray[np.float64]) -> float:
    return ulcer_index(a[:, 1]) - ulcer_index(a[:, 0])


def _d_sr(a: npt.NDArray[np.float64]) -> float:
    return excess_sharpe(a[:, 1], a[:, 2]) - excess_sharpe(a[:, 0], a[:, 2])


def paired(
    arms: pd.DataFrame, *, base: str = "bh", arm: str = "ov"
) -> npt.NDArray[np.float64]:
    """The ``[base, arm, rf]`` matrix every paired statistic resamples by row."""
    return arms[[base, arm, "rf"]].to_numpy(dtype=np.float64)


def bootstrap_legs(
    arms: pd.DataFrame,
    *,
    base: str = "bh",
    arm: str = "ov",
    n_boot: int = N_BOOT,
    block: int = BLOCK,
    seed: int = SEED,
) -> tuple[BootstrapCI, BootstrapCI]:
    """Stationary-bootstrap CIs for ``ΔUI`` and ``ΔSR`` (``arm − base``) on shared resamples."""
    a = paired(arms, base=base, arm=arm)
    d_ui = block_bootstrap_ci(
        a, _d_ui, n_boot=n_boot, block=block, method="stationary", seed=seed
    )
    d_sr = block_bootstrap_ci(
        a, _d_sr, n_boot=n_boot, block=block, method="stationary", seed=seed
    )
    return d_ui, d_sr


def overlay_verdict(d_ui: BootstrapCI, ui_ratio: float, d_sr: BootstrapCI) -> str:
    """FOUND / BOUNDED / EXCLUDED / INSUFFICIENT, per the module docstring."""
    if d_ui.lo >= 0.0 or d_sr.hi < -SHARPE_MARGIN:
        return "EXCLUDED"
    leg1 = d_ui.hi < 0.0
    leg2 = ui_ratio <= UI_RATIO_BAR
    leg3 = d_sr.lo > -SHARPE_MARGIN
    if leg1 and leg2 and leg3:
        return "FOUND"
    if leg1:
        return "BOUNDED"
    return "INSUFFICIENT"


@dataclass(frozen=True)
class OverlayGate:
    d_ui: BootstrapCI
    ui_ratio: float
    d_sr: BootstrapCI
    verdict: str


def evaluate_overlay(
    arms: pd.DataFrame,
    *,
    base: str = "bh",
    arm: str = "ov",
    n_boot: int = N_BOOT,
    block: int = BLOCK,
    seed: int = SEED,
) -> OverlayGate:
    """Run the three-leg gate on ``arm`` against ``base``."""
    d_ui, d_sr = bootstrap_legs(
        arms, base=base, arm=arm, n_boot=n_boot, block=block, seed=seed
    )
    ratio = ulcer_index(arms[arm].to_numpy()) / ulcer_index(arms[base].to_numpy())
    return OverlayGate(d_ui, ratio, d_sr, overlay_verdict(d_ui, ratio, d_sr))


@dataclass(frozen=True)
class ArmSummary:
    ann_return: float
    ann_vol: float
    sharpe: float
    ulcer: float
    max_dd: float
    tuw_sessions: int
    switches_per_year: float
    in_market: float
    turnover_per_year: float


def summarize_arm(
    arms: pd.DataFrame, arm: str, *, pos: str | None = None
) -> ArmSummary:
    """Reported, not gated: return, volatility, drawdown shape and turnover.

    ``pos`` names the arm's position column; without it the arm is held fully
    every session, as ``bh`` is. A switch is any session whose position moved,
    so on a fractional weight it counts nearly every session and turnover
    (``Σ|Δpos|`` per calendar year) is the figure to read.
    """
    r = arms[arm].to_numpy(dtype=np.float64)
    rf = arms["rf"].to_numpy(dtype=np.float64)
    years = calendar_years(arms.index)
    wealth = float(np.prod(1.0 + r))
    if pos is not None:
        delta = arms[pos].diff().abs()
        switches = float((delta > 0).sum()) / years
        turnover = float(delta.sum()) / years
        in_market = float(arms[pos].mean())
    else:
        switches, turnover, in_market = 0.0, 0.0, 1.0
    return ArmSummary(
        ann_return=wealth ** (1.0 / years) - 1.0,
        ann_vol=float(np.std(r, ddof=1)) * math.sqrt(TRADING_DAYS),
        sharpe=excess_sharpe(r, rf),
        ulcer=ulcer_index(r),
        max_dd=max_drawdown(r),
        tuw_sessions=time_under_water(r),
        switches_per_year=switches,
        in_market=in_market,
        turnover_per_year=turnover,
    )


@dataclass(frozen=True)
class BetaAttribution:
    beta: float
    alpha_annual: float
    alpha_t: float


def beta_attribution(arms: pd.DataFrame, arm: str = "ov") -> BetaAttribution:
    """OLS of ``arm``'s excess return on the market's. Reported, not the yardstick."""
    rf = arms["rf"].to_numpy(dtype=np.float64)
    y = arms[arm].to_numpy(dtype=np.float64) - rf
    x = arms["bh"].to_numpy(dtype=np.float64) - rf
    xc = x - x.mean()
    beta = float(np.dot(xc, y - y.mean()) / np.dot(xc, xc))
    alpha = float(y.mean() - beta * x.mean())
    resid = y - alpha - beta * x
    n = len(y)
    s2 = float(np.dot(resid, resid)) / (n - 2)
    se_alpha = math.sqrt(s2 * (1.0 / n + x.mean() ** 2 / float(np.dot(xc, xc))))
    return BetaAttribution(beta, alpha * TRADING_DAYS, alpha / se_alpha)
