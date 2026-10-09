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

A different failure shares the seam: a provider that stops serving a ticker and
resumes it with a different security (AVB, migration 007). No overlap bar comes
back, so neither ``check_ohlcv`` (page-scoped) nor the basis guard sees it.
``_store_page`` therefore also classifies the pair across the seam with
``data_quality.classify_level_break`` and logs it; it never drops a row (#469).
"""

import logging
from collections.abc import Callable

import duckdb
import pandas as pd

from analytics.data_fetcher import BARS_MAX_LIMIT, fetch_bars
from analytics.data_quality import (
    LevelBreak,
    check_ohlcv,
    find_level_breaks,
    quarantine,
    quote_disagrees,
)
from analytics.data_store import (
    get_bar_before,
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

#: Returns the provider's quoted last price for a symbol, or None when unreadable.
#: Injected so tests never reach the network; production callers pass
#: ``utils.yfinance_client.fetch_last_price``.
QuoteFn = Callable[[str], float | None]


def backfill(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start_ms: int,
    *,
    require_data: bool = False,
    quote_fn: QuoteFn | None = None,
) -> int:
    """Fetch OHLCV history from ``start_ms`` to now and store it.

    Pages through ``fetch_bars`` — each call yields at most ``BARS_MAX_LIMIT``
    bars — until a page comes back short. Returns total rows upserted.
    ``require_data`` is passed to ``fetch_bars``, so a page the provider
    returned nothing for raises ``NoProviderDataError``. ``quote_fn`` is passed
    to ``_store_page`` for the level-break cross-check.
    """
    total = 0
    cursor = start_ms
    while True:
        df = fetch_bars(symbol, timeframe, cursor, require_data=require_data)
        if df.empty:
            return total
        page_rows = len(df)
        last_open_time = int(df["open_time"].max())
        # A short page is the end of the tape; a full one is a paging boundary.
        is_final_page = page_rows < BARS_MAX_LIMIT
        total += _store_page(
            conn,
            symbol,
            timeframe,
            df,
            series_ends_here=is_final_page,
            quote_fn=quote_fn,
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
    quote_fn: QuoteFn | None = None,
) -> int:
    """Quality-check, quarantine and store one page. Returns rows upserted.

    ``series_ends_here`` must be True only for the page that terminates the
    paging loop — it is what lets ``check_ohlcv`` judge a frozen tail, and a
    full page's last row is a paging boundary rather than a stopped tape.

    ``quote_fn``, when given, cross-checks the newest close against the
    provider's quote after a level break on the series-ending page.
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

    _warn_level_breaks(
        conn,
        symbol,
        timeframe,
        clean,
        quote_fn=quote_fn if series_ends_here else None,
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


def _seam_pairs(
    conn: duckdb.DuckDBPyConnection, symbol: str, timeframe: str, clean: pd.DataFrame
) -> tuple[list[int], list[float]]:
    """The page's open_times and closes, prefixed with the stored bar it continues.

    The stored predecessor is prepended only when the page's first bar is NEW. A
    page that starts on a stored bar (sync's overlap) carries its own anchor, and
    pairing it with the bar before would read a split restatement — the overlap
    re-fetched on the new basis, its predecessor still on the old — as a break.
    """
    page = clean.sort_values("open_time")
    times = [int(t) for t in page["open_time"]]
    closes = [float(c) for c in page["close"]]
    if get_close_at(conn, symbol, timeframe, times[0]) is None:
        prev = get_bar_before(conn, symbol, timeframe, times[0])
        if prev is not None:
            times.insert(0, prev[0])
            closes.insert(0, prev[1])
    return times, closes


def _warn_level_breaks(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    clean: pd.DataFrame,
    *,
    quote_fn: QuoteFn | None,
) -> list[LevelBreak]:
    """Log every level break the page writes. Advisory: nothing is dropped."""
    times, closes = _seam_pairs(conn, symbol, timeframe, clean)
    breaks = find_level_breaks(times, closes, timeframe)
    for brk in breaks:
        label = (
            "probable wrong instrument" if brk.kind == "gapped" else "large level move"
        )
        logging.warning(
            "%s %s %s: bar %d close %.4f -> %.4f (x%.4f), %.1f days after the"
            " previous bar"
            " — stored, not dropped; check the provider (#469)",
            label,
            symbol,
            timeframe,
            brk.open_time,
            brk.prev_close,
            brk.close,
            brk.ratio,
            brk.gap_days,
        )
    if breaks and quote_fn is not None:
        newest = closes[-1]
        quote = quote_fn(symbol)
        if quote is not None and quote_disagrees(newest, quote):
            logging.warning(
                "probable wrong instrument %s %s: newest close %.4f but the"
                " provider quotes %.4f — the history is not this security (#469)",
                symbol,
                timeframe,
                newest,
                quote,
            )
        else:
            logging.info(
                "level move %s %s: provider quote %s, newest close %.4f",
                symbol,
                timeframe,
                "unreadable" if quote is None else f"{quote:.4f} agrees",
                newest,
            )
    return breaks


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
    *,
    require_data: bool = False,
    quote_fn: QuoteFn | None = None,
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
    backfill first. Returns total rows upserted. With ``require_data=True``,
    raises ``NoProviderDataError`` when the provider returned nothing, which
    ``0`` rows alone cannot tell apart from "no new bars". ``quote_fn`` is
    passed through to ``backfill`` for the level-break cross-check.
    """
    latest = get_latest_open_time(conn, symbol, timeframe)
    if latest is None:
        raise ValueError(f"No data found for {symbol}/{timeframe}. Run backfill first.")

    before = get_close_at(conn, symbol, timeframe, latest)
    rows = backfill(
        conn,
        symbol,
        timeframe,
        latest,
        require_data=require_data,
        quote_fn=quote_fn,
    )
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
    return rows + backfill(
        conn,
        symbol,
        timeframe,
        earliest,
        require_data=require_data,
        quote_fn=quote_fn,
    )
