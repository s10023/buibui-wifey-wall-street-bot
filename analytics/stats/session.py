"""Session breakdown — US equity session extreme analysis (ET windows)."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _DOW_SHORT, _start_ms


@dataclass
class SessionRow:
    """Session extreme analysis."""

    session: str  # "Pre-Market" | "RTH" | "Power Hour" | "After Hours"
    high_pct: float  # fraction of trading days this session made the daily high
    low_pct: float  # fraction of trading days this session made the daily low
    by_dow: dict[str, float]  # session_high_pct keyed by short DOW name


@dataclass
class SessionResult:
    """Session breakdown for all 4 equity sessions."""

    rows: list[SessionRow]


# Half-open ET minute-since-midnight ranges (mirrors T15 alert formatter):
#   Pre-Market  [04:00, 09:30) → [240, 569]
#   RTH         [09:30, 15:00) → [570, 899]
#   Power Hour  [15:00, 16:00) → [900, 959]
#   After Hours [16:00, 20:00) → [960, 1199]
# Overnight 20:00–04:00 ET → 'Off' (excluded from aggregation).
_SESSION_CASE = """
    CASE
        WHEN HOUR(ny) * 60 + MINUTE(ny) BETWEEN 240  AND 569  THEN 'Pre-Market'
        WHEN HOUR(ny) * 60 + MINUTE(ny) BETWEEN 570  AND 899  THEN 'RTH'
        WHEN HOUR(ny) * 60 + MINUTE(ny) BETWEEN 900  AND 959  THEN 'Power Hour'
        WHEN HOUR(ny) * 60 + MINUTE(ny) BETWEEN 960  AND 1199 THEN 'After Hours'
        ELSE 'Off'
    END
"""

_NY_TS = "(epoch_ms(open_time)::TIMESTAMP AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')"

_SESSION_ORDER = ["Pre-Market", "RTH", "Power Hour", "After Hours"]


def compute_session_breakdown(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> SessionResult:
    """Compute session breakdown: which US equity session most often makes daily H/L.

    Sessions are in America/New_York wall-clock (DST-aware):
    - Pre-Market:  04:00–09:30
    - RTH:         09:30–15:00 (cash open through last hour before close)
    - Power Hour:  15:00–16:00 (final hour of cash session)
    - After Hours: 16:00–20:00

    Overnight 20:00–04:00 ET is excluded.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    start = _start_ms(days)

    rows = conn.execute(
        f"""
        WITH hourly AS (
            SELECT open_time, high, low,
                {_NY_TS} AS ny,
                {_NY_TS}::DATE AS trade_date,
                dayname({_NY_TS}::DATE) AS dow,
                {_SESSION_CASE} AS session
            FROM ohlcv WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
        ),
        daily_ext AS (
            SELECT trade_date, MAX(high) AS day_high, MIN(low) AS day_low
            FROM hourly GROUP BY trade_date
        ),
        session_ext AS (
            SELECT h.trade_date, h.session, h.dow,
                   MAX(h.high) = de.day_high AS made_high,
                   MIN(h.low)  = de.day_low  AS made_low
            FROM hourly h JOIN daily_ext de ON h.trade_date = de.trade_date
            WHERE h.session != 'Off'
            GROUP BY h.trade_date, h.session, h.dow, de.day_high, de.day_low
        ),
        totals AS (SELECT COUNT(DISTINCT trade_date) AS n FROM daily_ext)
        SELECT
            session,
            SUM(made_high::INT)::DOUBLE / t.n AS high_pct,
            SUM(made_low::INT)::DOUBLE  / t.n AS low_pct
        FROM session_ext CROSS JOIN totals t
        GROUP BY session, t.n
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    dow_rows = conn.execute(
        f"""
        WITH hourly AS (
            SELECT open_time, high, low,
                {_NY_TS} AS ny,
                {_NY_TS}::DATE AS trade_date,
                dayname({_NY_TS}::DATE) AS dow,
                {_SESSION_CASE} AS session
            FROM ohlcv WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
        ),
        daily_ext AS (
            SELECT trade_date, MAX(high) AS day_high, MIN(low) AS day_low
            FROM hourly GROUP BY trade_date
        ),
        session_ext AS (
            SELECT h.trade_date, h.session, h.dow,
                   MAX(h.high) = de.day_high AS made_high
            FROM hourly h JOIN daily_ext de ON h.trade_date = de.trade_date
            WHERE h.session != 'Off'
            GROUP BY h.trade_date, h.session, h.dow, de.day_high
        ),
        totals_by_dow AS (
            SELECT dow, COUNT(DISTINCT trade_date) AS n
            FROM (SELECT DISTINCT trade_date, dow FROM hourly WHERE session != 'Off')
            GROUP BY dow
        )
        SELECT
            se.session,
            se.dow,
            SUM(se.made_high::INT)::DOUBLE / td.n AS high_pct_dow
        FROM session_ext se
        JOIN totals_by_dow td ON se.dow = td.dow
        GROUP BY se.session, se.dow, td.n
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    session_dow_map: dict[str, dict[str, float]] = {s: {} for s in _SESSION_ORDER}
    for session, dow_full, high_pct_dow in dow_rows:
        s = str(session)
        if s not in session_dow_map:
            continue
        short = _DOW_SHORT.get(str(dow_full), str(dow_full)[:3])
        session_dow_map[s][short] = float(high_pct_dow)

    session_map: dict[str, SessionRow] = {}
    for session, high_pct, low_pct in rows:
        s = str(session)
        session_map[s] = SessionRow(
            session=s,
            high_pct=float(high_pct),
            low_pct=float(low_pct),
            by_dow=session_dow_map.get(s, {}),
        )

    ordered = [session_map[s] for s in _SESSION_ORDER if s in session_map]
    return SessionResult(rows=ordered)
