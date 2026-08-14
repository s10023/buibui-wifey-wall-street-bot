"""Edge-hunt #2 — low-beta / BAB 2x2 audit (read-only verdict).

Runs the pre-registered {beta,vol} x {beta-neutral L/S, long-only} grid over the
research universe (1d), prints each cell's headline + DSR/PBO/boot-CI/MinTRL +
realized portfolio beta, the cost-sensitivity sweep (0/2/8 bps), and the PASS/FAIL
+ deploy-grade flag on the committed `beta_neutral_ls` cell. No writes.

Usage::

    PYTHONPATH=. poetry run python tools/lowvol_audit.py
    PYTHONPATH=. poetry run python tools/lowvol_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.lowvol.replay import bab_market_return, replay_bab_grid
from analytics.lowvol.report import BabGridReport, evaluate_bab_grid
from analytics.store import DEFAULT_DB_PATH
from utils.config_validation import load_research_universe

_BETA_WINDOW = 252
_VOL_WINDOW = 252


def build_grid(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    slippage_bps: float,
    beta_window: int = _BETA_WINDOW,
    vol_window: int = _VOL_WINDOW,
) -> BabGridReport:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    books = replay_bab_grid(
        conn, cfg, symbols, beta_window=beta_window, vol_window=vol_window
    )
    mkt = bab_market_return(conn, symbols)
    return evaluate_bab_grid(books, cfg, mkt)


def _grid_frame(rep: BabGridReport) -> pd.DataFrame:
    rows = []
    for key, c in rep.cells.items():
        a = rep.attribution[key]
        rows.append(
            {
                "cell": key,
                "days": c.n_obs,
                "sharpe": c.sharpe_annual,
                "dsr": c.dsr,
                "pbo": c.pbo,
                "boot_lo": c.boot_lo,
                "min_trl": c.min_trl,
                "realized_beta": a.beta,
                "alpha_t": a.alpha_tstat,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    syms = load_research_universe().stocks()
    print(f"universe={len(syms)} stocks")

    for bps in (0.0, args.slippage_bps, 8.0):
        rep = build_grid(conn, symbols=syms, slippage_bps=bps)
        print(f"\n=== BAB 2x2 grid @ {bps:g} bps ===")
        print(
            _grid_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.3f}")
        )
        verdict = "PASS" if rep.passed else "FAIL"
        deploy = " (DEPLOY-GRADE)" if rep.deploy_grade else ""
        print(f"committed cell ({rep.committed_key}) @ {bps:g}bps: {verdict}{deploy}")

    print(
        "\nGate (pre-registered, beta x beta-neutral L/S): DSR>=0.95 ∧ PBO<=0.5 ∧ "
        "boot_lo>0 ∧ Sharpe>=0.7; deploy-grade tier = Sharpe>=1.0 ∧ "
        "long-only leg>=0.7. The beta-neutral construct defuses (does not fully "
        "eliminate) the survivorship bias of the current-S&P-500 set; realized_beta "
        "≈0 confirms the neutralization. Short-borrow cost omitted (mildly optimistic "
        "short legs)."
    )


if __name__ == "__main__":
    main()
