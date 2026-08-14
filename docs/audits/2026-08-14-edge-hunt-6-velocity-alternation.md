# Edge-hunt #6 — velocity-alternation sleeve (2026-08-14)

**Verdict: EXCLUDED — and unlike edge-hunt #5 this is a NULL, not a refutation.
The committed `broad_ls` cell fails all four gate legs at every cost tier, but
its headline Sharpe is contaminated: the beta guardrail FIRED at −1.646, and
once market exposure is stripped the sleeve returns a beta-hedged Sharpe of
−0.169 with an alpha t-stat of −0.49 — no effect distinguishable from zero in
EITHER direction, so the post-hoc inverse is not available here the way it was
for the gap magnet. The decomposition is the actual finding: `velocity` is
`depth / duration`, and the thesis arm correlates +0.499 with the depth control
and +0.546 with the duration control while performing indistinguishably from
depth alone (−0.265 vs −0.279). The ratio isolates nothing its two components
did not already contain. Cost is NOT the binding constraint — at 24.5× daily
gross turnover (8.6× lower than #5's ~211×) the signal is already absent
gross.**

Runner `make wifey-velocity-audit`. Pre-registration lives in
`analytics/velocity/signals.py`'s module docstring. Beta-failure diagnostic
`docs/plans/scripts/velocity_beta_neutralize_failure.py`.

This is the **eighth** equity sleeve and the eighth non-positive one. It ran
because the user reopened the concluded free-data arc per-candidate on
2026-08-14, selecting the thesis-inbox candidates over paid data.

## The thesis and what makes it falsifiable

H-007 (`docs/plans/thesis-inbox.md`, @fenggemeigu 2026-07-05): *the pace of a
decline predicts the pace of the next move — a slow grind lower tends to be
followed by a sharp rally, a sharp drop by a slow recovery.*

Both halves predict a recovery and differ only in its **pace**, so over a fixed
forward horizon the cross-sectional bet is **long the slow decliners, short the
fast ones** — long low velocity.

**The decomposition is the whole test.** `velocity = depth / duration`, so a low
velocity arises from a shallow decline or a long one, and those are two already
known effects (short-term reversal/momentum, and a calendar effect). H-007's only
novel claim is that the **ratio** carries information its components do not. The
family therefore books `depth` and `duration` as separate control arms **on the
identical eligibility population** — a design constraint enforced by
`tests/test_velocity_signals.py::TestSharedPopulation`, because controls drawn
from a wider population would compare two different experiments.

## Pre-registration

Fixed in `analytics/velocity/signals.py` before the universe run and unchanged
after it:

| Parameter | Value |
| --- | --- |
| `LOOKBACK` | 60 sessions (formation window) |
| `MIN_DEPTH` | 0.10 (≥10% off the window peak) |
| `MIN_DURATION` | 3 sessions |
| committed cell | `broad_ls` (`replay.COMMITTED_KEY`) |
| gate | DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0 ∧ Sharpe ≥ 0.7 |

**Honest caveat on the pre-registration.** A 10-symbol smoke run was executed
between writing the constants and the 505-name run, and it showed `broad_ls` at
−0.417. No constant was changed after seeing it, but the sequence is recorded
here rather than claimed away — a pre-registration is only worth what its
timeline says.

## Population (counts — deliberately not a rate)

501 stocks (`universe.stocks()`, ETFs excluded), `1d`, 2,163 book days.

| symbols | sessions | sessions with a book | eligible name-days | median names/session | max |
| --- | --- | --- | --- | --- | --- |
| 501 | 2,163 | 2,103 | 313,534 | 124 | 481 |

These are **counts, not rates, on purpose.** Edge-hunt #5 shipped "90.3% of
material gaps fill within 60 sessions" and it survived review because it was
true; a matched placebo filled 88.9%, so the real content was +1.5pp. A rate over
a horizon needs a matched null before it means anything, so this sleeve does not
quote one.

## Results

`gross` is `fee_pct = 0` **and that is the point** — every sibling sleeve's audit
prints a "0 bps" column that still bills 1bp, because `run_xs_backtest` charges
`cfg.fee_pct + cfg.slippage_pct` and `fee_pct` defaults to 1bp (#198). Only a
true zero-cost row separates "the signal is absent" from "the costs ate it".

### @ gross (fee = 0, slip = 0)

| cell | sharpe | dsr | pbo | boot_lo | realized β | **hedged sharpe** | **alpha t** | corr depth | corr duration | turnover |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **broad_ls** | −0.265 | 0.017 | 0.030 | −0.863 | −1.646 | **−0.169** | **−0.49** | +0.499 | +0.546 | 24.5 |
| depth_control | −0.279 | 0.016 | 0.030 | −0.901 | −1.539 | −0.208 | −0.60 | — | −0.285 | 28.7 |
| duration_control | +0.066 | 0.126 | 0.030 | −0.556 | −0.816 | +0.119 | +0.34 | −0.285 | — | 24.0 |
| long_only | +0.649 | 0.713 | 0.030 | +0.046 | +12.003 | +0.004 | +0.01 | +0.055 | +0.047 | 13.2 |

Committed cell: **FAIL** at gross, at live (2bps slip + 1bp fee, Sharpe −0.545)
and at stressed (8bps + 1bp, −1.104).

## Reading it

**1. The beta guardrail fired, so read the hedged column.** At realized β −1.646
the L/S book was substantially short the market across a rising sample, and a
book left short a rising market bleeds in a way that looks exactly like a refuted
signal. Per the `lowvol` / `pead` precedent a fired guardrail means the
*construction* failed its own neutrality precondition, so the raw Sharpe is not
evidence about the premise. The hedged Sharpe (−0.169) and alpha t (−0.49) are,
and they say **nothing is there** — which is why this is EXCLUDED as a null and
**not** a direction refutation. The post-hoc inverse (+0.169 hedged) is not a
lead: it is below the 0.7 bar by a factor of four and it is post-hoc.

**2. Why the neutralization failed — measured, and it is NOT #198's mechanism.**
`_beta_neutralize` has a documented escape hatch (a day with no valid short leg,
`k ≤ 0`, or `k` NaN is left untouched), and #198 measured it at 10.2% of active
days in the gapfill sleeve and named it the cause there. Here it accounts for
**193 of 2,103 active days (9.2%), every one of them a causal-beta warm-up day
with no short leg at all** — and on the days the scaling *did* apply, the
**post-neutralization ex-ante net beta is exactly +0.000** (median `k` 1.296).
So the construction neutralizes its own causal beta estimates perfectly and still
realizes −1.646: **the causal betas do not describe the realized period.** That
is an ex-ante/ex-post estimation gap, not the hatch. The same shape of error
would afflict any sleeve using `causal_betas` for neutralization, which is worth
knowing before the next one is built.

**3. The decomposition is the finding.** The thesis arm sits at +0.499 / +0.546
correlation to its two components and delivers a Sharpe (−0.265) statistically
indistinguishable from the depth control's (−0.279). The ratio did not isolate a
novel effect; it re-expressed a blend of two known ones. `duration_control` is
the only arm non-negative gross (+0.066 raw, +0.119 hedged, alpha t +0.34) and it
too is a null far below the bar.

**4. Cost is not the binding constraint, unlike #5.** 24.5× daily gross turnover
is 8.6× lower than the gap magnet's ~211×, and the gross column is already
non-positive. The 3bps live tier costs ~0.28 Sharpe, so costs *worsen* a
non-result rather than creating one. A lower-turnover reformulation cannot
rescue a signal that is absent before any fee.

## ⚠ Cross-sleeve finding: `long_only`'s Sharpe is 100% market beta

`long_only` prints **+0.649 gross Sharpe, DSR 0.713, boot_lo +0.046** — by far
the best-looking cell in the table, and the only one that passes a bootstrap
lower bound. It is **entirely market exposure**: realized β **+12.003**, and the
beta-hedged Sharpe is **+0.004** with alpha t **+0.01**. Not small — zero.

Edge-hunt #5 established that the long-only arm is a construction artifact in
every sleeve (`run_xs_backtest` SUMS legs, which offsets in L/S and does not in
long-only). This sleeve gives that claim a sharper statistic: the artifact is not
merely "beta-contaminated", it is **beta and nothing else**.

**This has a consequence nobody has acted on.** `deploy_grade` is defined as
`passed ∧ committed Sharpe ≥ 1.0 ∧ long_only Sharpe ≥ 0.7` — so one of the three
legs of the deploy tier is a market-exposure reading, in a shared gate read by
five sleeve reports (`gapfill`, `lowvol`, `pead`, `xasset`, and this one —
`xsmom` does not compute a deploy tier). No recorded verdict rests on it, because
`deploy_grade` is only consulted on a cell that already cleared `passed` and no
sleeve ever has, so this is latent rather than live. **Changing it is
a gate change and therefore a user call** — flagged here, not fixed.

## What is and is not excluded

**Excluded:** a daily-rebalanced cross-sectional book ranking on
`depth / duration` over a 60-session formation window, with ≥10% / ≥3-session
eligibility floors, on the free 1d equity cross-section.

**Not tested, and not queued:** a *time-series* form (does an individual name's
own slow decline predict its own sharp rally?), which is the shape the pundit
actually described — they were talking about one asset's gold drawdown, not a
cross-section. The cross-sectional null lowers the prior but does not close it,
and a per-name event study is a different measurement. **Do not start it without
an explicit user go** — the arc conclusion still stands and this reopening was
per-candidate.

**H-007's status** in the SoT hypothesis inbox moves from `new` to **tested,
cross-sectional form EXCLUDED as a null**.
