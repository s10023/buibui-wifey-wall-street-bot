# Edge-hunt #5 — gap-fill "magnet" sleeve (2026-08-14)

**Verdict: EXCLUDED. The gap-magnet direction is not merely unsupported, it is
REFUTED — cost-free, fading toward an unfilled gap edge returns Sharpe −0.460,
so in this cross-section gaps CONTINUE rather than revert. The opposite
(continuation) book is +0.392 cost-free, which is below the pre-registered 0.7
bar and is post-hoc anyway; and at this construction's ~211× daily gross
turnover even the 1bp fee alone costs ~0.9 Sharpe, so neither direction is
tradeable. Meanwhile 90.3% of material gaps DO fill within 60 sessions. The
descriptive claim is true and worth nothing: that gap is the entire finding.**

Spec + pre-registration:
`docs/superpowers/specs/2026-08-14-edge-hunt-5-gapfill-magnet-design.md`.
Runner `make wifey-gapfill-audit`. Post-hoc diagnostics
`docs/plans/scripts/gapfill_posthoc_diagnostics.py`.

This is the **seventh** equity sleeve and the seventh non-positive one. It ran
because the user explicitly reopened the concluded free-data arc on 2026-08-14
for this candidate.

## Population (descriptive — a fill rate is NOT an edge)

501 stocks, `1d`, 2018-01-02 → 2026-08, 2,163 book days.

| material gaps | up-share | filled ≤1d | ≤5d | ≤20d | ≤60d |
| --- | --- | --- | --- | --- | --- |
| 234,427 | 52.9% | 39.9% | 69.6% | 83.6% | **90.3%** |

**The thesis' descriptive half is confirmed and it is inert.** Gaps overwhelmingly
fill — 9 in 10 within 60 sessions. That fact survives contact with the data and
still produces no tradeable book, which is exactly why the spec pre-registered
"a fill rate is not an edge" *before* the numbers were read rather than
discovering it as an excuse afterwards. A 90% fill rate whose 10% tail runs
against you, on a position you must re-mark daily, is not an edge.

## The pre-registered family

| cell | days | Sharpe | DSR | PBO | boot_lo | realized β | corr→reversal |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **broad_ls** (gated) | 2163 | **−1.333** | 0.000 | 0.000 | −1.984 | −1.564 | −0.163 |
| broad_ls_range | 2163 | −1.317 | 0.000 | 0.000 | −1.943 | −0.868 | −0.154 |
| reversal_control | 2163 | −0.152 | 0.001 | 0.000 | −0.804 | +5.173 | — |
| long_only | 2163 | +0.528 | 0.101 | 0.000 | −0.119 | +44.576 | +0.048 |

(at 0 bps slippage; the committed cell reads −3.086 @2bps and −8.211 @8bps.)

**`broad_ls` FAILS all four gate legs** — Sharpe −1.333 < 0.7, DSR 0.000 < 0.95,
PBO 0.000 ≤ 0.5 passes but boot_lo −1.984 ≤ 0 fails. The verdict does not rest on
any single leg.

**The range-regime conditioning changes nothing** (−1.317 vs −1.333). The
thesis' one explicit conditioning clause — "especially in a range regime" — was
pre-registered as a declared secondary cell and it does not rescue the book.

## Signal or cost? Both, and the order matters

The runner's "0 bps" column is **not cost-free**: `run_xs_backtest` charges
`fee_pct + slippage_pct` and `fee_pct` defaults to 1bp. Against ~211× daily
gross turnover that alone is ~2%/day, which is enough to bury any signal. So the
runner's own zero column cannot answer "signal or cost" and a truly gross row was
measured post-hoc:

| costs | magnet (as pre-registered) | inverse (continuation) |
| --- | --- | --- |
| **GROSS (0 fee, 0 slip)** | **−0.460** | **+0.392** |
| 0 bps slip (1bp fee) | −1.353 | −0.494 |
| 2 bps | −3.133 | −2.262 |
| 8 bps | −8.367 | −7.465 |

**Estimator note — the two tables are not directly comparable to three decimals.**
The family table's Sharpe is `evaluate_xs`'s `sharpe_annual` over the full return
array; this table's is computed over **live rows only** (`r != 0`, `ddof=1`), which
is why the same cell reads −1.333 above and −1.353 here. The gap is the warm-up
and flat rows, it is ~0.02 throughout, and no conclusion turns on it — but quote
whichever surface you are reading from rather than mixing them.

Two conclusions, and they are different claims:

1. **The direction is refuted.** Cost-free, the magnet loses. By the standard
   this repo applied to the trend sleeve's G2 ("negative even *pre-cost*, so a
   signal failure not a cost failure"), this is a signal failure. The data's
   mild preference is for gap **continuation**, not reversion.
