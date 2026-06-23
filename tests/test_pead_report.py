"""Edge-hunt #4 (PEAD-lite): grid report structure + pre-registered gate."""

from __future__ import annotations

import duckdb
import numpy as np

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


def test_grid_report_structure_and_gate() -> None:
    conn = _seeded_conn()
    cfg = ForecastConfig()
    books = replay_pead_grid(conn, cfg, window=60)
    spy = pead_market_return(conn)
    rep = evaluate_pead_grid(books, cfg, spy)

    assert isinstance(rep, PeadGridReport)
    assert set(rep.cells) == {"broad_ls", "broad_long", "mega_ls", "mega_long"}
    assert rep.committed_key == "broad_ls"
    assert set(rep.attribution) == set(rep.cells)
    assert isinstance(rep.passed, bool)
    assert isinstance(rep.deploy_grade, bool)


def test_realized_beta_is_finite() -> None:
    conn = _seeded_conn()
    cfg = ForecastConfig()
    rep = evaluate_pead_grid(
        replay_pead_grid(conn, cfg, window=60), cfg, pead_market_return(conn)
    )
    assert np.isfinite(rep.realized_beta)
