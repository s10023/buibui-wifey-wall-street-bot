"""Assemble the velocity-alternation verdict: guards + beta + decomposition + turnover.

Reuses ``evaluate_xs`` (DSR/PBO/boot-CI) and ``beta_attribution`` from the xsmom
sleeve, and reads ``passes_sleeve_gate`` on the pre-committed ``broad_ls`` cell
only; the full four-book family feeds the DSR deflation and the CSCV/PBO trial
count.

Two fields this sleeve adds over ``gapfill``'s report, each earned by a prior
edge-hunt's failure mode:

``corr_to_controls``  ``velocity = depth / duration``, so the thesis arm is a
                      deterministic function of the two control arms' inputs. A
                      high correlation to either means the ratio contributed
                      nothing over a component that was already known — the
                      ``corr_to_trend`` +0.62 read that sank xsmom.
``gross_turnover``    mean daily sum of ``|Δleverage|``. Edge-hunt #5 booked
                      ~211x daily gross, where a 1bp fee alone costs ~0.9 Sharpe,
                      so a headline Sharpe with no turnover beside it is not a
                      tradeability claim.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.research_guards import DEPLOY_SHARPE, GATE_SHARPE, passes_sleeve_gate
from analytics.velocity.replay import CONTROL_KEYS
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.diagnostics import BetaAttribution, beta_attribution
from analytics.xsmom.report import XSReport, evaluate_xs


@dataclass(frozen=True)
class VelocityGridReport:
    """Per-cell XSReports + beta attribution + decomposition + turnover."""

    cells: dict[str, XSReport]
    attribution: dict[str, BetaAttribution]
    corr_to_controls: dict[str, dict[str, float]]
    gross_turnover: dict[str, float]
    committed_key: str
    passed: bool
    deploy_grade: bool


def _corr(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
    """Pearson correlation over rows where BOTH books are live.

    Restricted to jointly non-zero rows for the same reason ``beta_attribution``
    is: warm-up and flat days are `0.0` by construction and a shared run of zeros
    manufactures correlation out of inactivity.
    """
    live = (a != 0.0) & (b != 0.0)
    if live.sum() < 2:
        return float("nan")
    x, y = a[live], b[live]
    if x.std() == 0.0 or y.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def mean_gross_turnover(leverage: pd.DataFrame) -> float:
    """Mean daily gross turnover: `mean_t( sum_i |lev[i,t] - lev[i,t-1]| )`.

    Same quantity the ``run_xs_backtest`` cost model charges against, so it is
    directly comparable to a fee in bps: turnover N means an N-bps round-trip
    drag per 1bp of fee.
    """
    delta = leverage.fillna(0.0).diff().abs().sum(axis=1)
    if delta.empty:
        return float("nan")
    return float(delta.iloc[1:].mean())


def evaluate_velocity_grid(
    books: dict[str, XSBookResult],
    leverages: dict[str, pd.DataFrame],
    cfg: ForecastConfig,
    market_ret: pd.Series,
    *,
    committed_key: str = "broad_ls",
    long_only_key: str = "long_only",
) -> VelocityGridReport:
    """Score the family and read the pre-registered gate on the committed cell.

    ``market_ret`` is the equal-weight market daily return over the same universe
    and window, used for the realized-portfolio-beta guardrail. The L/S cells are
    beta-neutralized, so a large realized beta means the neutralization FAILED and
    that cell is not evidence about the thesis — the ``lowvol`` / ``pead``
    precedent, where a fired guardrail invalidated the construction rather than
    the premise. Note ``_beta_neutralize`` silently leaves a day untouched when
    the short leg is degenerate (#198 measured 10.2% of active days), which is the
    usual mechanism behind a fired guardrail.

    ``corr_to_trend`` / ``trend_sharpe`` in each ``XSReport`` are unused here (no
    trend-sleeve comparison), so an empty trend array is passed, exactly as the
    BAB and gapfill reports do.
    """
    family = {k: v.portfolio_return for k, v in books.items()}
    empty_trend = np.asarray([], dtype=np.float64)

    cells: dict[str, XSReport] = {}
    attribution: dict[str, BetaAttribution] = {}
    corr_to_controls: dict[str, dict[str, float]] = {}
    gross_turnover: dict[str, float] = {}

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
        corr_to_controls[key] = {
            ctrl: (
                float("nan")
                if key == ctrl or ctrl not in books
                else _corr(r, books[ctrl].portfolio_return)
            )
            for ctrl in CONTROL_KEYS
        }
        lev = leverages.get(key)
        gross_turnover[key] = float("nan") if lev is None else mean_gross_turnover(lev)

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
    return VelocityGridReport(
        cells=cells,
        attribution=attribution,
        corr_to_controls=corr_to_controls,
        gross_turnover=gross_turnover,
        committed_key=committed_key,
        passed=passed,
        deploy_grade=deploy_grade,
    )
