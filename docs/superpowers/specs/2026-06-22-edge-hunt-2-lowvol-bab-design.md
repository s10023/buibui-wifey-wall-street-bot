# Edge-Hunt #2 — Low-Beta / BAB Long Sleeve

**Status:** DRAFT — approved in brainstorm 2026-06-22, pending spec review
**Date:** 2026-06-22
**Owner:** wifey (forked from `buibui-moon-trader-bot`)

## Motivation

The binding constraint is recorded in memory
[[binding-constraint-no-equity-edge]]: **wifey has no trustworthy, deployable
positive-Sharpe equity edge.** Three sleeves now fail their gates — forecast
trend **G2 ≈ 0** (#91), cross-sectional momentum **G3 = −0.156** (#92), and
residualized XS-momentum **experiment #1 = FAIL** (#98, committed cell Sharpe
+0.15 @2 bps, DSR 0.44, boot_lo < 0). The whole sizing / portfolio / risk stack
is built and portable, but it is *downstream* of a signal we do not have: you
cannot vol-target your way out of negative Sharpe.

This is **edge-hunt #2** on the roadmap fixed in the experiment-#1 spec
(`docs/superpowers/specs/2026-06-21-equity-edge-hunt-roadmap-residual-xsmom-design.md`):
a **low-volatility / betting-against-beta (BAB) long sleeve**, free price-only
data, to be built *regardless of #1's outcome*. Each edge family gets its own
spec → plan → implementation cycle; this spec designs #2 in full.

## The lesson from #1 that shapes this design

Experiment #1's only positive number was its **long-only top-quintile leg
(+0.88 Sharpe @2 bps)** — and it was *untrustworthy*: a long-only book on the
**current** S&P 500 (survivors) over the **2018–2026 bull run** is confounded by
two things it cannot separate from alpha — survivorship and plain long-equity
beta. The gated, dollar-neutral cell failed cleanly; the confounded long-only
number was discarded by pre-registration discipline.

A naive long-only low-volatility sleeve walks straight into the same trap:
low-vol names rose because *everything* rose. So the central design decision is
**how we measure the anomaly so the gate reads alpha, not "long equities during a
bull market on survivors."** BAB answers this structurally: its native construct
is a **beta-neutral long-short**, which is ~insensitive to "the whole market went
up" and to survivorship. That is why this sleeve ranks on **beta**, not total
volatility, and gates on the **beta-neutral spread**, not the long-only form.

## Hypothesis and null

- **Null under attack:** "Betting-against-beta has no edge in US equities."
- **Economic prior:** leverage-constrained investors bid up high-beta names,
  depressing their risk-adjusted returns; a beta-neutral long-low-beta /
  short-high-beta spread harvests the difference (Frazzini–Pedersen 2014). The
  effect is decades-deep and internationally replicated, but got crowded and was
  beaten up in the 2018–2020 "quant winter," so a recent-decade US equity result
  is a genuinely open empirical question.
- **Decisive either way.** A pre-registered pass → a *trustworthy* (beta-neutral,
  not bull-confounded) equity sleeve and the binding constraint finally cracks. A
  clean fail → BAB is absent in our investable free-data set, and the roadmap
  pivots to #3 (cross-asset TSMOM) having spent the least to learn it.

## Construction (maximal reuse, additive, read-only)

