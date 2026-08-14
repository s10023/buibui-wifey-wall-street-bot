"""Edge-hunt #3 — cross-asset TSMOM 2x2 audit (read-only verdict).

Runs the pre-registered {broad,commodity} x {long-short,long-flat} grid over the
frozen cross-asset ETF basket (1d), prints each cell's headline + DSR/PBO/boot-CI/
MinTRL + realized equity-beta to SPY, the cost-sensitivity sweep (0/2/8 bps), and
the PASS/FAIL + deploy-grade flag on the committed `broad_ls` cell. No writes.

Usage::

    PYTHONPATH=. poetry run python tools/xasset_audit.py
    PYTHONPATH=. poetry run python tools/xasset_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.xasset.replay import replay_xasset_grid, xasset_market_return
from analytics.xasset.report import XAssetGridReport, evaluate_xasset_grid
from analytics.xasset.universe import broad_symbols


def build_grid(
    conn: duckdb.DuckDBPyConnection,
    *,
    slippage_bps: float,
) -> XAssetGridReport:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    books = replay_xasset_grid(conn, cfg)
    mkt = xasset_market_return(conn)
    return evaluate_xasset_grid(books, cfg, mkt)


def _grid_frame(rep: XAssetGridReport) -> pd.DataFrame:
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
                "equity_beta": a.beta,
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
    print(f"basket={len(broad_symbols())} ETFs: {' '.join(broad_symbols())}")

    for bps in (0.0, args.slippage_bps, 8.0):
        rep = build_grid(conn, slippage_bps=bps)
        print(f"\n=== cross-asset TSMOM 2x2 grid @ {bps:g} bps ===")
        print(
            _grid_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.3f}")
        )
        verdict = "PASS" if rep.passed else "FAIL"
        deploy = " (DEPLOY-GRADE)" if rep.deploy_grade else ""
        print(f"committed cell ({rep.committed_key}) @ {bps:g}bps: {verdict}{deploy}")

    print(
        "\nGate (pre-registered, broad x long-short): DSR>=0.95 ∧ PBO<=0.5 ∧ "
        "boot_lo>0 ∧ Sharpe>=0.7; deploy-grade tier = Sharpe>=1.0 ∧ "
        "long-flat leg>=0.7. equity_beta is the realized beta to SPY — the "
        "committed cross-asset book should read ≈0 (the diversification thesis). "
        "USO contango + ETF tracking error are accepted free-data proxy "
        "imperfections; short-borrow cost on the L/S legs is omitted (mildly "
        "optimistic)."
    )


if __name__ == "__main__":
    main()
