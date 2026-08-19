"""Regime gate backtest replay — answers the soft→hard flip question.

Replays the v2 Phase 2 regime gate (`config/strategy_params.toml [bias.regime]`)
against historical `backtest_trades`. For each trade the gate would have
suppressed in hard mode, computes avg_r on the suppressed subset.

This is the empirical substitute for the original "wait 2 weeks in soft
mode" plan: same flip-decision data, derived from history rather than
forward observation.

Decision rule — PER CELL, then combined under the single-switch constraint.

Each suppressed (strategy × regime) cell earns an :mod:`analytics.audit_guard`
verdict: a block-bootstrap CI on the suppressed slice's mean R that must clear
±``bar``, AND a Holm-adjusted p-value below ``alpha`` across the family of
tested cells. ``ENABLE`` means that slice reliably loses (dropping it helps);
``DISABLE``/``CONCENTRATE`` means it reliably wins (dropping it costs);
``INSUFFICIENT`` means the run cannot tell.

⚠ **The cells are then combined, not pooled.** ``[bias.regime].mode`` is ONE
GLOBAL SWITCH: flipping it to ``hard`` activates every cell's suppression at
once, so a single reliably-winning cell blocks the flip no matter how many
cells or how much volume point the other way. The previous rule pooled an
n-weighted ``avg_r`` across strategy AND regime, which let one large losing
cell carry the aggregate and print ``FLIP justified`` while cells pointing the
other way sat in the table above it — the tool's own docs had to carry a
"read the per-cell table, never the banner" warning to compensate. The banner
now derives from the cells, so the warning is no longer needed.

The pooled aggregates are still printed, labelled DESCRIPTIVE, and are not
decision-bearing.

Usage:
    PYTHONPATH=. poetry run python tools/regime_gate_replay.py [--db PATH]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.audit_guard import (
    DECISION_CONCENTRATE,
    DECISION_DISABLE,
    DECISION_ENABLE,
    DEFAULT_ALPHA,
    DEFAULT_BAR,
    DEFAULT_MIN_N,
    AuditCell,
    CellVerdict,
    evaluate_audit_cells,
)
from analytics.regime import Regime, classify_series
from analytics.signal_config import load_signal_config
from analytics.store import DEFAULT_DB_PATH
from analytics.strategies import STRATEGY_REGISTRY

# Live gate uses 4h candles regardless of signal TF (per config/strategy_params.toml).
_REGIME_TF = "4h"

# A cell whose CI clears the bar on the winning side blocks the flip outright,
# because `[bias.regime].mode` is a single global switch — see module docstring.
_BLOCKING_DECISIONS = frozenset({DECISION_DISABLE, DECISION_CONCENTRATE})


def _load_trades(
    conn: duckdb.DuckDBPyConnection, strategies: list[str]
) -> pd.DataFrame:
    """All closed trades for the strategies the gate could suppress."""
    if not strategies:
        return pd.DataFrame()
    placeholders = ",".join(["?"] * len(strategies))
    return conn.execute(
        f"""
        SELECT strategy, symbol, timeframe, direction, entry_time, pnl_r
        FROM backtest_trades
        WHERE strategy IN ({placeholders})
          AND outcome != 'open'
          AND pnl_r IS NOT NULL
        """,
        strategies,
    ).df()


def _load_4h_ohlcv(conn: duckdb.DuckDBPyConnection, symbol: str) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT open_time, high, low, close
        FROM ohlcv
        WHERE symbol = ? AND timeframe = ?
        ORDER BY open_time
        """,
        [symbol, _REGIME_TF],
    ).df()


def _regimes_at_entries(
    entry_times_ms: pd.Series,
    bar_open_times: pd.Series,
    bar_regimes: pd.Series,
) -> pd.Series:
    """Regime of the most recent CLOSED bar at each entry time.

    Resolved POSITIONALLY against the bar index, never by arithmetic on the
    timestamp. The previous implementation floored `entry_time` to a UTC 4h
    boundary, which is correct only on a 24/7 tape: on an RTH equity tape 4h
    bars stamp 13:30/17:30 UTC, so **0 of 105,708** bars in this repo's DB are
    UTC-4h aligned. The floor therefore produced a key no bar could have, every
    lookup missed, and `fillna("unknown")` turned that into a fall-open — the
    replay reported 0 suppressed of 2,849 trades and printed a verdict
    indistinguishable from a genuine sample shortage.

    Mirrors live's `iloc[-2]` rule by stepping back one bar from the bar
    containing the entry. Entries before the second bar have no closed
    predecessor and resolve to "unknown", matching live's cache-miss fall-open.
    """
    labels = np.asarray(bar_regimes, dtype=object)
    index = entry_times_ms.index
    if labels.size == 0:
        return pd.Series("unknown", index=index, dtype=object)
    opens = np.asarray(bar_open_times, dtype="int64")
    entries = np.asarray(entry_times_ms, dtype="int64")
    # searchsorted(right) - 1 = bar containing the entry; one more back = last CLOSED bar.
    pos = np.searchsorted(opens, entries, side="right") - 2
    resolved = np.where(pos >= 0, labels[np.clip(pos, 0, None)], "unknown")
    return pd.Series(resolved, index=index, dtype=object)


