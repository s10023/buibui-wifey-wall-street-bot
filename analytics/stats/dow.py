"""Day-of-week pattern statistics."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _DOW_SHORT, _start_ms


@dataclass
class DOWRow:
    """Day-of-week pattern statistics."""

    dow: str  # "Mon" … "Sun"
    avg_range_pct: float
    # Median sits beside the mean rather than replacing it: daily range is
    # right-skewed, so a single liquidation day can drag a DOW's mean well above
    # any range that day typically prints. Reading both is how you tell "Tuesday
    # is wide" from "one Tuesday was wide".
    #
    # Unlike `median_return_pct`, this one is NOT dimmed against an error bar.
    # Range is bounded below by zero and carries no direction, so there is no
    # sign to falsely imply — the failure mode `return_stderr_pct` exists to stop
    # does not apply here. It is a plain robustness cross-check on the mean.
    median_range_pct: float
    bull_pct: float  # % days close > open
    sample_days: int
    avg_return_pct: float = 0.0  # avg (close-open)/open — directional return
    median_return_pct: float = 0.0  # median of the same — robust to fat tails
    # Standard error of avg_return_pct: stddev/sqrt(n). Exists so a CONSUMER can tell
    # a directional read from noise without re-deriving dispersion.
    #
    # On US equities this column is noise, and not marginally. Measured 2026-08-12
    # over SPY/QQQ/NVDA/AAPL/MSFT at 365d (script
    # `docs/plans/scripts/dow_return_noise.py`, which calls this function rather
    # than reading 1d bars — the two sources disagree): **all 25 of 25 cells** fall
    # inside the band, the largest |t| across every symbol and weekday is **1.76**
    # (SPY Tue), and mean and median disagree on sign in **5 of 25**. None of that
    # was visible while the mean was rendered alone in green or red.
    #
    # The Stats tab dims both return values inside **2.576** SE — Bonferroni for the
    # **5** weekdays read at once (0.05/5 = 0.01 two-sided). **This is NOT the
    # parent's 2.69**, which is Bonferroni over **7**: crypto trades weekends and US
    # equities do not, so the DOW table here has 5 rows, never 7. Re-derive the
    # constant if the row count ever changes.
    #
    # Not a gate, just the honest error bar. None when n < 2, where sample stddev is
    # undefined — treat that as "cannot claim a direction", never as "significant".
    return_stderr_pct: float | None = None
    strong_high_pct: float = 0.0  # fraction of days where upper wick < 20% of range
    strong_low_pct: float = 0.0  # fraction of days where lower wick < 20% of range


@dataclass
class DOWResult:
    """Day-of-week patterns, Mon-first.

    **Five rows on US equities, not seven** — the ordering below still lists Sat/Sun
    so the type stays shared with the crypto parent, but RTH produces no weekend
    sessions, so they are filtered out and never appear. Any constant derived from
    "the number of weekdays read at once" (see `DOWRow.return_stderr_pct`) must use
    5 here.
    """

    rows: list[DOWRow]


def compute_dow_patterns(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> DOWResult:
    """Compute day-of-week average range, bull percentage, and sample count.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    start = _start_ms(days)
    rows = conn.execute(
        """
        WITH daily AS (
            SELECT
                (epoch_ms(open_time)::TIMESTAMP)::DATE                   AS trade_date,
                dayname((epoch_ms(open_time)::TIMESTAMP)::DATE)          AS dow,
                MAX(high) AS day_high, MIN(low) AS day_low,
                FIRST(open ORDER BY open_time)  AS day_open,
                LAST(close ORDER BY open_time)  AS day_close
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h'
              AND open_time >= $start_ms
            GROUP BY trade_date, dow
        )
        SELECT
            dow,
            AVG((day_high - day_low) / day_open) AS avg_range_pct,
            MEDIAN((day_high - day_low) / day_open) AS median_range_pct,
            SUM(CASE WHEN day_close > day_open THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS bull_pct,
            COUNT(*) AS sample_days,
            AVG((day_close - day_open) / day_open) AS avg_return_pct,
            MEDIAN((day_close - day_open) / day_open) AS median_return_pct,
            STDDEV_SAMP((day_close - day_open) / day_open)
                / SQRT(COUNT(*)) AS return_stderr_pct,
            AVG(CASE
                WHEN (day_high - day_low) > 0 AND
                     (day_close - day_low) / (day_high - day_low) < 0.20
                THEN 1.0 ELSE 0.0
            END) AS strong_high_pct,
            AVG(CASE
                WHEN (day_high - day_low) > 0 AND
                     (day_high - day_close) / (day_high - day_low) < 0.20
                THEN 1.0 ELSE 0.0
            END) AS strong_low_pct
        FROM daily
        WHERE day_open > 0
        GROUP BY dow
        ORDER BY dow
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    dow_map: dict[str, DOWRow] = {}
    for (
        dow_full,
        avg_range,
        median_range,
        bull_pct,
        n,
        avg_return,
        median_return,
        return_stderr,
        strong_high,
        strong_low,
    ) in rows:
        short = _DOW_SHORT.get(str(dow_full), str(dow_full)[:3])
        dow_map[short] = DOWRow(
            dow=short,
            avg_range_pct=float(avg_range),
            median_range_pct=float(median_range),
            bull_pct=float(bull_pct),
            sample_days=int(n),
            avg_return_pct=float(avg_return),
            median_return_pct=float(median_return),
            # NULL at n < 2 (sample stddev undefined) — kept as None rather than
            # coerced to 0.0, which would read as a zero-width error bar and make a
            # single day look infinitely significant.
            return_stderr_pct=None if return_stderr is None else float(return_stderr),
            strong_high_pct=float(strong_high),
            strong_low_pct=float(strong_low),
        )

    # Return in Mon–Sun order
    ordered_rows = [
        dow_map[s]
        for s in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if s in dow_map
    ]
    return DOWResult(rows=ordered_rows)
