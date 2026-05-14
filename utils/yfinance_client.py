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
) -> pd.DataFrame:
    """Fetch raw OHLCV bars for a single symbol.

    Args:
        symbol: Plain ticker, e.g. ``"AAPL"`` (no suffix).
        interval: One of ``YF_INTERVALS`` keys (``"1h"``, ``"1d"``, ``"1wk"``).
        period: yfinance period string (``"6mo"``, ``"2y"``, ``"max"``, ...).
            yfinance caps 1h period to 730 days regardless of this value.

    Returns:
        DataFrame with columns ``open, high, low, close, volume`` and a
        UTC-naive DatetimeIndex. Empty DataFrame on no data.

    ``auto_adjust=False`` preserves raw prices (S/R levels need absolute close).
    ``actions=False`` strips Dividends and Stock Splits columns. Split adjustment
    is applied by yfinance by default; dividend adjustment is not.
    """
    yf_interval = YF_INTERVALS[interval]
    raw = cast(
        pd.DataFrame,
        yf.Ticker(symbol).history(
            period=period,
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
