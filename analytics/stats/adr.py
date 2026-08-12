"""Average Daily Range (ADR) statistics.

The median fields (ported from parent #598, 2026-08-12) are **display-only**. The
median answers "what does a normal day look like", which is the question an operator
reading the Stats tab is actually asking; the mean answers "what is the total range
per day", a different question, and it stays where it is.

**The gap is the signal, and do not write copy that assumes mean > median.** Daily
range is right-skewed, so the mean usually sits above the median — measured through
this function 2026-08-12 that held **strictly in 9 of 10** equity cells
(SPY/QQQ/NVDA/AAPL/MSFT × 14d/30d; SPY 30d 0.94% mean vs 0.89% median, NVDA 14d 3.42%
vs 3.18%). The tenth, **AAPL 30d, is a tie at 2.19% / 2.19%** — so the strict
inequality already fails on equity data at n=10, before considering the parent's
ETHUSDT case where a 14-day window fully **inverted** (2.54% mean against a 2.75%
median, a few unusually *quiet* days pulling the mean down).

**Quote these figures only against `compute_adr`, which derives from `1h` bars.**
Reading `1d` bars directly gives different numbers (SPY 30d 0.96%/0.91% rather than
0.94%/0.89%) and a different verdict on AAPL 30d. Same instrument, same day, two
sources — name the source.

Nothing SIZES off the median, deliberately. `adr_14` is a live input to `weekly_state`,
`weekly_wick`, `signal/stats_context` and the alert formatter, and `today_consumed_pct`
below still divides by the mean. Repointing any of those is a strategy change needing
its own evidence, not a display tweak riding along with one —
`tests/test_stats_adr.py::test_today_consumed_still_divides_by_the_MEAN` fails loudly
if it happens by accident.

Script: `docs/plans/scripts/dow_return_noise.py`.
"""

import statistics
from dataclasses import dataclass

import duckdb

from analytics.stats._common import _start_ms


@dataclass
class ADRResult:
    """Average Daily Range statistics."""

    adr_14: float  # 14-day avg (high-low)/open
    adr_30: float  # 30-day avg (high-low)/open
    adr_14_median: float  # 14-day MEDIAN (high-low)/open — display only
    adr_30_median: float  # 30-day MEDIAN (high-low)/open — display only
    today_range_pct: float | None  # today's (high-low)/open so far
    today_consumed_pct: float | None  # today_range / adr_14 (the MEAN, on purpose)
    today_move_up: bool | None = (
        None  # True if latest close is in upper half of today's range
    )


def compute_adr(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
) -> ADRResult:
    """Compute Average Daily Range for 14-day and 30-day windows plus today's range.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    # We need 30 days back for the 30-day ADR, but also today's data
    start = _start_ms(35)  # 35 days gives us buffer for 30-day calc

    rows = conn.execute(
        """
        WITH daily AS (
            SELECT
                (epoch_ms(open_time)::TIMESTAMP)::DATE AS trade_date,
                MAX(high) AS day_high, MIN(low) AS day_low,
                FIRST(open ORDER BY open_time) AS day_open,
                LAST(close ORDER BY open_time) AS day_close
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h'
              AND open_time >= $start_ms
            GROUP BY trade_date
            ORDER BY trade_date DESC
        )
        SELECT trade_date, day_high, day_low, day_open, day_close
        FROM daily
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    # rows are ordered newest first
    ranges = [
        (float(day_high) - float(day_low)) / float(day_open)
        for (_, day_high, day_low, day_open, _close) in rows
        if float(day_open) > 0
    ]

    if not ranges:
        raise ValueError(f"No OHLCV data for {symbol}")

    adr_14 = sum(ranges[:14]) / min(14, len(ranges))
    adr_30 = sum(ranges[:30]) / min(30, len(ranges))
    # `ranges` is non-empty (guarded above), so neither slice can be empty and
    # statistics.median cannot raise StatisticsError.
    adr_14_median = statistics.median(ranges[:14])
    adr_30_median = statistics.median(ranges[:30])

    # Today's range: use the most recent date in rows (rows[0] is the newest day)
    # rows[0] may be today (partial) or yesterday (if before today's candles sync)
    today_range_pct: float | None = None
    today_consumed_pct: float | None = None
    today_move_up: bool | None = None
    if rows:
        newest_day_high = float(rows[0][1])
        newest_day_low = float(rows[0][2])
        newest_day_open = float(rows[0][3])
        newest_day_close = float(rows[0][4])
        if newest_day_open > 0:
            today_range_pct = (newest_day_high - newest_day_low) / newest_day_open
            if adr_14 > 0:
                today_consumed_pct = today_range_pct / adr_14
        if newest_day_high != newest_day_low:
            mid = (newest_day_high + newest_day_low) / 2
            today_move_up = newest_day_close > mid

    return ADRResult(
        adr_14=adr_14,
        adr_30=adr_30,
        adr_14_median=adr_14_median,
        adr_30_median=adr_30_median,
        today_range_pct=today_range_pct,
        today_consumed_pct=today_consumed_pct,
        today_move_up=today_move_up,
    )
