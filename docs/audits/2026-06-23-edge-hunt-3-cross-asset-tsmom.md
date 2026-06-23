# Edge-Hunt #3 — Cross-Asset TSMOM: gate verdict

**Date:** 2026-06-23
**Verdict:** **FAIL** (pre-registered gate, committed `broad × long-short` cell)
**Audit:** `make wifey-xasset-audit` (read-only) over the frozen 13-ETF cross-asset
basket (1d, 4858 sessions from 2007-03-01).
**Spec:** `docs/superpowers/specs/2026-06-22-edge-hunt-3-cross-asset-tsmom-design.md`

## Headline

The pre-registered committed cell — the **broad cross-asset, signed long-short
TSMOM** book — **fails the gate at 0 / 2 / 8 bps**. Its Sharpe is weakly positive
cost-free (+0.41) but never approaches the 0.7 bar, PBO is ~0.79 (≫ 0.5), the
bootstrap lower bound turns negative by 2 bps, and MinTRL is infinite. This
edge-hunt #3 does not crack the binding constraint
([[binding-constraint-no-equity-edge]]); **five sleeves now fail** (trend G2 ≈ 0
in #91, XS G3 < 0 in #92, residual XS-mom #98, BAB #100, cross-asset TSMOM #3).

Unlike #2, this is a **clean** fail: the equity-beta guardrail **held** — the
committed book's realized β to SPY is **−0.083 ≈ 0**, exactly as the
diversification thesis predicted. The construction did what it was designed to do
(it genuinely diversifies away from the US-equity beta that confounded the prior
four sleeves); the **cross-asset TSMOM premium itself is simply too weak**, net of
cost, in the free-ETF proxy set to clear a disciplined gate. That makes this a
*stronger* negative than #2's (which failed its own neutralization), comparable
to the #98 clean fail.

## The 2×2 grid

Pre-registered gate (read on `broad_ls` only): net-of-cost DSR ≥ 0.95 ∧ PBO ≤ 0.5
∧ boot_lo > 0 ∧ n ≥ MinTRL ∧ OOS Sharpe ≥ 0.7. Deploy-grade tier = Sharpe ≥ 1.0 ∧
long-flat leg ≥ 0.7.

| cell (@ 2 bps) | days | Sharpe | DSR | PBO | boot_lo | equity_β |
| --- | --- | --- | --- | --- | --- | --- |
| **broad_ls** ◀ gated | 4858 | **+0.358** | 0.879 | 0.765 | −0.033 | −0.083 |
| broad_long | 4858 | +0.529 | 0.973 | 0.765 | +0.141 | +0.151 |
| commodity_ls | 4858 | +0.455 | 0.945 | 0.765 | −0.004 | −0.141 |
| commodity_long | 4858 | +0.550 | 0.978 | 0.765 | +0.083 | +0.077 |

Cost sensitivity of the committed cell — monotone decay, gate never met:

| bps | Sharpe | DSR | PBO | boot_lo | equity_β |
| --- | --- | --- | --- | --- | --- |
| 0 | +0.409 | 0.925 | 0.794 | +0.019 | −0.083 |
| 2 | +0.358 | 0.879 | 0.765 | −0.033 | −0.083 |
| 8 | +0.203 | 0.630 | 0.610 | −0.192 | −0.083 |

(`min_trl = +inf` for the committed cell at every cost — its daily Sharpe sits
below the 1.0-annual target, so there is no finite minimum track-record length and
`n ≥ MinTRL` also fails.)

## Reading the result

1. **Committed cell misses on multiple conditions.** Sharpe +0.41 cost-free is the
   ceiling and is far under 0.7; PBO ~0.79 says the in-sample-best of the four
   books does not persist out-of-sample; the bootstrap lower bound is barely
   positive cost-free (+0.019) and goes negative by 2 bps; MinTRL is infinite. The
   weak-but-positive cost-free Sharpe and boot_lo show a *faint* real trend signal
   exists — it is nowhere near a deployable, robust edge and it decays quickly with
   cost.

2. **The guardrail held — the test was clean.** The spec pre-declared that the
   committed cross-asset book should read realized β ≈ 0; it came back **−0.083**
   (the commodity-only L/S cell −0.141, both long-flat cells small-positive from
   their deliberate long bias). So the negative verdict is a clean measurement of
   the *premium*, not an artifact of a broken construction. This is the opposite of
   #2, whose realized β fired at +3.9.

3. **PBO ~0.79 is uniform and damning for the 2×2.** Across all cost levels the
   probability of backtest overfitting sits near 0.8 — the four-book ranking does
   not hold up under CSCV. (With only four trials the metric is coarse, but a value
   this high is unambiguous: no robust cell selection.)

4. **The higher-Sharpe cells are the long-bias confound, again.** `broad_long`
   (+0.53) and `commodity_long` (+0.55) edge out the signed books, but they carry
   *positive* equity β (+0.15 / +0.08) — the long-only tilt simply re-absorbs some
   market drift — and they still miss 0.7. They are not the gated cell; their
   Sharpe is discarded by pre-registration discipline.

## Decision

**FAIL.** Per pre-registration the gate is read on the committed `broad_ls` cell
as-is; the result is a clean miss and we do **not** retune-and-rerun (that would be
selection bias — chasing a pass). The binding constraint stands. The roadmap moves
to **edge-hunt #4 (PEAD-lite)** — and #3's clean negative materially sharpens the
**honest-exit** question (#1–#4 all failing → trigger the $29/mo Polygon upgrade or
accept that a free-data US-investable edge needs paid event / flow data).

## Why "free-ETF TSMOM fails" is not "TSMOM is dead"

Cross-asset time-series momentum is well documented at a far higher diversified
Sharpe in the academic *futures* literature (Moskowitz–Ooi–Pedersen ~1.0 over six
decades across ~58 markets). The gap to our +0.4 is plausibly structural and
*expected* under the free-data constraint, not a refutation of the anomaly:

- **ETF proxies, not futures** — USO contango / roll drag and ETF tracking error
  bleed the very commodity legs that carry much of the TSMOM premium.
- **13 instruments, not ~58** — a thin basket gives the portfolio vol governor far
  less diversification to harvest; per-asset-class trend is noisier.
- **2007–2025 window** — includes the widely-noted post-2011 managed-futures /
  trend drought, which depresses any in-sample TSMOM Sharpe.

So this is a faithful negative for *what we can actually trade for free*, and a
clean one (β ≈ 0). It is also the cleanest signpost yet toward the honest-exit
decision: the next failure does not need a new construction so much as a verdict on
whether to pay for futures-grade breadth (a separately pre-registered question, not
a retune of this cell).

## Honesty note

The realized β ≈ 0 means this is a **trustworthy** fail — the book was genuinely
market-neutral / diversified, so we measured the premium itself, not a confounded
proxy. Short-borrow cost on the L/S legs and ETF-vs-futures roll are omitted (mildly
optimistic); neither can rescue a +0.4 cost-free Sharpe that fails on PBO and the
Sharpe floor. The binding constraint is unchanged: no trustworthy, deployable
positive-Sharpe equity (or cross-asset, free-data) sleeve yet.
