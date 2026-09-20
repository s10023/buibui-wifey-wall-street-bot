# H-024 — insider routine-vs-opportunistic sleeve, phase 3 (2026-09-20)

**Verdict: EXCLUDED as a deployable sleeve — and, separately, the paper's
routine/opportunistic distinction is NOT refuted here, because the test that
would refute it is underpowered. These are two findings and collapsing them
prints the confident one. The pre-registered primary cell T1 (opportunistic
L/S, 1-month hold, ADV-weighted) returns a net Sharpe of −0.468 with DSR 0.001
and a bootstrap lower bound of −1.054, failing every gate leg except PBO. It is
negative gross as well (−0.402), so this is a signal failure rather than a cost
failure: pricing moves the cell by 0.066 Sharpe. No cell in the frozen
four-trial family passes at either cost tier. The realized β of the primary
construction is −0.167 — close enough to neutral that the negative Sharpe is not
a beta artifact — so unlike the velocity sleeve this is evidence about the
premise and not about the construction. The reversal observable fires on all
four pairs and every pair reports `measurable`, so these are funded books rather
than the all-zero artifact the three-state guard exists to catch; but
`indistinguishable` is CI-contains-zero, not an `audit_guard` powered null, and
the paired CIs run to roughly ±150 bps/mo against the paper's 82 bps/mo
headline. The arms are indistinguishable on this panel without this panel being
able to exclude the paper's effect. T4 is the cell a later session will misread:
its net Sharpe of +0.756 and boot_lo of +0.147 clear two legs, but β is +1.043,
the hedged Sharpe is +0.337, the alpha t-stat is +0.99, DSR is 0.65 against a
0.95 bar, and its own routine placebo returns +0.546 with the paired difference
spanning zero. It is market exposure, and survivorship flatters the buy leg on
top of that.**

Runner `make wifey-insider-audit` (read-only; 6m31s wall clock, 10:00:11 →
10:06:42 UTC). Pre-registration:
`docs/superpowers/specs/2026-08-29-h024-insider-routine-opportunistic-design.md`,
Amendments 1–4. Engine `analytics/insider/`.

This is the **ninth** equity sleeve and the ninth non-positive one. It is the
first **non-price** sleeve, so the TA freeze did not bind it, and it is the only
row so far whose input is a filing rather than a bar.

## The claim being tested

An insider is **routine** if she traded in the same calendar month in each of ≥3
consecutive years, **opportunistic** otherwise; the claim is that opportunistic
trades carry information and routine trades do not. The paper's headline on a
1986–2007 panel is an opportunistic L/S book at 82 bps/mo VW (t=2.15) against a
routine arm at −20 bps/mo (t=0.57). The effect size was never assumed to port —
that panel is pre-SOX, with a different filing-lag regime — so this build is an
out-of-sample test on 2018→present, which is the entire point of running it.

## Pre-registration, as frozen

Four trials, fixed before any return was read. Placebos are the routine-arm
mirror of each trial: **controls, not trials**, and they do not enter the DSR
family.

| Cell | Book | Hold |
| --- | --- | --- |
| T1 (primary) | Opportunistic buys − opportunistic sells | 1 month |
| T2 | Opportunistic buys only (long) | 1 month |
| T3 | Opportunistic L/S | 3 months |
| T4 | Opportunistic buys only (long) | 3 months |

Gate per cell: `passes_sleeve_gate` as shipped — net Sharpe ≥ `GATE_SHARPE` of
0.7, DSR ≥ 0.95, PBO ≤ 0.5, `boot_lo` > 0 — with DSR deflated at the four-trial
family. Long-only cells were pre-committed to the beta-hedged alpha t-stat
rather than the raw Sharpe, because a long-only book is market exposure plus
whatever else it has. That condition was declared before any data, on the
velocity lesson (`long_only` +0.649 gross hedged to +0.004).

Equal-weighted forms were refused in advance, trade-date formation was refused
as lookahead, and any fifth cell would be a new pre-registration rather than a
robustness check.

## Population

The run resolved 498 stocks from the 505-member universe file (ETFs excluded by
`universe.stocks()`), of which **494** carried a usable panel. Book length is
**2,190 days**.

| P/S rows | symbols | insiders | classified | routine | opportunistic |
| --- | --- | --- | --- | --- | --- |
| 340,802 | 494 | 12,172 | 133,319 (39.1%) | 90,812 | 42,507 |

Costs are the shared-base `CostModel`: half-spread 20/8/3/1 bps by trailing
20-day dollar ADV (bucket edges $5M / $50M / $500M), `impact_coef` 1, borrow
1.00%/yr. Costs are **modelled, not realised**.

## The trial family

