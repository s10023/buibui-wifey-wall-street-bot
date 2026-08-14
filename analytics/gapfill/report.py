"""Assemble the gap-fill verdict: per-cell guards + beta + the reversal confound.

Reuses ``evaluate_xs`` (DSR/PBO/boot-CI/MinTRL stamp) and ``beta_attribution``
from the xsmom sleeve, and reads ``passes_sleeve_gate`` on the pre-committed
``broad_ls`` cell only; the full four-book family feeds the DSR deflation and the
CSCV/PBO trial count.

The one field this sleeve adds over ``lowvol``'s report is ``corr_to_reversal``.
The magnet construction is mechanically a gap-fade (see ``signals`` module
docstring), so "did it beat zero" is not the whole question — "is it anything
other than short-term reversal" is the other half, and a sleeve that correlates
+0.9 with its own control has not found a gap effect whatever its Sharpe.
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

CONTROL_KEY = "reversal_control"


@dataclass(frozen=True)
class GapfillGridReport:
    """Per-cell XSReports + beta attribution + the reversal-confound read."""

    cells: dict[str, XSReport]
    attribution: dict[str, BetaAttribution]
    corr_to_reversal: dict[str, float]
    committed_key: str
    passed: bool
    deploy_grade: bool


def _corr(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
    """Pearson correlation over rows where BOTH books are live.

    Restricted to jointly non-zero rows for the same reason
    ``beta_attribution`` is: warm-up and flat days are `0.0` by construction and
    a shared run of zeros manufactures correlation out of inactivity.
    """
    live = (a != 0.0) & (b != 0.0)
    if live.sum() < 2:
        return float("nan")
    x, y = a[live], b[live]
    if x.std() == 0.0 or y.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def evaluate_gapfill_grid(
    books: dict[str, XSBookResult],
    cfg: ForecastConfig,
    market_ret: pd.Series,
    *,
    committed_key: str = "broad_ls",
    long_only_key: str = "long_only",
) -> GapfillGridReport:
    """Score the family and read the pre-registered gate on the committed cell.

    ``market_ret`` is the equal-weight market daily return over the same universe
    and window, used for the realized-portfolio-beta guardrail — the L/S cells are
    beta-neutralized, so a large realized beta means the neutralization failed and
    that cell is not evidence about the gap premium (the `lowvol` / `pead`
    precedent, where a fired guardrail invalidated the construction rather than
    the thesis).

    ``corr_to_trend`` / ``trend_sharpe`` in each ``XSReport`` are unused here (no
    trend-sleeve comparison), so an empty trend array is passed, exactly as the
    BAB report does.
    """
    family = {k: v.portfolio_return for k, v in books.items()}
    empty_trend = np.asarray([], dtype=np.float64)
    control = books.get(CONTROL_KEY)

    cells: dict[str, XSReport] = {}
    attribution: dict[str, BetaAttribution] = {}
    corr_to_reversal: dict[str, float] = {}
    for key, book in books.items():
        cells[key] = evaluate_xs(
            book, cfg, trial_returns=family, trend_returns=empty_trend
        )
        r = book.portfolio_return
        m: npt.NDArray[np.float64] = market_ret.reindex(book.daily_index).to_numpy(
            dtype=np.float64
        )
        live = r != 0.0
        attribution[key] = beta_attribution(r[live], m[live], cfg.annualization_days)
        corr_to_reversal[key] = (
            float("nan")
            if control is None or key == CONTROL_KEY
            else _corr(r, control.portfolio_return)
        )

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
    return GapfillGridReport(
        cells=cells,
        attribution=attribution,
        corr_to_reversal=corr_to_reversal,
        committed_key=committed_key,
        passed=passed,
        deploy_grade=deploy_grade,
    )
