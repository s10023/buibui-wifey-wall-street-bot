"""Pure beta-attribution + forward-persistence diagnostics for the XS sleeve.

No DB/IO; numpy + pandas + stdlib only. Consumed by ``tools/xsmom_audit.py`` to
quantify how much of the headline Sharpe is market beta vs alpha, and whether the
edge persists across calendar years and recent windows. Equity port: ``ann_days``
defaults to 252 NYSE sessions (callers thread ``cfg.annualization_days``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd


def equal_weight_market_return(closes: dict[str, pd.Series]) -> pd.Series:
    """Active-set mean of per-instrument daily returns (the 'alt market').

    Aligns each instrument's ``pct_change()`` to the sorted union daily index and
    averages across the present (non-NaN) instruments each day (skipna). Day-0 of
    each instrument is NaN by construction.
    """
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    union = union.sort_values()
    rets = [c.pct_change().reindex(union) for c in closes.values()]
    mat = pd.concat(rets, axis=1)
    return mat.mean(axis=1)


@dataclass(frozen=True)
class BetaAttribution:
    alpha_annual: float
    beta: float
    alpha_tstat: float
    beta_hedged_sharpe: float
    r_squared: float


def _ann_sharpe(r: npt.NDArray[np.float64], ann_days: float) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd) * math.sqrt(ann_days)


def beta_attribution(
    port_ret: npt.ArrayLike, mkt_ret: npt.ArrayLike, ann_days: float = 252.0
) -> BetaAttribution:
    """Full-sample OLS ``r_port = alpha + beta * r_mkt + eps``.

    Reports the annualized *beta-hedged* Sharpe of ``r_port - beta * r_mkt`` (=
    alpha + residual), NOT the zero-mean OLS residual. Aligns on the common tail,
    drops non-finite rows, and is degenerate-safe (zero-variance market -> beta
    0.0, hedged == port).

    Caller contract: ``port_ret`` and ``mkt_ret`` must already be positionally
    aligned (same dates, same order) — pass arrays built on the *same* index
    (e.g. the XS book's union daily index). The common-tail slice only repairs a
    pure length difference, not a date misalignment. ``alpha_tstat`` is 0.0 when
    the residual variance is numerically indistinguishable from zero (a perfect
    fit), not a true zero-significance signal.
    """
    x = np.asarray(mkt_ret, dtype=np.float64)
    y = np.asarray(port_ret, dtype=np.float64)
    n = min(len(x), len(y))
    x, y = x[-n:], y[-n:]
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]

    if len(x) < 2 or float(np.std(x, ddof=1)) < 1e-12:
        return BetaAttribution(
            alpha_annual=(float(np.mean(y)) * ann_days) if len(y) else 0.0,
            beta=0.0,
            alpha_tstat=0.0,
            beta_hedged_sharpe=_ann_sharpe(y, ann_days),
            r_squared=0.0,
        )

    design = np.column_stack([np.ones(len(x)), x])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    alpha_d, beta = float(coef[0]), float(coef[1])
    resid = y - design @ coef

    dof = len(x) - 2
    sigma2 = float(resid @ resid) / dof if dof > 0 else 0.0
    xtx_inv = np.linalg.inv(design.T @ design)
    se_alpha = math.sqrt(sigma2 * float(xtx_inv[0, 0])) if sigma2 > 0 else 0.0
    tstat = alpha_d / se_alpha if se_alpha > 1e-15 else 0.0

    hedged = y - beta * x
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 1e-15 else 0.0

    return BetaAttribution(
        alpha_annual=alpha_d * ann_days,
        beta=beta,
        alpha_tstat=tstat,
        beta_hedged_sharpe=_ann_sharpe(hedged, ann_days),
        r_squared=r2,
    )


@dataclass(frozen=True)
class PersistenceReport:
    by_year: dict[int, float]
    trailing_2y: float
    trailing_1y: float
    n_obs: int


def subperiod_sharpe(
    port_ret: npt.ArrayLike,
    index: pd.DatetimeIndex,
    ann_days: float = 252.0,
) -> PersistenceReport:
    """Annualized Sharpe per calendar year + trailing 2y / 1y windows.

    Any sub-slice with < 2 observations or ~0 std returns 0.0 (never NaN),
    assuming ``port_ret`` itself contains no NaN values (the XS book's
    ``portfolio_return`` is NaN-free by construction).
    """
    dt_index = pd.DatetimeIndex(index)
    s = pd.Series(np.asarray(port_ret, dtype=np.float64), index=dt_index)
    by_year = {
        int(year): _ann_sharpe(np.asarray(grp, dtype=np.float64), ann_days)
        for year, grp in s.groupby(dt_index.year)
    }
    last = dt_index.max()
    t2 = s[dt_index > last - pd.Timedelta(days=730)]
    t1 = s[dt_index > last - pd.Timedelta(days=365)]
    return PersistenceReport(
        by_year=by_year,
        trailing_2y=_ann_sharpe(np.asarray(t2, dtype=np.float64), ann_days),
        trailing_1y=_ann_sharpe(np.asarray(t1, dtype=np.float64), ann_days),
        n_obs=len(s),
    )
