"""Edge-hunt #5 — gap-fill "magnet" audit (read-only verdict).

Runs the pre-registered four-arm family over the research universe (1d), prints
each cell's headline + DSR/PBO/boot-CI + realized portfolio beta + correlation to
the reversal control, the gap population it is drawn from, the cost-sensitivity
sweep (0/2/8 bps), and PASS/FAIL + deploy-grade on the committed ``broad_ls``
cell. No writes.

Read the ``corr_to_reversal`` column before the Sharpe column. The magnet
construction is mechanically a gap-fade, so a ``broad_ls`` that tracks
``reversal_control`` is a short-term-reversal result and the gap logic added
nothing — the same read as the xsmom sleeve's `corr_to_trend` +0.62.

Usage::

    PYTHONPATH=. poetry run python tools/gapfill_audit.py
    PYTHONPATH=. poetry run python tools/gapfill_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.vol import ew_return_vol
from analytics.gapfill.replay import (
    gapfill_market_return,
    load_daily_ohlc,
    replay_gapfill_grid,
)
from analytics.gapfill.report import GapfillGridReport, evaluate_gapfill_grid
from analytics.gapfill.signals import (
    FILL_HORIZONS,
    GAP_MAX_AGE,
    MAX_DIST_SIGMA,
    MIN_GAP_SIGMA,
    gap_fill_stats,
)
from analytics.store import DEFAULT_DB_PATH
from utils.config_validation import load_research_universe


def build_grid(
    conn: duckdb.DuckDBPyConnection, *, symbols: list[str], slippage_bps: float
) -> GapfillGridReport:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    books = replay_gapfill_grid(conn, cfg, symbols)
    mkt = gapfill_market_return(conn, symbols)
    return evaluate_gapfill_grid(books, cfg, mkt)


def _grid_frame(rep: GapfillGridReport) -> pd.DataFrame:
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
                "realized_beta": a.beta,
                "alpha_t": a.alpha_tstat,
                "corr_reversal": rep.corr_to_reversal[key],
            }
        )
    return pd.DataFrame(rows)


def _population_frame(
    conn: duckdb.DuckDBPyConnection, symbols: list[str], cfg: ForecastConfig
) -> pd.DataFrame:
    """Descriptive gap population — pooled over the universe. NOT an edge claim."""
    bars, _, _ = load_daily_ohlc(conn, symbols)
    totals: dict[str, float] = {"n_gaps": 0.0, "up": 0.0}
    hit: dict[int, float] = dict.fromkeys(FILL_HORIZONS, 0.0)
    for frame in bars.values():
        vol = ew_return_vol(frame["close"], cfg.vol_span).shift(1)
        s = gap_fill_stats(frame, vol)
        n = s["n_gaps"]
        if n <= 0:
            continue
        totals["n_gaps"] += n
        totals["up"] += s["up_share"] * n
        for h in FILL_HORIZONS:
            hit[h] += s[f"fill_{h}"] * n
    n = totals["n_gaps"]
    row = {
        "symbols": len(bars),
        "material_gaps": int(n),
        "up_share": totals["up"] / n if n else float("nan"),
    }
    for h in FILL_HORIZONS:
        row[f"filled_within_{h}d"] = hit[h] / n if n else float("nan")
    return pd.DataFrame([row])


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
        f"pre-registered: gap >= {MIN_GAP_SIGMA}σ, magnet range {MAX_DIST_SIGMA}σ, "
        f"gap life {GAP_MAX_AGE} sessions"
    )

    cfg = ForecastConfig()
    print("\n=== gap population (descriptive — a fill rate is NOT an edge) ===")
    print(
        _population_frame(conn, syms, cfg).to_string(
            index=False, float_format=lambda x: f"{x:.3f}"
        )
    )

    for bps in (0.0, args.slippage_bps, 8.0):
        rep = build_grid(conn, symbols=syms, slippage_bps=bps)
        print(f"\n=== gap-fill magnet family @ {bps:g} bps ===")
        print(
            _grid_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.3f}")
        )
        verdict = "PASS" if rep.passed else "FAIL"
        deploy = " (DEPLOY-GRADE)" if rep.deploy_grade else ""
        print(f"committed cell ({rep.committed_key}) @ {bps:g}bps: {verdict}{deploy}")

    print(
        "\nGate (pre-registered, broad_ls): DSR>=0.95 ∧ PBO<=0.5 ∧ boot_lo>0 ∧ "
        "Sharpe>=0.7; deploy-grade tier = Sharpe>=1.0 ∧ long-only leg>=0.7. "
        "MinTRL is a reported stamp, NOT a leg (#166). realized_beta ≈0 confirms "
        "the neutralization held — a large one means the construction failed its "
        "own precondition and that cell is not evidence about the gap premium. "
        "corr_reversal near 1 means the gap logic added nothing over plain "
        "short-term reversal. Short-borrow cost omitted (mildly optimistic shorts)."
    )


if __name__ == "__main__":
    main()
