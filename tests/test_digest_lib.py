"""Tests for analytics/digest_lib.py — in-memory DuckDB, no real DB."""

from __future__ import annotations

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.digest_lib import (
    QUERY_NAMES,
    query_adr_ab,
    query_combos,
    query_consistency,
    query_day_filter_ab,
    query_direction_bias,
    query_recovery_factor,
    query_strategy,
    query_volume_ab,
    run_digest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def _insert_run(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    strategy: str = "bos",
    avg_r: float = 0.5,
    total_r: float = 5.0,
    closed_trades: int = 20,
    win_rate: float = 0.55,
    win_count: int = 11,
    loss_count: int = 9,
    day_filter: str = "off",
    adr_suppress_threshold: float | None = None,
    volume_suppress: bool | None = None,
    long_avg_r: float = 0.6,
    short_avg_r: float = 0.4,
    long_closed_trades: int = 10,
    short_closed_trades: int = 10,
    long_win_rate: float = 0.6,
    short_win_rate: float = 0.5,
    long_total_r: float = 3.0,
    short_total_r: float = 2.0,
    max_drawdown_r: float = 3.0,
    recovery_factor: float = 1.67,
    run_id: str | None = None,
) -> None:
    import time

    rid = (
        run_id
        or f"{symbol}|{timeframe}|{strategy}|{day_filter}|{avg_r}|{volume_suppress}|{adr_suppress_threshold}"
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_runs (
            run_id, symbol, timeframe, strategy,
            data_start_ms, data_end_ms, days,
            sl_pct, tp_r, fee_pct,
            day_filter,
            total_signals, closed_trades, win_count, loss_count,
            win_rate, avg_r, total_r, max_drawdown_r,
            run_at_ms, sweep_id,
            long_closed_trades, long_win_count, long_win_rate, long_avg_r,
            short_closed_trades, short_win_count, short_win_rate, short_avg_r,
            adr_suppress_threshold, long_total_r, short_total_r,
            recovery_factor, volume_suppress
        ) VALUES (
            ?, ?, ?, ?,
            0, 0, 90,
            0.02, 2.0, 0.0005,
            ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, NULL,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?
        )
        """,
        [
            rid,
            symbol,
            timeframe,
            strategy,
            day_filter,
            closed_trades + 5,
            closed_trades,
            win_count,
            loss_count,
            win_rate,
            avg_r,
            total_r,
            max_drawdown_r,
            int(time.time() * 1000),
            long_closed_trades,
            win_count,
            long_win_rate,
            long_avg_r,
            short_closed_trades,
            loss_count,
            short_win_rate,
            short_avg_r,
            adr_suppress_threshold,
            long_total_r,
            short_total_r,
            recovery_factor,
            volume_suppress,
        ],
    )


# ---------------------------------------------------------------------------
# Basic shape tests (all 10 cards return columns + rows)
# ---------------------------------------------------------------------------


def _seed_basic(conn: duckdb.DuckDBPyConnection) -> None:
    for sym in ("BTCUSDT", "ETHUSDT"):
        for strat in ("bos", "fvg", "engulfing"):
            for tf in ("15m", "1h"):
                _insert_run(conn, symbol=sym, strategy=strat, timeframe=tf)


@pytest.mark.parametrize("query", QUERY_NAMES)
def test_all_queries_return_columns_and_rows(query: str) -> None:
    conn = _make_conn()
    _seed_basic(conn)
    result = run_digest(conn, query, min_trades=1)
    assert "columns" in result
    assert "rows" in result
    assert isinstance(result["columns"], list)
    assert len(result["columns"]) > 0
    assert isinstance(result["rows"], list)


def test_empty_db_returns_no_rows() -> None:
    conn = _make_conn()
    for query in QUERY_NAMES:
        result = run_digest(conn, query, min_trades=1)
        assert result["rows"] == [], f"{query} should return empty rows on empty DB"


def test_invalid_query_raises() -> None:
    conn = _make_conn()
    with pytest.raises(ValueError, match="Unknown digest query"):
        run_digest(conn, "nonsense")


# ---------------------------------------------------------------------------
# min_trades filtering
# ---------------------------------------------------------------------------


def test_min_trades_filter() -> None:
    conn = _make_conn()
    _insert_run(conn, closed_trades=3, run_id="small")
    _insert_run(conn, closed_trades=20, run_id="big")
    result = query_strategy(conn, min_trades=10)
    assert len(result["rows"]) == 1


# ---------------------------------------------------------------------------
# ADR A/B — requires paired runs
# ---------------------------------------------------------------------------


def test_adr_ab_needs_paired_runs() -> None:
    conn = _make_conn()
    # Only ungated run — no pair, should return empty
    _insert_run(conn, adr_suppress_threshold=None, run_id="ungated")
    result = query_adr_ab(conn, min_trades=1)
    assert result["rows"] == []


def test_adr_ab_returns_delta() -> None:
    conn = _make_conn()
    # Gated run (better)
    _insert_run(
        conn,
        avg_r=0.8,
        adr_suppress_threshold=0.7,
        run_id="gated",
        day_filter="off",
    )
    # Ungated run (same strategy/symbol/tf/params)
    _insert_run(
        conn,
        avg_r=0.5,
        adr_suppress_threshold=None,
        run_id="ungated",
        day_filter="off",
    )
    result = query_adr_ab(conn, min_trades=1)
    assert len(result["rows"]) == 1
    cols = result["columns"]
    delta_idx = cols.index("delta_avg_r")
    delta = result["rows"][0][delta_idx]
    assert abs(delta - 0.3) < 0.01


# ---------------------------------------------------------------------------
# Volume suppress A/B
# ---------------------------------------------------------------------------


def test_volume_ab_needs_paired_runs() -> None:
    conn = _make_conn()
    _insert_run(conn, volume_suppress=True, run_id="suppress_only")
    result = query_volume_ab(conn, min_trades=1)
    assert result["rows"] == []


def test_volume_ab_returns_delta() -> None:
    conn = _make_conn()
    _insert_run(conn, avg_r=0.9, volume_suppress=True, run_id="suppress_on")
    _insert_run(conn, avg_r=0.5, volume_suppress=False, run_id="suppress_off")
    result = query_volume_ab(conn, min_trades=1)
    assert len(result["rows"]) == 1
    cols = result["columns"]
    delta_idx = cols.index("delta_avg_r")
    delta = result["rows"][0][delta_idx]
    assert abs(delta - 0.4) < 0.01


# ---------------------------------------------------------------------------
# Day filter A/B
# ---------------------------------------------------------------------------


def test_day_filter_ab_pairs_correctly() -> None:
    conn = _make_conn()
    _insert_run(conn, avg_r=0.7, day_filter="tue_thu", run_id="filtered")
    _insert_run(conn, avg_r=0.4, day_filter="off", run_id="unfiltered")
    result = query_day_filter_ab(conn, min_trades=1)
    assert len(result["rows"]) == 1
    cols = result["columns"]
    delta_idx = cols.index("delta_avg_r")
    delta = result["rows"][0][delta_idx]
    assert abs(delta - 0.3) < 0.01


# ---------------------------------------------------------------------------
# Direction bias
# ---------------------------------------------------------------------------


def test_direction_bias_computes_delta() -> None:
    conn = _make_conn()
    _insert_run(conn, long_avg_r=1.2, short_avg_r=0.3, run_id="biased")
    result = query_direction_bias(conn, min_trades=1)
    assert len(result["rows"]) > 0
    cols = result["columns"]
    delta_idx = cols.index("long_minus_short")
    delta = result["rows"][0][delta_idx]
    assert abs(delta - 0.9) < 0.01


# ---------------------------------------------------------------------------
# Consistency
# ---------------------------------------------------------------------------


def test_consistency_counts_profitable_combos() -> None:
    conn = _make_conn()
    # 2 profitable, 1 losing
    _insert_run(conn, avg_r=0.5, symbol="BTCUSDT", timeframe="1h", run_id="p1")
    _insert_run(conn, avg_r=0.3, symbol="ETHUSDT", timeframe="1h", run_id="p2")
    _insert_run(conn, avg_r=-0.1, symbol="BTCUSDT", timeframe="4h", run_id="l1")
    result = query_consistency(conn, min_trades=1)
    assert len(result["rows"]) == 1
    cols = result["columns"]
    pct_idx = cols.index("pct_profitable")
    pct = result["rows"][0][pct_idx]
    assert abs(pct - 66.7) < 0.2


# ---------------------------------------------------------------------------
# Combos top_n
# ---------------------------------------------------------------------------


def test_combos_top_n_respected() -> None:
    conn = _make_conn()
    for i in range(15):
        _insert_run(
            conn,
            symbol="BTCUSDT",
            strategy="bos",
            timeframe="1h",
            avg_r=float(i) * 0.1,
            run_id=f"run{i}",
        )
    result = query_combos(conn, min_trades=1, top_n=5)
    assert len(result["rows"]) == 5


# ---------------------------------------------------------------------------
# Recovery factor
# ---------------------------------------------------------------------------


def test_recovery_factor_excludes_zero_rf() -> None:
    conn = _make_conn()
    _insert_run(conn, recovery_factor=0.0, run_id="zero_rf")
    _insert_run(conn, recovery_factor=2.5, run_id="good_rf")
    result = query_recovery_factor(conn, min_trades=1)
    # Only the run with rf > 0 should appear
    assert len(result["rows"]) == 1
    cols = result["columns"]
    rf_idx = cols.index("avg_rf")
    assert abs(result["rows"][0][rf_idx] - 2.5) < 0.01