def annotate_regime_4h(
    trades: pd.DataFrame, conn: duckdb.DuckDBPyConnection
) -> pd.DataFrame:
    """Attach `regime` (Regime label) per trade based on 4h classification.

    Trades whose 4h regime cannot be resolved (insufficient history, cache miss)
    are labelled `unknown` — matching the live gate's fall-open behaviour.
    """
    parts: list[pd.DataFrame] = []
    for symbol, group in trades.groupby("symbol", sort=False):
        ohlcv = _load_4h_ohlcv(conn, str(symbol))
        if ohlcv.empty:
            g = group.copy()
            g["regime"] = "unknown"
            parts.append(g)
            continue
        ohlcv["regime"] = classify_series(ohlcv, _REGIME_TF)
        g = group.copy()
        g["regime"] = _regimes_at_entries(
            g["entry_time"], ohlcv["open_time"], ohlcv["regime"]
        )
        parts.append(g)
    return (
        pd.concat(parts, ignore_index=True)
        if parts
        else trades.assign(regime="unknown")
    )


def annotate_suppression(
    trades: pd.DataFrame, regime_allowed_fn: object
) -> pd.DataFrame:
    """Mark each trade as suppressed/kept by replaying the gate logic.

    `regime_allowed_fn` matches BiasConfig.regime_allowed signature:
      (strategy, strategy_type, regime) -> bool
    """
    out = trades.copy()
    types = {n: s.strategy_type for n, s in STRATEGY_REGISTRY.items()}

    def _is_suppressed(row: pd.Series) -> bool:
        strategy = str(row["strategy"])
        regime: Regime = row["regime"]
        if regime == "unknown":
            return False  # falls open
        strategy_type = types.get(strategy, "")
        return not regime_allowed_fn(strategy, strategy_type, regime)  # type: ignore[operator]

    out["suppressed"] = out.apply(_is_suppressed, axis=1)
    return out


def aggregate(trades: pd.DataFrame) -> pd.DataFrame:
    """Per-(strategy × regime × suppressed) avg_r table."""
    grouped = trades.groupby(
        ["strategy", "regime", "suppressed"], sort=False, observed=True
    )
    agg = grouped.agg(
        n=("pnl_r", "size"),
        avg_r=("pnl_r", "mean"),
        win_rate=("pnl_r", lambda s: (s > 0).mean()),
    ).reset_index()
    return agg.sort_values(
        ["suppressed", "strategy", "regime"], ascending=[False, True, True]
    ).reset_index(drop=True)


FLIP_JUSTIFIED = "FLIP justified"
FLIP_BLOCKED = "DO NOT FLIP"
FLIP_HOLD = "HOLD"


@dataclass(frozen=True)
class CellEvidence:
    """One suppressed cell's identity plus its :mod:`analytics.audit_guard` verdict."""

    strategy: str
    regime: str
    verdict: CellVerdict


def build_audit_cells(trades: pd.DataFrame) -> list[tuple[str, str, AuditCell]]:
    """One :class:`AuditCell` per suppressed (strategy × regime) cell.

    ``kept_r`` is the SAME strategy's surviving book across all regimes — the
    honest counterfactual for a per-strategy gate: what that strategy still
    trades once this cell is dropped. Suppression is a pure function of
    (strategy, regime), so no cell is part-suppressed and the two slices are
    disjoint by construction; that is why ``kept_r`` cannot be drawn from the
    cell itself.
    """
    out: list[tuple[str, str, AuditCell]] = []
    if trades.empty:
        return out
    kept_by_strategy: dict[str, list[float]] = {
        str(name): [float(x) for x in group["pnl_r"]]
        for name, group in trades[~trades["suppressed"]].groupby("strategy", sort=False)
    }
    empty: list[float] = []
    suppressed = trades[trades["suppressed"]]
    for (strategy, regime), group in suppressed.groupby(
        ["strategy", "regime"], sort=True
    ):
        name, label = str(strategy), str(regime)
        out.append(
            (
                name,
                label,
                AuditCell(
                    label=f"{name}/{label}",
                    supp_r=[float(x) for x in group["pnl_r"]],
                    kept_r=kept_by_strategy.get(name, empty),
                ),
            )
        )
    return out


