# Phase 0.3c Purged + Embargoed CV Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add purged + embargoed K-fold cross-validation (López de Prado, *Advances in Financial ML* ch. 7) to `run_param_sweep`, behind a `cv` flag whose default reproduces today's contiguous 70/30 split byte-identically — goldens unmoved until explicitly enabled.

**Architecture:** A new pure module `analytics/backtest/cv_splits.py` (`CvConfig`, `FoldSplit`, `fold_bounds`, `purged_kfold_split` — pandas only, no DB, no engine imports) produces K disjoint test folds, each paired with up to two contiguous train segments (pre-test with optional purge, post-test with embargo). `param_sweep.py` gains a CV grid worker that backtests each segment standalone — the engine already censors boundary-straddling trades as `outcome="open"` (excluded from all metrics), so per-segment truncation IS the mechanical purge; explicit `purge_bars`/`embargo_bars` handle stub-bias and serial correlation on top. IS = train trades pooled across folds, deduped by `(signal_time, direction)`; OOS = disjoint test folds pooled directly. CLI: `wifey param-sweep --cv-mode purged`.

**Tech Stack:** Python 3.11, pandas, dataclasses, ProcessPoolExecutor (existing), pytest + unittest.mock. mypy strict, ruff.

---

## Context

- Spec: `docs/superpowers/specs/2026-06-05-phase0-correctness-foundation-design.md` §4.3 (0.3c): "Replace/augment `_split_ohlcv` with a purged K-fold that drops trades straddling each fold boundary and applies an embargo of *k* bars after each test fold… Behind a `cv_mode` flag (default = legacy contiguous split → goldens unmoved)."
- Branch: `feat/phase0-purged-cv` (already created and checked out, clean on top of main `c845e81`).
- 0.3a/b (DSR + PBO, `analytics/backtest/stats_overfit.py`) shipped in PR #73 — this completes the 0.3 overfitting cluster.
- Sweep output is NOT in the regression goldens (`tests/test_regression.py` extracts metric fields explicitly) — `make test-regression` must stay green with no `regression-update`.
- `run_strategy_audit` and `tools/multi_symbol_wfo.py` stay contiguous-only (out of scope; TA sweeps are frozen anyway — this is research-integrity infrastructure, not a tuning run).
- The module-level CLI in `analytics/param_sweep.py` (`python -m analytics.param_sweep`) stays contiguous-only; `wifey param-sweep` (`cli/param.py`) is the primary entry and gets the flags.

**Why `purge_bars` defaults to 0:** the engine marks trades unresolved at end-of-data as `outcome="open"` and `BacktestResult.closed_trades` excludes them from every metric. Backtesting each train segment as its own contiguous slice therefore already censors any trade whose resolution would straddle into the test fold — no peeking. The embargo (default ~1% of rows, LdP's standard) is the real addition: it stops serial correlation in the bars immediately after a test fold from leaking into the post-test train segment. `purge_bars` remains available for extra conservatism.

**Known approximation (documented, deliberate):** in CV mode the IS pool (deduped train trades) and the OOS pool (test folds) overlap in time — the same trade appears in both. Feeding both into the CSCV/PBO matrix would double-count every trade, so `_attach_overfit_stats` gains a `pooled_oos_only` switch: in CV mode the PBO matrix is built from the OOS test folds alone (they tile the full history exactly once — the natural CSCV input). DSR still uses the IS Sharpe across trials, as designed.

## File Structure

| File | Action | Responsibility |
| ---- | ------ | -------------- |
| `analytics/backtest/cv_splits.py` | Create | Pure fold geometry: `CvConfig`, `FoldSplit`, `fold_bounds`, `purged_kfold_split` |
| `analytics/param_sweep.py` | Modify | `_row_from_results` (extracted scoring tail), `_dedup_trades`, `_sweep_grid_worker_cv`, `run_param_sweep(cv=...)` wiring, `_attach_overfit_stats(pooled_oos_only=...)` |
| `cli/param.py` | Modify | `--cv-mode / --cv-folds / --cv-purge-bars / --cv-embargo-bars` flags + `_cv_from_args` |
| `tests/test_cv_splits.py` | Create | Fold bounds, config validation, purge/embargo geometry, signal assignment |
| `tests/test_param_sweep.py` | Modify | `_dedup_trades`, `_row_from_results`, CV worker, `run_param_sweep` integration |
| `tests/test_cli_param_cv.py` | Create | CLI flag defaults + `CvConfig` construction |
| `CLAUDE.md`, `README.md`, `.claude/context/analytics.md` | Modify | Docs sync |

---

### Task 1: `CvConfig` + `fold_bounds` (new module scaffolding)

**Files:**

- Create: `analytics/backtest/cv_splits.py`
- Create: `tests/test_cv_splits.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_splits.py`:

```python
"""Tests for analytics/backtest/cv_splits.py — Phase 0.3c purged+embargoed K-fold CV."""

from __future__ import annotations

import pytest

from analytics.backtest.cv_splits import CvConfig, fold_bounds


class TestFoldBounds:
    def test_partitions_evenly_divisible_rows(self) -> None:
        assert fold_bounds(100, 5) == [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]

    def test_distributes_remainder_to_earliest_folds(self) -> None:
        assert fold_bounds(102, 5) == [(0, 21), (21, 42), (42, 62), (62, 82), (82, 102)]

    def test_bounds_partition_every_row_exactly_once(self) -> None:
        bounds = fold_bounds(97, 4)
        covered = [i for start, end in bounds for i in range(start, end)]
        assert covered == list(range(97))

    def test_rejects_single_fold(self) -> None:
        with pytest.raises(ValueError, match="n_folds"):
            fold_bounds(100, 1)

    def test_rejects_fewer_rows_than_folds(self) -> None:
        with pytest.raises(ValueError, match="n_rows"):
            fold_bounds(3, 5)


class TestCvConfig:
    def test_defaults(self) -> None:
        cfg = CvConfig()
        assert cfg.mode == "contiguous"
        assert cfg.n_folds == 5
        assert cfg.purge_bars == 0
        assert cfg.embargo_bars is None

    def test_rejects_bad_n_folds(self) -> None:
        with pytest.raises(ValueError, match="n_folds"):
            CvConfig(n_folds=1)

    def test_rejects_negative_purge(self) -> None:
        with pytest.raises(ValueError, match="purge_bars"):
            CvConfig(purge_bars=-1)

    def test_rejects_negative_embargo(self) -> None:
        with pytest.raises(ValueError, match="embargo_bars"):
            CvConfig(embargo_bars=-1)

    def test_resolve_embargo_explicit_passthrough(self) -> None:
        assert CvConfig(embargo_bars=7).resolve_embargo_bars(1000) == 7

    def test_resolve_embargo_defaults_to_one_percent(self) -> None:
        assert CvConfig().resolve_embargo_bars(1000) == 10

    def test_resolve_embargo_floors_at_one_bar(self) -> None:
        assert CvConfig().resolve_embargo_bars(50) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_cv_splits.py -v`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'analytics.backtest.cv_splits'`

- [ ] **Step 3: Write the minimal implementation**

Create `analytics/backtest/cv_splits.py`:

```python
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
```

(Note: `pd` is imported now but only used from Task 2 — if ruff flags the unused
import at this step, defer the `import pandas as pd` line to Task 2 instead.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_cv_splits.py -v`
Expected: all 12 tests PASS

- [ ] **Step 5: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: clean (if ruff flagged the unused `pandas` import in Step 3, remove it and re-run)

- [ ] **Step 6: Commit**

```bash
git add analytics/backtest/cv_splits.py tests/test_cv_splits.py
git commit -m "feat(cv): CvConfig + fold_bounds — purged K-fold scaffolding (Phase 0.3c)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `purged_kfold_split` + `FoldSplit`

**Files:**

- Modify: `analytics/backtest/cv_splits.py`
- Modify: `tests/test_cv_splits.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_cv_splits.py`, extend the imports and add helpers + a test class:

```python
import pandas as pd

from analytics.backtest.cv_splits import (
    CvConfig,
    fold_bounds,
    purged_kfold_split,
)

_TF_MS = 3_600_000  # 1h bars


def _make_ohlcv(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * _TF_MS for i in range(n)],
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [1_000.0] * n,
        }
    )


