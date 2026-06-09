# Phase 0.2 — Lookahead / Leakage Audit + Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish and lock causality guarantees for the signal pipeline — prove no detector or backtest fill depends on bars after its `open_time` — via a reusable truncated-series test harness, a written audit, and a hardened data-adjustment convention.

**Architecture:** A new `tests/test_lookahead.py` runs every detector in `DETECTOR_REGISTRY` over the committed real-AAPL OHLCV parquet fixtures; for each emitted signal at `open_time` t it re-runs the detector on the series truncated at t and asserts the set of signals at t is byte-identical (no future dependence). A meta-test proves the harness has teeth by catching a deliberately look-ahead detector. A second block locks the backtest engine's next-bar-open fill and proves the entry is independent of future bars. A written audit (`docs/redesign/phase0-lookahead-audit.md`) enumerates and classifies every historical-data read site, and the yfinance adjustment convention is documented + guarded.

**Tech Stack:** Python 3.11+, pandas, numpy, pytest + unittest.mock, ruff, mypy strict. No new runtime deps; tests use committed parquet fixtures and `:memory:`-free pure functions.

---

## Scope notes (locked decisions)

- **These are characterization / guard tests.** The expected first result is **green** — current code is believed causal (verified by a coverage probe on 2026-06-09: all 16 detectors fire on the 4h fixture). For pure-guard tasks, "red" means a *real leak was discovered* — if a task's test fails, that is a finding: fix the leak, flag the golden move per-fix (§4.2 DoD), and note it in the audit doc. To preserve a genuine red→green TDD beat, Task 1 ships a **meta-test** first: an injected look-ahead detector that the harness MUST flag (red until the harness logic is correct, then green).
- **Truncation points are sampled, not exhaustive.** Re-running a detector once per emitted signal is O(signals × n) and redundant. The harness samples up to `_MAX_TRUNC_POINTS = 25` truncation points spread evenly across the signal `open_time`s (always including the earliest and latest). This bounds suite runtime while still exercising the full index range. The audit doc (Task 4) carries the exhaustive *reasoning*; the harness is the regression guard.
- **Next-bar-open is the causal fill and is already correct** (`engine.py`: `entry_idx = sig_idx + 1; entry_price = opens_np[entry_idx]`). Task 2 *locks* it and proves entry-independence from future bars; it does not change behaviour.
- **Forward exit-resolution is justified, not lookahead.** The engine walks `highs/lows[entry_idx:]` forward to resolve SL/TP — that is the *realized* outcome of an already-placed trade, not future knowledge used to decide entry. The audit classifies it JUSTIFIED; the harness does not test it.
- **Adjustment convention is bounded, not eliminated.** `yfinance_client.fetch_history` uses `auto_adjust=False, actions=False`: raw close preserved, but yfinance still back-applies *split* factors to historical OHLC (future splits embedded into past bars — a mild as-of violation), and does **not** back-adjust dividends. Fixing it requires unadjusted data + manual as-of corporate-action application, which is out of the free-data scope. Task 3 *documents and guards* the convention (the kwargs are already locked by an existing test); it does not switch to unadjusted prices.
- **No golden moves expected.** Every change here is an additive test or a docstring/doc edit. `make test-regression` must stay byte-identical (Task 5 Step 3). If the harness forces a real leak fix that moves a golden, STOP and flag it as a reviewed, deliberate change.

## File Structure

- **Create** `tests/test_lookahead.py` — the whole harness: helpers (`_load_fixture`, `_normalize_signals_at`, `_truncation_points`, `_first_lookahead_violation`), the harness meta-test, the parametrized detector causality test (Task 1), and the backtest entry-path tests (Task 2). One responsibility: causality guards. No production imports beyond the public registry + engine.
- **Modify** `utils/yfinance_client.py` — harden the `fetch_history` docstring to state the as-of-adjustment convention + its bounded lookahead implication (Task 3).
- **Modify** `tests/test_yfinance_client.py` — add one lookahead-framed guard test for the adjustment convention (Task 3).
- **Create** `docs/redesign/phase0-lookahead-audit.md` — the written audit: every read site classified causal / leaking / justified (Task 4).
- **Modify** docs: `CLAUDE.md` (tests/ surface note + audit-doc pointer), `MEMORY.md` Current State (Task 5).

Implement on a fresh branch off `main`:

```bash
git checkout main && git pull && git checkout -b feat/phase0-lookahead-audit
```

---

