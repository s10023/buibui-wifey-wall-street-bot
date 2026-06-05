# Phase 0.5 — Data-Quality Monitors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OHLCV data-quality gate to ingest so corrupt bars (NaNs, non-positive prices, broken bar geometry, duplicate timestamps) are quarantined before storage, and softer anomalies (zero volume, return outliers, suspected unadjusted splits, non-monotonic timestamps) are logged.

**Architecture:** A new pure module `analytics/data_quality.py` exposes `check_ohlcv(df) -> DataQualityReport` (detection only, no side effects) and `quarantine(df, report) -> (clean, dropped)` (drops only unambiguously-bad rows). `analytics/data_sync.backfill` calls them between `fetch_bars` and `upsert_ohlcv`: it logs the report and stores only the clean rows. Purely additive — on clean data nothing is dropped and behaviour is identical, so regression goldens (fixture-driven, not ingest-driven) stay byte-identical.

**Tech Stack:** Python 3.11+, pandas, numpy, duckdb (`:memory:` in tests), pytest + unittest.mock, ruff, mypy strict.

---

## Scope notes (locked decisions)

- **No calendar-aware gap detection.** Equity weekends/holidays make naive cadence-gap checks fire constantly without a trading calendar. We detect only unambiguous timestamp anomalies (duplicates, non-monotonic order). Calendar-aware gaps are explicitly out of scope.
- **Quarantine (drop) only unambiguous corruption:** NaN in any OHLCV field, non-positive price, broken bar geometry (`high < low`, `high < open/close`, `low > open/close`), and later-duplicate timestamps. Everything else is **warn-only** (logged, not dropped) so a real 20% earnings move or a halt bar is never silently deleted.
- **Quarantine indices are positional** (0..n-1) over a reset index; `check_ohlcv` and `quarantine` both `reset_index(drop=True)` internally so callers pass the raw `fetch_bars` frame.

## File Structure

- **Create** `analytics/data_quality.py` — detection (`check_ohlcv`) + `DataQualityReport` + `quarantine`. One responsibility: OHLCV integrity. No DB, no I/O.
- **Create** `tests/test_data_quality.py` — unit tests on synthetic clean/dirty frames.
- **Modify** `analytics/data_sync.py:19-35` (`backfill`) — wire the gate in.
- **Modify** `tests/test_data_sync.py` — one wiring test (dirty frame → only clean rows stored).
- **Modify** docs: `CLAUDE.md` (analytics module list), `.claude/context/analytics.md` (module reference).

Implement on a fresh branch off `main`:

```bash
git checkout main && git pull && git checkout -b feat/phase0-data-quality
```

---

### Task 1: `DataQualityReport` + core quarantine detection (NaN / non-positive price / bad geometry)

**Files:**

- Create: `analytics/data_quality.py`
- Test: `tests/test_data_quality.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for analytics/data_quality.py."""

import pandas as pd

from analytics.data_quality import DataQualityReport, check_ohlcv

_COLS = ["symbol", "timeframe", "open_time", "open", "high", "low", "close", "volume"]


def _frame(rows: list[dict]) -> pd.DataFrame:
    """Build an OHLCV frame; each row dict overrides the clean defaults."""
    base = {
        "symbol": "AAPL",
        "timeframe": "1d",
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }
    out = []
    for i, r in enumerate(rows):
        row = {**base, "open_time": 1_000 + i * 86_400_000}
        row.update(r)
        out.append(row)
    return pd.DataFrame(out, columns=_COLS)


def test_clean_frame_is_clean() -> None:
    rep = check_ohlcv(_frame([{}, {}, {}]))
    assert isinstance(rep, DataQualityReport)
    assert rep.n_rows == 3
    assert rep.quarantine_idx == ()
    assert rep.is_clean is True


def test_nan_price_is_quarantined() -> None:
    rep = check_ohlcv(_frame([{}, {"close": float("nan")}, {}]))
    assert rep.nan_idx == (1,)
    assert 1 in rep.quarantine_idx
    assert rep.is_clean is False


def test_nonpositive_price_is_quarantined() -> None:
    rep = check_ohlcv(_frame([{}, {"low": 0.0}, {"open": -5.0}]))
    assert rep.nonpositive_price_idx == (1, 2)
    assert set(rep.quarantine_idx) == {1, 2}


def test_broken_bar_geometry_is_quarantined() -> None:
    # high below low; and high below open
    rep = check_ohlcv(_frame([{"high": 98.0}, {"high": 100.5, "open": 101.0}]))
    assert set(rep.bad_bar_idx) == {0, 1}
    assert set(rep.quarantine_idx) == {0, 1}


def test_empty_frame() -> None:
    rep = check_ohlcv(_frame([]))
    assert rep.n_rows == 0
    assert rep.quarantine_idx == ()
    assert rep.is_clean is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_data_quality.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.data_quality'`

