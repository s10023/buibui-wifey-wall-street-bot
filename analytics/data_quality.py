"""OHLCV data-quality monitor — detection (pure) + quarantine helper.

No DB, no network, no side effects. `check_ohlcv` returns a typed report of
integrity problems by positional row index (0..n-1 over a reset index).
`quarantine` (Task 4) drops only the unambiguously-bad rows.

``check_ohlcv`` stays calendar-free (no library dependency, pure): it flags only
unambiguous timestamp anomalies (duplicates, non-monotonic order). Calendar-aware
*session-gap* detection (N3 PR2) lives in the pure ``detect_session_gaps`` /
``SessionGapReport`` below, fed an NYSE trading-date list by
``analytics.trading_calendar`` (the only module importing ``exchange_calendars``).
Missing sessions are warn-only — absent data, never quarantined.

A FROZEN TAIL (``series_ends_here=True``) is the one quarantine set that is a
property of the *series* rather than of a row, so it is opt-in per call: see
``check_ohlcv``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

_PRICE_COLS = ["open", "high", "low", "close"]
_OHLCV_NUMERIC = _PRICE_COLS + ["volume"]

# canonical price ratios (new/old close) that signal an unadjusted split
_SPLIT_FACTORS: tuple[float, ...] = (0.5, 1.0 / 3.0, 0.25, 0.2, 2.0, 3.0, 4.0, 5.0)
_SPLIT_TOL: float = 0.05  # within ±5% (relative) of a canonical factor


@dataclass(frozen=True)
class DataQualityReport:
    """Per-frame integrity findings, as positional row indices.

    Quarantine sets (dropped before storage): nan, non-positive price, broken
    bar geometry, later-duplicate timestamps, frozen tail. Warn-only sets
    (logged, kept): zero volume, return outliers, suspected splits,
    non-monotonic timestamps.

    ``frozen_tail_idx`` is empty unless the caller passed
    ``series_ends_here=True`` — it is the only set that cannot be judged from a
    row alone.
    """

    n_rows: int
    nan_idx: tuple[int, ...]
    nonpositive_price_idx: tuple[int, ...]
    bad_bar_idx: tuple[int, ...]
    duplicate_time_idx: tuple[int, ...]
    zero_volume_idx: tuple[int, ...]
    return_outlier_idx: tuple[int, ...]
    suspected_split_idx: tuple[int, ...]
    nonmonotonic_idx: tuple[int, ...]
    frozen_tail_idx: tuple[int, ...] = ()

    @property
    def quarantine_idx(self) -> tuple[int, ...]:
        s = (
            set(self.nan_idx)
            | set(self.nonpositive_price_idx)
            | set(self.bad_bar_idx)
            | set(self.duplicate_time_idx)
            | set(self.frozen_tail_idx)
        )
        return tuple(sorted(s))

    @property
    def has_warnings(self) -> bool:
        return bool(
            self.zero_volume_idx
            or self.return_outlier_idx
            or self.suspected_split_idx
            or self.nonmonotonic_idx
        )

    @property
    def is_clean(self) -> bool:
        return self.n_rows > 0 and not self.quarantine_idx and not self.has_warnings

    def summary(self) -> str:
        parts: list[str] = []
        labels = [
            ("NaN", self.nan_idx),
            ("non-positive price", self.nonpositive_price_idx),
            ("bad geometry", self.bad_bar_idx),
            ("duplicate ts", self.duplicate_time_idx),
            ("frozen tail", self.frozen_tail_idx),
            ("zero volume", self.zero_volume_idx),
            ("return outlier", self.return_outlier_idx),
            ("suspected split", self.suspected_split_idx),
            ("non-monotonic ts", self.nonmonotonic_idx),
        ]
        for name, idx in labels:
            if idx:
                parts.append(f"{len(idx)} {name}")
        return ", ".join(parts) or "clean"


def _idx_tuple(mask: "pd.Series[bool]") -> tuple[int, ...]:
    return tuple(int(i) for i in mask.index[mask.to_numpy()])


def _trailing_frozen_idx(df: pd.DataFrame, valid: "pd.Series[bool]") -> tuple[int, ...]:
    """Positional indices of the maximal FROZEN run that ENDS at the last row.

    Frozen = non-positive volume AND a close identical to the previous bar's.
    A provider that keeps quoting a name after its last trade forward-fills the
    final print at zero volume, so the run is the dead tape; the run must reach
    the end of the frame, because the same row shape occurs harmlessly deep in
    history.

    ⚠ **Position is the whole discriminator, and the row shape alone is not.**
    Measured 2026-09-02 over the full DB: "zero volume AND unchanged close"
    matches 1,368 rows, only 9 of which are dead tails — the other 1,359 sit
    mid-history in live names (808 ``SW``, 231 ``AMCR``, 188 ``^GSPC``).
    Requiring the run to terminate the series leaves exactly those 9. The
    nearest surviving frozen row is 81 bars from its series end, so the two
    populations do not overlap.

    Row 0 can never start a run (no previous close inside the frame), which
    fails in the safe direction: a wholly-frozen frame keeps its first row.
    """
    frozen = valid & (df["volume"] <= 0) & (df["close"] == df["close"].shift(1))
    out: list[int] = []
    for i in range(len(df) - 1, -1, -1):
        if not bool(frozen.iat[i]):
            break
        out.append(i)
    return tuple(sorted(out))


def _is_split_like(ratio: float) -> bool:
    if pd.isna(ratio):
        return False
    return any(abs(ratio - f) <= _SPLIT_TOL * f for f in _SPLIT_FACTORS)


def check_ohlcv(
    df: pd.DataFrame,
    *,
    return_outlier_pct: float = 0.5,
    series_ends_here: bool = False,
) -> DataQualityReport:
    """Inspect an OHLCV frame; return a DataQualityReport of positional indices.

    Pure: never mutates the input, never logs, never raises on dirty data.

    ``series_ends_here`` is the caller asserting that ``df``'s last row is the
    newest bar of the whole series, which is what makes ``frozen_tail_idx``
    meaningful. It defaults to **False** because the only production caller
    pages: ``data_sync.backfill`` hands over up to ``BARS_MAX_LIMIT`` bars at a
    time, so an intermediate page's last row is a paging boundary and not a tape
    that stopped. Defaulting to True would re-admit through that boundary
    exactly the mid-history false positives the run rule exists to exclude.
    """
    df = df.reset_index(drop=True)
    n = len(df)
    empty: tuple[int, ...] = ()
    if n == 0:
        return DataQualityReport(
            0, empty, empty, empty, empty, empty, empty, empty, empty, empty
        )

    nan_mask = df[_OHLCV_NUMERIC].isna().any(axis=1)
    nonpos_mask = (df[_PRICE_COLS] <= 0).any(axis=1) & ~nan_mask

    valid = ~nan_mask & ~nonpos_mask
    hi, lo, op, cl = df["high"], df["low"], df["open"], df["close"]
    bad_bar_mask = valid & ((hi < lo) | (hi < op) | (hi < cl) | (lo > op) | (lo > cl))

    dup_mask = df["open_time"].duplicated(keep="first")
    nonmono_mask = df["open_time"].diff() < 0

    zero_vol_mask = valid & (df["volume"] <= 0)
    ret = df["close"].pct_change()
    ratio = df["close"] / df["close"].shift(1)
    outlier_mask = valid & (ret.abs() > return_outlier_pct)
    split_mask = valid & ratio.apply(_is_split_like)

    frozen_tail = _trailing_frozen_idx(df, valid) if series_ends_here else empty

    return DataQualityReport(
        n_rows=n,
        nan_idx=_idx_tuple(nan_mask),
        nonpositive_price_idx=_idx_tuple(nonpos_mask),
        bad_bar_idx=_idx_tuple(bad_bar_mask),
        duplicate_time_idx=_idx_tuple(dup_mask),
        zero_volume_idx=_idx_tuple(zero_vol_mask),
        return_outlier_idx=_idx_tuple(outlier_mask),
        suspected_split_idx=_idx_tuple(split_mask),
        nonmonotonic_idx=_idx_tuple(nonmono_mask),
        frozen_tail_idx=frozen_tail,
    )


def quarantine(
    df: pd.DataFrame,
    report: DataQualityReport,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (clean, dropped) using ``report.quarantine_idx``.

    Indices are positional over a reset index — call with the same frame passed
    to ``check_ohlcv``. Never mutates the input.
    """
    df = df.reset_index(drop=True)
    drop = list(report.quarantine_idx)
    if not drop:
        return df, df.iloc[0:0].reset_index(drop=True)
    clean = df.drop(index=drop).reset_index(drop=True)
    dropped = df.loc[drop].reset_index(drop=True)
    return clean, dropped