### Task 1: Detector lookahead harness (truncated-series property test)

**Files:**

- Create: `tests/test_lookahead.py`

- [ ] **Step 1: Write the harness + the meta-test (this is the red→green beat)**

Create `tests/test_lookahead.py`:

```python
"""Lookahead / leakage harness (Phase 0.2).

Proves the signal pipeline is causal: a signal emitted at bar open_time t must
not depend on any bar after t. We verify this by feeding each detector a series
truncated at t and asserting the set of signals at t is identical to the
full-series run.

Fixtures: the committed real-AAPL OHLCV parquets in tests/fixtures. The 4h frame
fires all 16 detectors; 1d fires 15 (orb needs the intraday session anchor);
1wk fires 14 (orb + ote_entry too rare).

These are characterization guards. Expected result: green (current code is
causal). A failure is a real leak — fix it and flag any golden move per §4.2.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from analytics.strategies._registry import DETECTOR_REGISTRY

_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_MAX_TRUNC_POINTS = 25
_FLOAT_DECIMALS = 8

Detector = Callable[[pd.DataFrame], pd.DataFrame]


def _load_fixture(tf: str) -> pd.DataFrame:
    """Load a committed AAPL OHLCV fixture with a clean RangeIndex."""
    df = pd.read_parquet(_FIXTURE_DIR / f"aapl_{tf}.parquet")
    return df.reset_index(drop=True)


def _normalize_signals_at(sig: pd.DataFrame, t: int) -> list[tuple[tuple[str, object], ...]]:
    """Order-independent, float-stable view of the signals whose open_time == t."""
    if sig.empty or "open_time" not in sig.columns:
        return []
    at = sig[sig["open_time"].astype("int64") == t]
    rows: list[tuple[tuple[str, object], ...]] = []
    for _, r in at.iterrows():
        cells: list[tuple[str, object]] = []
        for col in sorted(sig.columns):
            v = r[col]
            if isinstance(v, float):
                v = round(v, _FLOAT_DECIMALS)
            cells.append((col, v))
        rows.append(tuple(cells))
    return sorted(rows)


def _truncation_points(open_times: list[int]) -> list[int]:
    """Up to _MAX_TRUNC_POINTS evenly-spread points; always first + last."""
    uniq = sorted(set(open_times))
    if len(uniq) <= _MAX_TRUNC_POINTS:
        return uniq
    step = (len(uniq) - 1) / (_MAX_TRUNC_POINTS - 1)
    idxs = sorted({round(i * step) for i in range(_MAX_TRUNC_POINTS)})
    return [uniq[i] for i in idxs]


def _first_lookahead_violation(detector: Detector, df: pd.DataFrame) -> int | None:
    """Return the first open_time whose signals change under truncation, else None.

    None also means "no signals" (vacuously causal) — callers skip that case.
    """
    full = detector(df)
    if full.empty:
        return None
    for t in _truncation_points(full["open_time"].astype("int64").tolist()):
        trunc = df[df["open_time"] <= t].reset_index(drop=True)
        if _normalize_signals_at(detector(trunc), t) != _normalize_signals_at(full, t):
            return t
    return None


def test_harness_catches_injected_lookahead() -> None:
    """A detector that reads the NEXT bar must be flagged — proves the harness bites."""
    df = _load_fixture("1d")

    def peeking(d: pd.DataFrame) -> pd.DataFrame:
        # Emit a long signal at every bar i<n-1 with sl_price taken from the
        # FUTURE bar's close — a textbook lookahead.
        n = len(d)
        ot = d["open_time"].to_numpy()
        cl = d["close"].to_numpy(dtype=float)
        rows = [
            {"open_time": int(ot[i]), "direction": "long",
             "reason": "peek", "sl_price": float(cl[i + 1])}
            for i in range(n - 1)
        ]
        return pd.DataFrame(rows, columns=["open_time", "direction", "reason", "sl_price"])

    assert _first_lookahead_violation(peeking, df) is not None
```

- [ ] **Step 2: Run the meta-test to verify the harness has teeth**

Run: `poetry run pytest tests/test_lookahead.py::test_harness_catches_injected_lookahead -v`
Expected: PASS — the injected look-ahead detector is flagged (the signal at the last sampled bar vanishes under truncation, so the at-t sets differ).

- [ ] **Step 3: Add the parametrized detector causality test**

Append to `tests/test_lookahead.py`:

