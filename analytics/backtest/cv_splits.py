"""Purged + embargoed K-fold CV splits for WFO parameter sweeps (Phase 0.3c).

López de Prado, *Advances in Financial Machine Learning*, ch. 7: K-fold CV on
financial series leaks information between train and test via (a) trades that
straddle a fold boundary and (b) serial correlation in the bars immediately
after a test fold. The fix here: backtest each train side as its own contiguous
segment (the engine censors boundary-straddling trades as ``outcome="open"``,
which excludes them from every closed-trade metric), optionally *purge* the
last ``purge_bars`` train bars before each test fold, and *embargo* the first
``embargo_bars`` train bars after it.

Pure splitting logic over OHLCV/signal DataFrames — no DB, no engine imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

CvMode = Literal["contiguous", "purged"]


@dataclass(frozen=True)
class CvConfig:
    """Cross-validation mode for ``run_param_sweep``.

    ``mode="contiguous"`` (default) reproduces the legacy single 70/30 split —
    ``run_param_sweep`` treats it identically to ``cv=None``, so sweep output
    stays byte-identical until ``"purged"`` is explicitly requested.
    """

    mode: CvMode = "contiguous"
    n_folds: int = 5
    purge_bars: int = 0
    embargo_bars: int | None = None  # None → max(1, round(1% of rows))

    def __post_init__(self) -> None:
        if self.n_folds < 2:
            raise ValueError(f"n_folds must be >= 2, got {self.n_folds}")
        if self.purge_bars < 0:
            raise ValueError(f"purge_bars must be >= 0, got {self.purge_bars}")
        if self.embargo_bars is not None and self.embargo_bars < 0:
            raise ValueError(f"embargo_bars must be >= 0, got {self.embargo_bars}")

    def resolve_embargo_bars(self, n_rows: int) -> int:
        """Explicit embargo when set; otherwise LdP's standard ~1% of rows."""
        if self.embargo_bars is not None:
            return self.embargo_bars
        return max(1, round(0.01 * n_rows))


def fold_bounds(n_rows: int, n_folds: int) -> list[tuple[int, int]]:
    """Contiguous [start, end) row bounds partitioning range(n_rows) into n_folds.

    The remainder is distributed one row at a time to the earliest folds, so
    fold sizes differ by at most 1 and the union covers every row exactly once.
    """
    if n_folds < 2:
        raise ValueError(f"n_folds must be >= 2, got {n_folds}")
    if n_rows < n_folds:
        raise ValueError(f"n_rows={n_rows} < n_folds={n_folds}")
    base, rem = divmod(n_rows, n_folds)
    bounds: list[tuple[int, int]] = []
    start = 0
    for k in range(n_folds):
        size = base + (1 if k < rem else 0)
        bounds.append((start, start + size))
        start += size
    return bounds


@dataclass(frozen=True)
class FoldSplit:
    """One CV fold: a test slice + up to two train segments (pre / post test).

    Each train segment is a contiguous ``(ohlcv, signals)`` pair so the backtest
    engine can run it standalone — trades that would straddle the segment end
    are censored as ``outcome="open"`` and never enter closed-trade metrics.
    """

    fold: int
    test_ohlcv: pd.DataFrame
    test_signals: pd.DataFrame
    train_segments: tuple[tuple[pd.DataFrame, pd.DataFrame], ...]


def _signals_in_window(
    signals: pd.DataFrame, ohlcv_slice: pd.DataFrame
) -> pd.DataFrame:
    """Signals whose open_time falls within the slice's [first, last] open_time."""
    if signals.empty or ohlcv_slice.empty:
        return signals.iloc[0:0].copy()
    lo = int(ohlcv_slice.iloc[0]["open_time"])
    hi = int(ohlcv_slice.iloc[-1]["open_time"])
    mask = (signals["open_time"] >= lo) & (signals["open_time"] <= hi)
    return signals[mask].copy()


def purged_kfold_split(
    ohlcv: pd.DataFrame, signals: pd.DataFrame, cfg: CvConfig
) -> list[FoldSplit]:
    """Split full-history OHLCV + signals into purged/embargoed K folds.

    Per fold k with test rows [t0, t1):
      - pre-test train  = rows [0, t0 - purge_bars)   (absent for the first fold)
      - post-test train = rows [t1 + embargo_bars, n) (absent for the last fold)

    Signals are assigned to a slice when their open_time falls inside it, so a
    signal landing in a purge or embargo zone is dropped from that fold
    entirely (it is still trained/tested in other folds).
    """
    n = len(ohlcv)
    embargo = cfg.resolve_embargo_bars(n)
    folds: list[FoldSplit] = []
    for k, (t0, t1) in enumerate(fold_bounds(n, cfg.n_folds)):
        test_ohlcv = ohlcv.iloc[t0:t1].copy()
        segments: list[tuple[pd.DataFrame, pd.DataFrame]] = []
        pre_end = max(0, t0 - cfg.purge_bars)
        if pre_end > 0:
            pre = ohlcv.iloc[0:pre_end].copy()
            segments.append((pre, _signals_in_window(signals, pre)))
        post_start = t1 + embargo
        if post_start < n:
            post = ohlcv.iloc[post_start:n].copy()
            segments.append((post, _signals_in_window(signals, post)))
        folds.append(
            FoldSplit(
                fold=k,
                test_ohlcv=test_ohlcv,
                test_signals=_signals_in_window(signals, test_ohlcv),
                train_segments=tuple(segments),
            )
        )
    return folds