def _make_signals(rows: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [r * _TF_MS for r in rows],
            "direction": ["long"] * len(rows),
            "reason": ["test"] * len(rows),
        }
    )
```

```python
class TestPurgedKfoldSplit:
    def test_produces_n_folds_with_disjoint_full_coverage_tests(self) -> None:
        ohlcv = _make_ohlcv(100)
        cfg = CvConfig(mode="purged", n_folds=5, embargo_bars=2)
        folds = purged_kfold_split(ohlcv, _make_signals([]), cfg)
        assert len(folds) == 5
        assert [f.fold for f in folds] == [0, 1, 2, 3, 4]
        recombined = pd.concat([f.test_ohlcv for f in folds], ignore_index=True)
        pd.testing.assert_frame_equal(recombined, ohlcv)

    def test_first_fold_has_no_pre_train_segment(self) -> None:
        cfg = CvConfig(mode="purged", embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), _make_signals([]), cfg)
        # fold 0 test = rows [0, 20) → only the post-test segment survives
        assert len(folds[0].train_segments) == 1
        post_ohlcv, _ = folds[0].train_segments[0]
        assert int(post_ohlcv.iloc[0]["open_time"]) == 22 * _TF_MS  # 20 + embargo 2

    def test_last_fold_has_no_post_train_segment(self) -> None:
        cfg = CvConfig(mode="purged", embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), _make_signals([]), cfg)
        # last fold test = rows [80, 100) → only the pre-test segment survives
        assert len(folds[-1].train_segments) == 1
        pre_ohlcv, _ = folds[-1].train_segments[0]
        assert int(pre_ohlcv.iloc[-1]["open_time"]) == 79 * _TF_MS

    def test_purge_trims_pre_test_segment_tail(self) -> None:
        cfg = CvConfig(mode="purged", purge_bars=3, embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), _make_signals([]), cfg)
        # fold 2 test = [40, 60) → pre segment = [0, 37) after purging 3 bars
        pre_ohlcv, _ = folds[2].train_segments[0]
        assert int(pre_ohlcv.iloc[-1]["open_time"]) == 36 * _TF_MS

    def test_embargo_trims_post_test_segment_head(self) -> None:
        cfg = CvConfig(mode="purged", purge_bars=3, embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), _make_signals([]), cfg)
        # fold 2 test = [40, 60) → post segment starts at row 62
        post_ohlcv, _ = folds[2].train_segments[1]
        assert int(post_ohlcv.iloc[0]["open_time"]) == 62 * _TF_MS

    def test_signals_assigned_to_test_and_train_windows(self) -> None:
        signals = _make_signals([10, 38, 41, 61, 79])
        cfg = CvConfig(mode="purged", purge_bars=3, embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), signals, cfg)
        fold2 = folds[2]  # test [40, 60); pre [0, 37); post [62, 100)
        assert list(fold2.test_signals["open_time"]) == [41 * _TF_MS]
        (_, pre_sigs), (_, post_sigs) = fold2.train_segments
        assert list(pre_sigs["open_time"]) == [10 * _TF_MS]
        assert list(post_sigs["open_time"]) == [79 * _TF_MS]

    def test_signals_in_purge_and_embargo_zones_dropped_from_fold(self) -> None:
        # row 38 sits in fold 2's purge zone [37, 40); row 61 in its embargo zone [60, 62)
        signals = _make_signals([38, 61])
        cfg = CvConfig(mode="purged", purge_bars=3, embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), signals, cfg)
        fold2 = folds[2]
        in_fold = [
            *fold2.test_signals["open_time"],
            *(ot for _, sigs in fold2.train_segments for ot in sigs["open_time"]),
        ]
        assert in_fold == []

    def test_each_signal_lands_in_exactly_one_test_fold(self) -> None:
        signals = _make_signals([0, 19, 20, 55, 99])
        cfg = CvConfig(mode="purged", embargo_bars=1)
        folds = purged_kfold_split(_make_ohlcv(100), signals, cfg)
        pooled = sorted(ot for f in folds for ot in f.test_signals["open_time"])
        assert pooled == sorted(signals["open_time"])

    def test_empty_signals_produce_empty_signal_frames(self) -> None:
        cfg = CvConfig(mode="purged", n_folds=2, embargo_bars=1)
        folds = purged_kfold_split(_make_ohlcv(50), _make_signals([]), cfg)
        assert all(f.test_signals.empty for f in folds)
        assert all(sigs.empty for f in folds for _, sigs in f.train_segments)

    def test_default_embargo_uses_one_percent_of_rows(self) -> None:
        cfg = CvConfig(mode="purged")
        folds = purged_kfold_split(_make_ohlcv(500), _make_signals([]), cfg)
        # 1% of 500 = 5 bars: fold 0 test = [0, 100) → post segment starts at row 105
        post_ohlcv, _ = folds[0].train_segments[0]
        assert int(post_ohlcv.iloc[0]["open_time"]) == 105 * _TF_MS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_cv_splits.py -v`
Expected: ImportError — `cannot import name 'purged_kfold_split'`

- [ ] **Step 3: Write the implementation**

Append to `analytics/backtest/cv_splits.py` (ensure `import pandas as pd` is present at the top):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_cv_splits.py -v`
Expected: all 22 tests PASS

