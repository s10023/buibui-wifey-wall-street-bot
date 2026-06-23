"""Assemble the PEAD-lite 2x2 verdict: per-cell guards + equity-β diagnostic + gate.

Reuses ``evaluate_xs`` (DSR/PBO/boot-CI/MinTRL) and ``beta_attribution`` from the
xsmom sleeve. The gate is read on the pre-committed ``broad_ls`` cell only; the
full 4-book family feeds the DSR deflation + CSCV/PBO trial count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.diagnostics import BetaAttribution, beta_attribution
from analytics.xsmom.report import XSReport, evaluate_xs

_GATE_SHARPE = 0.7  # pre-registered net-of-cost dollar-neutral bar
_DEPLOY_SHARPE = 1.0  # deploy-grade tier annotation (NOT the pass/fail line)


@dataclass(frozen=True)
class PeadGridReport:
    """The 2x2 grid's per-cell XSReports + realized-β attribution + verdict."""

    cells: dict[str, XSReport]
    attribution: dict[str, BetaAttribution]
    committed_key: str
    passed: bool
    deploy_grade: bool

    @property
    def realized_beta(self) -> float:
        """Realized β of the committed book to SPY — expected ≈ 0 (event-driven)."""
        return self.attribution[self.committed_key].beta


def evaluate_pead_grid(
    books: dict[str, XSBookResult],
    cfg: ForecastConfig,
    market_ret: pd.Series,
    *,
    committed_key: str = "broad_ls",
    long_only_key: str = "broad_long",
) -> PeadGridReport:
    """Score the 2x2 and read the pre-registered gate on the committed cell.

    ``market_ret`` is the SPY daily return used for the realized-portfolio-β
    diagnostic — for the dollar-neutral PEAD cell it is expected ≈ 0, confirming
    the drift is an event anomaly and not equity beta in disguise. PEAD has no
    trend-sleeve comparison, so an empty trend array is passed to ``evaluate_xs``.
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
    return PeadGridReport(
        cells=cells,
        attribution=attribution,
        committed_key=committed_key,
        passed=passed,
        deploy_grade=deploy_grade,
    )
