# Edge-Hunt #3 — Cross-Asset TSMOM Sleeve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cross-asset time-series momentum (TSMOM) sleeve that runs the existing `forecast/` EWMAC engine over a frozen 13-ETF cross-asset basket, scored on a pre-registered net-of-cost gate with an equity-beta guardrail — as an additive, read-only package so the regression goldens stay byte-identical.

**Architecture:** A new self-contained package `analytics/xasset/` mirroring the `forecast/` / `xsmom/` / `lowvol/` split (`universe.py` frozen basket, `replay.py` DB-only, `report.py` gate, `__init__.py` re-exports) plus a read-only `tools/xasset_audit.py`. It reuses `run_forecast_backtest` (`forecast/book.py`), `load_daily_inputs` (`forecast/replay.py`), `evaluate` / `G2Report` (`forecast/report.py`), and `beta_attribution` (`xsmom/diagnostics.py`). The only edit to existing code is a default-off `long_only` kwarg on the forecast book (the "long-flat" deployable form); nothing on the detector / backtest / signal path imports any of this, so the goldens cannot move.

**Tech Stack:** Python 3.11, numpy, pandas, duckdb (in-memory for tests), pytest, ruff, mypy strict. Spec: `docs/superpowers/specs/2026-06-22-edge-hunt-3-cross-asset-tsmom-design.md`.

---

## Setup (before Task 1)

This plan ships alongside the spec as a **docs-only PR** (branch
`docs/edge-hunt-3-cross-asset-tsmom-spec`). Execution happens later on a fresh
feature branch:

- [ ] After the docs PR merges, branch off `main`:
  `git checkout main && git pull && git checkout -b feat/xasset-tsmom-sleeve`
- [ ] Confirm the baseline is green: `make test` passes (1718 / 3 skip) and
  `make test-regression` PASSES both configs. These are the byte-identical
  goldens the sleeve must not move.
- [ ] Verify the per-repo git identity before the first commit:
  `git config --local user.email` must print `ngkhaijian@gmail.com`.

## File structure

- Create `analytics/xasset/__init__.py` — eager re-exports (Task 5).
- Create `analytics/xasset/universe.py` — frozen basket + asset-class tags (Task 1).
- Modify `analytics/forecast/book.py` — additive default-off `long_only` kwarg (Task 2).
- Create `analytics/xasset/replay.py` — the only DB-touching module; runs the 2×2 + SPY benchmark (Task 3).
- Create `analytics/xasset/report.py` — `XAssetGridReport` + `evaluate_xasset_grid` gate (Task 4).
- Create `tools/xasset_audit.py` — read-only verdict CLI (Task 6).
- Modify `Makefile` — add `wifey-xasset-audit` + `wifey-xasset-backfill` targets + `.PHONY` entries (Task 6).
- Modify `tests/forecast/test_book_instrument.py` — `long_only` parity + no-short tests (Task 2).
- Create `tests/test_xasset_universe.py` (Task 1), `tests/test_xasset_replay.py` (Task 3), `tests/test_xasset_report.py` (Task 4).
- Create `docs/audits/2026-06-22-edge-hunt-3-cross-asset-tsmom.md` — verdict note (Task 8).

The 2×2 grid keys are fixed: `broad_ls` (**gated cell**), `broad_long`, `commodity_ls`, `commodity_long`.

---

### Task 1: Frozen cross-asset universe

**Files:**

