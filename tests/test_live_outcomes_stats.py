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
    compute_live_outcomes,
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
