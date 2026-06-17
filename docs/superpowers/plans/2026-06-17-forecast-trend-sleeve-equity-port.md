# Forecast Trend Sleeve — Equity Port (PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the parent's `analytics/forecast/` EWMAC trend sleeve into wifey,
adapted crypto→equity (252-day annualization, zero funding, equity breadth
universe), as an additive read-only package that leaves regression goldens
byte-identical and produces wifey's first continuous vol-targeted equity G2
verdict.

**Architecture:** Pure-math primitives (`vol`, `ewmac`, `config`, `weights`,
`metrics`) → causal per-instrument book + portfolio governor (`book`) → read-only
DuckDB/universe front door (`replay`) → research-guard verdict (`report`) → CLI
audit (`tools/forecast_audit.py`). Mirrors the parent module-for-module; the only
behavioural changes are the seven adaptation decisions in the campaign spec
(`docs/superpowers/specs/2026-06-17-systematic-sleeves-equity-port-design.md`).

**Tech Stack:** Python 3.11+, pandas, numpy, duckdb; `analytics/research_guards/`
(wifey N2), `utils.config_validation.load_research_universe` (N3),
`analytics/store/market_data.get_ohlcv`. Test: pytest + `duckdb.connect(":memory:")`.

**Parent source root:** `/home/kng/repo/buibui-moon-trader-bot` (on `main`,
read-only). Cited as `PARENT/...` below.

---

## File Structure

- Create `analytics/forecast/__init__.py` — eager re-exports.
- Create `analytics/forecast/config.py` — `ForecastConfig` (annualization 252).
- Create `analytics/forecast/vol.py` — causal EW vol (annualize default 252).
- Create `analytics/forecast/ewmac.py` — EWMAC forecast math (verbatim).
- Create `analytics/forecast/weights.py` — candidate weight schemes (verbatim).
- Create `analytics/forecast/metrics.py` — curve-metrics slice (PPY 252).
- Create `analytics/forecast/book.py` — per-instrument + portfolio book (funding fed zeros).
- Create `analytics/forecast/report.py` — `G2Report` + `evaluate` (passes PPY from cfg).
- Create `analytics/forecast/replay.py` — read-only DB seam (no funding, equity universe, `min_history_days`).
- Create `tools/forecast_audit.py` — CLI G2 audit.
- Modify `Makefile` — add `wifey-forecast-audit`.
- Create `tests/forecast/` package mirroring `PARENT/tests/forecast/`.

---

## Task 0: Pre-flight — branch, package skeleton, guard-signature check

**Files:**

- Create: `analytics/forecast/__init__.py` (empty placeholder for now)
- Create: `tests/forecast/__init__.py`
- Test: `tests/forecast/test_guard_signatures.py`

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull
git checkout -b feat/forecast-trend-sleeve-equity
git config --local user.email   # must print ngkhaijian@gmail.com
```

- [ ] **Step 2: Write the failing guard-signature test**

The forecast `report` binds to four research-guard functions. Verify wifey's
verbatim N2 port exposes the exact signatures the parent's `report` calls, so a
drift surfaces here, not deep in Task 6.

```python
# tests/forecast/test_guard_signatures.py
import numpy as np

from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)


def test_guard_signatures_match_parent_report_calls() -> None:
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, size=400)

    # block_bootstrap_ci(r, stat_fn=, seed=) -> obj with .lo/.hi
    boot = block_bootstrap_ci(r, stat_fn=lambda x: float(np.mean(x)), seed=7)
    assert hasattr(boot, "lo") and hasattr(boot, "hi")

    # cscv_pbo(mat) -> obj with .pbo
    mat = np.column_stack([r, rng.normal(0.0, 0.01, size=400)])
    assert hasattr(cscv_pbo(mat), "pbo")

    # deflated_sharpe_ratio(sr, n_obs, trial_srs=) -> float
    dsr = deflated_sharpe_ratio(0.05, len(r), trial_srs=[0.05, 0.02])
    assert isinstance(dsr, float)

    # min_track_record_length(sr, target_sr=, confidence=) -> float
    mtrl = min_track_record_length(0.05, target_sr=0.01, confidence=0.95)
    assert isinstance(mtrl, float)