- [ ] **Step 5: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add analytics/backtest/cv_splits.py tests/test_cv_splits.py
git commit -m "feat(cv): purged_kfold_split + FoldSplit train/test segments

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Extract `_row_from_results` + add `_dedup_trades` (param_sweep refactor)

**Files:**

- Modify: `analytics/param_sweep.py`
- Modify: `tests/test_param_sweep.py`

This is a behaviour-preserving refactor (the legacy worker's scoring tail moves into a shared helper) plus one new pure helper. The full suite passing unchanged is the refactor's proof.

- [ ] **Step 1: Write the failing tests**

In `tests/test_param_sweep.py`:

(a) Extend the import block:

```python
import math

import pytest

from analytics.param_sweep import (
    AuditRow,
    SweepRow,
    _audit_strategy_worker,
    _dedup_trades,
    _directional_split_hint,
    _row_from_results,
    _score,
    _sweep_grid_worker,
    format_audit_results,
    format_sweep_results,
)
```

(b) Give `_win` / `_loss` a `signal_time` kwarg (default keeps every existing test working) and add an `_open_trade` helper. Replace the two helpers with:

```python
def _win(direction: str, r: float = 1.0, signal_time: int = _BASE_TIME) -> Trade:
    entry = 100.0
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tp = entry + r * 5.0 if direction == "long" else entry - r * 5.0
    t = Trade(
        signal_time=signal_time,
        entry_time=signal_time + 1,
        entry_price=entry,
        direction=direction,
        sl_price=sl,
        tp_price=tp,
        fee_pct=0.0,
    )
    t.outcome = "win"
    t.exit_price = tp
    return t


def _loss(direction: str, signal_time: int = _BASE_TIME) -> Trade:
    entry = 100.0
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tp = entry + 10.0 if direction == "long" else entry - 10.0
    t = Trade(
        signal_time=signal_time,
        entry_time=signal_time + 1,
        entry_price=entry,
        direction=direction,
        sl_price=sl,
        tp_price=tp,
        fee_pct=0.0,
    )
    t.outcome = "loss"
    t.exit_price = sl
    return t


def _open_trade(direction: str, signal_time: int = _BASE_TIME) -> Trade:
    """An unresolved trade (outcome='open') — what segment truncation produces."""
    entry = 100.0
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tp = entry + 10.0 if direction == "long" else entry - 10.0
    return Trade(
        signal_time=signal_time,
        entry_time=signal_time + 1,
        entry_price=entry,
        direction=direction,
        sl_price=sl,
        tp_price=tp,
        fee_pct=0.0,
    )
```

(c) Add the test classes:

```python
class TestDedupTrades:
    def test_keeps_distinct_signal_times(self) -> None:
        a = _win("long", signal_time=_BASE_TIME + 1)
        b = _win("long", signal_time=_BASE_TIME + 2)
        assert _dedup_trades([a, b]) == [a, b]

    def test_keeps_both_directions_at_same_signal_time(self) -> None:
        a, b = _win("long"), _win("short")
        assert _dedup_trades([a, b]) == [a, b]

    def test_collapses_duplicate_resolved_instances(self) -> None:
        assert len(_dedup_trades([_win("long"), _win("long")])) == 1

    def test_prefers_resolved_over_earlier_open(self) -> None:
        o, w = _open_trade("long"), _win("long")
        assert _dedup_trades([o, w]) == [w]

    def test_keeps_resolved_over_later_open(self) -> None:
        w, o = _win("long"), _open_trade("long")
        assert _dedup_trades([w, o]) == [w]

    def test_preserves_first_appearance_order(self) -> None:
        t3 = _win("long", signal_time=_BASE_TIME + 3)
        t1 = _win("long", signal_time=_BASE_TIME + 1)
        t2 = _win("long", signal_time=_BASE_TIME + 2)
        assert _dedup_trades([t3, t1, t2]) == [t3, t1, t2]


class TestRowFromResults:
    def test_scores_and_decay_match_score_function(self) -> None:
        bt_is = _make_result(long_trades=[_win("long", 2.0), _win("long", 2.0)])
        bt_oos = _make_result(long_trades=[_win("long", 2.0)])
        row = _row_from_results({"tp_r": 2.0}, bt_is, bt_oos, is_min=1)
        assert row.is_score == pytest.approx(_score(bt_is, 1))
        assert row.oos_score == pytest.approx(_score(bt_oos, 1))
        assert row.decay == pytest.approx(row.oos_score / row.is_score)
        assert not row.overfit

    def test_negative_oos_flags_overfit(self) -> None:
        bt_is = _make_result(long_trades=[_win("long", 2.0), _win("long", 2.0)])
        bt_oos = _make_result(long_trades=[_loss("long")])
        row = _row_from_results({"tp_r": 2.0}, bt_is, bt_oos, is_min=1)
        assert row.overfit

    def test_zero_is_score_gives_nan_decay(self) -> None:
        bt_is = _make_result()  # no trades → score 0
        bt_oos = _make_result(long_trades=[_win("long", 2.0)])
        row = _row_from_results({"tp_r": 2.0}, bt_is, bt_oos, is_min=1)
        assert math.isnan(row.decay)
        assert not row.overfit  # positive OOS + NaN decay → not flagged (legacy semantics)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_param_sweep.py -v`
Expected: ImportError — `cannot import name '_dedup_trades'`

- [ ] **Step 3: Implement**

In `analytics/param_sweep.py`:

(a) Change the import at line 42 to include `Trade`:

```python
from analytics.backtest_lib import BacktestResult, Trade, run_backtest
```

(b) Insert these two helpers directly above `def _sweep_grid_worker(` (under the "Core sweep logic" section header):

```python
def _row_from_results(
    params: dict[str, Any],
    bt_is: BacktestResult,
    bt_oos: BacktestResult,
    is_min: int,
) -> SweepRow:
    """Score an IS/OOS result pair into a SweepRow (shared by both grid workers)."""
    is_s = _score(bt_is, is_min)
    oos_s = _score(bt_oos, 1)
    decay = (oos_s / is_s) if is_s > 0 else float("nan")
    oos_avg_r = bt_oos.avg_r
    overfit = (oos_avg_r is None or oos_avg_r <= 0) or (
        not math.isnan(decay) and decay < 0.4
    )
    return SweepRow(
        params=params,
        is_result=bt_is,
        oos_result=bt_oos,
        is_score=is_s,
        oos_score=oos_s,
        decay=decay,
        overfit=overfit,
    )


def _dedup_trades(trades: list[Trade]) -> list[Trade]:
    """Collapse duplicate (signal_time, direction) trades across CV folds.

    The same signal appears in the train segments of several folds. Backtesting
    each segment independently yields identical resolved trades (same bars,
    same params — the engine is deterministic), except when a fold layout cuts
    a segment short and censors the trade as outcome="open". Keep one instance
    per key, preferring a resolved one; order follows first appearance.
    """
    by_key: dict[tuple[int, str], Trade] = {}
    order: list[tuple[int, str]] = []
    for t in trades:
        key = (t.signal_time, t.direction)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = t
            order.append(key)
        elif existing.outcome == "open" and t.outcome != "open":
            by_key[key] = t
    return [by_key[k] for k in order]
```

(c) Replace the scoring tail of `_sweep_grid_worker` (everything from `is_s = _score(bt_is, is_min)` through the closing `)` of the `return SweepRow(...)`) with:

```python
    return _row_from_results(params, bt_is, bt_oos, is_min)
```

- [ ] **Step 4: Run the full suite to verify the refactor is behaviour-preserving**

Run: `poetry run pytest tests/ -x -q`
Expected: all pass (~1387 pass / 3 skip + the new tests)

- [ ] **Step 5: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add analytics/param_sweep.py tests/test_param_sweep.py
git commit -m "refactor(sweep): extract _row_from_results + add _dedup_trades

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `_sweep_grid_worker_cv` (purged-CV grid worker)

**Files:**

- Modify: `analytics/param_sweep.py`
- Modify: `tests/test_param_sweep.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_param_sweep.py`, add to the imports:

```python
from analytics.backtest.cv_splits import FoldSplit
from analytics.param_sweep import _sweep_grid_worker_cv
```

(fold `_sweep_grid_worker_cv` into the existing `from analytics.param_sweep import (...)` block)

Add the test class:

```python
class TestSweepGridWorkerCv:
    def test_pools_test_folds_and_dedups_train_segments(self) -> None:
        ohlcv = pd.DataFrame({"open_time": [0]})
        sigs = pd.DataFrame({"open_time": [0]})
        seg = (ohlcv, sigs)
        folds = [
            FoldSplit(fold=0, test_ohlcv=ohlcv, test_signals=sigs, train_segments=(seg,)),
            FoldSplit(fold=1, test_ohlcv=ohlcv, test_signals=sigs, train_segments=(seg,)),
        ]
        dup_open = _open_trade("long")  # censored instance of a train signal
        dup_resolved = _win("long")  # resolved instance of the same (signal_time, dir)
        test_a = _win("long", signal_time=_BASE_TIME + 10)
        test_b = _loss("long", signal_time=_BASE_TIME + 20)
        # worker call order: fold0 train seg → fold0 test → fold1 train seg → fold1 test
        results = [
            _make_result(long_trades=[dup_open]),
            _make_result(long_trades=[test_a]),
            _make_result(long_trades=[dup_resolved]),
            _make_result(long_trades=[test_b]),
        ]
        with patch("analytics.param_sweep.run_backtest", side_effect=results) as mock_bt:
            row = _sweep_grid_worker_cv(
                {"tp_r": 2.0},
                folds,
                "AAPL",
                "1d",
                "bos",
                0.0,
                1,
            )
        assert mock_bt.call_count == 4
        assert row.params == {"tp_r": 2.0}
        assert row.is_result.symbol == "AAPL"
        # open+resolved duplicates collapse to the single resolved instance
        assert row.is_trades == 1
        assert row.is_result.trades == [dup_resolved]
        # disjoint test folds pool directly — no dedup
        assert row.oos_trades == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_param_sweep.py::TestSweepGridWorkerCv -v`
Expected: ImportError — `cannot import name '_sweep_grid_worker_cv'`

- [ ] **Step 3: Implement**

In `analytics/param_sweep.py`:

(a) Add the cv_splits import after line 33 (before the `analytics.backtest.stats_overfit` import block — `cv_splits` sorts first alphabetically):

```python
from analytics.backtest.cv_splits import CvConfig, FoldSplit, purged_kfold_split
```

(`CvConfig` and `purged_kfold_split` are consumed in Task 5; if ruff flags them
as unused at this step, import only `FoldSplit` now and extend the line in Task 5.)

(b) Insert directly below `_sweep_grid_worker` (after its `return _row_from_results(...)` line):

```python
def _sweep_grid_worker_cv(
    params: dict[str, Any],
    folds: list[FoldSplit],
    symbol: str,
    timeframe: str,
    strategy: str,
    fee_pct: float,
    is_min: int,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    *,
    live_parity: LiveParityConfig | None = None,
    bias_cfg: BiasConfig | None = None,
    regime_series: pd.Series | None = None,
    strategy_params: dict[str, StrategyOverride] | None = None,
    htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None = None,
) -> SweepRow:
    """Purged-CV grid worker — module-level so ProcessPoolExecutor can pickle it.

    Per fold: backtest each train segment standalone (pooled into the IS trade
    list) and the test fold (pooled into OOS). Per-segment truncation censors
    boundary-straddling trades as outcome="open" — the mechanical purge. Test
    folds are disjoint so OOS pools directly; train segments overlap across
    folds, so IS trades are deduped by (signal_time, direction), preferring
    resolved instances, then sorted chronologically.
    """
    tp_r = float(params.get("tp_r", 2.0))
    sl_pct = float(params.get("sl_pct", 0.02))

    def _bt(ohlcv: pd.DataFrame, signals: pd.DataFrame) -> BacktestResult:
        return run_backtest(
            ohlcv,
            signals,
            symbol,
            timeframe,
            strategy,
            sl_pct=sl_pct,
            tp_r=tp_r,
            fee_pct=fee_pct,
            atr_sl_multiplier=atr_sl_multiplier,
            atr_sl_floor=atr_sl_floor,
            live_parity=live_parity,
            bias_cfg=bias_cfg,
            regime_series=regime_series,
            strategy_params=strategy_params,
            htf_slope_series_by_anchor=htf_slope_series_by_anchor,
        )

    train_trades: list[Trade] = []
    test_trades: list[Trade] = []
    for fold in folds:
        for seg_ohlcv, seg_signals in fold.train_segments:
            train_trades.extend(_bt(seg_ohlcv, seg_signals).trades)
        test_trades.extend(_bt(fold.test_ohlcv, fold.test_signals).trades)

    deduped = _dedup_trades(train_trades)
    deduped.sort(key=lambda t: (t.entry_time, t.signal_time))
    bt_is = BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        fee_pct=fee_pct,
        trades=deduped,
    )
    bt_oos = BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        fee_pct=fee_pct,
        trades=test_trades,
    )
    return _row_from_results(params, bt_is, bt_oos, is_min)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_param_sweep.py -v`
Expected: all PASS

- [ ] **Step 5: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: clean (adjust the (a) import per its note if ruff flags unused names)

- [ ] **Step 6: Commit**

```bash
git add analytics/param_sweep.py tests/test_param_sweep.py
git commit -m "feat(sweep): _sweep_grid_worker_cv purged-CV grid worker

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Wire `cv` into `run_param_sweep` (default = legacy, byte-identical)

**Files:**

- Modify: `analytics/param_sweep.py:370-535` (`run_param_sweep`), `analytics/param_sweep.py:327` (`_attach_overfit_stats`)
- Modify: `tests/test_param_sweep.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_param_sweep.py`, extend imports:

```python
from unittest.mock import MagicMock, patch

from analytics.backtest.cv_splits import CvConfig, FoldSplit
from analytics.param_sweep import ParamRange, run_param_sweep
from tests.conftest import _candle, _make_ohlcv
```

(fold the names into the existing import blocks; `patch` is already imported)

Note: `tests.conftest._make_ohlcv` takes a list of `_candle(...)` dicts — it is a
different helper from the local `_make_ohlcv(n)` in `tests/test_cv_splits.py`.

Add the test class:

```python
class TestRunParamSweepCv:
    """Integration: real engine + ProcessPoolExecutor; DB + detection mocked out.

    Synthetic series: every candle opens at 100 with high 103 / low 99, so a
    long with sl_pct=0.02 (sl 98) and tp_r ≤ 1.0 (tp ≤ 102) wins on its entry
    bar and never touches the SL — fully deterministic fills.
    """

    def _ohlcv(self, n: int = 60) -> pd.DataFrame:
        return _make_ohlcv(
            [
                _candle(_BASE_TIME + i * 3_600_000, 100.0, 103.0, 99.0, 100.0)
                for i in range(n)
            ]
        )

    def _signals(self, rows: list[int]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "open_time": [_BASE_TIME + r * 3_600_000 for r in rows],
                "direction": ["long"] * len(rows),
                "reason": ["test"] * len(rows),
            }
        )

    @patch("analytics.param_sweep.detect_signals_for_strategy")
    @patch("analytics.param_sweep.get_ohlcv")
    def test_purged_cv_pools_train_and_test_folds(
        self, mock_ohlcv: Any, mock_detect: Any
    ) -> None:
        mock_ohlcv.return_value = self._ohlcv()
        mock_detect.return_value = self._signals([5, 12, 19, 26, 33, 40, 46, 54])
        rows = run_param_sweep(
            conn=MagicMock(),
            strategy="bos",
            symbol="AAPL",
            timeframe="1h",
            days=30,
            param_ranges=[ParamRange("tp_r", [1.0])],
            wfo_split=0.7,
            min_trades=2,
            fee_pct=0.0,
            top_n=5,
            cv=CvConfig(mode="purged", n_folds=5, embargo_bars=1),
        )
        assert len(rows) == 1
        row = rows[0]
        # ≥6 of the 8 signals resolve inside their fold/segment (a signal whose
        # entry or resolution bar falls past a slice end is censored — fine)
        assert row.oos_trades >= 6
        assert row.oos_win_rate == 1.0
        assert row.is_trades >= 6
        assert row.is_win_rate == 1.0
        assert not row.overfit
        assert row.overfit_stats is not None

    @patch("analytics.param_sweep.detect_signals_for_strategy")
    @patch("analytics.param_sweep.get_ohlcv")
    def test_contiguous_cv_config_matches_cv_none(
        self, mock_ohlcv: Any, mock_detect: Any
    ) -> None:
        mock_ohlcv.return_value = self._ohlcv()
        mock_detect.return_value = self._signals([5, 12, 19, 26, 33, 40, 46, 54])
        kwargs: dict[str, Any] = dict(
            strategy="bos",
            symbol="AAPL",
            timeframe="1h",
            days=30,
            param_ranges=[ParamRange("tp_r", [0.5, 1.0])],
            wfo_split=0.7,
            min_trades=2,
            fee_pct=0.0,
            top_n=5,
        )
        legacy = run_param_sweep(conn=MagicMock(), **kwargs)
        contiguous = run_param_sweep(
            conn=MagicMock(), cv=CvConfig(mode="contiguous"), **kwargs
        )
        assert [r.params for r in legacy] == [r.params for r in contiguous]
        assert [r.is_score for r in legacy] == [r.is_score for r in contiguous]
        assert [r.oos_score for r in legacy] == [r.oos_score for r in contiguous]
        assert [r.decay for r in legacy] == [r.decay for r in contiguous]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_param_sweep.py::TestRunParamSweepCv -v`
Expected: FAIL — `run_param_sweep() got an unexpected keyword argument 'cv'`

- [ ] **Step 3: Implement**

All edits in `analytics/param_sweep.py`. If Task 4 imported only `FoldSplit`,
extend that import line now to:

```python
from analytics.backtest.cv_splits import CvConfig, FoldSplit, purged_kfold_split
```

(a) **Signature** — add `cv` as the last keyword-only param of `run_param_sweep`:

```python
    htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None = None,
    cv: CvConfig | None = None,
) -> list[SweepRow]:
```

and append to the docstring (after the `regime_series` paragraph):

```python
    ``cv`` selects the walk-forward geometry (Phase 0.3c): ``None`` or
    ``mode="contiguous"`` keeps the legacy single ``wfo_split`` IS/OOS split
    (byte-identical output); ``mode="purged"`` runs purged+embargoed K-fold CV
    (LdP AFML ch. 7) — IS = train segments pooled across folds and deduped by
    (signal_time, direction), OOS = the disjoint test folds pooled directly.
