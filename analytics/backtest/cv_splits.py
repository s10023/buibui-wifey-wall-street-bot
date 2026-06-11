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
