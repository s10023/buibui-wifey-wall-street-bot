"""Assemble the cross-sectional momentum verdict: headline metrics + guards.

Pure over an XSBookResult plus the candidate trials' daily returns (the honest
multiple-testing family for DSR/PBO) and the trend sleeve's daily returns (for
the diversification read). Mirrors ``analytics.forecast.report`` and adds
``corr_to_trend`` / ``trend_sharpe``. Annualization (252 NYSE sessions) is
threaded from ``cfg.annualization_days`` into every metric call (D1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast import metrics
from analytics.forecast.config import ForecastConfig
from analytics.research_guards import (
    ann_sharpe,
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
    passes_sleeve_gate,
    per_period_sharpe,
)
from analytics.xsmom.book import XSBookResult, equity_curve


def _aligned_corr(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
    """Pearson corr over the common tail, excluding joint dead warm-up (0, 0)."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    x = np.asarray(a[-n:], dtype=np.float64)
    y = np.asarray(b[-n:], dtype=np.float64)
    live = ~((x == 0.0) & (y == 0.0))
    x, y = x[live], y[live]
    if (
        len(x) < 2
        or float(np.std(x, ddof=1)) < 1e-12
        or float(np.std(y, ddof=1)) < 1e-12
    ):
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


@dataclass(frozen=True)
class XSReport:
    """Headline metrics + guards + the diversification read for the XS verdict."""

    sharpe_annual: float
    sortino_annual: float
    max_dd: float
    calmar: float
    annual_return: float
    annual_vol: float
    n_obs: int
    dsr: float
    pbo: float
    boot_lo: float
    boot_hi: float
    min_trl: float
    corr_to_trend: float
    trend_sharpe: float


def evaluate_xs(
    result: XSBookResult,
    cfg: ForecastConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    trend_returns: npt.NDArray[np.float64],
) -> XSReport:
    """Compute all XS metrics + research-guard stamps + trend diversification.

    ``trial_returns`` is the honest multiple-testing family (per-speed XS sleeves
    + combined) — the same set ``replay_xs_trials`` produces. ``trend_returns`` is
    the trend sleeve's daily portfolio returns on the same universe/window.
    """
    r = result.portfolio_return
    curve = equity_curve(result)
    ann = math.sqrt(cfg.annualization_days)

    sr_d = per_period_sharpe(r)
    trial_srs = [per_period_sharpe(v) for v in trial_returns.values()]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack([v[-min_len:] for v in trial_returns.values()])
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")

    if sr_d != 0.0:

        def _stat_fn(x: npt.NDArray[np.float64]) -> float:
            return ann_sharpe(x, ann)

        boot = block_bootstrap_ci(r, stat_fn=_stat_fn, seed=7)
        boot_lo, boot_hi = boot.lo, boot.hi
        dsr = deflated_sharpe_ratio(sr_d, len(r), trial_srs=trial_srs)
        min_trl = min_track_record_length(sr_d, target_sr=1.0 / ann, confidence=0.95)
    else:
        boot_lo = boot_hi = dsr = 0.0
        min_trl = float("inf")

    if len(trend_returns) >= 2:
        trend_curve = (1.0 + pd.Series(trend_returns)).cumprod()
        trend_sharpe = metrics.sharpe(trend_curve, cfg.annualization_days)
    else:
        trend_sharpe = 0.0
    corr_to_trend = _aligned_corr(r, trend_returns)

    return XSReport(
        sharpe_annual=metrics.sharpe(curve, cfg.annualization_days),
        sortino_annual=metrics.sortino(curve, cfg.annualization_days),
        max_dd=metrics.max_drawdown(curve),
        calmar=metrics.calmar(curve, cfg.annualization_days),
        annual_return=metrics.annual_return(curve, cfg.annualization_days),
        annual_vol=metrics.annual_vol(curve, cfg.annualization_days),
        n_obs=len(r),
        dsr=dsr,
        pbo=pbo,
        boot_lo=boot_lo,
        boot_hi=boot_hi,
        min_trl=min_trl,
        corr_to_trend=corr_to_trend,
        trend_sharpe=trend_sharpe,
    )


@dataclass(frozen=True)
class ResidualGridReport:
    """The 2x2 grid's per-cell XSReports + the pre-registered gate verdict."""

    cells: dict[str, XSReport]
    committed_key: str
    passed: bool


def evaluate_residual_grid(
    books: dict[str, XSBookResult],
    cfg: ForecastConfig,
    trend_by_universe: dict[str, npt.NDArray[np.float64]],
    *,
    committed_key: str = "broad_residual_skip",
) -> ResidualGridReport:
    """Score the 2x2 and read the pre-registered gate on the committed cell.

    The 4-book family feeds the DSR deflation + the CSCV/PBO trial count (the
    honest multiple-testing set = the constructions we selected among). Each
    cell's corr_to_trend uses its own universe's trend returns (key prefix before
    the first '_').
    """
    family = {k: v.portfolio_return for k, v in books.items()}
    cells: dict[str, XSReport] = {}
    for key, book in books.items():
        universe = key.split("_")[0]
        cells[key] = evaluate_xs(
            book,
            cfg,
            trial_returns=family,
            trend_returns=trend_by_universe[universe],
        )
    c = cells[committed_key]
    passed = passes_sleeve_gate(
        dsr=c.dsr,
        pbo=c.pbo,
        boot_lo=c.boot_lo,
        sharpe_annual=c.sharpe_annual,
    )
    return ResidualGridReport(cells=cells, committed_key=committed_key, passed=passed)
