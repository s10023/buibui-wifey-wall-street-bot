"""Gap-fill replay + report tests (edge-hunt #5).

The load-bearing one is `TestLoaderMatchesForecastLoader`. This sleeve needed a
second daily loader (gaps need OHLC, `load_daily_inputs` returns closes only),
and two loaders over one table is exactly how two surfaces end up describing
different populations while both look right. Asserting byte-equality of the
closes turns "I think I copied the index conventions" into a checked
precondition — the same move `/post-branch` demands of any measurement script.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.gapfill.replay import (
    COMMITTED_KEY,
    gapfill_market_return,
    load_daily_ohlc,
    replay_gapfill_grid,
)
from analytics.gapfill.report import (
    CONTROL_KEY,
    GapfillGridReport,
    evaluate_gapfill_grid,
)
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema

_DAY = 86_400_000
_T0 = 1_514_764_800_000  # 2018-01-01T00:00:00Z in ms
_N = 500


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    """A walk with real overnight gaps: the open is the prior close plus a jump."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.012, _N))
    prev = np.concatenate([[close[0]], close[:-1]])
    open_ = prev * (1 + rng.normal(0.0, 0.008, _N))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.0, 0.004, _N)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.0, 0.004, _N)))
    upsert_ohlcv(
        conn,
        pd.DataFrame(
            {
                "symbol": sym,
                "timeframe": "1d",
                "open_time": [_T0 + i * _DAY for i in range(_N)],
                "open": open_,
                "high": hi,
                "low": lo,
                "close": close,
                "volume": 1_000_000.0,
            }
        ),
    )


def _db(symbols: list[str]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    for i, s in enumerate(symbols):
        _seed(conn, s, seed=i)
    return conn


class TestLoaderMatchesForecastLoader:
    def test_closes_are_identical_to_load_daily_inputs(self) -> None:
        syms = ["A", "B", "C"]
        conn = _db(syms)
        _, closes, fundings = load_daily_ohlc(conn, syms)
        ref_closes, ref_fundings = load_daily_inputs(conn, syms)
        assert set(closes) == set(ref_closes)
        for s in syms:
            pd.testing.assert_series_equal(closes[s], ref_closes[s], check_names=False)
            pd.testing.assert_series_equal(
                fundings[s], ref_fundings[s], check_names=False
            )

    def test_bars_carry_ohlc_on_the_same_index(self) -> None:
        conn = _db(["A"])
        bars, closes, _ = load_daily_ohlc(conn, ["A"])
        assert list(bars["A"].columns) == ["open", "high", "low", "close"]
        pd.testing.assert_index_equal(bars["A"].index, closes["A"].index)

    def test_missing_symbol_is_skipped_not_faked(self) -> None:
        conn = _db(["A"])
        bars, closes, _ = load_daily_ohlc(conn, ["A", "NOPE"])
        assert set(bars) == {"A"}
        assert set(closes) == {"A"}


class TestGrid:
    def test_returns_the_four_pre_registered_arms(self) -> None:
        syms = ["A", "B", "C", "D", "E", "F"]
        conn = _db(syms)
        books = replay_gapfill_grid(conn, ForecastConfig(), syms)
        assert set(books) == {
            "broad_ls",
            "broad_ls_range",
            "reversal_control",
            "long_only",
        }
        assert COMMITTED_KEY in books
        assert CONTROL_KEY in books
        assert books[COMMITTED_KEY].portfolio_return.shape[0] > 0

    def test_empty_universe_returns_no_books(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        assert replay_gapfill_grid(conn, ForecastConfig(), ["NOPE"]) == {}

    def test_range_arm_trades_a_subset_of_the_broad_arm(self) -> None:
        """The regime arm zeroes positions outside the range regime, so it can
        never be live on more days than the unconditioned book."""
        syms = ["A", "B", "C", "D"]
        conn = _db(syms)
        books = replay_gapfill_grid(conn, ForecastConfig(), syms)
        broad_live = int((books["broad_ls"].portfolio_return != 0.0).sum())
        range_live = int((books["broad_ls_range"].portfolio_return != 0.0).sum())
        assert 0 < range_live <= broad_live

    def test_range_gating_masks_ROWS_not_columns(self) -> None:
        """`DataFrame.where(Series)` aligns the Series on the INDEX.

        Pinned because the sibling behaviour (most DataFrame/Series binary ops
        broadcasting across columns) is the opposite, and getting it wrong would
        silently zero the wrong axis — the range arm would still produce a
        plausible non-empty book, so nothing else here would notice.
        """
        idx = pd.date_range("2020-01-01", periods=4, tz="UTC")
        df = pd.DataFrame({"A": [1.0, 2, 3, 4], "B": [5.0, 6, 7, 8]}, index=idx)
        cond = pd.Series([True, False, True, False], index=idx)
        masked = df.where(cond, 0.0)
        assert list(masked["A"]) == [1.0, 0.0, 3.0, 0.0]
        assert list(masked["B"]) == [5.0, 0.0, 7.0, 0.0]

    def test_market_return_is_a_series(self) -> None:
        conn = _db(["A", "B"])
        mkt = gapfill_market_return(conn, ["A", "B"])
        assert isinstance(mkt, pd.Series)
        assert len(mkt) > 0


@pytest.fixture(scope="module")
def report() -> GapfillGridReport:
    """Module-scoped: building the grid costs ~10s and five tests read it.

    Per-test construction put five 10s calls in `make test`'s slowest-10 list on
    first run. Same fix `pead`'s fixture already carries — the sleeve tests are
    already half the suite's runtime, so a new one has to pay its own way.
    """
    syms = ["A", "B", "C", "D", "E", "F"]
    conn = _db(syms)
    cfg = ForecastConfig()
    books = replay_gapfill_grid(conn, cfg, syms)
    return evaluate_gapfill_grid(books, cfg, gapfill_market_return(conn, syms))


class TestReport:
    def test_scores_every_cell_and_reads_the_committed_gate(
        self, report: GapfillGridReport
    ) -> None:
        rep = report
        assert set(rep.cells) == {
            "broad_ls",
            "broad_ls_range",
            "reversal_control",
            "long_only",
        }
        assert rep.committed_key == COMMITTED_KEY
        assert isinstance(rep.passed, bool)
        assert isinstance(rep.deploy_grade, bool)

    def test_control_has_no_correlation_to_itself(
        self, report: GapfillGridReport
    ) -> None:
        """`corr_to_reversal` on the control row would be a tautological 1.0, so
        it is NaN by construction — a 1.0 there would read as a confound."""
        rep = report
        assert np.isnan(rep.corr_to_reversal[CONTROL_KEY])
        assert not np.isnan(rep.corr_to_reversal["broad_ls"])

    def test_correlation_is_in_range(self, report: GapfillGridReport) -> None:
        rep = report
        c = rep.corr_to_reversal["broad_ls"]
        assert -1.0 <= c <= 1.0

    def test_deploy_grade_implies_passed(self, report: GapfillGridReport) -> None:
        rep = report
        assert not rep.deploy_grade or rep.passed

    def test_beta_attribution_present_for_every_cell(
        self, report: GapfillGridReport
    ) -> None:
        rep = report
        for key in rep.cells:
            assert np.isfinite(rep.attribution[key].beta) or np.isnan(
                rep.attribution[key].beta
            )