```python
@pytest.mark.parametrize("tf", ["4h", "1d", "1wk"])
@pytest.mark.parametrize("detector_name", sorted(DETECTOR_REGISTRY))
def test_detector_has_no_lookahead(detector_name: str, tf: str) -> None:
    detector = DETECTOR_REGISTRY[detector_name]
    df = _load_fixture(tf)
    if detector(df).empty:
        pytest.skip(f"{detector_name} emits no signals on the {tf} fixture")
    violation = _first_lookahead_violation(detector, df)
    assert violation is None, (
        f"{detector_name} on {tf}: signals at open_time={violation} differ between "
        f"the full-series and truncated-at-t runs — the detector reads future bars."
    )
```

- [ ] **Step 4: Run the full harness to confirm all detectors are causal**

Run: `poetry run pytest tests/test_lookahead.py -v`
Expected: PASS — meta-test green; `test_detector_has_no_lookahead` green for all firing (detector × tf) cells, skipped for the non-firing ones (orb on 1d/1wk; ote_entry on 1wk).
If any cell FAILS: a real leak is found. Read the named detector, fix the future-bar read, re-run, and record the fix + any golden movement in the audit doc (Task 4) and the PR body.

- [ ] **Step 5: Commit**

```bash
git add tests/test_lookahead.py
git commit -m "test(lookahead): truncated-series causality harness across all 16 detectors"
```

---

### Task 2: Backtest entry-path lookahead guards

**Files:**

- Modify: `tests/test_lookahead.py`

- [ ] **Step 1: Write the failing/guard tests**

Append to `tests/test_lookahead.py` (imports `_candle` / `_make_ohlcv` from the shared conftest helpers):

```python
from analytics.backtest.engine import run_backtest  # noqa: E402
from tests.conftest import _candle, _make_ohlcv  # noqa: E402

_T = 1_700_000_000_000


def _long_signal(open_time: int) -> pd.DataFrame:
    return pd.DataFrame(
        [{"open_time": open_time, "direction": "long", "reason": "test"}],
        columns=["open_time", "direction", "reason"],
    )


def test_backtest_entry_is_strictly_next_bar_open() -> None:
    """Entry fills at the NEXT bar's open — never the signal bar's close, never a future bar."""
    ohlcv = _make_ohlcv(
        [
            _candle(_T + 0, 100, 105, 95, 102),  # idx 0: signal candle (close=102)
            _candle(_T + 1, 100, 103, 99, 101),  # idx 1: entry candle (open=100)
            _candle(_T + 2, 101, 106, 99, 105),  # idx 2: resolves
        ]
    )
    res = run_backtest(ohlcv, _long_signal(_T + 0), "AAPL", "4h", "fvg", sl_pct=0.02, tp_r=2.0)
    assert len(res.trades) == 1
    tr = res.trades[0]
    assert tr.signal_time == _T + 0
    assert tr.entry_time == _T + 1  # strictly the next bar
    assert tr.entry_price == pytest.approx(100.0)  # that bar's OPEN, not the signal close (102)


def test_backtest_entry_independent_of_future_bars() -> None:
    """Truncating the series right after the entry bar leaves entry price/time unchanged."""
    candles = [
        _candle(_T + 0, 100, 105, 95, 102),  # signal
        _candle(_T + 1, 100, 103, 99, 101),  # entry (no SL/TP hit here)
        _candle(_T + 2, 101, 106, 99, 105),  # would resolve the trade in the full run
        _candle(_T + 3, 105, 130, 104, 129),  # large future move
    ]
    sig = _long_signal(_T + 0)
    full = run_backtest(_make_ohlcv(candles), sig, "AAPL", "4h", "fvg", sl_pct=0.02, tp_r=2.0)
    trunc = run_backtest(_make_ohlcv(candles[:2]), sig, "AAPL", "4h", "fvg", sl_pct=0.02, tp_r=2.0)
    assert len(full.trades) == 1 and len(trunc.trades) == 1
    assert trunc.trades[0].entry_time == full.trades[0].entry_time == _T + 1
    assert trunc.trades[0].entry_price == pytest.approx(full.trades[0].entry_price)
    assert trunc.trades[0].entry_price == pytest.approx(100.0)
```

- [ ] **Step 2: Run to confirm the entry path is causal**

Run: `poetry run pytest tests/test_lookahead.py -k "entry" -v`
Expected: PASS — entry fills at the next bar's open (100.0, not the signal close 102.0); the truncated-after-entry run produces a byte-identical entry. (`trunc` leaves the trade `open` — `run_backtest` still records it in `.trades`, confirmed 2026-06-09.)