- [ ] **Step 3: Write minimal implementation**

```python
"""OHLCV data-quality monitor — detection (pure) + quarantine helper.

No DB, no network, no side effects. `check_ohlcv` returns a typed report of
integrity problems by positional row index (0..n-1 over a reset index).
`quarantine` (Task 4) drops only the unambiguously-bad rows.

Calendar-aware gap detection is intentionally out of scope: equity
weekends/holidays make naive cadence checks fire constantly without a trading
calendar. We flag only unambiguous timestamp anomalies (duplicates,
non-monotonic order — Task 2).
"""

import logging
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_data_quality.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/data_quality.py tests/test_data_quality.py
git commit -m "feat(data-quality): DataQualityReport + core quarantine detection"
```

---

### Task 2: Duplicate-timestamp + non-monotonic detection

**Files:**

- Modify: `analytics/data_quality.py` (inside `check_ohlcv`)
- Test: `tests/test_data_quality.py`

- [ ] **Step 1: Write the failing test**

```python
def test_duplicate_timestamp_quarantines_later_row() -> None:
    df = _frame([{}, {}, {}])
    df.loc[2, "open_time"] = df.loc[1, "open_time"]  # row 2 duplicates row 1
    rep = check_ohlcv(df)
    assert rep.duplicate_time_idx == (2,)
    assert 2 in rep.quarantine_idx


def test_nonmonotonic_timestamp_is_warned_not_dropped() -> None:
    df = _frame([{}, {}, {}])
    df.loc[2, "open_time"] = df.loc[0, "open_time"] - 1  # goes backwards
    rep = check_ohlcv(df)
    assert rep.nonmonotonic_idx == (2,)
    assert 2 not in rep.quarantine_idx  # warn-only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_data_quality.py -k "duplicate or nonmonotonic" -v`
Expected: FAIL — `duplicate_time_idx`/`nonmonotonic_idx` are `()`

- [ ] **Step 3: Write minimal implementation**

In `check_ohlcv`, replace the `duplicate_time_idx=empty,` and `nonmonotonic_idx=empty,` lines by computing them before the `return`. Insert after the `bad_bar_mask` line:

```python
    dup_mask = df["open_time"].duplicated(keep="first")
    nonmono_mask = df["open_time"].diff() < 0
```

Then update the constructor call:

```python
        duplicate_time_idx=_idx_tuple(dup_mask),
        ...
        nonmonotonic_idx=_idx_tuple(nonmono_mask),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_data_quality.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/data_quality.py tests/test_data_quality.py
git commit -m "feat(data-quality): duplicate + non-monotonic timestamp detection"
```

---

### Task 3: Warn-only anomaly flags (zero volume / return outlier / suspected split)

**Files:**

- Modify: `analytics/data_quality.py`
- Test: `tests/test_data_quality.py`

- [ ] **Step 1: Write the failing test**

```python
def test_zero_volume_is_warned_not_dropped() -> None:
    rep = check_ohlcv(_frame([{}, {"volume": 0.0}, {}]))
    assert rep.zero_volume_idx == (1,)
    assert 1 not in rep.quarantine_idx


def test_return_outlier_flagged() -> None:
    # row 1 close jumps +80% vs row 0 (101 -> 181.8): outlier, not split-like
    rep = check_ohlcv(_frame([{}, {"open": 180.0, "high": 182.0, "low": 179.0, "close": 181.8}, {}]))
    assert 1 in rep.return_outlier_idx
    assert 1 not in rep.suspected_split_idx


def test_suspected_split_flagged() -> None:
    # row 1 close halves vs row 0 (101 -> ~50.5): ratio ~0.5 == 2:1 split
    rep = check_ohlcv(
        _frame([{}, {"open": 50.0, "high": 51.0, "low": 49.5, "close": 50.5}, {}])
    )
    assert 1 in rep.suspected_split_idx


def test_normal_move_not_flagged() -> None:
    # +3% day is neither outlier nor split
    rep = check_ohlcv(_frame([{}, {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0}, {}]))
    assert rep.return_outlier_idx == ()
    assert rep.suspected_split_idx == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_data_quality.py -k "zero_volume or outlier or split or normal_move" -v`
Expected: FAIL — those index tuples are `()`

- [ ] **Step 3: Write minimal implementation**

Add the module constants near the top, under `_OHLCV_NUMERIC`:

```python
# canonical price ratios (new/old close) that signal an unadjusted split
_SPLIT_FACTORS: tuple[float, ...] = (0.5, 1.0 / 3.0, 0.25, 0.2, 2.0, 3.0, 4.0, 5.0)
_SPLIT_TOL: float = 0.05  # within ±5% (relative) of a canonical factor
```

