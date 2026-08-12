"""EWMAC trend sleeve audit (P2, equity port) — read-only G2 verdict.

Runs the continuous multi-speed EWMAC trend forecast across the N3 breadth
universe through the portfolio-vol-targeted paper book and prints the gate-G2
read: portfolio Sharpe/Sortino/max-DD with DSR/PBO/bootstrap-CI/MinTRL stamps, a
cost-sensitivity sweep, a breadth (universe vs majors-only) contrast, and the
per-speed Sharpes (the H2 cycle-bias check).

Equity adaptation: 252-session annualization (via ``ForecastConfig``), zero
funding (equities have none), and the breadth universe / majors come from the
N3 research universe rather than the crypto symbol list. Read-only — no writes,
no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/forecast_audit.py
    PYTHONPATH=. poetry run python tools/forecast_audit.py --majors AAPL,MSFT,NVDA
    PYTHONPATH=. poetry run python tools/forecast_audit.py --weight-study
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import (
    replay_trials,
    replay_universe,
    replay_weight_schemes,
)
from analytics.forecast.report import evaluate
from analytics.forecast.weights import candidate_schemes
from analytics.research_guards import passes_gate
from analytics.store import DEFAULT_DB_PATH
from utils.config_validation import load_research_universe

_MAJORS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]


def build_g2_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
) -> dict[str, object]:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    result = replay_universe(conn, cfg, symbols=symbols)
    trials = replay_trials(conn, cfg, symbols=symbols)
    rep = evaluate(result, cfg, trial_returns=trials)
    return {
        "label": label,
        "n_inst": len(result.per_instrument_net),
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "sortino": rep.sortino_annual,
        "max_dd": rep.max_dd,
        "ann_ret": rep.annual_return,
        "ann_vol": rep.annual_vol,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
    }


def _ann_sharpe_of(r: np.ndarray, ann: float) -> float:
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    return (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0


def build_weight_study(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> pd.DataFrame:
    """Per-scheme universe Sharpe + two-family DSR/PBO stamps + gate verdict.

    DSR deflates over {all schemes} + {per-speed singles}; PBO/CSCV over the
    schemes only (the configs we select among). Clears the bar iff
    DSR >= 0.95 and PBO <= 0.5 and boot_lo > 0.
    """
    cfg = ForecastConfig()
    ann = math.sqrt(cfg.annualization_days)
    schemes = candidate_schemes(cfg)

    results = replay_weight_schemes(conn, cfg, symbols=symbols)
    scheme_returns = {name: res.portfolio_return for name, res in results.items()}
    singles = {
        k: v
        for k, v in replay_trials(conn, cfg, symbols=symbols).items()
        if k != "combined"
    }
    dsr_family = {**scheme_returns, **singles}
    pbo_family = scheme_returns

    rows: list[dict[str, object]] = []
    for name, res in results.items():
        rep = evaluate(res, cfg, trial_returns=dsr_family, pbo_returns=pbo_family)
        clears = passes_gate(rep.dsr, rep.pbo, rep.boot_lo)
        rows.append(
            {
                "scheme": name,
                "a_priori": schemes[name].a_priori,
                "sharpe": _ann_sharpe_of(res.portfolio_return, ann),
                "dsr": rep.dsr,
                "pbo": rep.pbo,
                "boot_lo": rep.boot_lo,
                "min_trl": rep.min_trl,
                "clears": clears,
            }
        )
    df = (
        pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    )
    df["rank"] = range(1, len(df) + 1)
    return df


def _per_speed_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ForecastConfig()
    trials = replay_trials(conn, cfg, symbols=symbols)
    ann = math.sqrt(cfg.annualization_days)
    rows = []
    for name, r in trials.items():
        sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        sr = (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0
        rows.append({"trial": name, "sharpe": sr})
    return pd.DataFrame(rows)


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--majors",
        type=str,
        default=",".join(_MAJORS),
        help="comma-separated majors-only contrast set",
    )
    parser.add_argument(
        "--weight-study",
        action="store_true",
        help="run the forecast-weight study (DSR/PBO-gated) and exit",
    )
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    universe = load_research_universe().stocks()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    if args.weight_study:
        df = build_weight_study(conn, universe)
        _print_df("Forecast-weight study (universe)", df)
        winners = df[df["clears"]]
        if winners.empty:
            print(
                "\nVERDICT: no scheme clears DSR>=0.95 & PBO<=0.5 & boot_lo>0 -> "
                "equal-weight stays; the lift is in-sample. Breadth (P3) remains "
                "the binding constraint."
            )
        else:
            ap = winners[winners["a_priori"]]
            kind = "a-priori" if not ap.empty else "data-snooped only"
            best = (ap if not ap.empty else winners).iloc[0]
            print(
                f"\nVERDICT: {len(winners)} scheme(s) clear the bar ({kind}); "
                f"best = {best['scheme']} (Sharpe {best['sharpe']:+.3f}, "
                f"DSR {best['dsr']:.3f}, PBO {best['pbo']:.3f}). "
                "If a-priori -> re-test G2 with these weights; if snooped-only -> "
                "suggestive, needs OOS confirmation before shipping."
            )
        return

    rows = [
        build_g2_report_row(conn, "universe @2bps", universe, 2.0),
        build_g2_report_row(conn, "majors @2bps", majors, 2.0),
    ]
    _print_df("Gate G2 — breadth contrast", pd.DataFrame(rows))

    sweep = [
        build_g2_report_row(conn, f"universe @{b:g}bps", universe, b)
        for b in (0.0, 2.0, 8.0, 16.0)
    ]
    _print_df("Cost sensitivity (universe)", pd.DataFrame(sweep))

    _print_df(
        "Per-speed Sharpe (H2: s64_256 vs combined)", _per_speed_sharpes(conn, universe)
    )

    print(
        "\nG2 = trend-sleeve OOS Sharpe >= ~1 on the universe, costs in, "
        "DSR/PBO-gated. Read boot_lo (annualised Sharpe CI lower bound) and pbo "
        "alongside the headline before calling PASS/MARGINAL/FAIL. Equity port: "
        "252-session annualization, funding=0 (short-borrow deferred to v2)."
    )


if __name__ == "__main__":
    main()
