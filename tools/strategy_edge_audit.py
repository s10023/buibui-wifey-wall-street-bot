"""Strategy Edge Audit — Phase 0 of docs/redesign/buibui-redesign.md.

Produces a ranked CSV of every detector × (timeframe × regime × session) slice
plus combo uplift, then assigns a deterministic KILL / DEMOTE / KEEP verdict
per strategy. Output gates the §3 strategy cuts in the v2 redesign.

Usage:
    poetry run python tools/strategy_edge_audit.py [--db PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import duckdb
import pandas as pd

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.regime import classify_series
from analytics.store import DEFAULT_DB_PATH

# Decision rule thresholds (kept in sync with phase0 doc)
_KILL_AVG_R_CEIL = 0.0
_COMBO_UPLIFT_MIN = 0.10
_DEMOTE_MAX_SLICES = 2
_KEEP_MIN_POSITIVE_SLICES = 3
_LOW_CONFIDENCE_N = (
    30  # slices below this n are flagged; KILL gets downgraded to DEMOTE
)

_SESSION_BUCKETS: list[tuple[int, int, str]] = [
    (0, 8, "asia"),
    (8, 13, "london"),
    (13, 22, "ny"),
    (22, 24, "off"),
]


def _load_trades(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Closed trades joined with their run's symbol/tf/strategy."""
    return conn.execute("""
        SELECT
            t.strategy,
            t.symbol,
            t.timeframe,
            t.signal_time,
            t.direction,
            t.outcome,
            t.pnl_r
        FROM backtest_trades t
        WHERE t.outcome != 'open' AND t.pnl_r IS NOT NULL
    """).df()


def _load_ohlcv(conn: duckdb.DuckDBPyConnection, symbol: str, tf: str) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT open_time, high, low, close
        FROM ohlcv
        WHERE symbol = ? AND timeframe = ?
        ORDER BY open_time
        """,
        [symbol, tf],
    ).df()


def _annotate_regime(
    trades: pd.DataFrame, conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    """Attach a `regime` column to trades by classifying the host candle."""
    parts: list[pd.DataFrame] = []
    for (symbol, tf), group in trades.groupby(["symbol", "timeframe"], sort=False):
        ohlcv = _load_ohlcv(conn, str(symbol), str(tf))
        if ohlcv.empty:
            group = group.copy()
            group["regime"] = "unknown"
            parts.append(group)
            continue
        ohlcv["regime"] = classify_series(ohlcv, str(tf))
        regime_lookup = ohlcv.set_index("open_time")["regime"]
        merged = group.copy()
        merged["regime"] = merged["signal_time"].map(regime_lookup).fillna("unknown")
        parts.append(merged)
    return pd.concat(parts, ignore_index=True)


def _session_bucket(ts_ms: int) -> str:
    hour = (ts_ms // 3_600_000) % 24
    for start, end, label in _SESSION_BUCKETS:
        if start <= hour < end:
            return label
    return "off"


def _annotate_session(trades: pd.DataFrame) -> pd.DataFrame:
    trades = trades.copy()
    trades["session"] = trades["signal_time"].apply(_session_bucket)
    return trades


def _aggregate_standalone(trades: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per (strategy × tf × regime × session)."""
    grouped = trades.groupby(
        ["strategy", "timeframe", "regime", "session"], sort=False, observed=True
    )
    agg = grouped.agg(
        n_trades=("pnl_r", "size"),
        avg_r=("pnl_r", "mean"),
        win_rate=("outcome", lambda s: (s == "win").mean()),
    ).reset_index()
    agg["expectancy_R"] = agg["avg_r"] * agg["win_rate"]
    agg["low_confidence"] = agg["n_trades"] < _LOW_CONFIDENCE_N
    return agg.sort_values(
        ["strategy", "expectancy_R"], ascending=[True, False]
    ).reset_index(drop=True)


