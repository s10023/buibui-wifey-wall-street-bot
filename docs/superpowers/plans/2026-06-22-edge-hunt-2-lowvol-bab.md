# Edge-Hunt #2 — Low-Beta / BAB Long Sleeve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a beta-neutral betting-against-beta (BAB) long-short equity sleeve over the 508-name S&P 500 universe, scored on a pre-registered net-of-cost gate, with a deployable long-only translation — as an additive, read-only package so the regression goldens stay byte-identical.

**Architecture:** A new self-contained package `analytics/lowvol/` mirroring the `xsmom/` split (`signals.py` pure, `replay.py` DB-only, `report.py` gate, `__init__.py` re-exports) plus a read-only `tools/lowvol_audit.py`. It reuses `rolling_beta` (`xsmom/residual.py`), `ew_return_vol` (`forecast/vol.py`), `equal_weight_market_return` (`xsmom/diagnostics.py`), `load_daily_inputs` (`forecast/replay.py`), the `run_xs_backtest(leverage=…)` cost-aware injection, and `research_guards` via the `evaluate_residual_grid` gate pattern. Nothing existing imports it ⇒ goldens trivially unmoved.

**Tech Stack:** Python 3.11, numpy, pandas, duckdb (in-memory for tests), pytest, ruff, mypy strict. Spec: `docs/superpowers/specs/2026-06-22-edge-hunt-2-lowvol-bab-design.md`.

---

## Setup (before Task 1)

The spec + this plan live on the docs branch `docs/edge-hunt-2-lowvol-bab-spec`. **Execution happens on a fresh feature branch off `main`:**

```bash
git checkout main && git pull
git checkout -b feat/lowvol-bab-sleeve
git config --local user.email   # must print ngkhaijian@gmail.com (per-repo identity)
```

All commit steps below assume this branch. Use the conventional-commit prefixes and the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

## File structure

- Create `analytics/lowvol/__init__.py` — eager re-exports (Task 5).
- Create `analytics/lowvol/signals.py` — pure causal signal + leverage builders (Tasks 1–4).
- Create `analytics/lowvol/replay.py` — the only DB-touching module; runs the 2×2 (Task 6).
- Create `analytics/lowvol/report.py` — `BabGridReport` + `evaluate_bab_grid` gate (Task 7).
- Create `tools/lowvol_audit.py` — read-only verdict CLI (Task 8).
- Modify `Makefile` — add `wifey-lowvol-audit` target + `.PHONY` entry (Task 8).
- Create `tests/test_lowvol_signals.py` (Tasks 1–4), `tests/test_lowvol_replay.py` (Task 6), `tests/test_lowvol_report.py` (Task 7).

The 2×2 grid keys are fixed: `beta_neutral_ls` (**gated cell**), `beta_long_only`, `vol_neutral_ls`, `vol_long_only`.

---

### Task 1: Causal ranking metrics + cross-sectional score

**Files:**

