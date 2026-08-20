#!/usr/bin/env python
"""H9 warning-value audit — do the W1–W8 candle warnings predict avg_r?

Live alerts append candle-anatomy warnings (W1 marubozu, W2 equal levels,
W5 wick rejection, W6 consecutive candles, W7 doji, W8 inside bar) that are
never persisted and gate nothing. This tool regenerates each historical
trade's flags from OHLCV via :mod:`analytics.warning_audit` (same helpers as
the live path) and emits a pre-committed SUPPRESS-CANDIDATE / REVERSE /
COSMETIC / INSUFFICIENT verdict per (warning × direction) via
:mod:`analytics.audit_guard` (block-bootstrap CI clearing ±bar + Holm
haircut, one family per source).

Substrate roles (pre-committed): ``backtest_trades`` = primary (verdicts
gate); ``signal_alert_outcomes`` = corroboration only. Backtest trades are
deduped across saved runs on (symbol, tf, strategy, direction, signal_time),
keeping the lexicographically-latest run_id — necessary here because
``backtest_runs`` has **four** writers and one signal legitimately appears
under several saved runs. Read-only; no engine/live change.

Ported from parent PR #492. Two fork-specific changes, both load-bearing:
timeframe length comes from ``analytics.signal._common.parse_timeframe_secs``
rather than a local map, because equity timeframes are ``4h`` / ``1d`` /
**``1wk``** and upstream's literal map spells the weekly one ``1w`` — a silent
``KeyError`` on the one timeframe wifey backtests but never scans live; and the
live substrate is expected to return ``INSUFFICIENT`` almost everywhere
(267 resolved rows against ``min_n=30`` across 12 cells).

Corrected 2026-08-13 (parent PR #617): COSMETIC now requires
``audit_guard.CellVerdict.powered_null`` — the CI strictly inside ±bar —
rather than the old ``n >= min_n`` proxy. A sample-size floor says a test
*ran*, never that it could have *seen* anything, so the proxy published
under-powered cells as "this warning carries no information".

Run: ``PYTHONPATH=. poetry run python tools/warning_value_audit.py``
(wrapped by ``make wifey-warning-value-audit``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.signal._common import parse_timeframe_secs  # noqa: E402
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.market_data import get_ohlcv  # noqa: E402
from analytics.warning_audit import (  # noqa: E402
    WARNING_KEYS,
    WarningVerdict,
    evaluate_warning_cells,
    tag_trades,
)

SourceResult = tuple[list[WarningVerdict], pd.DataFrame, int, int]


def _tf_ms(tf: str) -> int:
    """Timeframe length in ms, via the repo's single parser.

    Deliberately NOT a local literal map: upstream's spells the weekly
    timeframe ``1w`` while every equity surface here uses yfinance's ``1wk``,
    so a copied map raises ``KeyError`` on the one timeframe that carries
    backtest trades but no live scans. ``parse_timeframe_secs`` already
    normalises the ``wk`` suffix, so deferring to it removes the trap by
    construction rather than by correcting a literal.
    """
    try:
        return parse_timeframe_secs(tf) * 1000
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError(f"unknown timeframe: {tf}") from exc


# --------------------------------------------------------------------------- #
# source normalization (pure)                                                  #
# --------------------------------------------------------------------------- #


def normalize_live(df: pd.DataFrame) -> pd.DataFrame:
    """``signal_alert_outcomes`` rows → the common entry frame."""
    out = pd.DataFrame(
        {
            "symbol": df["symbol"],
            "tf": df["tf"],
            "strategy": df["strategy"],
            "direction": df["direction"],
            "ts_ms": df["candle_ts_ms"],
            "r": df["outcome_r"],
        }
    )
    return out.dropna(subset=["ts_ms", "r"]).reset_index(drop=True)


def normalize_backtest(df: pd.DataFrame) -> pd.DataFrame:
    """``backtest_trades`` rows → the common entry frame, deduped across runs.

    The same signal can appear under multiple saved runs (config refreshes,
    sweeps). Dedup on (symbol, tf, strategy, direction, ts_ms) keeping the
    lexicographically-latest ``run_id`` — deterministic, one row per signal.
    """
    out = pd.DataFrame(
        {
            "run_id": df["run_id"],
            "symbol": df["symbol"],
            "tf": df["timeframe"],
            "strategy": df["strategy"],
            "direction": df["direction"],
            "ts_ms": df["signal_time"],
            "r": df["pnl_r"],
        }
    ).dropna(subset=["ts_ms", "r"])
    out = out.sort_values("run_id", kind="stable").drop_duplicates(
        subset=["symbol", "tf", "strategy", "direction", "ts_ms"], keep="last"
    )
    return out.drop(columns=["run_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# DB front door (read-only)                                                    #
# --------------------------------------------------------------------------- #


def _load_entries(db: Path, src: str, since_ms: int | None) -> pd.DataFrame:
    with duckdb.connect(str(db), read_only=True) as conn:
        if src == "live":
            q = (
                "SELECT symbol, tf, strategy, direction, candle_ts_ms, outcome_r "
                "FROM signal_alert_outcomes "
                "WHERE outcome_r IS NOT NULL AND candle_ts_ms IS NOT NULL"
            )
            if since_ms is not None:
                q += f" AND candle_ts_ms >= {since_ms}"
            return normalize_live(conn.execute(q).df())
        q = (
            "SELECT run_id, symbol, timeframe, strategy, direction, "
            "signal_time, pnl_r "
            "FROM backtest_trades WHERE pnl_r IS NOT NULL"
        )
        if since_ms is not None:
            q += f" AND signal_time >= {since_ms}"
        return normalize_backtest(conn.execute(q).df())


def _load_market(
    db: Path, entries: pd.DataFrame, window_bars: int
) -> dict[tuple[str, str], pd.DataFrame]:
    out: dict[tuple[str, str], pd.DataFrame] = {}
    pairs = entries[["symbol", "tf"]].drop_duplicates()
    with duckdb.connect(str(db), read_only=True) as conn:
        for symbol, tf in zip(pairs["symbol"], pairs["tf"], strict=True):
            sub = entries[(entries["symbol"] == symbol) & (entries["tf"] == tf)]
            margin = (window_bars + 2) * _tf_ms(str(tf))
            tmin, tmax = int(sub["ts_ms"].min()), int(sub["ts_ms"].max())
            out[(str(symbol), str(tf))] = get_ohlcv(
                conn, str(symbol), str(tf), tmin - margin, tmax + _tf_ms(str(tf))
            )
    return out


# --------------------------------------------------------------------------- #
# exploratory (reported, NOT gate-deciding)                                    #
# --------------------------------------------------------------------------- #


def exploratory_by_tf(tagged: pd.DataFrame) -> pd.DataFrame:
    """Per-(warning × direction × tf) breakdown; report-only, no verdicts."""
    rows: list[dict[str, object]] = []
    combos = tagged[["direction", "tf"]].drop_duplicates()
    for warning in WARNING_KEYS:
        for direction, tf in zip(combos["direction"], combos["tf"], strict=True):
            sub = tagged[(tagged["direction"] == direction) & (tagged["tf"] == tf)]
            warned = sub.loc[sub[warning], "r"]
            clean = sub.loc[~sub[warning], "r"]
            if warned.empty:
                continue
            avg_clean = float(clean.mean()) if len(clean) else float("nan")
            rows.append(
                {
                    "warning": warning,
                    "direction": direction,
                    "tf": tf,
                    "n_warned": int(len(warned)),
                    "avg_warned": float(warned.mean()),
                    "n_clean": int(len(clean)),
                    "avg_clean": avg_clean,
                    "lift": float(warned.mean()) - avg_clean,
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# report                                                                       #
# --------------------------------------------------------------------------- #


def _fmt(x: float | None) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:+.3f}"


def format_report(
    results: dict[str, SourceResult],
    *,
    min_n: int,
    bar: float,
    alpha: float,
    n_boot: int,
    seed: int | None,
) -> str:
    lines: list[str] = []
    lines.append(
        "# H9 warning-value audit — do the W1–W8 alert warnings predict avg_r?"
    )
    lines.append("")
    lines.append(
        f"Generated by `tools/warning_value_audit.py` (read-only). "
        f"Params: min_n={min_n}, bar=±{bar}R, alpha={alpha}, "
        f"n_boot={n_boot}, seed={seed}."
    )
    lines.append("")
    lines.append(
        "Pre-committed semantics: the warned slice is the would-be-suppressed "
        "slice of a hypothetical warning gate. SUPPRESS-CANDIDATE = warned "
        "trades reliably lose ≥ bar (audit_guard ENABLE: bootstrap CI + Holm). "
        "REVERSE = warned trades reliably win ≥ bar (DISABLE). COSMETIC = "
        "the CI lies strictly inside ±bar, i.e. an effect worth acting on is "
        "RULED OUT (CONCENTRATE detail kept in Raw). INSUFFICIENT = not "
        "ruled out — either n below the floor or a CI wider than the bar; "
        "these are different states and neither is a null result. "
        "The two-sample lift CI (warned − clean) "
        "is corroboration only. Backtest = primary substrate; live = "
        "corroboration only."
    )
    lines.append("")
    lines.append("## Headline (backtest primary)")
    lines.append("")
    bt = results.get("backtest")
    if bt is None:
        lines.append("- (backtest source not run — no primary verdict)")
    else:
        sup = [
            f"{v.warning}/{v.direction}"
            for v in bt[0]
            if v.verdict == "SUPPRESS-CANDIDATE"
        ]
        rev = [f"{v.warning}/{v.direction}" for v in bt[0] if v.verdict == "REVERSE"]
        if sup:
            lines.append("- SUPPRESS-CANDIDATE: " + ", ".join(sup))
        if rev:
            lines.append("- REVERSE: " + ", ".join(rev))
        if not sup and not rev:
            lines.append(
                "- COSMETIC — no warning carries gate-grade information on "
                "the primary (backtest) substrate"
            )
    for src, (verdicts, expl, n_entries, n_dropped) in results.items():
        lines.append("")
        lines.append(f"## Source: {src} ({n_entries} tagged, {n_dropped} dropped)")
        lines.append("")
        lines.append(
            "| warning | dir | n_warn | days | DEFF | n_clean | avg_warn | avg_clean "
            "| CI lo | CI hi | Holm p | lift lo | lift hi | raw | verdict |"
        )
        lines.append(
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
            "| --- | --- | --- | --- |"
        )
        for v in verdicts:
            days = "—" if v.n_days is None else str(v.n_days)
            deff = "—" if v.design_effect is None else f"{v.design_effect:.2f}"
            lines.append(
                f"| {v.warning} | {v.direction} | {v.n_warned} | {days} | {deff} "
                f"| {v.n_clean} "
                f"| {_fmt(v.avg_warned)} | {_fmt(v.avg_clean)} "
                f"| {_fmt(v.ci_lo)} | {_fmt(v.ci_hi)} | {_fmt(v.adj_pvalue)} "
                f"| {_fmt(v.lift_lo)} | {_fmt(v.lift_hi)} "
                f"| {v.raw_decision} | {v.verdict} |"
            )
        if not expl.empty:
            lines.append("")
            lines.append(f"### Exploratory per-tf breakdown ({src}, report-only)")
            lines.append("")
            lines.append(
                "| warning | dir | tf | n_warn | avg_warn | n_clean "
                "| avg_clean | lift |"
            )
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            top = expl.sort_values("n_warned", ascending=False).head(40)
            for row in top.to_dict("records"):
                lines.append(
                    f"| {row['warning']} | {row['direction']} | {row['tf']} "
                    f"| {row['n_warned']} | {_fmt(float(row['avg_warned']))} "
                    f"| {row['n_clean']} | {_fmt(float(row['avg_clean']))} "
                    f"| {_fmt(float(row['lift']))} |"
                )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="H9: do the W1–W8 alert warnings predict avg_r?"
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--source", choices=["live", "backtest", "both"], default="both")
    p.add_argument("--since-days", type=int, default=None)
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--bar", type=float, default=0.05)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--window-bars", type=int, default=12)
    p.add_argument("--out", type=Path, default=None)
    return p


def main() -> int:
    args = build_parser().parse_args()
    since_ms: int | None = None
    if args.since_days is not None:
        since_ms = int(
            (
                pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.since_days)
            ).timestamp()
            * 1000
        )
    sources = ["backtest", "live"] if args.source == "both" else [args.source]
    results: dict[str, SourceResult] = {}
    for src in sources:
        entries = _load_entries(args.db, src, since_ms)
        if entries.empty:
            print(f"[warn] no entries for source={src}", file=sys.stderr)
            continue
        market = _load_market(args.db, entries, args.window_bars)
        tagged, dropped = tag_trades(entries, market, window_bars=args.window_bars)
        if tagged.empty:
            print(f"[warn] nothing taggable for source={src}", file=sys.stderr)
            continue
        verdicts = evaluate_warning_cells(
            tagged,
            min_n=args.min_n,
            bar=args.bar,
            alpha=args.alpha,
            n_boot=args.n_boot,
            seed=args.seed,
        )
        results[src] = (verdicts, exploratory_by_tf(tagged), len(tagged), dropped)
    if not results:
        print("no data — nothing to evaluate", file=sys.stderr)
        return 1
    report = format_report(
        results,
        min_n=args.min_n,
        bar=args.bar,
        alpha=args.alpha,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    print(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
        print(f"[saved] {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