def _et_date(open_time_ms: int) -> date:
    """America/New_York session date for a UTC epoch-ms ``open_time``.

    Daily/weekly bars are midnight-ET-in-UTC (04:00/05:00 UTC by DST); 4h bars
    are 13:30-UTC RTH bins. All map to the ET calendar date of the session.
    """
    return (
        pd.Timestamp(open_time_ms, unit="ms", tz="UTC")
        .tz_convert("America/New_York")
        .date()
    )


def _week_start(d: date) -> date:
    """Monday of ``d``'s ISO week (weekly-bar anchor key)."""
    return d - timedelta(days=d.weekday())


@dataclass(frozen=True)
class SessionGapReport:
    """Missing trading sessions for one (symbol, timeframe) OHLCV series.

    ``unit`` is ``"week"`` for ``1wk`` (gaps reported as the Monday of a skipped
    trading week) and ``"session"`` otherwise (gaps reported as the missing
    trading-day dates). Warn-only — a gap is absent data, never quarantined.
    """

    timeframe: str
    unit: str
    n_present: int
    n_expected: int
    missing: tuple[date, ...]

    @property
    def n_missing(self) -> int:
        return len(self.missing)

    @property
    def has_gaps(self) -> bool:
        return bool(self.missing)

    def summary(self) -> str:
        if not self.missing:
            return f"no session gaps ({self.n_present} {self.unit}s present)"
        sample = ", ".join(d.isoformat() for d in self.missing[:5])
        more = "" if self.n_missing <= 5 else f", +{self.n_missing - 5} more"
        return (
            f"{self.n_missing} {self.unit} gap(s) "
            f"({self.n_present}/{self.n_expected} present): {sample}{more}"
        )


