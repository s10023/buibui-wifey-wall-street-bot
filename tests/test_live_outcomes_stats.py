"""Tests for analytics/stats/live_outcomes.py using in-memory DuckDB.

Mirrors the read-only CLI stop-gap tools/live_outcomes_report.py: a roll-up
over signal_alert_outcomes plus per-(strategy, tf, direction) and per-strategy
win-rate / avg-R breakdowns.
"""

from __future__ import annotations

import time

import duckdb

from analytics.data_store import init_schema
from analytics.stats import (
    LiveOutcomeCell,
    LiveOutcomesResult,
    LiveOutcomeStrategyRow,
    LiveOutcomeSymbolRow,
    OpenPosition,
    compute_live_outcomes,
    mark_open_positions,
    open_positions,
)

_NOW_MS = int(time.time() * 1000)
_DAY_MS = 86_400_000


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def _insert(
    conn: duckdb.DuckDBPyConnection,
    signal_id: str,
    *,
    strategy: str = "bos",
    tf: str = "4h",
    direction: str = "short",
    outcome: str | None = "win",
    outcome_r: float | None = 1.0,
    tp_price: float | None = 105.0,
    fired_at_ms: int | None = None,
    symbol: str = "AAPL",
) -> None:
    conn.execute(
        """
        INSERT INTO signal_alert_outcomes
            (signal_id, symbol, tf, strategy, direction, fired_at_ms,
             entry_price, sl_price, tp_price, rr_ratio, outcome, outcome_r)
        VALUES (?, ?, ?, ?, ?, ?, 100.0, 95.0, ?, 1.0, ?, ?)
        """,
        (
            signal_id,
            symbol,
            tf,
            strategy,
            direction,
            _NOW_MS if fired_at_ms is None else fired_at_ms,
            tp_price,
            outcome,
            outcome_r,
        ),
    )


def test_empty_table_returns_zero_rollup() -> None:
    conn = _conn()
    res = compute_live_outcomes(conn, days=0, min_n=1)
    assert isinstance(res, LiveOutcomesResult)
    assert res.rollup.total_rows == 0
    assert res.rollup.resolved == 0
    assert res.rollup.open == 0
    assert res.rollup.open_no_tp == 0
    assert res.cells == []
    assert res.by_strategy == []


def test_rollup_counts() -> None:
    conn = _conn()
    _insert(conn, "a", outcome="win", outcome_r=1.0)
    _insert(conn, "b", outcome="loss", outcome_r=-1.0)
    _insert(conn, "c", outcome="expired", outcome_r=0.2)
    # Open row, but has a tp_price → resolvable, just not yet resolved.
    _insert(conn, "d", outcome=None, outcome_r=None, tp_price=105.0)
    # Open AND no tp_price → the integrity hole the ledger fix closed.
    _insert(conn, "e", outcome=None, outcome_r=None, tp_price=None)

    res = compute_live_outcomes(conn, days=0, min_n=1)
    assert res.rollup.total_rows == 5
    assert res.rollup.resolved == 3
    assert res.rollup.open == 2
    assert res.rollup.open_no_tp == 1
    assert res.rollup.wins == 1
    assert res.rollup.losses == 1
    assert res.rollup.expired == 1


def test_cells_win_rate_excludes_expired() -> None:
    conn = _conn()
    # bos/4h/short: 2 wins, 1 loss, 1 expired → win_rate over win+loss = 2/3.
    _insert(conn, "w1", outcome="win", outcome_r=1.5)
    _insert(conn, "w2", outcome="win", outcome_r=1.5)
    _insert(conn, "l1", outcome="loss", outcome_r=-1.0)
    _insert(conn, "e1", outcome="expired", outcome_r=0.0)

    res = compute_live_outcomes(conn, days=0, min_n=1)
    assert len(res.cells) == 1
    cell = res.cells[0]
    assert isinstance(cell, LiveOutcomeCell)
    assert cell.strategy == "bos"
    assert cell.tf == "4h"
    assert cell.direction == "short"
    assert cell.n == 4
    assert cell.wins == 2
    assert cell.losses == 1
    assert cell.expired == 1
    assert cell.win_rate is not None
    assert abs(cell.win_rate - (2 / 3)) < 1e-9
    # avg_r over all resolved rows: (1.5 + 1.5 - 1.0 + 0.0) / 4 = 0.5
    assert cell.avg_r is not None
    assert abs(cell.avg_r - 0.5) < 1e-9


