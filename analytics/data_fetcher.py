"""Pure data-fetching logic — yfinance to canonical OHLCV DataFrames.

4h bars are synthesised by resampling 1h bars anchored to 13:30 UTC
(US regular-session open). Other intervals (1h, 1d, 1wk) pass through.

No module-level side effects.
"""

from datetime import UTC, datetime

import pandas as pd

from utils.yfinance_client import fetch_history

BARS_MAX_LIMIT: int = 5000

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
) -> pd.DataFrame:
    """Fetch up to ``limit`` bars at or after start_ms (Unix ms).

    Returns a DataFrame with columns matching OHLCV_COLUMNS.
    Returns an empty DataFrame (with correct columns) on no data.
    Raises on yfinance / network errors — callers decide whether to retry.
    """
    if interval not in _INTERVAL_CONFIG:
        raise ValueError(
            f"Unsupported interval '{interval}'. Supported: {list(_INTERVAL_CONFIG)}"
        )
    yf_interval, period = _INTERVAL_CONFIG[interval]
    raw = fetch_history(symbol, interval=yf_interval, period=period)
    if raw.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    if interval == "4h":
        raw = _resample_to_4h(raw)
        if raw.empty:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

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