- Create: `analytics/lowvol/signals.py`
- Test: `tests/test_lowvol_signals.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lowvol_signals.py
import numpy as np
import pandas as pd

from analytics.lowvol.signals import (
    causal_betas,
    cross_sectional_score,
    realized_vols,
)


def _toy_closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2020-01-01", periods=400, freq="D")
    rng = np.random.default_rng(3)
    out = {}
    for s in ("A", "B", "C", "D"):
        out[s] = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.01, 400)), index=idx)
    return out


def test_causal_betas_shape_and_causality() -> None:
    closes = _toy_closes()
    b = causal_betas(closes, window=60)
    assert set(b.columns) == set(closes)
    assert np.isnan(b["A"].to_numpy()[:60]).all()  # warm-up + shift NaN

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[200] *= 1.5  # bump a FUTURE bar (middle, live region)
    b2 = causal_betas(closes2, window=60)
    # B's beta vs the EW market: a bump at 200 moves the market return at 200,
    # which only affects rolling windows that include bar 200 (-> shifted 201..260).
    assert b["B"].to_numpy()[150] == b2["B"].to_numpy()[150]  # past unchanged
    assert b["B"].to_numpy()[230] != b2["B"].to_numpy()[230]  # within reach (non-vacuous)


def test_realized_vols_is_causal_and_positive() -> None:
    closes = _toy_closes()
    v = realized_vols(closes, window=60)
    assert set(v.columns) == set(closes)
    finite = v.to_numpy()[np.isfinite(v.to_numpy())]
    assert (finite >= 0.0).all()

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[200] *= 1.5  # future bar
    v2 = realized_vols(closes2, window=60)
    assert v["A"].to_numpy()[150] == v2["A"].to_numpy()[150]  # past unchanged
    assert v["A"].to_numpy()[230] != v2["A"].to_numpy()[230]  # within reach (non-vacuous)


def test_cross_sectional_score_low_metric_is_long() -> None:
    idx = pd.date_range("2020-01-01", periods=1, freq="D")
    metric = pd.DataFrame({"A": [0.5], "B": [1.0], "C": [1.5]}, index=idx)
    score = cross_sectional_score(metric)
    # low metric (A) -> highest (long) score; high metric (C) -> lowest (short)
    assert score["A"].iloc[0] > 0.0
    assert score["C"].iloc[0] < 0.0
    assert abs(float(score.iloc[0].mean())) < 1e-12  # demeaned -> row sums to ~0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_lowvol_signals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.lowvol'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# analytics/lowvol/signals.py
"""Causal low-beta / BAB signal + leverage primitives (edge-hunt #2).

Pure: numpy + pandas + the forecast vol primitive + the causal `rolling_beta` and
`equal_weight_market_return` already shipped for the residual XS sleeve. No DB, no
engine. Every transform is causal — trailing rolling windows are `.shift(1)`-ed so
the position held during day `d` is sized from information through day `d-1`.
Spec: docs/superpowers/specs/2026-06-22-edge-hunt-2-lowvol-bab-design.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.vol import ew_return_vol
from analytics.xsmom.diagnostics import equal_weight_market_return
from analytics.xsmom.residual import rolling_beta

_BETA_WINDOW = 252  # a-priori trailing sessions for the market beta (matches residual)
_VOL_WINDOW = 252  # a-priori trailing sessions for the realized-vol ranking


def _union(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex([])
    for s in closes.values():
        idx = idx.union(pd.DatetimeIndex(s.index))
    return idx.sort_values()


def causal_betas(
    closes: dict[str, pd.Series], *, window: int = _BETA_WINDOW
) -> pd.DataFrame:
    """Per-name trailing market beta vs the equal-weight market, `.shift(1)`-ed.

    Beta at day `d` is computed from returns through `d-1` (causal). Columns =
    symbols, index = sorted union daily index. Warm-up bars are NaN.
    """
    mkt = equal_weight_market_return(closes)
    union = _union(closes)
    cols = {
        sym: rolling_beta(close.pct_change(), mkt, window).shift(1).reindex(union)
        for sym, close in closes.items()
    }
    return pd.DataFrame(cols, index=union)


def realized_vols(
    closes: dict[str, pd.Series], *, window: int = _VOL_WINDOW
) -> pd.DataFrame:
    """Per-name trailing realized return vol (rolling std), `.shift(1)`-ed.

    Vol at day `d` uses returns through `d-1` (causal). Parallels `causal_betas`
    so the two grid rows use the same as-of-`d-1` convention.
    """
    union = _union(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        v = close.pct_change().rolling(window, min_periods=window).std().shift(1)
        cols[sym] = v.reindex(union)
    return pd.DataFrame(cols, index=union)


def cross_sectional_score(metric: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-scored *negative* demean: low metric -> high (long) score.

    Per row: `-(x - row_mean) / row_std`. Dimensionless so the beta-rank and
    vol-rank rows size into the same vol-target band (Sharpe is scale-invariant
    regardless). Rows with <2 finite values or zero dispersion -> all-NaN; inf -> NaN.
    """
    demeaned = metric.sub(metric.mean(axis=1), axis=0)
    z = demeaned.div(metric.std(axis=1), axis=0)
    return (-z).replace([np.inf, -np.inf], np.nan)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_lowvol_signals.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add analytics/lowvol/signals.py tests/test_lowvol_signals.py
git commit -m "feat(lowvol): causal beta/vol ranking metrics + cross-sectional score

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Beta-neutralization helper

**Files:**

- Modify: `analytics/lowvol/signals.py` (append `_beta_neutralize`)
- Test: `tests/test_lowvol_signals.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lowvol_signals.py  (append)
from analytics.lowvol.signals import _beta_neutralize