2. **Neither direction is tradeable.** +0.392 gross is below the 0.7 bar before
   a single basis point, and the ~211× turnover converts 1bp of fee into ~0.9
   Sharpe of drag. A book that re-marks its entire gross exposure daily cannot
   pay for itself at any plausible cost.

The continuation result is **post-hoc** — a fifth arm run after the pre-registered
four were read — and is recorded as a bounded observation, not a lead. It is
below the bar in the favourable direction with no multiplicity correction
applied; correcting would only lower it.

## What the guardrails said

**The realized-β guardrail FIRED** (−1.564 on a book pre-registered as
beta-neutral). Unlike `lowvol` (β +3.9) and `pead` (β ≈ +113), it does **not**
invalidate the read here, because the cell fails on every surviving leg
independently — but it must be reported, and the mechanism is measured rather
than assumed:

| | |
| --- | --- |
| active rows | 2,128 |
| rows where the neutralizing `k` is usable | 1,910 |
| **rows left UNTOUCHED (`k ≤ 0` or NaN)** | **218 (10.2%)** |
| median names long / short per day | 268 / 156 |
| median gross leverage per day | 202.6× |

`_beta_neutralize` leaves a day alone when the short leg is degenerate, and that
happens on 1 day in 10 here. Against 202× gross leverage a residual β of 1.5 is
~0.7% of gross, so the neutralization mostly worked and its failure mode is a
long tail of untouched days rather than a broken construction.

**The reversal control falsified my own stated mechanism, which is the useful
thing it did.** The spec argued a priori that "trade toward the nearest unfilled
edge" is *mechanically* a gap-fade and therefore short-term reversal in a
costume, and predicted a high correlation. Measured: **−0.163**. The magnet book
is very nearly uncorrelated with plain 1-session reversal — because the nearest
unfilled edge is often days old and has nothing to do with yesterday's return.
**The prediction was wrong and the control is what showed it**; had it come back
at +0.9 the whole sleeve would have been a re-run of a known factor.

**`long_only`'s +0.528 / β +44.6 is a construction artifact, not a result, and it
is shared with every sibling sleeve.** `run_xs_backtest` **sums** legs. In an L/S
book the legs offset; in a long-only book holding a median 181 names at 103×
median gross they do not, so the arm is a ~100×-levered long and its β measures
that leverage, not a gap effect. The vol governor clips at `g_max` 1.5 and cannot
scale it back. Nothing in this audit rests on that arm, and no sleeve's deploy
tier has ever bound on it because none passed the main gate first — but the
long-only leg of `lowvol`, `pead`, `xasset` and `xsmom` has the same property and
should not be read as deployable.

## What would have to change, and why it is not queued

The excluded thing is precisely a **daily-rebalanced cross-sectional proximity**
book. A hold-until-filled construction (enter once when the gap forms, exit on
fill or expiry) would cut turnover by orders of magnitude and is untested. That
is a **different sleeve, not a parameter tweak**, and running it after seeing
these numbers is exactly the swept-maximum trap #197 documented — the honest
route is a fresh pre-registration, not another arm bolted onto this one.

It is **not queued**, for a reason that is about the result and not about effort:
the direction is refuted gross. A lower-turnover version of a book whose signal
has the wrong sign inherits the wrong sign. The continuation side would be the
only candidate, and at +0.392 gross it starts below the bar.

## Caveats

1. **Daily bars cannot see intraday drift.** The thesis is an intraday claim
   about the index; this is gated on breadth daily (user decision, with the
   trade-off recorded in the spec before the run). **A null here does not
   falsify the intraday claim** — it falsifies the daily cross-sectional one.
2. **Survivorship.** The universe is the *current* S&P 500 membership pinned at
   2026-06-16, so the names are survivors. The L/S construction defuses rather
   than eliminates it; it would flatter a long-tilted book, and this book is not.
3. **Short-borrow cost is omitted**, which is mildly optimistic for the short
   legs — i.e. the true numbers are slightly worse than printed.
4. **`_GATE_SHARPE` = 0.7 is the declared and effective bar** (#166 dropped the
   MinTRL leg); MinTRL is a reported stamp only.

## Edge-question verdict

**EXCLUDED.** Gap-fill as a daily cross-sectional signal is closed: the magnet
direction is refuted cost-free, the continuation direction is below the bar
before costs, and the construction's turnover makes either untradeable. The
descriptive fill rate is confirmed at 90.3%/60d and carries no edge. This closes
the gap-fill half of the 2026-08-14 reopening; the IPO post-hype thesis (the
other half of that candidate) remains untested and is blocked on data the
universe does not contain.