def test_by_strategy_rollup_ordered_by_avg_r_desc() -> None:
    conn = _conn()
    # strong strategy (avg_r +1.0) and weak strategy (avg_r -1.0)
    _insert(conn, "s1", strategy="orb", outcome="win", outcome_r=1.0)
    _insert(conn, "s2", strategy="orb", outcome="win", outcome_r=1.0)
    _insert(conn, "w1", strategy="ema", outcome="loss", outcome_r=-1.0)
    _insert(conn, "w2", strategy="ema", outcome="loss", outcome_r=-1.0)

    res = compute_live_outcomes(conn, days=0, min_n=1)
    assert [r.strategy for r in res.by_strategy] == ["orb", "ema"]
    assert isinstance(res.by_strategy[0], LiveOutcomeStrategyRow)
    assert res.by_strategy[0].avg_r == 1.0
    assert res.by_strategy[1].avg_r == -1.0


def test_min_n_filters_cells_and_strategies() -> None:
    conn = _conn()
    _insert(conn, "a", strategy="bos", outcome="win", outcome_r=1.0)
    _insert(conn, "b", strategy="ema", outcome="win", outcome_r=1.0)
    _insert(conn, "c", strategy="ema", outcome="loss", outcome_r=-1.0)

    res = compute_live_outcomes(conn, days=0, min_n=2)
    # Only ema has >= 2 resolved rows.
    assert [c.strategy for c in res.cells] == ["ema"]
    assert [r.strategy for r in res.by_strategy] == ["ema"]


def test_days_window_applies_to_cells_not_rollup() -> None:
    conn = _conn()
    # Recent resolved row.
    _insert(conn, "recent", outcome="win", outcome_r=1.0, fired_at_ms=_NOW_MS)
    # Old resolved row (40 days ago).
    _insert(
        conn,
        "old",
        outcome="loss",
        outcome_r=-1.0,
        fired_at_ms=_NOW_MS - 40 * _DAY_MS,
    )

    res = compute_live_outcomes(conn, days=30, min_n=1)
    # Roll-up is all-time: both rows counted.
    assert res.rollup.total_rows == 2
    assert res.rollup.resolved == 2
    # Cells windowed: only the recent row survives.
    assert len(res.cells) == 1
    assert res.cells[0].wins == 1
    assert res.cells[0].losses == 0


def test_open_rows_excluded_from_cells() -> None:
    conn = _conn()
    _insert(conn, "open", outcome=None, outcome_r=None)
    res = compute_live_outcomes(conn, days=0, min_n=1)
    assert res.rollup.total_rows == 1
    assert res.rollup.open == 1
    # No resolved rows → no cell breakdown.
    assert res.cells == []
    assert res.by_strategy == []


def test_symbol_filter_slices_rollup_and_tables() -> None:
    conn = _conn()
    _insert(conn, "a1", symbol="AAPL", outcome="win", outcome_r=1.0)
    _insert(conn, "a2", symbol="AAPL", outcome="loss", outcome_r=-1.0)
    _insert(conn, "m1", symbol="MSFT", outcome="win", outcome_r=2.0)

    res = compute_live_outcomes(conn, days=0, min_n=1, symbol="AAPL")

    # Roll-up follows the filter too (spec D2) — not just the tables.
    assert res.rollup.total_rows == 2
    assert res.rollup.resolved == 2
    assert res.rollup.wins == 1
    assert res.rollup.losses == 1
    # Tables: avg_r over AAPL rows only = (1.0 - 1.0) / 2 = 0.0
    assert len(res.by_strategy) == 1
    assert res.by_strategy[0].avg_r == 0.0


def test_symbol_none_matches_unfiltered_baseline() -> None:
    conn = _conn()
    _insert(conn, "a1", symbol="AAPL", outcome="win", outcome_r=1.0)
    _insert(conn, "m1", symbol="MSFT", outcome="loss", outcome_r=-1.0)

    explicit_none = compute_live_outcomes(conn, days=0, min_n=1, symbol=None)
    default = compute_live_outcomes(conn, days=0, min_n=1)

    assert explicit_none.rollup == default.rollup
    assert explicit_none.cells == default.cells
    assert explicit_none.by_strategy == default.by_strategy
    assert default.rollup.total_rows == 2