- [ ] **Step 3: Run the whole file**

Run: `poetry run pytest tests/test_lookahead.py -v`
Expected: PASS (meta-test + detector matrix + 2 entry-path tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_lookahead.py
git commit -m "test(lookahead): lock next-bar-open fill + entry independence from future bars"
```

---

### Task 3: Data-adjustment convention — guard test + docstring hardening

**Files:**

- Modify: `tests/test_yfinance_client.py`
- Modify: `utils/yfinance_client.py`

- [ ] **Step 1: Write the failing/guard test**

The existing `test_fetch_history_calls_yfinance_with_canonical_args` already pins `auto_adjust=False, actions=False`. Add a second, lookahead-framed guard that fails if anyone ever flips to auto-adjusted (which would silently change the price basis and deepen the as-of violation). Append to `tests/test_yfinance_client.py`:

```python
def test_fetch_history_never_auto_adjusts_prices() -> None:
    """As-of adjustment guard: auto_adjust MUST stay False so the close is the raw
    print. (Split factors are still back-applied by yfinance — a bounded as-of
    violation documented in the module docstring and docs/redesign/phase0-lookahead-audit.md.)
    """
    from utils.yfinance_client import fetch_history

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame(
        {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100]},
        index=pd.DatetimeIndex(["2026-01-02"], tz="America/New_York"),
    )
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
        fetch_history("AAPL", interval="1d")
    kwargs = mock_ticker.history.call_args.kwargs
    assert kwargs["auto_adjust"] is False
    assert kwargs["actions"] is False
```

- [ ] **Step 2: Run to verify it passes against current code**

Run: `poetry run pytest tests/test_yfinance_client.py -v`
Expected: PASS (3 tests) — current code already satisfies the convention.

- [ ] **Step 3: Harden the docstring to state the convention precisely**

In `utils/yfinance_client.py`, replace the final docstring paragraph of `fetch_history` (the `auto_adjust=False ...` block) with:

```python
    As-of adjustment convention (Phase 0.2 lookahead audit):
    ``auto_adjust=False`` preserves the raw print as ``close`` (absolute S/R
    levels need it). ``actions=False`` strips the Dividends/Stock-Splits columns.
    Dividends are **not** back-adjusted. **Splits are still back-applied** by
    yfinance to historical OHLC, so a split effective after a given bar is
    embedded into that bar's price — a mild, bounded as-of violation. On the
    liquid mega-cap universe splits are rare and this bias is small; eliminating
    it would require unadjusted data plus manual as-of corporate-action
    application, which is out of the free-data scope. See
    ``docs/redesign/phase0-lookahead-audit.md``.