Built as a **new self-contained package `analytics/lowvol/`** (mirrors the
`forecast/` · `xsmom/` · `exits/` one-sleeve-one-package pattern). Purely
additive and read-only — no DB writes, no schema change, no detector / backtest /
signal-path change — so the default path is untouched and the regression goldens
do not move (nothing existing imports it, same as the #91 / #92 ports). It
*reuses*, it does not reinvent:

- `rolling_beta` (causal, `.shift(1)`) from `analytics/xsmom/residual.py`
- `ew_return_vol` / `annualize` from `analytics/forecast/vol.py`
- `load_daily_inputs` (read-only OHLCV over the universe) from
  `analytics/forecast/replay.py`
- `equal_weight_market_return` from `analytics/xsmom/diagnostics.py` (the market
  proxy for beta)
- `run_xs_backtest(closes, fundings, cfg, *, leverage=…)` — the cost-aware
  injection added in #1 — books any leverage frame on actual closes with the
  honest turnover cost model and funding = 0
- `research_guards` (DSR / PBO / block-bootstrap CI / MinTRL) via the
  `evaluate_residual_grid` gate pattern in `analytics/xsmom/report.py`

### The three book forms

1. **Gated cell — beta-neutral BAB long-short.** Per name: causal
   `β_i = rolling_beta(ret_i, ew_market_ret, window).shift(1)` (window =
   `_BETA_WINDOW = 252` trailing sessions, the a-priori value already used in
   `residual.py`). Cross-sectional signal `z_i = −demean(β_i)` (low-beta → long,
   high-beta → short). Vol-parity scale each name by its **actual** annualized
   return vol (the `ew_return_vol` / `vol_target_annual` machinery used verbatim
   in `xs_residual_leverage`). Then **beta-neutralize**: scale the long leg
   (`z > 0`) and short leg (`z < 0`) so the net causal portfolio beta is zero,
   `Σ_i w_i · β_i = 0`. The existing 20%-vol portfolio governor in
   `run_xs_backtest` sets the level. A beta-neutral spread is structurally
   insensitive to the bull-market / survivorship confound — this is the
   *trustworthy* number.
2. **Robustness row — low-vol-ranked.** The identical pipeline, but rank on
   trailing realized vol (`ew_return_vol(ret).shift(1)`) instead of beta, still
   beta-neutralized for comparability. **Diagnostic only** — tells us whether the
   effect is beta-specific or a general "low-risk" tilt; never gated.
3. **Deployable form — long-only bottom-quintile low-beta.** Long the lowest-beta
   quintile, vol-targeted unit longs, no shorts, no dollar-neutral re-center
   (mirrors `long_only_residual_leverage`). This is the wife-sleeve product form,
   but it **carries the survivorship + long-equity-beta confound flag and is
   never the gated cell** — exactly the #1 lesson.

### Beta-neutrality guardrail

The report regresses each book's realized returns on the equal-weight market and
prints the **realized portfolio beta**, which must be ≈ 0 for the beta-neutral
cells. If it is materially non-zero the neutralization is broken and the
"trustworthy / unconfounded" claim is void — this is a correctness check on the
construct, not a performance metric.

## Universe and the 2×2 grid

To separate "is it beta or is it general low-risk" the experiment runs a **2×2
grid** = `{beta-rank, vol-rank} × {beta-neutral L/S, long-only bottom-quintile}`
= four books, each gate-scored:

| ranking ↓ \ construct → | beta-neutral L/S | long-only bottom-quintile |
| --- | --- | --- |
| **beta (BAB)** | **◀ GATED CELL** | deployable (flagged) |
| realized vol | robustness | deployable (flagged) |

- **Universe:** the current S&P 500 already committed to `config/universe.json`
  (508 members = 504 stocks + 4 ETFs; ETFs excluded from the cross-section via
  `ResearchUniverse.stocks()`), 1d history from 2018, balanced with
  `with_min_history` so post-2018 IPOs do not contaminate the panel — identical
  to #1's data setup.
- **Survivorship caveat (on the record):** today's S&P 500 is a survivor set. The
  beta-neutral construct largely *defuses* this (a market-neutral spread is far
  less survivorship-sensitive than a long-only book), but it is not eliminated; a
  **marginal pass is treated as suspect, a clean fail is trustworthy.** Claim is
  bounded to current constituents (no delisted history), consistent with the
  universe policy.

## Acceptance gate (pre-registered — fixed before any result exists)

The bar is fixed **now**, before any number is computed:

- **PASS = all of:** net-of-cost **DSR ≥ 0.95**, **PBO ≤ 0.5**, bootstrap-CI
  **lower bound > 0**, **n ≥ MinTRL**, and **OOS Sharpe ≥ 0.7**.
- **Pre-committed cell:** the verdict is read on the **`beta × beta-neutral
  L/S`** book — *not* whichever of the four 2×2 books scores highest (that would
  re-introduce selection bias). The other three books are diagnostic context, and
  the full 4-book grid feeds the PBO / DSR trial count.
- **Deploy-grade tier (annotation, not the pass/fail line):** Sharpe **≥ 1.0**
  *and* the long-only translated form holds up. A `≥ 0.7` pass is "real edge,
  candidate sleeve"; a `≥ 1.0` pass is "deploy-grade, fast-track." This captures
  the higher bar without pre-registering a near-certain fail (US-equity BAB net
  of cost is unlikely to clear 1.0 even if a real edge exists — Frazzini–Pedersen
  US BAB ran ~0.78 gross over 1926–2012 and weaker recently — so 1.0 as the
  pass line would make the experiment low-information).
- **Costs in every P&L surface:** report Sharpe at **0 / 2 / 8 bps** slippage
  sensitivity (`--slippage-bps`, like `xsmom_residual_audit.py`). A pass that
  dies by 8 bps is "thin / parked," not a deploy.

The `≥ 0.7` Sharpe floor is paired with a **net-of-cost DSR ≥ 0.95 deflated over
the full 2×2 trial count**, which is itself a very high bar against thin / overfit
edges; a spurious 0.7 will not clear it.

## Components (units, each one purpose, additive)

- `analytics/lowvol/signals.py` *(new, pure — no DB, no engine import)* —
  `causal_betas` (per-name shifted rolling beta vs the EW market),
  `realized_vols` (per-name shifted trailing vol), `cross_sectional_score`
  (`−demean(metric)`: low metric → long), `beta_neutral_leverage` (vol-parity
  long-short, beta-neutralized via `Σ wβ = 0` — drives **both** the beta-row and
  vol-row L/S cells from its score input + the causal betas), and
  `long_only_leverage` (long bottom-quantile, vol-targeted — drives **both**
  long-only cells from its score input). Two builders × two score inputs (beta,
  vol) = the four grid cells. All causal, `.shift(1)`.
- `analytics/lowvol/replay.py` *(new, the only DB-touching module, read-only)* —
  `replay_bab_grid`: `load_daily_inputs` over `load_research_universe().stocks()`
  → build the four leverage frames → `run_xs_backtest(closes, fundings, cfg,
  leverage=…)` per cell → `dict[str, XSBookResult]`.
- `analytics/lowvol/report.py` *(new)* — `evaluate_bab_grid` mirroring
  `evaluate_residual_grid`: per-cell `evaluate_xs`, gate read on the pre-committed
  `beta × beta-neutral L/S` cell, full grid → PBO trial count, plus the
  realized-portfolio-beta diagnostic, the long-only-leg Sharpe, and the
  deploy-grade tier flag. Fixed gate constants (`_GATE_SHARPE = 0.7`, DSR ≥ 0.95,
  PBO ≤ 0.5, boot_lo > 0, n ≥ MinTRL).
- `analytics/lowvol/__init__.py` *(new)* — eager re-exports.
- `tools/lowvol_audit.py` *(new, read-only)* + `make wifey-lowvol-audit` — runs
  the 2×2, prints the gate verdict on the pre-committed cell, the cost-sensitivity
  sweep, the long-only leg, the realized-beta check, and the survivorship flag.
  `build_grid` / the verdict helper are the testable units. `--slippage-bps`.
- `docs/audits/2026-06-22-edge-hunt-2-lowvol-bab.md` *(verdict note, written after
  the audit runs)*.

## Data flow

```text
config/universe.json (S&P 500 + sectors)
  -> replay.load_daily_inputs (OHLCV, read-only) over .stocks()
  -> equal_weight_market_return -> rolling_beta (causal, .shift(1))   [or realized_vols]
  -> cross-sectional rank/demean -> vol-parity sizing
  -> beta-neutralize (Σ wβ = 0)        [or long-only bottom-quintile]
  -> run_xs_backtest(leverage=…)  (cost model, funding = 0)
  -> report.evaluate_bab_grid (+ research_guards)
       -> 2x2 grid + pre-committed-cell verdict + realized-β diagnostic
```

## Testing

- **TDD, in-memory DuckDB**, mypy strict, ruff, hand-formatted markdown — house
  conventions.
- **Causality is the load-bearing invariant.** A perturbation test per new
  transform: bump a *future* bar and assert today's `causal_betas` /
  `realized_vols` / `beta_neutral_leverage` / `long_only_leverage` are unchanged
  (RED-without-`.shift(1)` verified non-vacuous). Mirrors the parent's
  xsmom / forecast guards.
- **Beta-neutrality test.** For the beta-neutral cells, assert the net causal
  portfolio beta `Σ w·β` is ≈ 0 by construction (the trust claim depends on it).
- **Goldens unmoved.** The package is additive and read-only; `make
  test-regression` both configs PASS, byte-identical. No change to any
  detector / backtest / signal path.

## Out of scope (YAGNI)

- No sizing / portfolio / paper-book work — gated on ≥ 2 clearing sleeves per the
  roadmap convergence rule.
- No new boolean TA detectors, no tp_r / gate / threshold sweeps (frozen
  category).
- No paid data, no delisted-history purchase — survivorship is bounded and
  flagged, not eliminated.
- No Frazzini–Pedersen leverage-to-β=1 *funding* model (perfect-funding-rate
  assumption); the beta-neutral spread captured here is the cleaner,
  free-data-only form.
- Roadmap items #3 (cross-asset TSMOM) and #4 (PEAD-lite) are cataloged in the
  edge-hunt roadmap spec and designed in their own specs.

## Open questions

None blocking. The pre-registered bar (net-of-cost DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧
boot_lo > 0 ∧ n ≥ MinTRL ∧ OOS Sharpe ≥ 0.7 on the `beta × beta-neutral L/S`
cell, with a ≥ 1.0 deploy-grade tier annotation) and the 2×2 grid are confirmed.