def test_symbols_chip_list_is_global_and_stable() -> None:
    conn = _conn()
    _insert(conn, "a1", symbol="AAPL", outcome="win", outcome_r=1.0)
    _insert(conn, "a2", symbol="AAPL", outcome="win", outcome_r=1.0)
    _insert(conn, "m1", symbol="MSFT", outcome="win", outcome_r=1.0)
    # Old row: must still count toward the chip list despite the days window.
    _insert(
        conn,
        "t1",
        symbol="TSLA",
        outcome="win",
        outcome_r=1.0,
        fired_at_ms=_NOW_MS - 400 * _DAY_MS,
    )

    filtered = compute_live_outcomes(conn, days=30, min_n=1, symbol="MSFT")

    # Chips are global: unaffected by BOTH the symbol filter and the days window.
    assert [(r.symbol, r.n) for r in filtered.symbols] == [
        ("AAPL", 2),
        ("MSFT", 1),
        ("TSLA", 1),
    ]
    assert isinstance(filtered.symbols[0], LiveOutcomeSymbolRow)


def test_unknown_symbol_returns_zero_rollup_not_error() -> None:
    conn = _conn()
    _insert(conn, "a1", symbol="AAPL", outcome="win", outcome_r=1.0)

    res = compute_live_outcomes(conn, days=0, min_n=1, symbol="NFLX")

    assert res.rollup.total_rows == 0
    assert res.cells == []
    assert res.by_strategy == []
    # Chips still list the real symbols so the operator can navigate back.
    assert [r.symbol for r in res.symbols] == ["AAPL"]


def _open_pos(
    *,
    symbol: str = "AAPL",
    direction: str = "long",
    entry: float | None = 100.0,
    sl: float | None = 95.0,
    tp: float | None = 110.0,
) -> OpenPosition:
    return OpenPosition(
        signal_id="x",
        symbol=symbol,
        strategy="bos",
        tf="4h",
        direction=direction,
        fired_at_ms=_NOW_MS,
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
    )


def test_open_positions_returns_only_unresolved_newest_first() -> None:
    conn = _conn()
    _insert(conn, "resolved", outcome="win", outcome_r=1.0)
    _insert(conn, "older", outcome=None, outcome_r=None, fired_at_ms=_NOW_MS - 1000)
    _insert(conn, "newer", outcome=None, outcome_r=None, fired_at_ms=_NOW_MS)

    rows = open_positions(conn)

    assert [r.signal_id for r in rows] == ["newer", "older"]
    assert rows[0].entry_price == 100.0
    assert rows[0].sl_price == 95.0


def test_open_positions_honours_symbol_filter() -> None:
    conn = _conn()
    _insert(conn, "a", symbol="AAPL", outcome=None, outcome_r=None)
    _insert(conn, "m", symbol="MSFT", outcome=None, outcome_r=None)

    assert [r.signal_id for r in open_positions(conn, symbol="MSFT")] == ["m"]


def test_mark_long_and_short_unrealized_r_sign() -> None:
    # risk = |100 - 95| = 5. Long at mark 105 → +1R. Short at mark 105 → -1R.
    long_pos = _open_pos(direction="long", entry=100.0, sl=95.0)
    short_pos = _open_pos(direction="short", entry=100.0, sl=105.0)

    marked = mark_open_positions([long_pos, short_pos], {"AAPL": 105.0})

    assert marked[0].unrealized_r is not None
    assert abs(marked[0].unrealized_r - 1.0) < 1e-9
    assert marked[1].unrealized_r is not None
    assert abs(marked[1].unrealized_r - (-1.0)) < 1e-9


def test_mark_computes_distances_to_sl_and_tp() -> None:
    pos = _open_pos(entry=100.0, sl=95.0, tp=110.0)

    marked = mark_open_positions([pos], {"AAPL": 100.0})

    assert marked[0].mark == 100.0
    assert marked[0].dist_sl_pct is not None
    assert abs(marked[0].dist_sl_pct - 0.05) < 1e-9
    assert marked[0].dist_tp_pct is not None
    assert abs(marked[0].dist_tp_pct - 0.10) < 1e-9


