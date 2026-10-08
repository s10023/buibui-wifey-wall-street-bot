"""VM — volatility-managed index exposure as a paired increment over OV-1 (#421).

Runs the frozen pre-registration in
``docs/superpowers/specs/2026-10-08-edge-pillars-research-design.md``
§ Amendment 1, on OV-1's frame and panel: ``vm`` holds ``min(1, σ_target /
σ̂_20d)`` of the market and ``RF`` on the rest, ``ovvm`` holds OV-1's position
times that weight. The headline is the increment test, ``ovvm`` against ``ov``;
the spec falsifier is ``vm`` against ``bh``. Both use OV-1's three legs.
Read-only, like ``tools/overlay_audit.py``, whose loaders it reuses.

Run ``--precheck`` first. It prints the panel, ``σ_target``, the pre-1952
Saturday volatility ratio and every gated half-width, and no point estimate.

Usage::

    PYTHONPATH=. poetry run python tools/vm_overlay_audit.py --precheck
    PYTHONPATH=. poetry run python tools/vm_overlay_audit.py --french-zip ff.zip
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.overlay.frame import (
    PANEL_START,
    build_frame,
    load_gspc_close,
    vol_target,
    with_vol_weight,
)
from analytics.overlay.replay import arm_returns
from analytics.overlay.report import (
    BLOCK,
    N_BOOT,
    SEED,
    SHARPE_MARGIN,
    TRADING_DAYS,
    UI_RATIO_BAR,
    beta_attribution,
    bootstrap_legs,
    calendar_years,
    evaluate_overlay,
    excess_sharpe,
    summarize_arm,
)
from analytics.overlay.rules import VOL_WINDOW, realized_vol, vol_weight
from analytics.research_guards.survival import drawdown_breach_curve, ulcer_index
from analytics.store import DEFAULT_DB_PATH
from tools.overlay_audit import (
    BREACH_DEPTHS,
    BREACH_HORIZON,
    PRIMARY_BPS,
    REPORT_BPS,
    SPY_START,
    _half,
    _load_french,
    _print_gate,
)

POSITIONS = {"ov": "pos", "vm": "w", "ovvm": "w_ov"}
ARMS = ("bh", "ov", "vm", "ovvm")
# (label, base, arm): the headline first, then the spec falsifier.
TESTS = (
    ("increment ovvm - ov", "ov", "ovvm"),
    ("falsifier vm - bh", "bh", "vm"),
)
REPORTED = ("replacement vm - ov", "ov", "vm")


def _vm_frame(
    close: pd.Series,
    market: pd.Series,
    rf: pd.Series,
    target: float | pd.Series,
    *,
    lag: int = 1,
    start: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """OV-1's frame on ``market`` plus VM's weight, opened where both are defined."""
    frame = build_frame(close, market, rf, lag=lag, start=start or PANEL_START)
    w = vol_weight(market, target, lag=lag).reindex(frame.index)
    frame = frame.loc[w.index[w.notna()][0] :]
    return with_vol_weight(frame, market, target, lag=lag)


def _gates(arms: pd.DataFrame, prefix: str = "") -> None:
    for label, base, arm in (*TESTS, REPORTED):
        _print_gate(f"{prefix}{label}", evaluate_overlay(arms, base=base, arm=arm))


def _print_arms(arms: pd.DataFrame, bps: float) -> None:
    print(
        f"  {'arm':<5} {'bps':>4} {'ann.ret':>8} {'ann.vol':>8} {'Sharpe':>7} {'UI':>7} "
        f"{'maxDD':>7} {'TUW(y)':>7} {'expo':>6} {'turn/yr':>8}"
    )
    for arm in ARMS:
        s = summarize_arm(arms, arm, pos=POSITIONS.get(arm))
        print(
            f"  {arm:<5} {bps:>4.0f} {s.ann_return:>7.2%} {s.ann_vol:>8.4f} {s.sharpe:>7.3f} "
            f"{s.ulcer:>7.2f} {s.max_dd:>6.1%} {s.tuw_sessions / TRADING_DAYS:>7.1f} "
            f"{s.in_market:>5.1%} {s.turnover_per_year:>8.2f}"
        )


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
    market, rf = ff["mkt_rf"] + ff["rf"], ff["rf"]
    ov1 = build_frame(close, market, rf)
    target = vol_target(market, ov1.index)
    frame = _vm_frame(close, market, rf, target)
    if not frame.index.equals(ov1.index):
        raise SystemExit("VM frame does not span OV-1's panel exactly")
    arms = arm_returns(frame, bps=PRIMARY_BPS, positions=POSITIONS)

    print("=" * 100)
    print(
        "VM — volatility-managed exposure as an increment over OV-1, French total-return frame"
    )
    print("=" * 100)
    print(f"  French file: {source}")
    print(
        f"  PANEL: {frame.index[0].date()} → {frame.index[-1].date()}  "
        f"{len(frame):,} sessions ({calendar_years(frame.index):.1f} calendar years), = OV-1's"
    )
    print(
        f"  σ_target = median σ̂_{VOL_WINDOW}d over the panel = {target:.5f}/session "
        f"({target * np.sqrt(TRADING_DAYS):.4f} annualized)"
    )
    pre = market.loc[pd.Timestamp("1929-01-01") : pd.Timestamp("1951-12-31")]
    sat = pd.DatetimeIndex(pre.index).dayofweek == 5
    print(
        f"  pre-1952 Saturday/weekday return-vol ratio: "
        f"{float(pre[sat].std() / pre[~sat].std()):.3f} ({int(sat.sum()):,} Saturdays)"
    )
    print(
        f"  VM weight: mean {frame['w'].mean():.3f}, below the cap on "
        f"{float((frame['w'] < 1.0).mean()):.1%} of sessions"
    )
    print(
        f"  bootstrap: stationary, mean block {BLOCK}, {N_BOOT:,} resamples, seed {SEED}; "
        f"cost {PRIMARY_BPS:g} bps per unit |Δpos| ({REPORT_BPS:g} reported)"
    )
    print()

    print("-- PRECISION (printed before any point estimate) " + "-" * 50)
    for label, base, arm in TESTS:
        d_ui, d_sr = bootstrap_legs(arms, base=base, arm=arm)
        print(
            f"  {label:<22} ΔUI half-width {_half(d_ui.lo, d_ui.hi):.3f}  "
            f"ΔSR half-width {_half(d_sr.lo, d_sr.hi):.4f}"
        )
    if args.precheck:
        return 0
    print()

    print(f"-- GATES @ {PRIMARY_BPS:g} bps " + "-" * 80)
    print(
        f"  legs: ΔUI CI hi < 0 · UI ratio <= {UI_RATIO_BAR} · "
        f"ΔSR CI lo > -{SHARPE_MARGIN}; the last row is reported, not gated"
    )
    headline = evaluate_overlay(arms, base="ov", arm="ovvm")
    _gates(arms)
    print(f"  HEADLINE VERDICT (increment ovvm - ov): {headline.verdict}")
    print()

    print("-- ARMS (reported, not gated) " + "-" * 70)
    _print_arms(arms, PRIMARY_BPS)
    arms5 = arm_returns(frame, bps=REPORT_BPS, positions=POSITIONS)
    _print_arms(arms5, REPORT_BPS)
    for arm in ("ov", "vm", "ovvm"):
        att = beta_attribution(arms, arm)
        print(
            f"  {arm} on bh (excess): beta {att.beta:+.3f}  alpha {att.alpha_annual:+.2%}/yr  "
            f"t {att.alpha_t:+.2f}  — reported, not the yardstick"
        )
    print()

    print(
        f"-- P(drawdown >= D within 5 years), {N_BOOT:,} paired paths of "
        f"{BREACH_HORIZON} sessions " + "-" * 18
    )
    print("  " + "D".ljust(6) + "".join(f"{d:>8.0%}" for d in BREACH_DEPTHS))
    for arm in ARMS:
        curve = drawdown_breach_curve(
            arms[arm].to_numpy(),
            BREACH_DEPTHS,
            horizon=BREACH_HORIZON,
            n_paths=N_BOOT,
            block=BLOCK,
            seed=SEED,
        )
        print("  " + arm.ljust(6) + "".join(f"{curve[d]:>8.1%}" for d in BREACH_DEPTHS))
    print()

    print("-- SENSITIVITY (reported; none can overturn the primary) " + "-" * 42)
    _gates(arms5, f"{REPORT_BPS:g}bps ")
    lag2 = _vm_frame(close, market, rf, target, lag=2)
    _gates(arm_returns(lag2, bps=PRIMARY_BPS, positions=POSITIONS), "lag2 ")
    realtime = realized_vol(market).expanding().median()
    rt = _vm_frame(close, market, rf, realtime)
    _gates(arm_returns(rt, bps=PRIMARY_BPS, positions=POSITIONS), "rt-target ")
    half = len(arms) // 2
    for label, sub in (
        ("first half", arms.iloc[:half]),
        ("second half", arms.iloc[half:]),
    ):
        cells = []
        for _, base, arm in (*TESTS, REPORTED):
            dui = ulcer_index(sub[arm].to_numpy()) - ulcer_index(sub[base].to_numpy())
            r = sub["rf"].to_numpy()
            dsr = excess_sharpe(sub[arm].to_numpy(), r) - excess_sharpe(
                sub[base].to_numpy(), r
            )
            cells.append(f"{arm}-{base} ΔUI {dui:+6.2f} ΔSR {dsr:+6.3f}")
        print(
            f"  {label:<11} {sub.index[0].date()} → {sub.index[-1].date()}  "
            + "  ".join(cells)
            + "  (sign only)"
        )
    if not args.no_spy:
        from utils.yfinance_client import fetch_total_return_close

        spy = fetch_total_return_close("SPY")
        spy.index = pd.DatetimeIndex(spy.index).normalize()
        spy_mkt = spy.loc[spy.index <= ff.index[-1]].pct_change().dropna()
        spy_mkt = spy_mkt.loc[spy_mkt.index.isin(ff.index)]
        sf = _vm_frame(close, spy_mkt, rf, target, start=SPY_START)
        spy_arms = arm_returns(sf, bps=PRIMARY_BPS, positions=POSITIONS)
        _gates(spy_arms, "SPY ")
        print(
            f"  SPY panel {sf.index[0].date()} → {sf.index[-1].date()} ({len(sf):,} sessions); "
            f"σ_target is the primary's; VM weight mean {sf['w'].mean():.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
