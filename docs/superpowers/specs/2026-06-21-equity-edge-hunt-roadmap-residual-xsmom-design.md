# Equity Edge-Hunt Roadmap + Experiment #1 (Residualized XS-Momentum)

**Status:** DRAFT — approved in brainstorm 2026-06-21, pending spec review
**Date:** 2026-06-21
**Owner:** wifey (forked from `buibui-moon-trader-bot`)

## Motivation

The binding constraint is recorded in memory
[[binding-constraint-no-equity-edge]]: **wifey has no positive-Sharpe equity
edge.** Both sleeves ported from the parent failed their gates — forecast trend
**G2 ≈ 0** (#91) and cross-sectional momentum **G3 = −0.156** (#92, negative even
at 0 bps). The whole sizing / portfolio / risk stack is built and portable, but
it is *downstream* of a signal we do not have: you cannot vol-target your way out
of negative Sharpe (the parent proved this — its raw-TA paper book scored Sharpe
−0.53 in #435; its positive number only appeared once it added a *new structural
sleeve*).

We are **not** blocked on the parent. Its "wait for moon G1" hybrid gate has
cleared. The job is now a research problem: **find an equity-native edge** under
the free-data (yfinance / EDGAR) constraint and the existing research-guard
discipline (DSR / PBO / MinTRL, honest costs, no new boolean TA detectors).

This spec delivers two things:

1. A **prioritized edge-hunt roadmap** — the durable map of candidate edge
   families, ranked by data cost, infrastructure reuse, robustness prior, and
   fit to the long-only north star.
2. A full design for **experiment #1**, the cheapest high-information test:
   **resurrect the cross-sectional momentum sleeve, built correctly** —
   residualized, skip-month, on a broadened universe.

## Why XS-momentum failed (the diagnosis we are testing)

The parent-fresh-eyes memo called equity XS-momentum *"the canonical,
decades-deep factor… arguably easier to validate"* — then it failed here. The
likely reason is **not** "momentum is dead in equities"; it is that #92 tested
**raw-return** momentum on **101 efficient S&P-100 mega-caps**, the most
arbitraged slice of the market. The academic premium lives in **residual**
(beta / industry-neutralized) momentum over a **broad** cross-section, with the
**most recent month skipped** (the last-month leg is short-term-reversal
contaminated). Our own #92 audit already hinted at this: the fast EWMAC speed
was −0.34 while the slow speed was +0.08.

Experiment #1 rebuilds the sleeve with three a-priori construction fixes and
reports a clean contrast so we learn *which* fix (if any) moved the number.

## Scope decision

This is a **decomposition**: the candidate edge families are independent
subsystems, each its own sleeve with its own data needs and acceptance gate. We
do **not** design "all the edges" in one spec. This spec fully designs
**experiment #1** and catalogs the rest as a ranked roadmap; each later
experiment gets its own spec → plan → implementation cycle.

## Experiment #1 — Residualized XS-Momentum

### Hypothesis and null

- **Null under attack:** "Cross-sectional momentum has no edge in US equities."
- **Our narrower prior:** the real null is "*raw-return* momentum on *efficient
  mega-caps* has no edge", and the literature edge survives in **residual**
  momentum on a **broader** cross-section with a **skip-month**.
- **Decisive either way.** A pre-registered pass → we have a core sleeve and the
  diagnosis is "construction, not asset class." A clean fail → momentum is
  genuinely absent in our investable free-data set, and the roadmap pivots to
  non-momentum families having spent the least to learn it.

### Construction (maximal reuse, additive, default-off)

Built as **new optional inputs to the existing `analytics/xsmom/`**, not a new
package, so the default path stays byte-identical and the regression goldens do
not move. Three fixes layered onto the existing pipeline:

1. **Residual returns.** For each name, strip market exposure via a **causal
   rolling beta** — regress daily returns on an equal-weight market proxy over a
   trailing window, `.shift(1)` everything — and momentum-rank the **residuals**
   `r_i − β_i · r_mkt` rather than raw returns. Reuses the existing
   `equal_weight_market_return` helper in `xsmom/diagnostics.py`.
