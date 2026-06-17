"""Read-only DuckDB front door for the EWMAC trend sleeve (equity port).

Loads 1d OHLCV for the breadth universe and runs the forecast book. The only
module in ``analytics/forecast/`` that touches the database; never writes.
Equities have no funding, so the funding leg is always zero (kept in the return
shape for book-signature compatibility).
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.weights import candidate_schemes
from analytics.store.market_data import get_ohlcv
from utils.config_validation import load_research_universe

# Sentinels that cover any realistic data range (Unix ms).
_FAR_PAST: int = 0
_FAR_FUTURE: int = 9_999_999_999_999


def _universe_symbols(min_history_days: int | None) -> list[str]:
    uni = load_research_universe(min_history_days=min_history_days)
    return uni.stocks()  # active single-name stocks; ETFs excluded from the XS set


def load_daily_inputs(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """Return (closes, fundings) day-indexed Series per symbol.

    Equities carry no funding, so ``fundings[sym]`` is all-zeros aligned to the
    close index (kept so the book signature is unchanged). Symbols with no OHLCV
    are skipped.
    """
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        if bars.empty:
            continue
        idx = pd.to_datetime(bars["open_time"], unit="ms", utc=True).dt.normalize()
        close = pd.Series(bars["close"].to_numpy(dtype=float), index=idx)
        close = close[~close.index.duplicated(keep="last")].sort_index()
        closes[sym] = close
        fundings[sym] = pd.Series(0.0, index=close.index)
    return closes, fundings


def replay_universe(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> ForecastBookResult:
    """Load the universe's 1d inputs and run the forecast book (read-only)."""
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)
    return run_forecast_backtest(closes, fundings, cfg)


def replay_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> dict[str, np.ndarray]:
    """Daily portfolio returns for each single-speed sleeve + the combined book.

    The honest multiple-testing family for DSR/PBO and the H2 check
    (s64_256 vs combined). Reuses ``run_forecast_backtest`` by swapping
    ``cfg.speeds`` to a single pair per trial.

    Keys:
    - ``"s{fast}_{slow}"`` — one per speed in ``cfg.speeds``
    - ``"combined"`` — the full multi-speed book
    """
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for fast, slow, scalar in cfg.speeds:
        single_cfg = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
        result = run_forecast_backtest(closes, fundings, single_cfg)
        trials[f"s{fast}_{slow}"] = result.portfolio_return
    combined = run_forecast_backtest(closes, fundings, cfg)
    trials["combined"] = combined.portfolio_return
    return trials


def replay_weight_schemes(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> dict[str, ForecastBookResult]:
    """Run the universe book once per candidate weight scheme (read-only).

    Keys are scheme names from ``candidate_schemes``; values are the full
    ``ForecastBookResult`` under that scheme's weights. Loads the daily inputs
    once and re-runs the book per scheme via ``dataclasses.replace``.
    """
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)
    out: dict[str, ForecastBookResult] = {}
    for name, scheme in candidate_schemes(cfg).items():
        scheme_cfg = dataclasses.replace(cfg, weights=scheme.weights)
        out[name] = run_forecast_backtest(closes, fundings, scheme_cfg)
    return out
