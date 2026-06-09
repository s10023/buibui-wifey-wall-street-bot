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

# canonical price ratios (new/old close) that signal an unadjusted split
_SPLIT_FACTORS: tuple[float, ...] = (0.5, 1.0 / 3.0, 0.25, 0.2, 2.0, 3.0, 4.0, 5.0)
_SPLIT_TOL: float = 0.05  # within ±5% (relative) of a canonical factor


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


def _is_split_like(ratio: float) -> bool:
    if pd.isna(ratio):
        return False
    return any(abs(ratio - f) <= _SPLIT_TOL * f for f in _SPLIT_FACTORS)


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

    dup_mask = df["open_time"].duplicated(keep="first")
    nonmono_mask = df["open_time"].diff() < 0

    zero_vol_mask = valid & (df["volume"] <= 0)
    ret = df["close"].pct_change()
    ratio = df["close"] / df["close"].shift(1)
    outlier_mask = valid & (ret.abs() > return_outlier_pct)
    split_mask = valid & ratio.apply(_is_split_like)

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
    )
