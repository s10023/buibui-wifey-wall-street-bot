# Honest-Exit Synthesis — the free-data equity edge-hunt arc is concluded

**Date:** 2026-06-24. **Trigger:** edge-hunt #4 (PEAD-lite, PR #104) FAIL — the
fourth and last roadmap free-data family. **Decision (with the user):** accept the
pre-registered honest-exit criterion and **re-scope**. This note is the conclusion
of the arc; it does not start a new experiment.

## What was tested

Six self-contained, additive, read-only sleeves, each pre-registered (gate fixed
before any number existed) and scored on the same net-of-cost guard stack
(DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ bootstrap-CI lower bound > 0 ∧ n ≥ MinTRL ∧
Sharpe ≥ 0.7), read on a dollar-neutral / market-neutral committed cell:

| # | sleeve | PR | committed-cell verdict (@2 bps) | guardrail |
| --- | --- | --- | --- | --- |
| — | forecast trend (G2) | #91 | FAIL, Sharpe −0.05 | — |
| — | XS-momentum (G3) | #92 | FAIL, −0.156 | — |
| 1 | residual XS-mom | #98 | FAIL, +0.15 (DSR 0.44, boot_lo<0) | clean |
| 2 | low-beta / BAB | #100 | FAIL, −0.069 | **β fired (+3.9)** |
| 3 | cross-asset TSMOM | #102 | FAIL, +0.36 | **β held (−0.08, clean)** |
| 4 | PEAD-lite | #104 | FAIL, +0.10 | **β fired (+113); mega arm clean, −0.53** |

All six are non-positive. None cleared the gate. The full per-sleeve narrative
lives in each `docs/audits/2026-06-*.md` and in the project memory
`binding-constraint-no-equity-edge`.

## What the evidence actually establishes

The fails split cleanly into two kinds, and that split is the whole finding:

1. **Clean nulls (the interpretable tests): the premia are genuinely weak net of
   cost on free data.**
   - Residual XS-mom (#1) failed clean — construction (residual + skip-month) was
     the dominant lever and flipped the raw-G3 sign, but the recovered gross edge
     was far below the bar and erased by cost; broadening to the S&P 500 did not
     unlock alpha (breadth ≈ neutral).
   - Cross-asset TSMOM (#3) failed with the equity-β guardrail **held** (β ≈ 0):
     the construction diversified exactly as designed, so we measured the premium
     itself — too weak net of ETF-proxy cost.
   - PEAD-lite's **mega arm** (#4) was a properly-neutral test (β −0.40) and came
     back **negative (−0.53)**: no post-earnings drift in liquid US large-caps net
     of cost — exactly where the literature expects the anomaly weakest.

2. **Construction-debt cells (broken instruments, not clean tests):** BAB (#2) and
   PEAD-broad (#4) both had the realized-β guardrail **fire** (β +3.9, +113) —
   a **vol-governor-saturation pathology** on sparse / vol-parity books (the
   governor, floored at 0.5×, cannot de-lever a hugely-levered sparse cohort, so
   the "dollar-neutral" book is neither neutral nor vol-controlled). These are not
   clean refutations of their premia; they are bugs in the neutralization that a
   future construction would have to fix to test cleanly.

The recurring confounder behind every *positive-looking* number was the same:
**long-only books on the current-S&P-500 survivors over a bull decade** (residual
+0.88, BAB +17.5 β, PEAD +1204 β). Survivorship + bull-market drift, not edge.

**Bottom line:** on a free-data, US-large-cap, price/event universe, a
trustworthy positive-Sharpe sleeve net of honest cost did not appear — and the
clean arms say the premia themselves (not just our constructions) are too weak
there. Nothing downstream (the built-and-portable sizing / portfolio stack) can
rescue this: you cannot vol-target out of a non-positive signal.

## Why we stop here (and why this is the disciplined move)

The roadmap pre-registered the stopping rule: *"if #1–#4 all fail clean, that is a
decisive finding."* We hit it. Continuing to dig for a seventh free-data edge is
exactly the behaviour a pre-registered stopping rule exists to prevent. Two of the
four edge-hunt cells were construction-debt rather than clean — but the **matched
clean arms** (cross-asset TSMOM clean; PEAD mega clean-negative) point the same way,
so the construction fix is ~90 % a closure exercise with low edge-EV. We therefore
**document it, and do not run it now.**

## Dormant options (preserved, not executed)

Available whenever the user chooses to revisit — each a **fresh pre-registration**,
never a retune of a failed cell:

1. **Pay for data (~$29/mo Polygon or equivalent).** The genuine unlock. Attacks
   the exact confounds the free-data arc could not escape:
   - **PIT / survivorship-free universe** → removes the long-only β-artifact that
     faked every positive number (#1/#2/#4).
   - **Small-cap / Russell-2000** → PEAD's textbook home (the drift lives in
     small, less-covered names this universe can't reach).
   - **Analyst-consensus SUE** → a stronger surprise than the free seasonal-RW SUE.
   - **Futures-grade cross-asset TSMOM** → #3 was a clean β≈0 null, so it is the
     *premium*, not the construction, that futures-grade breadth would retest.
2. **Construction-debt fix (free, low-EV closure).** Beta-neutralize the legs (not
   just dollar-neutral demean) **and** fix governor saturation on sparse books
   (per-name gross cap / active-name floor / longer cohort overlap). One shared fix
   covers both #2 and #4-broad. Pre-registered; expected to confirm the clean-arm
   nulls rather than flip them.

## Re-scope

- The free-data edge-hunt arc is **closed**, not abandoned — recorded with a clean
  verdict and the dormant options above.
- The deferred `portfolio/` sizing / paper-book work **stays gated** — it requires
  ≥ 1 positive sleeve, which the arc did not produce.
- The live `make go-live` alert loop continues unchanged as the OOS ledger
  generator (data collection, not a tuning target).
- "What to pursue instead" is a deliberate, open user decision — revisit paid data
  when ready, or point the work at a different problem. No new free-data hunt
  starts silently.

> Honesty note: every sleeve's gate, window, and universe were fixed before any
> result existed; committed cells were read as-is; the β-guardrail firings are
> reported as the primary diagnostic precisely so no fail is over-claimed as a
> clean refutation. Six sleeves, six non-positive verdicts, regression goldens
> byte-identical throughout.
