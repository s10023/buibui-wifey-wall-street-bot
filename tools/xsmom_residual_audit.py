"""Experiment #1 — residualized XS-momentum 2x2 audit (read-only verdict).

Runs the pre-registered {mega,broad} x {raw, residual+skip} grid over the
research universe (1d), prints each cell's headline + DSR/PBO/boot-CI/MinTRL, the
cost-sensitivity sweep (0/2/8 bps), the long-only top-quintile leg, and the
PASS/FAIL on the committed broad x residual+skip cell. No writes.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_residual_audit.py
    PYTHONPATH=. poetry run python tools/xsmom_residual_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast import ForecastConfig, replay_universe
from analytics.forecast.replay import load_daily_inputs
from analytics.store import DEFAULT_DB_PATH
from analytics.xsmom import (
    evaluate_residual_grid,
    replay_residual_grid,
    run_xs_backtest,
)
from analytics.xsmom.replay import _sector_map
from analytics.xsmom.report import ResidualGridReport
from analytics.xsmom.residual import (
    _SLOW_SPEEDS_DEFAULT,
    long_only_residual_leverage,
)
from utils.config_validation import load_research_universe

_BETA_WINDOW = 252
_SNAPSHOT = Path("config/universe_sp100_snapshot.json")


def _mega_symbols(broad: list[str]) -> list[str]:
    """The pre-expansion S&P-100 stocks (the stable mega arm), intersected with
    the active universe so a missing backfill never crashes the audit."""
    snap = set(json.loads(_SNAPSHOT.read_text())) if _SNAPSHOT.exists() else set()
    mega = [s for s in broad if s in snap]
    return mega or broad  # fall back to broad if the snapshot is absent


def build_grid(
    conn: duckdb.DuckDBPyConnection,
    *,
    mega: list[str],
    broad: list[str],
    sector_map: dict[str, str],
    slippage_bps: float,
) -> ResidualGridReport:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    books = replay_residual_grid(
        conn, cfg, beta_window=_BETA_WINDOW, mega_symbols=mega,
        broad_symbols=broad, sector_map=sector_map,
    )
    trend = {
        "mega": replay_universe(conn, cfg, symbols=mega).portfolio_return,
        "broad": replay_universe(conn, cfg, symbols=broad).portfolio_return,
    }
    return evaluate_residual_grid(books, cfg, trend_by_universe=trend)


def _grid_frame(rep: ResidualGridReport) -> pd.DataFrame:
    rows = []
    for key, c in rep.cells.items():
        rows.append({
            "cell": key, "days": c.n_obs, "sharpe": c.sharpe_annual,
            "dsr": c.dsr, "pbo": c.pbo, "boot_lo": c.boot_lo,
            "min_trl": c.min_trl, "corr_to_trend": c.corr_to_trend,
        })
    return pd.DataFrame(rows)


def long_only_sharpe(
    conn: duckdb.DuckDBPyConnection,
    broad: list[str],
    sector_map: dict[str, str],
    slippage_bps: float,
) -> float:
    """Annualized Sharpe of the deployable long-only top-quintile residual book
    on the committed (broad, residual+skip) config."""
    cfg = dataclasses.replace(
        ForecastConfig(),
        slippage_pct=slippage_bps / 10_000.0,
        speeds=_SLOW_SPEEDS_DEFAULT,
    )
    closes, fundings = load_daily_inputs(conn, broad)
    sm = {s: sector_map[s] for s in broad if s in sector_map}
    lev = long_only_residual_leverage(
        closes, cfg, beta_window=_BETA_WINDOW, sector_map=sm, quantile=0.8
    )
    r = run_xs_backtest(closes, fundings, cfg, leverage=lev).portfolio_return
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r)) / sd * math.sqrt(cfg.annualization_days)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    broad = load_research_universe().stocks()
    sector_map = _sector_map()
    mega = _mega_symbols(broad)
    print(f"mega={len(mega)} broad={len(broad)} stocks")

    for bps in (0.0, args.slippage_bps, 8.0):
        rep = build_grid(
            conn, mega=mega, broad=broad, sector_map=sector_map, slippage_bps=bps,
        )
        print(f"\n=== 2x2 grid @ {bps:g} bps ===")
        print(_grid_frame(rep).to_string(index=False,
              float_format=lambda x: f"{x:+.3f}"))
        verdict = "PASS" if rep.passed else "FAIL"
        print(f"committed cell ({rep.committed_key}) @ {bps:g}bps: {verdict}")

    print("\n=== long-only top-quintile (broad, residual+skip) Sharpe ===")
    for bps in (0.0, args.slippage_bps, 8.0):
        print(f"  @ {bps:g}bps: {long_only_sharpe(conn, broad, sector_map, bps):+.3f}")

    print(
        "\nGate (pre-registered, broad x residual+skip): DSR>=0.95 ∧ PBO<=0.5 ∧ "
        "boot_lo>0 ∧ n>=MinTRL ∧ Sharpe>=0.7. Broad-arm numbers carry the "
        "survivorship flag — a marginal pass is suspect; a clean fail is "
        "trustworthy. Short-borrow cost omitted (mildly optimistic short legs)."
    )


if __name__ == "__main__":
    main()
