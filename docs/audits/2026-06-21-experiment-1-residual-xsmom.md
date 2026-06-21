# Experiment #1 — Residualized XS-Momentum: Verdict (FAIL)

**Date:** 2026-06-21
**Sleeve:** `analytics/xsmom/residual.py` (residual/skip-month cross-sectional
momentum), audited via `tools/xsmom_residual_audit.py`
(`make wifey-xsmom-residual-audit`).
**Spec:** `docs/superpowers/specs/2026-06-21-equity-edge-hunt-roadmap-residual-xsmom-design.md`
**Plan:** `docs/superpowers/plans/2026-06-21-residual-xsmom-experiment-1.md`

## TL;DR

The pre-registered gate **FAILS cleanly at every cost level.** The committed
`broad × residual+skip` long-short cell is net-negative-to-marginal
(Sharpe +0.15 @2bps, DSR 0.44, bootstrap lower bound below zero). Per the
pre-registration discipline the gate is read on that cell only, and it does not
clear. Equity cross-sectional momentum is **not** recoverable into a deployable
dollar-neutral sleeve by residualization + skip-month + breadth.

Two things were learned, neither of which rescues the gate:

1. **Construction was the dominant lever.** Residualization + slow-only speeds
   flips the headline from negative to mildly positive
   (`broad_raw` −0.127 → `broad_residual_skip` +0.204 @0bps). The original G3
   failure (−0.156, raw construction) was therefore **partly a construction
   artifact** — but the recovered signal sits far below the +0.7 bar and is
   erased by costs.
2. **Breadth was ~neutral.** Going from the 101-name mega arm to the 504-name
   broad arm barely moved the residual+skip Sharpe (+0.230 → +0.204 @0bps), so
   the prior failure was not a small-universe artifact and broadening did not
   unlock alpha.

## Pre-registered gate (fixed before any result)

Net-of-cost, read on the `broad_residual_skip` cell only:

`DSR ≥ 0.95  ∧  PBO ≤ 0.5  ∧  boot_lo > 0  ∧  n_obs ≥ MinTRL  ∧  Sharpe ≥ 0.7`

## The 2×2 grid

Universe: 504 active S&P 500 single-name stocks (mega arm = 101 pre-expansion
S&P-100 survivors); 1d bars, 2127 sessions from 2018-01-02; P&L booked on actual
closes for every cell; annualization 252 NYSE sessions.

### @ 2 bps slippage (headline realistic cost)

| cell | days | Sharpe | DSR | PBO | boot_lo | MinTRL | corr_to_trend |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mega_raw | 2127 | −0.136 | 0.166 | 0.242 | −0.678 | ∞ | +0.620 |
| mega_residual_skip | 2127 | +0.173 | 0.471 | 0.242 | −0.440 | ∞ | +0.445 |
| broad_raw | 2127 | −0.190 | 0.130 | 0.242 | −0.734 | ∞ | +0.601 |
| **broad_residual_skip** (committed) | 2127 | **+0.147** | **0.441** | **0.242** | **−0.449** | **∞** | +0.416 |

Committed cell @2bps: **FAIL** (Sharpe 0.147 < 0.70; DSR 0.44 < 0.95;
boot_lo < 0; n < MinTRL).

### Cost sensitivity — committed cell across bps

| slippage | Sharpe | DSR | boot_lo | verdict |
| --- | --- | --- | --- | --- |
| 0 bps | +0.204 | 0.510 | −0.394 | FAIL |
| 2 bps | +0.147 | 0.441 | −0.449 | FAIL |
| 8 bps | −0.018 | 0.256 | −0.610 | FAIL |

The residual+skip construction is gross-positive but cost-fragile: it decays to
~zero by 8 bps. This is a **signal** failure (the gross edge is too thin to clear
the bar), compounded by a cost failure (what little edge exists does not survive
realistic slippage).

## The long-only leg (reported, not gated)

Long-only top-quintile residual momentum (the "wife-sleeve" form), Sharpe on the
committed `broad × residual+skip` config:

| slippage | Sharpe |
| --- | --- |
| 0 bps | +0.910 |
| 2 bps | +0.882 |
| 8 bps | +0.797 |

This is the only number in the experiment that clears 0.7, and it is
cost-robust. **It is not trusted, for three reasons:**

1. **Not the pre-registered cell.** The gate is the dollar-neutral L/S
   `broad_residual_skip`; the long-only leg is an exploratory secondary report.
   Reading the verdict off it would be exactly the post-hoc gate-moving the
   research guards exist to prevent.
2. **Maximal survivorship exposure.** The broad arm is the *current* S&P 500
   (survivors). A long-only book on survivor names over a bull decade is the
   single construction in which survivorship bias inflates returns the most —
   the audit footer flags this explicitly ("a marginal pass is suspect; a clean
   fail is trustworthy"). The dollar-neutral L/S book is far less exposed
   (longs and shorts both drawn from survivors) and it cleanly fails. The
   trustworthy reads say no edge; the suspect read is the one survivorship would
   fabricate.
3. **Beta, not alpha.** The leg carries no overfitting deflation (raw Sharpe
   only), and a long-only momentum book in 2018–2026 is dominated by long-equity
   beta, not a market-neutral edge.

## Method / data notes

- Universe expanded 105 → 508 members (504 stocks + 4 ETFs) tracking the current
  S&P 500; ETFs excluded from the cross-section. Backfill: 1d + 1wk from 2018,
  1016 timeframe-loads, **1 row quarantined** (HUBB 1d), 0 errors; coverage
  508/508. 4h intentionally skipped (unused by the daily XS sleeve).
- Residual returns = `r_i − β_i · r_mkt` with a causal trailing 252-session beta
  vs the equal-weight market; momentum is EWMAC on the synthetic residual price.
  Skip-month = slow speeds only `((16,64),(32,128),(64,256))`. Sector-neutral
  demean within GICS sectors. Sizing on **actual** return vol (positions target
  real vol while the signal is residual).
- DSR/PBO/bootstrap/MinTRL via `analytics.research_guards`; the 4-book family is
  the honest multiple-testing set. Short-borrow cost omitted (mildly optimistic
  short legs).
- Additive / default-off: `run_xs_backtest(leverage=None)` is byte-identical to
  the prior book; regression goldens unmoved (both configs PASS).

## Roadmap consequence

Per the spec, a clean fail on the committed gate pivots the edge-hunt to **#2
(low-vol / BAB long sleeve)**. The binding constraint — *wifey has no
positive-Sharpe equity edge* — **still stands**: no trustworthy, deployable
positive-Sharpe sleeve emerged.

The experiment was not wasted: it isolated **construction (not asset class, not
breadth)** as the lever that matters for equity XS-momentum, and it surfaced one
honest lead — **long-only residual momentum** — that is currently confounded by
survivorship and beta. Before discarding it entirely, retest the long-only leg on
a **survivorship-free / point-in-time** universe with an explicit market-beta
hedge; if it survives both, it becomes a candidate sleeve. That retest is
gated behind a PIT membership feed wifey does not yet have, so it is a backlog
item, not a now-task.
