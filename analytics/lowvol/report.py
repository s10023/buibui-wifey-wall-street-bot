"""Assemble the BAB 2x2 verdict: per-cell guards + realized-beta diagnostic + gate.

Reuses ``evaluate_xs`` (DSR/PBO/boot-CI/MinTRL) and ``beta_attribution`` from the
xsmom sleeve. The gate is read on the pre-committed ``beta_neutral_ls`` cell only;
the full 4-book family feeds the DSR deflation + CSCV/PBO trial count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.research_guards import DEPLOY_SHARPE, GATE_SHARPE, passes_sleeve_gate
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.diagnostics import BetaAttribution, beta_attribution
from analytics.xsmom.report import XSReport, evaluate_xs


@dataclass(frozen=True)
class BabGridReport:
    """The 2x2 grid's per-cell XSReports + realized-beta attribution + verdict."""

    cells: dict[str, XSReport]
    attribution: dict[str, BetaAttribution]
    committed_key: str
    passed: bool
    deploy_grade: bool


def evaluate_bab_grid(
    books: dict[str, XSBookResult],
    cfg: ForecastConfig,
    market_ret: pd.Series,
    *,
    committed_key: str = "beta_neutral_ls",
    long_only_key: str = "beta_long_only",
) -> BabGridReport:
    """Score the 2x2 and read the pre-registered gate on the committed cell.

    ``market_ret`` is the equal-weight market daily return (same universe/window)
    used for the realized-portfolio-beta diagnostic — for the beta-neutral cell it
    must come out ≈0, else the neutralization is broken. ``corr_to_trend`` /
    ``trend_sharpe`` in the per-cell XSReport are unused for BAB (no trend sleeve
    comparison), so an empty trend array is passed.
    """
    family = {k: v.portfolio_return for k, v in books.items()}
    empty_trend = np.asarray([], dtype=np.float64)
    cells: dict[str, XSReport] = {}
    attribution: dict[str, BetaAttribution] = {}
    for key, book in books.items():
        cells[key] = evaluate_xs(
            book, cfg, trial_returns=family, trend_returns=empty_trend
        )
        r = book.portfolio_return
        m: npt.NDArray[np.float64] = market_ret.reindex(book.daily_index).to_numpy(
            dtype=np.float64
        )
        live = r != 0.0  # restrict the diagnostic to the live trading window
        attribution[key] = beta_attribution(r[live], m[live], cfg.annualization_days)

    c = cells[committed_key]
    passed = passes_sleeve_gate(
        dsr=c.dsr,
        pbo=c.pbo,
        boot_lo=c.boot_lo,
        sharpe_annual=c.sharpe_annual,
    )
    lo = cells[long_only_key]
    deploy_grade = bool(
        passed and c.sharpe_annual >= DEPLOY_SHARPE and lo.sharpe_annual >= GATE_SHARPE
    )
    return BabGridReport(
        cells=cells,
        attribution=attribution,
        committed_key=committed_key,
        passed=passed,
        deploy_grade=deploy_grade,
    )
