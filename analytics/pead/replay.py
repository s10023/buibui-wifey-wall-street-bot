"""Read-only DuckDB front door for the PEAD-lite sleeve (edge-hunt #4).

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + all-zero equity
funding) for the price side and ``earnings_facts`` for the surprise side, builds
the seasonal-RW SUE panel once, and runs the pre-registered 2x2 grid through the
shared cost-aware ``run_xs_backtest``. The only module in ``analytics/pead/`` that
touches the DB; never writes.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.pead.signals import seasonal_sue, sue_leverage
from analytics.store.earnings import get_earnings_facts
from analytics.xsmom.book import XSBookResult, run_xs_backtest
from utils.config_validation import load_research_universe

_MARKET_PROXY = "SPY"
_SNAPSHOT = Path("config/universe_sp100_snapshot.json")


def _broad_symbols(min_history_days: int | None) -> list[str]:
    return load_research_universe(min_history_days=min_history_days).stocks()


def _mega_symbols(broad: list[str]) -> list[str]:
    """The pre-expansion S&P-100 stocks (stable mega arm) ∩ the active universe."""
    snap = (
        set(json.loads(_SNAPSHOT.read_text(encoding="utf-8")))
        if _SNAPSHOT.exists()
        else set()
    )
    mega = [s for s in broad if s in snap]
    return mega or broad  # fall back to broad if the snapshot is absent


def pead_market_return(conn: duckdb.DuckDBPyConnection) -> pd.Series:
    """SPY daily return — the equity-beta benchmark for the guardrail (read-only)."""
    closes, _ = load_daily_inputs(conn, [_MARKET_PROXY])
    spy = closes.get(_MARKET_PROXY)
    if spy is None:
        return pd.Series(dtype=float)
    return spy.pct_change()


def replay_pead_grid(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    window: int = 60,
    min_history_days: int | None = None,
) -> dict[str, XSBookResult]:
    """Run the pre-registered `{broad,mega} x {long-short,long-only}` 2x2.

    SUE is built once over the union of both arms (per-name, no cross-contamination)
    and sliced per cell by ``sue_leverage`` (it filters on the cell's ``closes``
    symbols). The gated cell is ``broad_ls``; the other three are diagnostic + feed
    the DSR/PBO trial count.
    """
    broad = _broad_symbols(min_history_days)
    mega = _mega_symbols(broad)
    facts = get_earnings_facts(conn, symbols=broad)
    sue = seasonal_sue(facts)

    books: dict[str, XSBookResult] = {}
    for arm, syms in (("broad", broad), ("mega", mega)):
        closes, fundings = load_daily_inputs(conn, syms)
        for tag, long_only in (("ls", False), ("long", True)):
            lev = sue_leverage(sue, closes, long_only=long_only, window=window, cfg=cfg)
            books[f"{arm}_{tag}"] = run_xs_backtest(closes, fundings, cfg, leverage=lev)
    return books
