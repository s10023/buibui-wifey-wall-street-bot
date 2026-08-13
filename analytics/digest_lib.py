"""Digest lib — aggregation queries over backtest_runs for the Analysis sub-tab.

Each function returns {"columns": [...], "rows": [[...], ...]} so the API and
CLI can share the same data without a Pydantic layer.

All queries respect a min_trades guard to exclude noise from sparse runs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import duckdb
import pandas as pd

DigestResult = dict[str, Any]


@dataclass
class DigestScope:
    """Optional config-scoped filters applied to all digest queries.

    All fields are None / empty by default (no filtering).
    Populated from app.state.active_config when use_config=true is requested.
    """

    day_filter: str | None = None
    fee_pct: float | None = None
    symbols: list[str] = field(default_factory=list)
    min_trades: int = 5
    min_trades_per_tf: dict[str, int] = field(default_factory=dict)

    def effective_min_trades(self, tf: str) -> int:
        return self.min_trades_per_tf.get(tf, self.min_trades)


def _scope_clauses(scope: DigestScope | None, alias: str = "") -> tuple[str, list[Any]]:
    """Return (extra_where_sql, params) for config-scoped filters.

    alias: table alias prefix, e.g. "br." — leave empty for no prefix.
    """
    if scope is None:
        return "", []
    pfx = f"{alias}." if alias else ""
    clauses: list[str] = []
    params: list[Any] = []
    if scope.day_filter is not None:
        clauses.append(f"{pfx}day_filter = ?")
        params.append(scope.day_filter)
    if scope.fee_pct is not None:
        clauses.append(f"{pfx}fee_pct = ?")
        params.append(scope.fee_pct)
    if scope.symbols:
        placeholders = ", ".join("?" * len(scope.symbols))
        clauses.append(f"{pfx}symbol IN ({placeholders})")
        params.extend(scope.symbols)
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def _min_trades_expr(
    scope: DigestScope | None,
    global_min: int,
    col: str = "closed_trades",
    tf_col: str = "timeframe",
) -> tuple[str, list[Any]]:
    """Return a SQL expression (and params) for per-TF min_trades filtering.

    When scope has per-TF overrides, builds a CASE expression.
    Falls back to a simple >= comparison otherwise.
    """
    per_tf = scope.min_trades_per_tf if scope else {}
    base = scope.min_trades if scope else global_min

    if not per_tf:
        return f"{col} >= ?", [base]

    # CASE WHEN timeframe = '15m' THEN closed_trades >= 30 ...
    when_parts = " ".join(
        f"WHEN {tf_col} = '{tf}' THEN {col} >= {n}" for tf, n in per_tf.items()
    )
    expr = f"(CASE {when_parts} ELSE {col} >= {base} END)"
    return expr, []


def _pooled(avg_col: str, n_col: str = "closed_trades") -> str:
    """SQL for a TRADE-WEIGHTED mean of a per-run average or rate.

    `backtest_runs` holds one row per (strategy, timeframe, symbol) run, each
    with its own average and its own trade count. `AVG(avg_r)` over those rows
    lets a 1-trade run move the number exactly as far as a 50-trade one, while
    the `SUM(closed_trades)` printed beside it reports the pooled count — a mean
    and a count that do not share a denominator. Pool instead:
    sum(avg_i x n_i) / sum(n_i), which for a rate column collapses to
    sum(wins) / sum(trades).

    The `FILTER` keeps numerator and denominator on the same rows: a directional
    average is NULL when that direction has no trades, and `SUM(n)` would
    otherwise count that row's trades against a numerator it contributed
    nothing to, dragging the result toward zero.

    Defined once on purpose. The expression was inlined at six sites and four of
    them had drifted to the unweighted form while two stayed correct — the
    duplication is what let the divergence hide (the #165 lesson).
    """
    return (
        f"SUM({avg_col} * {n_col}) "
        f"/ NULLIF(SUM({n_col}) FILTER (WHERE {avg_col} IS NOT NULL), 0)"
    )


QUERY_NAMES = [
    "symbol",
    "strategy",
    "tf",
    "combos",
    "adr_ab",
    "volume_ab",
    "day_filter_ab",
    "direction_bias",
    "consistency",
    "recovery_factor",
    "co_firing",
    "cross_tf_combos",
]


def _df_to_result(df: pd.DataFrame) -> DigestResult:
    """Convert a DataFrame to the generic {columns, rows} wire format."""
    return {
        "columns": list(df.columns),
        "rows": [list(row) for row in df.itertuples(index=False)],
    }


# ---------------------------------------------------------------------------
# Card 1 — Symbol leaderboard
# ---------------------------------------------------------------------------


def query_symbol(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Rank symbols by total_r.  Shows which market is generating the most edge."""
    mt_expr, mt_params = _min_trades_expr(scope, min_trades)
    sc_sql, sc_params = _scope_clauses(scope)
    df = conn.execute(
        f"""
        SELECT
            symbol,
            ROUND(SUM(total_r), 2)                      AS total_r,
            ROUND({_pooled("avg_r")}, 3)                AS avg_avg_r,
            SUM(closed_trades)                          AS total_trades,
            COUNT(*)                                    AS run_count,
            MAX(CASE WHEN avg_r = max_avg_r THEN strategy END) AS best_strategy
        FROM (
            SELECT *,
                MAX(avg_r) OVER (PARTITION BY symbol) AS max_avg_r
            FROM backtest_runs
            WHERE {mt_expr}{sc_sql}
        ) sub
        GROUP BY symbol
        ORDER BY total_r DESC
        """,
        mt_params + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 2 — Strategy leaderboard
# ---------------------------------------------------------------------------


def query_strategy(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Rank strategies by trade-weighted avg_r across all symbols × TFs."""
    mt_expr, mt_params = _min_trades_expr(scope, min_trades)
    sc_sql, sc_params = _scope_clauses(scope)
    df = conn.execute(
        f"""
        SELECT
            strategy,
            ROUND({_pooled("avg_r")}, 3)                AS weighted_avg_r,
            ROUND(SUM(total_r), 2)                      AS total_r,
            SUM(closed_trades)                          AS total_trades,
            COUNT(*)                                    AS run_count,
            ROUND({_pooled("win_rate")} * 100, 1)       AS avg_win_pct
        FROM backtest_runs
        WHERE {mt_expr}{sc_sql}
        GROUP BY strategy
        ORDER BY weighted_avg_r DESC
        """,
        mt_params + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 3 — TF ranking
# ---------------------------------------------------------------------------


def query_tf(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Rank timeframes by trade-weighted avg_r."""
    mt_expr, mt_params = _min_trades_expr(scope, min_trades)
    sc_sql, sc_params = _scope_clauses(scope)
    df = conn.execute(
        f"""
        SELECT
            timeframe,
            ROUND({_pooled("avg_r")}, 3)                AS weighted_avg_r,
            ROUND(SUM(total_r), 2)                      AS total_r,
            SUM(closed_trades)                          AS total_trades,
            COUNT(*)                                    AS run_count
        FROM backtest_runs
        WHERE {mt_expr}{sc_sql}
        GROUP BY timeframe
        ORDER BY weighted_avg_r DESC
        """,
        mt_params + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 4 — Best combos (top-N)
# ---------------------------------------------------------------------------


def query_combos(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    top_n: int = 20,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Top symbol × strategy × TF combos by avg_r (min_trades filter applied)."""
    mt_expr, mt_params = _min_trades_expr(scope, min_trades)
    sc_sql, sc_params = _scope_clauses(scope)
    df = conn.execute(
        f"""
        SELECT
            symbol,
            strategy,
            timeframe,
            day_filter,
            ROUND(avg_r, 3)                             AS avg_r,
            ROUND(total_r, 2)                           AS total_r,
            closed_trades                               AS trades,
            ROUND(win_rate * 100, 1)                    AS win_pct,
            ROUND(recovery_factor, 2)                   AS rf
        FROM backtest_runs
        WHERE {mt_expr}{sc_sql}
        ORDER BY avg_r DESC
        LIMIT {top_n}
        """,
        mt_params + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 5 — ADR gate A/B
# ---------------------------------------------------------------------------


def query_adr_ab(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Compare avg_r with vs without the ADR bias gate per strategy × TF.

    Self-joins on matching (symbol, strategy, tf, day_filter, sl_pct, tp_r,
    fee_pct) so only pairs with both runs appear.  Shows Δavg_r = gated − ungated.
    day_filter scoping applied to on_r side only (off_r must have day_filter='off').
    """
    # For A/B queries: scope without day_filter (the join already constrains it)
    sc_sql, sc_params = _scope_clauses(
        DigestScope(
            fee_pct=scope.fee_pct if scope else None,
            symbols=scope.symbols if scope else [],
        )
        if scope
        else None
    )
    mt_expr, mt_params = _min_trades_expr(
        scope, min_trades, col="on_r.closed_trades", tf_col="on_r.timeframe"
    )
    mt_expr2, mt_params2 = _min_trades_expr(
        scope, min_trades, col="off_r.closed_trades", tf_col="off_r.timeframe"
    )
    on_sc = sc_sql.replace("symbol", "on_r.symbol").replace("fee_pct", "on_r.fee_pct")
    off_sc = sc_sql.replace("symbol", "off_r.symbol").replace(
        "fee_pct", "off_r.fee_pct"
    )
    df = conn.execute(
        f"""
        SELECT
            on_r.strategy,
            on_r.timeframe,
            on_r.symbol,
            ROUND(on_r.avg_r, 3)                        AS gated_avg_r,
            ROUND(off_r.avg_r, 3)                       AS ungated_avg_r,
            ROUND(on_r.avg_r - off_r.avg_r, 3)          AS delta_avg_r,
            on_r.closed_trades                          AS gated_trades,
            off_r.closed_trades                         AS ungated_trades
        FROM backtest_runs on_r
        JOIN backtest_runs off_r
          ON  on_r.symbol      = off_r.symbol
          AND on_r.strategy    = off_r.strategy
          AND on_r.timeframe   = off_r.timeframe
          AND on_r.day_filter  = off_r.day_filter
          AND on_r.sl_pct      = off_r.sl_pct
          AND on_r.tp_r        = off_r.tp_r
          AND on_r.fee_pct     = off_r.fee_pct
          -- Same data window. An A/B across different windows is not a
          -- controlled comparison whatever the gate did — a 365-day-anchored
          -- run gives no warm-up and truncates at the edge, and two windows
          -- can differ in SIGN on the same cell. `days` cannot stand in for
          -- this: every row in the table declares 365 while the earliest were
          -- measured over 90 (the #144 recorded-vs-executed defect).
          --
          -- This is also what keeps the card honest now that
          -- `adr_suppress_threshold` records what EXECUTED (2026-08-11).
          -- Pre-#142 rows legitimately carry 0.80 on 1d/1wk (the gate ran
          -- there, degenerately); their post-#142 twins correctly carry NULL.
          -- Those two are not an experiment, they are the same cell measured
          -- under different code two months apart, and without this clause the
          -- card reported 661 such pairs as gated-vs-ungated deltas.
          AND on_r.data_start_ms = off_r.data_start_ms
          AND on_r.data_end_ms   = off_r.data_end_ms
        WHERE on_r.adr_suppress_threshold IS NOT NULL
          AND off_r.adr_suppress_threshold IS NULL
          AND {mt_expr}{on_sc}
          AND {mt_expr2}{off_sc}
        ORDER BY delta_avg_r DESC
        """,
        mt_params + sc_params + mt_params2 + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 6 — Volume suppress A/B
# ---------------------------------------------------------------------------


def query_volume_ab(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Compare avg_r with vs without volume suppression per strategy × TF."""
    sc_sql, sc_params = _scope_clauses(
        DigestScope(
            day_filter=scope.day_filter if scope else None,
            fee_pct=scope.fee_pct if scope else None,
            symbols=scope.symbols if scope else [],
        )
        if scope
        else None
    )
    mt_expr, mt_params = _min_trades_expr(
        scope, min_trades, col="on_r.closed_trades", tf_col="on_r.timeframe"
    )
    mt_expr2, mt_params2 = _min_trades_expr(
        scope, min_trades, col="off_r.closed_trades", tf_col="off_r.timeframe"
    )
    on_sc = (
        sc_sql.replace("symbol", "on_r.symbol")
        .replace("fee_pct", "on_r.fee_pct")
        .replace("day_filter", "on_r.day_filter")
    )
    off_sc = (
        sc_sql.replace("symbol", "off_r.symbol")
        .replace("fee_pct", "off_r.fee_pct")
        .replace("day_filter", "off_r.day_filter")
    )
    df = conn.execute(
        f"""
        SELECT
            on_r.strategy,
            on_r.timeframe,
            on_r.symbol,
            ROUND(on_r.avg_r, 3)                        AS suppressed_avg_r,
            ROUND(off_r.avg_r, 3)                       AS all_vol_avg_r,
            ROUND(on_r.avg_r - off_r.avg_r, 3)          AS delta_avg_r,
            on_r.closed_trades                          AS suppressed_trades,
            off_r.closed_trades                         AS all_vol_trades
        FROM backtest_runs on_r
        JOIN backtest_runs off_r
          ON  on_r.symbol      = off_r.symbol
          AND on_r.strategy    = off_r.strategy
          AND on_r.timeframe   = off_r.timeframe
          AND on_r.day_filter  = off_r.day_filter
          AND on_r.sl_pct      = off_r.sl_pct
          AND on_r.tp_r        = off_r.tp_r
          AND on_r.fee_pct     = off_r.fee_pct
        WHERE on_r.volume_suppress  = TRUE
          AND (off_r.volume_suppress = FALSE OR off_r.volume_suppress IS NULL)
          AND {mt_expr}{on_sc}
          AND {mt_expr2}{off_sc}
        ORDER BY delta_avg_r DESC
        """,
        mt_params + sc_params + mt_params2 + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 7 — Day filter A/B
# ---------------------------------------------------------------------------


def query_day_filter_ab(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Compare avg_r with day filter ON vs OFF per strategy × symbol × TF.

    Δavg_r = filtered − unfiltered.  Positive means filtering helped.
    When scope has day_filter set, shows only that filter mode vs 'off'.
    """
    # For day_filter A/B: scope by symbol + fee_pct only; day_filter handled by join
    sc_sql, sc_params = _scope_clauses(
        DigestScope(
            fee_pct=scope.fee_pct if scope else None,
            symbols=scope.symbols if scope else [],
        )
        if scope
        else None
    )
    on_day_filter_clause = (
        f"on_r.day_filter = '{scope.day_filter}'"
        if scope and scope.day_filter and scope.day_filter != "off"
        else "on_r.day_filter != 'off'"
    )
    mt_expr, mt_params = _min_trades_expr(
        scope, min_trades, col="on_r.closed_trades", tf_col="on_r.timeframe"
    )
    mt_expr2, mt_params2 = _min_trades_expr(
        scope, min_trades, col="off_r.closed_trades", tf_col="off_r.timeframe"
    )
    on_sc = sc_sql.replace("symbol", "on_r.symbol").replace("fee_pct", "on_r.fee_pct")
    off_sc = sc_sql.replace("symbol", "off_r.symbol").replace(
        "fee_pct", "off_r.fee_pct"
    )
    df = conn.execute(
        f"""
        SELECT
            on_r.strategy,
            on_r.timeframe,
            on_r.symbol,
            on_r.day_filter                             AS filter_mode,
            ROUND(on_r.avg_r, 3)                        AS filtered_avg_r,
            ROUND(off_r.avg_r, 3)                       AS unfiltered_avg_r,
            ROUND(on_r.avg_r - off_r.avg_r, 3)          AS delta_avg_r,
            on_r.closed_trades                          AS filtered_trades,
            off_r.closed_trades                         AS unfiltered_trades
        FROM backtest_runs on_r
        JOIN backtest_runs off_r
          ON  on_r.symbol      = off_r.symbol
          AND on_r.strategy    = off_r.strategy
          AND on_r.timeframe   = off_r.timeframe
          AND on_r.sl_pct      = off_r.sl_pct
          AND on_r.tp_r        = off_r.tp_r
          AND on_r.fee_pct     = off_r.fee_pct
        WHERE {on_day_filter_clause}
          AND off_r.day_filter = 'off'
          AND {mt_expr}{on_sc}
          AND {mt_expr2}{off_sc}
        ORDER BY delta_avg_r DESC
        """,
        mt_params + sc_params + mt_params2 + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 8 — Direction bias
# ---------------------------------------------------------------------------


def query_direction_bias(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Long vs short avg_r per strategy — shows directional edge asymmetry."""
    mt_expr, mt_params = _min_trades_expr(scope, min_trades, col="long_closed_trades")
    mt_expr2, mt_params2 = _min_trades_expr(
        scope, min_trades, col="short_closed_trades"
    )
    sc_sql, sc_params = _scope_clauses(scope)
    long_r = _pooled("long_avg_r", "long_closed_trades")
    short_r = _pooled("short_avg_r", "short_closed_trades")
    long_wr = _pooled("long_win_rate", "long_closed_trades")
    short_wr = _pooled("short_win_rate", "short_closed_trades")
    df = conn.execute(
        f"""
        SELECT
            strategy,
            ROUND({long_r}, 3)                          AS long_avg_r,
            ROUND({short_r}, 3)                         AS short_avg_r,
            ROUND({long_r} - {short_r}, 3)              AS long_minus_short,
            SUM(long_closed_trades)                     AS long_trades,
            SUM(short_closed_trades)                    AS short_trades,
            ROUND({long_wr} * 100, 1)                   AS long_win_pct,
            ROUND({short_wr} * 100, 1)                  AS short_win_pct
        FROM backtest_runs
        WHERE {mt_expr}
          AND {mt_expr2}{sc_sql}
        GROUP BY strategy
        ORDER BY ABS({long_r} - {short_r}) DESC
        """,
        mt_params + mt_params2 + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 9 — Consistency (edge breadth)
# ---------------------------------------------------------------------------


def query_consistency(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """For each strategy, how many symbol × TF combos show positive avg_r?"""
    mt_expr, mt_params = _min_trades_expr(scope, min_trades)
    sc_sql, sc_params = _scope_clauses(scope)
    df = conn.execute(
        f"""
        SELECT
            strategy,
            COUNT(*)                                    AS total_combos,
            SUM(CASE WHEN avg_r > 0 THEN 1 ELSE 0 END) AS profitable_combos,
            ROUND(
                100.0 * SUM(CASE WHEN avg_r > 0 THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0),
                1
            )                                          AS pct_profitable,
            ROUND({_pooled("avg_r")}, 3)                AS overall_avg_r,
            SUM(closed_trades)                          AS total_trades
        FROM backtest_runs
        WHERE {mt_expr}{sc_sql}
        GROUP BY strategy
        ORDER BY pct_profitable DESC, profitable_combos DESC
        """,
        mt_params + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 10 — Recovery factor ranking
# ---------------------------------------------------------------------------


def query_recovery_factor(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 5,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Rank strategies by average recovery factor (total_r / max_drawdown_r)."""
    mt_expr, mt_params = _min_trades_expr(scope, min_trades)
    sc_sql, sc_params = _scope_clauses(scope)
    df = conn.execute(
        f"""
        SELECT
            strategy,
            ROUND(AVG(recovery_factor), 2)              AS avg_rf,
            ROUND(MAX(recovery_factor), 2)              AS best_rf,
            ROUND(AVG(max_drawdown_r), 3)               AS avg_max_dd_r,
            ROUND({_pooled("avg_r")}, 3)                AS avg_r,
            SUM(closed_trades)                          AS total_trades,
            COUNT(*)                                    AS run_count
        FROM backtest_runs
        WHERE {mt_expr}
          AND recovery_factor IS NOT NULL
          AND recovery_factor > 0{sc_sql}
        GROUP BY strategy
        ORDER BY avg_rf DESC
        """,
        mt_params + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 11 — Co-firing confluence leaderboard
# ---------------------------------------------------------------------------


def query_co_firing(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 3,
    top_n: int = 30,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Rank strategy-pair combos by avg_r from backtest_combos table."""
    sc_sql, sc_params = _scope_clauses(scope)

    df = conn.execute(
        f"""
        -- One row per (symbol, tf, pair, window, day_filter) — latest run_at_ms wins.
        SELECT
            strategy_a || '+' || strategy_b       AS combo,
            symbol,
            timeframe,
            window_candles                         AS window,
            closed_trades                          AS trades,
            ROUND(win_rate * 100, 1)               AS win_pct,
            ROUND(avg_r, 3)                        AS avg_r,
            ROUND(total_r, 2)                      AS total_r,
            ROUND(max_drawdown_r, 2)               AS max_dd,
            ROUND(recovery_factor, 2)              AS rf
        FROM backtest_combos
        WHERE closed_trades >= ?{sc_sql}
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY symbol, timeframe, strategy_a, strategy_b, window_candles, day_filter
            ORDER BY run_at_ms DESC
        ) = 1
        ORDER BY avg_r DESC
        LIMIT {top_n}
        """,
        [min_trades] + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Card 12 — Cross-TF co-firing leaderboard
# ---------------------------------------------------------------------------


def query_cross_tf_combos(
    conn: duckdb.DuckDBPyConnection,
    min_trades: int = 3,
    top_n: int = 30,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Rank cross-TF strategy-pair combos by avg_r from backtest_cross_tf_combos.

    HTF/LTF roles are ordered (not a frozenset), so 4h→15m and 15m→4h are distinct.
    Deduplicates to the latest run per (symbol, tf_htf, tf_ltf, strategy_htf,
    strategy_ltf, window_hours, day_filter).
    """
    sc_sql, sc_params = _scope_clauses(scope)

    df = conn.execute(
        f"""
        -- One row per (symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf,
        --              window_hours, day_filter) — latest run_at_ms wins.
        SELECT
            strategy_htf || ' → ' || strategy_ltf  AS combo,
            tf_htf || ' → ' || tf_ltf              AS tf_pair,
            symbol,
            window_hours,
            closed_trades                           AS trades,
            ROUND(win_rate * 100, 1)                AS win_pct,
            ROUND(avg_r, 3)                         AS avg_r,
            ROUND(total_r, 2)                       AS total_r,
            ROUND(max_drawdown_r, 2)                AS max_dd,
            ROUND(recovery_factor, 2)               AS rf
        FROM backtest_cross_tf_combos
        WHERE closed_trades >= ?{sc_sql}
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf,
                         window_hours, day_filter
            ORDER BY run_at_ms DESC
        ) = 1
        ORDER BY avg_r DESC
        LIMIT {top_n}
        """,
        [min_trades] + sc_params,
    ).df()
    return _df_to_result(df)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_QUERY_FN: dict[str, Callable[..., DigestResult]] = {
    "symbol": query_symbol,
    "strategy": query_strategy,
    "tf": query_tf,
    "combos": query_combos,
    "adr_ab": query_adr_ab,
    "volume_ab": query_volume_ab,
    "day_filter_ab": query_day_filter_ab,
    "direction_bias": query_direction_bias,
    "consistency": query_consistency,
    "recovery_factor": query_recovery_factor,
    "co_firing": query_co_firing,
    "cross_tf_combos": query_cross_tf_combos,
}


_QUERY_MIN_TRADES: dict[str, int] = {
    "co_firing": 3,  # co-firing pairs are rare; lower floor than single-strategy queries
    "cross_tf_combos": 3,  # cross-TF pairs are equally rare
}
_DEFAULT_MIN_TRADES = 5


def run_digest(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    min_trades: int | None = None,
    top_n: int = 20,
    scope: DigestScope | None = None,
) -> DigestResult:
    """Dispatch to the named query function and return generic {columns, rows}.

    min_trades defaults to None, which lets each query use its own floor
    (_QUERY_MIN_TRADES for co_firing=3, _DEFAULT_MIN_TRADES=5 for everything else).
    Pass an explicit value to override.
    """
    fn = _QUERY_FN.get(query)
    if fn is None:
        raise ValueError(
            f"Unknown digest query '{query}'. Valid: {', '.join(QUERY_NAMES)}"
        )
    effective_min = (
        min_trades
        if min_trades is not None
        else _QUERY_MIN_TRADES.get(query, _DEFAULT_MIN_TRADES)
    )
    if query in ("combos", "co_firing", "cross_tf_combos"):
        return fn(conn, min_trades=effective_min, top_n=top_n, scope=scope)
    return fn(conn, min_trades=effective_min, scope=scope)