def evaluate_cells(
    trades: pd.DataFrame,
    *,
    bar: float = DEFAULT_BAR,
    alpha: float = DEFAULT_ALPHA,
    min_n: int = DEFAULT_MIN_N,
) -> list[CellEvidence]:
    """Per-cell verdicts sharing ONE Holm family across the suppressed cells."""
    built = build_audit_cells(trades)
    if not built:
        return []
    verdicts = evaluate_audit_cells(
        [cell for _, _, cell in built], bar=bar, alpha=alpha, min_n=min_n
    )
    return [
        CellEvidence(strategy, regime, verdict)
        for (strategy, regime, _), verdict in zip(built, verdicts, strict=True)
    ]


def flip_verdict(cells: Sequence[CellEvidence]) -> tuple[str, list[str]]:
    """Combine per-cell verdicts under the single-global-switch constraint.

    ⚠ **Not a pool.** ``[bias.regime].mode`` flips every cell at once, so ONE
    cell whose CI clears the bar on the winning side blocks the flip regardless
    of how many cells or how much trade volume point the other way. An
    n-weighted mean cannot express that, which is why the old banner could
    contradict its own table.
    """
    blocking = [c for c in cells if c.verdict.decision in _BLOCKING_DECISIONS]
    enabling = [c for c in cells if c.verdict.decision == DECISION_ENABLE]
    reasons: list[str] = []

    if blocking:
        for cell in blocking:
            supp = cell.verdict.supp_avg
            reasons.append(
                f"{cell.strategy}/{cell.regime}: {cell.verdict.decision} — "
                f"suppressing a reliable winner (n={cell.verdict.n_supp}, "
                f"avg_r={supp:+.4f})"
                if supp is not None
                else f"{cell.strategy}/{cell.regime}: {cell.verdict.decision}"
            )
        reasons.append(
            "mode is ONE GLOBAL SWITCH — a single blocking cell rules out the flip, "
            "even where other cells earn ENABLE."
        )
        return FLIP_BLOCKED, reasons

    if not enabling:
        reasons.append(
            "No cell clears the bar on either side — the run cannot tell, which is "
            "NOT evidence the gate is harmless."
        )
        powered = [c for c in cells if c.verdict.powered_null]
        if powered:
            reasons.append(
                f"{len(powered)} of {len(cells)} cell(s) are powered nulls "
                "(CI strictly inside ±bar): an effect worth acting on IS ruled out there."
            )
        return FLIP_HOLD, reasons

    for cell in enabling:
        supp = cell.verdict.supp_avg
        reasons.append(
            f"{cell.strategy}/{cell.regime}: ENABLE — suppressing a reliable loser "
            f"(n={cell.verdict.n_supp}, avg_r={supp:+.4f})"
            if supp is not None
            else f"{cell.strategy}/{cell.regime}: ENABLE"
        )
    reasons.append(f"No blocking cell among the {len(cells)} suppressed cell(s).")
    return FLIP_JUSTIFIED, reasons


def attach_verdicts(agg: pd.DataFrame, cells: Sequence[CellEvidence]) -> pd.DataFrame:
    """Left-join each cell's verdict onto the descriptive aggregate.

    The exported CSV would otherwise persist only the DESCRIPTIVE half and drop
    every decision-bearing column — CI, adjusted p, verdict — which is the half
    a reader needs to reach the same conclusion the banner did. Kept rows are
    never tested, so their verdict columns stay null by construction rather than
    by omission.
    """
    if agg.empty:
        return agg
    by_key = {(c.strategy, c.regime): c.verdict for c in cells}
    out = agg.copy()
    keys = list(
        zip(out["strategy"].astype(str), out["regime"].astype(str), strict=True)
    )
    out["verdict"] = [by_key[k].decision if k in by_key else None for k in keys]
    out["ci_lo"] = [by_key[k].ci_lo if k in by_key else None for k in keys]
    out["ci_hi"] = [by_key[k].ci_hi if k in by_key else None for k in keys]
    out["adj_pvalue"] = [by_key[k].adj_pvalue if k in by_key else None for k in keys]
    out["powered_null"] = [
        by_key[k].powered_null if k in by_key else None for k in keys
    ]
    return out


