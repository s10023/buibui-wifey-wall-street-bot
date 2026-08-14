"""Read-only DuckDB front door for the gap-fill magnet sleeve (edge-hunt #5).

The only module in ``analytics/gapfill/`` that touches the DB; never writes.

Unlike the other equity sleeves this one needs full OHLC — a gap is
`open` vs the prior `close`, and a fill is a later `high`/`low` trading through
the edge — so ``load_daily_inputs`` (closes only) is not enough. ``load_daily_ohlc``
mirrors its index conventions exactly (UTC-normalized day index, last-wins on
duplicates, sorted, empty symbols skipped), and ``TestLoaderMatchesForecastLoader``
asserts the closes it returns are identical to that loader's, so the two cannot
drift into describing different populations.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.gapfill.signals import (
    cross_sectional_long_score,
    magnet_score,
    range_regime_mask,
    reversal_score,
)
from analytics.lowvol.signals import (
    beta_neutral_leverage,
    causal_betas,
    long_only_leverage,
)
from analytics.store.market_data import get_ohlcv
from analytics.xsmom.book import XSBookResult, run_xs_backtest
from analytics.xsmom.diagnostics import equal_weight_market_return
from utils.config_validation import load_research_universe

_FAR_PAST = 0
_FAR_FUTURE = 4_102_444_800_000  # 2100-01-01, matches forecast.replay

COMMITTED_KEY = "broad_ls"
"""The pre-registered gated cell. Declared here, before any result is read."""


def _universe_symbols(min_history_days: int | None) -> list[str]:
    uni = load_research_universe(min_history_days=min_history_days)
    return uni.stocks()  # active single-name stocks; ETFs excluded from the XS set


def load_daily_ohlc(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series], dict[str, pd.Series]]:
    """Return (bars, closes, fundings) per symbol on a normalized daily index.

    ``bars[sym]`` carries open/high/low/close. ``closes``/``fundings`` are the
    exact shapes ``run_xs_backtest`` expects; equities carry no funding, so
    fundings are all-zero. Symbols with no OHLCV are skipped.
    """
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for sym in symbols:
        raw = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        if raw.empty:
            continue
        idx = pd.to_datetime(raw["open_time"], unit="ms", utc=True).dt.normalize()
        frame = pd.DataFrame(
            {
                "open": raw["open"].to_numpy(dtype=np.float64),
                "high": raw["high"].to_numpy(dtype=np.float64),
                "low": raw["low"].to_numpy(dtype=np.float64),
                "close": raw["close"].to_numpy(dtype=np.float64),
            },
            index=pd.DatetimeIndex(idx),
        )
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        bars_by_symbol[sym] = frame
        closes[sym] = frame["close"]
        fundings[sym] = pd.Series(0.0, index=frame.index)
    return bars_by_symbol, closes, fundings


def gapfill_market_return(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.Series:
    """Equal-weight market daily return over the symbol set (read-only)."""
    _, closes, _ = load_daily_ohlc(conn, symbols)
    return equal_weight_market_return(closes)


def replay_gapfill_grid(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> dict[str, XSBookResult]:
    """Run the pre-registered four-arm family.

    * ``broad_ls`` — beta-neutral cross-sectional long/short on the magnet score.
      **The gated cell** (``COMMITTED_KEY``), declared before results are read.
    * ``broad_ls_range`` — the same book with positions zeroed outside the
      market range regime. The thesis' explicit conditioning clause, as ONE
      declared secondary cell rather than a swept dimension.
    * ``reversal_control`` — plain 1-session reversal, no gap logic. The confound
      arm: the magnet construction is mechanically a gap-fade, so a positive
      ``broad_ls`` that merely tracks this is a reversal result.
    * ``long_only`` — top-quintile long-only form, the deployable shape the other
      sleeves also report.

    All four are sized on actual closes and booked through the shared cost-aware
    ``run_xs_backtest``, so costs and the vol governor are identical across arms.
    The family is also the honest multiple-testing set for DSR/PBO.
    """
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    bars, closes, fundings = load_daily_ohlc(conn, syms)
    if not closes:
        return {}

    betas = causal_betas(closes)
    magnet_z = cross_sectional_long_score(magnet_score(bars, cfg))
    reversal_z = cross_sectional_long_score(reversal_score(bars, cfg))

    market = equal_weight_market_return(closes)
    in_range = range_regime_mask(market).reindex(magnet_z.index).fillna(False)
    range_z = magnet_z.where(in_range, 0.0)

    books: dict[str, XSBookResult] = {}
    books["broad_ls"] = run_xs_backtest(
        closes,
        fundings,
        cfg,
        leverage=beta_neutral_leverage(magnet_z, betas, closes, cfg),
    )
    books["broad_ls_range"] = run_xs_backtest(
        closes,
        fundings,
        cfg,
        leverage=beta_neutral_leverage(range_z, betas, closes, cfg),
    )
    books["reversal_control"] = run_xs_backtest(
        closes,
        fundings,
        cfg,
        leverage=beta_neutral_leverage(reversal_z, betas, closes, cfg),
    )
    books["long_only"] = run_xs_backtest(
        closes, fundings, cfg, leverage=long_only_leverage(magnet_z, closes, cfg)
    )
    return books