- Create: `analytics/xasset/universe.py`
- Test: `tests/test_xasset_universe.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_xasset_universe.py
from analytics.xasset.universe import (
    BROAD_BASKET,
    COMMODITY_BASKET,
    MARKET_PROXY,
    broad_symbols,
    commodity_symbols,
)


def test_broad_basket_has_13_unique_symbols() -> None:
    syms = broad_symbols()
    assert len(syms) == 13
    assert len(set(syms)) == 13  # no duplicate tickers


def test_commodity_basket_is_a_strict_subset() -> None:
    broad = set(broad_symbols())
    commodity = set(commodity_symbols())
    assert commodity == {"GLD", "SLV", "DBC", "USO", "DBA"}
    assert commodity < broad  # strict subset


def test_market_proxy_is_in_the_basket() -> None:
    assert MARKET_PROXY in broad_symbols()


def test_every_member_has_a_nonempty_asset_class() -> None:
    assert len(BROAD_BASKET) == 13
    assert len(COMMODITY_BASKET) == 5
    for m in BROAD_BASKET:
        assert m.asset_class
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_xasset_universe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xasset'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# analytics/xasset/universe.py
"""Frozen, pre-registered cross-asset ETF basket for edge-hunt #3 (TSMOM).

Pure, no I/O. The basket and its metals/commodity sub-set are committed here as
the pre-registration artifact: changing them after a result exists would
re-introduce selection bias. All 13 ETFs are liquid, free on yfinance, and have
full daily history from 2007-03-01 (UUP, the latest listing).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetMember:
    """One basket member: ticker + its broad asset-class tag."""

    symbol: str
    asset_class: str


BROAD_BASKET: tuple[AssetMember, ...] = (
    AssetMember("SPY", "equity"),
    AssetMember("QQQ", "equity"),
    AssetMember("EFA", "equity"),
    AssetMember("EEM", "equity"),
    AssetMember("TLT", "rates"),
    AssetMember("IEF", "rates"),
    AssetMember("LQD", "credit"),
    AssetMember("GLD", "metal"),
    AssetMember("SLV", "metal"),
    AssetMember("DBC", "commodity"),
    AssetMember("USO", "commodity"),
    AssetMember("DBA", "commodity"),
    AssetMember("UUP", "fx"),
)

# The narrow contrast arm: metals + commodities only.
_COMMODITY_CLASSES = frozenset({"metal", "commodity"})
COMMODITY_BASKET: tuple[AssetMember, ...] = tuple(
    m for m in BROAD_BASKET if m.asset_class in _COMMODITY_CLASSES
)

# The equity-beta benchmark used by the realized-beta guardrail.
MARKET_PROXY = "SPY"


def broad_symbols() -> list[str]:
    """All 13 basket tickers, in pre-registered order."""
    return [m.symbol for m in BROAD_BASKET]


def commodity_symbols() -> list[str]:
    """The 5 metals/commodity tickers (the narrow contrast arm)."""
    return [m.symbol for m in COMMODITY_BASKET]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_xasset_universe.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xasset/universe.py tests/test_xasset_universe.py
git commit -m "feat(xasset): frozen cross-asset ETF basket (edge-hunt #3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `long_only` clip on the forecast book

The deployable "long-flat" form clips the combined forecast at zero before
sizing, so leverage is never negative. This is the **only** edit to existing
code. It is a keyword-only, default-`False` parameter: when `False` (every
existing call site) the output is byte-identical, so the forecast sleeve's own
tests, its G2 audit, and the regression goldens are all unmoved.

**Files:**

- Modify: `analytics/forecast/book.py`
- Test: `tests/forecast/test_book_instrument.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/forecast/test_book_instrument.py` (the file already defines the
`_close()` helper and imports `instrument_returns` + `ForecastConfig`):

```python
def test_long_only_false_is_byte_identical_to_default() -> None:
    close = _close()
    zero = pd.Series(0.0, index=close.index)
    cfg = ForecastConfig()
    default = instrument_returns(close, zero, cfg)
    explicit = instrument_returns(close, zero, cfg, long_only=False)
    assert default.equals(explicit)