def test_beta_neutralize_zeroes_net_portfolio_beta() -> None:
    idx = pd.date_range("2020-01-01", periods=1, freq="D")
    lev = pd.DataFrame(
        {"A": [1.0], "B": [1.0], "C": [-1.0], "D": [-1.0]}, index=idx
    )  # 2 long, 2 short
    betas = pd.DataFrame(
        {"A": [0.5], "B": [0.7], "C": [1.3], "D": [1.5]}, index=idx
    )
    out = _beta_neutralize(lev, betas)
    net = (out * betas).sum(axis=1)
    assert abs(float(net.iloc[0])) < 1e-12  # beta-neutral by construction
    # long leg untouched, short leg scaled by k = -beta_long/beta_short = 1.2/2.8
    assert out["A"].iloc[0] == 1.0
    assert abs(out["C"].iloc[0] - (-1.0 * 1.2 / 2.8)) < 1e-12


def test_beta_neutralize_leaves_degenerate_short_leg_untouched() -> None:
    idx = pd.date_range("2020-01-01", periods=1, freq="D")
    lev = pd.DataFrame({"A": [1.0], "B": [1.0]}, index=idx)  # no short leg
    betas = pd.DataFrame({"A": [0.5], "B": [0.7]}, index=idx)
    out = _beta_neutralize(lev, betas)
    assert out["A"].iloc[0] == 1.0 and out["B"].iloc[0] == 1.0  # unchanged (k -> 1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_lowvol_signals.py -k beta_neutralize -v`
Expected: FAIL — `ImportError: cannot import name '_beta_neutralize'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `analytics/lowvol/signals.py`:

```python
def _beta_neutralize(lev: pd.DataFrame, betas: pd.DataFrame) -> pd.DataFrame:
    """Scale the short leg per day so the net causal portfolio beta is zero.

    `Σ_i w_i β_i = 0` after scaling the short leg by `k = -β_long / β_short`
    (β_long = Σ over long positions, β_short = Σ over short positions). A day with
    no valid short leg, k ≤ 0, or k NaN is left untouched (residual beta accepted;
    the realized-beta diagnostic confirms it nets to ≈0 across the full sample).
    """
    contrib = lev * betas
    long_mask = lev > 0
    short_mask = lev < 0
    beta_long = contrib.where(long_mask).sum(axis=1, min_count=1)
    beta_short = contrib.where(short_mask).sum(axis=1, min_count=1)
    k = (-beta_long / beta_short).replace([np.inf, -np.inf], np.nan)
    k = k.where(k.notna() & (k > 0.0), 1.0)
    scaled = lev.mul(k, axis=0)
    return lev.where(~short_mask, scaled)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_lowvol_signals.py -k beta_neutralize -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add analytics/lowvol/signals.py tests/test_lowvol_signals.py
git commit -m "feat(lowvol): per-day beta-neutralization helper (Σwβ=0)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Beta-neutral long-short leverage builder

**Files:**

- Modify: `analytics/lowvol/signals.py` (append `beta_neutral_leverage`)
- Test: `tests/test_lowvol_signals.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lowvol_signals.py  (append)
from analytics.forecast.config import ForecastConfig
from analytics.lowvol.signals import beta_neutral_leverage


def test_beta_neutral_leverage_shape_neutrality_and_causality() -> None:
    closes = _toy_closes()
    cfg = ForecastConfig()
    betas = causal_betas(closes, window=60)
    score = cross_sectional_score(betas)
    lev = beta_neutral_leverage(score, betas, closes, cfg)
    assert set(lev.columns) == set(closes)

    # net causal portfolio beta ≈ 0 on (essentially) every live day; the median is
    # robust to the rare degenerate day the k>0 guard leaves un-neutralized. The
    # exact math is proven deterministically in test_beta_neutralize_*.
    contrib = (lev * betas).sum(axis=1, min_count=1)
    live = contrib.dropna()
    assert len(live) > 0
    assert float(live.abs().median()) < 1e-6

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[-1] *= 1.5  # bump the future-most bar
    betas2 = causal_betas(closes2, window=60)
    lev2 = beta_neutral_leverage(cross_sectional_score(betas2), betas2, closes2, cfg)
    np.testing.assert_array_equal(lev["B"].to_numpy()[:-1], lev2["B"].to_numpy()[:-1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_lowvol_signals.py -k beta_neutral_leverage -v`
