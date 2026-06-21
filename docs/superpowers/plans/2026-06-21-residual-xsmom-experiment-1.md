# Residualized XS-Momentum (Experiment #1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resurrect the cross-sectional momentum sleeve "done right" —
beta/sector-residualized returns, skip-month (slow EWMAC speeds only), broadened
to the current S&P 500 — and score a pre-registered `{mega,broad}×{raw,residual+
skip}` 2×2 to decide whether equity momentum is recoverable.

**Architecture:** Additive, default-off extension of `analytics/xsmom/`. A new
pure `analytics/xsmom/residual.py` builds residual price series (causal rolling
beta → residual returns → synthetic price) and a residual-forecast leverage
matrix; `run_xs_backtest` gains one keyword-only `leverage=` injection point so
the residual leg sizes on residual *signal* but books P&L on *actual* prices.
`replay.py` runs the 2×2 grid, `report.py` scores it against the pre-registered
gate, and a read-only `tools/xsmom_residual_audit.py` prints the verdict. The
default code path is byte-identical, so the regression goldens do not move.

**Tech Stack:** Python 3.11, pandas, numpy, DuckDB (read-only replay),
`analytics.forecast` EWMAC primitives, `analytics.research_guards`
(DSR/PBO/bootstrap/MinTRL), pytest + in-memory DuckDB, ruff, mypy strict.

---

## File Structure

- `tools/expand_universe_sp500.py` *(new)* — one-shot: snapshot the existing
  S&P-100 stock symbols, then merge current S&P 500 constituents (+GICS sectors)
  into `config/universe.json`. Idempotent.
- `config/universe.json` *(modify — data)* — expanded to ~500 stocks + 4 ETFs.
- `config/universe_sp100_snapshot.json` *(new — data)* — the pre-expansion
  S&P-100 stock list, the stable "mega" arm of the 2×2.
- `analytics/xsmom/residual.py` *(new, pure)* — `rolling_beta`,
  `residual_returns`, `residual_close`, `residual_closes`,
  `sector_neutral_demean`, `xs_residual_leverage`.
- `analytics/xsmom/book.py` *(modify)* — `run_xs_backtest(..., *, leverage=None)`.
- `analytics/xsmom/replay.py` *(modify)* — `_sector_map`, `replay_residual_grid`.
- `analytics/xsmom/report.py` *(modify)* — `ResidualGridReport`,
  `evaluate_residual_grid`.
- `analytics/xsmom/__init__.py` *(modify)* — export the new public names.
- `tools/xsmom_residual_audit.py` *(new, read-only)* — the 2×2 audit + verdict.
- `Makefile` *(modify)* — `wifey-xsmom-residual-audit` target + `.PHONY`.
- `tests/test_xsmom_residual.py`, `tests/test_xsmom_residual_replay.py`,
  `tests/test_xsmom_residual_report.py`, `tests/test_expand_universe_sp500.py`
  *(new)*.
- `docs/audits/2026-06-21-experiment-1-residual-xsmom.md` *(new)* — verdict note.

Pre-registered constants used throughout (a-priori, never swept):

- `_SLOW_SPEEDS = ((16, 64, 3.75), (32, 128, 2.65), (64, 256, 1.91))` — the
  default speeds minus the fast `(8, 32)` leg (the skip-month analog).
- `_BETA_WINDOW = 252` — trailing sessions for the causal market beta.
- Gate: `dsr >= 0.95 ∧ pbo <= 0.5 ∧ boot_lo > 0 ∧ n_obs >= min_trl ∧
  sharpe_annual >= 0.7`, read on the `broad_residual_skip` cell.

---

## Task 1: Universe expansion script

**Files:**

- Create: `tools/expand_universe_sp500.py`
- Test: `tests/test_expand_universe_sp500.py`

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path

from tools.expand_universe_sp500 import merge_constituents


def test_merge_preserves_existing_and_adds_new(tmp_path: Path) -> None:
    universe = {
        "universe_policy": {"scope": "x", "as_of": "fixed", "survivorship_note": "n"},
        "membership_as_of": "2026-06-16",
        "members": {
            "AAPL": {"sector": "Information Technology", "kind": "stock",
                     "delisted": False},
            "SPY": {"sector": "ETF", "kind": "etf", "delisted": False},
        },
    }
    # constituents = (symbol, sector) rows; AAPL already present, FOO is new.
    constituents = [("AAPL", "Information Technology"), ("FOO", "Industrials")]
    merged, snapshot = merge_constituents(universe, constituents)

    assert snapshot == ["AAPL"]  # pre-existing STOCK symbols only (SPY excluded)
    assert merged["members"]["FOO"] == {
        "sector": "Industrials", "kind": "stock", "delisted": False,
    }
    # existing entries untouched (idempotent on AAPL, ETF preserved)
    assert merged["members"]["AAPL"]["sector"] == "Information Technology"
    assert merged["members"]["SPY"]["kind"] == "etf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_expand_universe_sp500.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: merge_constituents`.

- [ ] **Step 3: Write minimal implementation**

