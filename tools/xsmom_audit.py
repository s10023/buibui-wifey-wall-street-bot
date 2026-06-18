"""Cross-sectional momentum sleeve audit (P3, equity) — read-only verdict.

Replays the demeaned-EWMAC relative-strength book across the breadth universe (1d)
and prints: a breadth contrast (universe vs majors-only), a dollar-neutral gate, a
beta-attribution table (alt-market + SPY proxy), a forward-persistence table, a
cost-sensitivity sweep, the per-speed XS Sharpes, and the diversification read
(correlation to the trend sleeve) — each with DSR/PBO/bootstrap-CI/MinTRL stamps.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_audit.py
    PYTHONPATH=. poetry run python tools/xsmom_audit.py --majors AAPL,MSFT,NVDA
    PYTHONPATH=. poetry run python tools/xsmom_audit.py --market-proxy QQQ
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast import ForecastConfig, replay_universe
from analytics.forecast.replay import load_daily_inputs
from analytics.store import DEFAULT_DB_PATH
from analytics.xsmom import (
    beta_attribution,
    equal_weight_market_return,
    evaluate_xs,
    replay_xs,
    replay_xs_trials,
    run_xs_backtest,
    subperiod_sharpe,
)
from utils.config_validation import load_research_universe

_MAJORS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
_MARKET_PROXY = "SPY"


def build_xs_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
    xs_dollar_neutral: bool = False,
) -> dict[str, object]:
    cfg = dataclasses.replace(
        ForecastConfig(),
        slippage_pct=slippage_bps / 10_000.0,
        xs_dollar_neutral=xs_dollar_neutral,
    )
    result = replay_xs(conn, cfg, symbols=symbols)
    trials = replay_xs_trials(conn, cfg, symbols=symbols)
    trend = replay_universe(conn, cfg, symbols=symbols).portfolio_return
    rep = evaluate_xs(result, cfg, trial_returns=trials, trend_returns=trend)
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
        "corr_to_trend": rep.corr_to_trend,
        "trend_sharpe": rep.trend_sharpe,
    }


def _per_speed_xs_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ForecastConfig()
    ann = math.sqrt(cfg.annualization_days)
    trials = replay_xs_trials(conn, cfg, symbols=symbols)
    rows = []
    for name, r in trials.items():
        sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        sr = (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0
        rows.append({"trial": name, "sharpe": sr})
    return pd.DataFrame(rows)


def _beta_attribution_table(
    conn: duckdb.DuckDBPyConnection, symbols: list[str], market_proxy: str
) -> pd.DataFrame:
    closes, fundings = load_daily_inputs(conn, symbols)
    mkt = equal_weight_market_return(closes)
    union = mkt.index
    mkt_arr: npt.NDArray[np.float64] = np.asarray(mkt.to_numpy(), dtype=np.float64)
    # Index-ETF market proxy (SPY by default), loaded separately. It is an ETF, not
    # in the .stocks() cross-section, so it never enters the demean.
    proxy_closes, _ = load_daily_inputs(conn, [market_proxy])
    proxy_arr: npt.NDArray[np.float64] | None = (
        np.asarray(
            proxy_closes[market_proxy].pct_change().reindex(union).to_numpy(),
            dtype=np.float64,
        )
        if market_proxy in proxy_closes
        else None
    )
    rows: list[dict[str, object]] = []
    for label, neutral in (("original", False), ("dollar-neutral", True)):
        cfg = dataclasses.replace(
            ForecastConfig(), slippage_pct=0.0002, xs_dollar_neutral=neutral
        )
        r = run_xs_backtest(closes, fundings, cfg).portfolio_return
        proxies: list[tuple[str, npt.NDArray[np.float64]]] = [("eq-wt-mkt", mkt_arr)]
        if proxy_arr is not None:
            proxies.append((market_proxy, proxy_arr))
        for proxy_name, proxy in proxies:
            ba = beta_attribution(r, proxy, ann_days=cfg.annualization_days)
            rows.append(
                {
                    "book": label,
                    "proxy": proxy_name,
                    "alpha_ann": ba.alpha_annual,
                    "beta": ba.beta,
                    "alpha_t": ba.alpha_tstat,
                    "hedged_sharpe": ba.beta_hedged_sharpe,
                    "r2": ba.r_squared,
                }
            )
    return pd.DataFrame(rows)


def _persistence_table(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    closes, fundings = load_daily_inputs(conn, symbols)
    cfg = dataclasses.replace(
        ForecastConfig(), slippage_pct=0.0002, xs_dollar_neutral=True
    )
    res = run_xs_backtest(closes, fundings, cfg)
    pr = subperiod_sharpe(
        res.portfolio_return, res.daily_index, ann_days=cfg.annualization_days
    )
    rows: list[dict[str, object]] = [
        {"period": str(year), "sharpe": sr} for year, sr in sorted(pr.by_year.items())
    ]
    rows.append({"period": "trailing_2y", "sharpe": pr.trailing_2y})
    rows.append({"period": "trailing_1y", "sharpe": pr.trailing_1y})
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
        "--market-proxy",
        type=str,
        default=_MARKET_PROXY,
        help="index-ETF beta proxy (loaded separately; excluded from the demean)",
    )
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    universe = load_research_universe().stocks()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]
    proxy = args.market_proxy.strip().upper()

    rows = [
        build_xs_report_row(conn, "universe @2bps", universe, 2.0),
        build_xs_report_row(conn, "majors @2bps", majors, 2.0),
    ]
    _print_df("Gate G3 — XS breadth contrast", pd.DataFrame(rows))

    neutral_rows = [
        build_xs_report_row(conn, "universe original @2bps", universe, 2.0, False),
        build_xs_report_row(conn, "universe neutral @2bps", universe, 2.0, True),
    ]
    _print_df("Dollar-neutral gate (original vs neutral)", pd.DataFrame(neutral_rows))
    _print_df(
        "Beta attribution (universe @2bps)",
        _beta_attribution_table(conn, universe, proxy),
    )
    _print_df(
        "Forward persistence (neutral, universe @2bps)",
        _persistence_table(conn, universe),
    )

    sweep = [
        build_xs_report_row(conn, f"universe @{b:g}bps", universe, b)
        for b in (0.0, 2.0, 8.0, 16.0)
    ]
    _print_df("Cost sensitivity (universe)", pd.DataFrame(sweep))

    _print_df("Per-speed XS Sharpe", _per_speed_xs_sharpes(conn, universe))

    print(
        "\nG3 read: is the XS sleeve positive, cost-robust, DSR/PBO-survivable, AND "
        "low-correlated to trend (corr_to_trend near 0)? A modest-Sharpe XS sleeve "
        "uncorrelated with trend is a real combine win (P3 IDM layer). Read "
        "corr_to_trend + boot_lo + pbo alongside the headline before calling it. "
        "Equity caveat: short-borrow cost is omitted (D2) — mildly optimistic on "
        "the short legs."
    )


if __name__ == "__main__":
    main()
