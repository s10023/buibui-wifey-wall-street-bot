"""Edge-hunt #6 — velocity-alternation audit (read-only verdict).

Runs the pre-registered four-arm family over the research universe (1d) and
prints, for each cost tier, every cell's headline + DSR/PBO/boot-CI + realized
portfolio beta + correlation to the two decomposition controls + gross turnover,
then PASS/FAIL and deploy-grade on the committed ``broad_ls`` cell. No writes.

READ THE COLUMNS IN THIS ORDER, and the reasons are three prior sleeves:

1. ``corr_depth`` / ``corr_duration`` — ``velocity`` IS ``depth / duration``, so a
   thesis arm that tracks either control found a component, not the ratio. This
   is the ``corr_to_trend`` +0.62 read that sank xsmom.
2. ``turnover`` — edge-hunt #5 booked ~211x daily gross, where 1bp of fee alone
   costs ~0.9 Sharpe. A Sharpe with no turnover beside it is not tradeability.
3. ``realized_beta`` — the L/S cells are beta-neutralized, so a large one means
   the construction failed its own precondition (``lowvol`` / ``pead``) and that
   cell is not evidence about the thesis either way. When it fires, read
   ``hedged_sharpe`` and ``alpha_t`` INSTEAD of ``sharpe``: a book left short the
   market bleeds in a rising one, which looks exactly like a refuted signal.
4. ...only then ``sharpe``.

The ``gross`` tier is ``fee_pct = 0``, and that is the point. Every sibling
sleeve's audit prints a "0 bps" column that is not cost-free: ``run_xs_backtest``
charges ``cfg.fee_pct + cfg.slippage_pct`` and ``fee_pct`` defaults to 1bp, so
zeroing slippage still bills a basis point. Only a true zero-cost row can
separate "the signal is absent" from "the costs ate it".

Usage::

    PYTHONPATH=. poetry run python tools/velocity_audit.py
    PYTHONPATH=. poetry run python tools/velocity_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import duckdb
import pandas as pd

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.forecast.config import ForecastConfig
from analytics.gapfill.replay import load_daily_ohlc
from analytics.store import DEFAULT_DB_PATH
from analytics.velocity.replay import replay_velocity_grid, velocity_market_return
from analytics.velocity.report import VelocityGridReport, evaluate_velocity_grid
from analytics.velocity.signals import (
    LOOKBACK,
    MIN_DEPTH,
    MIN_DURATION,
    eligible_count,
)
from utils.config_validation import load_research_universe


def build_grid(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    slippage_bps: float,
    fee_bps: float,
) -> VelocityGridReport:
    cfg = dataclasses.replace(
        ForecastConfig(),
        slippage_pct=slippage_bps / 10_000.0,
        fee_pct=fee_bps / 10_000.0,
    )
    books, leverages = replay_velocity_grid(conn, cfg, symbols)
    mkt = velocity_market_return(conn, symbols)
    return evaluate_velocity_grid(books, leverages, cfg, mkt)


def _grid_frame(rep: VelocityGridReport) -> pd.DataFrame:
    rows = []
    for key, c in rep.cells.items():
        a = rep.attribution[key]
        ctrl = rep.corr_to_controls[key]
        rows.append(
            {
                "cell": key,
                "days": c.n_obs,
                "sharpe": c.sharpe_annual,
                "dsr": c.dsr,
                "pbo": c.pbo,
                "boot_lo": c.boot_lo,
                "realized_beta": a.beta,
                "hedged_sharpe": a.beta_hedged_sharpe,
                "alpha_t": a.alpha_tstat,
                "corr_depth": ctrl.get("depth_control", float("nan")),
                "corr_duration": ctrl.get("duration_control", float("nan")),
                "turnover": rep.gross_turnover[key],
            }
        )
    return pd.DataFrame(rows)


def _population_frame(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    """Descriptive tradeable population — COUNTS, deliberately not a rate.

    A rate over a horizon needs a matched null before it means anything (#198's
    90.3% gap-fill headline was +1.5pp over a placebo). Counts need no null, so
    this table quotes counts and says nothing about how often a decline recovers.
    """
    bars, _, _ = load_daily_ohlc(conn, symbols)
    if not bars:
        return pd.DataFrame()
    counts = eligible_count(bars)
    live = counts[counts > 0]
    return pd.DataFrame(
        [
            {
                "symbols": len(bars),
                "sessions": int(len(counts)),
                "sessions_with_book": int((counts >= 2).sum()),
                "eligible_name_days": int(counts.sum()),
                "median_names_per_session": float(live.median()) if len(live) else 0.0,
                "max_names_per_session": int(counts.max()) if len(counts) else 0,
            }
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    syms = load_research_universe().stocks()
    print(f"universe={len(syms)} stocks")
    print(
        f"pre-registered: lookback {LOOKBACK} sessions, depth >= {MIN_DEPTH:.0%}, "
        f"duration >= {MIN_DURATION} sessions; committed cell broad_ls"
    )

    print("\n=== tradeable population (counts — a rate would need a matched null) ===")
    pop = _population_frame(conn, syms)
    print(
        "(empty)"
        if pop.empty
        else pop.to_string(index=False, float_format=lambda x: f"{x:.1f}")
    )

    # (label, fee_bps, slippage_bps). The `gross` row is the only genuinely
    # cost-free one — see the module docstring.
    tiers = [
        ("gross (fee=0, slip=0)", 0.0, 0.0),
        (f"live ({args.slippage_bps:g}bps slip + 1bp fee)", 1.0, args.slippage_bps),
        ("stressed (8bps slip + 1bp fee)", 1.0, 8.0),
    ]
    for label, fee_bps, slip_bps in tiers:
        rep = build_grid(conn, symbols=syms, slippage_bps=slip_bps, fee_bps=fee_bps)
        print(f"\n=== velocity-alternation family @ {label} ===")
        print(
            _grid_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.3f}")
        )
        verdict = "PASS" if rep.passed else "FAIL"
        deploy = " (DEPLOY-GRADE)" if rep.deploy_grade else ""
        print(f"committed cell ({rep.committed_key}) @ {label}: {verdict}{deploy}")

    print(
        "\nGate (pre-registered, broad_ls): DSR>=0.95 ∧ PBO<=0.5 ∧ boot_lo>0 ∧ "
        "Sharpe>=0.7; deploy-grade tier = Sharpe>=1.0 ∧ long-only leg>=0.7. "
        "MinTRL is not a leg (#166). `long_only` is reported for family "
        "consistency and is NOT evidence — run_xs_backtest SUMS legs, which makes "
        "the long-only arm a construction artifact in every sleeve (#198). "
        "realized_beta ~0 confirms the neutralization held. corr_depth / "
        "corr_duration near 1 mean the ratio added nothing over a component that "
        "was already known. Short-borrow cost omitted (mildly optimistic shorts)."
    )


if __name__ == "__main__":
    main()
