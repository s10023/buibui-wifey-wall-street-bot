"""OHLCV data-quality monitor — detection (pure) + quarantine helper.

No DB, no network, no side effects. `check_ohlcv` returns a typed report of
integrity problems by positional row index (0..n-1 over a reset index).
`quarantine` (Task 4) drops only the unambiguously-bad rows.

Calendar-aware gap detection is intentionally out of scope: equity
weekends/holidays make naive cadence checks fire constantly without a trading
calendar. We flag only unambiguous timestamp anomalies (duplicates,
non-monotonic order — Task 2).
"""

from dataclasses import dataclass

import pandas as pd

_PRICE_COLS = ["open", "high", "low", "close"]
_OHLCV_NUMERIC = _PRICE_COLS + ["volume"]


@dataclass(frozen=True)
class DataQualityReport:
    """Per-frame integrity findings, as positional row indices.

    Quarantine sets (dropped before storage): nan, non-positive price, broken
    bar geometry, later-duplicate timestamps. Warn-only sets (logged, kept):
    zero volume, return outliers, suspected splits, non-monotonic timestamps.
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

    @property
    def quarantine_idx(self) -> tuple[int, ...]:
        s = (
            set(self.nan_idx)
            | set(self.nonpositive_price_idx)
            | set(self.bad_bar_idx)
            | set(self.duplicate_time_idx)
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


def check_ohlcv(
    df: pd.DataFrame,
    *,
    return_outlier_pct: float = 0.5,
) -> DataQualityReport:
    """Inspect an OHLCV frame; return a DataQualityReport of positional indices.

    Pure: never mutates the input, never logs, never raises on dirty data.
    """
    df = df.reset_index(drop=True)
    n = len(df)
    empty: tuple[int, ...] = ()
    if n == 0:
        return DataQualityReport(0, empty, empty, empty, empty, empty, empty, empty, empty)

    nan_mask = df[_OHLCV_NUMERIC].isna().any(axis=1)
    nonpos_mask = (df[_PRICE_COLS] <= 0).any(axis=1) & ~nan_mask

    valid = ~nan_mask & ~nonpos_mask
    hi, lo, op, cl = df["high"], df["low"], df["open"], df["close"]
    bad_bar_mask = valid & ((hi < lo) | (hi < op) | (hi < cl) | (lo > op) | (lo > cl))

    return DataQualityReport(
        n_rows=n,
        nan_idx=_idx_tuple(nan_mask),
        nonpositive_price_idx=_idx_tuple(nonpos_mask),
        bad_bar_idx=_idx_tuple(bad_bar_mask),
        duplicate_time_idx=empty,
        zero_volume_idx=empty,
        return_outlier_idx=empty,
        suspected_split_idx=empty,
        nonmonotonic_idx=empty,
    )