def test_long_only_true_never_shorts() -> None:
    close = _close()
    zero = pd.Series(0.0, index=close.index)
    out = instrument_returns(close, zero, ForecastConfig(), long_only=True)
    lev = out["leverage"].dropna()
    assert (lev >= 0.0).all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/forecast/test_book_instrument.py -v`
Expected: FAIL — `instrument_returns() got an unexpected keyword argument 'long_only'`.

- [ ] **Step 3: Write the minimal implementation**

In `analytics/forecast/book.py`, change the `instrument_returns` signature and
the forecast line. Replace:

```python
def instrument_returns(
    close: pd.Series,
    funding_daily: pd.Series,
    cfg: ForecastConfig,
) -> pd.DataFrame:
    """Causal subsystem returns for one instrument.

    Columns: leverage, gross, turnover_cost, funding_cost, net (indexed like
    `close`). `funding_daily` is the day's summed funding rate aligned to the
    close index (0.0 where missing). Equities carry no funding, so the caller
    passes an all-zero series.
    """
    forecast = combine_forecasts(
        close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
    ).shift(1)
```

with:

```python
def instrument_returns(
    close: pd.Series,
    funding_daily: pd.Series,
    cfg: ForecastConfig,
    *,
    long_only: bool = False,
) -> pd.DataFrame:
    """Causal subsystem returns for one instrument.

    Columns: leverage, gross, turnover_cost, funding_cost, net (indexed like
    `close`). `funding_daily` is the day's summed funding rate aligned to the
    close index (0.0 where missing). Equities carry no funding, so the caller
    passes an all-zero series.

    When ``long_only`` is True the combined forecast is clipped at zero before
    sizing (long or flat, never short) — the deployable no-short form. Default
    False is byte-identical to the signed book.
    """
    forecast = combine_forecasts(
        close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
    )
    if long_only:
        forecast = forecast.clip(lower=0.0)
    forecast = forecast.shift(1)
```

Then thread the flag through `run_forecast_backtest`. Replace its signature:

```python
def run_forecast_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> ForecastBookResult:
    """Aggregate per-instrument subsystem returns + causal vol governor."""
```

with:

```python
def run_forecast_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
    *,
    long_only: bool = False,
) -> ForecastBookResult:
    """Aggregate per-instrument subsystem returns + causal vol governor.

    ``long_only`` is threaded to ``instrument_returns`` (default False is the
    signed book, byte-identical to before).
    """
```

and inside its loop replace:

```python
        out = instrument_returns(close, fund, cfg)
```

with:

```python
        out = instrument_returns(close, fund, cfg, long_only=long_only)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/forecast/ -v`
Expected: PASS — the two new tests pass and every existing forecast test still
passes (the default path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/book.py tests/forecast/test_book_instrument.py
git commit -m "feat(forecast): default-off long_only clip on the book (for xasset long-flat)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Replay the 2×2 grid + SPY benchmark (DB front door)

**Files:**

- Create: `analytics/xasset/replay.py`
- Test: `tests/test_xasset_replay.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_xasset_replay.py
import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xasset.replay import replay_xasset_grid, xasset_market_return
from analytics.xasset.universe import broad_symbols

_DAY = 86_400_000
_T0 = 1_514_764_800_000  # 2018-01-01T00:00:00Z in ms


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0005, 0.01, size=500)
    close = 100.0 * np.exp(np.cumsum(steps))
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


def _seed_basket(conn: duckdb.DuckDBPyConnection) -> None:
    for i, s in enumerate(broad_symbols()):
        _seed(conn, s, seed=i)


def test_replay_xasset_grid_returns_four_books() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_basket(conn)
    books = replay_xasset_grid(conn, ForecastConfig())
    assert set(books) == {
        "broad_ls",
        "broad_long",
        "commodity_ls",
        "commodity_long",
    }
    assert books["broad_ls"].portfolio_return.shape[0] > 0


def test_xasset_market_return_is_a_series() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_basket(conn)
    mkt = xasset_market_return(conn)
    assert isinstance(mkt, pd.Series)
    assert len(mkt) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_xasset_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xasset.replay'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# analytics/xasset/replay.py