def test_mark_missing_symbol_yields_all_none() -> None:
    marked = mark_open_positions([_open_pos()], {"MSFT": 400.0})

    assert marked[0].mark is None
    assert marked[0].unrealized_r is None
    assert marked[0].dist_sl_pct is None
    assert marked[0].dist_tp_pct is None
    # The ledger row itself still comes back.
    assert marked[0].position.symbol == "AAPL"


def test_mark_zero_risk_does_not_divide_by_zero() -> None:
    # entry == sl → risk 0. Must yield None, not ZeroDivisionError or inf.
    pos = _open_pos(entry=100.0, sl=100.0)

    marked = mark_open_positions([pos], {"AAPL": 105.0})

    assert marked[0].unrealized_r is None
    # Distance to the stop is still meaningful.
    assert marked[0].dist_sl_pct is not None


def test_mark_null_entry_or_tp_degrades_field_by_field() -> None:
    no_entry = mark_open_positions([_open_pos(entry=None)], {"AAPL": 105.0})
    assert no_entry[0].unrealized_r is None
    # dist_sl_pct only needs the stop and the mark — a null entry must not
    # suppress it.
    assert no_entry[0].dist_sl_pct is not None

    no_tp = mark_open_positions([_open_pos(tp=None)], {"AAPL": 105.0})
    assert no_tp[0].dist_tp_pct is None
    # Everything else still computed.
    assert no_tp[0].unrealized_r is not None
    assert no_tp[0].dist_sl_pct is not None


def test_mark_null_entry_still_computes_stop_distance() -> None:
    # dist_sl_pct needs only the stop and the mark — a null entry must not
    # suppress it, though it does suppress unrealized_r.
    marked = mark_open_positions([_open_pos(entry=None)], {"AAPL": 105.0})

    assert marked[0].unrealized_r is None
    assert marked[0].dist_sl_pct is not None
    assert abs(marked[0].dist_sl_pct - (10.0 / 105.0)) < 1e-9


def test_mark_zero_mark_price_yields_none() -> None:
    # A zero mark is a bad tick, not a price — every derived field degrades.
    marked = mark_open_positions([_open_pos()], {"AAPL": 0.0})

    assert marked[0].unrealized_r is None
    assert marked[0].dist_sl_pct is None
    assert marked[0].dist_tp_pct is None


def test_mark_empty_input_returns_empty() -> None:
    assert mark_open_positions([], {"AAPL": 100.0}) == []


def test_by_strategy_carries_outcome_counts() -> None:
    conn = _conn()
    _insert(conn, "w1", strategy="bos", outcome="win", outcome_r=1.5)
    _insert(conn, "l1", strategy="bos", outcome="loss", outcome_r=-1.0)
    _insert(conn, "e1", strategy="bos", outcome="expired", outcome_r=0.2)
    _insert(conn, "e2", strategy="bos", outcome="expired", outcome_r=-0.1)

    res = compute_live_outcomes(conn, days=0, min_n=1)
    row = res.by_strategy[0]
    assert row.strategy == "bos"
    assert row.n == 4
    assert row.wins == 1
    assert row.losses == 1
    assert row.expired == 2
    # win% still excludes expired: 1 / (1 + 1)
    assert row.win_rate == 0.5
    # avg_r still spans every resolved row: (1.5 - 1.0 + 0.2 - 0.1) / 4
    assert row.avg_r is not None
    assert abs(row.avg_r - 0.15) < 1e-9


def test_by_strategy_expired_only_has_null_win_rate() -> None:
    conn = _conn()
    _insert(conn, "e1", strategy="fvg", outcome="expired", outcome_r=0.0)
    _insert(conn, "e2", strategy="fvg", outcome="expired", outcome_r=0.0)

    res = compute_live_outcomes(conn, days=0, min_n=1)
    row = res.by_strategy[0]
    assert row.n == 2
    assert row.wins == 0
    assert row.losses == 0
    assert row.expired == 2
    assert row.win_rate is None