def render_verdict(agg: pd.DataFrame, cells: Sequence[CellEvidence] = ()) -> str:
    """Print per-cell table + global flip verdict."""
    lines: list[str] = []
    lines.append("Per-cell (strategy × regime × suppressed):")
    lines.append("-" * 78)
    lines.append(
        f"{'strategy':<18} {'regime':<10} {'supp':<6} {'n':>7} {'avg_r':>8} {'win%':>6}"
    )
    lines.append("-" * 78)
    for _, row in agg.iterrows():
        lines.append(
            f"{row['strategy']:<18} {row['regime']:<10} "
            f"{'YES' if row['suppressed'] else 'no':<6} "
            f"{row['n']:>7} {row['avg_r']:>+8.4f} {row['win_rate'] * 100:>5.1f}%"
        )
    lines.append("-" * 78)

    suppressed = agg[agg["suppressed"]]
    kept = agg[~agg["suppressed"]]
    if not suppressed.empty:
        weighted_avg_r = (suppressed["avg_r"] * suppressed["n"]).sum() / suppressed[
            "n"
        ].sum()
        total_n = int(suppressed["n"].sum())
        lines.append("")
        lines.append("DESCRIPTIVE aggregates — NOT decision-bearing:")
        lines.append(f"  SUPPRESSED: n={total_n}  avg_r={weighted_avg_r:+.4f}")
    if not kept.empty:
        weighted_avg_r_k = (kept["avg_r"] * kept["n"]).sum() / kept["n"].sum()
        total_n_k = int(kept["n"].sum())
        lines.append(f"  KEPT:       n={total_n_k}  avg_r={weighted_avg_r_k:+.4f}")
    if not suppressed.empty or not kept.empty:
        lines.append(
            "  (An n-weighted mean pools across strategy AND regime, so one large "
            "cell can carry it. The decision below reads the cells.)"
        )

    lines.append("")
    lines.append(
        f"Per-cell significance (bootstrap CI vs ±{DEFAULT_BAR:.2f}R, "
        f"Holm-adjusted across {len(cells)} cell(s), alpha={DEFAULT_ALPHA:.2f}):"
    )
    lines.append("-" * 78)
    lines.append(
        f"{'strategy':<18} {'regime':<10} {'n':>6} {'avg_r':>8} "
        f"{'CI':>18} {'adj_p':>7}  verdict"
    )
    lines.append("-" * 78)
    for cell in cells:
        v = cell.verdict
        avg = f"{v.supp_avg:+.4f}" if v.supp_avg is not None else "n/a"
        ci = (
            f"[{v.ci_lo:+.3f}, {v.ci_hi:+.3f}]"
            if v.ci_lo is not None and v.ci_hi is not None
            else "—"
        )
        adj = f"{v.adj_pvalue:.3f}" if v.adj_pvalue is not None else "—"
        flag = " (powered null)" if v.powered_null else ""
        lines.append(
            f"{cell.strategy:<18} {cell.regime:<10} {v.n_supp:>6} {avg:>8} "
            f"{ci:>18} {adj:>7}  {v.decision}{flag}"
        )
    if not cells:
        lines.append("  (no suppressed cells)")
    lines.append("-" * 78)

    decision, reasons = flip_verdict(cells)
    lines.append("")
    lines.append("Decision:")
    lines.append(f"  {decision}")
    for reason in reasons:
        lines.append(f"    - {reason}")

    return "\n".join(lines)


def run(db_path: Path, config_path: Path) -> tuple[pd.DataFrame, str]:
    cfg = load_signal_config(config_path)
    if not cfg.bias.regime_enabled:
        raise SystemExit(
            f"{config_path}: [bias.regime].enabled is false — nothing to replay."
        )

    # Strategies the gate could suppress: any strategy whose type is NOT enabled
    # in at least one regime, plus per_strategy overrides.
    suppressible: set[str] = set()
    for name, spec in STRATEGY_REGISTRY.items():
        # Default mapping vs override.
        if name in cfg.bias.regime_per_strategy:
            allowed = cfg.bias.regime_per_strategy[name]
        else:
            allowed = cfg.bias.regime_enabled_regimes.get(spec.strategy_type, [])
        if {"trend", "range", "high_vol"} - set(allowed):
            suppressible.add(name)

    conn = duckdb.connect(str(db_path), read_only=True)
    trades = _load_trades(conn, sorted(suppressible))
    if trades.empty:
        raise SystemExit(
            f"No backtest_trades for suppressible strategies {sorted(suppressible)}."
        )

    trades = annotate_regime_4h(trades, conn)
    trades = annotate_suppression(trades, cfg.bias.regime_allowed)
    agg = aggregate(trades)
    cells = evaluate_cells(trades)
    return attach_verdicts(agg, cells), render_verdict(agg, cells)


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
        "--out",
        type=Path,
        default=None,
        help=(
            "Optional CSV output path for the per-cell aggregate, including the "
            "verdict / CI / adjusted-p columns for every tested cell."
        ),
    )
    args = parser.parse_args(argv)

    agg, verdict = run(args.db, args.config)
    print(verdict)
    if args.out is not None:
        agg.to_csv(args.out, index=False)
        print(f"\nWrote per-cell CSV: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