"""Read-only DuckDB front door for the cross-asset TSMOM sleeve (edge-hunt #3).

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + all-zero funding)
and runs the pre-registered 2x2 grid over the frozen cross-asset ETF basket via
``run_forecast_backtest``. The only module in ``analytics/xasset/`` that touches
the DB; never writes.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.xasset.universe import MARKET_PROXY, broad_symbols, commodity_symbols


def xasset_market_return(conn: duckdb.DuckDBPyConnection) -> pd.Series:
    """SPY daily return — the equity-beta benchmark for the guardrail (read-only).

    Loaded on its own (not as an equal-weight basket mean) so the realized-beta
    diagnostic regresses each book against the actual equity market proxy.
    """
    closes, _ = load_daily_inputs(conn, [MARKET_PROXY])
    spy = closes.get(MARKET_PROXY)
    if spy is None:
        return pd.Series(dtype=float)
    return spy.pct_change()


def replay_xasset_grid(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
) -> dict[str, ForecastBookResult]:
    """Run the pre-registered `{broad,commodity} x {long-short,long-flat}` 2x2.

    All four books run through the shared ``run_forecast_backtest`` (multi-speed
    EWMAC + portfolio vol governor). The gated cell is ``broad_ls``; the other
    three are diagnostic + feed the DSR/PBO trial count.
    """
    broad_closes, broad_fund = load_daily_inputs(conn, broad_symbols())
    comm_closes, comm_fund = load_daily_inputs(conn, commodity_symbols())

    books: dict[str, ForecastBookResult] = {}
    books["broad_ls"] = run_forecast_backtest(broad_closes, broad_fund, cfg)
    books["broad_long"] = run_forecast_backtest(
        broad_closes, broad_fund, cfg, long_only=True
    )
    books["commodity_ls"] = run_forecast_backtest(comm_closes, comm_fund, cfg)
    books["commodity_long"] = run_forecast_backtest(
        comm_closes, comm_fund, cfg, long_only=True
    )
    return books
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_xasset_replay.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/xasset/replay.py tests/test_xasset_replay.py
git commit -m "feat(xasset): read-only 2x2 grid replay + SPY benchmark

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Gate report (`evaluate_xasset_grid`)

**Files:**

- Create: `analytics/xasset/report.py`
- Test: `tests/test_xasset_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_xasset_report.py
import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xasset.replay import replay_xasset_grid, xasset_market_return
from analytics.xasset.report import evaluate_xasset_grid
from analytics.xasset.universe import broad_symbols

_DAY = 86_400_000
_T0 = 1_514_764_800_000  # 2018-01-01T00:00:00Z in ms


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0005, 0.01, size=500)
    close = 100.0 * np.exp(np.cumsum(steps))
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


def _seed_basket(conn: duckdb.DuckDBPyConnection) -> None:
    for i, s in enumerate(broad_symbols()):
        _seed(conn, s, seed=i)


def test_evaluate_xasset_grid_structure_and_gate() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_basket(conn)
    cfg = ForecastConfig()
    books = replay_xasset_grid(conn, cfg)
    mkt = xasset_market_return(conn)
    rep = evaluate_xasset_grid(books, cfg, mkt)
    assert set(rep.cells) == {
        "broad_ls",
        "broad_long",
        "commodity_ls",
        "commodity_long",
    }
    assert rep.committed_key == "broad_ls"
    assert set(rep.attribution) == set(rep.cells)
    assert isinstance(rep.passed, bool)
    assert isinstance(rep.deploy_grade, bool)
    # the realized equity beta of every cell is a finite number
    assert np.isfinite(rep.attribution["broad_ls"].beta)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_xasset_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xasset.report'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# analytics/xasset/report.py
"""Assemble the cross-asset TSMOM 2x2 verdict: per-cell guards + equity-beta + gate.

Reuses ``forecast.report.evaluate`` (DSR/PBO/boot-CI/MinTRL over the 4-book trial
family) and ``xsmom.diagnostics.beta_attribution`` (realized beta to SPY). The
gate is read on the pre-committed ``broad_ls`` cell only; the full 4-book family
feeds the DSR deflation + CSCV/PBO trial count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.forecast.report import G2Report, evaluate
from analytics.xsmom.diagnostics import BetaAttribution, beta_attribution

