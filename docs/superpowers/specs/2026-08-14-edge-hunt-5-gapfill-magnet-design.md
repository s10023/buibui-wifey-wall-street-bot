# Edge-hunt #5 — gap-fill "magnet" sleeve (design + pre-registration)

**Date:** 2026-08-14
**Status:** pre-registered before results were read
**Sleeve:** `analytics/gapfill/` · runner `make wifey-gapfill-audit`

## Why this hunt exists at all

The free-data edge-hunt arc was **CONCLUDED** on 2026-06-24 after six
non-positive sleeves, and the standing rule is that a #5 hunt needs an explicit
user go. **That go was given 2026-08-14** after the three remaining candidate
surfaces were put to the user (paid data / the two untested equity-native theses
/ the 3-bar horizon). This is the gap-fill half of candidate 2.

It is worth running for one reason the six failures were not: those were all
**factor** constructions (trend, cross-sectional momentum, low-beta, cross-asset
TSMOM, PEAD). This is **structural** — equities gap overnight and over weekends,
so there is a dense gap population that a 24/7 crypto tape simply does not have.
The parent only ever built gap *detection* (`cme_gap_lib`), never a de-biased
test, so there is no inherited verdict to re-litigate.

## The claim under test

Forwarded from the parent's `/ingest-x` (@DaanCrypto, re the S&P 500):

> unfilled price gaps act as intraday **magnet levels** — price tends to drift
> toward / fill nearby gaps when it trades close to them, **especially in a
> range/sideways regime**.

## Population — and what it is NOT

**Gated on breadth daily, cross-sectional** (user decision, 2026-08-14): 505 S&P
names, `1d` bars from 2018-01-02, active single-name stocks only (ETFs excluded
from the XS set, as in every sibling sleeve).

The literal claim is about the **index intraday**, and that is deliberately not
what is gated, for two reasons that were weighed before choosing:

* `4h` history starts **2024-05-16** and RTH is **2 bars/day**, so "4h intraday"
  is nearly daily anyway and the sample is ~2.2 years;
* one asset has no cross-section, so the **beta guardrail cannot fire** — and a
  positive long-only cell on correlated names in a bull market is exactly what
  faked experiment #1's residual-XS long leg.

**Consequence to state in the verdict, not to discover later:** daily bars test
*"the gap gets filled / price moves toward it over days"*, **not** *"price drifts
toward it during the session"*. A null here does not falsify the intraday claim.

## Construction — and what it mechanically is

The magnet level is the gap's **far edge**, `close[d-1]` — the price the market
must return to for the gap to be filled. An up gap leaves that edge **below**
price; a down gap leaves it **above**.

So "trade toward the nearest unfilled edge" is **short after an up gap, long
after a down gap**: a gap-fade, i.e. a **short-horizon reversal** book. This is
stated up front because it dictates the control arm — a positive result that
merely tracks plain 1-session reversal is a reversal result wearing a gap
costume, the same read as the xsmom sleeve's `corr_to_trend` +0.62.

Signal chain, every step causal:

1. `nearest_unfilled_level` — per session: create the day's gap if material,
   expire anything older than the age cap, fill-check every open gap against the
   day's own high/low, then snapshot the surviving edge nearest the close.
2. `_raw_magnet` — signed proximity in daily sigma, `+1` at the edge and decaying
   linearly to `0` at the range cap.
3. `.shift(1)` — the causality guard. The raw score reads `close[d]`, so the
   position it sizes cannot be held until `d+1`.
4. `cross_sectional_long_score` — **positive** z (high score → long). Note this
   is the opposite orientation to `lowvol.signals.cross_sectional_score`, which
   is a negative z because its inputs rank low-is-good.
5. `lowvol.signals.beta_neutral_leverage` — vol-parity sizing, short leg scaled
   so net causal portfolio beta is zero. Reused, not re-implemented.
6. `xsmom.book.run_xs_backtest(leverage=…)` — the shared cost-aware booker, so
   costs and the vol governor are identical across arms.

## Pre-registered constants (a priori — NOT swept)

| constant | value | why |
| --- | --- | --- |
| `MIN_GAP_SIGMA` | 0.5 | Materiality floor. US equities gap nearly every session; with no floor "unfilled gap" means "every bar" and the population is meaningless. |
| `MAX_DIST_SIGMA` | 2.0 | The thesis' "when it trades close" clause, as a decaying weight rather than a boolean cut. |
| `GAP_MAX_AGE` | 60 | The claim is short-horizon. A three-year-old gap is not a level, and an unbounded list is also O(n²). |
| `RANGE_WINDOW` | 20 | Sessions in the market range test. |
| `RANGE_SIGMA` | 1.0 | Sideways = the market drifted < 1σ over the window. |

**Changing any of these is a new trial and must be declared before results are
read.** The repo has just been bitten by a swept maximum (#197: an arm-level
t of +2.52 against a Bonferroni bar of 2.81), so the grid is deliberately small
and fixed.

## Arms (the whole family, declared up front)

| arm | what | role |
| --- | --- | --- |
| `broad_ls` | beta-neutral XS long/short on the magnet score | **THE GATED CELL** (`COMMITTED_KEY`) |
| `broad_ls_range` | same, positions zeroed outside the market range regime | the thesis' conditioning clause, ONE declared secondary cell — regime is **not** a swept dimension |
| `reversal_control` | plain 1-session reversal, no gap logic | the confound arm |
| `long_only` | top-quintile long-only | the deployable shape the sibling sleeves report |

All four feed the DSR deflation and the CSCV/PBO trial count.

## Gate (pre-registered)

`passes_sleeve_gate` on `broad_ls` only:

**DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0 ∧ Sharpe ≥ 0.7**, net of costs.

MinTRL is a reported stamp, **not** a leg (#166 dropped it). Deploy-grade tier =
Sharpe ≥ 1.0 ∧ long-only leg ≥ 0.7. Cost sensitivity is reported at 0 / 2 / 8 bps.

Two reads that are **not** the gate but can invalidate a cell:

* **realized beta** — the L/S arms are beta-neutralized, so a large realized beta
  means the *construction* failed its own precondition and that cell is not
  evidence about the gap premium (the `lowvol` β +3.9 and `pead` β ≈ +113
  precedent).
* **`corr_to_reversal`** — near 1 means the gap logic added nothing over plain
  short-term reversal, whatever the Sharpe.

## What a fill rate is not

The runner prints a descriptive gap population (counts, up-share, fill rates at
1/5/20/60 sessions). **A fill rate is not an edge.** A 70% fill rate with fat
losing tails is still negative expectancy; the book carries the tradeable claim
and the population table exists only so the verdict can state what it drew from.

## Freeze status

Read-only research: no DB writes, no schema change, no detector, no dispatch, no
`tp_r` / gate / threshold sweep. Outside the TA freeze on the #183 precedent (a
measurement that changes no dispatch does not touch the freeze's parameter
surface). **If it passes, shipping it is a separate decision that does touch the
freeze** — that is the user's call, not this sleeve's.

## Verdict

See `docs/audits/2026-08-14-edge-hunt-5-gapfill-magnet.md`.