Expected: FAIL — `ImportError: cannot import name 'beta_neutral_leverage'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `analytics/lowvol/signals.py`:

```python
def beta_neutral_leverage(
    score: pd.DataFrame,
    betas: pd.DataFrame,
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> pd.DataFrame:
    """Vol-parity long-short leverage from a cross-sectional `score`, beta-neutralized.

    `lev_i = score_i * (vol_target / vol_ann_i)` (vol-parity, the `xs_*_leverage`
    machinery), then the short leg is scaled so the net causal portfolio beta is
    zero (`_beta_neutralize`). `score` is already `.shift(1)`-ed via `causal_betas`
    / `realized_vols`, so no further shift here. `betas` (always the causal market
    betas) drives the neutralization regardless of which metric `score` ranks on.
    """
    union = pd.DatetimeIndex(score.index)
    ann = np.sqrt(cfg.annualization_days)
    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        lev = score[sym] * (cfg.vol_target_annual / vol_ann)
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    lev_df = pd.DataFrame(lev_cols, index=union)
    return _beta_neutralize(lev_df, betas.reindex(union))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_lowvol_signals.py -k beta_neutral_leverage -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add analytics/lowvol/signals.py tests/test_lowvol_signals.py
git commit -m "feat(lowvol): beta-neutral vol-parity long-short leverage builder

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Long-only deployable leverage builder

**Files:**

- Modify: `analytics/lowvol/signals.py` (append `long_only_leverage`)
- Test: `tests/test_lowvol_signals.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lowvol_signals.py  (append)
from analytics.lowvol.signals import long_only_leverage


def test_long_only_leverage_is_nonnegative_and_causal() -> None:
    closes = _toy_closes()
    cfg = ForecastConfig()
    score = cross_sectional_score(causal_betas(closes, window=60))
    lev = long_only_leverage(score, closes, cfg, quantile=0.5)
    stacked = lev.to_numpy()
    finite = stacked[np.isfinite(stacked)]
    assert (finite >= 0.0).all()  # long-only: no negative legs
    assert finite.any()  # at least some non-zero longs emerge

    closes2 = {k: v.copy() for k, v in closes.items()}
    closes2["A"].iloc[-1] *= 1.5  # future-most bar
    score2 = cross_sectional_score(causal_betas(closes2, window=60))
    lev2 = long_only_leverage(score2, closes2, cfg, quantile=0.5)
    np.testing.assert_array_equal(lev["B"].to_numpy()[:-1], lev2["B"].to_numpy()[:-1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_lowvol_signals.py -k long_only_leverage -v`
Expected: FAIL — `ImportError: cannot import name 'long_only_leverage'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `analytics/lowvol/signals.py`:

```python
def long_only_leverage(
    score: pd.DataFrame,
    closes: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    quantile: float = 0.8,
) -> pd.DataFrame:
    """Long-only bottom-quantile leverage (deployable wife-sleeve form).

    Keep names whose cross-sectional `score` is in the top `quantile` AND positive
    (the lowest-beta / lowest-vol side) each day; each kept name gets a unit
    vol-targeted long, everything else is 0 (no shorts, no re-center). Mirrors
    `long_only_residual_leverage`. Causal — `score` is already `.shift(1)`-ed.
    """
    union = pd.DatetimeIndex(score.index)
    ann = np.sqrt(cfg.annualization_days)
    thresh = score.quantile(quantile, axis=1)
    longs = score.ge(thresh, axis=0) & score.gt(0.0)
    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        unit = (cfg.vol_target_annual / vol_ann).replace([np.inf, -np.inf], np.nan)
        lev_cols[sym] = unit.where(longs[sym], 0.0)
    return pd.DataFrame(lev_cols, index=union)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_lowvol_signals.py -v`
Expected: all signals tests pass (8 total).

- [ ] **Step 5: Commit**

```bash
git add analytics/lowvol/signals.py tests/test_lowvol_signals.py
git commit -m "feat(lowvol): long-only bottom-quantile deployable leverage builder

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Package `__init__` re-exports

**Files:**

- Create: `analytics/lowvol/__init__.py`
- Test: `tests/test_lowvol_signals.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lowvol_signals.py  (append)
def test_package_reexports() -> None:
    import analytics.lowvol as lv

    for name in (
        "causal_betas",
        "realized_vols",
        "cross_sectional_score",
        "beta_neutral_leverage",
        "long_only_leverage",
    ):
        assert hasattr(lv, name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_lowvol_signals.py -k reexports -v`
Expected: FAIL — `AttributeError` (the names are not yet re-exported at package level).

- [ ] **Step 3: Write the minimal implementation**

```python
# analytics/lowvol/__init__.py
"""Low-beta / BAB long sleeve (edge-hunt #2) — beta-neutral long-short book."""

from analytics.lowvol.signals import (
    beta_neutral_leverage,
    causal_betas,
    cross_sectional_score,
    long_only_leverage,
    realized_vols,
)

__all__ = [
    "beta_neutral_leverage",
    "causal_betas",
    "cross_sectional_score",
    "long_only_leverage",
    "realized_vols",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_lowvol_signals.py -k reexports -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add analytics/lowvol/__init__.py tests/test_lowvol_signals.py
git commit -m "feat(lowvol): package __init__ re-exports

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Replay the 2×2 grid (DB front door)

**Files:**

- Create: `analytics/lowvol/replay.py`
- Test: `tests/test_lowvol_replay.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lowvol_replay.py
import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.lowvol.replay import bab_market_return, replay_bab_grid
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema

_DAY = 86_400_000
_T0 = 1_514_764_800_000  # 2018-01-01T00:00:00Z in ms


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, 500))
    df = pd.DataFrame(
        {
            "symbol": sym,
            "timeframe": "1d",
            "open_time": [_T0 + i * _DAY for i in range(500)],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        }
    )
    upsert_ohlcv(conn, df)


