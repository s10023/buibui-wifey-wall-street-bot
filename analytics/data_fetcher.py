"""Pure data-fetching logic — yfinance to canonical OHLCV DataFrames.

4h bars are synthesised by resampling 1h bars anchored to 13:30 UTC
(US regular-session open). 1h and 1d pass through; 1wk is re-fetched onto Monday
stamps when Yahoo anchors it elsewhere (see ``_monday_anchored``).

No module-level side effects.
"""

from datetime import UTC, datetime
from typing import cast

import pandas as pd

from utils.yfinance_client import fetch_history

BARS_MAX_LIMIT: int = 5000


class NoProviderDataError(RuntimeError):
    """The provider returned no history at all for a symbol and interval.

    yfinance hides its own fetch exceptions by default (``hide_exceptions``): a
    DNS outage, a dropped connection and a delisted ticker each come back as one
    logged error and an empty frame. This error therefore says that a fetch
    produced nothing, never which of those causes it was. Only a caller that
    passes ``require_data=True`` to ``fetch_bars`` sees it.
    """


OHLCV_COLUMNS: list[str] = [
    "symbol",
    "timeframe",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

# Mapping from canonical interval to (yfinance_interval, default_period).
# yfinance caps history per interval — defaults below stay within those caps.
_INTERVAL_CONFIG: dict[str, tuple[str, str]] = {
    "1h": ("1h", "2y"),  # yf 1h caps at 730d
    "4h": ("1h", "2y"),  # synthesised by resampling 1h
    "1d": ("1d", "max"),  # unlimited
    "1wk": ("1wk", "max"),
}


def fetch_bars(
    symbol: str,
    interval: str,
    start_ms: int,
    limit: int = BARS_MAX_LIMIT,
    *,
    require_data: bool = False,
) -> pd.DataFrame:
    """Fetch up to ``limit`` bars at or after start_ms (Unix ms).

    Returns a DataFrame with columns matching OHLCV_COLUMNS.
    Returns an empty DataFrame (with correct columns) on no data.

    "No data" covers two states: the provider returned history but none of it
    is at or after ``start_ms``, or the provider returned nothing. The second
    is how yfinance reports a network failure, so ``require_data=True`` raises
    ``NoProviderDataError`` for it instead. yfinance raises a rate limit
    (``YFRateLimitError``) either way.
    """
    if interval not in _INTERVAL_CONFIG:
        raise ValueError(
            f"Unsupported interval '{interval}'. Supported: {list(_INTERVAL_CONFIG)}"
        )
    yf_interval, period = _INTERVAL_CONFIG[interval]
    raw = fetch_history(symbol, interval=yf_interval, period=period)
    if raw.empty:
        if require_data:
            raise NoProviderDataError(f"{symbol} {interval}: provider returned no data")
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    if interval == "4h":
        raw = _resample_to_4h(raw)
        if raw.empty:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
    if interval == "1wk":
        raw = _monday_anchored(symbol, raw)

    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=UTC).replace(tzinfo=None)
    raw = raw.loc[raw.index >= start_dt]
    # yfinance intermittently returns the current forming bar (or stale rows)
    # with NaN OHLCV. The ohlcv table is NOT NULL on every column, so drop any
    # such row before upsert — otherwise one bad row crashes the whole cycle.
    raw = raw.dropna(subset=["open", "high", "low", "close", "volume"])
    raw = raw.head(limit)
    if raw.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    return pd.DataFrame(
        {
            "symbol": symbol,
            "timeframe": interval,
            "open_time": raw.index.values.astype("datetime64[ms]").astype("int64"),
            "open": raw["open"].astype(float).values,
            "high": raw["high"].astype(float).values,
            "low": raw["low"].astype(float).values,
            "close": raw["close"].astype(float).values,
            "volume": raw["volume"].astype(float).values,
        }
    )


def _monday_anchored(symbol: str, weekly: pd.DataFrame) -> pd.DataFrame:
    """Return ``weekly`` re-fetched onto Monday stamps if Yahoo anchored it elsewhere.

    Under ``period="max"`` Yahoo anchors 1wk bars on the weekday of the symbol's
    first session, so a history that began on a Tuesday is Tuesday-stamped
    forever (ABBV, UNP, LUV, PEG; AEE on Thursday). A request starting on or
    after that session anchors on Monday, so re-ask from the first Monday after
    the first bar; only that partial listing week is lost. The two anchors bucket
    different days, so a stamp shift cannot repair it. Raises rather than store
    a mixed series if the re-fetch is still off-Monday (#323).
    """
    if weekly.empty or (cast(pd.DatetimeIndex, weekly.index).dayofweek == 0).all():
        return weekly
    first = cast(pd.Timestamp, weekly.index.min()).normalize()
    monday = first + pd.Timedelta(days=(7 - first.dayofweek) % 7)
    refetched = fetch_history(symbol, interval="1wk", start=monday.date().isoformat())
    if not (cast(pd.DatetimeIndex, refetched.index).dayofweek == 0).all():
        raise ValueError(
            f"{symbol} 1wk: off-Monday anchor persists after re-fetching from "
            f"{monday.date()}"
        )
    return refetched


def _resample_to_4h(hourly: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h bars to 4h, anchored to 13:30 UTC (US regular-session open).

    4h bins: 13:30-17:30, 17:30-21:30, 21:30-01:30, 01:30-05:30, 05:30-09:30, 09:30-13:30.
    The first three cover RTH + early after-hours; the latter three cover overnight.
    """
    return (
        hourly.resample("4h", origin="start_day", offset="13h30min")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
