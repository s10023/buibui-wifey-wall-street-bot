# Edge-Hunt #2 — Low-Beta / BAB Long Sleeve: G-gate verdict

**Date:** 2026-06-22
**Verdict:** **FAIL** (pre-registered gate, committed `beta × beta-neutral L/S` cell)
**Audit:** `make wifey-lowvol-audit` (read-only) over the breadth universe (504
single-name stocks, 1d, 2127 sessions from 2018).
**Spec:** `docs/superpowers/specs/2026-06-22-edge-hunt-2-lowvol-bab-design.md`

## Headline

The pre-registered committed cell — the **beta-neutral betting-against-beta
long-short** book — **fails every gate condition at 0 / 2 / 8 bps**. It is
Sharpe-negative even cost-free. Edge-hunt #2 does not crack the binding
constraint ([[binding-constraint-no-equity-edge]]); four sleeves now fail (trend
G2 ≈ 0 #91, XS G3 < 0 #92, residual XS-mom #98, BAB #2).

There is an important **methodological caveat**: the realized-beta guardrail
fired (realized portfolio β **+3.9**, not the ≈0 the construct intends), so this
is a clean fail of *this construction*, not a clean isolation of the BAB premium.

## The 2×2 grid

Pre-registered gate (read on `beta_neutral_ls` only): net-of-cost DSR ≥ 0.95 ∧
PBO ≤ 0.5 ∧ boot_lo > 0 ∧ n ≥ MinTRL ∧ OOS Sharpe ≥ 0.7. Deploy-grade tier =
Sharpe ≥ 1.0 ∧ long-only leg ≥ 0.7.

| cell (@ 2 bps) | days | Sharpe | DSR | PBO | boot_lo | realized_β | alpha_t |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **beta_neutral_ls** ◀ gated | 2127 | **−0.069** | 0.028 | 0.000 | −0.703 | +3.93 | −0.41 |
| beta_long_only | 2127 | +0.650 | 0.571 | 0.000 | +0.014 | +17.50 | +0.45 |
| vol_neutral_ls | 2127 | −0.488 | 0.001 | 0.000 | −1.128 | +1.46 | −1.50 |
| vol_long_only | 2127 | +0.638 | 0.557 | 0.000 | +0.001 | +21.93 | +0.17 |

Cost sensitivity of the committed cell — monotone, negative at every level:

| bps | Sharpe | DSR | boot_lo | realized_β |
| --- | --- | --- | --- | --- |
| 0 | −0.047 | 0.033 | −0.680 | +3.93 |
| 2 | −0.069 | 0.028 | −0.703 | +3.93 |
| 8 | −0.139 | 0.016 | −0.771 | +3.92 |

(`min_trl = +inf` for the committed cell at every cost — a non-positive Sharpe has
no finite minimum track-record length, so `n ≥ MinTRL` also fails.)

## Reading the result

1. **Committed cell fails outright.** Negative Sharpe at 0 bps (−0.047) rules out
   a cost problem — it is a *signal* failure. DSR ~0.03 (≪ 0.95), boot lower bound
   strongly negative. No condition is close.

2. **The neutralization did not hold in realized terms — the guardrail fired.**
   The spec pre-declared: "the realized portfolio beta … must be ≈0 for the
   beta-neutral cells. If it is materially non-zero the neutralization is broken
   and the 'trustworthy / unconfounded' claim is void." The committed cell's
   realized β is **+3.9** (vol-ranked neutral cell +1.5). The ex-ante construct
   correctly zeroes the *causal trailing-β* weighted sum each day (proven
   deterministically in `test_beta_neutralize_*`), but on a **vol-parity-sized,
   governor-saturated 500-name book** that does **not** translate into a realized
   market-neutral book: vol-parity overweights low-vol (≈low-β) longs, the
   single-leg `k`-scaling leaves a large dollar imbalance, the 20%-vol governor
   pins at its `g_min` floor (the #92 breadth-saturation signature) so the book
   runs hot and ends up effectively a leveraged long-the-market position
   (realized β +3.9, correlation ~0.8 to the EW market). So the negative committed
   Sharpe is **not** a clean measurement of the BAB premium — it is a clean fail
   of this construction.

3. **The only positive numbers are the confounded long-only cells.** `beta_long_only`
   +0.65 and `vol_long_only` +0.64 are exactly the experiment-#1 trap — leveraged
   long-equity books on *current* S&P-500 survivors over a bull decade (realized β
   **+17.5 / +21.9**). They are below the 0.7 bar and are not the gated cell;
   their Sharpe is discarded by pre-registration discipline.

## Decision

**FAIL.** Per pre-registration the gate is read on the committed cell as-is; the
result is a clean miss and we do **not** retune-the-construction-and-rerun (that
would be selection bias — chasing a pass). The roadmap pivots to **edge-hunt #3
(cross-asset TSMOM, metals/commodity futures)**.

## Flagged leads (for a future, separately pre-registered retest — not now)

The realized-β guardrail surfaced a genuine construction lead worth its own spec
later, gated on doing it *before* looking at results again:

- **Proper beta-neutralization.** Replace the single-leg `k`-scaling with the
  Frazzini–Pedersen leg-to-β = 1 construction (long leg levered to β 1, short leg
  de-levered to β 1) **or** an explicit ex-post market hedge, and verify realized
  β ≈ 0 as an *input gate* before scoring performance.
- **Governor saturation.** The 20%-vol governor pinning at `g_min` on a 500-name
  book (also seen in #92) means the level is uncontrolled; a per-construction vol
  scale or a dollar-and-beta-balanced weighting is owed before BAB can be tested
  cleanly.
- A clean, genuinely market-neutral BAB on this universe remains an *open*
  question — this run did not answer it; it answered "this construction fails."

## Honesty note

A correction-by-construction (the realized β being far from 0) means this is a
weaker negative than #98's: #98 was a clean fail of a correctly-neutral book;
this is a fail of a book that did not achieve neutrality. Both nonetheless leave
the binding constraint standing — no trustworthy, deployable positive-Sharpe
equity sleeve yet. Short-borrow cost is omitted throughout (mildly optimistic for
the short legs); it cannot rescue a negative pre-cost Sharpe.
