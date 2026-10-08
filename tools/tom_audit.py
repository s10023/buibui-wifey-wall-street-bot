"""TOM — turn-of-the-month market exposure, judged on beta-hedged returns (#422).

Runs the frozen pre-registration in
``docs/superpowers/specs/2026-10-08-edge-pillars-research-design.md``
§ Amendment 2: ``tom`` holds the French market over the last session of each
month and the first three of the next and earns French ``RF`` otherwise, against
``bh``. The gate is the edge yardstick on the hedged series: Sharpe ≥ 0.7, DSR
≥ 0.95 at four trials, bootstrap lower bound > 0. Reads only the French file
(downloaded per run, or ``--french-zip``), never ``analytics.db``.

Run ``--precheck`` first. It prints the panels, the window's share of sessions
and the gated CI half-widths, and no point estimate.

Usage::

    PYTHONPATH=. poetry run python tools/tom_audit.py --precheck
    PYTHONPATH=. poetry run python tools/tom_audit.py --french-zip ff.zip
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.overlay.replay import arm_returns
from analytics.overlay.report import (
    TRADING_DAYS,
    beta_attribution,
    calendar_years,
    summarize_arm,
)
from analytics.research_guards import GATE_SHARPE
from analytics.tom import (
    N_TRIALS,
    PRIMARY_START,
    SECONDARY_START,
    TomGate,
    evaluate_tom,
    hedged_returns,
    tom_frame,
    window_spread,
)
from analytics.trading_calendar import nyse_sessions
from tools.overlay_audit import PRIMARY_BPS, REPORT_BPS, _half, _load_french

POSITIONS = {"tom": "pos"}


def _frame(ff: pd.DataFrame, start: pd.Timestamp, *, lag: int = 0) -> pd.DataFrame:
    return tom_frame(ff, start=start, sessions_fn=nyse_sessions, lag=lag)


def _arms(frame: pd.DataFrame, bps: float = PRIMARY_BPS) -> pd.DataFrame:
    return arm_returns(frame, bps=bps, positions=POSITIONS)


def _print_gate(label: str, gate: TomGate) -> None:
    ci = gate.ci
    print(
        f"  {label:<24} β {gate.beta:+.3f}  hedged SR {gate.hedged_sharpe:+.3f} "
        f"[{ci.lo:+.3f}, {ci.hi:+.3f}]  DSR {gate.dsr:.3f}  n={gate.n_obs:,}"
        f"  → {gate.verdict} (premise {gate.premise})"
    )


def _print_arms(arms: pd.DataFrame, bps: float) -> None:
    print(
        f"  {'arm':<4} {'bps':>4} {'ann.ret':>8} {'ann.vol':>8} {'Sharpe':>7} {'UI':>7} "
        f"{'maxDD':>7} {'TUW(y)':>7} {'in-mkt':>7} {'turn/yr':>8}"
    )
    for arm in ("bh", "tom"):
        s = summarize_arm(arms, arm, pos=POSITIONS.get(arm))
        print(
            f"  {arm:<4} {bps:>4.0f} {s.ann_return:>7.2%} {s.ann_vol:>8.4f} {s.sharpe:>7.3f} "
            f"{s.ulcer:>7.2f} {s.max_dd:>6.1%} {s.tuw_sessions / TRADING_DAYS:>7.1f} "
            f"{s.in_market:>6.1%} {s.turnover_per_year:>8.2f}"
        )


def _panel_line(name: str, frame: pd.DataFrame) -> None:
    print(
        f"  {name}: {frame.index[0].date()} → {frame.index[-1].date()}  "
        f"{len(frame):,} sessions, {calendar_years(frame.index):.2f} calendar years, "
        f"window share {frame['pos'].mean():.1%}"
    )


def precheck(ff: pd.DataFrame, source: str) -> None:
    primary, secondary = _frame(ff, PRIMARY_START), _frame(ff, SECONDARY_START)
    print(f"  French file: {source}")
    print(f"  file: {ff.index[0].date()} → {ff.index[-1].date()}")
    _panel_line("PRIMARY", primary)
    _panel_line("SECONDARY", secondary)
    per_month = (
        primary["pos"].groupby(pd.DatetimeIndex(primary.index).to_period("M")).sum()
    )
    odd = per_month[per_month != 4.0]
    print(f"  months with a window of other than 4 sessions: {len(odd)}")
    for name, frame in (("primary", primary), ("secondary", secondary)):
        h, _ = hedged_returns(_arms(frame))
        gate = evaluate_tom(_arms(frame))
        print(
            f"  {name} hedged-SR CI half-width {_half(gate.ci.lo, gate.ci.hi):.4f}"
            f"  (n={len(h):,}, trials {N_TRIALS}, bar {GATE_SHARPE})"
        )
    print("  (no point estimate printed in --precheck)")


def run(ff: pd.DataFrame, source: str) -> None:
    primary = _frame(ff, PRIMARY_START)
    arms = _arms(primary)
    print("=" * 100)
    print(
        "TOM — turn-of-the-month exposure, beta-hedged edge gate (spec § Amendment 2)"
    )
    print("=" * 100)
    print(f"  French file: {source}")
    _panel_line("PRIMARY", primary)

    print("\nGATE (primary, 2 bps)")
    gate = evaluate_tom(arms)
    _print_gate("primary", gate)
    attr = beta_attribution(arms, arm="tom")
    print(
        f"  attribution: β {attr.beta:+.3f}  alpha {attr.alpha_annual:+.2%}/yr"
        f"  t {attr.alpha_t:+.2f}"
    )

    print("\nARMS")
    _print_arms(arms, PRIMARY_BPS)
    _print_arms(_arms(primary, REPORT_BPS), REPORT_BPS)

    ws = window_spread(primary)
    print("\nWINDOW SPREAD (mean daily market excess return, primary)")
    print(
        f"  inside {ws.inside * 1e4:+.2f} bp   outside {ws.outside * 1e4:+.2f} bp   "
        f"diff {ws.diff.point * 1e4:+.2f} bp [{ws.diff.lo * 1e4:+.2f}, {ws.diff.hi * 1e4:+.2f}]"
    )

    print("\nSENSITIVITIES (reported, not gated)")
    _print_gate("5 bps", evaluate_tom(_arms(primary, REPORT_BPS)))
    _print_gate("1-session lag", evaluate_tom(_arms(_frame(ff, PRIMARY_START, lag=1))))
    mid = primary.index[len(primary) // 2]
    _print_gate("first half (sign)", evaluate_tom(_arms(primary.loc[:mid])))
    _print_gate("second half (sign)", evaluate_tom(_arms(primary.loc[mid:].iloc[1:])))
    secondary = _frame(ff, SECONDARY_START)
    _panel_line("SECONDARY", secondary)
    _print_gate("2006→ (sign)", evaluate_tom(_arms(secondary)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--french-zip", type=Path, default=None)
    parser.add_argument("--precheck", action="store_true")
    args = parser.parse_args()
    ff, source = _load_french(args.french_zip)
    if args.precheck:
        precheck(ff, source)
    else:
        run(ff, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