Add a helper above `check_ohlcv`:

```python
def _is_split_like(ratio: float) -> bool:
    if pd.isna(ratio):
        return False
    return any(abs(ratio - f) <= _SPLIT_TOL * f for f in _SPLIT_FACTORS)
```

In `check_ohlcv`, after the `nonmono_mask` line, add:

```python
    zero_vol_mask = valid & (df["volume"] <= 0)
    ret = df["close"].pct_change()
    ratio = df["close"] / df["close"].shift(1)
    outlier_mask = valid & (ret.abs() > return_outlier_pct)
    split_mask = valid & ratio.apply(_is_split_like)
```

Update the constructor call to populate the three fields:

```python
        zero_volume_idx=_idx_tuple(zero_vol_mask),
        return_outlier_idx=_idx_tuple(outlier_mask),
        suspected_split_idx=_idx_tuple(split_mask),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_data_quality.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/data_quality.py tests/test_data_quality.py
git commit -m "feat(data-quality): zero-volume, return-outlier, suspected-split flags"
```

---

### Task 4: `quarantine()` splitter

**Files:**

- Modify: `analytics/data_quality.py`
- Test: `tests/test_data_quality.py`

- [ ] **Step 1: Write the failing test**

```python
from analytics.data_quality import quarantine


def test_quarantine_drops_only_bad_rows() -> None:
    df = _frame([{}, {"close": float("nan")}, {}, {"low": 0.0}])
    rep = check_ohlcv(df)
    clean, dropped = quarantine(df, rep)
    assert len(clean) == 2
    assert len(dropped) == 2
    assert clean["close"].tolist() == [101.0, 101.0]
    # input frame is untouched
    assert len(df) == 4


def test_quarantine_clean_frame_is_passthrough() -> None:
    df = _frame([{}, {}, {}])
    rep = check_ohlcv(df)
    clean, dropped = quarantine(df, rep)
    assert len(clean) == 3
    assert dropped.empty
    assert list(clean.columns) == list(df.columns)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_data_quality.py -k quarantine -v`
Expected: FAIL — `ImportError: cannot import name 'quarantine'`

- [ ] **Step 3: Write minimal implementation**

Append to `analytics/data_quality.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_data_quality.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/data_quality.py tests/test_data_quality.py
git commit -m "feat(data-quality): quarantine() clean/dropped splitter"
```

---

### Task 5: Wire the gate into `data_sync.backfill`

**Files:**

- Modify: `analytics/data_sync.py:19-35`
- Test: `tests/test_data_sync.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_sync.py` (it already has `_make_conn`, `_make_df`, `OHLCV_COLUMNS`, and imports `patch`):

```python
def test_backfill_quarantines_bad_rows() -> None:
    conn = _make_conn()
    df = _make_df([1_000, 2_000, 3_000], symbol="AAPL", timeframe="1d")
    df.loc[1, "close"] = float("nan")  # one corrupt row
    with patch("analytics.data_sync.fetch_bars", return_value=df):
        stored = backfill(conn, "AAPL", "1d", 0)
    assert stored == 2  # corrupt row dropped
    rows = conn.execute(
        "SELECT COUNT(*) FROM ohlcv WHERE symbol = 'AAPL' AND timeframe = '1d'"
    ).fetchone()[0]
    assert rows == 2


def test_backfill_clean_data_stores_all_rows() -> None:
    conn = _make_conn()
    df = _make_df([1_000, 2_000, 3_000], symbol="MSFT", timeframe="1d")
    with patch("analytics.data_sync.fetch_bars", return_value=df):
        stored = backfill(conn, "MSFT", "1d", 0)
    assert stored == 3  # unchanged behaviour on clean data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_data_sync.py -k "quarantine or clean_data_stores" -v`
Expected: FAIL — `test_backfill_quarantines_bad_rows` stores 3 (NaN row reaches DB) or errors

- [ ] **Step 3: Write minimal implementation**

Replace the body of `backfill` in `analytics/data_sync.py`. New imports at the top of the file:

```python
from analytics.data_quality import check_ohlcv, quarantine
```

New `backfill` body (keep the signature and docstring):

```python
    df = fetch_bars(symbol, timeframe, start_ms)
    if df.empty:
        return 0

    report = check_ohlcv(df)
    if not report.is_clean:
        logging.warning(
            "data-quality %s %s: %s", symbol, timeframe, report.summary()
        )
    clean, dropped = quarantine(df, report)
    if clean.empty:
        logging.warning(
            "data-quality %s %s: all %d rows quarantined", symbol, timeframe, len(df)
        )
        return 0

    upsert_ohlcv(conn, clean)
    logging.info(
        "backfill %s %s: stored %d rows (%d quarantined)",
        symbol,
        timeframe,
        len(clean),
        len(dropped),
    )
    return len(clean)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_data_sync.py -v`