_GATE_SHARPE = 0.7  # pre-registered net-of-cost TSMOM bar
_DEPLOY_SHARPE = 1.0  # deploy-grade tier annotation (NOT the pass/fail line)


@dataclass(frozen=True)
class XAssetGridReport:
    """The 2x2 grid's per-cell G2Reports + realized equity-beta + verdict."""

    cells: dict[str, G2Report]
    attribution: dict[str, BetaAttribution]
    committed_key: str
    passed: bool
    deploy_grade: bool


def evaluate_xasset_grid(
    books: dict[str, ForecastBookResult],
    cfg: ForecastConfig,
    market_ret: pd.Series,
    *,
    committed_key: str = "broad_ls",
    long_only_key: str = "broad_long",
) -> XAssetGridReport:
    """Score the 2x2 and read the pre-registered gate on the committed cell.

    ``market_ret`` is the SPY daily return; the realized beta of each book to it
    is the thesis guardrail (the committed cross-asset book should read ≈0). The
    diagnostic is restricted to each book's live window (``portfolio_return`` is
    0.0 during the governor warm-up).
    """
    family = {k: v.portfolio_return for k, v in books.items()}
    cells: dict[str, G2Report] = {}
    attribution: dict[str, BetaAttribution] = {}
    for key, book in books.items():
        cells[key] = evaluate(book, cfg, trial_returns=family)
        r = book.portfolio_return
        m: npt.NDArray[np.float64] = market_ret.reindex(book.daily_index).to_numpy(
            dtype=np.float64
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
    return XAssetGridReport(
        cells=cells,
        attribution=attribution,
        committed_key=committed_key,
        passed=passed,
        deploy_grade=deploy_grade,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_xasset_report.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add analytics/xasset/report.py tests/test_xasset_report.py
git commit -m "feat(xasset): 2x2 gate report + equity-beta guardrail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Package `__init__` re-exports

**Files:**

- Create: `analytics/xasset/__init__.py`

- [ ] **Step 1: Write the implementation**

```python
# analytics/xasset/__init__.py
"""Cross-asset TSMOM sleeve (edge-hunt #3) — trend-following a frozen ETF basket."""

from analytics.xasset.replay import replay_xasset_grid, xasset_market_return
from analytics.xasset.report import XAssetGridReport, evaluate_xasset_grid
from analytics.xasset.universe import (
    BROAD_BASKET,
    COMMODITY_BASKET,
    MARKET_PROXY,
    AssetMember,
    broad_symbols,
    commodity_symbols,
)

__all__ = [
    "BROAD_BASKET",
    "COMMODITY_BASKET",
    "MARKET_PROXY",
    "AssetMember",
    "XAssetGridReport",
    "broad_symbols",
    "commodity_symbols",
    "evaluate_xasset_grid",
    "replay_xasset_grid",
    "xasset_market_return",
]
```

- [ ] **Step 2: Verify the package imports cleanly**

Run: `poetry run python -c "import analytics.xasset as x; print(x.__all__)"`
Expected: prints the `__all__` list, no ImportError.

- [ ] **Step 3: Commit**

```bash
git add analytics/xasset/__init__.py
git commit -m "feat(xasset): package __init__ re-exports

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Read-only audit CLI + Makefile targets

**Files:**

- Create: `tools/xasset_audit.py`
- Modify: `Makefile`

- [ ] **Step 1: Write the audit CLI**

```python
# tools/xasset_audit.py
"""Edge-hunt #3 — cross-asset TSMOM 2x2 audit (read-only verdict).

Runs the pre-registered {broad,commodity} x {long-short,long-flat} grid over the
frozen cross-asset ETF basket (1d), prints each cell's headline + DSR/PBO/boot-CI/
MinTRL + realized equity-beta to SPY, the cost-sensitivity sweep (0/2/8 bps), and
the PASS/FAIL + deploy-grade flag on the committed `broad_ls` cell. No writes.

Usage::

    PYTHONPATH=. poetry run python tools/xasset_audit.py
    PYTHONPATH=. poetry run python tools/xasset_audit.py --slippage-bps 8
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.xasset.replay import replay_xasset_grid, xasset_market_return
from analytics.xasset.report import XAssetGridReport, evaluate_xasset_grid
from analytics.xasset.universe import broad_symbols


def build_grid(
    conn: duckdb.DuckDBPyConnection,
    *,
    slippage_bps: float,
) -> XAssetGridReport:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    books = replay_xasset_grid(conn, cfg)
    mkt = xasset_market_return(conn)
    return evaluate_xasset_grid(books, cfg, mkt)


def _grid_frame(rep: XAssetGridReport) -> pd.DataFrame:
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
                "equity_beta": a.beta,
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
    print(f"basket={len(broad_symbols())} ETFs: {' '.join(broad_symbols())}")

    for bps in (0.0, args.slippage_bps, 8.0):
        rep = build_grid(conn, slippage_bps=bps)
        print(f"\n=== cross-asset TSMOM 2x2 grid @ {bps:g} bps ===")
        print(
            _grid_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.3f}")
        )
        verdict = "PASS" if rep.passed else "FAIL"
        deploy = " (DEPLOY-GRADE)" if rep.deploy_grade else ""
        print(f"committed cell ({rep.committed_key}) @ {bps:g}bps: {verdict}{deploy}")

    print(
        "\nGate (pre-registered, broad x long-short): DSR>=0.95 ∧ PBO<=0.5 ∧ "
        "boot_lo>0 ∧ n>=MinTRL ∧ Sharpe>=0.7; deploy-grade tier = Sharpe>=1.0 ∧ "
        "long-flat leg>=0.7. equity_beta is the realized beta to SPY — the "
        "committed cross-asset book should read ≈0 (the diversification thesis). "
        "USO contango + ETF tracking error are accepted free-data proxy "
        "imperfections; short-borrow cost on the L/S legs is omitted (mildly "
        "optimistic)."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the Makefile targets**

In `Makefile`, append `wifey-xasset-audit wifey-xasset-backfill` to the `.PHONY`
list on line 14 (immediately after `wifey-lowvol-audit`). Then add both targets
after the `wifey-lowvol-audit` block (around line 139):

```text
wifey-xasset-audit:
    @echo "🔬 Edge-hunt #3 — cross-asset TSMOM 2x2 audit over the frozen ETF basket (1d)..."
    @PYTHONPATH=. poetry run python tools/xasset_audit.py $(ARGS)

wifey-xasset-backfill:
    @echo "📥 Backfilling the cross-asset TSMOM ETF basket (1d) from $(or $(SINCE),2007-03-01)..."
    @poetry run python wifey.py analytics backfill \
        --symbols SPY QQQ EFA EEM TLT IEF LQD GLD SLV DBC USO DBA UUP \
        --timeframes 1d --since $(or $(SINCE),2007-03-01)
```

(Both recipe bodies must each begin with a real TAB, not the spaces shown here —
Make requires a TAB; the spaces above are only to satisfy the markdown linter.)

- [ ] **Step 3: Verify the audit CLI parses and the make target is wired**

Run: `PYTHONPATH=. poetry run python tools/xasset_audit.py --help`
Expected: prints the argparse help with `--db` and `--slippage-bps`.

Run: `make -n wifey-xasset-backfill`
Expected: dry-run prints the `wifey.py analytics backfill --symbols …` command.

- [ ] **Step 4: Commit**

```bash
git add tools/xasset_audit.py Makefile
git commit -m "feat(xasset): read-only TSMOM audit CLI + make targets

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Full gate — lint, types, suite, goldens byte-identical

**Files:** none new (verification + fixups only).

- [ ] **Step 1: Format + lint**

Run: `make lint-py`
Expected: ruff format reports the new files already formatted (or auto-formats
them), lint clean. If ruff reformats, re-run and commit:
`git add -A && git commit -m "style: ruff format xasset package"`.

- [ ] **Step 2: Type-check (mypy strict)**

Run: `make typecheck`
Expected: `Success: no issues found`. If mypy flags a missing annotation, add it
and re-run. (All new public functions already carry full annotations.)

- [ ] **Step 3: Full test suite**

Run: `make test`
Expected: every test passes, including the new `tests/test_xasset_*.py` (7 new
tests) and the two new forecast-book tests, no regressions.

- [ ] **Step 4: Regression goldens byte-identical**

Run: `make test-regression`
Expected: both configs PASS, goldens unmoved. The `analytics/xasset/` package is
additive and imported by nothing on the detector / backtest / signal path, and
the `forecast/book.py` change is a default-off kwarg, so the golden pipeline
cannot see either. If a golden moves, STOP — something is wrong (an unintended
import or a non-default `long_only` path); do not regenerate.

- [ ] **Step 5: Commit any fixups**

```bash
git add -A
git commit -m "test(xasset): green suite + goldens byte-identical

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Backfill, run the audit, write the verdict

**Files:**

- Create: `docs/audits/2026-06-22-edge-hunt-3-cross-asset-tsmom.md`

- [ ] **Step 1: Backfill the cross-asset basket (one-shot, writes to the real DB)**

Run: `make wifey-xasset-backfill`
Expected: 13 ETFs backfilled at 1d from 2007-03-01 into the real `analytics.db`
(idempotent upsert). Watch for any `NO DATA` / all-quarantined warnings; all 13
were confirmed available on yfinance during spec self-review (UUP first bar
2007-03-01). If a ticker comes back short, note it in the verdict — do not change
the frozen basket (that would break pre-registration).

- [ ] **Step 2: Run the audit against the real DB**

Run: `make wifey-xasset-audit`
Expected: the 2×2 grid prints at 0 / 2 / 8 bps with PASS/FAIL on `broad_ls`, and
`equity_beta` for the committed cell reads ≈ 0 (the diversification thesis check).
Capture the output verbatim.

- [ ] **Step 3: Write the verdict note**

Create `docs/audits/2026-06-22-edge-hunt-3-cross-asset-tsmom.md` following the
structure of `docs/audits/2026-06-22-edge-hunt-2-lowvol-bab.md`: headline verdict
(PASS / FAIL / deploy-grade), the 2×2 grid table at each cost, the gate read on
the committed `broad_ls` cell, the realized equity-beta diagnostic (is it ≈ 0?),
the long-flat leg Sharpe, the no-survivorship note (continuous ETFs), and the
roadmap implication: a clean fail → the binding constraint stands, pivot to
edge-hunt #4 (PEAD-lite) or trigger the honest-exit ($29 Polygon) criterion; a
pass → the binding constraint cracks and ≥ 2-sleeve convergence work
(`portfolio/` sizing ports) unlocks. Hand-format markdown to the full
markdownlint ruleset (memory: CI is stricter than local — mind MD060 tables,
MD018, line wrapping).

- [ ] **Step 4: Lint markdown + commit**

```bash
make lint-md
git add docs/audits/2026-06-22-edge-hunt-3-cross-asset-tsmom.md
git commit -m "docs(xasset): edge-hunt #3 cross-asset TSMOM verdict note

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## After execution

- [ ] Run `/pr-summary` to draft the feat PR title + summary + test plan.
- [ ] `gh pr create --repo s10023/buibui-wifey-wall-street-bot` (the explicit
  `--repo` is mandatory — the gh default points at the parent).
- [ ] Run `/post-branch` for the docs sweep (CLAUDE.md `analytics/xasset/` entry,
  README, MEMORY.md Current State + per-sleeve verdict log in
  [[binding-constraint-no-equity-edge]], the new Makefile targets).
- [ ] Update `docs/plans/next-conversation-prompt.md` with the #3 outcome and the
  pivot to edge-hunt #4 / honest-exit.
