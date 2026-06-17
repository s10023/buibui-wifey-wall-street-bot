# Systematic Sleeves — Equity Port Design (forecast trend + XS-momentum)

**Status:** DRAFT — decisions to confirm before execution
**Date:** 2026-06-17
**Owner:** wifey (forked from `buibui-moon-trader-bot`)

## Motivation

The master to-do's north star is a **cross-sectional (XS) momentum** forecast on
US equities. That work was gated on the parent passing gate **G1**; the
`/sync-parent` run on 2026-06-17 found the parent has since cleared **G1, G2 and
G3** on crypto and built a reusable systematic-research stack:

- `analytics/forecast/` — a Carver-style **EWMAC trend sleeve** (continuous,
  vol-targeted forecasts), parent PRs #438 / #443. Verdict on crypto: G2 FAIL on
  the ≥1-Sharpe bar but a real, structural, cost-robust positive (universe Sharpe
  +0.36; fast s8/32 carries +0.83).
- `analytics/xsmom/` — a **dollar-neutral long-short cross-sectional momentum
  sleeve** built on the trend primitives, parent PR #444. Verdict on crypto:
  **G3 CLEARED — the first gate-passing sleeve.**

This campaign **ports the engine** so wifey can compute its **own equity
verdict**. The parent's G3-clear was on 25 crypto perps; whether XS-momentum
clears on ~100 US large-caps is a *fresh empirical question*. We are porting the
machinery to answer it honestly — **not** assuming the crypto result transfers.

## What wifey already has (no re-port)

The parent's redesign arc converged with work wifey did independently. These are
the load-bearing dependencies and they already exist here:

| Dependency | Wifey home | Parity |
| --- | --- | --- |
| Research guards (DSR / PBO / bootstrap-CI / MinTRL) | `analytics/research_guards/` (N2, #81) | **verbatim** port of the parent API — `block_bootstrap_ci`, `cscv_pbo`, `deflated_sharpe_ratio`, `min_track_record_length` all present with identical signatures |
| Breadth universe | `config/universe.json` + `utils.config_validation.load_research_universe()` (N3) | wifey-native `ResearchUniverse`; `.stocks()` returns active single-name stocks (the XS cross-section) |
| `min_history` filter | `ResearchUniverse.with_min_history(days)` / `load_research_universe(min_history_days=)` (#89) | available to trim short-history names (GEV/PLTR/UBER) from the cross-section |
| 1d OHLCV, full history | `analytics/store/market_data.get_ohlcv(conn, sym, "1d", start, end)` | breadth universe backfilled 2018→ for 1d; same signature the parent replay calls |
| `DEFAULT_DB_PATH` | re-exported from `analytics.store` | parent's import path resolves unchanged |

## Dependency DAG (what the port pulls in)

```text
analytics/xsmom/report.py
  ├─ analytics/xsmom/book.py ──────┐
  ├─ analytics/forecast/config.py  │ (ForecastConfig)
  ├─ analytics/research_guards/    │  ✓ wifey N2
  ├─ portfolio.metrics  (curve fns)│  → port thin slice
  └─ analytics/forecast/book.py    │  (trend returns for corr_to_trend)
                                   │
analytics/xsmom/book.py ──────────┤
  ├─ analytics/forecast/config.py  │
  ├─ analytics/forecast/ewmac.py   │  (combine_forecasts)
  └─ analytics/forecast/vol.py     │  (ew_return_vol)
                                   │
analytics/xsmom/replay.py ────────┤
  ├─ analytics/forecast/replay.py  │  (load_daily_inputs — DB+universe seam)
  └─ analytics/forecast/config.py  │
```

**Consequence:** the forecast sleeve is a *hard prerequisite* of xsmom (its
config / ewmac / vol / replay primitives are imported, and the trend sleeve's
returns feed the `corr_to_trend` diversification read in the XS verdict). So the
campaign is two PRs, in order:

- **PR 1 — forecast trend sleeve** (`analytics/forecast/` + curve metrics +
  `tools/forecast_audit.py`). Independently valuable: wifey's first continuous
  vol-targeted equity backtest + a G2 verdict.
- **PR 2 — XS-momentum sleeve** (`analytics/xsmom/` + `tools/xsmom_audit.py`),
  built on PR 1. Produces wifey's first equity G3 verdict.

Both are **additive, read-only** packages (no DB writes, no schema change, no
detector/backtest changes) → **regression goldens stay byte-identical**.

## Adaptation decisions (crypto → equity)

These are defaulted with rationale; **confirm or veto before execution**.

### D1 — Annualization: 252, not 365 *(load-bearing)*

Crypto trades 365 days/year; US equities ~252 NYSE sessions. Every Sharpe / vol /
governor annualization must use **252**. Change `ForecastConfig.annualization_days`
default `365.0 → 252.0`, and the ported curve-metrics module default
`_PPY 365.0 → 252.0`. To keep a single source of truth, the report passes
`periods_per_year=cfg.annualization_days` into the metrics calls explicitly
(parent relied on both defaulting to 365). **This is the most important
correctness seam — a wrong factor silently rescales every headline.**

### D2 — Funding → 0 (borrow deferred)

Equities have no funding/perp-financing leg; wifey stripped `get_funding_rates`
in T4. The ported `load_daily_inputs` drops the funding fetch and returns an
**all-zero funding dict** for signature compatibility (`fundings[sym] =
Series(0.0, index=close.index)`). The book's `funding_cost = leverage * fund`
term then vanishes. **Short-borrow cost** (the honest equity analog, applies to
the short legs of the dollar-neutral XS book) is a **v2 refinement** — wifey's
Phase 0.4 cost model carries a borrow rate (0.01/yr constant) we can wire later;
for v1 it is 0, which is mildly optimistic on the short legs but conservative to
omit complexity. Flagged in the verdict caveats.

### D3 — Cost model: keep the Carver turnover-bps model (parameterized)

The sleeves cost = `|Δleverage| × (fee_pct + slippage_pct)` per day. This is the
standard continuous-position turnover cost and is **orthogonal** to wifey's Phase
0.4 per-trade-R cost model (which is per-discrete-trade, the wrong shape for
continuous vol-scaled positions). Keep the turnover model; set conservative
equity defaults `fee_pct=0.0001` (1 bp commission-ish) and `slippage_pct=0.0002`
(2 bp), and **sweep slippage 0/2/8/16 bps** in the audit tool for cost-robustness
(as the parent does). Do **not** drop `from_toml`'s `[backtest]` coupling silently
— wifey's `[backtest]` block has no `slippage_bps`, so `from_toml` falls back to
its defaults; document that the sleeve cost is config-defaulted, not read from the
live signal config.

### D4 — Cross-section = active single-name stocks (`.stocks()`)

`load_universe()` (parent crypto) → `load_research_universe().stocks()`. Use
`.stocks()` (active, `kind=="stock"`) **not** `.active_symbols()`: the index ETFs
(SPY/QQQ/DIA/IWM) must not enter the cross-sectional demean — an index is a basket
of the very names being ranked and would distort relative strength. The trend
sleeve uses the same `.stocks()` set for a like-for-like `corr_to_trend`.

### D5 — `min_history`: default OFF, available as a knob

The book's NaN-skipping aggregation (`mean(axis=1)` / active-only demean) already
handles staggered histories — short-history names (GEV/PLTR/UBER) simply
contribute only once warmed up. So a hard `min_history` cut is **not required**.
Default the replay to the full `.stocks()` set; expose `min_history_days` as an
optional arg on the replay front door so the audit tool can offer a
clean-cross-section contrast (uses #89's `with_min_history`). This is the concrete
consumer the #89 seam was built for.

### D6 — Timeframe = 1d (no new ingest)

The sleeves operate on daily closes. wifey's breadth universe is backfilled 1d
full-history 2018→. No new data work. (4h/1wk are out of scope for these daily
sleeves.)

### D7 — Curve metrics: port a thin slice, not `portfolio/`

xsmom + forecast reports only use the **curve-based** functions of the parent's
`portfolio/metrics.py` (`sharpe`, `sortino`, `max_drawdown`, `calmar`,
`annual_return`, `annual_vol`) — all pure over a daily equity curve and already
`periods_per_year`-parameterized. The `avg_exposure` / `risk_turnover` /
`attribution` functions depend on `portfolio.book` (the P1 Carver sizing package)
and are **not** needed. Port only the curve slice to a new
`analytics/forecast/metrics.py` (default `_PPY=252.0`). Do **not** port the
`portfolio/` package.

## Equity-majors contrast set (audit tooling)

The audit's breadth contrast (universe vs majors) used crypto majors
(BTC/ETH/SOL). Equity analog = a mega-cap cluster, default
`AAPL,MSFT,NVDA,AMZN,GOOGL,META` (overridable via `--majors`). The point of the
contrast is "does breadth help vs a concentrated cluster" (the parent found
breadth halves max-DD) — the exact majors list is not load-bearing.

## Acceptance gates (verdict, not auto-commit)

Both sleeves emit a read-only verdict; there is no auto-gate on a live path. The
"pass" bar mirrors wifey's `sweep_guard` commit rule:

- **G2 (trend):** report all of `sharpe_annual`, `dsr`, `pbo`, `boot_lo/hi`,
  `min_trl`. Honest pass = `boot_lo > 0 ∧ dsr ≥ 0.95 ∧ pbo ≤ 0.5 ∧ cost-robust to
  ≥8 bps`. Expect (from crypto priors) a likely *sub-bar but real* result; the
  deliverable is the **number on equities**, whatever it is.
- **G3 (XS):** same stamps **plus** `corr_to_trend` and `trend_sharpe`. A
  modest-Sharpe XS sleeve that is *uncorrelated with trend* (`corr_to_trend ≈ 0`)
  is a genuine combine win even below the solo bar — read `corr_to_trend +
  boot_lo + pbo` alongside the headline.

**Honesty rule:** the equity verdict is whatever the data says. If XS-momentum
does **not** clear on US equities, that is a publishable negative and the campaign
still succeeds (the question is answered with guards, not vibes).

## Non-goals (this campaign)

- Porting the `portfolio/` Carver-sizing package (#435) — only the curve-metrics
  slice (D7).
- Porting the exits work (#433 MFE/MAE, #437 exit-policy replay) — a separate,
  equally-portable direction tracked for later.
- Any live-path / Telegram / order-layer wiring — these are research sleeves
  (read-only audits), Phase A stays signals-only.
- Crypto carry-overs: funding backfill (#429), funding+slippage cost model (#430,
  wifey has its own 0.4), crypto perp universe (#434).

## PR sequence & module map

### PR 1 — forecast trend sleeve (equity)

| File | Source (parent) | Adaptation |
| --- | --- | --- |
| `analytics/forecast/__init__.py` | same | re-export `ForecastConfig`, `replay_universe`, `replay_trials`, `evaluate`, `G2Report` |
| `analytics/forecast/config.py` | same | D1 (`annualization_days=252.0`); drop `weights` study path is optional (keep — it is additive) |
| `analytics/forecast/vol.py` | same | `annualize` default `365→252` |
| `analytics/forecast/ewmac.py` | same | verbatim (pure, instrument-agnostic) |
| `analytics/forecast/book.py` | same | D2 (funding term stays but is fed zeros) |
| `analytics/forecast/metrics.py` | `portfolio/metrics.py` (curve slice) | D7 (`_PPY=252.0`); drop book-dependent fns |
| `analytics/forecast/replay.py` | same | D2 (drop funding fetch), D4 (`load_research_universe().stocks()`), D5 (`min_history_days` arg) |
| `analytics/forecast/report.py` | same | D1 (pass `periods_per_year=cfg.annualization_days` to metrics) |
| `analytics/forecast/weights.py` | same | verbatim (additive weight-study; pure) |
| `tools/forecast_audit.py` | same | D1, equity majors, wifey DB path |
| `Makefile` | — | add `wifey-forecast-audit` target |
| `tests/forecast/*` | parent `tests/forecast/*` | port; adjust expected annualization to 252; funding tests → zero-funding |

### PR 2 — XS-momentum sleeve (equity)

| File | Source (parent) | Adaptation |
| --- | --- | --- |
| `analytics/xsmom/__init__.py` | same | re-export `replay_xs`, `replay_xs_trials`, `evaluate_xs`, `XSReport` |
| `analytics/xsmom/book.py` | same | D2 (funding zeros) |
| `analytics/xsmom/replay.py` | same | reuses PR1 `load_daily_inputs`; D4/D5 |
| `analytics/xsmom/report.py` | same | D1 |
| `tools/xsmom_audit.py` | same | D1, equity majors, wifey DB path |
| `Makefile` | — | add `wifey-xsmom-audit` target |
| `tests/xsmom/*` | parent `tests/xsmom/*` | port; 252; zero-funding |

PR 2's detailed TDD plan is written **after PR 1 lands and is verified on equity
data** (the foundation must exist + pass before its consumer is planned in
detail). PR 1's plan is `docs/superpowers/plans/2026-06-17-forecast-trend-sleeve-equity-port.md`.

## Risks & mitigations

- **Annualization drift (D1):** mitigated by a single `cfg.annualization_days`
  source threaded into the report's metric calls + a test asserting 252.
- **Causality (the load-bearing invariant):** the parent's tests include
  middle-bar perturbation tests (RED without `.shift(1)`). Port them verbatim —
  they are the look-ahead guard.
- **Verbatim-port guard signature drift:** research_guards were ported verbatim
  (N2), but PR 1 Task 0 verifies `block_bootstrap_ci(stat_fn=, seed=)`,
  `deflated_sharpe_ratio(sr, n, trial_srs=)`, `cscv_pbo(mat).pbo`,
  `min_track_record_length(sr, target_sr=, confidence=)` resolve before any
  report code is written.
- **Empty/thin verdict:** the equity result may be weak or negative — that is an
  accepted outcome (honesty rule), not a port failure.
