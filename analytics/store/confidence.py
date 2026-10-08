"""confidence_ratings table accessors (combined + directional star ratings)."""

import time
from collections.abc import Collection

import duckdb
import pandas as pd


def upsert_confidence_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    ratings: dict[str, dict[str, int]],
    win_rates: pd.DataFrame,
    day_filter: str | None = None,
    direction: str = "combined",
    avg_r_col: str = "avg_r",
    win_rate_col: str = "win_rate",
) -> None:
    """Upsert per-config confidence star ratings keyed by (config_name, strategy, tf, direction).

    ratings: {strategy: {tf: stars}}
    win_rates: DataFrame from get_backtest_win_rates() — used to store avg_r/win_rate alongside stars.
    day_filter: the config's day_filter value — stored so backtest rows can JOIN correctly.
    direction: 'combined' (default), 'long', or 'short'.
    avg_r_col / win_rate_col: column names to read from win_rates (allows directional lookups).
    """
    if not ratings:
        return
    now_ms = int(time.time() * 1000)
    stats: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    if not win_rates.empty and avg_r_col in win_rates.columns:
        for _, row in win_rates.iterrows():
            key = (str(row["strategy"]), str(row["timeframe"]))
            ar = row.get(avg_r_col)
            wr = row.get(win_rate_col)
            stats[key] = (
                float(ar) if ar is not None and not pd.isna(ar) else None,
                float(wr) if wr is not None and not pd.isna(wr) else None,
            )
    rows = []
    for strategy, tf_map in ratings.items():
        for tf, stars in tf_map.items():
            avg_r_val, win_rate_val = stats.get((strategy, tf), (None, None))
            rows.append(
                {
                    "config_name": config_name,
                    "strategy": strategy,
                    "tf": tf,
                    "direction": direction,
                    "stars": stars,
                    "avg_r": avg_r_val,
                    "win_rate": win_rate_val,
                    "updated_at_ms": now_ms,
                    "day_filter": day_filter,
                }
            )
    df = pd.DataFrame(rows)
    conn.register("_cr_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO confidence_ratings "
            "SELECT config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter "
            "FROM _cr_upsert_df"
        )
    finally:
        conn.unregister("_cr_upsert_df")


def prune_undeclared_confidence_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    declared: Collection[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Delete this config's ratings for cells it does not declare.

    Returns the deleted (strategy, tf, direction) triples so the caller can
    report them — a silent delete would swap one invisible surface for another.

    Necessary because ``upsert_confidence_ratings`` only ever inserts-or-
    replaces: filtering the *write* stops new orphans but cannot remove rows
    already on disk, which then outlive the declaration that produced them
    indefinitely. Scoped to ``config_name`` so the two live configs never prune
    each other, and keyed on (strategy, tf) across all directions because
    ``declared`` has no direction axis.

    A declared cell that is merely unrated this run — too few trades — is left
    untouched: it is not an orphan, and dropping it would silently discard a
    rating the next refresh may well restore.
    """
    rows = conn.execute(
        "SELECT strategy, tf, direction FROM confidence_ratings WHERE config_name = ?",
        [config_name],
    ).fetchall()
    allowed = set(declared)
    orphans = [
        (str(strategy), str(tf), str(direction))
        for strategy, tf, direction in rows
        if (str(strategy), str(tf)) not in allowed
    ]
    for strategy, tf, direction in orphans:
        conn.execute(
            "DELETE FROM confidence_ratings WHERE config_name = ? AND strategy = ? "
            "AND tf = ? AND direction = ?",
            [config_name, strategy, tf, direction],
        )
    return orphans


def get_confidence_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    direction: str = "combined",
) -> dict[str, dict[str, int]]:
    """Load confidence star ratings for a given config and direction from the DB.

    direction: 'combined' (default), 'long', or 'short'.
    Returns {strategy: {tf: stars}}, or empty dict if no ratings have been written yet.
    """
    rows = conn.execute(
        "SELECT strategy, tf, stars FROM confidence_ratings "
        "WHERE config_name = ? AND direction = ?",
        [config_name, direction],
    ).fetchall()
    result: dict[str, dict[str, int]] = {}
    for strategy, tf, stars in rows:
        if strategy not in result:
            result[str(strategy)] = {}
        result[str(strategy)][str(tf)] = int(stars)
    return result


def get_directional_confidence_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
) -> dict[str, dict[str, dict[str, int]]]:
    """Load directional confidence star ratings for a given config.

    Returns {strategy: {tf: {"long": stars, "short": stars}}}.
    Only includes entries where both long and short ratings exist.
    """
    rows = conn.execute(
        "SELECT strategy, tf, direction, stars FROM confidence_ratings "
        "WHERE config_name = ? AND direction IN ('long', 'short')",
        [config_name],
    ).fetchall()
    result: dict[str, dict[str, dict[str, int]]] = {}
    for strategy, tf, direction, stars in rows:
        s, t, d = str(strategy), str(tf), str(direction)
        if s not in result:
            result[s] = {}
        if t not in result[s]:
            result[s][t] = {}
        result[s][t][d] = int(stars)
    return result