def test_replay_bab_grid_returns_four_books() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    cfg = ForecastConfig()
    books = replay_bab_grid(conn, cfg, syms, beta_window=60, vol_window=60)
    assert set(books) == {
        "beta_neutral_ls",
        "beta_long_only",
        "vol_neutral_ls",
        "vol_long_only",
    }
    assert books["beta_neutral_ls"].portfolio_return.shape[0] > 0


def test_bab_market_return_is_a_series() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    mkt = bab_market_return(conn, syms)
    assert isinstance(mkt, pd.Series)
    assert len(mkt) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_lowvol_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.lowvol.replay'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# analytics/lowvol/replay.py
"""Read-only DuckDB front door for the low-beta / BAB sleeve (edge-hunt #2).

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + all-zero equity
funding) and runs the BAB 2x2 over the breadth universe's active single-name
stocks. The only module in ``analytics/lowvol/`` that touches the DB; never writes.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.lowvol.signals import (
    beta_neutral_leverage,
    causal_betas,
    cross_sectional_score,
    long_only_leverage,
    realized_vols,
)
from analytics.xsmom.book import XSBookResult, run_xs_backtest
from analytics.xsmom.diagnostics import equal_weight_market_return
from utils.config_validation import load_research_universe


def _universe_symbols(min_history_days: int | None) -> list[str]:
    uni = load_research_universe(min_history_days=min_history_days)
    return uni.stocks()  # active single-name stocks; ETFs excluded from the XS set


def bab_market_return(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.Series:
    """Equal-weight market daily return over the symbol set (read-only)."""
    closes, _ = load_daily_inputs(conn, symbols)
    return equal_weight_market_return(closes)


def replay_bab_grid(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    beta_window: int,
    vol_window: int,
    min_history_days: int | None = None,
) -> dict[str, XSBookResult]:
    """Run the pre-registered `{beta,vol} x {beta-neutral L/S, long-only}` 2x2.

    All four books are sized on actual closes and booked through the shared
    cost-aware ``run_xs_backtest``. The gated cell is ``beta_neutral_ls``; the
    other three are diagnostic + feed the DSR/PBO trial count.
    """
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)

    betas = causal_betas(closes, window=beta_window)
    vols = realized_vols(closes, window=vol_window)
    beta_score = cross_sectional_score(betas)
    vol_score = cross_sectional_score(vols)

    books: dict[str, XSBookResult] = {}
    books["beta_neutral_ls"] = run_xs_backtest(
        closes, fundings, cfg, leverage=beta_neutral_leverage(beta_score, betas, closes, cfg)
    )
    books["beta_long_only"] = run_xs_backtest(
        closes, fundings, cfg, leverage=long_only_leverage(beta_score, closes, cfg)
    )
    books["vol_neutral_ls"] = run_xs_backtest(
        closes, fundings, cfg, leverage=beta_neutral_leverage(vol_score, betas, closes, cfg)
    )
    books["vol_long_only"] = run_xs_backtest(
        closes, fundings, cfg, leverage=long_only_leverage(vol_score, closes, cfg)
    )
    return books
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_lowvol_replay.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add analytics/lowvol/replay.py tests/test_lowvol_replay.py
git commit -m "feat(lowvol): read-only DuckDB replay of the BAB 2x2 grid

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Gate report (`evaluate_bab_grid`)

**Files:**

- Create: `analytics/lowvol/report.py`
- Modify: `analytics/lowvol/__init__.py` (add report + replay re-exports)
- Test: `tests/test_lowvol_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lowvol_report.py
import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.lowvol.replay import bab_market_return, replay_bab_grid
from analytics.lowvol.report import BabGridReport, evaluate_bab_grid
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema

_DAY = 86_400_000
_T0 = 1_514_764_800_000


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, 500))
    df = pd.DataFrame(
        {
            "symbol": sym,
            "timeframe": "1d",
            "open_time": [_T0 + i * _DAY for i in range(500)],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        }
    )
    upsert_ohlcv(conn, df)


def test_evaluate_bab_grid_structure_and_gate() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    cfg = ForecastConfig()
    books = replay_bab_grid(conn, cfg, syms, beta_window=60, vol_window=60)
    mkt = bab_market_return(conn, syms)
    rep = evaluate_bab_grid(books, cfg, mkt)

    assert isinstance(rep, BabGridReport)
    assert set(rep.cells) == {
        "beta_neutral_ls",
        "beta_long_only",
        "vol_neutral_ls",
        "vol_long_only",
    }
    assert rep.committed_key == "beta_neutral_ls"
    assert set(rep.attribution) == set(rep.cells)
    assert isinstance(rep.passed, bool)
    assert isinstance(rep.deploy_grade, bool)
    # the realized portfolio beta of every cell is a finite number
    assert np.isfinite(rep.attribution["beta_neutral_ls"].beta)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_lowvol_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.lowvol.report'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# analytics/lowvol/report.py
"""Assemble the BAB 2x2 verdict: per-cell guards + realized-beta diagnostic + gate.

