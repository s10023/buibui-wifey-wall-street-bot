"""Signal package common helpers — constants, in-memory backtest cache, time helpers.

The `_bt_mem_cache` dict is defined exactly once here. Every consumer must
`from analytics.signal._common import _bt_mem_cache` and mutate in place
(`.clear()`, `[k] = v`, `del [k]`). NEVER `_bt_mem_cache = {}` after import —
that re-binds a local and breaks cache coherence.
"""

import math
import time

from analytics.backtest_lib import BacktestResult
from analytics.data_store import BacktestSnapshot

_CANDLE_CLOSE_BUFFER_SECS = 10

# Detectors only need recent candles to check the latest signal (max lookback = 100).
# Slicing to this window before scan_symbol drastically reduces Phase 2 time
# (e.g. 15m/200d = 19,200 rows → 200 rows = ~96× less data for pandas ops).
# The full OHLCV window is preserved in ohlcv_map for _compute_backtest in Phase 3.
_SCAN_WINDOW = 200

# Two-layer backtest cache: L1 (module dict, fast) backed by L2 (DuckDB, survives restarts).
# Keys are 24-char hex strings from _make_bt_cache_key(run_id, last_candle_ts).
_bt_mem_cache: dict[str, BacktestResult | BacktestSnapshot | None] = {}


def _reset_bt_cache() -> None:
    """Clear L1 memory cache. Call in test fixtures to prevent state bleed."""
    _bt_mem_cache.clear()


def _fmt_hold(hours: float) -> str:
    """Format median hold time: '~4h', '~3d'."""
    if hours >= 48:
        return f"~{hours / 24:.0f}d"
    return f"~{hours:.0f}h"


def parse_timeframe_secs(tf: str) -> int:
    """Convert a timeframe string to seconds (e.g. '4h' → 14400, '1wk' → 604800).

    Equity timeframes use yfinance's `1wk` (weekly) suffix; treat it the same as
    a `1w` token for parsing purposes.
    """
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    if tf.endswith("wk"):
        tf = tf[:-1]  # "1wk" → "1w"
    return int(tf[:-1]) * units[tf[-1]]


def secs_until_next_boundary(timeframes: list[str]) -> tuple[float, float]:
    """Return (sleep_seconds, wakeup_unix_timestamp) for the next candle close.

    Wakes at the earliest upcoming boundary + a small buffer so the provider has
    time to finalise the candle (e.g. 04:00:10, not 04:00:00).
    """
    now = time.time()
    next_wakeups = []
    for tf in timeframes:
        interval = parse_timeframe_secs(tf)
        next_close = math.ceil(now / interval) * interval
        next_wakeups.append(next_close + _CANDLE_CLOSE_BUFFER_SECS)
    wake_ts = min(next_wakeups)
    return max(0.0, wake_ts - now), wake_ts