```python
"""One-shot: expand config/universe.json from S&P 100 to the current S&P 500.

Snapshots the pre-expansion stock symbols (the stable "mega" arm of experiment
#1's 2x2) to config/universe_sp100_snapshot.json, then merges the current S&P 500
constituents (+GICS sectors) as kind=stock/delisted=False members. Existing
entries (including ETFs and any listed dates) are preserved untouched.

Constituents source: by default scraped free from Wikipedia's "List of S&P 500
companies" via pandas.read_html; pass --from-csv PATH (columns: Symbol,Sector)
for a deterministic/offline run. Run once, then commit universe.json + snapshot
and re-run `make wifey-universe-backfill`.

Usage::

    PYTHONPATH=. poetry run python tools/expand_universe_sp500.py
    PYTHONPATH=. poetry run python tools/expand_universe_sp500.py --from-csv sp500.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

UNIVERSE_PATH = Path("config/universe.json")
SNAPSHOT_PATH = Path("config/universe_sp100_snapshot.json")


def merge_constituents(
    universe: dict[str, object],
    constituents: list[tuple[str, str]],
) -> tuple[dict[str, object], list[str]]:
    """Return (merged_universe, sp100_snapshot).

    Snapshot = the sorted pre-existing kind=="stock" symbols. New constituent
    symbols are added as kind=stock/delisted=False; existing members are left
    byte-identical (idempotent).
    """
    members: dict[str, dict[str, object]] = dict(universe["members"])  # type: ignore[arg-type]
    snapshot = sorted(s for s, m in members.items() if m.get("kind") == "stock")
    for symbol, sector in constituents:
        if symbol not in members:
            members[symbol] = {
                "sector": sector, "kind": "stock", "delisted": False,
            }
    merged = dict(universe)
    merged["members"] = members
    return merged, snapshot


def _load_constituents_from_csv(path: Path) -> list[tuple[str, str]]:
    import csv

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [(row["Symbol"].strip().upper(), row["Sector"].strip())
                for row in reader if row.get("Symbol")]


def _load_constituents_from_wikipedia() -> list[tuple[str, str]]:
    import pandas as pd

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    table = pd.read_html(url)[0]
    return [
        (str(s).strip().upper().replace(".", "-"), str(sec).strip())
        for s, sec in zip(table["Symbol"], table["GICS Sector"])
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-csv", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=UNIVERSE_PATH)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    args = parser.parse_args()

    universe = json.loads(args.universe.read_text())
    constituents = (
        _load_constituents_from_csv(args.from_csv)
        if args.from_csv
        else _load_constituents_from_wikipedia()
    )
    merged, snapshot = merge_constituents(universe, constituents)
    args.universe.write_text(json.dumps(merged, indent=2) + "\n")
    args.snapshot.write_text(json.dumps(snapshot, indent=2) + "\n")
    n_new = len(merged["members"]) - len(universe["members"])
    print(f"Merged {len(constituents)} constituents (+{n_new} new). "
          f"Snapshot: {len(snapshot)} S&P-100 stocks -> {args.snapshot}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_expand_universe_sp500.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/expand_universe_sp500.py tests/test_expand_universe_sp500.py
git commit -m "feat(xsmom): S&P 500 universe-expansion script (experiment #1 prereq)"
```

---

## Task 2: Expand the universe + backfill (one-time data op)

**Files:**

- Modify: `config/universe.json`
- Create: `config/universe_sp100_snapshot.json`

This task runs commands; there is no new code. Network + time heavy (~400 new
symbols × 1d+1wk). Expect a handful of tickers to fail (rebrands/illiquid) — the
data-quality gate quarantines bad rows and `universe-coverage` reports holes;
that is acceptable and flagged, not a blocker.

- [ ] **Step 1: Snapshot + expand**

Run (default scrapes Wikipedia; use `--from-csv` if offline):

```bash
PYTHONPATH=. poetry run python tools/expand_universe_sp500.py
```

Expected: prints `Merged ~503 constituents (+~400 new). Snapshot: 101 S&P-100
stocks`. `config/universe.json` now has ~500 stock members + the 4 ETFs;
`config/universe_sp100_snapshot.json` lists the original 101.

- [ ] **Step 2: Validate the expanded universe loads**

Run:

```bash
PYTHONPATH=. poetry run python -c "from utils.config_validation import load_research_universe as L; u=L(); print('stocks', len(u.stocks()), 'members', len(u.members))"
```

Expected: `stocks ~500 members ~504` and no validation error.

- [ ] **Step 3: Backfill the expanded universe (1d + 1wk from 2018)**

Run:

```bash
make wifey-universe-backfill SINCE=2018-01-01
```

Expected: completes; new symbols ingested. (4h is bounded ~730d; fine.)

- [ ] **Step 4: Coverage report**

Run:

```bash
make universe-coverage
```

Expected: per-(member × timeframe) bar counts; note any missing symbols. A small
missing set is acceptable (flag it in the verdict doc, Task 12).

- [ ] **Step 5: Commit the data artifacts**

```bash
git add config/universe.json config/universe_sp100_snapshot.json
git commit -m "data(xsmom): expand universe to current S&P 500 (experiment #1)"
```

---

## Task 3: `rolling_beta` (causal trailing beta)

**Files:**

- Create: `analytics/xsmom/residual.py`
- Test: `tests/test_xsmom_residual.py`

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pandas as pd

from analytics.xsmom.residual import rolling_beta


def test_rolling_beta_recovers_known_beta() -> None:
    idx = pd.date_range("2020-01-01", periods=40, freq="D")
    rng = np.random.default_rng(0)
    mkt = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    inst = 1.5 * mkt + pd.Series(rng.normal(0, 1e-6, 40), index=idx)
    beta = rolling_beta(inst, mkt, window=20)
    assert np.isnan(beta.iloc[18])  # warm-up: < window obs
    assert abs(beta.iloc[-1] - 1.5) < 0.01


def test_rolling_beta_is_causal() -> None:
    idx = pd.date_range("2020-01-01", periods=40, freq="D")
    rng = np.random.default_rng(1)
    mkt = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    inst = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    base = rolling_beta(inst, mkt, window=20)
    inst2 = inst.copy()
    inst2.iloc[30] *= 5.0  # bump a FUTURE bar
    pert = rolling_beta(inst2, mkt, window=20)
    assert base.iloc[25] == pert.iloc[25]  # past beta unchanged
    assert base.iloc[30] != pert.iloc[30]  # bumped bar changed (non-vacuous)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -v`
Expected: FAIL with `ModuleNotFoundError: analytics.xsmom.residual`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Residual (beta/sector-neutral) cross-sectional momentum primitives.

