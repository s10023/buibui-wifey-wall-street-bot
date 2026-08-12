import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.research_guards import passes_sleeve_gate
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.report import evaluate_residual_grid


def _book(mean: float, seed: int, n: int = 600) -> XSBookResult:
    rng = np.random.default_rng(seed)
    r = rng.normal(mean, 0.01, n)
    idx = pd.date_range("2018-01-01", periods=n, freq="D")
    return XSBookResult(
        daily_index=idx,
        portfolio_return=r,
        pre_governor_return=r,
        governor=np.ones(n),
        active_count=np.full(n, 5),
        per_instrument_net={},
    )


def test_evaluate_residual_grid_reads_committed_cell() -> None:
    cfg = ForecastConfig()
    books = {
        "mega_raw": _book(0.0, 1),
        "mega_residual_skip": _book(0.0002, 2),
        "broad_raw": _book(0.0, 3),
        "broad_residual_skip": _book(0.0008, 4),  # the pre-committed cell
    }
    trend = {"mega": np.zeros(600), "broad": np.zeros(600)}
    rep = evaluate_residual_grid(books, cfg, trend_by_universe=trend)
    assert rep.committed_key == "broad_residual_skip"
    assert set(rep.cells) == set(books)
    assert isinstance(rep.passed, bool)
    # The gate reads the committed cell only. Call the shared gate rather than
    # restating its expression: an inline copy of the code under test cannot
    # falsify it, which is why this assertion passed against any gate for as
    # long as it existed. Leg-by-leg coverage lives in test_research_guards_gate.
    c = rep.cells["broad_residual_skip"]
    assert rep.passed == passes_sleeve_gate(
        dsr=c.dsr,
        pbo=c.pbo,
        boot_lo=c.boot_lo,
        n_obs=c.n_obs,
        min_trl=c.min_trl,
        sharpe_annual=c.sharpe_annual,
    )