```

- [ ] **Step 4: Re-run the gate for this module**

Run: `poetry run pytest tests/test_yfinance_client.py -v && make typecheck`
Expected: PASS — tests green, mypy strict clean (docstring-only change).

- [ ] **Step 5: Commit**

```bash
git add utils/yfinance_client.py tests/test_yfinance_client.py
git commit -m "test(lookahead): guard + document the yfinance as-of adjustment convention"
```

---

### Task 4: Write the lookahead audit document

**Files:**

- Create: `docs/redesign/phase0-lookahead-audit.md`

- [ ] **Step 1: Write the audit doc**

Create `docs/redesign/phase0-lookahead-audit.md` with the structure below. The read-site enumeration and classifications are seeded from the 2026-06-09 grounding pass — verify each against the current code while writing, and append any site you find that is not listed.

````markdown
# Phase 0.2 — Lookahead / Leakage Audit

**Date:** 2026-06-09
**Deliverable:** Phase 0.2 of `docs/superpowers/specs/2026-06-05-phase0-correctness-foundation-design.md` §4.2
**Guard:** `tests/test_lookahead.py` (truncated-series causality harness; all 16 detectors + backtest entry path)

## Method

Every place the pipeline reads historical OHLCV is enumerated and classified:

- **causal** — the value at bar t depends only on bars with `open_time <= t`. Proven empirically by the harness where applicable.
- **justified** — reads "future" bars, but only to resolve the *realized* outcome of an already-committed decision (not to make the decision). Acceptable.
- **leaking** — depends on bars after the decision bar. Must be fixed (flag any golden move).

## Read-site classification

| # | Read site | File : function | Class | Evidence |
| - | --------- | --------------- | ----- | -------- |
| 1 | Detector signal emission (16 detectors) | `analytics/strategies/*.py` via `DETECTOR_REGISTRY` | causal | Harness `test_detector_has_no_lookahead` green across 4h/1d/1wk. Forward-scan detectors (`wick_fill`, `fvg`, `eqh_eql`, `order_block`, `marubozu`) emit on the *fill/retest* bar j and read only bars i..j (all <= j). |
| 2 | Volume confirmation rolling mean | `analytics/strategies/_shared.py : volume_confirm` | causal | Trailing window (`center=False`); the signal bar's volume vs the prior rolling mean. |
| 3 | EMA / slope | `analytics/strategies/_shared.py : compute_ema`, `ema_cross_count` | causal | `ewm` value at i is a weighted sum of bars <= i; truncating future bars cannot change it. |
| 4 | Backtest entry fill | `analytics/backtest/engine.py : run_backtest` (`entry_idx = sig_idx + 1; entry_price = opens_np[entry_idx]`) | causal | Harness `test_backtest_entry_is_strictly_next_bar_open` + `..._independent_of_future_bars`. Next-bar-open. |
| 5 | Backtest exit resolution | `analytics/backtest/engine.py : run_backtest` (`highs_np[entry_idx:]`, `lows_np[entry_idx:]`) | justified | Walks forward to realize SL/TP of an already-placed trade — outcome, not decision. |
| 6 | ATR-based SL / floor | `analytics/backtest/engine.py : _compute_atr14(.., sig_idx)`; `analytics/signal/atr_floor.py` | causal | Trailing 14 bars ending at `sig_idx`. |
| 7 | Regime gate (live + replay) | `analytics/signal/gates.py : _apply_regime_gate`; `engine.py : _resolve_regime_at` | causal | Live `iloc[-2]` (last *closed* HTF bar); replay mirrors it via `searchsorted` for the largest HTF `open_time <= signal_time`. |
| 8 | HTF-EMA slope gate | `analytics/signal/gates.py : _apply_htf_ema_gate`; `engine.py : _resolve_series_at` | causal | Same `<= signal_time` searchsorted lookup as #7. |
| 9 | Live forming-bar exclusion | `analytics/signal/scanner.py : scan_symbol` | causal | Forming bar excluded by wall-clock `now < open_time + tf_ms` (go-live fix #67); catch-up path keeps own-candle entry. Covered by `tests/test_catch_up.py`. |
| 10 | Price-adjustment basis | `utils/yfinance_client.py : fetch_history` (`auto_adjust=False, actions=False`) | justified (bounded) | Raw close; dividends not back-adjusted; **splits back-applied** by yfinance = mild as-of violation, small on mega-caps, out of free-data scope to fix. Guarded by `tests/test_yfinance_client.py`. |
| 11 | Descriptive stats / seasonality | `analytics/stats/*`, `analytics/strategies/_seasonality.py` | (verify) | Computed over the full series for *display/context*, not as a live signal gate. Confirm while writing that no stat feeds a gate; if one does, it must be made as-of. |

## Findings

- _List any leak discovered while running the Task 1/2 harness, the fix, and whether goldens moved. If none: "No leaks found — the pipeline is causal; the only as-of caveat is the bounded split back-adjustment (row 10), accepted and documented."_

## Adjustment convention (row 10, expanded)

State the chosen convention verbatim with the `utils/yfinance_client.py` docstring, and the bound: mega-cap split frequency over the sample window, why dividend non-adjustment is acceptable for signal geometry, and the upgrade path (unadjusted feed + as-of corporate actions) deferred with the universe/data work.
````

- [ ] **Step 2: Verify every row against the code**

Run a spot-check that the cited symbols exist:

```bash
grep -n "entry_idx = sig_idx + 1\|_resolve_regime_at\|_resolve_series_at" analytics/backtest/engine.py
grep -n "def volume_confirm\|def compute_ema" analytics/strategies/_shared.py
grep -n "def _apply_regime_gate\|def _apply_htf_ema_gate" analytics/signal/gates.py
```

Expected: each grep returns the cited line. Fix any drift in the table before committing.

- [ ] **Step 3: Lint the doc**

Run: `make lint-md`
Expected: PASS (the audit doc passes markdownlint). Hand-format to the full default ruleset — blank lines around headings/lists/code fences, padded table separators, dash bullets, no trailing whitespace (CI markdownlint is stricter than local; see `project_markdownlint_ci_divergence`).

- [ ] **Step 4: Commit**

```bash
git add docs/redesign/phase0-lookahead-audit.md
git commit -m "docs(lookahead): Phase 0.2 read-site audit + classification"
```

---

### Task 5: Docs sync + regression-golden verification + final gate + PR

**Files:**

- Modify: `CLAUDE.md`
- Modify: `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/MEMORY.md`

- [ ] **Step 1: Add a pointer in `CLAUDE.md`**

In the `tests/` bullet of the Project Structure section, append a sentence:

```markdown
  `tests/test_lookahead.py` is the Phase 0.2 causality harness — a truncated-series property test asserting no detector or backtest fill depends on bars after its `open_time`; the written audit lives at `docs/redesign/phase0-lookahead-audit.md`.
```

- [ ] **Step 2: Verify regression goldens are byte-identical**

Run: `make test-regression`
Expected: PASS / SKIP unchanged — this deliverable adds only tests + docs and changes no detector or engine code, so no golden moves. If a golden moves, STOP — a leak fix slipped in; review it as a deliberate change before proceeding.

- [ ] **Step 3: Full project gate**

Run: `make lint-py && make typecheck && make test && make lint-md`
Expected: all green (ruff format+lint, mypy strict, full pytest suite incl. the new harness, markdownlint).

- [ ] **Step 4: Update MEMORY Current State**

Per the Session Memory Protocol, prepend a one-line Current State entry to `MEMORY.md` summarizing: Phase 0.2 lookahead audit + harness shipped; `tests/test_lookahead.py` (detector matrix + entry-path + meta-test); audit doc; adjustment convention documented; goldens byte-identical, no db-update; next Phase 0 step = 0.3a DSR + 0.3b PBO.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(lookahead): point CLAUDE.md at the Phase 0.2 causality harness"
```

- [ ] **Step 6: Push + open PR**

```bash
git push -u origin feat/phase0-lookahead-audit
gh pr create --repo s10023/buibui-wifey-wall-street-bot --base main \
  --title "test(lookahead): Phase 0.2 causality harness + read-site audit" \
  --body "Implements Phase 0.2 from docs/superpowers/specs/2026-06-05-phase0-correctness-foundation-design.md. Truncated-series harness across all 16 detectors + the backtest entry path; written read-site audit; yfinance as-of adjustment convention documented + guarded. Additive tests + docs only — regression goldens byte-identical, no db-update."
```

---

## Self-Review

**Spec coverage (§4.2):**

- "written audit enumerating every place historical data is read, classifying causal / leaking / justified" → Task 4 (`docs/redesign/phase0-lookahead-audit.md`, 11-row table). ✓
- "lookahead test harness: feeds a detector/backtest a series truncated at t and asserts the signal at t is identical to the full series" → Task 1 (`_first_lookahead_violation` over all 16 detectors) + Task 2 (entry path). ✓
- "green across all 16 detectors + the backtest entry path" → Task 1 Step 4 (detector matrix) + Task 2 (entry tests). ✓
- "adjustment lookahead: confirm/establish as-of adjustment ... document the chosen convention in `utils/yfinance_client.py`" → Task 3 (docstring) + Task 4 row 10. ✓
- "DoD: any genuine leak fixed (may move goldens — flag per-fix)" → Scope notes + Task 1 Step 4 + Task 5 Step 2 STOP guard. ✓
- "lives in tests/" → `tests/test_lookahead.py`. ✓
- §6 "truncated-series property tests for 0.2" → Task 1/2. ✓
- §6 "regression goldens byte-identical (proven before merge)" → Task 5 Step 2. ✓

**Placeholder scan:** none — all test code is complete; the audit-doc Findings bullet and row 11 carry explicit "verify/list while writing" instructions, not code TODOs. The audit table is seeded with concrete file:function citations spot-checked in Task 4 Step 2.

**Type consistency:** `Detector` alias, `_load_fixture(tf)`, `_normalize_signals_at(sig, t)`, `_truncation_points(open_times)`, `_first_lookahead_violation(detector, df) -> int | None`, and `_long_signal(open_time)` are defined once and used identically across Tasks 1–2. `run_backtest(...).trades[0].entry_time/.entry_price/.signal_time` match the `Trade` dataclass fields in `engine.py`. The yfinance guard asserts the same kwargs (`auto_adjust`, `actions`) the existing canonical-args test pins. ✓