```

- [ ] **Step 3: Add the empty package markers**

```python
# analytics/forecast/__init__.py
"""EWMAC trend sleeve (P2, equity port). Re-exports filled in Task 9."""
```

```python
# tests/forecast/__init__.py  (empty file)
```

- [ ] **Step 4: Run the test**

Run: `poetry run pytest tests/forecast/test_guard_signatures.py -v`
Expected: PASS (if any assertion fails, STOP — the N2 guard port drifted; fix the
binding before continuing).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/__init__.py tests/forecast/
git commit -m "test(forecast): pre-flight guard-signature check + package skeleton"
```

---

## Task 1: Port the pure primitives — `vol.py` + `ewmac.py`

**Files:**

- Create: `analytics/forecast/vol.py`
- Create: `analytics/forecast/ewmac.py`
- Test: `tests/forecast/test_vol.py`, `tests/forecast/test_ewmac.py`

- [ ] **Step 1: Write `vol.py` (verbatim except the `annualize` default → 252)**

```python
"""Causal exponentially-weighted volatility estimators for the trend sleeve.

All estimators are shifted so the value at day `d` uses only returns through
day `d-1` — the position held during day `d` is sized on yesterday's information.
"""

from __future__ import annotations

import math

import pandas as pd


def ew_return_vol(close: pd.Series, span: int) -> pd.Series:
    """Causal EW std of daily simple returns (decimal, e.g. 0.03 = 3%/day)."""
    returns = close.pct_change()
    return returns.ewm(span=span, min_periods=span).std().shift(1)


def price_vol(close: pd.Series, span: int) -> pd.Series:
    """Causal price volatility in price units = return-vol x price."""
    return ew_return_vol(close, span) * close


def annualize(daily_vol: float, days: float = 252.0) -> float:
    return daily_vol * math.sqrt(days)
```

- [ ] **Step 2: Port `ewmac.py` verbatim from the parent**

Copy `PARENT/analytics/forecast/ewmac.py` unchanged (pure, instrument-agnostic:
`raw_ewmac`, `scaled_forecast`, `combine_forecasts`; imports `price_vol` from
`analytics.forecast.vol`). No edits.

- [ ] **Step 3: Port the primitive tests + the causality test**

Port `PARENT/tests/forecast/test_vol.py` and `PARENT/tests/forecast/test_ewmac.py`.
Keep the **middle-bar perturbation (look-ahead) tests** verbatim — they are the
causality guard. Add one explicit equity-default assertion:

```python
# tests/forecast/test_vol.py  (append)
from analytics.forecast.vol import annualize


def test_annualize_defaults_to_252_trading_days() -> None:
    # equity adaptation D1: NYSE sessions, not 365
    assert annualize(0.01) == 0.01 * (252.0 ** 0.5)
```

- [ ] **Step 4: Run**

Run: `poetry run pytest tests/forecast/test_vol.py tests/forecast/test_ewmac.py -v`
Expected: PASS (including the perturbation tests — verify they go RED if you
delete the `.shift(1)` in `ew_return_vol`, then restore).

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/vol.py analytics/forecast/ewmac.py tests/forecast/test_vol.py tests/forecast/test_ewmac.py
git commit -m "feat(forecast): port EWMAC vol + forecast primitives (equity, 252d)"
```

---

## Task 2: Port `config.py` (annualization 252)

**Files:**

- Create: `analytics/forecast/config.py`
- Test: `tests/forecast/test_config.py`

- [ ] **Step 1: Write the failing default-annualization test**

```python
# tests/forecast/test_config.py
from analytics.forecast.config import ForecastConfig


def test_default_annualization_is_252() -> None:
    assert ForecastConfig().annualization_days == 252.0


def test_min_history_is_longest_slow_plus_vol_span() -> None:
    cfg = ForecastConfig()  # longest slow 256 + vol_span 32
    assert cfg.min_history == 288
