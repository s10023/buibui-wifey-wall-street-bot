"""Unit tests for the velocity sleeve's replay wiring and verdict assembly.

``mean_gross_turnover`` gets exact arithmetic on a hand-built leverage frame
rather than a re-derivation, because it is the number that decides tradeability
(edge-hunt #5 died at ~211x daily gross) and a test that recomputes its subject
proves nothing about it.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.velocity.replay import (
    COMMITTED_KEY,
    CONTROL_KEYS,
    replay_velocity_grid,
    velocity_market_return,
)
from analytics.velocity.report import (
    VelocityGridReport,
    evaluate_velocity_grid,
    mean_gross_turnover,
)

_DAY = 86_400_000
_T0 = 1_514_764_800_000
_N = 500


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.02, _N))
    df = pd.DataFrame(
        {
            "symbol": sym,
            "timeframe": "1d",
            "open_time": [_T0 + i * _DAY for i in range(_N)],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        }
    )
    upsert_ohlcv(conn, df)


@pytest.fixture(scope="module")
def grid() -> VelocityGridReport:
    """Module-scoped: building the family costs ~9s (bootstrap + 4 books).

    Six tests read it and none mutates it, so a function-scoped fixture made this
    file the four slowest tests in the whole suite. Same reason the pead fixture
    is module-scoped — see [[project_suite_runtime_profile]].
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    cfg = ForecastConfig()
    books, leverages = replay_velocity_grid(conn, cfg, syms)
    mkt = velocity_market_return(conn, syms)
    return evaluate_velocity_grid(books, leverages, cfg, mkt)


class TestMeanGrossTurnover:
    def test_exact_arithmetic_on_a_known_frame(self) -> None:
        lev = pd.DataFrame({"A": [0.0, 1.0, 1.0, 0.0], "B": [0.0, 0.0, 2.0, 2.0]})
        # per-row |delta| sums, first row dropped: 1.0, 2.0, 1.0 -> mean 4/3
        assert np.isclose(mean_gross_turnover(lev), 4.0 / 3.0)

    def test_a_frozen_book_has_zero_turnover(self) -> None:
        lev = pd.DataFrame({"A": [3.0] * 5, "B": [-2.0] * 5})
        assert mean_gross_turnover(lev) == 0.0

    def test_nan_leverage_is_treated_as_flat_not_dropped(self) -> None:
        """A NaN row is warm-up, i.e. no position — not a missing observation."""
        lev = pd.DataFrame({"A": [np.nan, np.nan, 1.0, 1.0]})
        assert np.isclose(mean_gross_turnover(lev), 1.0 / 3.0)

    def test_empty_frame_is_nan_not_zero(self) -> None:
        assert np.isnan(mean_gross_turnover(pd.DataFrame()))


class TestGridStructure:
    def test_family_shape_and_committed_cell(self, grid: VelocityGridReport) -> None:
        rep = grid
        assert isinstance(rep, VelocityGridReport)
        assert set(rep.cells) == {
            "broad_ls",
            "depth_control",
            "duration_control",
            "long_only",
        }
        assert rep.committed_key == COMMITTED_KEY == "broad_ls"
        assert set(rep.attribution) == set(rep.cells)
        assert set(rep.gross_turnover) == set(rep.cells)
        assert isinstance(rep.passed, bool)
        assert isinstance(rep.deploy_grade, bool)

    def test_every_cell_reports_a_finite_beta_and_turnover(
        self, grid: VelocityGridReport
    ) -> None:
        rep = grid
        for key in rep.cells:
            assert np.isfinite(rep.attribution[key].beta), key
            assert np.isfinite(rep.gross_turnover[key]), key

    def test_deploy_grade_implies_passed(self, grid: VelocityGridReport) -> None:
        rep = grid
        assert not rep.deploy_grade or rep.passed


class TestControlCorrelations:
    def test_a_control_is_never_correlated_against_itself(
        self, grid: VelocityGridReport
    ) -> None:
        rep = grid
        for ctrl in CONTROL_KEYS:
            assert np.isnan(rep.corr_to_controls[ctrl][ctrl])

    def test_committed_cell_reports_both_control_correlations(
        self, grid: VelocityGridReport
    ) -> None:
        """The decomposition read is the point of the sleeve — it must exist."""
        rep = grid
        corr = rep.corr_to_controls[COMMITTED_KEY]
        assert set(corr) == set(CONTROL_KEYS)
        for ctrl in CONTROL_KEYS:
            assert np.isfinite(corr[ctrl]), f"{ctrl} correlation is not finite"
            assert -1.0 <= corr[ctrl] <= 1.0