def detect_session_gaps(
    open_times: Sequence[int],
    timeframe: str,
    sessions: Sequence[date],
) -> SessionGapReport:
    """Find expected trading sessions absent from ``open_times`` (pure).

    ``sessions`` is the NYSE trading-date list spanning the observed range
    (supplied by the caller via ``analytics.trading_calendar.nyse_sessions`` —
    this function never touches a calendar library). For ``1wk`` the unit is the
    trading *week* (Monday anchor); otherwise the trading *day*. Expected
    sessions are clamped to the observed ``[min, max]`` bar range so only interior
    gaps are flagged.
    """
    is_weekly = timeframe == "1wk"
    unit = "week" if is_weekly else "session"

    if not open_times:
        return SessionGapReport(timeframe, unit, 0, 0, ())

    present_dates = sorted({_et_date(t) for t in open_times})
    lo, hi = present_dates[0], present_dates[-1]
    in_range = [s for s in sessions if lo <= s <= hi]

    if is_weekly:
        present_keys = {_week_start(d) for d in present_dates}
        expected_keys = sorted({_week_start(s) for s in in_range})
    else:
        present_keys = set(present_dates)
        expected_keys = sorted(set(in_range))

    missing = tuple(k for k in expected_keys if k not in present_keys)
    return SessionGapReport(
        timeframe=timeframe,
        unit=unit,
        n_present=len(present_keys),
        n_expected=len(expected_keys),
        missing=missing,
    )
