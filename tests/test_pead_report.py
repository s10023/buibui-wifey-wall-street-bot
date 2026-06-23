"""Edge-hunt #4 (PEAD-lite): grid report structure + pre-registered gate.

The grid + evaluate is built once (module-scoped fixture) because
``evaluate_pead_grid`` runs the DSR/PBO/bootstrap stack — too slow to repeat under
the 30s per-test budget.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pytest

from analytics.forecast.config import ForecastConfig
from analytics.pead.replay import pead_market_return, replay_pead_grid
from analytics.pead.report import PeadGridReport, evaluate_pead_grid
from analytics.store.earnings import upsert_earnings_facts
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from tests.test_pead_replay import _earnings_rows, _ohlcv_rows


def _seeded_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_ohlcv(conn, _ohlcv_rows())
    upsert_earnings_facts(conn, _earnings_rows())
    return conn


@pytest.fixture(scope="module")
def report() -> PeadGridReport:
    conn = _seeded_conn()
    cfg = ForecastConfig()
    return evaluate_pead_grid(
        replay_pead_grid(conn, cfg, window=60), cfg, pead_market_return(conn)
    )


def test_grid_report_structure_and_gate(report: PeadGridReport) -> None:
    assert isinstance(report, PeadGridReport)
    assert set(report.cells) == {"broad_ls", "broad_long", "mega_ls", "mega_long"}
    assert report.committed_key == "broad_ls"
    assert set(report.attribution) == set(report.cells)
    assert isinstance(report.passed, bool)
    assert isinstance(report.deploy_grade, bool)


def test_realized_beta_is_finite(report: PeadGridReport) -> None:
    assert np.isfinite(report.realized_beta)


def test_audit_build_grid_smoke() -> None:
    from tools.pead_audit import build_grid

    rep = build_grid(_seeded_conn(), slippage_bps=2.0, window=60)
    assert rep.committed_key == "broad_ls"
    assert isinstance(rep.passed, bool)