Reuses ``evaluate_xs`` (DSR/PBO/boot-CI/MinTRL) and ``beta_attribution`` from the
xsmom sleeve. The gate is read on the pre-committed ``beta_neutral_ls`` cell only;
the full 4-book family feeds the DSR deflation + CSCV/PBO trial count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.diagnostics import BetaAttribution, beta_attribution
from analytics.xsmom.report import XSReport, evaluate_xs

_GATE_SHARPE = 0.7  # pre-registered net-of-cost equity beta-neutral bar
_DEPLOY_SHARPE = 1.0  # deploy-grade tier annotation (NOT the pass/fail line)


@dataclass(frozen=True)
class BabGridReport:
    """The 2x2 grid's per-cell XSReports + realized-beta attribution + verdict."""

    cells: dict[str, XSReport]
    attribution: dict[str, BetaAttribution]
    committed_key: str
    passed: bool
    deploy_grade: bool


def evaluate_bab_grid(
    books: dict[str, XSBookResult],
    cfg: ForecastConfig,
    market_ret: pd.Series,
    *,
    committed_key: str = "beta_neutral_ls",
    long_only_key: str = "beta_long_only",
) -> BabGridReport:
    """Score the 2x2 and read the pre-registered gate on the committed cell.

    ``market_ret`` is the equal-weight market daily return (same universe/window)
    used for the realized-portfolio-beta diagnostic — for the beta-neutral cell it
    must come out ≈0, else the neutralization is broken. ``corr_to_trend`` /
    ``trend_sharpe`` in the per-cell XSReport are unused for BAB (no trend sleeve
    comparison), so an empty trend array is passed.
    """
    family = {k: v.portfolio_return for k, v in books.items()}
    empty_trend = np.asarray([], dtype=np.float64)
    cells: dict[str, XSReport] = {}
    attribution: dict[str, BetaAttribution] = {}
    for key, book in books.items():
        cells[key] = evaluate_xs(
            book, cfg, trial_returns=family, trend_returns=empty_trend
        )
        r = book.portfolio_return
        m: npt.NDArray[np.float64] = (
            market_ret.reindex(book.daily_index).to_numpy(dtype=np.float64)
        )
        live = r != 0.0  # restrict the diagnostic to the live trading window
        attribution[key] = beta_attribution(r[live], m[live], cfg.annualization_days)

    c = cells[committed_key]
    passed = bool(
        c.dsr >= 0.95
        and c.pbo <= 0.5
        and c.boot_lo > 0.0
        and c.n_obs >= c.min_trl
        and c.sharpe_annual >= _GATE_SHARPE
    )
    lo = cells[long_only_key]
    deploy_grade = bool(
        passed
        and c.sharpe_annual >= _DEPLOY_SHARPE
        and lo.sharpe_annual >= _GATE_SHARPE
    )
    return BabGridReport(
        cells=cells,
        attribution=attribution,
        committed_key=committed_key,
        passed=passed,
        deploy_grade=deploy_grade,
    )
```

- [ ] **Step 4: Extend the package `__init__`**

Replace `analytics/lowvol/__init__.py` with:

```python
# analytics/lowvol/__init__.py
"""Low-beta / BAB long sleeve (edge-hunt #2) — beta-neutral long-short book."""