Pure: numpy + pandas + the forecast EWMAC/vol primitives. No DB, no engine. Every
transform is causal — a trailing rolling window or a same-day reduction over
already-causal inputs; positions are shifted downstream in `xs_residual_leverage`.
Experiment #1 (docs/superpowers/specs/2026-06-21-equity-edge-hunt-roadmap-
residual-xsmom-design.md).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.vol import ew_return_vol
from analytics.xsmom.book import xs_forecasts
from analytics.xsmom.diagnostics import equal_weight_market_return


def rolling_beta(inst_ret: pd.Series, mkt_ret: pd.Series, window: int) -> pd.Series:
    """Causal trailing OLS beta = Cov(inst, mkt) / Var(mkt) over `window` bars.

    `min_periods=window` so warm-up bars are NaN. Uses only data through each day
    (a trailing window never reads future bars). inf/zero-var -> NaN.
    """
    df = pd.concat([inst_ret, mkt_ret], axis=1, keys=["i", "m"])
    cov = df["i"].rolling(window, min_periods=window).cov(df["m"])
    var = df["m"].rolling(window, min_periods=window).var()
    beta = cov / var
    return beta.replace([np.inf, -np.inf], np.nan)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/residual.py tests/test_xsmom_residual.py
git commit -m "feat(xsmom): causal rolling_beta for residual momentum"
```

---

## Task 4: `residual_returns`

**Files:**

- Modify: `analytics/xsmom/residual.py`
- Test: `tests/test_xsmom_residual.py`

- [ ] **Step 1: Write the failing test**

```python
from analytics.xsmom.residual import residual_returns


def test_residual_returns_strips_market_component() -> None:
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    inst = pd.Series([0.02, 0.03, -0.01, 0.00, 0.05], index=idx)
    mkt = pd.Series([0.01, 0.01, -0.01, 0.00, 0.02], index=idx)
    beta = pd.Series([2.0, 2.0, 2.0, 2.0, 2.0], index=idx)
    resid = residual_returns(inst, mkt, beta)
    # r_i - beta * r_mkt
    assert abs(resid.iloc[0] - (0.02 - 2.0 * 0.01)) < 1e-12
    assert abs(resid.iloc[4] - (0.05 - 2.0 * 0.02)) < 1e-12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py::test_residual_returns_strips_market_component -v`
Expected: FAIL with `ImportError: residual_returns`.

- [ ] **Step 3: Write minimal implementation** (append to `residual.py`)

```python
def residual_returns(
    inst_ret: pd.Series, mkt_ret: pd.Series, beta: pd.Series
) -> pd.Series:
    """`r_i - beta_i * r_mkt`, aligned on the union of the three indices."""
    idx = inst_ret.index.union(mkt_ret.index).union(beta.index)
    i = inst_ret.reindex(idx)
    m = mkt_ret.reindex(idx)
    b = beta.reindex(idx)
    return i - b * m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/residual.py tests/test_xsmom_residual.py
git commit -m "feat(xsmom): residual_returns (strip market beta)"
```

---

## Task 5: `residual_close` + `residual_closes` (synthetic residual price)

**Files:**

- Modify: `analytics/xsmom/residual.py`
- Test: `tests/test_xsmom_residual.py`

- [ ] **Step 1: Write the failing test**

```python
from analytics.xsmom.residual import residual_close, residual_closes


def test_residual_close_builds_price_and_is_causal() -> None:
    idx = pd.date_range("2020-01-01", periods=60, freq="D")
    close = pd.Series(100.0 + np.arange(60), index=idx)
    mkt = pd.Series(np.linspace(0.001, 0.002, 60), index=idx)
    base = residual_close(close, mkt, window=20)
    assert base.notna().sum() > 0  # a usable price series emerges post warm-up
    close2 = close.copy()
    close2.iloc[50] *= 1.5  # bump a FUTURE bar
    pert = residual_close(close2, mkt, window=20)
    assert base.iloc[40] == pert.iloc[40]  # past price unchanged (causal)
    assert base.iloc[50] != pert.iloc[50]  # bumped bar changed (non-vacuous)


