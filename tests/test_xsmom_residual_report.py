import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.report import evaluate_residual_grid


def _book(mean: float, seed: int, n: int = 600) -> XSBookResult:
    rng = np.random.default_rng(seed)
    r = rng.normal(mean, 0.01, n)
    idx = pd.date_range("2018-01-01", periods=n, freq="D")
    return XSBookResult(
        daily_index=idx, portfolio_return=r, pre_governor_return=r,
        governor=np.ones(n), active_count=np.full(n, 5), per_instrument_net={},
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
    # the gate reads the committed cell only
    c = rep.cells["broad_residual_skip"]
    expected = (
        c.dsr >= 0.95 and c.pbo <= 0.5 and c.boot_lo > 0.0
        and c.n_obs >= c.min_trl and c.sharpe_annual >= 0.7
    )
    assert rep.passed == expected
