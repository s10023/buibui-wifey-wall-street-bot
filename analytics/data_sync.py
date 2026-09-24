"""Orchestration logic for backfill and incremental sync.

yfinance returns the full available history per call (capped by interval —
2y for 1h/4h, unlimited for 1d/1wk), but ``fetch_bars`` keeps only the first
``BARS_MAX_LIMIT`` bars at or after its ``start_ms``. A deep start therefore
cannot be served in one call: asking for ``1d`` bars since 1927 would store
1927–1947 and silently leave every later bar missing, which reads exactly like
a symbol with no recent history. ``backfill`` pages instead, advancing past the
last bar it stored until a short page proves the history is exhausted.

``sync`` appends the tail, which is why a split can leave a permanent fake
return in the stored series: the provider restates every historical bar onto
the post-split basis, while the bars already stored keep the old one, so the
seam survives every later sync unless caught. ``ADJUSTMENT_BASIS_TOL`` catches
it: the overlap bar is re-fetched anyway, so the restatement is observable at
exactly one point, and the whole series is re-synced when it moves.

The guard only prevents a new seam; it cannot repair one already stored, since
the overlap bar has long since settled onto the new basis by the time the seam
is noticed. Repairing an existing seam needs a full re-backfill of that series.
"""

import logging

import duckdb
import pandas as pd

from analytics.data_fetcher import BARS_MAX_LIMIT, fetch_bars
from analytics.data_quality import check_ohlcv, quarantine
from analytics.data_store import (
    get_close_at,
    get_earliest_open_time,
    get_latest_open_time,
    upsert_ohlcv,
)
from analytics.trading_calendar import check_session_gaps

#: Relative move in the re-fetched overlap bar's close that means the provider
#: restated the series rather than merely finalising a forming candle.
#:
#: This threshold is only safe because ``utils/yfinance_client`` fetches with
#: ``auto_adjust=False``. Yahoo applies splits to the raw OHLC series
#: retroactively but leaves dividends out of it, so a stored bar's close is
#: stable across syncs except when a split lands — with ``auto_adjust=True``
#: every ex-dividend date would shift history a little and trip this on names
#: that did nothing. Re-derive the tolerance if that flag ever changes.
#:
#: 1% sits far above float/rounding noise and far below any split: the canonical
#: factor nearest parity in ``data_quality._SPLIT_FACTORS`` is 0.5, i.e. 50x this
#: tolerance away, so the two populations do not come close to meeting.
ADJUSTMENT_BASIS_TOL: float = 0.01


def backfill(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start_ms: int,
) -> int:
    """Fetch OHLCV history from ``start_ms`` to now and store it.

    Pages through ``fetch_bars`` — each call yields at most ``BARS_MAX_LIMIT``
    bars — until a page comes back short. Returns total rows upserted.
    """
    total = 0
    cursor = start_ms
    while True:
        df = fetch_bars(symbol, timeframe, cursor)
        if df.empty:
            return total
        page_rows = len(df)
        last_open_time = int(df["open_time"].max())
        # A short page is the end of the tape; a full one is a paging boundary.
        is_final_page = page_rows < BARS_MAX_LIMIT
        total += _store_page(
            conn, symbol, timeframe, df, series_ends_here=is_final_page
        )
        if is_final_page:
            return total
        # Strictly increasing: fetch_bars only returns bars at or after cursor.
        cursor = last_open_time + 1


def _store_page(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    *,
    series_ends_here: bool = False,
) -> int:
    """Quality-check, quarantine and store one page. Returns rows upserted.

    ``series_ends_here`` must be True only for the page that terminates the
    paging loop — it is what lets ``check_ohlcv`` judge a frozen tail, and a
    full page's last row is a paging boundary rather than a stopped tape.
    """
    report = check_ohlcv(df, series_ends_here=series_ends_here)
    if not report.is_clean:
        logging.warning("data-quality %s %s: %s", symbol, timeframe, report.summary())
    clean, dropped = quarantine(df, report)
    if clean.empty:
        logging.warning(
            "data-quality %s %s: all %d rows quarantined", symbol, timeframe, len(df)
        )
        return 0

    if len(clean) >= 2:
        gap_report = check_session_gaps(clean, timeframe)
        if gap_report.has_gaps:
            logging.warning(
                "session gap %s %s: %s", symbol, timeframe, gap_report.summary()
            )

    upsert_ohlcv(conn, clean)
    logging.info(
        "backfill %s %s: stored %d rows (%d quarantined)",
        symbol,
        timeframe,
        len(clean),
        len(dropped),
    )
    return len(clean)


def basis_changed(before: float | None, after: float | None) -> bool:
    """Did the provider restate the overlap bar beyond rounding noise?

    ``before`` is the close stored for that bar before the sync, ``after`` the
    close the same bar came back with. A missing or non-positive ``before``
    answers False — an unmeasurable basis and an unchanged one must not both
    read as "restated", and the keep-on-doubt direction here is to append.
    """
    if before is None or after is None or before <= 0:
        return False
    return abs(after / before - 1.0) > ADJUSTMENT_BASIS_TOL


def sync(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
) -> int:
    """Fetch candles from the latest stored open_time onwards (inclusive).

    Starting from ``latest`` (not ``latest + 1``) re-fetches the most recent
    stored candle so its OHLCV values are overwritten with the final values
    once the bar closes — otherwise a candle stored mid-formation would keep
    its stale close forever.

    That overlap bar is also the only point at which a change of the provider's
    split-adjustment basis is observable, and this re-syncs the whole series
    when it moves — see ``ADJUSTMENT_BASIS_TOL``.

    Raises ``ValueError`` if no data exists for (symbol, timeframe) — run
    backfill first. Returns total rows upserted.
    """
    latest = get_latest_open_time(conn, symbol, timeframe)
    if latest is None:
        raise ValueError(f"No data found for {symbol}/{timeframe}. Run backfill first.")

    before = get_close_at(conn, symbol, timeframe, latest)
    rows = backfill(conn, symbol, timeframe, latest)
    after = get_close_at(conn, symbol, timeframe, latest)
    # The None legs are re-stated here rather than left to `basis_changed`
    # alone so mypy narrows both operands for the ratio in the log line below.
    if before is None or after is None or not basis_changed(before, after):
        return rows

    earliest = get_earliest_open_time(conn, symbol, timeframe)
    logging.warning(
        "adjustment basis changed %s %s: overlap bar %d restated %.4f -> %.4f "
        "(x%.4f) — re-syncing the series from %s",
        symbol,
        timeframe,
        latest,
        before,
        after,
        after / before,
        earliest,
    )
    if earliest is None or earliest >= latest:
        return rows
    return rows + backfill(conn, symbol, timeframe, earliest)
