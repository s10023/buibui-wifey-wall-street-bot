"""Assemble the G2 verdict: headline metrics + research-guard stamps.

Pure over a ForecastBookResult plus the candidate trials' daily returns (the
honest multiple-testing family for DSR/PBO). Research guards consume per-period
Sharpe; the curve metrics return annualised (252 NYSE sessions, threaded from
``cfg.annualization_days`` so the annualization factor has a single source).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from analytics.forecast import metrics
from analytics.forecast.book import ForecastBookResult, equity_curve
from analytics.forecast.config import ForecastConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)


def _per_period_sharpe(r: npt.NDArray[np.float64]) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd)


def _ann_sharpe(r: npt.NDArray[np.float64], ann: float) -> float:
    return _per_period_sharpe(r) * ann


@dataclass(frozen=True)
class G2Report:
    """Headline metrics + research-guard stamps for the G2 verdict."""

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


def evaluate(
    result: ForecastBookResult,
    cfg: ForecastConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    pbo_returns: dict[str, npt.NDArray[np.float64]] | None = None,
) -> G2Report:
    """Compute all G2 metrics and research-guard stamps.

    ``trial_returns`` is the honest multiple-testing family (per-speed sleeves
    + combined) — the same set that ``replay_trials`` produces.

    ``pbo_returns`` (optional) is the PBO/CSCV selection family; when given it
    is used for the CSCV matrix only while DSR still deflates against the wider
    ``trial_returns``.  The forecast-weight study passes the schemes-only family
    here.  Defaults to ``trial_returns`` when *None* (back-compat identical).
    """
    r = result.portfolio_return
    curve = equity_curve(result)
    ann = math.sqrt(cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(v) for v in trial_returns.values()]

    # PBO over the selection family (defaults to the DSR family).
    # cscv_pbo's default n_splits=14 needs block_size = T // 14 >= 2, i.e. T >= 28.
    pbo_family = pbo_returns if pbo_returns is not None else trial_returns
    min_len = min((len(v) for v in pbo_family.values()), default=0)
    if min_len >= 28 and len(pbo_family) >= 2:
        mat = np.column_stack([v[-min_len:] for v in pbo_family.values()])
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")

    if sr_d != 0.0:

        def _stat_fn(x: npt.NDArray[np.float64]) -> float:
            return _ann_sharpe(x, ann)

        boot = block_bootstrap_ci(r, stat_fn=_stat_fn, seed=7)
        boot_lo, boot_hi = boot.lo, boot.hi
        dsr = deflated_sharpe_ratio(sr_d, len(r), trial_srs=trial_srs)
        min_trl = min_track_record_length(sr_d, target_sr=1.0 / ann, confidence=0.95)
    else:
        boot_lo = boot_hi = dsr = 0.0
        min_trl = float("inf")

    return G2Report(
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
    )
