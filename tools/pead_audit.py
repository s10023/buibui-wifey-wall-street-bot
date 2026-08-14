"""Edge-hunt #4 — PEAD-lite 2x2 audit (read-only verdict).

Runs the pre-registered {broad,mega} x {long-short,long-only} grid over the
research universe (1d) on the free-EDGAR seasonal-RW SUE, prints each cell's
headline + DSR/PBO/boot-CI/MinTRL + realized equity-β to SPY, the 8-K-vs-10-Q
announcement-coverage diagnostic, the cost-sensitivity sweep (0/2/8 bps), and the
PASS/FAIL + deploy-grade flag on the committed `broad_ls` cell. No writes.

Usage::

    PYTHONPATH=. poetry run python tools/pead_audit.py
    PYTHONPATH=. poetry run python tools/pead_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.pead.replay import pead_market_return, replay_pead_grid
from analytics.pead.report import PeadGridReport, evaluate_pead_grid
from analytics.store import DEFAULT_DB_PATH

_DRIFT_WINDOW = 60  # pre-registered Bernard-Thomas drift horizon (sessions)


def build_grid(
    conn: duckdb.DuckDBPyConnection,
    *,
    slippage_bps: float,
    window: int = _DRIFT_WINDOW,
) -> PeadGridReport:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    books = replay_pead_grid(conn, cfg, window=window)
    spy = pead_market_return(conn)
    return evaluate_pead_grid(books, cfg, spy)


def _grid_frame(rep: PeadGridReport) -> pd.DataFrame:
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


def _coverage(conn: duckdb.DuckDBPyConnection) -> str:
    try:
        df = conn.execute(
            "SELECT source, COUNT(*) AS n FROM earnings_facts GROUP BY source"
        ).df()
    except duckdb.Error:
        return "earnings_facts: (absent — run `make wifey-pead-backfill` first)"
    if df.empty:
        return "earnings_facts: empty — run `make wifey-pead-backfill` first"
    total = int(df["n"].sum())
    by = {str(r["source"]): int(r["n"]) for _, r in df.iterrows()}
    n_8k = by.get("8k", 0)
    pct = 100.0 * n_8k / total if total else 0.0
    return (
        f"earnings_facts: {total} quarters, 8-K coverage {pct:.1f}% "
        f"({total - n_8k} on 10-Q fallback)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--window", type=int, default=_DRIFT_WINDOW)
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    print(_coverage(conn))
    print(f"drift window: {args.window} sessions (pre-registered)")

    for bps in (0.0, args.slippage_bps, 8.0):
        rep = build_grid(conn, slippage_bps=bps, window=args.window)
        print(f"\n=== PEAD 2x2 grid @ {bps:g} bps ===")
        print(
            _grid_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.3f}")
        )
        verdict = "PASS" if rep.passed else "FAIL"
        deploy = " (DEPLOY-GRADE)" if rep.deploy_grade else ""
        print(f"committed cell ({rep.committed_key}) @ {bps:g}bps: {verdict}{deploy}")

    print(
        "\nGate (pre-registered, broad x dollar-neutral L/S): DSR>=0.95 ∧ PBO<=0.5 ∧ "
        "boot_lo>0 ∧ Sharpe>=0.7; deploy-grade tier = Sharpe>=1.0 ∧ "
        "long-only leg>=0.7. SUE = seasonal random walk (EPS − same-fp prior year), "
        "entry next session after the 8-K item-2.02 announcement, 60-session drift. "
        "The current-S&P-500 universe is a deliberately hard (large-cap, efficient) "
        "PEAD test; realized_beta ≈0 confirms an event anomaly, not equity beta. "
        "Short-borrow cost omitted (mildly optimistic short legs)."
    )


if __name__ == "__main__":
    main()
