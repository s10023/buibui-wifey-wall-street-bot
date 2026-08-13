"""Tests for analytics/digest_lib.py — in-memory DuckDB, no real DB."""

from __future__ import annotations

from typing import Any

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


# ---------------------------------------------------------------------------
# Averages are pooled over trades, not averaged over runs
#
# `backtest_runs` holds one row per (strategy, timeframe, symbol) run, each with
# its own average and its own trade count. `AVG(avg_r)` over those rows lets an
# 8-trade run move the number as far as a 40-trade one while the
# `SUM(closed_trades)` printed beside it is pooled — a mean and a count with
# different denominators. `query_strategy` and `query_tf` were already pooled;
# four other queries had drifted to the unweighted form.
#
# Every fixture below puts TWO runs with different trade counts in one group,
# which no pre-existing digest test did — so the whole weighting path was
# untested and any of these queries could be silently switched back.
# ---------------------------------------------------------------------------

# 40 trades @ -0.10R and 8 trades @ +1.50R:
#   average over runs  = (-0.10 + 1.50) / 2         = +0.700
#   pooled over trades = (40*-0.10 + 8*1.50) / 48   = +0.167
# win counts 10/40 and 8/8:
#   average over runs  = (0.25 + 1.00) / 2          = 62.5%
#   pooled over trades = (10 + 8) / 48              = 37.5%
_BIG: dict[str, Any] = {
    "closed_trades": 40,
    "avg_r": -0.10,
    "win_rate": 0.25,
    "win_count": 10,
}
_SMALL: dict[str, Any] = {
    "closed_trades": 8,
    "avg_r": 1.50,
    "win_rate": 1.00,
    "win_count": 8,
}


def test_query_symbol_avg_r_is_pooled_over_trades() -> None:
    from analytics.digest_lib import query_symbol

    conn = _make_conn()
    _insert_run(conn, symbol="SPY", strategy="bos", run_id="big", **_BIG)
    _insert_run(conn, symbol="SPY", strategy="orb", run_id="small", **_SMALL)
    result = query_symbol(conn, min_trades=5)
    idx = result["columns"].index("avg_avg_r")
    assert result["rows"][0][idx] == pytest.approx(0.167, abs=1e-3)


def test_query_strategy_win_pct_is_pooled_over_trades() -> None:
    conn = _make_conn()
    _insert_run(conn, strategy="bos", timeframe="1h", run_id="big", **_BIG)
    _insert_run(conn, strategy="bos", timeframe="4h", run_id="small", **_SMALL)
    result = query_strategy(conn, min_trades=5)
    cols = result["columns"]
    row = result["rows"][0]
    assert row[cols.index("avg_win_pct")] == pytest.approx(37.5, abs=0.1)
    assert row[cols.index("weighted_avg_r")] == pytest.approx(0.167, abs=1e-3)


def test_query_consistency_overall_avg_r_is_pooled_over_trades() -> None:
    conn = _make_conn()
    _insert_run(conn, strategy="bos", timeframe="1h", run_id="big", **_BIG)
    _insert_run(conn, strategy="bos", timeframe="4h", run_id="small", **_SMALL)
    result = query_consistency(conn, min_trades=5)
    idx = result["columns"].index("overall_avg_r")
    assert result["rows"][0][idx] == pytest.approx(0.167, abs=1e-3)


def test_query_recovery_factor_avg_r_is_pooled_over_trades() -> None:
    conn = _make_conn()
    _insert_run(conn, strategy="bos", timeframe="1h", run_id="big", **_BIG)
    _insert_run(conn, strategy="bos", timeframe="4h", run_id="small", **_SMALL)
    result = query_recovery_factor(conn, min_trades=5)
    idx = result["columns"].index("avg_r")
    assert result["rows"][0][idx] == pytest.approx(0.167, abs=1e-3)


def test_query_direction_bias_is_pooled_over_directional_trades() -> None:
    """The long leg carries the same 40-vs-8 split; the short leg is flat."""
    conn = _make_conn()
    for rid, tf, n, r, wr in [
        ("big", "1h", 40, -0.10, 0.25),
        ("small", "4h", 8, 1.50, 1.00),
    ]:
        _insert_run(
            conn,
            strategy="bos",
            timeframe=tf,
            run_id=rid,
            long_closed_trades=n,
            long_avg_r=r,
            long_win_rate=wr,
            short_closed_trades=10,
            short_avg_r=0.2,
            short_win_rate=0.5,
        )
    result = query_direction_bias(conn, min_trades=5)
    cols = result["columns"]
    row = result["rows"][0]
    assert row[cols.index("long_avg_r")] == pytest.approx(0.167, abs=1e-3)
    assert row[cols.index("long_win_pct")] == pytest.approx(37.5, abs=0.1)
    assert row[cols.index("short_avg_r")] == pytest.approx(0.2, abs=1e-3)
    # long_minus_short must be built from the POOLED legs, not the run means
    assert row[cols.index("long_minus_short")] == pytest.approx(-0.033, abs=1e-3)


def test_direction_bias_ordering_uses_the_pooled_delta() -> None:
    """The ORDER BY key was the unweighted difference — it ranked the report.

    `wide` has the larger pooled gap (0.2 - 1.0 = -0.8); `narrow` looks wider
    under the average-over-runs form ((0.6+1.5)/2 - 0.2 = +0.85) purely because
    its +1.5 leg rests on 8 trades against the other leg's 40.
    """
    conn = _make_conn()
    _insert_run(
        conn,
        strategy="wide",
        timeframe="1h",
        run_id="w1",
        long_closed_trades=40,
        long_avg_r=0.2,
        short_closed_trades=40,
        short_avg_r=1.0,
    )
    _insert_run(
        conn,
        strategy="narrow",
        timeframe="1h",
        run_id="n1",
        long_closed_trades=40,
        long_avg_r=0.6,
        short_closed_trades=40,
        short_avg_r=0.2,
    )
    _insert_run(
        conn,
        strategy="narrow",
        timeframe="4h",
        run_id="n2",
        long_closed_trades=8,
        long_avg_r=1.5,
        short_closed_trades=40,
        short_avg_r=0.2,
    )
    result = query_direction_bias(conn, min_trades=5)
    assert [r[0] for r in result["rows"]] == ["wide", "narrow"]


def test_pooled_mean_skips_rows_with_no_average_in_both_parts() -> None:
    """A NULL directional average must leave its trade count out of the divisor.

    Counting n in the denominator with nothing in the numerator invents n
    trades at R=0 — the same bias-toward-a-fiction the pooling removes.
    """
    conn = _make_conn()
    _insert_run(
        conn,
        strategy="bos",
        timeframe="1h",
        run_id="has_r",
        long_closed_trades=10,
        long_avg_r=0.5,
        short_closed_trades=10,
        short_avg_r=0.2,
    )
    conn.execute(
        "UPDATE backtest_runs SET long_avg_r = NULL, long_closed_trades = 30 "
        "WHERE run_id = 'has_r'"
    )
    _insert_run(
        conn,
        strategy="bos",
        timeframe="4h",
        run_id="keeps_r",
        long_closed_trades=10,
        long_avg_r=0.5,
        short_closed_trades=10,
        short_avg_r=0.2,
    )
    result = query_direction_bias(conn, min_trades=5)
    idx = result["columns"].index("long_avg_r")
    assert result["rows"][0][idx] == pytest.approx(0.5, abs=1e-3)