def test_residual_closes_covers_all_symbols() -> None:
    idx = pd.date_range("2020-01-01", periods=60, freq="D")
    closes = {
        "A": pd.Series(100.0 + np.arange(60), index=idx),
        "B": pd.Series(50.0 + 2 * np.arange(60), index=idx),
    }
    out = residual_closes(closes, window=20)
    assert set(out) == {"A", "B"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -k residual_close -v`
Expected: FAIL with `ImportError: residual_close`.

- [ ] **Step 3: Write minimal implementation** (append to `residual.py`)

```python
_BETA_WINDOW = 252  # a-priori trailing sessions for the market beta


def residual_close(
    inst_close: pd.Series, mkt_ret: pd.Series, window: int
) -> pd.Series:
    """Synthetic residual *price* = cumprod(1 + residual_returns).

    Feedable to `combine_forecasts` as a price series so EWMAC momentum is
    computed on the beta-stripped path. Leading warm-up bars are NaN.
    """
    inst_ret = inst_close.pct_change()
    beta = rolling_beta(inst_ret, mkt_ret, window)
    resid = residual_returns(inst_ret, mkt_ret, beta)
    return (1.0 + resid).cumprod()


def residual_closes(
    closes: dict[str, pd.Series], window: int = _BETA_WINDOW
) -> dict[str, pd.Series]:
    """Residual price series per instrument vs the equal-weight market."""
    mkt = equal_weight_market_return(closes)
    return {sym: residual_close(c, mkt, window) for sym, c in closes.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/residual.py tests/test_xsmom_residual.py
git commit -m "feat(xsmom): residual_close/residual_closes (causal synthetic price)"
```

---

## Task 6: `sector_neutral_demean`

**Files:**

- Modify: `analytics/xsmom/residual.py`
- Test: `tests/test_xsmom_residual.py`

- [ ] **Step 1: Write the failing test**

```python
from analytics.xsmom.residual import sector_neutral_demean


def test_sector_neutral_demean_zeroes_within_sector() -> None:
    idx = pd.date_range("2020-01-01", periods=2, freq="D")
    forecasts = pd.DataFrame(
        {"A": [1.0, 2.0], "B": [3.0, 4.0], "C": [10.0, 10.0]}, index=idx
    )
    sector_map = {"A": "Tech", "B": "Tech", "C": "Energy"}
    out = sector_neutral_demean(forecasts, sector_map)
    # within Tech each row sums to ~0; single-name Energy sector -> 0
    assert abs(out.loc[idx[0], "A"] + out.loc[idx[0], "B"]) < 1e-12
    assert abs(out.loc[idx[0], "A"] - (-1.0)) < 1e-12  # 1 - mean(1,3) = -1
    assert abs(out.loc[idx[0], "C"]) < 1e-12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -k sector_neutral -v`
Expected: FAIL with `ImportError: sector_neutral_demean`.

- [ ] **Step 3: Write minimal implementation** (append to `residual.py`)

```python
def sector_neutral_demean(
    forecasts: pd.DataFrame, sector_map: dict[str, str]
) -> pd.DataFrame:
    """Cross-sectionally demean within each GICS sector (skipna per row).

    Columns absent from `sector_map` are grouped under their own None bucket and
    demeaned among themselves. Each row of each sector group sums to ~0.
    """
    out = forecasts.copy()
    groups: dict[str | None, list[str]] = {}
    for col in forecasts.columns:
        groups.setdefault(sector_map.get(str(col)), []).append(str(col))
    for cols in groups.values():
        sub = forecasts[cols]
        out[cols] = sub.sub(sub.mean(axis=1), axis=0)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/residual.py tests/test_xsmom_residual.py
git commit -m "feat(xsmom): sector_neutral_demean"
```

---

## Task 7: `run_xs_backtest` leverage-injection point (default byte-identical)

**Files:**

- Modify: `analytics/xsmom/book.py:101-152`
- Test: `tests/test_xsmom_residual.py`

- [ ] **Step 1: Write the failing test**

```python
from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import run_xs_backtest, xs_leverage


def _toy_closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2020-01-01", periods=400, freq="D")
    rng = np.random.default_rng(3)
    out = {}
    for s in ("A", "B", "C", "D"):
        out[s] = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.01, 400)), index=idx)
    return out


def test_injected_default_leverage_is_byte_identical() -> None:
    closes = _toy_closes()
    fundings: dict[str, pd.Series] = {}
    cfg = ForecastConfig()
    base = run_xs_backtest(closes, fundings, cfg)
    lev = xs_leverage(closes, cfg)
    injected = run_xs_backtest(closes, fundings, cfg, leverage=lev)
    np.testing.assert_array_equal(base.portfolio_return, injected.portfolio_return)
    np.testing.assert_array_equal(base.governor, injected.governor)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -k injected_default -v`
Expected: FAIL with `TypeError: run_xs_backtest() got an unexpected keyword argument 'leverage'`.

- [ ] **Step 3: Write minimal implementation**

In `analytics/xsmom/book.py`, change the signature and the first body line of
`run_xs_backtest`. Current:

```python
def run_xs_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> XSBookResult:
```

becomes:

```python
def run_xs_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    leverage: pd.DataFrame | None = None,
) -> XSBookResult:
```

and the line `leverage = xs_leverage(closes, cfg)` becomes:

```python
    leverage = xs_leverage(closes, cfg) if leverage is None else leverage
```

Add to the docstring: "When ``leverage`` is supplied (e.g. the residual-forecast
matrix), it is used verbatim for sizing while P&L is still booked on the actual
``closes``; ``leverage=None`` reproduces the demeaned-EWMAC book byte-identically."

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -k injected_default -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/book.py tests/test_xsmom_residual.py
git commit -m "feat(xsmom): additive leverage= injection on run_xs_backtest (default identical)"
```

---

## Task 8: `xs_residual_leverage` (residual signal, actual-vol sizing)

**Files:**

- Modify: `analytics/xsmom/residual.py`
- Test: `tests/test_xsmom_residual.py`

- [ ] **Step 1: Write the failing test**

```python
from analytics.xsmom.residual import xs_residual_leverage


def test_xs_residual_leverage_shape_and_causality() -> None:
    closes = _toy_closes()  # defined in Task 7
    cfg = ForecastConfig(speeds=((16, 64, 3.75), (32, 128, 2.65)))
    lev = xs_residual_leverage(closes, cfg, beta_window=60)
    assert set(lev.columns) == set(closes)
    base = lev["A"].to_numpy()

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[-1] *= 1.5  # bump the LAST (future-most) bar of A
    lev2 = xs_residual_leverage(closes2, cfg, beta_window=60)
    a = lev["A"].to_numpy()
    b = lev2["A"].to_numpy()
    # leverage is shifted one day, so today's last-bar bump cannot move any
    # past leverage; assert the interior is unchanged (causal).
    np.testing.assert_array_equal(a[:-1], b[:-1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -k residual_leverage -v`
Expected: FAIL with `ImportError: xs_residual_leverage`.

- [ ] **Step 3: Write minimal implementation** (append to `residual.py`)

