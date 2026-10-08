"""Direction-filter backtest replay — answers the soft→hard flip question for T2c.

Replays `[bias.direction_filter]` + per-strategy `suppress_long` / `suppress_short`
flags against historical `backtest_trades`. For every trade the gate would have
dropped in hard mode, computes avg_r on the suppressed subset and compares to
the kept subset.

This is the empirical substitute for "wait 2 weeks in soft mode" — same
flip-decision data, derived from history rather than forward observation.
Mirrors `tools/regime_gate_replay.py`.

Decision rule (matching the original soft-mode criteria):
  * Suppressed avg_r ≤ 0 with n ≥ 100 → flip is justified.
  * Kept avg_r > suppressed avg_r → gate concentrates edge correctly.
  * Suppressed avg_r > 0 with n ≥ 100 → gate is dropping winners; redo.

Usage:
    PYTHONPATH=. poetry run python tools/direction_filter_replay.py [--db PATH] \\
        [--config config/signal_watch.toml]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.signal_config import StrategyOverride, load_signal_config
from analytics.store import DEFAULT_DB_PATH

_MIN_TRADES_FOR_DECISION = 100


def _suppressed_strategies(
    strategy_params: dict[str, StrategyOverride],
) -> dict[str, tuple[bool, bool]]:
    """Return {strategy: (suppress_long, suppress_short)} for any flagged strategy."""
    return {
        name: (ov.suppress_long, ov.suppress_short)
        for name, ov in strategy_params.items()
        if ov.suppress_long or ov.suppress_short
    }


def _load_trades(
    conn: duckdb.DuckDBPyConnection, strategies: list[str]
) -> pd.DataFrame:
    if not strategies:
        return pd.DataFrame()
    placeholders = ",".join(["?"] * len(strategies))
    return conn.execute(
        f"""
        SELECT strategy, direction, pnl_r AS realized_r
        FROM backtest_trades
        WHERE strategy IN ({placeholders})
          AND pnl_r IS NOT NULL
        """,
        strategies,
    ).fetch_df()


def _label_suppressed(
    trades: pd.DataFrame,
    flagged: dict[str, tuple[bool, bool]],
) -> pd.Series:
    def is_suppressed(row: pd.Series) -> bool:
        flags = flagged.get(str(row["strategy"]))
        if flags is None:
            return False
        long_flag, short_flag = flags
        if row["direction"] == "long":
            return long_flag
        if row["direction"] == "short":
            return short_flag
        return False

    return trades.apply(is_suppressed, axis=1)


def _format_summary(label: str, sub: pd.DataFrame) -> str:
    if sub.empty:
        return f"  {label:<14} n=0"
    return (
        f"  {label:<14} n={len(sub):>6}  "
        f"avg_r={sub['realized_r'].mean():+.4f}  "
        f"median_r={sub['realized_r'].median():+.4f}"
    )


def _per_strategy_breakdown(
    trades: pd.DataFrame,
    suppressed_mask: pd.Series,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (strategy, direction), grp in trades.groupby(["strategy", "direction"]):
        mask = suppressed_mask.loc[grp.index]
        n = len(grp)
        avg_r = grp["realized_r"].mean()
        sup = bool(mask.iloc[0]) if not mask.empty else False
        rows.append(
            {
                "strategy": strategy,
                "direction": direction,
                "n": n,
                "avg_r": avg_r,
                "suppressed": sup,
            }
        )
    return pd.DataFrame(rows).sort_values(["strategy", "direction"])


def _verdict(sup_avg: float, sup_n: int, kept_avg: float) -> str:
    if sup_n < _MIN_TRADES_FOR_DECISION:
        return f"INSUFFICIENT DATA — suppressed n={sup_n} < {_MIN_TRADES_FOR_DECISION}"
    if sup_avg > 0:
        return (
            "DO NOT FLIP — suppressed subset is net-positive "
            f"({sup_avg:+.4f}R). Gate would drop winners."
        )
    if kept_avg <= sup_avg:
        return (
            "DO NOT FLIP — kept subset is not better than suppressed "
            f"({kept_avg:+.4f}R vs {sup_avg:+.4f}R). Gate concentrates nothing."
        )
    return (
        f"FLIP justified — suppressed avg_r={sup_avg:+.4f}R "
        f"(would-be-dropped losers); kept avg_r={kept_avg:+.4f}R."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to analytics.db")
    p.add_argument(
        "--config",
        default="config/signal_watch.toml",
        help="Path to signal_watch TOML to replay (default: signal_watch.toml)",
    )
    args = p.parse_args(argv)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        return 2

    cfg = load_signal_config(cfg_path)
    flagged = _suppressed_strategies(cfg.strategy_params)
    if not flagged:
        print(
            "No strategies have suppress_long / suppress_short set in "
            f"{cfg_path}. Nothing to replay.",
            file=sys.stderr,
        )
        return 1

    print(f"Config: {cfg_path}")
    print(
        f"Gate state: enabled={cfg.bias.direction_filter_enabled} "
        f"mode={cfg.bias.direction_filter_mode!r}"
    )
    print("Flagged strategies:")
    for name, (lo, sh) in sorted(flagged.items()):
        bits = []
        if lo:
            bits.append("long")
        if sh:
            bits.append("short")
        print(f"  {name:<20} suppress: {', '.join(bits)}")

    conn = duckdb.connect(args.db, read_only=True)
    try:
        trades = _load_trades(conn, list(flagged.keys()))
    finally:
        conn.close()

    if trades.empty:
        print("No matching closed trades in backtest_trades.", file=sys.stderr)
        return 1

    suppressed_mask = _label_suppressed(trades, flagged)
    suppressed = trades[suppressed_mask]
    kept = trades[~suppressed_mask]

    print(f"\nTotal flagged-strategy trades: {len(trades):,}")
    print(_format_summary("SUPPRESSED", suppressed))
    print(_format_summary("KEPT", kept))

    sup_avg = float(suppressed["realized_r"].mean()) if not suppressed.empty else 0.0
    kept_avg = float(kept["realized_r"].mean()) if not kept.empty else 0.0
    print("\nVerdict:")
    print(f"  {_verdict(sup_avg, len(suppressed), kept_avg)}")

    print("\nPer (strategy × direction) breakdown:")
    breakdown = _per_strategy_breakdown(trades, suppressed_mask)
    for _, row in breakdown.iterrows():
        marker = "✗ suppressed" if row["suppressed"] else "  kept"
        print(
            f"  {row['strategy']:<20} {row['direction']:<6} "
            f"n={int(row['n']):>6}  avg_r={row['avg_r']:+.4f}  {marker}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
