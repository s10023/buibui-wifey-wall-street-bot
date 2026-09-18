"""Regime classifier slope-threshold sensitivity sweep.

Re-runs the regime gate replay (see `tools/regime_gate_replay.py`) across a
grid of candidate `_SLOPE_TREND_THRESHOLD` values in `analytics/regime.py`.
For each threshold, computes:

  * suppressed_n / suppressed_avg_r
  * kept_n / kept_avg_r
  * lift = kept_avg_r − suppressed_avg_r  (gate concentrates edge correctly
    when this is positive, with `suppressed_avg_r` ≤ 0)

The hypothesis (option 3.b in `docs/redesign/buibui-redesign-phase2-replay-findings.md`):
the live default 0.5% may mis-label exhaustion as trend. If raising the
threshold separates `kept_avg_r > suppressed_avg_r` cleanly with
suppressed ≤ 0, the §6 mapping is salvageable — just use that threshold.
If no value works, the mapping itself (option 3.a INVERT) is the issue.

Usage:
    PYTHONPATH=. poetry run python tools/regime_threshold_sweep.py [--db PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

import duckdb
import pandas as pd

from analytics.regime import classify_series
from analytics.signal_config import load_signal_config
from analytics.store import DEFAULT_DB_PATH
from analytics.strategies import STRATEGY_REGISTRY
from tools.regime_gate_replay import (
    _REGIME_TF,
    _load_4h_ohlcv,
    _load_trades,
    _regimes_at_entries,
    annotate_suppression,
)

_DEFAULT_THRESHOLDS: tuple[float, ...] = (
    0.001,
    0.002,
    0.003,
    0.005,
    0.0075,
    0.010,
    0.015,
    0.020,
    0.030,
    0.050,
)
_LIVE_THRESHOLD = 0.005


def _suppressible_strategies(cfg: object) -> set[str]:
    """Strategies the gate could suppress under the active config."""
    suppressible: set[str] = set()
    bias = cfg.bias  # type: ignore[attr-defined]
    for name, spec in STRATEGY_REGISTRY.items():
        if name in bias.regime_per_strategy:
            allowed = bias.regime_per_strategy[name]
        else:
            allowed = bias.regime_enabled_regimes.get(spec.strategy_type, [])
        if {"trend", "range", "high_vol"} - set(allowed):
            suppressible.add(name)
    return suppressible


def _annotate_regime_for_threshold(
    trades: pd.DataFrame,
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    threshold: float,
) -> pd.DataFrame:
    """Replay annotation under a custom slope threshold.

    Mirrors `tools.regime_gate_replay.annotate_regime_4h` but reuses pre-loaded
    OHLCV (so the sweep doesn't re-query DuckDB N times) and threads
    `slope_threshold` through `classify_series`.
    """
    parts: list[pd.DataFrame] = []
    for symbol, group in trades.groupby("symbol", sort=False):
        ohlcv = ohlcv_by_symbol.get(str(symbol))
        if ohlcv is None or ohlcv.empty:
            g = group.copy()
            g["regime"] = "unknown"
            parts.append(g)
            continue
        regimes = classify_series(ohlcv, _REGIME_TF, slope_threshold=threshold)
        g = group.copy()
        g["regime"] = _regimes_at_entries(g["entry_time"], ohlcv["open_time"], regimes)
        parts.append(g)
    return (
        pd.concat(parts, ignore_index=True)
        if parts
        else trades.assign(regime="unknown")
    )


def sweep(
    db_path: Path,
    config_path: Path,
    thresholds: tuple[float, ...] = _DEFAULT_THRESHOLDS,
) -> pd.DataFrame:
    cfg = load_signal_config(config_path)
    if not cfg.bias.regime_enabled:
        raise SystemExit(
            f"{config_path}: [bias.regime].enabled is false — nothing to sweep."
        )

    suppressible = _suppressible_strategies(cfg)
    conn = duckdb.connect(str(db_path), read_only=True)
    trades = _load_trades(conn, sorted(suppressible))
    if trades.empty:
        raise SystemExit(
            f"No backtest_trades for suppressible strategies {sorted(suppressible)}."
        )

    ohlcv_by_symbol: dict[str, pd.DataFrame] = {}
    for symbol in trades["symbol"].unique():
        ohlcv_by_symbol[str(symbol)] = _load_4h_ohlcv(conn, str(symbol))

    rows: list[dict[str, float | int]] = []
    for t in thresholds:
        annotated = _annotate_regime_for_threshold(trades, ohlcv_by_symbol, t)
        annotated = annotate_suppression(annotated, cfg.bias.regime_allowed)
        sup = annotated[annotated["suppressed"]]
        kept = annotated[~annotated["suppressed"]]
        sup_n = int(len(sup))
        kept_n = int(len(kept))
        sup_avg = float(sup["pnl_r"].mean()) if sup_n else 0.0
        kept_avg = float(kept["pnl_r"].mean()) if kept_n else 0.0
        rows.append(
            {
                "threshold": t,
                "n_suppressed": sup_n,
                "suppressed_avg_r": sup_avg,
                "n_kept": kept_n,
                "kept_avg_r": kept_avg,
                "lift": kept_avg - sup_avg,
            }
        )
    return pd.DataFrame(rows)


def _argmax_label(frame: pd.DataFrame, column: str) -> int:
    """Index label of `frame`'s largest `column`, narrowed to `int`.

    `sweep` builds its frame with `pd.DataFrame(rows)` from a list of dicts, so
    the index is a RangeIndex and every label — including one carried through a
    boolean mask — is an `int`. `Series.idxmax` is typed `-> Hashable`, which
    `.loc[]` has no overload for, so the cast states that invariant. Keep
    `idxmax` rather than `numpy.argmax`: it skips NaN, and `argmax` does not.
    """
    return cast(int, frame[column].idxmax())


def render(df: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("Regime classifier slope-threshold sweep")
    lines.append(f"  (live default = {_LIVE_THRESHOLD:.4f} — marked with *)")
    lines.append("-" * 78)
    lines.append(
        f"{'threshold':>10} {'n_supp':>8} {'sup_avg_r':>11} "
        f"{'n_kept':>8} {'kept_avg_r':>11} {'lift':>9}"
    )
    lines.append("-" * 78)
    for _, row in df.iterrows():
        marker = "*" if abs(float(row["threshold"]) - _LIVE_THRESHOLD) < 1e-9 else " "
        lines.append(
            f"{marker}{row['threshold']:>9.4f} "
            f"{int(row['n_suppressed']):>8} "
            f"{row['suppressed_avg_r']:>+11.4f} "
            f"{int(row['n_kept']):>8} "
            f"{row['kept_avg_r']:>+11.4f} "
            f"{row['lift']:>+9.4f}"
        )
    lines.append("-" * 78)

    qualifying = df[df["suppressed_avg_r"] <= 0]
    lines.append("")
    lines.append("Verdict:")
    if qualifying.empty:
        best = df.loc[_argmax_label(df, "lift")]
        lines.append(
            "  NO THRESHOLD QUALIFIES — every value leaves suppressed_avg_r > 0."
        )
        lines.append(
            f"  Best lift at threshold={best['threshold']:.4f} "
            f"(lift={best['lift']:+.4f}, sup_avg_r={best['suppressed_avg_r']:+.4f})."
        )
        lines.append(
            "  → Classifier refinement (option 3.b) cannot rescue the §6 mapping."
        )
        lines.append(
            "  → Next step: option 3.a (INVERT) or 3.c (re-derive from SignalCandidate)."
        )
    else:
        best = qualifying.loc[_argmax_label(qualifying, "lift")]
        lines.append(
            f"  WINNER threshold={best['threshold']:.4f} — "
            f"sup_avg_r={best['suppressed_avg_r']:+.4f} ≤ 0, "
            f"kept_avg_r={best['kept_avg_r']:+.4f}, lift={best['lift']:+.4f}."
        )
        lines.append(
            "  → §6 mapping is salvageable; update _SLOPE_TREND_THRESHOLD and re-replay."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(DEFAULT_DB_PATH),
        help=f"Path to analytics.db (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/strategy_params.toml"),
        help="Signal config to read [bias.regime] from (default: strategy_params.toml).",
    )
    parser.add_argument(
        "--thresholds",
        type=str,
        default=None,
        help="Comma-separated list of slope thresholds to sweep (overrides default grid).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional CSV output path for the per-threshold table.",
    )
    args = parser.parse_args(argv)

    if args.thresholds:
        thresholds = tuple(float(x) for x in args.thresholds.split(","))
    else:
        thresholds = _DEFAULT_THRESHOLDS

    df = sweep(args.db, args.config, thresholds)
    print(render(df))
    if args.out is not None:
        df.to_csv(args.out, index=False)
        print(f"\nWrote per-threshold CSV: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