Expected: PASS (existing tests + 2 new; clean-data path returns the full count unchanged)

- [ ] **Step 5: Commit**

```bash
git add analytics/data_sync.py tests/test_data_sync.py
git commit -m "feat(data-quality): gate ingest backfill on the data-quality monitor"
```

---

### Task 6: Docs sync + regression-golden verification + final gate

**Files:**

- Modify: `CLAUDE.md` (analytics module bullet list)
- Modify: `.claude/context/analytics.md`

- [ ] **Step 1: Add the module to `CLAUDE.md`**

In the `analytics/` module list, add a bullet near `data_fetcher.py` / `data_sync.py`:

```markdown
  - `data_quality.py` — OHLCV integrity monitor (Phase 0.5). `check_ohlcv(df) -> DataQualityReport` (pure detection: NaN / non-positive price / broken bar geometry / duplicate + non-monotonic timestamps / zero-volume / return-outlier / suspected-unadjusted-split) + `quarantine(df, report) -> (clean, dropped)`. Wired into `data_sync.backfill` between fetch and upsert: hard-corrupt rows (NaN, non-positive price, bad geometry, duplicate ts) are dropped before storage; soft anomalies are logged. Calendar-aware gap detection intentionally out of scope.
```

- [ ] **Step 2: Add a reference entry to `.claude/context/analytics.md`**

Add a short section documenting `check_ohlcv`, the `DataQualityReport` fields (quarantine vs warn-only sets), and `quarantine`, mirroring the style of neighbouring entries in that file.

- [ ] **Step 3: Verify regression goldens are byte-identical**

Run: `make test-regression`
Expected: PASS / SKIP unchanged — the data-quality gate is not in the fixture-driven backtest pipeline, and clean data is a pass-through, so no golden moves. (If any golden moves, STOP — that means clean data is being altered; investigate before proceeding.)

- [ ] **Step 4: Full project gate**

Run: `make lint-py && make typecheck && make test && make lint-md`
Expected: all green (ruff format+lint, mypy strict, full pytest suite, markdownlint).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .claude/context/analytics.md
git commit -m "docs(data-quality): document the Phase 0.5 ingest integrity monitor"
```

- [ ] **Step 6: Push + open PR**

```bash
git push -u origin feat/phase0-data-quality
gh pr create --repo s10023/buibui-wifey-wall-street-bot --base main \
  --title "feat(data-quality): Phase 0.5 OHLCV ingest integrity monitor" \
  --body "Implements Phase 0.5 from docs/superpowers/specs/2026-06-05-phase0-correctness-foundation-design.md. Additive ingest gate; goldens byte-identical."
```

---

## Self-Review

**Spec coverage (§4.5):**

- "typed DataQualityReport" → Task 1 (frozen dataclass). ✓
- "bar-cadence gaps" → consciously narrowed to duplicate + non-monotonic timestamp checks (Task 2); calendar gaps documented as out-of-scope. ✓ (deviation recorded in Scope notes)
- "NaN/zero-volume rows" → NaN quarantined (Task 1), zero-volume warned (Task 3). ✓
- "|return| σ-outliers" → return-outlier flag via `return_outlier_pct` (Task 3). ✓
- "split-sanity check" → `_is_split_like` + `suspected_split_idx` (Task 3). ✓
- "wired into data_sync.backfill/sync after fetch (log + quarantine, raise only on catastrophic emptiness)" → Task 5. `sync` delegates to `backfill`, so wiring `backfill` covers both; empty fetch still returns 0 without raising. ✓
- "regression goldens byte-identical" → Task 6 Step 3. ✓
- "TDD with synthetic dirty/clean frames" → every task is test-first. ✓

**Placeholder scan:** none — all code blocks are complete; Task 6 Step 2 references concrete neighbouring style rather than leaving a TODO.

**Type consistency:** `check_ohlcv(df, *, return_outlier_pct)`, `DataQualityReport` field names, `quarantine(df, report) -> tuple[pd.DataFrame, pd.DataFrame]`, and `_idx_tuple` are used identically across Tasks 1–5. `quarantine_idx`/`is_clean`/`has_warnings`/`summary()` defined in Task 1 and consumed in Task 5. ✓

**Note on `sync`:** the spec mentions `backfill/sync`; `sync` calls `backfill` internally (`data_sync.py:56`), so the gate applies to both with a single wiring point — no separate task needed.