Gross first, then net. `boot_lo` is the bootstrap lower bound; `hedged_sr` is
the beta-hedged Sharpe.

### Gross

| cell | arm | form | hold_m | sharpe | dsr | pbo | boot_lo | beta | alpha_t | hedged_sr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | oppo | ls | 1 | −0.402 | 0.002 | 0.001 | −0.988 | −0.167 | −0.97 | −0.329 |
| T2 | oppo | long | 1 | +0.437 | 0.323 | 0.001 | −0.122 | +1.061 | −0.20 | −0.068 |
| T3 | oppo | ls | 3 | −0.289 | 0.005 | 0.001 | −0.875 | −0.182 | −0.53 | −0.182 |
| T4 | oppo | long | 3 | +0.763 | 0.691 | 0.001 | +0.153 | +1.043 | +1.02 | +0.349 |
| P1 | rout | ls | 1 | −0.337 | 0.003 | 0.001 | −0.931 | −0.220 | −0.70 | −0.239 |
| P2 | rout | long | 1 | +0.354 | 0.241 | 0.001 | −0.262 | +1.087 | −0.45 | −0.153 |
| P3 | rout | ls | 3 | −0.307 | 0.004 | 0.001 | −0.923 | −0.272 | −0.46 | −0.157 |
| P4 | rout | long | 3 | +0.553 | 0.453 | 0.001 | −0.047 | +1.012 | +0.13 | +0.044 |

### Net

| cell | arm | form | hold_m | sharpe | dsr | pbo | boot_lo | beta | alpha_t | hedged_sr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | oppo | ls | 1 | −0.468 | 0.001 | 0.000 | −1.054 | −0.167 | −1.16 | −0.396 |
| T2 | oppo | long | 1 | +0.418 | 0.272 | 0.000 | −0.140 | +1.060 | −0.26 | −0.090 |
| T3 | oppo | ls | 3 | −0.347 | 0.002 | 0.000 | −0.935 | −0.182 | −0.70 | −0.240 |
| T4 | oppo | long | 3 | +0.756 | 0.652 | 0.000 | +0.147 | +1.043 | +0.99 | +0.337 |
| P1 | rout | ls | 1 | −0.397 | 0.001 | 0.000 | −0.990 | −0.220 | −0.88 | −0.300 |
| P2 | rout | long | 1 | +0.337 | 0.200 | 0.000 | −0.278 | +1.086 | −0.52 | −0.176 |
| P3 | rout | ls | 3 | −0.358 | 0.002 | 0.000 | −0.975 | −0.272 | −0.62 | −0.210 |
| P4 | rout | long | 3 | +0.546 | 0.410 | 0.000 | −0.053 | +1.012 | +0.10 | +0.034 |

**Cost is not the binding constraint.** T1 moves −0.402 → −0.468, i.e. 0.066
Sharpe, and it is already negative before a basis point is charged. This is the
same shape as the EWMAC trend sleeve's G2 failure and the opposite of the
gap-fill sleeve, where turnover alone cost ~0.9 Sharpe.

## The reversal observable — measurable, and NOT a powered null

The paired difference is trial minus its routine placebo, in bps/mo, block
bootstrapped on the paired series.

| trial | placebo | diff (gross) | CI (gross) | diff (net) | CI (net) |
| --- | --- | --- | --- | --- | --- |
| T1 | P1 | −16.9 | [−167.7, +135.5] | −18.5 | [−169.4, +134.1] |
| T2 | P2 | +20.0 | [−109.5, +156.8] | +19.3 | [−110.1, +156.0] |
| T3 | P3 | +7.3 | [−116.1, +132.7] | +6.9 | [−116.5, +132.2] |
| T4 | P4 | +41.7 | [−63.9, +149.8] | +41.6 | [−64.1, +149.7] |

Every pair reports `measurable = True`. That matters: `PairedDifference` has
**three** states, not two, and two never-funded books would give an all-zero
difference with a CI of `[0, 0]` that satisfies "indistinguishable" while
establishing nothing. These are real books.

⚠ **But `indistinguishable` is `measurable AND lo ≤ 0 ≤ hi` — a
CI-contains-zero test, not an `audit_guard` powered null**, which would require
the CI to sit strictly inside a ±bar. The CIs here are roughly ±150 bps/mo wide
against a paper headline of 82 bps/mo, so the interval comfortably contains both
zero and the effect being tested. The honest reading is that **this panel cannot
separate the two arms**, not that the two arms are known to be the same. A
future session reading "the reversal observable fired" as "opportunistic vs
routine is dead" would be reading a confident conclusion out of an underpowered
one — the exact error the `audit_guard` warning-value audit was written about.

## What the guardrails said