def _aggregate_combos(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Best combo uplift per strategy across same-tf and cross-tf combos.

    Uplift = best_combo_avg_r − best_standalone_avg_r (per strategy).
    """
    standalone_max = conn.execute("""
        SELECT strategy, MAX(avg_r) AS standalone_max_avg_r
        FROM backtest_runs
        WHERE closed_trades >= 30
        GROUP BY strategy
    """).df()

    same_tf = conn.execute("""
        SELECT strategy_a AS strategy, MAX(avg_r) AS combo_max_avg_r,
               SUM(closed_trades) AS combo_n
        FROM backtest_combos
        WHERE closed_trades >= 30
        GROUP BY strategy_a
        UNION ALL
        SELECT strategy_b AS strategy, MAX(avg_r) AS combo_max_avg_r,
               SUM(closed_trades) AS combo_n
        FROM backtest_combos
        WHERE closed_trades >= 30
        GROUP BY strategy_b
    """).df()

    cross_tf = conn.execute("""
        SELECT strategy_htf AS strategy, MAX(avg_r) AS combo_max_avg_r,
               SUM(closed_trades) AS combo_n
        FROM backtest_cross_tf_combos
        WHERE closed_trades >= 30
        GROUP BY strategy_htf
        UNION ALL
        SELECT strategy_ltf AS strategy, MAX(avg_r) AS combo_max_avg_r,
               SUM(closed_trades) AS combo_n
        FROM backtest_cross_tf_combos
        WHERE closed_trades >= 30
        GROUP BY strategy_ltf
    """).df()

    all_combos = pd.concat([same_tf, cross_tf], ignore_index=True)
    if all_combos.empty:
        result = standalone_max.copy()
        result["combo_max_avg_r"] = pd.NA
        result["combo_n"] = 0
        result["uplift_R"] = pd.NA
        return result

    best_combo = all_combos.groupby("strategy", as_index=False).agg(
        combo_max_avg_r=("combo_max_avg_r", "max"), combo_n=("combo_n", "sum")
    )
    out = standalone_max.merge(best_combo, on="strategy", how="outer")
    out["uplift_R"] = out["combo_max_avg_r"] - out["standalone_max_avg_r"]
    return out


def _decide_verdicts(slices: pd.DataFrame, combos: pd.DataFrame) -> pd.DataFrame:
    """Per-strategy verdict — KILL / DEMOTE / KEEP — applied to all slices."""
    valid = slices[slices["regime"] != "unknown"]
    per_strategy = (
        valid.groupby("strategy")
        .agg(
            n_slices=("avg_r", "size"),
            n_positive_slices=("avg_r", lambda s: (s > 0).sum()),
            best_avg_r=("avg_r", "max"),
            worst_avg_r=("avg_r", "min"),
            total_n=("n_trades", "sum"),
            best_low_conf=("low_confidence", "min"),
        )
        .reset_index()
    )
    merged = per_strategy.merge(
        combos[["strategy", "uplift_R", "combo_max_avg_r"]],
        on="strategy",
        how="left",
    )

    def verdict(row: pd.Series) -> str:
        all_negative = row["best_avg_r"] <= _KILL_AVG_R_CEIL
        no_combo_edge = pd.isna(row["uplift_R"]) or row["uplift_R"] < _COMBO_UPLIFT_MIN
        if all_negative and no_combo_edge:
            # Sample-size tie-breaker: low-conf in best slice → DEMOTE not KILL
            if row["best_low_conf"]:
                return "DEMOTE"
            return "KILL"
        if row["n_positive_slices"] >= _KEEP_MIN_POSITIVE_SLICES:
            return "KEEP"
        if row["n_positive_slices"] <= _DEMOTE_MAX_SLICES:
            return "DEMOTE"
        return "KEEP"

    merged["verdict"] = merged.apply(verdict, axis=1)
    return merged.sort_values(
        ["verdict", "best_avg_r"], ascending=[True, False]
    ).reset_index(drop=True)


def _write_csv(slices: pd.DataFrame, verdicts: pd.DataFrame, path: Path) -> None:
    verdict_lookup = verdicts.set_index("strategy")["verdict"]
    out = slices.copy()
    out["verdict"] = out["strategy"].map(verdict_lookup).fillna("UNKNOWN")
    out["pct_of_fires"] = out.groupby("strategy")["n_trades"].transform(
        lambda s: s / s.sum()
    )
    cols = [
        "verdict",
        "strategy",
        "timeframe",
        "regime",
        "session",
        "n_trades",
        "avg_r",
        "win_rate",
        "expectancy_R",
        "pct_of_fires",
        "low_confidence",
    ]
    out[cols].to_csv(path, index=False)


def _print_summary(verdicts: pd.DataFrame) -> None:
    print()
    print("Per-strategy verdicts (sorted by verdict, then best slice avg_r):")
    print("-" * 90)
    cols = [
        "verdict",
        "strategy",
        "n_slices",
        "n_positive_slices",
        "best_avg_r",
        "worst_avg_r",
        "uplift_R",
        "combo_max_avg_r",
        "total_n",
    ]

    def _fmt_signed(v: float) -> str:
        return "n/a" if pd.isna(v) else f"{v:+.3f}"

    formatters: dict[str, Callable[..., str]] = {
        "best_avg_r": _fmt_signed,
        "worst_avg_r": _fmt_signed,
        "uplift_R": _fmt_signed,
        "combo_max_avg_r": _fmt_signed,
    }
    print(verdicts[cols].to_string(index=False, formatters=formatters))
    print()
    print("Verdict counts:")
    print(verdicts["verdict"].value_counts().to_string())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy edge audit (Phase 0)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/strategy_edge_audit.csv"),
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        trades = _load_trades(conn)
        if trades.empty:
            print("No closed trades found in DB.", file=sys.stderr)
            return 1
        print(f"Loaded {len(trades):,} closed trades", file=sys.stderr)

        trades = _annotate_regime(trades, conn)
        trades = _annotate_session(trades)
        slices = _aggregate_standalone(trades)
        combos = _aggregate_combos(conn)
        verdicts = _decide_verdicts(slices, combos)

        _write_csv(slices, verdicts, args.out)
        print(f"Wrote {len(slices):,} slice rows → {args.out}", file=sys.stderr)
        _print_summary(verdicts)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
