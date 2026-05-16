"""Detector: ORB Breakout — anchored on the US regular-trading-hours session open.

Bars are grouped into trading sessions whose open is 13:30 UTC (09:30 ET during
DST, matches the T4 4h synthesis anchor). Wifey is an equity-only fork, so the
prior 00:00 UTC daily anchor (24/7 crypto markets) is no longer correct.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df

# Shift each bar timestamp back by 13:30 hours before taking the calendar date.
# A bar at 13:30 UTC maps to 00:00 on its session date; a bar at 13:29 UTC the
# next day maps to 23:59 of the original session date. This cleanly partitions
# bars into RTH sessions without an explicit per-bar session-lookup table.
_SESSION_OPEN_SHIFT = pd.Timedelta(hours=13, minutes=30)


def detect_orb_breakout(
    df: pd.DataFrame,
    range_candles: int = 1,
    # Legacy params kept so existing callers / tests that pass them don't crash.
    # The session anchor is hardcoded to 13:30 UTC (US RTH open).
    session_hour_utc: int = 0,
    timeframe_minutes: int = 0,
) -> pd.DataFrame:
    """Detect Opening Range Breakout (ORB) signals against the US RTH session.

    The opening range is defined by the first ``range_candles`` candles of each
    US trading session (anchored at 13:30 UTC ≈ 09:30 ET during DST). A breakout
    signal fires on any subsequent candle within the same session that *closes*
    outside the range:

    * close > range_high  →  LONG  (SL = range_low)
    * close < range_low   →  SHORT (SL = range_high)

    TP is placed at entry ± 1.5 × range_width (stored in ``context``).
    Only one signal per session per direction is emitted (per-session dedup).

    Note on DST: the 13:30 UTC anchor matches NYSE 09:30 ET during US daylight
    saving time. During standard time the actual session open shifts to 14:30
    UTC; bar timestamps from the data layer shift in lockstep, so the relative
    grouping holds. Cross-DST sessions may see a one-hour offset between the
    range's first bar and the wall-clock session open — acceptable for now.

    Parameters
    ----------
    df:
        OHLCV DataFrame with at least ``open_time``, ``high``, ``low``,
        ``close`` columns. ``open_time`` must be Unix milliseconds UTC.
    range_candles:
        Number of candles from session open that form the opening range (1–4).
        For 4h bars this should typically be 1; for hourly bars, 1–2.
    session_hour_utc:
        Ignored (kept for backwards-compatibility with old callers).
    timeframe_minutes:
        Ignored (kept for backwards-compatibility with old callers).
    """
    n = len(df)
    if n < range_candles + 1:
        return _empty_signals()

    dt_utc = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    session_dates = (dt_utc - _SESSION_OPEN_SHIFT).dt.date

    signals: list[dict[str, object]] = []
    # Track which (session_date, direction) pairs have already fired.
    fired: set[tuple[object, str]] = set()

    unique_sessions = session_dates.unique()
    for session in unique_sessions:
        session_mask = session_dates == session
        session_idx = df.index[session_mask].tolist()

        # Need at least range_candles + 1 candles in this session.
        if len(session_idx) < range_candles + 1:
            continue

        # Opening range = first range_candles candles of the session.
        range_rows = df.loc[session_idx[:range_candles]]
        range_high = float(range_rows["high"].max())
        range_low = float(range_rows["low"].min())
        range_width = range_high - range_low
        if range_width <= 0:
            continue

        range_open_ts = int(df.loc[session_idx[0]]["open_time"])
        range_ctx = (
            f"ORB range {_fmt_time(range_open_ts)} H:{range_high:.2f} L:{range_low:.2f}"
        )

        # Check every candle after the opening range window.
        for idx in session_idx[range_candles:]:
            row = df.loc[idx]
            close = float(row["close"])
            open_time_ms = int(row["open_time"])

            if close > range_high and (session, "long") not in fired:
                tp_price = close + range_width * 1.5
                signals.append(
                    {
                        "open_time": open_time_ms,
                        "direction": "long",
                        "reason": f"orb_long@{range_high:.2f}",
                        "sl_price": range_low,
                        "context": f"{range_ctx} TP:{tp_price:.2f}",
                    }
                )
                fired.add((session, "long"))

            elif close < range_low and (session, "short") not in fired:
                tp_price = close - range_width * 1.5
                signals.append(
                    {
                        "open_time": open_time_ms,
                        "direction": "short",
                        "reason": f"orb_short@{range_low:.2f}",
                        "sl_price": range_high,
                        "context": f"{range_ctx} TP:{tp_price:.2f}",
                    }
                )
                fired.add((session, "short"))

    return _signals_to_df(signals)