```

- [ ] **Step 2: Run — expect ImportError/FAIL**

Run: `poetry run pytest tests/forecast/test_config.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `config.py`**

Port `PARENT/analytics/forecast/config.py` with one edit: default
`annualization_days: float = 252.0` (was 365.0). Keep `from_toml` (reads
`[backtest].fee_pct` / `slippage_bps`; wifey's block lacks `slippage_bps` so it
falls back to the 2 bps default — that is intended, see spec D3). Keep the
`weights` field + `__post_init__` length check (additive weight-study path).

```python
"""Configuration for the EWMAC trend sleeve (P2, equity port)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_SPEEDS: tuple[tuple[int, int, float], ...] = (
    (8, 32, 5.3),
    (16, 64, 3.75),
    (32, 128, 2.65),
    (64, 256, 1.91),
)


@dataclass(frozen=True)
class ForecastConfig:
    speeds: tuple[tuple[int, int, float], ...] = _DEFAULT_SPEEDS
    vol_span: int = 32
    fdm: float = 1.25
    cap: float = 20.0
    vol_target_annual: float = 0.20
    fee_pct: float = 0.0001
    slippage_pct: float = 0.0002
    gov_window: int = 64
    g_min: float = 0.5
    g_max: float = 1.5
    annualization_days: float = 252.0
    weights: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if self.weights is not None and len(self.weights) != len(self.speeds):
            raise ValueError(
                f"weights length {len(self.weights)} != "
                f"speeds length {len(self.speeds)}"
            )

    @property
    def min_history(self) -> int:
        longest_slow = max(slow for _, slow, _ in self.speeds)
        return longest_slow + self.vol_span

    @classmethod
    def from_toml(cls, path: Path | str) -> "ForecastConfig":
        with open(path, "rb") as f:
            data = tomllib.load(f)
        bt = data.get("backtest", {})
        fee = float(bt.get("fee_pct", 0.0001))
        slip_bps = float(bt.get("slippage_bps", 2.0))
        return cls(fee_pct=fee, slippage_pct=slip_bps / 10_000.0)
```

Note: equity defaults `fee_pct=0.0001` (1 bp) vs the parent's `0.0005` (spec D3).

- [ ] **Step 4: Run — expect PASS**

Run: `poetry run pytest tests/forecast/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/config.py tests/forecast/test_config.py
git commit -m "feat(forecast): port ForecastConfig (equity defaults — 252d, 1bp fee)"
```

---

## Task 3: Port the curve-metrics slice — `metrics.py` (PPY 252)

**Files:**

- Create: `analytics/forecast/metrics.py`
- Test: `tests/forecast/test_metrics.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/forecast/test_metrics.py
import numpy as np
import pandas as pd

from analytics.forecast import metrics


def _curve(daily_ret: float, n: int) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.Series((1.0 + daily_ret) ** np.arange(n), index=idx)


def test_sharpe_uses_252_by_default() -> None:
    # pure-drift curve has ~0 std -> degenerate guard returns 0.0
    assert metrics.sharpe(_curve(0.001, 500)) == 0.0


def test_sharpe_annualizes_with_252() -> None:
    rng = np.random.default_rng(1)
    rets = rng.normal(0.0005, 0.01, size=1000)
    curve = pd.Series((1.0 + pd.Series(rets)).cumprod())
    r = curve.pct_change().dropna()
    expected = float(r.mean() / r.std(ddof=1) * np.sqrt(252.0))
    assert abs(metrics.sharpe(curve) - expected) < 1e-9


def test_flat_curve_is_zero_not_nan() -> None:
    flat = pd.Series([1.0, 1.0, 1.0, 1.0])
    assert metrics.sharpe(flat) == 0.0
    assert metrics.max_drawdown(flat) == 0.0
    assert metrics.calmar(flat) == 0.0
```

- [ ] **Step 2: Run — expect FAIL**

