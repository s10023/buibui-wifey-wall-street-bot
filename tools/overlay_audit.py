"""OV-1 — the 200-day MA filter judged as an overlay on a total-return frame (#418).

Runs the frozen pre-registration in
``docs/superpowers/specs/2026-10-08-edge-pillars-research-design.md`` § Phase 2:
``bh`` (French ``Mkt-RF + RF`` every session) against ``ov`` (that market while
the prior ``^GSPC`` close is above its 200-session SMA, French ``RF``
otherwise), gated on ulcer index with Sharpe non-inferiority. Read-only: the
French file is downloaded per run (or read from ``--french-zip``) and never
written to ``analytics.db``.

Run ``--precheck`` first. It prints the panel dates, the frame's calendar facts
and both bootstrap half-widths, and no point estimate.

Usage::

    PYTHONPATH=. poetry run python tools/overlay_audit.py --precheck
    PYTHONPATH=. poetry run python tools/overlay_audit.py
    PYTHONPATH=. poetry run python tools/overlay_audit.py --french-zip ff.zip --no-spy
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.overlay.frame import PANEL_START, build_frame, load_gspc_close
from analytics.overlay.replay import arm_returns
from analytics.overlay.report import (
    BLOCK,
    N_BOOT,
    SEED,
    SHARPE_MARGIN,
    TRADING_DAYS,
    UI_RATIO_BAR,
    OverlayGate,
    beta_attribution,
    bootstrap_legs,
    calendar_years,
    evaluate_overlay,
    excess_sharpe,
    overlay_verdict,
    summarize_arm,
)
from analytics.research_guards.survival import drawdown_breach_curve, ulcer_index
from analytics.store import DEFAULT_DB_PATH
from utils.french_client import (
    FRENCH_DAILY_URL,
    csv_from_zip,
    fetch_zip,
    parse_daily_factors,
)

PRIMARY_BPS = 2.0
REPORT_BPS = 5.0
BREACH_DEPTHS = (0.15, 0.20, 0.25, 0.30, 0.40)
BREACH_HORIZON = 5 * TRADING_DAYS
SPY_START = pd.Timestamp("1993-01-29")


def _load_french(path: Path | None) -> tuple[pd.DataFrame, str]:
    blob = path.read_bytes() if path else fetch_zip()
    text = csv_from_zip(blob)
    build = text.splitlines()[0].strip()
    src = str(path) if path else FRENCH_DAILY_URL
    sha = hashlib.sha256(blob).hexdigest()[:16]
    return parse_daily_factors(text), f"{src}\n  {build}  sha256[:16]={sha}"


def _primary(close: pd.Series, ff: pd.DataFrame, *, lag: int = 1) -> pd.DataFrame:
    return build_frame(close, ff["mkt_rf"] + ff["rf"], ff["rf"], lag=lag)


def _spy_frame(close: pd.Series, ff: pd.DataFrame) -> pd.DataFrame:
    from utils.yfinance_client import fetch_total_return_close

    spy = fetch_total_return_close("SPY")
    spy.index = pd.DatetimeIndex(spy.index).normalize()
    spy = spy.loc[spy.index <= ff.index[-1]]
    market = spy.pct_change().dropna()
    missing_rf = int((~market.index.isin(ff.index)).sum())
    if missing_rf:
        print(f"  ⚠ {missing_rf} SPY sessions have no French RF row; dropped")
        market = market.loc[market.index.isin(ff.index)]
    return build_frame(close, market, ff["rf"], start=SPY_START)


def _half(ci_lo: float, ci_hi: float) -> float:
    return (ci_hi - ci_lo) / 2.0


def _print_gate(label: str, gate: OverlayGate) -> None:
    print(
        f"  {label:<26} ΔUI {gate.d_ui.point:+7.3f} [{gate.d_ui.lo:+7.3f}, {gate.d_ui.hi:+7.3f}]"
        f"  UI ratio {gate.ui_ratio:5.3f}"
        f"  ΔSR {gate.d_sr.point:+6.3f} [{gate.d_sr.lo:+6.3f}, {gate.d_sr.hi:+6.3f}]"
        f"  → {gate.verdict}"
    )


def _print_arms(arms: pd.DataFrame, bps: float) -> None:
    print(
        f"  {'arm':<4} {'bps':>4} {'ann.ret':>8} {'ann.vol':>8} {'Sharpe':>7} {'UI':>7} "
        f"{'maxDD':>7} {'TUW(y)':>7} {'in-mkt':>7} {'sw/yr':>6}"
    )
    for arm in ("bh", "ov"):
        s = summarize_arm(arms, arm)
        print(
            f"  {arm:<4} {bps:>4.0f} {s.ann_return:>7.2%} {s.ann_vol:>8.4f} {s.sharpe:>7.3f} "
            f"{s.ulcer:>7.2f} {s.max_dd:>6.1%} {s.tuw_sessions / TRADING_DAYS:>7.1f} "
            f"{s.in_market:>6.1%} {s.switches_per_year:>6.2f}"
        )


def _print_alignment(frame: pd.DataFrame, close: pd.Series) -> None:
    """Return-free of the arms: does ^GSPC's date line up with French's?

    A one-day mislabel is how a timing rule acquires look-ahead, so the
    same-dated correlation must dominate the +/-1-day shifts in every era. A
    Monday after a Saturday session spans two French returns but one ^GSPC
    return, which dilutes the pre-1953 figure without shifting it.
    """
    g = close.pct_change()
    print("  alignment, corr(French mkt_t, ^GSPC return_t+k):")
    for lo, hi in (
        ("1929", "1951"),
        ("1953", "1975"),
        ("1976", "2026"),
        ("1953", "2026"),
    ):
        m = frame["mkt"].loc[pd.Timestamp(lo) : pd.Timestamp(f"{hi}-12-31")]
        cells = []
        for k in (-1, 0, 1):
            d = pd.DataFrame({"m": m, "g": g.shift(-k).reindex(m.index)}).dropna()
            cells.append(f"k={k:+d} {float(np.corrcoef(d['m'], d['g'])[0, 1]):+.4f}")
        print(f"    {lo}-{hi}  " + "  ".join(cells))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--french-zip", type=Path, default=None)
    parser.add_argument("--precheck", action="store_true")
    parser.add_argument("--no-spy", action="store_true")
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        close = load_gspc_close(conn)
    finally:
        conn.close()
    ff, source = _load_french(args.french_zip)
    frame = _primary(close, ff)
    arms = arm_returns(frame, bps=PRIMARY_BPS)

    print("=" * 96)
    print("OV-1 — 200-session MA filter as an overlay, French total-return frame")
    print("=" * 96)
    print(f"  French file: {source}")
    print(
        f"  ^GSPC 1d: {close.index[0].date()} → {close.index[-1].date()} ({len(close):,} closes)"
    )
    print(
        f"  PANEL: {frame.index[0].date()} → {frame.index[-1].date()}  "
        f"{len(frame):,} sessions ({calendar_years(frame.index):.1f} calendar years); "
        f"pre-registered start: first defined session on/after {PANEL_START.date()}"
    )
    sats = int((pd.DatetimeIndex(frame.index).dayofweek == 5).sum())
    print(
        f"  return calendar = French; {sats:,} Saturday sessions kept (no ^GSPC close)"
    )
    _print_alignment(frame, close)
    print(
        f"  bootstrap: stationary, mean block {BLOCK}, {N_BOOT:,} resamples, seed {SEED}; "
        f"cost {PRIMARY_BPS:g} bps per unit |Δpos| ({REPORT_BPS:g} reported)"
    )
    print()

    d_ui, d_sr = bootstrap_legs(arms)
    print("-- PRECISION (printed before any point estimate) " + "-" * 46)
    print(f"  leg 1  ΔUI half-width: {_half(d_ui.lo, d_ui.hi):.3f} ulcer points")
    print(f"  leg 3  ΔSR half-width: {_half(d_sr.lo, d_sr.hi):.4f} annualized Sharpe")
    print("         spec power line (Sharpe-of-difference, analytic): 0.1987")
    if args.precheck:
        return 0
    print()

    ratio = ulcer_index(arms["ov"].to_numpy()) / ulcer_index(arms["bh"].to_numpy())
    primary = OverlayGate(d_ui, ratio, d_sr, overlay_verdict(d_ui, ratio, d_sr))
    print(f"-- PRIMARY GATE @ {PRIMARY_BPS:g} bps " + "-" * 70)
    print(
        f"  legs: ΔUI CI hi < 0 · UI ratio <= {UI_RATIO_BAR} · "
        f"ΔSR CI lo > -{SHARPE_MARGIN} (Sharpe in excess of RF)"
    )
    _print_gate("ov - bh", primary)
    print(f"  VERDICT: {primary.verdict}")
    print()

    print("-- ARMS (reported, not gated) " + "-" * 66)
    _print_arms(arms, PRIMARY_BPS)
    arms5 = arm_returns(frame, bps=REPORT_BPS)
    _print_arms(arms5, REPORT_BPS)
    att = beta_attribution(arms)
    print(
        f"  ov on bh (excess): beta {att.beta:+.3f}  alpha {att.alpha_annual:+.2%}/yr  "
        f"t {att.alpha_t:+.2f}  — reported, not the yardstick"
    )
    print()

    print(
        f"-- P(drawdown >= D within 5 years), {N_BOOT:,} paired paths of "
        f"{BREACH_HORIZON} sessions " + "-" * 14
    )
    curves = {
        arm: drawdown_breach_curve(
            arms[arm].to_numpy(),
            BREACH_DEPTHS,
            horizon=BREACH_HORIZON,
            n_paths=N_BOOT,
            block=BLOCK,
            seed=SEED,
        )
        for arm in ("bh", "ov")
    }
    print("  " + "D".ljust(6) + "".join(f"{d:>8.0%}" for d in BREACH_DEPTHS))
    for arm, curve in curves.items():
        print("  " + arm.ljust(6) + "".join(f"{curve[d]:>8.1%}" for d in BREACH_DEPTHS))
    print()

    print("-- SENSITIVITY (reported; none can overturn the primary) " + "-" * 38)
    _print_gate(f"cost {REPORT_BPS:g} bps", evaluate_overlay(arms5))
    lag2 = arm_returns(_primary(close, ff, lag=2), bps=PRIMARY_BPS)
    _print_gate("1-session execution lag", evaluate_overlay(lag2))
    half = len(arms) // 2
    for label, sub in (
        ("first half", arms.iloc[:half]),
        ("second half", arms.iloc[half:]),
    ):
        dui = ulcer_index(sub["ov"].to_numpy()) - ulcer_index(sub["bh"].to_numpy())
        rf = sub["rf"].to_numpy()
        dsr = excess_sharpe(sub["ov"].to_numpy(), rf) - excess_sharpe(
            sub["bh"].to_numpy(), rf
        )
        print(
            f"  {label:<26} {sub.index[0].date()} → {sub.index[-1].date()}  "
            f"ΔUI {dui:+7.3f}  ΔSR {dsr:+6.3f}  (sign only)"
        )
    if not args.no_spy:
        spy = arm_returns(_spy_frame(close, ff), bps=PRIMARY_BPS)
        g = evaluate_overlay(spy)
        _print_gate("SPY total return 1993→", g)
        print(
            f"  SPY panel {spy.index[0].date()} → {spy.index[-1].date()} "
            f"({len(spy):,} sessions); ΔSR half-width {_half(g.d_sr.lo, g.d_sr.hi):.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