2. **Skip-month.** Feed the EWMAC forecast **only its slow speeds** (drop the
   fast, reversal-contaminated leg). This is the no-new-code analog of the
   canonical 12-1 skip and is consistent with #92's per-speed evidence.
3. **Sector-neutral demean (optional arm).** Cross-sectionally demean **within
   GICS sector** (sectors already live in `config/universe.json`), so the book
   is neutral to sector tilts as well as market beta.

Everything downstream is reused verbatim: demean → `.shift(1)` → vol-parity
leverage → portfolio governor → `run_xs_backtest`; `report.evaluate_xs` +
`research_guards` for the gate; the existing turnover / cost model for honest
costs.

**Both books reported:** the full **long-short** book (where the alpha is
measured — the diagnosis) and the **long-only top-quintile** book (the
deployable wife-sleeve form), so a win is immediately translatable to the
product.

### Universe — broaden to current S&P 500, with a 2×2 contrast

To separate "mega-caps too efficient" from "construction wrong", experiment #1
runs a **2×2 grid**: `{105 mega-cap, ~500 broad} × {raw, residual+skip}` = four
books, each gate-scored. This isolates which lever moved the number instead of a
single ambiguous pass/fail.

- **Data prerequisite:** expand `config/universe.json` from 105 to the **current
  S&P 500 constituents** (~+400 names, free; GICS sectors + `kind` + `delisted:
  false`, plus a `listed` date for post-2018 IPOs where known), then backfill
  the 1d and 1wk history from 2018 via `wifey analytics backfill --universe`.
  Coverage verified with `make universe-coverage`.
- **Balanced panel:** post-2018 IPOs are handled by the existing `listed` /
  `ResearchUniverse.with_min_history(days)` seam so short-history names do not
  contaminate the cross-section.
- **Survivorship caveat (on the record):** today's S&P 500 is a survivor set, and
  for a *momentum* book that bias is **not** neutral — winners that kept winning
  stayed in, losers that delisted dropped out. Every broad-universe number
  carries the flag; a **marginal pass on the broad arm is treated as suspect**, a
  clean fail is still trustworthy. Claim is bounded to current constituents (no
  delisted), consistent with the universe policy.

### Acceptance gate (pre-registered — fixed before any result exists)

