"""Weekly price-distribution cone — conditional path percentiles over 1h history.

Equity port of the parent's weekly cone (parent spec:
docs/superpowers/specs/2026-07-21-weekly-path-cone-design.md).

- Period = the Monday-anchored trading week, Mon–Fri RTH. A complete week is
  5 sessions × 7 hourly bars = 35 bars; holiday and early-close weeks drop
  out via the completeness rule (a 4-session week is not a comparable path).
- Each complete historical week's hourly-close path is normalized by that
  week's OWN trailing AWR14 (mean (high−low)/open over the last 14 complete
  weeks strictly before it) → ×AWR units comparable across vol regimes.
- Per direction (3: all/bull/bear): per-step percentile bands, low/high timing
  distributions, excursion percentiles, and pivot-magnitude percentiles.
  Weekday is the x-axis here, not a conditioning cell — so "all" IS the
  unconditional reference band rendered beneath the conditional cones.

The cone is CONDITIONAL ON OUTCOME, not a forecast: a "bull week" is defined
by its close, so the bull cone sits above the unconditional one by
construction and that separation carries no predictive information.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import duckdb
import numpy as np

BAND_PCTS = (10.0, 25.0, 50.0, 75.0, 90.0)  # bands row order p10…p90
EXCURSION_PCTS = (10.0, 50.0, 90.0)
PIVOT_PCTS = (50.0, 80.0)
DIRECTIONS = ("all", "bull", "bear")
WEEK_BARS = 35  # 5 RTH sessions × 7 hourly bars (Mon–Fri)
_AWR_WINDOW = 14  # weeks
_HOUR_MS = 3_600_000
_WEEK_MS = 7 * 24 * _HOUR_MS
_CURRENT_FETCH_WEEKS = 24  # covers 14 complete prior weeks with holiday slack


@dataclass
class WeeklyConeCombo:
    """One direction slice of the weekly cone. Empty lists when n == 0."""

    direction: str  # "all" | "bull" | "bear"
    n: int
    bands: list[list[float]]  # 35 steps × 5 percentiles (×AWR), steps 1…35
    low_in_by: list[float]  # 35 cumulative fractions P(week low set by bar h)
    high_in_by: list[float]
    mae_p: list[float]  # [p10, p50, p90] of per-week path minimum (×AWR)
    mfe_p: list[float]  # [p10, p50, p90] of per-week path maximum (×AWR)
    high_piv: list[float]  # [p50, p80] of (week_high − open)/open/awr14, ≥ 0
    low_piv: list[float]  # [p50, p80] of (open − week_low)/open/awr14, ≥ 0


@dataclass
class WeeklyConeBundle:
    """The 3 direction combos + population size. Deterministic per (symbol, week)."""

    combos: dict[str, WeeklyConeCombo]  # keyed "all" | "bull" | "bear"
    total_weeks: int


@dataclass
class CurrentWeekPath:
    """The forming week's normalized partial path — live, never cached."""

    points: list[float]  # normalized hourly closes (forming bar last)
    elapsed_h: int  # completed hourly bars this week (0–35)
    awr14_current: float
    week_open: float


@dataclass
class WeekRecord:
    week: date  # the Monday
    direction: str  # "bull" | "bear" | "doji"
    norm_path: list[float]
    low_hour: int  # 1–35, earliest bar whose low equals the week low
    high_hour: int
    mae: float
    mfe: float
    high_mag: float
    low_mag: float


_WeekRecord = WeekRecord  # back-compat alias for internal references


def _week_key(moment: datetime) -> date:
    """The Monday (UTC date) of the week containing `moment`."""
    return (moment - timedelta(days=moment.weekday())).date()


def _fetch_hourly(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start_ms: int | None = None,
) -> dict[date, list[tuple[int, float, float, float, float]]]:
    """1h bars grouped by Monday-anchored week; each week's bars sorted by open_time."""
    sql = (
        "SELECT open_time, open, high, low, close FROM ohlcv "
        "WHERE symbol = ? AND timeframe = '1h' "
    )
    params: list[object] = [symbol]
    if start_ms is not None:
        sql += "AND open_time >= ? "
        params.append(start_ms)
    sql += "ORDER BY open_time"
    by_week: dict[date, list[tuple[int, float, float, float, float]]] = defaultdict(
        list
    )
    for open_time, o, h, lo, c in conn.execute(sql, params).fetchall():
        moment = datetime.fromtimestamp(int(open_time) / 1000, tz=UTC)
        by_week[_week_key(moment)].append(
            (int(open_time), float(o), float(h), float(lo), float(c))
        )
    return by_week