```

(b) **Split guard** — replace:

```python
    ohlcv_is, ohlcv_oos = _split_ohlcv(ohlcv_full, wfo_split)
```

with:

```python
    cv_active = cv if cv is not None and cv.mode == "purged" else None
    ohlcv_is = pd.DataFrame()
    ohlcv_oos = pd.DataFrame()
    if cv_active is None:
        ohlcv_is, ohlcv_oos = _split_ohlcv(ohlcv_full, wfo_split)
```

(c) **Signal split / fold build** — replace:

```python
    # Split signals at the same timestamp boundary as OHLCV.
    if not ohlcv_is.empty and not ohlcv_oos.empty:
        split_ts = int(ohlcv_oos.iloc[0]["open_time"])
        signals_is = signals_full[signals_full["open_time"] < split_ts].copy()
        signals_oos = signals_full[signals_full["open_time"] >= split_ts].copy()
    else:
        signals_is = signals_full.copy()
        signals_oos = pd.DataFrame()
```

with:

```python
    # Split signals at the same timestamp boundary as OHLCV (legacy contiguous
    # mode) or into purged+embargoed K folds (cv mode, Phase 0.3c).
    folds: list[FoldSplit] = []
    if cv_active is not None:
        try:
            folds = purged_kfold_split(ohlcv_full, signals_full, cv_active)
        except ValueError as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            return []
        signals_is = pd.DataFrame()
        signals_oos = pd.DataFrame()
    elif not ohlcv_is.empty and not ohlcv_oos.empty:
        split_ts = int(ohlcv_oos.iloc[0]["open_time"])
        signals_is = signals_full[signals_full["open_time"] < split_ts].copy()
        signals_oos = signals_full[signals_full["open_time"] >= split_ts].copy()
    else:
        signals_is = signals_full.copy()
        signals_oos = pd.DataFrame()