Run: `poetry run pytest tests/forecast/test_metrics.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `metrics.py` (curve slice of `PARENT/portfolio/metrics.py`)**

Port only the curve functions; drop `avg_exposure` / `risk_turnover` /
`attribution` and the `from portfolio.book import ...` dependency. Default
`_PPY = 252.0`.

```python
"""Risk-adjusted metrics on a daily equity curve (equity port, 252 sessions/yr).

Pure functions over a pandas daily curve (Series indexed by UTC day). Degenerate
inputs (flat / single-point curves) return 0.0 rather than NaN. Curve slice of
the parent's portfolio.metrics — the book-dependent attribution functions are
intentionally not ported (see spec D7).
"""

from __future__ import annotations

import math

import pandas as pd

_PPY = 252.0


def daily_returns(curve: pd.Series) -> pd.Series:
    return curve.pct_change().dropna()


def sharpe(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    if sd < 1e-10:
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sortino(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    if len(r) < 2:
        return 0.0
    downside = r[r < 0.0]
    dd = float(math.sqrt(float((downside**2).mean()))) if len(downside) > 0 else 0.0
    if dd <= 0.0:
        return 0.0
    return float(r.mean() / dd * math.sqrt(periods_per_year))


def max_drawdown(curve: pd.Series) -> float:
    if len(curve) < 2:
        return 0.0
    roll_max = curve.cummax()
    dd = (curve - roll_max) / roll_max
    return float(dd.min())


def annual_return(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    if len(curve) < 2 or curve.iloc[0] <= 0.0:
        return 0.0
    total = curve.iloc[-1] / curve.iloc[0]
    if total <= 0.0:
        return -1.0
    return float(total ** (periods_per_year / len(curve)) - 1.0)


def annual_vol(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    return sd * math.sqrt(periods_per_year)


def calmar(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    mdd = abs(max_drawdown(curve))
    if mdd <= 0.0:
        return 0.0
    return annual_return(curve, periods_per_year) / mdd
```

- [ ] **Step 4: Run — expect PASS**

Run: `poetry run pytest tests/forecast/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/metrics.py tests/forecast/test_metrics.py
git commit -m "feat(forecast): port curve-metrics slice (252d PPY, no book dep)"
```

---

## Task 4: Port `weights.py` (verbatim, additive)

**Files:**

- Create: `analytics/forecast/weights.py`
- Test: `tests/forecast/test_weights.py`

- [ ] **Step 1: Port verbatim**

Copy `PARENT/analytics/forecast/weights.py` unchanged (pure: `candidate_schemes`
plus the scheme dataclass; imports only `ForecastConfig`). It defines the
a-priori and data-snooped weight schemes the `replay_weight_schemes` call iterates.

- [ ] **Step 2: Port its tests verbatim**

Copy `PARENT/tests/forecast/test_weights.py`. No equity adaptation (weights are
dimensionless; no annualization, no funding).

- [ ] **Step 3: Run**

Run: `poetry run pytest tests/forecast/test_weights.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add analytics/forecast/weights.py tests/forecast/test_weights.py
git commit -m "feat(forecast): port candidate weight schemes (verbatim)"
```

---

## Task 5: Port `book.py` (funding fed zeros)

**Files:**

- Create: `analytics/forecast/book.py`
- Test: `tests/forecast/test_book_instrument.py`, `tests/forecast/test_book_portfolio.py`

- [ ] **Step 1: Write the causality + zero-funding tests first**

```python
# tests/forecast/test_book_instrument.py
import numpy as np
import pandas as pd

from analytics.forecast.book import instrument_returns
from analytics.forecast.config import ForecastConfig


def _close(n: int = 400, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    steps = rng.normal(0.0005, 0.01, size=n)
    return pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)


def test_zero_funding_means_zero_funding_cost() -> None:
    close = _close()
    zero = pd.Series(0.0, index=close.index)
    out = instrument_returns(close, zero, ForecastConfig())
    assert (out["funding_cost"].fillna(0.0) == 0.0).all()
    # net == gross - turnover_cost when funding is zero
    recon = out["gross"] - out["turnover_cost"]
    assert np.allclose(out["net"].fillna(0.0), recon.fillna(0.0))


def test_position_is_causal_no_lookahead() -> None:
    # perturbing the LAST close must not change any earlier leverage value
    close = _close()
    cfg = ForecastConfig()
    base = instrument_returns(close, pd.Series(0.0, index=close.index), cfg)
    bumped = close.copy()
    bumped.iloc[-1] *= 1.10
    after = instrument_returns(bumped, pd.Series(0.0, index=bumped.index), cfg)
    assert np.allclose(
        base["leverage"].iloc[:-1].fillna(0.0),
        after["leverage"].iloc[:-1].fillna(0.0),
    )
```

- [ ] **Step 2: Run — expect FAIL**

Run: `poetry run pytest tests/forecast/test_book_instrument.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Port `book.py`**

Copy `PARENT/analytics/forecast/book.py` verbatim. No code edit is required —
`instrument_returns` already takes a `funding_daily` series and the equity caller
passes zeros (spec D2); `annualization_days` flows from `cfg` (252). Keep
`instrument_returns`, `ForecastBookResult`, `run_forecast_backtest`,
`equity_curve` unchanged.

- [ ] **Step 4: Port the portfolio-aggregation test**

Port `PARENT/tests/forecast/test_book_portfolio.py`; feed an all-zero funding
dict. Add an assertion that `run_forecast_backtest` over a single trending
instrument yields a finite, NaN-free `portfolio_return` of length == union index.

- [ ] **Step 5: Run — expect PASS**

Run: `poetry run pytest tests/forecast/test_book_instrument.py tests/forecast/test_book_portfolio.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add analytics/forecast/book.py tests/forecast/test_book_instrument.py tests/forecast/test_book_portfolio.py
git commit -m "feat(forecast): port causal book + portfolio governor (zero funding)"
```

---

## Task 6: Port `report.py` (G2 verdict; PPY threaded from cfg)

**Files:**

- Create: `analytics/forecast/report.py`
- Test: `tests/forecast/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/forecast/test_report.py
import numpy as np
import pandas as pd

from analytics.forecast.book import run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.report import G2Report, evaluate


def _closes(n: int = 600) -> dict[str, pd.Series]:
    rng = np.random.default_rng(3)
    idx = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    out = {}
    for k, sym in enumerate(("AAA", "BBB", "CCC")):
        steps = rng.normal(0.0006, 0.012, size=n)
        out[sym] = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)
    return out


def test_evaluate_returns_full_g2_report() -> None:
    cfg = ForecastConfig()
    closes = _closes()
    fundings = {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}
    result = run_forecast_backtest(closes, fundings, cfg)
    trials = {"combined": result.portfolio_return}
    rep = evaluate(result, cfg, trial_returns=trials)
    assert isinstance(rep, G2Report)
    assert rep.n_obs == len(result.portfolio_return)
    assert np.isfinite(rep.sharpe_annual)
    # annualization uses 252: annual_vol ~= daily_vol * sqrt(252)
    daily = pd.Series(result.portfolio_return)
    live = daily[daily != 0.0]
    if len(live) > 2:
        assert rep.annual_vol > 0.0
```

- [ ] **Step 2: Run — expect FAIL**

Run: `poetry run pytest tests/forecast/test_report.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Port `report.py` with the D1 edit**

Copy `PARENT/analytics/forecast/report.py`, then change every
`metrics.<fn>(curve)` call to pass `periods_per_year=cfg.annualization_days` so
the metrics annualization is the single `cfg` source of truth (252), not the
module default. Example in `evaluate`'s return:

```python
    return G2Report(
        sharpe_annual=metrics.sharpe(curve, cfg.annualization_days),
        sortino_annual=metrics.sortino(curve, cfg.annualization_days),
        max_dd=metrics.max_drawdown(curve),
        calmar=metrics.calmar(curve, cfg.annualization_days),
        annual_return=metrics.annual_return(curve, cfg.annualization_days),
        annual_vol=metrics.annual_vol(curve, cfg.annualization_days),
        n_obs=len(r),
        dsr=dsr,
        pbo=pbo,
        boot_lo=boot_lo,
        boot_hi=boot_hi,
        min_trl=min_trl,
    )
```

Imports change from `from portfolio import metrics` to
`from analytics.forecast import metrics`. Everything else (the `_per_period_sharpe`
helper, the guard calls, the `min_len >= 28` PBO guard) is verbatim.

- [ ] **Step 4: Run — expect PASS**

Run: `poetry run pytest tests/forecast/test_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/report.py tests/forecast/test_report.py
git commit -m "feat(forecast): port G2 report (252d annualization threaded from cfg)"
```

---

## Task 7: Port `replay.py` (no funding, equity universe, `min_history_days`)

**Files:**

- Create: `analytics/forecast/replay.py`
- Test: `tests/forecast/test_replay.py`

- [ ] **Step 1: Write the failing replay test (in-memory DB)**

```python
# tests/forecast/test_replay.py
import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs, replay_universe
from analytics.store.schema import init_schema
from analytics.store.market_data import upsert_ohlcv


def _seed(conn: duckdb.DuckDBPyConnection, sym: str, n: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2018-01-01", tz="UTC")
    steps = rng.normal(0.0006, 0.012, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    rows = pd.DataFrame(
        {
            "symbol": sym,
            "timeframe": "1d",
            "open_time": [
                int((start + pd.Timedelta(days=i)).timestamp() * 1000)
                for i in range(n)
            ],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        }
    )
    upsert_ohlcv(conn, rows)


def test_load_daily_inputs_returns_zero_funding() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAA", 400, 0)
    closes, fundings = load_daily_inputs(conn, ["AAA", "MISSING"])
    assert "AAA" in closes and "MISSING" not in closes  # empty symbol skipped
    assert (fundings["AAA"] == 0.0).all()  # equities: no funding


def test_replay_universe_runs_book() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    for k, sym in enumerate(("AAA", "BBB", "CCC")):
        _seed(conn, sym, 500, k)
    result = replay_universe(conn, ForecastConfig(), symbols=["AAA", "BBB", "CCC"])
    assert len(result.portfolio_return) > 0
    assert np.isfinite(result.portfolio_return).all()
```

(Confirm the exact `upsert_ohlcv` name/shape against
`analytics/store/market_data.py` before running; adjust the seed helper to match
wifey's OHLCV column contract.)

- [ ] **Step 2: Run — expect FAIL**

Run: `poetry run pytest tests/forecast/test_replay.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `replay.py` (equity-adapted)**

Port `PARENT/analytics/forecast/replay.py` with three edits (spec D2/D4/D5):

```python
"""Read-only DuckDB front door for the EWMAC trend sleeve (equity port).

Loads 1d OHLCV for the breadth universe and runs the forecast book. The only
module in ``analytics/forecast/`` that touches the database; never writes.
Equities have no funding, so the funding leg is always zero (kept in the return
shape for book-signature compatibility).
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.weights import candidate_schemes
from analytics.store.market_data import get_ohlcv
from utils.config_validation import load_research_universe

_FAR_PAST: int = 0
_FAR_FUTURE: int = 9_999_999_999_999


def _universe_symbols(min_history_days: int | None) -> list[str]:
    uni = load_research_universe(min_history_days=min_history_days)
    return uni.stocks()  # active single-name stocks; ETFs excluded from the XS set


def load_daily_inputs(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """Return (closes, fundings) day-indexed Series per symbol.

    Equities carry no funding, so ``fundings[sym]`` is all-zeros aligned to the
    close index (kept so the book signature is unchanged). Symbols with no OHLCV
    are skipped.
    """
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        if bars.empty:
            continue
        idx = pd.to_datetime(bars["open_time"], unit="ms", utc=True).dt.normalize()
        close = pd.Series(bars["close"].to_numpy(dtype=float), index=idx)
        close = close[~close.index.duplicated(keep="last")].sort_index()
        closes[sym] = close
        fundings[sym] = pd.Series(0.0, index=close.index)
    return closes, fundings


def replay_universe(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> ForecastBookResult:
    """Load the universe's 1d inputs and run the forecast book (read-only)."""
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)
    return run_forecast_backtest(closes, fundings, cfg)


def replay_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> dict[str, np.ndarray]:
    """Daily portfolio returns per single-speed sleeve + the combined book."""
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)
    trials: dict[str, np.ndarray] = {}
    for fast, slow, scalar in cfg.speeds:
        single_cfg = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
        result = run_forecast_backtest(closes, fundings, single_cfg)
        trials[f"s{fast}_{slow}"] = result.portfolio_return
    combined = run_forecast_backtest(closes, fundings, cfg)
    trials["combined"] = combined.portfolio_return
    return trials


def replay_weight_schemes(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> dict[str, ForecastBookResult]:
    """Run the universe book once per candidate weight scheme (read-only)."""
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)
    out: dict[str, ForecastBookResult] = {}
    for name, scheme in candidate_schemes(cfg).items():
        scheme_cfg = dataclasses.replace(cfg, weights=scheme.weights)
        out[name] = run_forecast_backtest(closes, fundings, scheme_cfg)
    return out
```

- [ ] **Step 4: Run — expect PASS**

Run: `poetry run pytest tests/forecast/test_replay.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/forecast/replay.py tests/forecast/test_replay.py
git commit -m "feat(forecast): port read-only replay seam (equity universe, no funding)"
```

---

## Task 8: CLI audit + Makefile target

**Files:**

- Create: `tools/forecast_audit.py`
- Modify: `Makefile`
- Test: `tests/forecast/test_audit_cli.py`

- [ ] **Step 1: Write a smoke test for the row builder**

```python
# tests/forecast/test_audit_cli.py
import duckdb

from tools.forecast_audit import build_g2_report_row
from tests.forecast.test_replay import _seed  # reuse the seeder
from analytics.store.schema import init_schema


def test_build_g2_report_row_smoke() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["AAA", "BBB", "CCC", "DDD"]
    for k, s in enumerate(syms):
        _seed(conn, s, 600, k)
    row = build_g2_report_row(conn, "universe @2bps", syms, 2.0)
    assert row["label"] == "universe @2bps"
    assert row["days"] > 0
    for key in ("sharpe", "dsr", "pbo", "boot_lo", "boot_hi", "min_trl"):
        assert key in row
```

- [ ] **Step 2: Run — expect FAIL**

Run: `poetry run pytest tests/forecast/test_audit_cli.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Port `tools/forecast_audit.py`**

Port `PARENT/tools/forecast_audit.py` with: equity majors default
`AAPL,MSFT,NVDA,AMZN,GOOGL,META`; universe from
`load_research_universe().stocks()`; `from analytics.store import DEFAULT_DB_PATH`;
the G2 read-text. Expose `build_g2_report_row(conn, label, symbols, slippage_bps)`
(mirror of the parent's row builder, using `replay_universe` + `replay_trials` +
`evaluate`). Keep the cost-sensitivity sweep `(0, 2, 8, 16) bps` and per-speed
Sharpe table.

- [ ] **Step 4: Add the Makefile target** (recipe lines shown space-indented to
  keep markdownlint happy — in the real Makefile they MUST be TAB-indented)

```makefile
wifey-forecast-audit:
    @echo "📈 G2 audit — EWMAC trend sleeve over the breadth universe (1d)..."
    @PYTHONPATH=. poetry run python tools/forecast_audit.py $(ARGS)
```

Add `wifey-forecast-audit` to the `.PHONY` line.

- [ ] **Step 5: Run — expect PASS**

Run: `poetry run pytest tests/forecast/test_audit_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/forecast_audit.py Makefile tests/forecast/test_audit_cli.py
git commit -m "feat(forecast): G2 audit CLI + make target (equity majors)"
```

---

## Task 9: Wire `__init__.py`, full gate, confirm goldens unmoved

**Files:**

- Modify: `analytics/forecast/__init__.py`

- [ ] **Step 1: Fill the eager re-exports**

```python
"""EWMAC trend sleeve (P2, equity port). Continuous vol-targeted forecasts."""

from analytics.forecast.config import ForecastConfig
from analytics.forecast.report import G2Report, evaluate
from analytics.forecast.replay import (
    load_daily_inputs,
    replay_trials,
    replay_universe,
    replay_weight_schemes,
)

__all__ = [
    "ForecastConfig",
    "G2Report",
    "evaluate",
    "load_daily_inputs",
    "replay_trials",
    "replay_universe",
    "replay_weight_schemes",
]
```

- [ ] **Step 2: Run the mandatory gate**

```bash
make lint-py && make typecheck && make test
```

Expected: ruff clean, mypy strict clean (new files type-complete), full suite
green with the new `tests/forecast/*` added. **Regression goldens MUST be
unmoved** (additive read-only package — no detector/backtest/store-write touched).
If a golden moves, STOP: something non-additive leaked.

- [ ] **Step 3: Commit**

```bash
git add analytics/forecast/__init__.py
git commit -m "feat(forecast): eager re-exports; trend sleeve package complete"
```

---

## Task 10: Run the equity G2 verdict + write the audit note

**Files:**

- Create: `docs/audits/2026-06-17-p2-forecast-trend-g2-equity.md`

- [ ] **Step 1: Run the audit against the live DB**

```bash
make wifey-forecast-audit
```

Capture: the breadth contrast (universe vs majors), the cost-sensitivity sweep,
the per-speed Sharpe table, and the DSR/PBO/boot-CI/MinTRL stamps.

- [ ] **Step 2: Write the verdict note**

Record the equity G2 result honestly (whatever it is — sub-bar/negative is a valid
outcome per the spec's honesty rule). Note the funding=0 / borrow-deferred caveat
(spec D2) and that this is the *equity* answer, distinct from the parent's crypto
+0.36. State the read for PR 2: is the trend sleeve a positive cost-robust core,
and what `corr_to_trend` should the XS sleeve aim to beat.

- [ ] **Step 3: Commit + open PR**

```bash
git add docs/audits/2026-06-17-p2-forecast-trend-g2-equity.md
git commit -m "docs(forecast): equity G2 verdict for the EWMAC trend sleeve"
git push -u origin feat/forecast-trend-sleeve-equity
gh pr create --repo s10023/buibui-wifey-wall-street-bot --base main \
  --title "feat(forecast): EWMAC trend sleeve — equity port (P2, G2 verdict)" \
  --body "<summary + the G2 numbers + goldens-unmoved note>"
```

---

## Self-Review

- **Spec coverage:** D1 (Tasks 1/2/3/6), D2 (Tasks 5/7), D3 (Task 2 defaults),
  D4 (Task 7 `.stocks()`), D5 (Task 7 `min_history_days`), D6 (1d throughout),
  D7 (Task 3 curve slice). G2 acceptance → Task 10. All seven decisions land in a
  task.
- **Placeholders:** none — adapted files show full code; verbatim files cite the
  exact `PARENT/...` path + "no edits"; the one TBD (`upsert_ohlcv` exact
  name/shape in Task 7) is explicitly flagged to confirm against
  `analytics/store/market_data.py` before running, not left silent.
- **Type/name consistency:** `ForecastConfig.annualization_days`, `replay_universe`,
  `replay_trials`, `evaluate`/`G2Report`, `metrics.sharpe(curve, ppy)` are used
  consistently across Tasks 2/3/6/7/8/9.

## Next plan (after this PR lands)

Write `docs/superpowers/plans/<date>-xsmom-sleeve-equity-port.md` (PR 2) once the
forecast foundation is merged and the equity G2 number is in hand — it reuses
`load_daily_inputs` + the primitives and adds `analytics/xsmom/` +
`tools/xsmom_audit.py` for the equity **G3** cross-sectional momentum verdict.