def _build_records(
    by_week: dict[date, list[tuple[int, float, float, float, float]]],
    current_week: date,
) -> tuple[list[_WeekRecord], float | None]:
    """Population records + AWR14 for `current_week` (None if < 14 complete priors).

    A week enters the population only if it is complete (35 bars), lies
    strictly before `current_week`, has a positive open, and has 14 complete
    prior weeks to form its trailing AWR window.
    """
    ranges: dict[date, float] = {}
    for w in sorted(by_week):
        if w >= current_week:
            continue
        bars = by_week[w]
        if len(bars) != WEEK_BARS:
            continue
        week_open = bars[0][1]
        if week_open <= 0:
            continue
        week_high = max(b[2] for b in bars)
        week_low = min(b[3] for b in bars)
        ranges[w] = (week_high - week_low) / week_open

    ordered = sorted(ranges)
    awr14: dict[date, float] = {}
    for i, w in enumerate(ordered):
        if i < _AWR_WINDOW:
            continue
        window = ordered[i - _AWR_WINDOW : i]
        val = sum(ranges[x] for x in window) / _AWR_WINDOW
        if val > 0:
            awr14[w] = val

    awr14_current: float | None = None
    if len(ordered) >= _AWR_WINDOW:
        val = sum(ranges[x] for x in ordered[-_AWR_WINDOW:]) / _AWR_WINDOW
        if val > 0:
            awr14_current = val

    records: list[_WeekRecord] = []
    for w in ordered:
        if w not in awr14:
            continue
        bars = by_week[w]
        week_open = bars[0][1]
        week_high = max(b[2] for b in bars)
        week_low = min(b[3] for b in bars)
        denom = week_open * awr14[w]
        norm_path = [(b[4] - week_open) / denom for b in bars]
        close_last = bars[-1][4]
        if close_last > week_open:
            direction = "bull"
        elif close_last < week_open:
            direction = "bear"
        else:
            direction = "doji"
        low_hour = next(i + 1 for i, b in enumerate(bars) if b[3] == week_low)
        high_hour = next(i + 1 for i, b in enumerate(bars) if b[2] == week_high)
        records.append(
            _WeekRecord(
                week=w,
                direction=direction,
                norm_path=norm_path,
                low_hour=low_hour,
                high_hour=high_hour,
                mae=min(norm_path),
                mfe=max(norm_path),
                high_mag=(week_high - week_open) / denom,
                low_mag=(week_open - week_low) / denom,
            )
        )
    return records, awr14_current


def _combo_from(direction: str, pop: list[_WeekRecord]) -> WeeklyConeCombo:
    n = len(pop)
    if n == 0:
        return WeeklyConeCombo(direction, 0, [], [], [], [], [], [], [])
    paths = np.array([r.norm_path for r in pop])
    bands = [
        [float(v) for v in row] for row in np.percentile(paths, BAND_PCTS, axis=0).T
    ]
    steps = range(1, WEEK_BARS + 1)
    low_in_by = [sum(1 for r in pop if r.low_hour <= h) / n for h in steps]
    high_in_by = [sum(1 for r in pop if r.high_hour <= h) / n for h in steps]
    mae_p = [float(v) for v in np.percentile([r.mae for r in pop], EXCURSION_PCTS)]
    mfe_p = [float(v) for v in np.percentile([r.mfe for r in pop], EXCURSION_PCTS)]
    high_piv = [float(v) for v in np.percentile([r.high_mag for r in pop], PIVOT_PCTS)]
    low_piv = [float(v) for v in np.percentile([r.low_mag for r in pop], PIVOT_PCTS)]
    return WeeklyConeCombo(
        direction=direction,
        n=n,
        bands=bands,
        low_in_by=low_in_by,
        high_in_by=high_in_by,
        mae_p=mae_p,
        mfe_p=mfe_p,
        high_piv=high_piv,
        low_piv=low_piv,
    )


def compute_weekly_cone(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> WeeklyConeBundle:
    """All-history weekly cone. Never raises on thin data (empty combos)."""
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    current_week = _week_key(datetime.fromtimestamp(now / 1000, tz=UTC))
    records, _ = _build_records(_fetch_hourly(conn, symbol), current_week)
    combos: dict[str, WeeklyConeCombo] = {}
    for direction in DIRECTIONS:
        pop = [r for r in records if direction == "all" or r.direction == direction]
        combos[direction] = _combo_from(direction, pop)
    return WeeklyConeBundle(combos=combos, total_weeks=len(records))


def week_records(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> list[WeekRecord]:
    """The cone's own completed-week population, chronological.

    Public accessor so any future audit shares the cone's AWR normalization
    and week-population rules rather than re-deriving them. Returns [] on
    thin data rather than raising, matching compute_weekly_cone.
    """
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    current_week = _week_key(datetime.fromtimestamp(now / 1000, tz=UTC))
    records, _ = _build_records(_fetch_hourly(conn, symbol), current_week)
    return records


def compute_current_week_path(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> CurrentWeekPath | None:
    """The forming week's normalized partial path. None when the week has no
    bars or the trailing AWR14 is unavailable (new listing / short history)."""
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    current_week = _week_key(datetime.fromtimestamp(now / 1000, tz=UTC))
    start_ms = now - _CURRENT_FETCH_WEEKS * _WEEK_MS
    by_week = _fetch_hourly(conn, symbol, start_ms=start_ms)
    _, awr14_current = _build_records(by_week, current_week)
    if awr14_current is None:
        return None
    bars = by_week.get(current_week, [])
    if not bars:
        return None
    week_open = bars[0][1]
    if week_open <= 0:
        return None
    denom = week_open * awr14_current
    points = [(b[4] - week_open) / denom for b in bars]
    elapsed = sum(1 for b in bars if b[0] + _HOUR_MS <= now)
    return CurrentWeekPath(
        points=points,
        elapsed_h=min(elapsed, WEEK_BARS),
        awr14_current=awr14_current,
        week_open=week_open,
    )
