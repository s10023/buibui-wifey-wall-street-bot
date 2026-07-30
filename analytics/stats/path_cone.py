"""Daily price-distribution cone — conditional path percentiles over 1h history.

Equity port of the parent's M5 cone (parent spec:
docs/superpowers/specs/2026-07-18-m5-price-distribution-cone-design.md).

- Period = one RTH session. yfinance 1h bars only exist during regular
  trading hours and RTH never crosses midnight UTC, so grouping bars by UTC
  date IS session grouping (ET is display-only, applied in the UI).
- A complete session has 7 hourly bars (09:30–16:00 ET; the last bar is the
  half-hour 15:30 stub). Early-close days drop out via the completeness rule.
- Each complete historical session's hourly-close path is normalized by that
  session's OWN trailing ADR14 (mean (high−low)/open over the last 14
  complete sessions strictly before it) → ×ADR units comparable across vol
  regimes.
- Per direction × weekday combo (3 × 6 = 18, Mon–Fri only): per-step
  percentile bands, low/high timing distributions, excursion percentiles,
  and pivot-magnitude percentiles. All combos are computed in one pass and
  cached via the StatsBundle; the today-path overlay is computed fresh
  (never cached).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime

import duckdb
import numpy as np

BAND_PCTS = (10.0, 25.0, 50.0, 75.0, 90.0)  # bands row order p10…p90
EXCURSION_PCTS = (10.0, 50.0, 90.0)
PIVOT_PCTS = (50.0, 80.0)
DIRECTIONS = ("all", "bull", "bear")
WEEKDAY_KEYS = ("all", "mon", "tue", "wed", "thu", "fri")
SESSION_BARS = 7  # hourly bars in a complete RTH session (09:30–16:00 ET)
_WD = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ADR_WINDOW = 14
_HOUR_MS = 3_600_000
_TODAY_FETCH_DAYS = 45  # covers 14 complete prior sessions with weekend/holiday slack


@dataclass
class ConeCombo:
    """One direction × weekday slice of the cone. Empty lists when n == 0."""

    direction: str  # "all" | "bull" | "bear"
    weekday: str  # "all" | "mon" … "fri"
    n: int
    bands: list[list[float]]  # 7 steps × 5 percentiles (×ADR), steps 1…7
    low_in_by: list[float]  # 7 cumulative fractions P(session low set by bar h)
    high_in_by: list[float]
    mae_p: list[float]  # [p10, p50, p90] of per-session path minimum (×ADR)
    mfe_p: list[float]  # [p10, p50, p90] of per-session path maximum (×ADR)
    high_piv: list[float]  # [p50, p80] of (day_high − open)/open/adr14, ≥ 0
    low_piv: list[float]  # [p50, p80] of (open − day_low)/open/adr14, ≥ 0


@dataclass
class PathConeBundle:
    """All 18 combos + population size. Deterministic per (symbol, UTC date)."""

    combos: dict[str, ConeCombo]  # keyed "direction|weekday", e.g. "bull|mon"
    total_days: int


@dataclass
class TodayPath:
    """Today's normalized partial path — live, never cached."""

    points: list[float]  # normalized hourly closes (forming bar last)
    elapsed_h: int  # completed hourly bars today (0–7)
    adr14_today: float
    today_open: float


@dataclass
class _DayRecord:
    day: date
    weekday_key: str
    direction: str  # "bull" | "bear" | "doji"
    norm_path: list[float]
    low_hour: int  # 1–7, earliest bar whose low equals the session low
    high_hour: int
    mae: float
    mfe: float
    high_mag: float
    low_mag: float


def _fetch_hourly(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start_ms: int | None = None,
) -> dict[date, list[tuple[int, float, float, float, float]]]:
    """1h bars grouped by UTC date; each session's bars sorted by open_time."""
    sql = (
        "SELECT open_time, open, high, low, close FROM ohlcv "
        "WHERE symbol = ? AND timeframe = '1h' "
    )
    params: list[object] = [symbol]
    if start_ms is not None:
        sql += "AND open_time >= ? "
        params.append(start_ms)
    sql += "ORDER BY open_time"
    by_day: dict[date, list[tuple[int, float, float, float, float]]] = defaultdict(list)
    for open_time, o, h, lo, c in conn.execute(sql, params).fetchall():
        d = datetime.fromtimestamp(int(open_time) / 1000, tz=UTC).date()
        by_day[d].append((int(open_time), float(o), float(h), float(lo), float(c)))
    return by_day