```python
def xs_residual_leverage(
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    beta_window: int = _BETA_WINDOW,
    sector_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Causal cross-sectional leverage from *residual* momentum, sized on
    *actual* return vol.

    Forecast = EWMAC on the residual price series (relative residual strength),
    demeaned cross-sectionally (sector-neutral when `sector_map` is given),
    `.shift(1)`-ed, then vol-parity scaled by each name's ACTUAL annualized return
    vol so the position targets real vol. Mirrors `xs_leverage` exactly except for
    the residual forecast input and the optional sector-neutral demean.
    """
    resid_closes = residual_closes(closes, beta_window)
    f = xs_forecasts(resid_closes, cfg)
    demeaned = (
        sector_neutral_demean(f, sector_map)
        if sector_map is not None
        else f.sub(f.mean(axis=1), axis=0)
    )
    demeaned_shifted = demeaned.shift(1)
    union = pd.DatetimeIndex(demeaned.index)
    ann = np.sqrt(cfg.annualization_days)

    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        lev = (demeaned_shifted[sym] / 10.0) * (cfg.vol_target_annual / vol_ann)
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    lev_df = pd.DataFrame(lev_cols, index=union)
    if cfg.xs_dollar_neutral:
        lev_df = lev_df.sub(lev_df.mean(axis=1), axis=0)
    return lev_df
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -v`
Expected: PASS (all residual-module tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/residual.py tests/test_xsmom_residual.py
git commit -m "feat(xsmom): xs_residual_leverage (residual signal, actual-vol sizing)"
```

- [ ] **Step 6: Write the failing test for the long-only leg**

```python
from analytics.xsmom.residual import long_only_residual_leverage


def test_long_only_residual_leverage_is_nonnegative_and_causal() -> None:
    closes = _toy_closes()  # defined in Task 7
    cfg = ForecastConfig(speeds=((16, 64, 3.75), (32, 128, 2.65)))
    lev = long_only_residual_leverage(closes, cfg, beta_window=60, quantile=0.5)
    stacked = lev.to_numpy()
    finite = stacked[np.isfinite(stacked)]
    assert (finite >= 0.0).all()  # long-only: no negative legs
    assert finite.any()  # at least some non-zero longs emerge

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[-1] *= 1.5  # bump the future-most bar
    lev2 = long_only_residual_leverage(closes2, cfg, beta_window=60, quantile=0.5)
    np.testing.assert_array_equal(lev["A"].to_numpy()[:-1], lev2["A"].to_numpy()[:-1])
```

- [ ] **Step 7: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -k long_only -v`
Expected: FAIL with `ImportError: long_only_residual_leverage`.

- [ ] **Step 8: Write minimal implementation** (append to `residual.py`)

```python
def long_only_residual_leverage(
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    beta_window: int = _BETA_WINDOW,
    sector_map: dict[str, str] | None = None,
    quantile: float = 0.8,
) -> pd.DataFrame:
    """Long-only top-`quantile` residual-momentum leverage (wife-sleeve form).

    Same residual forecast + (optional) sector-neutral demean + `.shift(1)` as
    `xs_residual_leverage`, but keep only names whose shifted demeaned forecast is
    in the top cross-sectional `quantile` AND positive each day; each kept name
    gets a unit vol-targeted long, everything else is 0 (no shorts, no
    dollar-neutral re-center). Causal.
    """
    resid_closes = residual_closes(closes, beta_window)
    f = xs_forecasts(resid_closes, cfg)
    demeaned = (
        sector_neutral_demean(f, sector_map)
        if sector_map is not None
        else f.sub(f.mean(axis=1), axis=0)
    )
    shifted = demeaned.shift(1)
    thresh = shifted.quantile(quantile, axis=1)
    longs = shifted.ge(thresh, axis=0) & shifted.gt(0.0)
    union = pd.DatetimeIndex(demeaned.index)
    ann = np.sqrt(cfg.annualization_days)

    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        unit = (cfg.vol_target_annual / vol_ann).replace([np.inf, -np.inf], np.nan)
        lev_cols[sym] = unit.where(longs[sym], 0.0)
    return pd.DataFrame(lev_cols, index=union)
```

- [ ] **Step 9: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual.py -v`
Expected: PASS (all residual-module tests, incl. long-only).

- [ ] **Step 10: Commit**

```bash
git add analytics/xsmom/residual.py tests/test_xsmom_residual.py
git commit -m "feat(xsmom): long_only_residual_leverage (top-quantile wife-sleeve leg)"
```

---

## Task 9: Replay — sector map + 2×2 grid

**Files:**

- Modify: `analytics/xsmom/replay.py`
- Test: `tests/test_xsmom_residual_replay.py`

- [ ] **Step 1: Write the failing test**

```python
import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.replay import replay_residual_grid


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    idx = pd.date_range("2018-01-01", periods=500, freq="D")
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, 500))
    df = pd.DataFrame(
        {
            "open_time": (idx.view("int64") // 10**6),
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1_000_000.0,
        }
    )
    upsert_ohlcv(conn, sym, "1d", df)


def test_replay_residual_grid_returns_four_books() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    sector_map = {s: ("Tech" if i % 2 == 0 else "Energy") for i, s in enumerate(syms)}
    cfg = ForecastConfig()
    books = replay_residual_grid(
        conn, cfg, beta_window=60, mega_symbols=syms[:3],
        broad_symbols=syms, sector_map=sector_map,
    )
    assert set(books) == {
        "mega_raw", "mega_residual_skip", "broad_raw", "broad_residual_skip",
    }
    assert books["broad_residual_skip"].portfolio_return.shape[0] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual_replay.py -v`
Expected: FAIL with `ImportError: replay_residual_grid`.

- [ ] **Step 3: Write minimal implementation** (append to `replay.py`; add imports)

Add to the imports block:

```python
from analytics.xsmom.residual import _SLOW_SPEEDS_DEFAULT, xs_residual_leverage
```

Add `_SLOW_SPEEDS_DEFAULT` to `residual.py` (near `_BETA_WINDOW`):

```python
_SLOW_SPEEDS_DEFAULT: tuple[tuple[int, int, float], ...] = (
    (16, 64, 3.75), (32, 128, 2.65), (64, 256, 1.91),
)  # default speeds minus the fast (8, 32) leg = the skip-month analog
```

Append to `replay.py`:

```python
def _sector_map() -> dict[str, str]:
    """`{symbol: GICS sector}` for active single-name stocks (read-only)."""
    uni = load_research_universe()
    return {
        m.symbol: m.sector
        for m in uni.members
        if not m.delisted and m.kind == "stock"
    }


def replay_residual_grid(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    beta_window: int,
    mega_symbols: list[str],
    broad_symbols: list[str],
    sector_map: dict[str, str],
) -> dict[str, XSBookResult]:
    """Run the pre-registered `{mega,broad} x {raw, residual+skip}` 2x2.

    `raw` = the original construction (default speeds, plain demean, actual
    closes). `residual_skip` = slow speeds + residual leverage + sector-neutral
    demean. P&L is booked on actual closes for every cell.
    """
    import dataclasses

    cfg_slow = dataclasses.replace(cfg, speeds=_SLOW_SPEEDS_DEFAULT)
    books: dict[str, XSBookResult] = {}
    for label, syms in (("mega", mega_symbols), ("broad", broad_symbols)):
        closes, fundings = load_daily_inputs(conn, syms)
        books[f"{label}_raw"] = run_xs_backtest(closes, fundings, cfg)
        sm = {s: sector_map[s] for s in syms if s in sector_map}
        lev = xs_residual_leverage(
            closes, cfg_slow, beta_window=beta_window, sector_map=sm
        )
        books[f"{label}_residual_skip"] = run_xs_backtest(
            closes, fundings, cfg_slow, leverage=lev
        )
    return books
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual_replay.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/replay.py analytics/xsmom/residual.py tests/test_xsmom_residual_replay.py
git commit -m "feat(xsmom): replay_residual_grid (the 2x2) + sector map loader"
```

---

## Task 10: Report — grid verdict against the pre-registered gate

**Files:**

- Modify: `analytics/xsmom/report.py`
- Test: `tests/test_xsmom_residual_report.py`

- [ ] **Step 1: Write the failing test**

```python
import numpy as np

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.report import evaluate_residual_grid


def _book(mean: float, seed: int, n: int = 600) -> XSBookResult:
    import pandas as pd

    rng = np.random.default_rng(seed)
    r = rng.normal(mean, 0.01, n)
    idx = pd.date_range("2018-01-01", periods=n, freq="D")
    return XSBookResult(
        daily_index=idx, portfolio_return=r, pre_governor_return=r,
        governor=np.ones(n), active_count=np.full(n, 5), per_instrument_net={},
    )


def test_evaluate_residual_grid_reads_committed_cell() -> None:
    cfg = ForecastConfig()
    books = {
        "mega_raw": _book(0.0, 1),
        "mega_residual_skip": _book(0.0002, 2),
        "broad_raw": _book(0.0, 3),
        "broad_residual_skip": _book(0.0008, 4),  # the pre-committed cell
    }
    trend = {"mega": np.zeros(600), "broad": np.zeros(600)}
    rep = evaluate_residual_grid(books, cfg, trend_by_universe=trend)
    assert rep.committed_key == "broad_residual_skip"
    assert set(rep.cells) == set(books)
    assert isinstance(rep.passed, bool)
    # the gate reads the committed cell only
    c = rep.cells["broad_residual_skip"]
    expected = (
        c.dsr >= 0.95 and c.pbo <= 0.5 and c.boot_lo > 0.0
        and c.n_obs >= c.min_trl and c.sharpe_annual >= 0.7
    )
    assert rep.passed == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual_report.py -v`
Expected: FAIL with `ImportError: evaluate_residual_grid`.

- [ ] **Step 3: Write minimal implementation** (append to `report.py`; extend imports)

Add to the imports: `import numpy.typing as npt` already present; ensure
`from analytics.xsmom.book import XSBookResult` (present). Append:

```python
@dataclass(frozen=True)
class ResidualGridReport:
    """The 2x2 grid's per-cell XSReports + the pre-registered gate verdict."""

    cells: dict[str, XSReport]
    committed_key: str
    passed: bool


_GATE_SHARPE = 0.7  # pre-registered equity long-short momentum bar


def evaluate_residual_grid(
    books: dict[str, XSBookResult],
    cfg: ForecastConfig,
    trend_by_universe: dict[str, npt.NDArray[np.float64]],
    *,
    committed_key: str = "broad_residual_skip",
) -> ResidualGridReport:
    """Score the 2x2 and read the pre-registered gate on the committed cell.

    The 4-book family feeds the DSR deflation + the CSCV/PBO trial count (the
    honest multiple-testing set = the constructions we selected among). Each
    cell's corr_to_trend uses its own universe's trend returns (key prefix before
    the first '_').
    """
    family = {k: v.portfolio_return for k, v in books.items()}
    cells: dict[str, XSReport] = {}
    for key, book in books.items():
        universe = key.split("_")[0]
        cells[key] = evaluate_xs(
            book, cfg, trial_returns=family,
            trend_returns=trend_by_universe[universe],
        )
    c = cells[committed_key]
    passed = bool(
        c.dsr >= 0.95
        and c.pbo <= 0.5
        and c.boot_lo > 0.0
        and c.n_obs >= c.min_trl
        and c.sharpe_annual >= _GATE_SHARPE
    )
    return ResidualGridReport(cells=cells, committed_key=committed_key, passed=passed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/report.py tests/test_xsmom_residual_report.py
git commit -m "feat(xsmom): evaluate_residual_grid + pre-registered gate verdict"
```

---

## Task 11: Exports + audit tool + Makefile target

**Files:**

- Modify: `analytics/xsmom/__init__.py`
- Create: `tools/xsmom_residual_audit.py`
- Modify: `Makefile`
- Test: `tests/test_xsmom_residual_replay.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_xsmom_residual_replay.py`)

```python
def test_residual_audit_build_row_smoke() -> None:
    from tools.xsmom_residual_audit import build_grid, long_only_sharpe

    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    sector_map = {s: ("Tech" if i % 2 == 0 else "Energy") for i, s in enumerate(syms)}
    rep = build_grid(
        conn, mega=syms[:3], broad=syms, sector_map=sector_map, slippage_bps=2.0,
    )
    assert rep.committed_key == "broad_residual_skip"
    assert "broad_residual_skip" in rep.cells
    lo = long_only_sharpe(conn, syms, sector_map, 2.0)
    assert isinstance(lo, float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual_replay.py -k audit_build_row -v`
Expected: FAIL with `ModuleNotFoundError: tools.xsmom_residual_audit`.

- [ ] **Step 3a: Add exports** to `analytics/xsmom/__init__.py`

Add imports:

```python
from analytics.xsmom.replay import (
    replay_residual_grid,
    replay_xs,
    replay_xs_trials,
)
from analytics.xsmom.report import (
    ResidualGridReport,
    XSReport,
    evaluate_residual_grid,
    evaluate_xs,
)
from analytics.xsmom.residual import (
    residual_closes,
    rolling_beta,
    xs_residual_leverage,
)
```

and add `"ResidualGridReport"`, `"evaluate_residual_grid"`,
`"replay_residual_grid"`, `"residual_closes"`, `"rolling_beta"`,
`"xs_residual_leverage"` to `__all__` (keep it sorted).

- [ ] **Step 3b: Write the audit tool** `tools/xsmom_residual_audit.py`

```python
"""Experiment #1 — residualized XS-momentum 2x2 audit (read-only verdict).

Runs the pre-registered {mega,broad} x {raw, residual+skip} grid over the
research universe (1d), prints each cell's headline + DSR/PBO/boot-CI/MinTRL, the
cost-sensitivity sweep (0/2/8 bps), the long-only top-quintile leg, and the
PASS/FAIL on the committed broad x residual+skip cell. No writes.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_residual_audit.py
    PYTHONPATH=. poetry run python tools/xsmom_residual_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast import ForecastConfig, replay_universe
from analytics.forecast.replay import load_daily_inputs
from analytics.store import DEFAULT_DB_PATH
from analytics.xsmom import (
    evaluate_residual_grid,
    replay_residual_grid,
    run_xs_backtest,
)
from analytics.xsmom.replay import _sector_map
from analytics.xsmom.report import ResidualGridReport
from analytics.xsmom.residual import (
    _SLOW_SPEEDS_DEFAULT,
    long_only_residual_leverage,
)
from utils.config_validation import load_research_universe

_BETA_WINDOW = 252
_SNAPSHOT = Path("config/universe_sp100_snapshot.json")


def _mega_symbols(broad: list[str]) -> list[str]:
    """The pre-expansion S&P-100 stocks (the stable mega arm), intersected with
    the active universe so a missing backfill never crashes the audit."""
    snap = set(json.loads(_SNAPSHOT.read_text())) if _SNAPSHOT.exists() else set()
    mega = [s for s in broad if s in snap]
    return mega or broad  # fall back to broad if the snapshot is absent


def build_grid(
    conn: duckdb.DuckDBPyConnection,
    *,
    mega: list[str],
    broad: list[str],
    sector_map: dict[str, str],
    slippage_bps: float,
) -> ResidualGridReport:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    books = replay_residual_grid(
        conn, cfg, beta_window=_BETA_WINDOW, mega_symbols=mega,
        broad_symbols=broad, sector_map=sector_map,
    )
    trend = {
        "mega": replay_universe(conn, cfg, symbols=mega).portfolio_return,
        "broad": replay_universe(conn, cfg, symbols=broad).portfolio_return,
    }
    return evaluate_residual_grid(books, cfg, trend_by_universe=trend)


def _grid_frame(rep: ResidualGridReport) -> pd.DataFrame:
    rows = []
    for key, c in rep.cells.items():
        rows.append({
            "cell": key, "days": c.n_obs, "sharpe": c.sharpe_annual,
            "dsr": c.dsr, "pbo": c.pbo, "boot_lo": c.boot_lo,
            "min_trl": c.min_trl, "corr_to_trend": c.corr_to_trend,
        })
    return pd.DataFrame(rows)


def long_only_sharpe(
    conn: duckdb.DuckDBPyConnection,
    broad: list[str],
    sector_map: dict[str, str],
    slippage_bps: float,
) -> float:
    """Annualized Sharpe of the deployable long-only top-quintile residual book
    on the committed (broad, residual+skip) config."""
    cfg = dataclasses.replace(
        ForecastConfig(),
        slippage_pct=slippage_bps / 10_000.0,
        speeds=_SLOW_SPEEDS_DEFAULT,
    )
    closes, fundings = load_daily_inputs(conn, broad)
    sm = {s: sector_map[s] for s in broad if s in sector_map}
    lev = long_only_residual_leverage(
        closes, cfg, beta_window=_BETA_WINDOW, sector_map=sm, quantile=0.8
    )
    r = run_xs_backtest(closes, fundings, cfg, leverage=lev).portfolio_return
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r)) / sd * math.sqrt(cfg.annualization_days)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    broad = load_research_universe().stocks()
    sector_map = _sector_map()
    mega = _mega_symbols(broad)
    print(f"mega={len(mega)} broad={len(broad)} stocks")

    for bps in (0.0, args.slippage_bps, 8.0):
        rep = build_grid(
            conn, mega=mega, broad=broad, sector_map=sector_map, slippage_bps=bps,
        )
        print(f"\n=== 2x2 grid @ {bps:g} bps ===")
        print(_grid_frame(rep).to_string(index=False,
              float_format=lambda x: f"{x:+.3f}"))
        verdict = "PASS" if rep.passed else "FAIL"
        print(f"committed cell ({rep.committed_key}) @ {bps:g}bps: {verdict}")

    print("\n=== long-only top-quintile (broad, residual+skip) Sharpe ===")
    for bps in (0.0, args.slippage_bps, 8.0):
        print(f"  @ {bps:g}bps: {long_only_sharpe(conn, broad, sector_map, bps):+.3f}")

    print(
        "\nGate (pre-registered, broad x residual+skip): DSR>=0.95 ∧ PBO<=0.5 ∧ "
        "boot_lo>0 ∧ n>=MinTRL ∧ Sharpe>=0.7. Broad-arm numbers carry the "
        "survivorship flag — a marginal pass is suspect; a clean fail is "
        "trustworthy. Short-borrow cost omitted (mildly optimistic short legs)."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3c: Add the Makefile target**

Append after the `wifey-xsmom-audit` recipe (recipe lines use a real TAB, not
spaces):

```text
wifey-xsmom-residual-audit:
    @echo "🔬 Experiment #1 — residualized XS-momentum 2x2 audit..."
    @PYTHONPATH=. poetry run python tools/xsmom_residual_audit.py $(ARGS)
```

(The two indented recipe lines above must each begin with a real TAB, not the
spaces shown here — Make requires a TAB; the spaces are only to satisfy the
markdown linter.)

and add `wifey-xsmom-residual-audit` to the `.PHONY` line (line 14).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_xsmom_residual_replay.py -v`
Expected: PASS (incl. the smoke test).

- [ ] **Step 5: Commit**

```bash
git add analytics/xsmom/__init__.py tools/xsmom_residual_audit.py Makefile tests/test_xsmom_residual_replay.py
git commit -m "feat(xsmom): residual XS-mom audit tool + make target + exports"
```

---

## Task 12: Full gate, goldens-unmoved, run the audit, write the verdict

**Files:**

- Create: `docs/audits/2026-06-21-experiment-1-residual-xsmom.md`
- Modify: `CLAUDE.md`, `README.md`,
  `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/MEMORY.md`

- [ ] **Step 1: Lint + typecheck + full suite**

Run:

```bash
make lint-py && make typecheck && make test
```

Expected: ruff clean, mypy strict clean, suite green (the prior pass count plus
all the new tests).

- [ ] **Step 2: Confirm regression goldens are byte-identical**

Run:

```bash
make test-regression
```

Expected: both configs PASS, goldens UNMOVED (this is additive/default-off; if a
golden moved, a default path was changed — find and revert it).

- [ ] **Step 3: Run the audit to produce the verdict numbers**

Run:

```bash
make wifey-xsmom-residual-audit
```

Expected: the 2×2 grid at 0/2/8 bps + the PASS/FAIL on `broad_residual_skip`.
Capture the table.

- [ ] **Step 4: Write the verdict note**

Create `docs/audits/2026-06-21-experiment-1-residual-xsmom.md` with: the 2×2
table (mega/broad × raw/residual+skip) at 0/2/8 bps, the committed-cell gate
result, the breadth-vs-construction read (which lever moved the number), the
long-only-leg Sharpe, the survivorship caveat, and the roadmap consequence
(pass → core sleeve; fail → pivot to #2 low-vol per the spec). Hand-format to the
full markdownlint ruleset.

- [ ] **Step 5: Sync docs + memory, then commit**

Update `CLAUDE.md` (`analytics/xsmom/` gains `residual.py` + the grid replay/
report; new `tools/xsmom_residual_audit.py` + `make wifey-xsmom-residual-audit`;
`config/universe.json` now ~500 names + the snapshot file), `README.md`
(make-target line), and `MEMORY.md` Current State (the experiment-#1 verdict +
whether the binding constraint is resolved). Then:

```bash
git add docs/audits/2026-06-21-experiment-1-residual-xsmom.md CLAUDE.md README.md
git commit -m "docs(xsmom): experiment #1 residual XS-mom verdict + doc sync"
```

(`MEMORY.md` lives outside the repo — update it but it is not part of this
commit.)

---

## Self-Review

**Spec coverage** (each spec requirement → task):

- Residual returns (beta-strip) → Tasks 3–5. ✓
- Skip-month (slow speeds only) → `_SLOW_SPEEDS_DEFAULT`, Task 8/9. ✓
- Sector-neutral demean → Task 6, wired in Task 8. ✓
- Additive/default-off, goldens unmoved → Task 7 (byte-identical test) + Task 12
  step 2. ✓
- Broaden to current S&P 500 + balanced panel → Tasks 1–2 (the `listed`/
  `with_min_history` seam already exists; not re-implemented). ✓
- 2×2 `{mega,broad}×{raw,residual+skip}` → Task 9. ✓
- Pre-registered gate on the committed cell; 4-book family feeds PBO → Task 10. ✓
- Both books (L/S + long-only top-quintile) at 0/2/8 bps → L/S grid is Tasks
  9–11; the long-only top-quintile leg is `long_only_residual_leverage` (Task 8
  steps 6–10) reported by the audit's `long_only_sharpe` at 0/2/8 bps (Task 11).
  ✓
- Causality guards (non-vacuous perturbation tests) → Tasks 3, 5, 8 (incl. the
  long-only leg). ✓
- Reuse research_guards + cost model → via `evaluate_xs` (Task 10) + `slippage_pct`
  sweep (Task 11). ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `run_xs_backtest(..., *, leverage=None)` used consistently
in Tasks 7/9/11; `XSBookResult` fields match book.py; `evaluate_residual_grid`
signature matches its caller in Task 11; `_SLOW_SPEEDS_DEFAULT` defined in Task 9
step 3 (residual.py) and imported by replay (Task 9) and the audit (Task 11);
`long_only_residual_leverage` (Task 8) imported by the audit (Task 11). ✓