```

(d) **Header print** — replace:

```python
    print(f"\n  Sweep: {strategy} / {symbol} / {timeframe}{sl_note}")
    print(
        f"  Grid size: {n} combos | IS candles: {len(ohlcv_is)} | OOS candles: {len(ohlcv_oos)}"
        f" | workers: {workers}"
    )
```

with:

```python
    print(f"\n  Sweep: {strategy} / {symbol} / {timeframe}{sl_note}")
    if cv_active is not None:
        embargo = cv_active.resolve_embargo_bars(len(ohlcv_full))
        print(
            f"  Grid size: {n} combos | CV: {cv_active.n_folds} purged folds "
            f"(purge={cv_active.purge_bars}, embargo={embargo} bars) "
            f"over {len(ohlcv_full)} candles | workers: {workers}"
        )
    else:
        print(
            f"  Grid size: {n} combos | IS candles: {len(ohlcv_is)} | OOS candles: {len(ohlcv_oos)}"
            f" | workers: {workers}"
        )
```

(e) **Worker submission** — inside the `with ProcessPoolExecutor(...) as pool:` block, replace the single `futures = {...}` dict comprehension with a branch (the `else` arm is the existing dict, unchanged):

```python
            if cv_active is not None:
                futures = {
                    pool.submit(
                        _sweep_grid_worker_cv,
                        p,
                        folds,
                        symbol,
                        timeframe,
                        strategy,
                        fee_pct,
                        is_min,
                        atr_sl_multiplier,
                        atr_sl_floor,
                        live_parity=live_parity,
                        bias_cfg=bias_cfg,
                        regime_series=regime_series,
                        strategy_params=strategy_params,
                        htf_slope_series_by_anchor=htf_slope_series_by_anchor,
                    ): p
                    for p in grid
                }
            else:
                futures = {
                    pool.submit(
                        _sweep_grid_worker,
                        p,
                        ohlcv_is,
                        signals_is,
                        ohlcv_oos,
                        signals_oos,
                        symbol,
                        timeframe,
                        strategy,
                        fee_pct,
                        is_min,
                        atr_sl_multiplier,
                        atr_sl_floor,
                        live_parity=live_parity,
                        bias_cfg=bias_cfg,
                        regime_series=regime_series,
                        strategy_params=strategy_params,
                        htf_slope_series_by_anchor=htf_slope_series_by_anchor,
                    ): p
                    for p in grid
                }
