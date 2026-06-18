# XS-Momentum Sleeve — Equity Port (PR-2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the parent's `analytics/xsmom/` cross-sectional-momentum sleeve (parent PRs #444 + #445) crypto→equity as an additive, read-only package, then compute wifey's own equity **G3 verdict** over the breadth universe.

**Architecture:** A dollar-neutral long-short book over **cross-sectionally demeaned** EWMAC forecasts (relative strength). It reuses PR-1's forecast primitives (`combine_forecasts`, `ew_return_vol`, `load_daily_inputs`, `replay_universe`, the `metrics` curve slice) and wifey's N2 `research_guards` (DSR / PBO / bootstrap-CI / MinTRL). It adds beta-attribution + forward-persistence diagnostics (#445) so the verdict separates market beta from alpha and tests out-of-sample persistence. Writes nothing to the DB → regression goldens stay byte-identical.

**Tech Stack:** Python 3.11, pandas, numpy, DuckDB (read-only), pytest. No new dependencies.

---

## Context for the implementer (read once)

You are in the wifey fork. PR-1 already landed the trend sleeve at
`analytics/forecast/` (merged `4159bcc`). This PR-2 builds directly on it.

**Scope = parent head `e3b2a05`** (= #444 core sleeve **+** #445 beta-neutral /
forward-persistence). The parent-sync pointer advances to `e3b2a05` only after
this lands (handled outside this plan).

**Parent source to copy from** (read-only clone, do **not** edit it):
`/home/kng/repo/buibui-moon-trader-bot` — files `analytics/xsmom/{book,diagnostics,replay,report,__init__}.py`, `tools/xsmom_audit.py`, `tests/xsmom/*`.

**This plan embeds the final wifey code in every task** — you do not need to read
the parent to execute. The parent is the provenance, not a dependency.

### Adaptation decisions (from the campaign spec — already settled)

Spec: `docs/superpowers/specs/2026-06-17-systematic-sleeves-equity-port-design.md`.

- **D1 — Annualization 252, not 365 (load-bearing).** Everything reads
  `cfg.annualization_days` (wifey default already `252.0`). The XS report passes
  `cfg.annualization_days` explicitly into every `metrics.*` call (the parent
  relied on a 365 default). The diagnostics module defaults `ann_days` to `252.0`
  and the audit threads `cfg.annualization_days` into beta/persistence calls.
- **D2 — Funding → 0.** `load_daily_inputs` (PR-1) already returns an all-zero
  funding dict; the book's `funding_cost = leverage * fund` term vanishes. Short
  borrow cost is a deferred v2 refinement (flag it in the verdict caveats).
- **D3 — Carver turnover cost kept.** `|Δleverage| × (fee_pct + slippage_pct)`;
  conservative equity defaults already on `ForecastConfig` (`fee_pct=0.0001`,
  `slippage_pct=0.0002`). The audit sweeps slippage `0/2/8/16 bps`.
- **D4 — Cross-section = `load_research_universe().stocks()`** (active single-name
  stocks; ETFs deliberately excluded — an index is a basket of the names being
  ranked and would distort the demean). The SPY market proxy used in the
  beta-attribution table is loaded **separately** and never enters the demean.
- **D5 — `min_history_days` is an optional knob, default OFF.** The book's
  NaN-skipping active-set aggregation already tolerates staggered histories;
  expose the knob on the replay front door for a clean-cross-section contrast
  (consumes #89's `with_min_history`).
- **D6 — Timeframe = 1d.** No new ingest; breadth universe is backfilled 1d
  full-history.
- **D7 — Curve metrics slice already exists** at `analytics/forecast/metrics.py`
  (PR-1). The XS report imports `from analytics.forecast import metrics`. Do
  **not** import or port `portfolio/`.

### Equity adaptations beyond the spec (this PR)

- **Equity majors** for the audit breadth contrast:
  `AAPL,MSFT,NVDA,AMZN,GOOGL,META` (overridable via `--majors`) — replaces the
  parent's `BTCUSDT,ETHUSDT,SOLUSDT`.
- **Market proxy** for beta attribution: **SPY** (overridable via
  `--market-proxy`) — replaces the parent's `BTCUSDT` index proxy. SPY is loaded
  on its own (it is an ETF, not in `.stocks()`), so it never pollutes the demean.
  Skipped gracefully if absent (mirrors the parent's `if "BTCUSDT" in closes`).
- **DB seeds in tests** drop `taker_buy_volume` (wifey's `upsert_ohlcv` dropped
  that column in T4) and use plain tickers `AAA`/`BBB` (not `AAAUSDT`).

### Honesty rule (non-negotiable)

Trend FAILED on equities (G2 Sharpe ≈ 0). So XS-momentum must stand on its **own
G3 number** — a corr-to-trend / diversification argument is moot because the
combined trend series is ≈ 0 Sharpe. A negative equity G3 is a real, acceptable,
publishable outcome. Port the engine, run it, report whatever the number is.

### Verbatim-port invariants (what NOT to "improve")

- **Causality is the load-bearing invariant.** The book demeans same-day causal
  forecasts, then `.shift(1)` BEFORE sizing. The middle-bar perturbation tests
  (`test_xs_leverage_is_causal_no_lookahead`, the dollar-neutral variant) are the
  look-ahead guard — port them verbatim, do not weaken them.
- The `/10` magnitude divisor in `xs_leverage` mirrors the trend sleeve so legs
  are comparable; the absolute level is governed downstream. Keep it.
- The book aggregates by **SUM** of legs (long-short P&L); the causal 20%-vol
  governor absorbs the scale. Keep `sum(axis=1)`.

---

## File structure (what gets created / modified)

| File | Action | Responsibility |
| --- | --- | --- |
| `analytics/forecast/config.py` | modify | add `xs_dollar_neutral: bool = False` (XS-only; trend ignores it) |
| `analytics/xsmom/__init__.py` | create | eager re-exports of the public surface |
| `analytics/xsmom/book.py` | create | demeaned forecasts, vol-parity leverage (± dollar-neutral), `run_xs_backtest`, `equity_curve` (verbatim port) |
| `analytics/xsmom/diagnostics.py` | create | `equal_weight_market_return`, `beta_attribution`, `subperiod_sharpe` (D1 default 252) |
| `analytics/xsmom/replay.py` | create | read-only DB front door; D4 universe seam + D5 `min_history_days` |
| `analytics/xsmom/report.py` | create | `XSReport` + `evaluate_xs` (D1 + `analytics.forecast.metrics`) |
| `tools/xsmom_audit.py` | create | read-only G3 audit driver (equity majors, SPY proxy, wifey universe) |
| `Makefile` | modify | add `wifey-xsmom-audit` target |
| `tests/xsmom/__init__.py` | create | empty package marker |
| `tests/xsmom/test_book.py` | create | book unit tests (signs, sum-to-zero, causality, dollar-neutral) |
| `tests/xsmom/test_diagnostics.py` | create | beta-attribution + persistence unit tests |
| `tests/xsmom/test_replay.py` | create | DB-seeded replay smoke tests |
| `tests/xsmom/test_report.py` | create | `evaluate_xs` shape + corr/degenerate tests |
| `tests/xsmom/test_audit_cli.py` | create | `build_xs_report_row` smoke test |
| `docs/audits/2026-06-18-p3-xsmom-g3-equity.md` | create | the equity G3 verdict note |

Docs sync (CLAUDE.md, `.claude/context/analytics.md`, README, MEMORY.md) happens
in the final task via `/post-branch`.

---

## Task 0: Branch + pre-flight

**Files:** none (setup only).

- [ ] **Step 1: Branch off `main`**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/xsmom-sleeve-equity
git config --local user.email   # must print ngkhaijian@gmail.com
```

- [ ] **Step 2: Confirm the forecast primitives xsmom imports resolve**

Run:

```bash
PYTHONPATH=. poetry run python -c "
from analytics.forecast.config import ForecastConfig
from analytics.forecast.ewmac import combine_forecasts
from analytics.forecast.vol import ew_return_vol
from analytics.forecast.replay import load_daily_inputs
from analytics.forecast import metrics, replay_universe
from analytics.research_guards import (
    block_bootstrap_ci, cscv_pbo, deflated_sharpe_ratio, min_track_record_length,
)
print('OK', ForecastConfig().annualization_days)
"
```

Expected: `OK 252.0`. If this fails, stop — PR-1's surface is the hard
prerequisite and something is wrong.

---

## Task 1: Add `xs_dollar_neutral` to `ForecastConfig`

The XS book reads `cfg.xs_dollar_neutral`; the trend sleeve never touches it, so
this is additive and leaves the trend G2 path (and the regression goldens)
byte-identical.

**Files:**

- Modify: `analytics/forecast/config.py`

- [ ] **Step 1: Add the field**

In `analytics/forecast/config.py`, change the `weights` line inside the
`ForecastConfig` dataclass from:

```python
    weights: tuple[float, ...] | None = None
```

to:

```python
    weights: tuple[float, ...] | None = None
    xs_dollar_neutral: bool = False  # XS-sleeve only; trend sleeve ignores it
```

- [ ] **Step 2: Confirm the trend path is unaffected**

Run:

```bash
make lint-py && make typecheck
PYTHONPATH=. poetry run pytest tests/forecast/ -q
```

Expected: all forecast tests still PASS (the new field is unused by the trend
book).

- [ ] **Step 3: Confirm regression goldens are byte-identical**

Run: `make test-regression`
Expected: both configs PASS (no golden moves — the field is additive and unread
by any detector/backtest path).

- [ ] **Step 4: Commit**

```bash
git add analytics/forecast/config.py
git commit -m "feat(forecast): add xs_dollar_neutral config field (XS-sleeve seam)"
```

---

## Task 2: `analytics/xsmom/book.py` — the demeaned long-short book

Verbatim port (every import already resolves in wifey; the book reads
`cfg.annualization_days` which is 252 by default, so it is correct for equities
with no code change). Funding is fed zeros by the replay layer (D2).

**Files:**

- Create: `tests/xsmom/__init__.py`
- Create: `tests/xsmom/test_book.py`
- Create: `analytics/xsmom/book.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/xsmom/__init__.py` as an empty file.

- [ ] **Step 2: Write the failing test (full book test file)**

Create `tests/xsmom/test_book.py`:

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import xs_demeaned_forecasts, xs_forecasts


def _closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=500, freq="D")
    # STRONG/WEAK are monotone ramps that saturate the EWMAC cap (+/-20).
    # FLAT is a seeded random walk with tiny positive drift so its forecast is
    # defined (non-NaN) and sub-cap, sitting between STRONG and WEAK.
    rng = np.random.default_rng(42)
    log_returns = rng.normal(0.0005, 0.01, 500)
    flat_rw = 200.0 * np.exp(np.cumsum(log_returns))
    return {
        "STRONG": pd.Series(np.linspace(100.0, 400.0, 500), index=idx),
        "FLAT": pd.Series(flat_rw, index=idx),
        "WEAK": pd.Series(np.linspace(400.0, 100.0, 500), index=idx),
    }


def test_xs_forecasts_aligned_to_union_index() -> None:
    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    assert list(f.columns) == ["STRONG", "FLAT", "WEAK"]
    assert len(f) == 500
    assert f.iloc[-1].notna().all()


def test_demeaned_forecast_rows_sum_to_zero_over_active() -> None:
    closes = _closes()
    g = xs_demeaned_forecasts(closes, ForecastConfig())
    warm = g.dropna(how="any")
    assert len(warm) > 0
    np.testing.assert_allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    last = g.iloc[-1]
    assert last["STRONG"] > last["FLAT"] > last["WEAK"]


def test_demean_over_active_set_with_staggered_history() -> None:
    # Heterogeneous histories: the late instrument is absent/warming on early union
    # days. The demean over the *present* (warmed) instruments must still sum to ~0,
    # and the late instrument must stay NaN — not pulled into the mean as 0. This is
    # exactly what a `fillna(0.0)` on the forecast would have broken.
    idx_full = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_late = pd.date_range("2021-06-01", periods=400, freq="D")
    closes = {
        "A": pd.Series(np.linspace(100.0, 400.0, 500), index=idx_full),
        "B": pd.Series(np.linspace(400.0, 100.0, 500), index=idx_full),
        "C": pd.Series(np.linspace(100.0, 300.0, 400), index=idx_late),
    }
    g = xs_demeaned_forecasts(closes, ForecastConfig())
    # days where A & B are warmed but C is not yet defined (absent or still warming)
    early = g.loc[g["C"].isna() & g[["A", "B"]].notna().all(axis=1)]
    assert len(early) > 0
    np.testing.assert_allclose(early[["A", "B"]].sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    assert early["C"].isna().all()


def test_xs_leverage_sign_long_strong_short_weak() -> None:
    from analytics.xsmom.book import xs_leverage

    lev = xs_leverage(_closes(), ForecastConfig())
    last = lev.iloc[-1]
    # strong uptrend held long, weak downtrend held short
    assert last["STRONG"] > 0.0
    assert last["WEAK"] < 0.0


def test_xs_leverage_is_causal_no_lookahead() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    base = xs_leverage(closes, ForecastConfig())

    # Perturb a MIDDLE bar of ONE instrument. Leverage at index k is sized from
    # demeaned forecasts through k-1, so close[k] must not affect leverage[:k+1]
    # for ANY column (the cross-sectional demean couples instruments).
    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["STRONG"].iloc[k] *= 1.5
    after = xs_leverage(bumped, ForecastConfig())

    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )


def _fundings(closes: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}


def test_run_xs_backtest_long_winner_short_loser_nets_positive() -> None:
    from analytics.xsmom.book import equity_curve, run_xs_backtest

    closes = _closes()
    res = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    assert res.portfolio_return.shape[0] == 500
    assert not np.isnan(res.portfolio_return).any()
    # long the strong / short the weak over a clean cross-section compounds up
    assert equity_curve(res).iloc[-1] > 1.0
    assert res.active_count.max() == 3


def test_run_xs_backtest_short_leg_receives_funding() -> None:
    from analytics.xsmom.book import run_xs_backtest

    closes = _closes()
    pos_fund = {s: pd.Series(0.001, index=c.index) for s, c in closes.items()}
    res = run_xs_backtest(closes, pos_fund, ForecastConfig())
    # WEAK is held short; positive funding on a short is a CREDIT -> net funding
    # cost on that leg is negative over the warmed-up tail.
    weak_net = res.per_instrument_net["WEAK"].dropna()
    no_fund = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    weak_net_nf = no_fund.per_instrument_net["WEAK"].dropna()
    # with positive funding the short leg nets HIGHER than with zero funding
    assert weak_net.sum() > weak_net_nf.sum()


def test_xs_leverage_dollar_neutral_rows_sum_to_zero() -> None:
    from analytics.xsmom.book import xs_leverage

    cfg = ForecastConfig(xs_dollar_neutral=True)
    lev = xs_leverage(_closes(), cfg)
    warm = lev.dropna(how="any")
    assert len(warm) > 0
    # Each active day's positions net to zero (truly dollar-neutral).
    np.testing.assert_allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)


def test_xs_leverage_dollar_neutral_changes_net_exposure() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    off = xs_leverage(closes, ForecastConfig())
    on = xs_leverage(closes, ForecastConfig(xs_dollar_neutral=True))
    # Off path keeps a residual net exposure; the flag removes it.
    off_net = off.dropna(how="any").sum(axis=1).abs().max()
    on_net = on.dropna(how="any").sum(axis=1).abs().max()
    assert off_net > 1e-6
    assert on_net < 1e-9


def test_xs_leverage_dollar_neutral_active_set_with_staggered_history() -> None:
    from analytics.xsmom.book import xs_leverage

    idx_full = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_late = pd.date_range("2021-06-01", periods=400, freq="D")
    closes = {
        "A": pd.Series(np.linspace(100.0, 400.0, 500), index=idx_full),
        "B": pd.Series(np.linspace(400.0, 100.0, 500), index=idx_full),
        "C": pd.Series(np.linspace(100.0, 300.0, 400), index=idx_late),
    }
    lev = xs_leverage(closes, ForecastConfig(xs_dollar_neutral=True))
    early = lev.loc[lev["C"].isna() & lev[["A", "B"]].notna().all(axis=1)]
    assert len(early) > 0
    # Re-center over the active set only; the absent instrument stays NaN.
    np.testing.assert_allclose(early[["A", "B"]].sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    assert early["C"].isna().all()


def test_xs_leverage_dollar_neutral_is_causal_no_lookahead() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    cfg = ForecastConfig(xs_dollar_neutral=True)
    base = xs_leverage(closes, cfg)
    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["STRONG"].iloc[k] *= 1.5
    after = xs_leverage(bumped, cfg)
    # Row k itself is included (`: k + 1`): leverage at k is sized from demeaned
    # forecasts through k-1 (the `.shift(1)`), so close[k] cannot affect it.
    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_book.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsmom'`.

- [ ] **Step 4: Write the implementation (verbatim port)**

Create `analytics/xsmom/book.py`:

```python
"""Per-instrument cross-sectional forecasts, leverage, and portfolio aggregation.

All sizing is causal: the position held during day `d` is sized from information
through day `d-1` only. The cross-sectional demean is a same-day reduction over
causal forecasts; the `.shift(1)` is applied AFTER demeaning, BEFORE sizing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.ewmac import combine_forecasts
from analytics.forecast.vol import ew_return_vol


def _union_index(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    return union.sort_values()


def xs_forecasts(closes: dict[str, pd.Series], cfg: ForecastConfig) -> pd.DataFrame:
    """Raw combined EWMAC forecasts per instrument, aligned to the union daily index.

    Columns = symbols, index = sorted union of all instrument dates. NaN where an
    instrument has not-yet-warmed-up or where return-vol is undefined. NaN warmup bars
    are intentional: the cross-sectional demean (`xs_demeaned_forecasts`) skips NaN via
    `mean(axis=1)`, so only warmed-up instruments contribute to the mean.
    Causal: each column is `combine_forecasts(...)`, which uses only closes through
    each day.
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        f = combine_forecasts(
            close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
        )
        cols[sym] = f.reindex(union)
    return pd.DataFrame(cols, index=union)


def xs_demeaned_forecasts(
    closes: dict[str, pd.Series], cfg: ForecastConfig
) -> pd.DataFrame:
    """Cross-sectionally demeaned forecasts (relative strength).

    `g_i(d) = f_i(d) - mean_{j in active(d)} f_j(d)`; the row mean skips NaN so it
    is taken over the active instruments only. Each active row sums to ~0
    (dollar-neutral). Not yet shifted — see `xs_leverage`.
    """
    f = xs_forecasts(closes, cfg)
    return f.sub(f.mean(axis=1), axis=0)


def xs_leverage(closes: dict[str, pd.Series], cfg: ForecastConfig) -> pd.DataFrame:
    """Causal cross-sectional (demeaned) vol-parity leverage matrix.

    Demean the forecast across active instruments (dollar-neutral), shift one day
    (position on day `d` uses info through `d-1`), then vol-target each leg:
    `leverage_i = (g_i_shifted / 10) * (vol_target / vol_ann_i)`. The `/10` mirrors
    the trend sleeve so magnitudes are comparable; the absolute level is governed
    downstream. Columns = symbols, index = union daily index. When
    ``cfg.xs_dollar_neutral`` is set, the matrix is re-centered so each day's
    active leverage sums to zero (dollar-neutral).
    """
    demeaned = xs_demeaned_forecasts(closes, cfg)
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
        # Subtract the per-day active-set mean leverage so each day's positions
        # net to zero (dollar-neutral). Same skipna idiom as the forecast demean:
        # NaN cells stay NaN; a same-day op on already-shifted leverage adds no
        # look-ahead.
        lev_df = lev_df.sub(lev_df.mean(axis=1), axis=0)
    return lev_df


@dataclass(frozen=True)
class XSBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-governor (NaN-free; warm-up = 0.0)
    pre_governor_return: np.ndarray
    governor: np.ndarray  # NaN for the first gov_window warm-up bars
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]


def run_xs_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> XSBookResult:
    """Causal dollar-neutral long-short book over the demeaned forecast.

    Per instrument: gross = leverage * return; honest costs = turnover
    `|Δlev|*(fee+slip)` + funding `leverage*funding` (shorts receive funding).
    Aggregate = SUM of legs (long-short portfolio P&L; the level is set by the
    causal 20%-vol governor, so sum-vs-mean is only a scale it absorbs).
    """
    leverage = xs_leverage(closes, cfg)
    union = pd.DatetimeIndex(leverage.index)
    cost = cfg.fee_pct + cfg.slippage_pct

    per_net: dict[str, pd.Series] = {}
    net_cols: list[pd.Series] = []
    for sym, close in closes.items():
        lev = leverage[sym]
        r = close.pct_change().reindex(union)
        gross = lev * r
        turnover = (lev - lev.shift(1).fillna(0.0)).abs() * cost
        fund = (
            fundings.get(sym, pd.Series(0.0, index=close.index))
            .reindex(union)
            .fillna(0.0)
        )
        funding_cost = lev * fund
        net = gross - turnover - funding_cost
        per_net[sym] = net
        net_cols.append(net)

    net_mat = pd.concat(net_cols, axis=1)
    active = net_mat.notna().sum(axis=1)
    pre = net_mat.sum(axis=1)  # all-NaN warm-up rows -> 0.0 (skipna)

    ann = np.sqrt(cfg.annualization_days)
    trailing_vol = (
        pre.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().shift(1) * ann
    )
    g = (cfg.vol_target_annual / trailing_vol).clip(cfg.g_min, cfg.g_max)
    port = g.fillna(0.0) * pre

    return XSBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_governor_return=pre.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        active_count=active.to_numpy(dtype=np.int64),
        per_instrument_net=per_net,
    )


def equity_curve(result: XSBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0) for the curve-metrics slice."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_book.py -q`
Expected: PASS (all 11 tests).

- [ ] **Step 6: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add analytics/xsmom/book.py tests/xsmom/__init__.py tests/xsmom/test_book.py
git commit -m "feat(xsmom): demeaned-EWMAC long-short book (equity port)"
```

---

## Task 3: `analytics/xsmom/diagnostics.py` — beta attribution + persistence

Pure numpy/pandas + stdlib (no DB, no forecast imports). The only adaptation is
**D1**: the `ann_days` defaults become `252.0`. The ported tests pass `ann_days`
explicitly (pure-math, annualization-agnostic), so they pin the math regardless
of the default.

**Files:**

- Create: `tests/xsmom/test_diagnostics.py`
- Create: `analytics/xsmom/diagnostics.py`

- [ ] **Step 1: Write the failing test (full diagnostics test file)**

Create `tests/xsmom/test_diagnostics.py`:

```python
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest


def test_equal_weight_market_return_active_set_mean() -> None:
    from analytics.xsmom.diagnostics import equal_weight_market_return

    idx = pd.date_range("2021-01-01", periods=4, freq="D")
    closes = {
        "A": pd.Series([100.0, 110.0, 121.0, 133.1], index=idx),  # +10%/day
        "B": pd.Series([100.0, 100.0, 100.0, 100.0], index=idx),  # 0%/day
    }
    mkt = equal_weight_market_return(closes)
    # day 0 is NaN (pct_change); day 1 = mean(+0.10, 0.0) = 0.05
    assert math.isnan(mkt.iloc[0])
    assert mkt.iloc[1] == pytest.approx(0.05)


def test_equal_weight_market_return_skips_absent_instrument() -> None:
    from analytics.xsmom.diagnostics import equal_weight_market_return

    idx_full = pd.date_range("2021-01-01", periods=4, freq="D")
    idx_late = pd.date_range("2021-01-03", periods=2, freq="D")
    closes = {
        "A": pd.Series([100.0, 110.0, 121.0, 133.1], index=idx_full),  # +10%/day
        "C": pd.Series([100.0, 200.0], index=idx_late),  # present only days 2-3
    }
    mkt = equal_weight_market_return(closes)
    # day 1: only A present -> 0.10; day 3: A +10% and C +100% -> mean 0.55
    assert mkt.loc[idx_full[1]] == pytest.approx(0.10)
    assert mkt.loc[idx_full[3]] == pytest.approx(0.55)


def test_beta_attribution_recovers_known_alpha_beta() -> None:
    from analytics.xsmom.diagnostics import beta_attribution

    rng = np.random.default_rng(0)
    n = 2000
    mkt = rng.normal(0.0, 0.02, n)
    noise = rng.normal(0.0, 0.001, n)
    port = 0.0003 + 1.4 * mkt + noise
    ba = beta_attribution(port, mkt, ann_days=365.0)
    assert abs(ba.beta - 1.4) < 0.05
    assert abs(ba.alpha_annual - 0.0003 * 365.0) < 0.02
    assert ba.r_squared > 0.95
    assert ba.alpha_tstat > 10.0  # true t-stat ~13.3 at SNR 20:1, n=2000
    # hedged stream = alpha + residual: positive mean, small vol -> high Sharpe
    assert ba.beta_hedged_sharpe > 1.0


def test_beta_attribution_degenerate_market_is_safe() -> None:
    from analytics.xsmom.diagnostics import beta_attribution

    port = np.array([0.01, -0.02, 0.03, 0.0, 0.015])
    mkt = np.zeros(5)  # zero-variance market
    ba = beta_attribution(port, mkt, ann_days=365.0)
    assert ba.beta == 0.0
    assert ba.r_squared == 0.0
    # hedged == port when there is no market factor to remove
    assert abs(ba.alpha_annual - float(np.mean(port)) * 365.0) < 1e-9


def test_subperiod_sharpe_by_year_and_trailing() -> None:
    from analytics.xsmom.diagnostics import subperiod_sharpe

    idx = pd.date_range("2021-01-01", "2022-12-31", freq="D")
    rng = np.random.default_rng(1)
    # 2021 strongly positive drift, 2022 flat-ish — distinct per-year Sharpe.
    r = np.where(
        idx.year.to_numpy() == 2021,
        rng.normal(0.002, 0.01, len(idx)),
        rng.normal(0.0, 0.01, len(idx)),
    )
    rep = subperiod_sharpe(r, idx, ann_days=365.0)
    assert set(rep.by_year.keys()) == {2021, 2022}
    assert rep.by_year[2021] > rep.by_year[2022]
    assert rep.n_obs == len(idx)
    # trailing 1y window pulls only from 2022 -> close to the 2022 figure
    assert rep.trailing_1y < rep.by_year[2021]
    # trailing 2y covers both years -> Sharpe strictly between the per-year values
    assert rep.by_year[2022] < rep.trailing_2y < rep.by_year[2021]


def test_subperiod_sharpe_degenerate_slices_are_zero_not_nan() -> None:
    from analytics.xsmom.diagnostics import subperiod_sharpe

    idx = pd.date_range("2021-01-01", periods=1, freq="D")
    rep = subperiod_sharpe(np.array([0.01]), idx, ann_days=365.0)
    assert rep.by_year[2021] == 0.0  # single obs -> 0.0, not NaN
    assert rep.trailing_1y == 0.0
    assert rep.trailing_2y == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_diagnostics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsmom.diagnostics'`.

- [ ] **Step 3: Write the implementation (D1: defaults → 252.0)**

Create `analytics/xsmom/diagnostics.py`:

```python
"""Pure beta-attribution + forward-persistence diagnostics for the XS sleeve.

No DB/IO; numpy + pandas + stdlib only. Consumed by ``tools/xsmom_audit.py`` to
quantify how much of the headline Sharpe is market beta vs alpha, and whether the
edge persists across calendar years and recent windows. Equity port: ``ann_days``
defaults to 252 NYSE sessions (callers thread ``cfg.annualization_days``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd


def equal_weight_market_return(closes: dict[str, pd.Series]) -> pd.Series:
    """Active-set mean of per-instrument daily returns (the 'alt market').

    Aligns each instrument's ``pct_change()`` to the sorted union daily index and
    averages across the present (non-NaN) instruments each day (skipna). Day-0 of
    each instrument is NaN by construction.
    """
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    union = union.sort_values()
    rets = [c.pct_change().reindex(union) for c in closes.values()]
    mat = pd.concat(rets, axis=1)
    return mat.mean(axis=1)


@dataclass(frozen=True)
class BetaAttribution:
    alpha_annual: float
    beta: float
    alpha_tstat: float
    beta_hedged_sharpe: float
    r_squared: float


def _ann_sharpe(r: npt.NDArray[np.float64], ann_days: float) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd) * math.sqrt(ann_days)


def beta_attribution(
    port_ret: npt.ArrayLike, mkt_ret: npt.ArrayLike, ann_days: float = 252.0
) -> BetaAttribution:
    """Full-sample OLS ``r_port = alpha + beta * r_mkt + eps``.

    Reports the annualized *beta-hedged* Sharpe of ``r_port - beta * r_mkt`` (=
    alpha + residual), NOT the zero-mean OLS residual. Aligns on the common tail,
    drops non-finite rows, and is degenerate-safe (zero-variance market -> beta
    0.0, hedged == port).

    Caller contract: ``port_ret`` and ``mkt_ret`` must already be positionally
    aligned (same dates, same order) — pass arrays built on the *same* index
    (e.g. the XS book's union daily index). The common-tail slice only repairs a
    pure length difference, not a date misalignment. ``alpha_tstat`` is 0.0 when
    the residual variance is numerically indistinguishable from zero (a perfect
    fit), not a true zero-significance signal.
    """
    x = np.asarray(mkt_ret, dtype=np.float64)
    y = np.asarray(port_ret, dtype=np.float64)
    n = min(len(x), len(y))
    x, y = x[-n:], y[-n:]
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]

    if len(x) < 2 or float(np.std(x, ddof=1)) < 1e-12:
        return BetaAttribution(
            alpha_annual=(float(np.mean(y)) * ann_days) if len(y) else 0.0,
            beta=0.0,
            alpha_tstat=0.0,
            beta_hedged_sharpe=_ann_sharpe(y, ann_days),
            r_squared=0.0,
        )

    design = np.column_stack([np.ones(len(x)), x])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    alpha_d, beta = float(coef[0]), float(coef[1])
    resid = y - design @ coef

    dof = len(x) - 2
    sigma2 = float(resid @ resid) / dof if dof > 0 else 0.0
    xtx_inv = np.linalg.inv(design.T @ design)
    se_alpha = math.sqrt(sigma2 * float(xtx_inv[0, 0])) if sigma2 > 0 else 0.0
    tstat = alpha_d / se_alpha if se_alpha > 1e-15 else 0.0

    hedged = y - beta * x
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 1e-15 else 0.0

    return BetaAttribution(
        alpha_annual=alpha_d * ann_days,
        beta=beta,
        alpha_tstat=tstat,
        beta_hedged_sharpe=_ann_sharpe(hedged, ann_days),
        r_squared=r2,
    )


@dataclass(frozen=True)
class PersistenceReport:
    by_year: dict[int, float]
    trailing_2y: float
    trailing_1y: float
    n_obs: int


def subperiod_sharpe(
    port_ret: npt.ArrayLike,
    index: pd.DatetimeIndex,
    ann_days: float = 252.0,
) -> PersistenceReport:
    """Annualized Sharpe per calendar year + trailing 2y / 1y windows.

    Any sub-slice with < 2 observations or ~0 std returns 0.0 (never NaN),
    assuming ``port_ret`` itself contains no NaN values (the XS book's
    ``portfolio_return`` is NaN-free by construction).
    """
    dt_index = pd.DatetimeIndex(index)
    s = pd.Series(np.asarray(port_ret, dtype=np.float64), index=dt_index)
    by_year = {
        int(year): _ann_sharpe(np.asarray(grp, dtype=np.float64), ann_days)
        for year, grp in s.groupby(dt_index.year)
    }
    last = dt_index.max()
    t2 = s[dt_index > last - pd.Timedelta(days=730)]
    t1 = s[dt_index > last - pd.Timedelta(days=365)]
    return PersistenceReport(
        by_year=by_year,
        trailing_2y=_ann_sharpe(np.asarray(t2, dtype=np.float64), ann_days),
        trailing_1y=_ann_sharpe(np.asarray(t1, dtype=np.float64), ann_days),
        n_obs=len(s),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_diagnostics.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/xsmom/diagnostics.py tests/xsmom/test_diagnostics.py
git commit -m "feat(xsmom): beta-attribution + forward-persistence diagnostics (equity port)"
```

---

## Task 4: `analytics/xsmom/replay.py` — read-only DB front door

D4 (universe seam = `load_research_universe().stocks()`) + D5 (`min_history_days`
knob). Reuses PR-1's `load_daily_inputs`. Mirrors `analytics/forecast/replay.py`
exactly (same `_universe_symbols`, same keyword-only `min_history_days`).

**Files:**

- Create: `tests/xsmom/test_replay.py`
- Create: `analytics/xsmom/replay.py`

- [ ] **Step 1: Write the failing test (DB-seeded; no `taker_buy_volume`)**

Create `tests/xsmom/test_replay.py`:

```python
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.replay import replay_xs, replay_xs_trials

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, slope: float) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + slope * i,
            "high": 101.0 + slope * i,
            "low": 99.0 + slope * i,
            "close": 100.0 + slope * i,
            "volume": 1000.0,
        }
        for i in range(320)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_replay_xs_returns_book_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAA", 1.0)
    _seed(conn, "BBB", -0.5)
    res = replay_xs(conn, ForecastConfig(), symbols=["AAA", "BBB"])
    assert isinstance(res, XSBookResult)
    assert res.portfolio_return.shape[0] > 0
    assert not np.isnan(res.portfolio_return).any()


def test_replay_xs_trials_has_per_speed_plus_combined() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAA", 1.0)
    _seed(conn, "BBB", -0.5)
    trials = replay_xs_trials(conn, ForecastConfig(), symbols=["AAA", "BBB"])
    assert set(trials) == {"s8_32", "s16_64", "s32_128", "s64_256", "combined"}
    for v in trials.values():
        assert isinstance(v, np.ndarray)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_replay.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsmom.replay'`.

- [ ] **Step 3: Write the implementation (D4 + D5)**

Create `analytics/xsmom/replay.py`:

```python
"""Read-only DuckDB front door for the cross-sectional momentum sleeve (equity).

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + all-zero equity
funding) and runs the XS book over the breadth universe's active single-name
stocks (``.stocks()`` — ETFs excluded from the cross-section). The only module in
``analytics/xsmom/`` that touches the DB; never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.xsmom.book import XSBookResult, run_xs_backtest
from utils.config_validation import load_research_universe


def _universe_symbols(min_history_days: int | None) -> list[str]:
    uni = load_research_universe(min_history_days=min_history_days)
    return uni.stocks()  # active single-name stocks; ETFs excluded from the XS set


def replay_xs(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> XSBookResult:
    """Load the universe's 1d inputs and run the XS book (read-only)."""
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)
    return run_xs_backtest(closes, fundings, cfg)


def replay_xs_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
    *,
    min_history_days: int | None = None,
) -> dict[str, np.ndarray]:
    """Daily XS portfolio returns per single-speed sleeve + the combined book.

    The honest multiple-testing family for DSR/PBO. Keys:
    ``s{fast}_{slow}`` per speed in ``cfg.speeds``, plus ``combined``.
    """
    syms = symbols if symbols is not None else _universe_symbols(min_history_days)
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for fast, slow, scalar in cfg.speeds:
        single_cfg = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
        result = run_xs_backtest(closes, fundings, single_cfg)
        trials[f"s{fast}_{slow}"] = result.portfolio_return
    combined = run_xs_backtest(closes, fundings, cfg)
    trials["combined"] = combined.portfolio_return
    return trials
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_replay.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/xsmom/replay.py tests/xsmom/test_replay.py
git commit -m "feat(xsmom): read-only DB replay front door (universe seam + min_history)"
```

---

## Task 5: `analytics/xsmom/report.py` — XS verdict assembler

D1 (thread `cfg.annualization_days` into every metrics call) + D7 (`from
analytics.forecast import metrics`). Adds `corr_to_trend` / `trend_sharpe` for the
diversification read.

**Files:**

- Create: `tests/xsmom/test_report.py`
- Create: `analytics/xsmom/report.py`

- [ ] **Step 1: Write the failing test (full report test file)**

Create `tests/xsmom/test_report.py`:

```python
"""Tests for analytics.xsmom.report — XSReport + evaluate_xs."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.report import XSReport, evaluate_xs


def _result(returns: np.ndarray) -> XSBookResult:
    idx = pd.date_range("2021-01-01", periods=len(returns), freq="D")
    return XSBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 2, dtype=np.int64),
        per_instrument_net={"AAA": pd.Series(returns, index=idx)},
    )


def test_report_shape_and_corr_to_trend() -> None:
    rng = np.random.default_rng(0)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    trials = {"combined": r, "s8_32": r * 1.1, "s64_256": r * 0.2}
    trend = 0.0008 + 0.01 * rng.standard_normal(800)
    rep = evaluate_xs(res, ForecastConfig(), trial_returns=trials, trend_returns=trend)
    assert isinstance(rep, XSReport)
    assert rep.n_obs == 800
    assert rep.boot_lo <= rep.sharpe_annual <= rep.boot_hi
    assert 0.0 <= rep.pbo <= 1.0
    assert -1.0 <= rep.corr_to_trend <= 1.0
    assert rep.trend_sharpe != 0.0


def test_corr_to_trend_identical_is_one() -> None:
    rng = np.random.default_rng(1)
    r = 0.001 + 0.01 * rng.standard_normal(400)
    res = _result(r)
    rep = evaluate_xs(
        res, ForecastConfig(), trial_returns={"combined": r}, trend_returns=r
    )
    assert rep.corr_to_trend > 0.99
    # single-trial family -> PBO guard returns NaN
    assert math.isnan(rep.pbo)


def test_corr_to_trend_anticorrelated_is_negative() -> None:
    rng = np.random.default_rng(2)
    r = 0.001 + 0.01 * rng.standard_normal(400)
    res = _result(r)
    rep = evaluate_xs(
        res, ForecastConfig(), trial_returns={"combined": r}, trend_returns=-r
    )
    assert rep.corr_to_trend < -0.99
    assert math.isnan(rep.pbo)


def test_flat_returns_degenerate_to_zero() -> None:
    res = _result(np.zeros(500))
    rep = evaluate_xs(
        res,
        ForecastConfig(),
        trial_returns={"combined": np.zeros(500)},
        trend_returns=np.zeros(500),
    )
    assert rep.sharpe_annual == 0.0
    assert rep.corr_to_trend == 0.0
    # sr_d == 0 degenerate branch
    assert rep.dsr == 0.0
    assert math.isinf(rep.min_trl)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_report.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.xsmom.report'`.

- [ ] **Step 3: Write the implementation (D1 + metrics import)**

Create `analytics/xsmom/report.py`:

```python
"""Assemble the cross-sectional momentum verdict: headline metrics + guards.

Pure over an XSBookResult plus the candidate trials' daily returns (the honest
multiple-testing family for DSR/PBO) and the trend sleeve's daily returns (for
the diversification read). Mirrors ``analytics.forecast.report`` and adds
``corr_to_trend`` / ``trend_sharpe``. Annualization (252 NYSE sessions) is
threaded from ``cfg.annualization_days`` into every metric call (D1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast import metrics
from analytics.forecast.config import ForecastConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
from analytics.xsmom.book import XSBookResult, equity_curve


def _per_period_sharpe(r: npt.NDArray[np.float64]) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd)


def _ann_sharpe(r: npt.NDArray[np.float64], ann: float) -> float:
    return _per_period_sharpe(r) * ann


def _aligned_corr(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
    """Pearson corr over the common tail, excluding joint dead warm-up (0, 0)."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    x = np.asarray(a[-n:], dtype=np.float64)
    y = np.asarray(b[-n:], dtype=np.float64)
    live = ~((x == 0.0) & (y == 0.0))
    x, y = x[live], y[live]
    if (
        len(x) < 2
        or float(np.std(x, ddof=1)) < 1e-12
        or float(np.std(y, ddof=1)) < 1e-12
    ):
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


@dataclass(frozen=True)
class XSReport:
    """Headline metrics + guards + the diversification read for the XS verdict."""

    sharpe_annual: float
    sortino_annual: float
    max_dd: float
    calmar: float
    annual_return: float
    annual_vol: float
    n_obs: int
    dsr: float
    pbo: float
    boot_lo: float
    boot_hi: float
    min_trl: float
    corr_to_trend: float
    trend_sharpe: float


def evaluate_xs(
    result: XSBookResult,
    cfg: ForecastConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    trend_returns: npt.NDArray[np.float64],
) -> XSReport:
    """Compute all XS metrics + research-guard stamps + trend diversification.

    ``trial_returns`` is the honest multiple-testing family (per-speed XS sleeves
    + combined) — the same set ``replay_xs_trials`` produces. ``trend_returns`` is
    the trend sleeve's daily portfolio returns on the same universe/window.
    """
    r = result.portfolio_return
    curve = equity_curve(result)
    ann = math.sqrt(cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(v) for v in trial_returns.values()]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack([v[-min_len:] for v in trial_returns.values()])
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")

    if sr_d != 0.0:

        def _stat_fn(x: npt.NDArray[np.float64]) -> float:
            return _ann_sharpe(x, ann)

        boot = block_bootstrap_ci(r, stat_fn=_stat_fn, seed=7)
        boot_lo, boot_hi = boot.lo, boot.hi
        dsr = deflated_sharpe_ratio(sr_d, len(r), trial_srs=trial_srs)
        min_trl = min_track_record_length(sr_d, target_sr=1.0 / ann, confidence=0.95)
    else:
        boot_lo = boot_hi = dsr = 0.0
        min_trl = float("inf")

    if len(trend_returns) >= 2:
        trend_curve = (1.0 + pd.Series(trend_returns)).cumprod()
        trend_sharpe = metrics.sharpe(trend_curve, cfg.annualization_days)
    else:
        trend_sharpe = 0.0
    corr_to_trend = _aligned_corr(r, trend_returns)

    return XSReport(
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
        corr_to_trend=corr_to_trend,
        trend_sharpe=trend_sharpe,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_report.py -q`
Expected: PASS (4 tests).

> Note: `boot_lo <= sharpe_annual <= boot_hi` holds because both sides use the
> same `sqrt(252)` annualization (the bootstrap `_stat_fn` and `metrics.sharpe`
> both read `cfg.annualization_days`). If you see a bracket failure, you broke D1
> consistency — do not "fix" the test.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/xsmom/report.py tests/xsmom/test_report.py
git commit -m "feat(xsmom): G3 verdict assembler (corr_to_trend + guards, 252 annualization)"
```

---

## Task 6: `analytics/xsmom/__init__.py` — public surface

Verbatim re-export port (every name now exists from Tasks 2–5).

**Files:**

- Create: `analytics/xsmom/__init__.py`

- [ ] **Step 1: Write the package init**

Create `analytics/xsmom/__init__.py`:

```python
"""Cross-sectional momentum sleeve (P3) — demeaned EWMAC relative-strength book."""

from analytics.xsmom.book import (
    XSBookResult,
    equity_curve,
    run_xs_backtest,
    xs_demeaned_forecasts,
    xs_forecasts,
    xs_leverage,
)
from analytics.xsmom.diagnostics import (
    BetaAttribution,
    PersistenceReport,
    beta_attribution,
    equal_weight_market_return,
    subperiod_sharpe,
)
from analytics.xsmom.replay import replay_xs, replay_xs_trials
from analytics.xsmom.report import XSReport, evaluate_xs

__all__ = [
    "BetaAttribution",
    "PersistenceReport",
    "XSBookResult",
    "XSReport",
    "beta_attribution",
    "equal_weight_market_return",
    "equity_curve",
    "evaluate_xs",
    "replay_xs",
    "replay_xs_trials",
    "run_xs_backtest",
    "subperiod_sharpe",
    "xs_demeaned_forecasts",
    "xs_forecasts",
    "xs_leverage",
]
```

- [ ] **Step 2: Verify the surface imports**

Run:

```bash
PYTHONPATH=. poetry run python -c "
import analytics.xsmom as x
print(sorted(x.__all__))
"
```

Expected: prints the sorted `__all__` list with no ImportError.

- [ ] **Step 3: Lint + typecheck + run the whole xsmom suite**

```bash
make lint-py && make typecheck
PYTHONPATH=. poetry run pytest tests/xsmom/ -q
```

Expected: all xsmom tests PASS (book + diagnostics + replay + report).

- [ ] **Step 4: Commit**

```bash
git add analytics/xsmom/__init__.py
git commit -m "feat(xsmom): package __init__ eager re-exports"
```

---

## Task 7: `tools/xsmom_audit.py` — read-only G3 audit driver

Adaptations: equity majors (`AAPL,MSFT,NVDA,AMZN,GOOGL,META`), **SPY** market
proxy loaded separately (replaces BTC; never enters the demean), wifey universe
(`load_research_universe().stocks()`), wifey DB path (`from analytics.store import
DEFAULT_DB_PATH`), and D1 (`ann_days=cfg.annualization_days` threaded into beta /
persistence). `build_xs_report_row` itself is verbatim (it threads cfg through
`evaluate_xs`).

**Files:**

- Create: `tests/xsmom/test_audit_cli.py`
- Create: `tools/xsmom_audit.py`

- [ ] **Step 1: Write the failing test (DB-seeded; no `taker_buy_volume`)**

Create `tests/xsmom/test_audit_cli.py`:

```python
from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from tools.xsmom_audit import build_xs_report_row

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, slope: float) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + slope * i,
            "high": 101.0 + slope * i,
            "low": 99.0 + slope * i,
            "close": 100.0 + slope * i,
            "volume": 1000.0,
        }
        for i in range(320)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_build_xs_report_row_returns_dict() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAA", 1.0)
    _seed(conn, "BBB", -0.5)
    row = build_xs_report_row(
        conn, "label", symbols=["AAA", "BBB"], slippage_bps=2.0
    )
    assert row["label"] == "label"
    for col in ("sharpe", "max_dd", "pbo", "dsr", "corr_to_trend", "trend_sharpe"):
        assert col in row
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_audit_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.xsmom_audit'`.

- [ ] **Step 3: Write the implementation (equity adaptations)**

Create `tools/xsmom_audit.py`:

```python
"""Cross-sectional momentum sleeve audit (P3, equity) — read-only verdict.

Replays the demeaned-EWMAC relative-strength book across the breadth universe (1d)
and prints: a breadth contrast (universe vs majors-only), a dollar-neutral gate, a
beta-attribution table (alt-market + SPY proxy), a forward-persistence table, a
cost-sensitivity sweep, the per-speed XS Sharpes, and the diversification read
(correlation to the trend sleeve) — each with DSR/PBO/bootstrap-CI/MinTRL stamps.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_audit.py
    PYTHONPATH=. poetry run python tools/xsmom_audit.py --majors AAPL,MSFT,NVDA
    PYTHONPATH=. poetry run python tools/xsmom_audit.py --market-proxy QQQ
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast import ForecastConfig, replay_universe
from analytics.forecast.replay import load_daily_inputs
from analytics.store import DEFAULT_DB_PATH
from analytics.xsmom import (
    beta_attribution,
    equal_weight_market_return,
    evaluate_xs,
    replay_xs,
    replay_xs_trials,
    run_xs_backtest,
    subperiod_sharpe,
)
from utils.config_validation import load_research_universe

_MAJORS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
_MARKET_PROXY = "SPY"


def build_xs_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
    xs_dollar_neutral: bool = False,
) -> dict[str, object]:
    cfg = dataclasses.replace(
        ForecastConfig(),
        slippage_pct=slippage_bps / 10_000.0,
        xs_dollar_neutral=xs_dollar_neutral,
    )
    result = replay_xs(conn, cfg, symbols=symbols)
    trials = replay_xs_trials(conn, cfg, symbols=symbols)
    trend = replay_universe(conn, cfg, symbols=symbols).portfolio_return
    rep = evaluate_xs(result, cfg, trial_returns=trials, trend_returns=trend)
    return {
        "label": label,
        "n_inst": len(result.per_instrument_net),
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "sortino": rep.sortino_annual,
        "max_dd": rep.max_dd,
        "ann_ret": rep.annual_return,
        "ann_vol": rep.annual_vol,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
        "corr_to_trend": rep.corr_to_trend,
        "trend_sharpe": rep.trend_sharpe,
    }


def _per_speed_xs_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ForecastConfig()
    ann = math.sqrt(cfg.annualization_days)
    trials = replay_xs_trials(conn, cfg, symbols=symbols)
    rows = []
    for name, r in trials.items():
        sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        sr = (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0
        rows.append({"trial": name, "sharpe": sr})
    return pd.DataFrame(rows)


def _beta_attribution_table(
    conn: duckdb.DuckDBPyConnection, symbols: list[str], market_proxy: str
) -> pd.DataFrame:
    closes, fundings = load_daily_inputs(conn, symbols)
    mkt = equal_weight_market_return(closes)
    union = mkt.index
    mkt_arr: npt.NDArray[np.float64] = np.asarray(mkt.to_numpy(), dtype=np.float64)
    # Index-ETF market proxy (SPY by default), loaded separately. It is an ETF, not
    # in the .stocks() cross-section, so it never enters the demean.
    proxy_closes, _ = load_daily_inputs(conn, [market_proxy])
    proxy_arr: npt.NDArray[np.float64] | None = (
        np.asarray(
            proxy_closes[market_proxy].pct_change().reindex(union).to_numpy(),
            dtype=np.float64,
        )
        if market_proxy in proxy_closes
        else None
    )
    rows: list[dict[str, object]] = []
    for label, neutral in (("original", False), ("dollar-neutral", True)):
        cfg = dataclasses.replace(
            ForecastConfig(), slippage_pct=0.0002, xs_dollar_neutral=neutral
        )
        r = run_xs_backtest(closes, fundings, cfg).portfolio_return
        proxies: list[tuple[str, npt.NDArray[np.float64]]] = [("eq-wt-mkt", mkt_arr)]
        if proxy_arr is not None:
            proxies.append((market_proxy, proxy_arr))
        for proxy_name, proxy in proxies:
            ba = beta_attribution(r, proxy, ann_days=cfg.annualization_days)
            rows.append(
                {
                    "book": label,
                    "proxy": proxy_name,
                    "alpha_ann": ba.alpha_annual,
                    "beta": ba.beta,
                    "alpha_t": ba.alpha_tstat,
                    "hedged_sharpe": ba.beta_hedged_sharpe,
                    "r2": ba.r_squared,
                }
            )
    return pd.DataFrame(rows)


def _persistence_table(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    closes, fundings = load_daily_inputs(conn, symbols)
    cfg = dataclasses.replace(
        ForecastConfig(), slippage_pct=0.0002, xs_dollar_neutral=True
    )
    res = run_xs_backtest(closes, fundings, cfg)
    pr = subperiod_sharpe(
        res.portfolio_return, res.daily_index, ann_days=cfg.annualization_days
    )
    rows: list[dict[str, object]] = [
        {"period": str(year), "sharpe": sr} for year, sr in sorted(pr.by_year.items())
    ]
    rows.append({"period": "trailing_2y", "sharpe": pr.trailing_2y})
    rows.append({"period": "trailing_1y", "sharpe": pr.trailing_1y})
    return pd.DataFrame(rows)


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--majors",
        type=str,
        default=",".join(_MAJORS),
        help="comma-separated majors-only contrast set",
    )
    parser.add_argument(
        "--market-proxy",
        type=str,
        default=_MARKET_PROXY,
        help="index-ETF beta proxy (loaded separately; excluded from the demean)",
    )
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    universe = load_research_universe().stocks()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]
    proxy = args.market_proxy.strip().upper()

    rows = [
        build_xs_report_row(conn, "universe @2bps", universe, 2.0),
        build_xs_report_row(conn, "majors @2bps", majors, 2.0),
    ]
    _print_df("Gate G3 — XS breadth contrast", pd.DataFrame(rows))

    neutral_rows = [
        build_xs_report_row(conn, "universe original @2bps", universe, 2.0, False),
        build_xs_report_row(conn, "universe neutral @2bps", universe, 2.0, True),
    ]
    _print_df("Dollar-neutral gate (original vs neutral)", pd.DataFrame(neutral_rows))
    _print_df(
        "Beta attribution (universe @2bps)",
        _beta_attribution_table(conn, universe, proxy),
    )
    _print_df(
        "Forward persistence (neutral, universe @2bps)",
        _persistence_table(conn, universe),
    )

    sweep = [
        build_xs_report_row(conn, f"universe @{b:g}bps", universe, b)
        for b in (0.0, 2.0, 8.0, 16.0)
    ]
    _print_df("Cost sensitivity (universe)", pd.DataFrame(sweep))

    _print_df("Per-speed XS Sharpe", _per_speed_xs_sharpes(conn, universe))

    print(
        "\nG3 read: is the XS sleeve positive, cost-robust, DSR/PBO-survivable, AND "
        "low-correlated to trend (corr_to_trend near 0)? A modest-Sharpe XS sleeve "
        "uncorrelated with trend is a real combine win (P3 IDM layer). Read "
        "corr_to_trend + boot_lo + pbo alongside the headline before calling it. "
        "Equity caveat: short-borrow cost is omitted (D2) — mildly optimistic on "
        "the short legs."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/xsmom/test_audit_cli.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add tools/xsmom_audit.py tests/xsmom/test_audit_cli.py
git commit -m "feat(xsmom): read-only G3 audit driver (equity majors + SPY beta proxy)"
```

---

## Task 8: Makefile target + full gate + the equity G3 verdict

**Files:**

- Modify: `Makefile`
- Create: `docs/audits/2026-06-18-p3-xsmom-g3-equity.md`

- [ ] **Step 1: Add the Makefile target**

In `Makefile`, directly after the `wifey-forecast-audit` target block (the one
ending at the `tools/forecast_audit.py $(ARGS)` line), add (the recipe lines are
shown space-indented for lint compliance — when you write the Makefile they MUST
be indented with a real **TAB**, not spaces, or `make` will reject them):

```makefile
wifey-xsmom-audit:
    @echo "📊 G3 audit — XS-momentum sleeve over the breadth universe (1d)..."
    @PYTHONPATH=. poetry run python tools/xsmom_audit.py $(ARGS)
```

If there is a `.PHONY` declaration listing `wifey-forecast-audit`, add
`wifey-xsmom-audit` to it as well.

- [ ] **Step 2: Full gate — lint, typecheck, whole suite**

Run:

```bash
make lint-py && make typecheck && make test
```

Expected: ruff + mypy-strict clean; suite green (≈ 1657 + 24 new xsmom tests ≈
1681 pass / 3 skip). If the count differs, confirm the delta is only the new
xsmom tests.

- [ ] **Step 3: Confirm regression goldens are byte-identical**

Run: `make test-regression`
Expected: both configs PASS. The xsmom package writes nothing and adds no
detector/backtest path, so goldens MUST be unmoved. If they moved, stop and
investigate (likely an accidental edit outside `analytics/xsmom/` + the one
additive config field).

- [ ] **Step 4: Run the equity G3 audit against the real DB**

Run: `make wifey-xsmom-audit`
Expected: prints the breadth contrast, dollar-neutral gate, beta-attribution,
forward-persistence, cost-sensitivity sweep, and per-speed tables without error.
Capture the full output (you will transcribe the numbers into the verdict doc).

> If `make wifey-xsmom-audit` errors on missing OHLCV, the breadth universe must
> be backfilled first: `make wifey-universe-backfill` (defaults `SINCE=2018-01-01`).

- [ ] **Step 5: Write the verdict note**

Create `docs/audits/2026-06-18-p3-xsmom-g3-equity.md` capturing, from the Step-4
output: the universe-vs-majors breadth contrast, the original-vs-dollar-neutral
gate, beta attribution (alpha_ann / beta / alpha_t / hedged_sharpe / r2 vs both
eq-wt-mkt and SPY), forward persistence (per-year + trailing 1y/2y), the
cost-sensitivity sweep (0/2/8/16 bps), the per-speed Sharpe table, and `corr_to_trend`
/ `trend_sharpe`. State the **G3 verdict** against the spec's honest-pass bar
(`boot_lo > 0 ∧ dsr ≥ 0.95 ∧ pbo ≤ 0.5 ∧ cost-robust to ≥8 bps`), explicitly read
`corr_to_trend` (a low-corr modest sleeve is still a combine win), and note the D2
short-borrow-cost caveat. Mirror the structure of
`docs/audits/2026-06-17-p2-forecast-trend-g2-equity.md`. **Report the number
honestly, whatever it is.** This doc is CI-linted — hand-format to the full
markdownlint ruleset.

- [ ] **Step 6: Lint markdown + commit**

```bash
make lint-md
git add Makefile docs/audits/2026-06-18-p3-xsmom-g3-equity.md
git commit -m "feat(xsmom): wifey-xsmom-audit target + equity G3 verdict note"
```

---

## Task 9: Docs sweep, PR, and handoff

**Files:** docs only (driven by the skills below).

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/xsmom-sleeve-equity
```

- [ ] **Step 2: Open the PR**

Use the `/pr-summary` skill to draft, then:

```bash
gh pr create --repo s10023/buibui-wifey-wall-street-bot \
  --base main --head feat/xsmom-sleeve-equity \
  --title "feat(xsmom): cross-sectional momentum sleeve — equity port (P3, G3 verdict)" \
  --body-file /tmp/pr-feat-xsmom-sleeve-equity.md
```

- [ ] **Step 3: Run `/post-branch`**

This is the behaviour-gated docs sweep. It diffs the branch against the doc
surfaces and proposes edits to:

- `CLAUDE.md` — add `analytics/xsmom/` to Project Structure + `tools/xsmom_audit.py`
  to the tools list (mirror the forecast entries).
- `.claude/context/analytics.md` — add a `## xsmom/` section (mirror `## forecast/`).
- `README.md` — add the `make wifey-xsmom-audit` target.
- `MEMORY.md` — Current State one-liner + flip the open-question (7) status, and
  note the parent-sync pointer is now ready to advance to `e3b2a05` (do the bump
  per the handoff's Task 2 only after the exits triage).

Confirm each edit before writing.

- [ ] **Step 4: Rewrite the handoff prompt**

Overwrite `docs/plans/next-conversation-prompt.md` (per the always-update-handoff
preference): PR-2 shipped with its equity G3 verdict; next = the exits-direction
triage (#433/#437 — port or backlog) **then** bump the parent-sync pointer
`2698cfd → e3b2a05` (scope now includes #445). Reference the verdict doc.

---

## Self-review (run before declaring the plan done)

**Spec coverage** (against `2026-06-17-systematic-sleeves-equity-port-design.md`
PR-2 table + the #444/#445 head):

- `analytics/xsmom/__init__.py` → Task 6 ✓
- `analytics/xsmom/book.py` (incl. `xs_dollar_neutral`, #445) → Task 2 ✓
- `analytics/xsmom/diagnostics.py` (#445) → Task 3 ✓
- `analytics/xsmom/replay.py` (D4/D5) → Task 4 ✓
- `analytics/xsmom/report.py` (D1) → Task 5 ✓
- `tools/xsmom_audit.py` (D1, equity majors, SPY proxy, #445 tables) → Task 7 ✓
- `Makefile` `wifey-xsmom-audit` → Task 8 ✓
- `tests/xsmom/*` (book, diagnostics, replay, report, audit_cli) → Tasks 2–7 ✓
- `xs_dollar_neutral` config field → Task 1 ✓
- Equity G3 verdict doc → Task 8 ✓
- D1 (252) → config default (PR-1) + report threading (Task 5) + diagnostics
  defaults + audit threading (Tasks 3/7) ✓
- D2 (funding 0) → reused `load_daily_inputs` (PR-1); borrow-caveat flagged in the
  audit + verdict ✓
- D3 (turnover cost + slippage sweep) → `run_xs_backtest` cost term + audit sweep ✓
- D4 (`.stocks()`) → replay `_universe_symbols` + audit universe ✓
- D5 (`min_history_days`) → replay keyword-only arg ✓
- D6 (1d) → `load_daily_inputs("1d")` (PR-1) ✓
- D7 (metrics slice) → `from analytics.forecast import metrics` ✓
- Causality guard tests → ported verbatim in Task 2 ✓
- Goldens byte-identical → asserted Tasks 1 & 8 ✓

**Type consistency:** `XSBookResult`, `XSReport`, `BetaAttribution`,
`PersistenceReport`, `run_xs_backtest`, `xs_leverage`, `evaluate_xs`,
`build_xs_report_row` names are used identically across tasks and tests. ✓

**Placeholder scan:** no TBD / "handle edge cases" / "similar to Task N" — every
code step embeds full code. ✓