from analytics.lowvol.replay import bab_market_return, replay_bab_grid
from analytics.lowvol.report import BabGridReport, evaluate_bab_grid
from analytics.lowvol.signals import (
    beta_neutral_leverage,
    causal_betas,
    cross_sectional_score,
    long_only_leverage,
    realized_vols,
)

__all__ = [
    "BabGridReport",
    "bab_market_return",
    "beta_neutral_leverage",
    "causal_betas",
    "cross_sectional_score",
    "evaluate_bab_grid",
    "long_only_leverage",
    "realized_vols",
    "replay_bab_grid",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_lowvol_report.py tests/test_lowvol_signals.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add analytics/lowvol/report.py analytics/lowvol/__init__.py tests/test_lowvol_report.py
git commit -m "feat(lowvol): BAB 2x2 gate report + realized-beta diagnostic

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Read-only audit CLI + Makefile target

**Files:**

- Create: `tools/lowvol_audit.py`
- Modify: `Makefile` (`.PHONY` line + new target)
- Test: `tests/test_lowvol_replay.py` (append smoke test)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lowvol_replay.py  (append)
def test_lowvol_audit_build_grid_smoke() -> None:
    from tools.lowvol_audit import build_grid

    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["A", "B", "C", "D", "E", "F"]
    for i, s in enumerate(syms):
        _seed(conn, s, seed=i)
    rep = build_grid(conn, symbols=syms, slippage_bps=2.0, beta_window=60, vol_window=60)
    assert rep.committed_key == "beta_neutral_ls"
    assert "beta_neutral_ls" in rep.cells
    assert "beta_neutral_ls" in rep.attribution
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_lowvol_replay.py -k audit_build_grid -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.lowvol_audit'`.

- [ ] **Step 3: Write the audit CLI**

```python
# tools/lowvol_audit.py
"""Edge-hunt #2 — low-beta / BAB 2x2 audit (read-only verdict).

Runs the pre-registered {beta,vol} x {beta-neutral L/S, long-only} grid over the
research universe (1d), prints each cell's headline + DSR/PBO/boot-CI/MinTRL +
realized portfolio beta, the cost-sensitivity sweep (0/2/8 bps), and the PASS/FAIL
+ deploy-grade flag on the committed `beta_neutral_ls` cell. No writes.

Usage::

    PYTHONPATH=. poetry run python tools/lowvol_audit.py
    PYTHONPATH=. poetry run python tools/lowvol_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.lowvol.replay import bab_market_return, replay_bab_grid
from analytics.lowvol.report import BabGridReport, evaluate_bab_grid
from analytics.store import DEFAULT_DB_PATH
from utils.config_validation import load_research_universe

_BETA_WINDOW = 252
_VOL_WINDOW = 252


def build_grid(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    slippage_bps: float,
    beta_window: int = _BETA_WINDOW,
    vol_window: int = _VOL_WINDOW,
) -> BabGridReport:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    books = replay_bab_grid(
        conn, cfg, symbols, beta_window=beta_window, vol_window=vol_window
    )
    mkt = bab_market_return(conn, symbols)
    return evaluate_bab_grid(books, cfg, mkt)


def _grid_frame(rep: BabGridReport) -> pd.DataFrame:
    rows = []
    for key, c in rep.cells.items():
        a = rep.attribution[key]
        rows.append(
            {
                "cell": key,
                "days": c.n_obs,
                "sharpe": c.sharpe_annual,
                "dsr": c.dsr,
                "pbo": c.pbo,
                "boot_lo": c.boot_lo,
                "min_trl": c.min_trl,
                "realized_beta": a.beta,
                "alpha_t": a.alpha_tstat,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    syms = load_research_universe().stocks()
    print(f"universe={len(syms)} stocks")

    for bps in (0.0, args.slippage_bps, 8.0):
        rep = build_grid(conn, symbols=syms, slippage_bps=bps)
        print(f"\n=== BAB 2x2 grid @ {bps:g} bps ===")
        print(
            _grid_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.3f}")
        )
        verdict = "PASS" if rep.passed else "FAIL"
        deploy = " (DEPLOY-GRADE)" if rep.deploy_grade else ""
        print(f"committed cell ({rep.committed_key}) @ {bps:g}bps: {verdict}{deploy}")

    print(
        "\nGate (pre-registered, beta x beta-neutral L/S): DSR>=0.95 ∧ PBO<=0.5 ∧ "
        "boot_lo>0 ∧ n>=MinTRL ∧ Sharpe>=0.7; deploy-grade tier = Sharpe>=1.0 ∧ "
        "long-only leg>=0.7. The beta-neutral construct defuses (does not fully "
        "eliminate) the survivorship bias of the current-S&P-500 set; realized_beta "
        "≈0 confirms the neutralization. Short-borrow cost omitted (mildly optimistic "
        "short legs)."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add the Makefile target**

In `Makefile`, append `wifey-lowvol-audit` to the `.PHONY` list (line 14, after `wifey-xsmom-residual-audit`), then add the target after the `wifey-xsmom-residual-audit` block (around line 136):

```text
wifey-lowvol-audit:
    @echo "🔬 Edge-hunt #2 — low-beta/BAB 2x2 audit over the breadth universe (1d)..."
    @PYTHONPATH=. poetry run python tools/lowvol_audit.py $(ARGS)
```

(The two indented recipe lines above must each begin with a real TAB, not the
spaces shown here — Make requires a TAB; the spaces are only to satisfy the
markdown linter.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_lowvol_replay.py -v`
Expected: all pass (3 tests).

- [ ] **Step 6: Commit**

```bash
git add tools/lowvol_audit.py Makefile tests/test_lowvol_replay.py
git commit -m "feat(lowvol): read-only BAB audit CLI + make wifey-lowvol-audit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Full gate — lint, types, suite, goldens byte-identical

**Files:** none new (verification + fixups only).

- [ ] **Step 1: Format + lint**

Run: `make lint-py`
Expected: ruff format reports the new files already formatted (or auto-formats them), lint clean. If ruff reformats, re-run and `git add -A && git commit -m "style: ruff format lowvol package"`.

- [ ] **Step 2: Type-check (mypy strict)**

Run: `make typecheck`
Expected: `Success: no issues found`. If mypy flags an untyped helper, add the missing annotation and re-run. (All new public functions already carry full annotations.)

- [ ] **Step 3: Full test suite**

Run: `make test`
Expected: every test passes, including the new `tests/test_lowvol_*.py` (≈15 new tests), no regressions.

- [ ] **Step 4: Regression goldens byte-identical**

Run: `make test-regression`
Expected: both configs PASS, goldens unmoved. The `analytics/lowvol/` package is additive and imported by nothing on the detector / backtest / signal path, so the golden pipeline cannot see it. If a golden moves, STOP — something is wrong (an unintended import); do not regenerate.

- [ ] **Step 5: Commit any fixups**

```bash
git add -A
git commit -m "test(lowvol): green suite + goldens byte-identical

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Run the audit + write the verdict

**Files:**

- Create: `docs/audits/2026-06-22-edge-hunt-2-lowvol-bab.md`

- [ ] **Step 1: Run the audit against the real DB**

Run: `make wifey-lowvol-audit`
Expected: the 2×2 grid prints at 0 / 2 / 8 bps with PASS/FAIL on `beta_neutral_ls`, and `realized_beta` for the beta-neutral cells reads ≈0 (sanity-check the neutralization). Capture the output.

- [ ] **Step 2: Write the verdict note**

Create `docs/audits/2026-06-22-edge-hunt-2-lowvol-bab.md` following the structure of `docs/audits/2026-06-21-experiment-1-residual-xsmom.md`: headline verdict (PASS / FAIL / deploy-grade), the 2×2 grid table at each cost, the gate read on the committed cell, the realized-beta diagnostic, the long-only-leg Sharpe, the survivorship flag, and the roadmap implication (a clean fail → pivot to edge-hunt #3 cross-asset TSMOM; a pass → the binding constraint cracks and ≥2-sleeve convergence work unlocks). Hand-format markdown to the full markdownlint ruleset.

- [ ] **Step 3: Lint markdown + commit**

```bash
make lint-md
git add docs/audits/2026-06-22-edge-hunt-2-lowvol-bab.md
git commit -m "docs(lowvol): edge-hunt #2 BAB verdict note

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## After execution

- `/pr-summary` → write the PR body, then `gh pr create --repo s10023/buibui-wifey-wall-street-bot` (the explicit `--repo` is mandatory in this fork).
- `/post-branch` → behaviour-gated docs sweep (CLAUDE.md `analytics/lowvol/` entry, `make wifey-lowvol-audit` in the tools list, README if touched) before reporting the PR URL.
- Update `MEMORY.md` Current State + the roadmap pointer in `project_todo_master.md` / `project_binding_constraint_no_equity_edge.md` with the verdict.
- Rewrite `docs/plans/next-conversation-prompt.md` for the next session (per the always-update-handoff preference).
