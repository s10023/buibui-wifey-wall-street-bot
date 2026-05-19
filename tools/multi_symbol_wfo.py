"""Multi-symbol pooled-trades WFO sweep — covers T-A (combined) and Task A
(directional) follow-ups to T14.

Runs `run_param_sweep` (tp_r-only grid) per symbol for a fixed cohort, then pools
OOS Trade objects across symbols and picks the tp_r maximizing pooled OOS avg_r.

Two modes:
  --direction combined  (default)  — pool BacktestResult.closed_trades (both
                                     directions). Original T-A use case.
  --direction long|short           — pool BacktestResult.long_closed_trades /
                                     short_closed_trades. Task A use case:
                                     directional tp_r_long / tp_r_short overrides.
  --direction both                 — print combined + long + short tables per
                                     cell. Useful when comparing.

Cell lists are hardcoded:
  --cells t-a    Original T-A T14-pending markers.
  --cells task-a (default) Task A directional cells — 4 strategies × 3 TFs ×
                 2 day_filters, including 1wk per Task C finding.

Usage:
    PYTHONPATH=. poetry run python tools/multi_symbol_wfo.py [--direction long|short|both] [--cells task-a|t-a] [--db PATH]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from analytics.backtest_lib import BacktestResult, Trade
from analytics.data_store import DEFAULT_DB_PATH
from analytics.param_sweep import ParamRange, _float_range, run_param_sweep

SYMBOLS = ("AAPL", "MSFT", "SPY", "QQQ")

# Per-TF history anchors: matches T14 (data start = earliest yfinance candle
# the watchlist covers uniformly). 1wk uses the 1d anchor — it just gives us
# fewer bars, which the MIN_POOLED_N gate filters out naturally.
SINCE_4H = "2024-05-16"
SINCE_1D = "2023-01-03"
SINCE_1WK = "2023-01-03"

# Cohort-pooled minimum trade count gate. With 4 symbols, ≥ 10 pooled trades
# is roughly "≥ 2–3 per symbol on average" — enough to trust the pooled avg_r
# without letting one outlier symbol dominate. Applied PER DIRECTION when in
# directional mode (so a 20-trade combined sample with 10 long + 10 short
# clears, but a 12-trade sample with 11 long + 1 short does not for short).
MIN_POOLED_N = 10

TP_R_RANGE = ParamRange("tp_r", _float_range(1.0, 5.0, 0.5))

FEE_PCT = 0.0
WFO_SPLIT = 0.7

# (strategy, tf, day_filter, current_tp_r, config_label)
# config_label is the TOML the cell lives in (for the printed report only).
# T-A cell list (combined-direction WFO that retired the T14 "multi-symbol
# pending" markers). Kept for re-runnability / regression.
CELLS_T_A: list[tuple[str, str, str, float, str]] = [
    # signal_watch.toml (tue_thu)
    ("pin_bar", "4h", "tue_thu", 4.5, "signal_watch.toml"),
    ("pin_bar", "1d", "tue_thu", 4.0, "signal_watch.toml"),
    ("hammer_hanging_man", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("inside_bar", "4h", "tue_thu", 3.5, "signal_watch.toml"),
    ("fib_golden_zone", "4h", "tue_thu", 3.5, "signal_watch.toml"),
    ("bos", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("bos", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("trend_day", "4h", "tue_thu", 3.5, "signal_watch.toml"),
    ("orb", "4h", "tue_thu", 5.0, "signal_watch.toml"),
    # signal_watch_weekdays.toml
    ("pin_bar", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("hammer_hanging_man", "4h", "weekdays", 4.0, "signal_watch_weekdays.toml"),
    ("fib_golden_zone", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("fib_golden_zone", "1d", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("bos", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("bos", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("trend_day", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("orb", "4h", "weekdays", 5.0, "signal_watch_weekdays.toml"),
    ("eqh_eql", "1d", "weekdays", 2.0, "signal_watch_weekdays.toml"),
    ("morning_evening_star", "1d", "weekdays", 4.0, "signal_watch_weekdays.toml"),
    ("ema", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
]

# Task A cell list — 4 strategies T14 flagged for directional gaps × 3 TFs ×
# 2 day_filters. 1wk included per Task C finding (every WFO since T13 had
# skipped 1wk; production signals routed through uncalibrated R-multiples).
# current_tp_r shown for context only — directional commit decision uses the
# new tp_r_long_<tf> / tp_r_short_<tf> keys (architecture extension in this PR).
CELLS_TASK_A: list[tuple[str, str, str, float, str]] = [
    # signal_watch.toml (tue_thu) — uses strategy-wide tp_r=3.0 on all 4 strategies.
    ("ema", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("ema", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("ema", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("trend_day", "4h", "tue_thu", 3.5, "signal_watch.toml"),  # tp_r_4h=3.5 (T14)
    ("trend_day", "1d", "tue_thu", 3.0, "signal_watch.toml"),  # tp_r=3.0
    ("trend_day", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("morning_evening_star", "4h", "tue_thu", 3.0, "signal_watch.toml"),  # tp_r=3.0
    ("morning_evening_star", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("morning_evening_star", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    # signal_watch_weekdays.toml — mix of strategy-wide and per-TF keys (see T14/T-A).
    ("ema", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("ema", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("ema", "1wk", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("engulfing", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("engulfing", "1d", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("engulfing", "1wk", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    (
        "trend_day",
        "4h",
        "weekdays",
        4.0,
        "signal_watch_weekdays.toml",
    ),  # tp_r_4h=4.0 (T-A)
    ("trend_day", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("trend_day", "1wk", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    (
        "morning_evening_star",
        "4h",
        "weekdays",
        2.5,
        "signal_watch_weekdays.toml",
    ),  # tp_r_4h=2.5 (T14)
    (
        "morning_evening_star",
        "1d",
        "weekdays",
        4.0,
        "signal_watch_weekdays.toml",
    ),  # tp_r_1d=4.0
    (
        "morning_evening_star",
        "1wk",
        "weekdays",
        4.0,
        "signal_watch_weekdays.toml",
    ),  # inherits tp_r=4.0 fallback
]


def _date_to_ms(d: str) -> int:
    dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _since_for_tf(tf: str) -> int:
    if tf == "4h":
        return _date_to_ms(SINCE_4H)
    if tf == "1wk":
        return _date_to_ms(SINCE_1WK)
    return _date_to_ms(SINCE_1D)


def _min_trades_for_tf(tf: str) -> int:
    # Mirrors the `_tf_defaults` in param_sweep.main. 1wk gets the same floor
    # as 1d (2) — 1wk samples are intrinsically thinnest and the WFO min_trades
    # gate filters per-symbol; the real edge gate is MIN_POOLED_N downstream.
    return {"4h": 5, "1d": 2, "1wk": 2}.get(tf, 5)


def _select_trades(res: BacktestResult, direction: str) -> list[Trade]:
    """Filter BacktestResult trades by direction.

    direction = "combined" → res.closed_trades (both directions)
              = "long"     → res.long_closed_trades
              = "short"    → res.short_closed_trades
    """
    if direction == "long":
        return res.long_closed_trades
    if direction == "short":
        return res.short_closed_trades
    return res.closed_trades


@dataclass
class PooledRow:
    tp_r: float
    pooled_n: int
    pooled_avg_r: float | None
    pooled_win_rate: float | None
    per_symbol_n: dict[str, int]


def _pool_oos_by_tp_r(
    per_symbol_results: dict[str, list],
    direction: str = "combined",
) -> list[PooledRow]:
    """Group SweepRow lists by tp_r across symbols and compute pooled OOS metrics.

    direction filters which trades are pooled (see _select_trades).
    """
    by_tp: dict[float, dict[str, BacktestResult]] = defaultdict(dict)
    for sym, rows in per_symbol_results.items():
        for row in rows:
            tp = float(row.params["tp_r"])
            by_tp[tp][sym] = row.oos_result

    pooled: list[PooledRow] = []
    for tp in sorted(by_tp.keys()):
        per_sym_n: dict[str, int] = {}
        r_values: list[float] = []
        win_count = 0
        for sym in SYMBOLS:
            res = by_tp[tp].get(sym)
            if res is None:
                per_sym_n[sym] = 0
                continue
            trades = _select_trades(res, direction)
            per_sym_n[sym] = len(trades)
            for t in trades:
                pnl = t.pnl_r
                if pnl is None:
                    continue
                r_values.append(pnl)
                if t.outcome == "win":
                    win_count += 1
        n = len(r_values)
        avg_r = sum(r_values) / n if n > 0 else None
        wr = win_count / n if n > 0 else None
        pooled.append(
            PooledRow(
                tp_r=tp,
                pooled_n=n,
                pooled_avg_r=avg_r,
                pooled_win_rate=wr,
                per_symbol_n=per_sym_n,
            )
        )
    return pooled


def _pick_winner(pooled: list[PooledRow]) -> PooledRow | None:
    """Pick the tp_r with the highest pooled OOS avg_r, subject to MIN_POOLED_N."""
    eligible = [
        p for p in pooled if p.pooled_n >= MIN_POOLED_N and p.pooled_avg_r is not None
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda p: p.pooled_avg_r or float("-inf"))


def _fmt_r(v: float | None) -> str:
    if v is None:
        return "  —  "
    return f"{v:+.3f}R"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "  —  "
    return f"{v * 100:.1f}%"


def _print_pooled_grid(
    pooled: list[PooledRow],
    direction_label: str,
    current_tp_r: float,
    tf: str,
) -> None:
    print(
        f"\n  [{direction_label}]"
        f"  {'tp_r':>5}  {'pooled n':>9}  {'pooled avg_r':>13}  {'pooled wr':>10}  "
        f"per-symbol n (AAPL, MSFT, SPY, QQQ)"
    )
    print("  " + "─" * 110)
    for p in pooled:
        per_sym = ", ".join(str(p.per_symbol_n.get(s, 0)) for s in SYMBOLS)
        print(
            f"  {'':10}  {p.tp_r:>5.1f}  {p.pooled_n:>9}  {_fmt_r(p.pooled_avg_r):>13}  "
            f"{_fmt_pct(p.pooled_win_rate):>10}  ({per_sym})"
        )

    winner = _pick_winner(pooled)
    if winner is None:
        print(
            f"\n  ✗ [{direction_label}] No eligible winner "
            f"(no tp_r reaches pooled_n ≥ {MIN_POOLED_N}). Keep current."
        )
        return

    if (winner.pooled_avg_r or 0.0) <= 0:
        print(
            f"\n  ✗ [{direction_label}] Best pooled OOS avg_r is non-positive "
            f"({_fmt_r(winner.pooled_avg_r)} at tp_r={winner.tp_r}, n={winner.pooled_n}). "
            "No directional edge — keep current."
        )
        return

    delta = winner.tp_r - current_tp_r
    arrow = "→" if delta != 0.0 else "="
    # Suggest TOML key based on direction.
    if direction_label == "long":
        key_hint = f"tp_r_long_{tf} = {winner.tp_r}"
    elif direction_label == "short":
        key_hint = f"tp_r_short_{tf} = {winner.tp_r}"
    else:
        key_hint = f"tp_r_{tf} = {winner.tp_r}"
    print(
        f"\n  ✓ [{direction_label}] Winner: tp_r {current_tp_r} {arrow} {winner.tp_r}  "
        f"(pooled OOS avg_r={_fmt_r(winner.pooled_avg_r)}, "
        f"n={winner.pooled_n}, wr={_fmt_pct(winner.pooled_win_rate)})"
        f"\n     TOML: {key_hint}"
    )


def _sweep_cell(
    conn: duckdb.DuckDBPyConnection,
    strategy: str,
    tf: str,
    day_filter: str,
    current_tp_r: float,
    config_label: str,
    directions: tuple[str, ...],
) -> None:
    since_ms = _since_for_tf(tf)
    min_trades = _min_trades_for_tf(tf)

    header = (
        f"\n{'=' * 110}\n"
        f"  Cell: {strategy} / {tf} / day_filter={day_filter}  "
        f"(currently tp_r={current_tp_r} in {config_label})\n"
        f"{'=' * 110}"
    )
    print(header)

    per_symbol_results: dict[str, list] = {}
    for sym in SYMBOLS:
        rows = run_param_sweep(
            conn=conn,
            strategy=strategy,
            symbol=sym,
            timeframe=tf,
            days=0,  # ignored when since_ms set
            param_ranges=[TP_R_RANGE],
            wfo_split=WFO_SPLIT,
            min_trades=min_trades,
            fee_pct=FEE_PCT,
            top_n=99,
            since_ms=since_ms,
            day_filter=day_filter,
        )
        per_symbol_results[sym] = rows

    any_grid = False
    for direction in directions:
        pooled = _pool_oos_by_tp_r(per_symbol_results, direction=direction)
        if not pooled:
            print(f"  [{direction}] No grid results (detection or data error).")
            continue
        any_grid = True
        _print_pooled_grid(pooled, direction, current_tp_r, tf)

    if not any_grid:
        print("  Skipping cell — no grid data for any direction.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="4-symbol pooled WFO sweep (T-A combined + Task A directional)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to DuckDB database (default: analytics.db)",
    )
    parser.add_argument(
        "--cells",
        choices=("task-a", "t-a"),
        default="task-a",
        help="Cell list to sweep. task-a (default) = 4 directional-gap cells "
        "× 3 TFs × 2 day_filters. t-a = original T-A combined-direction cells.",
    )
    parser.add_argument(
        "--direction",
        choices=("long", "short", "both", "combined"),
        default="both",
        help="Pool by direction. long / short = single-direction pooling "
        "(Task A). combined = both directions in one bucket (T-A). "
        "both (default) = print all three tables per cell.",
    )
    parser.add_argument(
        "--cell",
        type=str,
        default=None,
        help='Filter to a single cell, format "strategy/tf/day_filter". '
        'Example: "ema/4h/tue_thu". Omit to run all cells in the chosen list.',
    )
    args = parser.parse_args()

    all_cells = CELLS_TASK_A if args.cells == "task-a" else CELLS_T_A

    if args.cell:
        parts = args.cell.split("/")
        if len(parts) != 3:
            raise SystemExit(
                f"--cell must be strategy/tf/day_filter, got: {args.cell!r}"
            )
        wanted = tuple(parts)
        cells = [c for c in all_cells if (c[0], c[1], c[2]) == wanted]
        if not cells:
            raise SystemExit(f"No cell matches {args.cell!r}")
    else:
        cells = list(all_cells)

    if args.direction == "both":
        directions: tuple[str, ...] = ("combined", "long", "short")
    else:
        directions = (args.direction,)

    print(
        f"Multi-symbol pooled WFO sweep — cohort: {', '.join(SYMBOLS)}\n"
        f"  Cells: {len(cells)} ({args.cells})   "
        f"tp_r grid: {len(TP_R_RANGE.values)} values "
        f"({TP_R_RANGE.values[0]}..{TP_R_RANGE.values[-1]} step 0.5)\n"
        f"  Directions: {', '.join(directions)}\n"
        f"  Anchors: 4h since {SINCE_4H}, 1d since {SINCE_1D}, "
        f"1wk since {SINCE_1WK}   fee_pct={FEE_PCT}   "
        f"wfo_split={WFO_SPLIT}   min_pooled_n={MIN_POOLED_N} (per direction)"
    )

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        for strategy, tf, day_filter, current_tp_r, config_label in cells:
            _sweep_cell(
                conn,
                strategy,
                tf,
                day_filter,
                current_tp_r,
                config_label,
                directions,
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
