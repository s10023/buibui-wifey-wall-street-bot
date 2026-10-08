"""The OV-1 return frame: a total-return market, a T-bill cash leg, and a position.

Fixes the two frame limits that kept the H1 audit from comparing with
buy-and-hold: ``^GSPC`` is price-only, and its flat leg earned zero. Here the
market is the French ``Mkt-RF + RF`` (CRSP value-weighted, dividends included)
and the flat leg earns French ``RF``. The signal still comes from ``^GSPC``
closes, so the two series are close substitutes, not one index.

The return calendar is the market series' own. The signal is mapped onto it with
:func:`analytics.overlay.rules.position_on`, never inner-joined: an inner join
with ``^GSPC`` would drop the 1,039 pre-1952 Saturday sessions the French file
carries. This is the only module in ``analytics/overlay/`` that reads the DB,
and it never writes.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.overlay.rules import MA_WINDOW, ma_signal, position_on

# Pre-registered (edge-pillars spec § Phase 2): the panel opens at the first
# session on or after this date whose position has a full SMA warm-up behind it.
PANEL_START = pd.Timestamp("1929-01-02")


def load_gspc_close(conn: duckdb.DuckDBPyConnection) -> pd.Series:
    """Daily ``^GSPC`` closes, indexed by session date (read-only).

    ``1d`` bars stamp 04:00/05:00 UTC, i.e. midnight New York time on the
    session date, so the UTC date is the session date.
    """
    df = conn.execute(
        "SELECT (to_timestamp(open_time / 1000) AT TIME ZONE 'UTC')::DATE AS d, close "
        "FROM ohlcv WHERE symbol = '^GSPC' AND timeframe = '1d' ORDER BY open_time"
    ).df()
    return pd.Series(
        df["close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(pd.to_datetime(df["d"])),
        name="close",
    )


def build_frame(
    close: pd.Series,
    market: pd.Series,
    rf: pd.Series,
    *,
    window: int = MA_WINDOW,
    lag: int = 1,
    start: pd.Timestamp = PANEL_START,
) -> pd.DataFrame:
    """Columns ``mkt`` (total return), ``rf`` and ``pos`` on ``market``'s calendar.

    The panel runs from the first session on or after ``start`` whose position
    is defined, through the last session of ``market``. ``rf`` must cover every
    one of those sessions. Raises when ``market`` runs past the last close,
    because the position on those sessions would be read from a stale close and
    nothing downstream could tell.
    """
    if market.index[-1] > close.index[-1]:
        raise ValueError(
            f"market runs to {market.index[-1].date()} but the last signal close is "
            f"{close.index[-1].date()}: sync ^GSPC before building the frame"
        )
    pos = position_on(ma_signal(close, window), pd.DatetimeIndex(market.index), lag=lag)
    frame = pd.DataFrame({"mkt": market, "rf": rf.reindex(market.index), "pos": pos})
    defined = frame.index[(frame.index >= start) & frame["pos"].notna()]
    if defined.empty:
        raise ValueError("no session has a defined position on or after start")
    frame = frame.loc[defined[0] :]
    if frame[["mkt", "rf"]].isna().any().any():
        missing = frame.index[frame[["mkt", "rf"]].isna().any(axis=1)]
        raise ValueError(
            f"{len(missing)} panel sessions lack mkt or rf, first {missing[0]}"
        )
    return frame