```

(f) **PBO double-count guard** — change `_attach_overfit_stats`'s signature and matrix input. Signature:

```python
def _attach_overfit_stats(
    rows: list[SweepRow], *, pooled_oos_only: bool = False
) -> None:
```

Append to its docstring:

```python
    ``pooled_oos_only=True`` (purged-CV mode) builds the PBO matrix from each
    config's OOS pool alone: the test folds tile the full history exactly once,
    whereas the CV train pool overlaps them — including both would double-count
    every trade in the CSCV matrix. Legacy mode keeps IS+OOS (disjoint windows).
```

and replace the `trade_points = [...]` comprehension with:

```python
    trade_points = [
        [
            (t.entry_time, t.pnl_r)
            for t in (
                r.oos_result.closed_trades
                if pooled_oos_only
                else (*r.is_result.closed_trades, *r.oos_result.closed_trades)
            )
            if t.pnl_r is not None
        ]
        for r in ordered
    ]
```

Then update the call site in `run_param_sweep`:

```python
    _attach_overfit_stats(rows, pooled_oos_only=cv_active is not None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_param_sweep.py -v`
Expected: all PASS (the integration tests spawn real worker processes — a few seconds is normal)

- [ ] **Step 5: Full suite + regression goldens (the byte-identical guarantee)**

Run: `make lint-py && make typecheck && make test && make test-regression`
Expected: all green, **no golden regeneration** — sweep output is not in the goldens and the default path is untouched

- [ ] **Step 6: Commit**

```bash
git add analytics/param_sweep.py tests/test_param_sweep.py
git commit -m "feat(sweep): run_param_sweep cv mode — purged K-fold wiring, default contiguous

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: CLI flags (`wifey param-sweep --cv-mode purged`)

**Files:**

- Modify: `cli/param.py`
- Create: `tests/test_cli_param_cv.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_param_cv.py`:

```python
"""Tests for cli/param.py --cv-* flags (Phase 0.3c purged+embargoed CV)."""

from __future__ import annotations

import argparse

from analytics.backtest.cv_splits import CvConfig
from cli.param import _cv_from_args, add_param_sweep_subparser

_REQUIRED = ["--strategy", "bos", "--symbol", "AAPL", "--timeframe", "1d"]


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    add_param_sweep_subparser(subparsers)
    return parser.parse_args(["param-sweep", *argv])


class TestCvFlags:
    def test_defaults_to_contiguous_legacy_path(self) -> None:
        args = _parse(_REQUIRED)
        assert args.cv_mode == "contiguous"
        assert args.cv_folds == 5
        assert args.cv_purge_bars == 0
        assert args.cv_embargo_bars is None
        assert _cv_from_args(args) is None

    def test_purged_builds_cv_config(self) -> None:
        args = _parse(
            [
                *_REQUIRED,
                "--cv-mode",
                "purged",
                "--cv-folds",
                "8",
                "--cv-purge-bars",
                "3",
                "--cv-embargo-bars",
                "10",
            ]
        )
        assert _cv_from_args(args) == CvConfig(
            mode="purged", n_folds=8, purge_bars=3, embargo_bars=10
        )

    def test_purged_defaults(self) -> None:
        args = _parse([*_REQUIRED, "--cv-mode", "purged"])
        assert _cv_from_args(args) == CvConfig(
            mode="purged", n_folds=5, purge_bars=0, embargo_bars=None
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_cli_param_cv.py -v`
Expected: ImportError — `cannot import name '_cv_from_args'`

- [ ] **Step 3: Implement**

All edits in `cli/param.py`:

(a) Extend the module header imports (keep the lazy-import convention for runtime deps):

```python
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from analytics.strategies import KNOWN_STRATEGIES
from cli._common import parse_since_to_ms

if TYPE_CHECKING:
    from analytics.backtest.cv_splits import CvConfig
```

(b) Add the helper directly above `def run_param_sweep(`:

```python
def _cv_from_args(args: argparse.Namespace) -> CvConfig | None:
    """Build a CvConfig from --cv-* flags; None when mode is contiguous (legacy)."""
    if args.cv_mode != "purged":
        return None
    from analytics.backtest.cv_splits import CvConfig

    return CvConfig(
        mode="purged",
        n_folds=args.cv_folds,
        purge_bars=args.cv_purge_bars,
        embargo_bars=args.cv_embargo_bars,
    )
```

(c) In the CLI `run_param_sweep(args)` body, replace the window print:

```python
    print(
        f"Window: {_window}  WFO split: {args.wfo_split:.0%} IS / {1 - args.wfo_split:.0%} OOS"
    )
```

with:

```python
    if args.cv_mode == "purged":
        print(f"Window: {_window}  CV: purged {args.cv_folds}-fold + embargo")
    else:
        print(
            f"Window: {_window}  WFO split: {args.wfo_split:.0%} IS / {1 - args.wfo_split:.0%} OOS"
        )
```

(d) Thread it into the sweep call — add one line to the `_run(...)` kwargs (after `atr_sl_floor=args.atr_sl_floor,`):

```python
                atr_sl_floor=args.atr_sl_floor,
                cv=_cv_from_args(args),
```

(e) In `add_param_sweep_subparser`, after the `--day-filter` argument block, add:

```python
    param_sweep_parser.add_argument(
        "--cv-mode",
        type=str,
        default="contiguous",
        dest="cv_mode",
        choices=["contiguous", "purged"],
        help="CV geometry: contiguous = legacy single IS/OOS split (default); "
        "purged = purged+embargoed K-fold CV (Phase 0.3c, LdP AFML ch. 7)",
    )
    param_sweep_parser.add_argument(
        "--cv-folds",
        type=int,
        default=5,
        dest="cv_folds",
        help="Fold count for --cv-mode purged (default: 5)",
    )
    param_sweep_parser.add_argument(
        "--cv-purge-bars",
        type=int,
        default=0,
        dest="cv_purge_bars",
        help="Bars purged from the end of each pre-test train segment (default: 0 — "
        "the engine already censors boundary-straddling trades as open)",
    )
    param_sweep_parser.add_argument(
        "--cv-embargo-bars",
        type=int,
        default=None,
        dest="cv_embargo_bars",
        help="Bars embargoed after each test fold (default: ~1%% of candles)",
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_cli_param_cv.py -v`
Expected: 3 PASS

- [ ] **Step 5: Lint + typecheck + smoke**

Run: `make lint-py && make typecheck`
Expected: clean

Smoke (real DB, read-only; confirms end-to-end wiring + fold header print):

```bash
poetry run python wifey.py param-sweep --strategy bos --symbol AAPL --timeframe 1d \
  --days 365 --param tp_r=1.0:2.0:0.5 --cv-mode purged
```

Expected: output shows `CV: purged 5-fold + embargo` and a `CV: 5 purged folds (purge=0, embargo=… bars)` grid line; rows render normally.

- [ ] **Step 6: Commit**

```bash
git add cli/param.py tests/test_cli_param_cv.py
git commit -m "feat(cli): param-sweep --cv-mode/--cv-folds/--cv-purge-bars/--cv-embargo-bars

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Docs sync + final gates

**Files:**

- Modify: `CLAUDE.md` (backtest module list + `param_sweep.py` bullet)
- Modify: `README.md` (lines 25 + 56 anchors)
- Modify: `.claude/context/analytics.md` (param_sweep section, lines ~93-110)

- [ ] **Step 1: CLAUDE.md — backtest module list**

In the `analytics/` → `backtest/` bullet: change `backtest engine split into 8 modules` to `backtest engine split into 9 modules`, and insert after the `live_parity_config.py (...)` entry:

```text
`cv_splits.py` (`CvConfig`, `FoldSplit`, `fold_bounds`, `purged_kfold_split` — Phase 0.3c purged+embargoed K-fold CV splits, LdP AFML ch. 7; pure pandas, no DB/engine imports; embargo defaults to ~1% of rows, purge defaults to 0 because per-segment truncation already censors boundary-straddling trades as `outcome="open"`),
```

In the same bullet, replace the `stats_overfit.py` tail sentence:

```text
0.3c purged-embargoed CV is a later PR — this does not touch `_split_ohlcv`)
```

with:

```text
0.3c shipped the purged-embargoed CV via `cv_splits.py` + `run_param_sweep(cv=...)` — legacy `_split_ohlcv` untouched, default-off)
```

- [ ] **Step 2: CLAUDE.md — `param_sweep.py` bullet**

Replace:

```text
- `param_sweep.py` — WFO sweep lib; `run_param_sweep` / `run_strategy_audit`; parallelized via `ProcessPoolExecutor`
```

with:

```text
- `param_sweep.py` — WFO sweep lib; `run_param_sweep` / `run_strategy_audit`; parallelized via `ProcessPoolExecutor`. Phase 0.3c: keyword-only `cv: CvConfig | None` switches `run_param_sweep` to purged+embargoed K-fold CV (`analytics/backtest/cv_splits.py`) — IS = train segments pooled across folds, deduped by `(signal_time, direction)` preferring resolved instances; OOS = disjoint test folds pooled directly; in CV mode the PBO matrix uses the OOS pool only (test folds tile history once; train overlaps would double-count). Default `cv=None` / `mode="contiguous"` reproduces the legacy split byte-identically (goldens unmoved). CLI: `wifey param-sweep --cv-mode purged [--cv-folds 5] [--cv-purge-bars 0] [--cv-embargo-bars N]`. `run_strategy_audit` + `tools/multi_symbol_wfo.py` stay contiguous-only.
```

- [ ] **Step 3: README.md**

Line 56 — replace:

```text
│   ├── param_sweep.py               # WFO sweep lib: run_param_sweep / run_strategy_audit; parallelized via ProcessPoolExecutor
```

with:

```text
│   ├── param_sweep.py               # WFO sweep lib: run_param_sweep / run_strategy_audit; optional purged+embargoed K-fold CV (--cv-mode purged)
```

Line 25 — extend the feature paragraph: after `…haircut for the number of grid trials.` append:

```text
Sweeps can optionally run purged + embargoed K-fold CV (`--cv-mode purged`, López de Prado AFML ch. 7) instead of the single contiguous split, censoring trades that straddle fold boundaries and embargoing the bars after each test fold.
```

- [ ] **Step 4: `.claude/context/analytics.md`**

In the `## param_sweep.py — WFO sweep lib` section: update the `run_param_sweep(...)` signature bullet to include `, cv=None` after `htf_slope_series_by_anchor=None`, and append to that bullet:

