"""Tests for the n_eff driver, including its refusal to emit unmeasured flags."""

from __future__ import annotations

import duckdb
import pytest

from analytics.research_guards import SeriesDeflator
from tools.n_eff import load_returns, render, resolve_symbols

# The only marker that means flags were actually emitted.
_PASTE_MARKER = "Paste into distil_power:"


def _conn_with_bars(rows: list[tuple[str, int, float]]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE ohlcv (symbol VARCHAR, timeframe VARCHAR, "
        "open_time BIGINT, close DOUBLE)"
    )
    for symbol, open_time, close in rows:
        conn.execute(
            "INSERT INTO ohlcv VALUES (?, '1d', ?, ?)", [symbol, open_time, close]
        )
    return conn


class TestResolveSymbols:
    def test_explicit_list_is_normalised(self) -> None:
        assert resolve_symbols("universe", " spy , qqq ") == ["SPY", "QQQ"]

    def test_explicit_list_overrides_source(self) -> None:
        """--symbols must win over --source without touching config on disk."""
        assert resolve_symbols("universe", "AAPL") == ["AAPL"]


class TestLoadReturns:
    def test_short_series_are_dropped_and_reported(self) -> None:
        conn = _conn_with_bars(
            [("AAA", t, 100.0 + t) for t in range(100)]
            + [("BBB", t, 50.0 + t) for t in range(5)]
        )
        try:
            kept, dropped = load_returns(conn, ["AAA", "BBB"], "1d", min_obs=60)
        finally:
            conn.close()
        assert list(kept) == ["AAA"]
        assert dropped == ["BBB"]

    def test_symbol_absent_from_db_is_dropped(self) -> None:
        conn = _conn_with_bars([("AAA", t, 100.0 + t) for t in range(100)])
        try:
            kept, dropped = load_returns(conn, ["AAA", "ZZZ"], "1d", min_obs=60)
        finally:
            conn.close()
        assert list(kept) == ["AAA"]
        assert dropped == ["ZZZ"]

    def test_misaligned_stamp_grids_do_not_null_the_panel(self) -> None:
        """REGRESSION: a shared pivot index silently emptied the panel.

        The first implementation pivoted every symbol onto one union index and
        called ``pct_change`` across it. When two symbols sit on different
        stamp grids, consecutive union rows belong to different symbols, so
        almost every return goes NaN. Measured against the live DB at ``1wk``
        it dropped **505 of 505** symbols that the fixed implementation keeps
        (503 of 505) — and it failed in the SAFE direction (a refusal, not a
        wrong number), which is exactly why it survived a hand check at ``1d``
        where the grids happen to align.

        AAA sits on even stamps and BBB on odd ones: zero index overlap, and
        both must still be kept.
        """
        conn = _conn_with_bars(
            [("AAA", 2 * t, 100.0 + t) for t in range(100)]
            + [("BBB", 2 * t + 1, 50.0 + t) for t in range(100)]
        )
        try:
            kept, dropped = load_returns(conn, ["AAA", "BBB"], "1d", min_obs=60)
        finally:
            conn.close()
        assert sorted(kept) == ["AAA", "BBB"]
        assert dropped == []
        assert len(kept["AAA"]) == 99
        assert len(kept["BBB"]) == 99

    def test_empty_symbol_list_short_circuits(self) -> None:
        conn = _conn_with_bars([])
        try:
            assert load_returns(conn, [], "1d", min_obs=60) == ({}, [])
        finally:
            conn.close()


class TestRender:
    def test_measured_panel_emits_paste_ready_flags(self) -> None:
        result = SeriesDeflator(
            k=504, rho=0.3365, n_eff=2.9605, t_deflator=13.048, measured=True
        )
        out = render(result, requested=505, dropped=["ZZZ"], timeframe="1d", min_obs=60)
        assert _PASTE_MARKER in out
        assert "--n-series 504 --n-eff 2.9605" in out
        assert "13.048x" in out

    def test_unmeasured_panel_withholds_flags(self) -> None:
        """The refusal IS the feature.

        An unmeasurable panel and an uncorrelated one both carry a deflator of
        1.0, so emitting flags here would launder "could not tell" into "no
        correction needed".
        """
        result = SeriesDeflator(
            k=1, rho=float("nan"), n_eff=1.0, t_deflator=1.0, measured=False
        )
        out = render(result, requested=1, dropped=[], timeframe="1d", min_obs=60)
        # Asserted on the paste-ready block, NOT on the bare flag strings: the
        # refusal message names both flags in its own explanation, so a
        # substring check passes for the wrong reason and would stay green if
        # the explanation were deleted.
        assert _PASTE_MARKER not in out
        assert "NOT MEASURABLE" in out

    def test_coverage_shortfall_is_warned(self) -> None:
        result = SeriesDeflator(
            k=105, rho=0.4, n_eff=2.5, t_deflator=6.4, measured=True
        )
        out = render(result, requested=505, dropped=[], timeframe="4h", min_obs=60)
        assert "COVERAGE" in out
        assert "SIZE-TILTED" in out

    def test_full_coverage_is_not_warned(self) -> None:
        """Positive control: the warning must be reachable AND avoidable."""
        result = SeriesDeflator(
            k=13, rho=0.5189, n_eff=1.799, t_deflator=2.688, measured=True
        )
        out = render(result, requested=13, dropped=[], timeframe="1d", min_obs=60)
        assert "COVERAGE" not in out


@pytest.mark.parametrize("n_dropped,named", [(3, True), (25, False)])
def test_dropped_symbols_named_only_while_actionable(
    n_dropped: int, named: bool
) -> None:
    result = SeriesDeflator(k=10, rho=0.3, n_eff=2.0, t_deflator=2.2, measured=True)
    dropped = [f"S{i}" for i in range(n_dropped)]
    out = render(
        result, requested=10 + n_dropped, dropped=dropped, timeframe="1d", min_obs=60
    )
    assert ("S0" in out) is named