**The primary construction is near-neutral.** Realized β on T1 is **−0.167** at
both cost tiers. No numeric threshold is coded for this guardrail — the value is
reported and the house rule is applied by judgement — but for calibration it
fired at +3.9 on `lowvol`, −1.646 on `velocity` and ≈ +113 on `pead`, and held
at −0.083 on `xasset`. At −0.167 the L/S book is close enough to neutral that
T1's negative Sharpe is not a beta artifact. **This cell is therefore usable
evidence about the premise**, which is more than the velocity sleeve could say
about its own headline number.

**Both long-only cells are beta.** T2 reads `MARKET BETA, not edge` outright
(alpha t −0.20 gross, −0.26 net). T4 carries a positive alpha t but it is +0.99,
short of even a one-sided 95% bar before any family correction.

## ⚠ The cell that will be misread: T4

T4 is the only cell in the family whose headline numbers clear anything, and it
will be quoted out of context unless this section is read with it. Net Sharpe
**+0.756** is above `GATE_SHARPE`, and `boot_lo` **+0.147** is above zero. It
still fails, on five separate grounds:

- **DSR is 0.652** against a 0.95 bar, deflated at the four-trial family.
- **β is +1.043** — the book is fully market-exposed by construction.
- **Hedged Sharpe is +0.337**, less than half the bar, so the market leg is
  where the return lives.
- **Alpha t is +0.99**, not significant, and the spec pre-committed to reading
  exactly this number for long-only cells rather than the raw Sharpe.
- **Its own routine placebo P4 returns +0.546** with the paired difference at
  +41.6 bps/mo on a CI of [−64.1, +149.7] — so T4 is not even distinguishable
  from the arm the hypothesis says carries no information.

On top of all five, survivorship runs **in opposite directions by leg** on this
panel (`delisted: 0` of 505 members): the buy legs are flattered because a
bought name that went to zero is simply absent. A long-only pass here would have
been an upper bound even if it had passed.

## Two panel figures, available for the first time

**Routine share is 68.1%** (90,812 routine against 42,507 opportunistic). The
figure previously in circulation was **74.0%**, and the standing instruction was
never to quote it as a panel figure because it came from six technology
mega-caps. That caveat can now be retired with a real number: the panel is
68.1%, still above the paper's ~55%, and the 10b5-1-era explanation for the gap
remains an untested hypothesis rather than a finding.

**Classification reaches 39.1% of the population** (133,319 of 340,802
purchase/sale rows). Unclassifiable insiders are excluded by the paper's own
rule — a trader needs ≥1 open-market trade in each of the three preceding years
to be classifiable at all — so this is expected rather than a parser defect, and
the 96.8% parse coverage measured at backfill is the separate quantity that
cleared reversal observable (b). It does bound what the split can claim: the
verdict above rests on the 39% of rows that carry a label.

## Caveats

- **Weights are trailing dollar ADV, not market cap.** No market-cap or
  shares-outstanding series exists anywhere in this repo, so Amendment 4 booked
  the spec's "value-weighted by market cap" on ADV instead. ADV/mcap is
  turnover, so this is a **liquidity weight**, and the book tilts toward
  high-turnover names. Any comparison to the paper's 82 bps/mo must carry that.
  This was an operator ruling with the options presented, declared before any
  return.
- **Costs are applied in return space**, not in R: `cost_breakdown` is
  trade-shaped and a weight book has no entry or stop. `replay` raises on an
  absent or disabled `[backtest.cost_model]` rather than booking gross and
  labelling it net.
- **Formation reads `filing_date`**, falling back to `acceptance_ts`; a row with
  neither is dropped rather than dated from its trade.
- **Costs are modelled, not realised**, and the short-leg borrow figure is the
  cost model's own rather than anything the paper states.

## What would have to change, and why it is not queued

The reversal observable is underpowered rather than negative, so the arithmetic
question "would more data separate the arms?" is open. It is **not queued**, for
two reasons that are independent of each other.

First, the primary cell is not marginal. T1 is negative at both cost tiers with
`boot_lo` −1.054; separating the arms more precisely would establish that the
opportunistic arm is reliably *worse*, which is not a deployable finding. The
underpowered leg concerns the paper's premise, not this sleeve's viability, and
only the premise would benefit.

Second, breadth does not buy power on this panel. The universe carries `n_eff` ≈
2.96 independent series at `1d`, and `n_eff → 1/rho` as names are added, so
adding constituents does not widen the effective cross-section. The lever that
would work is **more history**, not more names, and the panel already starts at
the earliest date the three-year classification lookback permits given a 2015
Form 4 fetch.

Reopening this row therefore needs an explicit user go and a stated lever, per
the free-data arc's per-candidate rule. It does not reopen on the strength of
T4.