def _build_records(
    by_day: dict[date, list[tuple[int, float, float, float, float]]],
    today: date,
) -> tuple[list[_DayRecord], float | None]:
    """Population records + ADR14 for `today` (None if < 14 complete priors).

    A session enters the population only if it is complete (7 bars), lies
    strictly before `today`, has a positive open, and has 14 complete prior
    sessions to form its trailing ADR window. ADR14 for session d is the mean
    range fraction over the last 14 complete sessions strictly before d.
    """
    ranges: dict[date, float] = {}
    for d in sorted(by_day):
        if d >= today:
            continue
        bars = by_day[d]
        if len(bars) != SESSION_BARS:
            continue
        day_open = bars[0][1]
        if day_open <= 0:
            continue
        day_high = max(b[2] for b in bars)
        day_low = min(b[3] for b in bars)
        ranges[d] = (day_high - day_low) / day_open

    ordered = sorted(ranges)
    adr14: dict[date, float] = {}
    for i, d in enumerate(ordered):
        if i < _ADR_WINDOW:
            continue
        window = ordered[i - _ADR_WINDOW : i]
        val = sum(ranges[w] for w in window) / _ADR_WINDOW
        if val > 0:
            adr14[d] = val

    adr14_today: float | None = None
    if len(ordered) >= _ADR_WINDOW:
        val = sum(ranges[w] for w in ordered[-_ADR_WINDOW:]) / _ADR_WINDOW
        if val > 0:
            adr14_today = val

    records: list[_DayRecord] = []
    for d in ordered:
        if d not in adr14:
            continue
        bars = by_day[d]
        day_open = bars[0][1]
        day_high = max(b[2] for b in bars)
        day_low = min(b[3] for b in bars)
        denom = day_open * adr14[d]
        norm_path = [(b[4] - day_open) / denom for b in bars]
        close_last = bars[-1][4]
        if close_last > day_open:
            direction = "bull"
        elif close_last < day_open:
            direction = "bear"
        else:
            direction = "doji"
        low_hour = next(i + 1 for i, b in enumerate(bars) if b[3] == day_low)
        high_hour = next(i + 1 for i, b in enumerate(bars) if b[2] == day_high)
        records.append(
            _DayRecord(
                day=d,
                weekday_key=_WD[d.weekday()],
                direction=direction,
                norm_path=norm_path,
                low_hour=low_hour,
                high_hour=high_hour,
                mae=min(norm_path),
                mfe=max(norm_path),
                high_mag=(day_high - day_open) / denom,
                low_mag=(day_open - day_low) / denom,
            )
        )
    return records, adr14_today


def _combo_from(direction: str, weekday: str, pop: list[_DayRecord]) -> ConeCombo:
    n = len(pop)
    if n == 0:
        return ConeCombo(direction, weekday, 0, [], [], [], [], [], [], [])
    paths = np.array([r.norm_path for r in pop])
    bands = [
        [float(v) for v in row] for row in np.percentile(paths, BAND_PCTS, axis=0).T
    ]
    steps = range(1, SESSION_BARS + 1)
    low_in_by = [sum(1 for r in pop if r.low_hour <= h) / n for h in steps]
    high_in_by = [sum(1 for r in pop if r.high_hour <= h) / n for h in steps]
    mae_p = [float(v) for v in np.percentile([r.mae for r in pop], EXCURSION_PCTS)]
    mfe_p = [float(v) for v in np.percentile([r.mfe for r in pop], EXCURSION_PCTS)]
    high_piv = [float(v) for v in np.percentile([r.high_mag for r in pop], PIVOT_PCTS)]
    low_piv = [float(v) for v in np.percentile([r.low_mag for r in pop], PIVOT_PCTS)]
    return ConeCombo(
        direction=direction,
        weekday=weekday,
        n=n,
        bands=bands,
        low_in_by=low_in_by,
        high_in_by=high_in_by,
        mae_p=mae_p,
        mfe_p=mfe_p,
        high_piv=high_piv,
        low_piv=low_piv,
    )


def compute_path_cone(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> PathConeBundle:
    """All-history conditional cone. Never raises on thin data (empty combos)."""
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    today = datetime.fromtimestamp(now / 1000, tz=UTC).date()
    records, _ = _build_records(_fetch_hourly(conn, symbol), today)
    combos: dict[str, ConeCombo] = {}
    for direction in DIRECTIONS:
        for wd in WEEKDAY_KEYS:
            pop = [
                r
                for r in records
                if (direction == "all" or r.direction == direction)
                and (wd == "all" or r.weekday_key == wd)
            ]
            combos[f"{direction}|{wd}"] = _combo_from(direction, wd, pop)
    return PathConeBundle(combos=combos, total_days=len(records))


def compute_today_path(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> TodayPath | None:
    """Today's normalized partial path. None when today has no bars or the
    trailing ADR14 is unavailable (new listing / short history)."""
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    today = datetime.fromtimestamp(now / 1000, tz=UTC).date()
    start_ms = now - _TODAY_FETCH_DAYS * 24 * _HOUR_MS
    by_day = _fetch_hourly(conn, symbol, start_ms=start_ms)
    _, adr14_today = _build_records(by_day, today)
    if adr14_today is None:
        return None
    bars = by_day.get(today, [])
    if not bars:
        return None
    today_open = bars[0][1]
    if today_open <= 0:
        return None
    denom = today_open * adr14_today
    points = [(b[4] - today_open) / denom for b in bars]
    elapsed = sum(1 for b in bars if b[0] + _HOUR_MS <= now)
    return TodayPath(
        points=points,
        elapsed_h=min(elapsed, SESSION_BARS),
        adr14_today=adr14_today,
        today_open=today_open,
    )
