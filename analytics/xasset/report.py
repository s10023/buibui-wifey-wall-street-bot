"""Assemble the cross-asset TSMOM 2x2 verdict: per-cell guards + equity-beta + gate.

Reuses ``forecast.report.evaluate`` (DSR/PBO/boot-CI/MinTRL over the 4-book trial
family) and ``xsmom.diagnostics.beta_attribution`` (realized beta to SPY). The
gate is read on the pre-committed ``broad_ls`` cell only; the full 4-book family
feeds the DSR deflation + CSCV/PBO trial count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.forecast.report import G2Report, evaluate
from analytics.xsmom.diagnostics import BetaAttribution, beta_attribution

_GATE_SHARPE = 0.7  # pre-registered net-of-cost TSMOM bar
_DEPLOY_SHARPE = 1.0  # deploy-grade tier annotation (NOT the pass/fail line)


@dataclass(frozen=True)
class XAssetGridReport:
    """The 2x2 grid's per-cell G2Reports + realized equity-beta + verdict."""

    cells: dict[str, G2Report]
    attribution: dict[str, BetaAttribution]
    committed_key: str
    passed: bool
    deploy_grade: bool


def evaluate_xasset_grid(
    books: dict[str, ForecastBookResult],
    cfg: ForecastConfig,
    market_ret: pd.Series,
    *,
    committed_key: str = "broad_ls",
    long_only_key: str = "broad_long",
) -> XAssetGridReport:
    """Score the 2x2 and read the pre-registered gate on the committed cell.

    ``market_ret`` is the SPY daily return; the realized beta of each book to it
    is the thesis guardrail (the committed cross-asset book should read ≈0). The
    diagnostic is restricted to each book's live window (``portfolio_return`` is
    0.0 during the governor warm-up).
    """
    family = {k: v.portfolio_return for k, v in books.items()}
    cells: dict[str, G2Report] = {}
    attribution: dict[str, BetaAttribution] = {}
    for key, book in books.items():
        cells[key] = evaluate(book, cfg, trial_returns=family)
        r = book.portfolio_return
        m: npt.NDArray[np.float64] = market_ret.reindex(book.daily_index).to_numpy(
            dtype=np.float64
        )
        live = r != 0.0  # restrict the diagnostic to the live trading window
        attribution[key] = beta_attribution(r[live], m[live], cfg.annualization_days)

    c = cells[committed_key]
    passed = bool(
        c.dsr >= 0.95
        and c.pbo <= 0.5
        and c.boot_lo > 0.0
        and c.n_obs >= c.min_trl
        and c.sharpe_annual >= _GATE_SHARPE
    )
    lo = cells[long_only_key]
    deploy_grade = bool(
        passed
        and c.sharpe_annual >= _DEPLOY_SHARPE
        and lo.sharpe_annual >= _GATE_SHARPE
    )
    return XAssetGridReport(
        cells=cells,
        attribution=attribution,
        committed_key=committed_key,
        passed=passed,
        deploy_grade=deploy_grade,
    )
