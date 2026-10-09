"""yfinance helper module.

No credentials, no module-level side effects. Wraps ``yfinance.Ticker.history()``
and normalises the returned DataFrame to the canonical OHLCV schema used by
the rest of the pipeline (lowercase columns; UTC-naive DatetimeIndex).

yfinance is an unofficial Yahoo Finance scraper. Yahoo may break or rate-limit
the underlying endpoints at any time — failures must be handled by callers.
"""

from __future__ import annotations

from typing import cast

import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]

YF_INTERVALS: dict[str, str] = {
    "1h": "60m",
    "1d": "1d",
    "1wk": "1wk",
}


def fetch_history(
    symbol: str,
    *,
    interval: str,
    period: str = "max",
    start: str | None = None,
) -> pd.DataFrame:
    """Fetch raw OHLCV bars for a single symbol.

    Args:
        symbol: Plain ticker, e.g. ``"AAPL"`` (no suffix).
        interval: One of ``YF_INTERVALS`` keys (``"1h"``, ``"1d"``, ``"1wk"``).
        period: yfinance period string (``"6mo"``, ``"2y"``, ``"max"``, ...).
            yfinance caps 1h period to 730 days regardless of this value.
        start: ISO date sent instead of ``period`` when given. It also sets the
            ``1wk`` anchor, which ``analytics.data_fetcher`` relies on (#323).

    Returns:
        DataFrame with columns ``open, high, low, close, volume`` and a
        UTC-naive DatetimeIndex. Empty DataFrame on no data.

    As-of adjustment convention (Phase 0.2 lookahead audit):
    ``auto_adjust=False`` preserves the raw print as ``close`` (absolute S/R
    levels need it). ``actions=False`` strips the Dividends/Stock-Splits columns.
    Dividends are **not** back-adjusted. **Splits are still back-applied** by
    yfinance to historical OHLC, so a split effective after a given bar is
    embedded into that bar's price — a mild, bounded as-of violation. On the
    liquid mega-cap universe splits are rare and this bias is small; eliminating
    it would require unadjusted data plus manual as-of corporate-action
    application, which is out of the free-data scope. See
    ``docs/redesign/phase0-lookahead-audit.md``.
    """
    yf_interval = YF_INTERVALS[interval]
    window = {"start": start} if start is not None else {"period": period}
    raw = cast(
        pd.DataFrame,
        yf.Ticker(symbol).history(
            **window,
            interval=yf_interval,
            auto_adjust=False,
            actions=False,
        ),
    )
    if raw.empty:
        return raw
    idx_utc = cast(pd.DatetimeIndex, raw.index).tz_convert("UTC").tz_localize(None)
    df = raw.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        },
    )[["open", "high", "low", "close", "volume"]]
    df.index = idx_utc
    return df


def fetch_total_return_close(symbol: str, *, period: str = "max") -> pd.Series:
    """Daily close adjusted for splits **and dividends**, as a total-return index.

    The one deliberate exception to ``fetch_history``'s ``auto_adjust=False``:
    a research frame that compares holding an ETF with holding cash needs the
    dividends the raw print strips. Never write it to ``ohlcv``, whose levels
    must stay raw (and whose split-seam guard in ``analytics/data_sync.py`` is
    tuned for unadjusted closes). Returns a UTC-naive-dated Series, empty on no
    data.
    """
    raw = cast(
        pd.DataFrame,
        yf.Ticker(symbol).history(
            period=period, interval="1d", auto_adjust=True, actions=False
        ),
    )
    if raw.empty:
        return pd.Series(dtype=float, name=symbol)
    idx = cast(pd.DatetimeIndex, raw.index).tz_convert("UTC").tz_localize(None)
    return pd.Series(raw["Close"].to_numpy(dtype=float), index=idx, name=symbol)