This is the pre-registration moment the master to-do flagged ("G2's Sharpe ≥ 1
may be rich for equity XS-mom; any revision must be pre-registered *before*
looking at results"). The bar is fixed now:

- **Pass = all of:** net-of-cost **DSR ≥ 0.95**, **PBO ≤ 0.5**, bootstrap-CI
  **lower bound > 0**, **n ≥ MinTRL**, and **OOS Sharpe ≥ 0.7** (a defensible
  equity long-short momentum bar — below the crypto 1.0, above noise).
- **Pre-committed cell:** the verdict is read on the **`broad × residual+skip`**
  book — *not* whichever of the four 2×2 books scores highest (that would
  re-introduce selection bias). The other three books are diagnostic context, and
  the full 4-book grid feeds the PBO trial count.
- **Costs in every P&L surface:** reuse the existing turnover / cost model;
  report Sharpe at **0 / 2 / 8 bps** sensitivity. A pass that dies by 8 bps is
  "thin / parked", not a deploy.

### Components (units, each one purpose, additive)

- `analytics/xsmom/residual.py` *(new, pure)* — `rolling_beta` (causal,
  `.shift(1)`), `residual_returns`, `sector_neutral_demean`. No DB, no engine
  import.
- `analytics/xsmom/book.py` *(extend)* — additive keyword-only options on the
  forecast builder (`residual_inputs`, `sector_map`, slow-only `speeds`); default
  off ⇒ byte-identical. `run_xs_backtest` signature unchanged (it already takes
  forecasts).
- `analytics/xsmom/replay.py` *(extend)* — load the GICS sector map from
  `load_research_universe`; a `replay_residual_xs` that runs the 2×2 grid
  read-only over the broadened universe.
- `analytics/xsmom/report.py` *(extend)* — reuse `evaluate_xs`; add a 2×2
  contrast table + the pre-committed-cell verdict + L/S-vs-long-only rows.
- `tools/xsmom_residual_audit.py` *(new, read-only)* — runs the 2×2, prints the
  gate verdict on the pre-committed cell, the cost-sensitivity sweep, the
  long-only leg, and the survivorship flag. Wired as `make
  wifey-xsmom-residual-audit`.
- `config/universe.json` *(data)* — expanded to S&P 500; committed artifact.

### Data flow

```text
universe.json (S&P 500 + sectors)
  -> replay.load_daily_inputs (OHLCV, read-only)  + sector map
  -> residual.rolling_beta / residual_returns      (causal, .shift(1))
  -> EWMAC forecast (slow speeds only = skip-month)
  -> demean (optionally sector-neutral)
  -> .shift(1) -> vol-parity leverage -> governor
  -> run_xs_backtest -> book returns (L/S and long-only)
  -> report.evaluate_xs (+ research_guards) -> 2x2 grid + pre-committed verdict
```

### Testing

- **TDD, in-memory DuckDB**, mypy strict, ruff, hand-formatted markdown — house
  conventions.
- **Causality is the load-bearing invariant.** A perturbation test per new
  transform: bump a *future* bar and assert today's `rolling_beta` /
  `residual_returns` / forecast / leverage are unchanged (RED-without-shift
  verified, non-vacuous). Mirrors the parent's xsmom/forecast guards.
- **Goldens unmoved.** Default path byte-identical; `make test-regression` both
  configs PASS. No change to any detector / backtest / signal path.

## The roadmap (ranked edge-hunt sequence)

Each family carries its trigger and data need. Order is fixed; each later
experiment gets its own spec → plan.

| # | Family | Data | Reuse | Long-only | Trigger |
| --- | --- | --- | --- | --- | --- |
| 1 | **Residualized XS-mom** (this spec) | free, price-only | extends `xsmom/` | L/S (+ long leg) | now |
| 2 | **Low-vol / BAB long sleeve** | free, price-only | vol primitives | **yes** | build regardless of #1 |
| 3 | **Cross-asset TSMOM** (metals + commodity futures) | trivial add | `forecast/` | long-biasable | after a first equity edge, or pull forward if #1 ∧ #2 fail |
| 4 | **PEAD-lite** (price drift around free earnings dates) | new ingestion | greenfield | longable | gated behind #1 / #2 |

- **Parked:** short-term reversal (cost-heavy, high-turnover), seasonality /
  turn-of-month (weak, smells like the frozen TA category), index-rebalance flow
  (thin n, hard to DSR-gate).
- **Convergence:** once **≥ 2 sleeves clear**, the deferred parent ports —
  `portfolio/` sizing (#435) and combine / IDM (#446) — finally earn their keep,
  and sizing work begins. That is also the substitute owed to the backlogged
  exits #437 A/B.
- **Honest exit criterion:** if #1–#4 all fail clean, that is a decisive finding
  — trigger the $29/mo Polygon upgrade (depth / breadth) or accept that the
  free-data US-equity edge needs paid event / flow data, and re-scope. We learn
  it cheaply.

## Out of scope (YAGNI)

- No sizing / portfolio / paper-book work this cycle — gated on ≥ 2 clearing
  sleeves.
- No new boolean TA detectors, no tp_r / gate / threshold sweeps (frozen
  category).
- No paid data, no delisted-history purchase — survivorship is bounded and
  flagged, not eliminated.
- Low-vol (#2) and beyond are cataloged here but designed in their own specs.

## Open questions

None blocking. The pre-registered bar (DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0 ∧
OOS Sharpe ≥ 0.7 on the `broad × residual+skip` cell) and the roadmap order are
confirmed.