```text
`cv=CvConfig(mode="purged", n_folds, purge_bars, embargo_bars)` (Phase 0.3c) switches to purged+embargoed K-fold CV via `analytics/backtest/cv_splits.py` (`fold_bounds` partitions rows into K contiguous test folds; per fold the pre-test train segment loses `purge_bars` tail bars and the post-test segment loses `resolve_embargo_bars(n)` head bars — default ~1% of rows; per-segment truncation censors straddling trades as outcome="open"). IS = `_dedup_trades`-pooled train segments across folds, OOS = disjoint test folds pooled; `_sweep_grid_worker_cv` is the picklable CV worker; `_attach_overfit_stats(pooled_oos_only=True)` builds the PBO matrix from OOS only in CV mode. `cv=None`/contiguous = legacy byte-identical path.
```

In the overfit section (line ~110), replace the tail sentence "0.3c purged-embargoed CV is a later PR (does not touch param_sweep._split_ohlcv)" with "0.3c shipped purged-embargoed CV (cv_splits.py; _split_ohlcv untouched, default-off)".

- [ ] **Step 5: Final gates**

Run: `make lint-py && make typecheck && make test && make test-regression && make lint-md`
Expected: all green; goldens untouched. Hand-check the markdown edits against the full default markdownlint ruleset (CI is stricter than local — MD013/MD033 off, everything else on incl. MD060 padded table separators).

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md .claude/context/analytics.md
git commit -m "docs: Phase 0.3c purged+embargoed CV — CLAUDE.md, analytics context, README

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## After the plan (execution session, not plan tasks)

1. Verify `git config --local user.email` = `ngkhaijian@gmail.com` before the first commit (mandatory per-repo identity).
2. Push + PR: `git push -u origin feat/phase0-purged-cv`, then `/pr-summary` → `gh pr create --repo s10023/buibui-wifey-wall-street-bot …` (the `--repo` flag is mandatory), then `/post-branch`.
3. Update memory Current State (compact one-liner) + master to-do N1 progress (0.3c done → next 0.1 universe-as-of).
