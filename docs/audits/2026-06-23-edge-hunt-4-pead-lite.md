# Edge-Hunt #4 — PEAD-lite (post-earnings drift): gate verdict

- **Spec:** `docs/superpowers/specs/2026-06-23-edge-hunt-4-pead-lite-design.md`
- **Plan:** `docs/superpowers/plans/2026-06-23-edge-hunt-4-pead-lite.md`
- **Audit tool:** `make wifey-pead-audit` (`tools/pead_audit.py`, read-only)
- **Backfill:** `make wifey-pead-backfill` — 480/504 names with EDGAR EPS,
  24,747 quarters, 0 errors. Run date 2026-06-24.

## Headline

**Verdict = FAIL.** The pre-committed dollar-neutral `broad_ls` cell misses the
gate on every axis except PBO: net-of-cost **Sharpe +0.10** (≪ 0.7), **DSR 0.20**
(≪ 0.95), bootstrap **boot_lo −0.46** (< 0), **MinTRL = ∞** (n never establishes
the track record), at all of 0 / 2 / 8 bps.

**The equity-β guardrail FIRED — hard (realized β ≈ +113).** A β of that
magnitude is mathematically impossible for a vol-controlled book (it implies a
book vol of ~1800 %+), so it is the unmistakable signature of the **vol-governor
saturating** on *sparse daily earnings cohorts*: on most sessions only a handful
of names sit inside their 60-day drift window, vol-parity sizes each to the 20 %
target, the dollar-neutral demean over 1–3 names does **not** neutralize market
beta, and the governor — floored at `g_min = 0.5` — cannot de-lever the result.
So `broad_ls` is a clean fail of the **gate**, but it is **not a clean test of the
PEAD premium itself** — the construction never delivered the neutrality it
assumes. This is the same governor-saturation pathology that failed edge-hunt #2
(BAB).

The interpretable signal is the **`mega_ls`** diagnostic cell, which *is* roughly
neutral (β −0.40) because the S&P-100 names carry dense, well-covered earnings:
there the drift is **negative (Sharpe −0.53 @2 bps)**. Read together, the honest
conclusion is **no positive post-earnings drift in liquid US large-caps net of
cost on free data.**

## The 2×2 grid (committed cell = `broad_ls`)

@ 2 bps slippage (0 / 8 bps within ±0.01 Sharpe — cost is not the lever here):

| cell | days | Sharpe | DSR | PBO | boot_lo | MinTRL | realized β (SPY) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **broad_ls** ◀ | 2127 | **+0.101** | **+0.200** | +0.275 | **−0.456** | **+inf** | **+113.5** |
| broad_long | 2127 | +0.226 | +0.317 | +0.275 | −0.235 | +inf | +1204.2 |
| mega_ls | 2127 | −0.529 | +0.004 | +0.275 | −1.099 | +inf | −0.394 |
| mega_long | 2127 | +0.269 | +0.362 | +0.275 | −0.338 | +inf | +14.9 |

- **Gate (pre-registered):** PASS = DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0 ∧
  n ≥ MinTRL ∧ Sharpe ≥ 0.7, read on `broad_ls`. **FAIL at 0 / 2 / 8 bps.** PBO
  (0.28) is the only passing component and is moot given the rest.
- **Data:** 24,747 quarters over 480 names; 8-K item-2.02 anchors 59.5 % of all
  quarters (the deep pre-2018 history, untradeable for lack of OHLCV, dominates the
  fallback; the tradeable 2018+ window is ~80 % 8-K-anchored). The 10-Q fallback
  enters a few days late — conservative, biasing toward null.

## Reading the result

1. **The committed cell's β-explosion invalidates it as a premium test, not just
   the gate.** Sparse event cohorts + vol-parity + a floored governor = a
   hugely-levered, non-neutral book. The +0.10 Sharpe is the noise of that
   pathology, not a measured drift.
2. **The mega arm is the clean read and it is negative.** With dense large-cap
   earnings the book *is* neutral (β −0.40) and the drift is **−0.53** — i.e. the
   PEAD premium does not survive in the most-arbitraged, most-covered slice of the
   market net of cost. This is exactly where the literature expects the anomaly to
   be weakest, so a large-cap null is consistent with PEAD living in
   small/illiquid names we cannot reach on this universe.
3. **The long-only legs are survivorship/β artifacts** (β +1204, +14.9), the same
   confound that faked the positive numbers in edge-hunts #1 and #2. Not gated,
   not an edge.
4. **Cost is not the killer here** (Sharpe ~flat 0→8 bps) — the signal is simply
   absent/negative, a *signal* failure, not a *cost* failure.

## Decision

**FAIL, recorded per pre-registration — no retune-and-rerun.** PEAD-lite joins the
fail log. This is the **sixth** sleeve to fail (trend G2 #91, XS G3 #92, residual
XS-mom #98, BAB #100, cross-asset TSMOM #102, PEAD-lite #103/#104) and the **fourth
and last roadmap free-data family**, which by the roadmap's own criterion triggers
the **honest-exit decision**: spend $29/mo on Polygon (depth / breadth / estimates →
analyst-SUE PEAD retest, PIT membership, small-cap universe) or accept that a
free-data US-investable edge needs paid event / flow data, and re-scope. Surfaced
to the user.

## Flagged leads (for a future, separately pre-registered retest — NOT now)

- **Construction (the β-fired cell):** beta-neutralize the L/S legs (not just
  dollar-neutral demean) **and** fix governor saturation (denser cohorts via a
  longer drift overlap, a per-day active-name floor, or capping per-name gross) so
  a sparse event book can actually be tested neutrally. This is the same fix owed
  to BAB #2 — a real construction question, not a tune of this failed cell.
- **Universe:** PEAD concentrates in small / less-covered names; a small-cap or
  Russell-2000 universe (which wifey does not carry) is the textbook home of the
  drift. Gated behind a universe feed we lack.
- **Surprise:** an analyst-consensus SUE (paid estimates) is a different, stronger
  surprise than the free seasonal-random-walk SUE — a paid-data retest, not a
  free-data tune.
- **Announcement timing:** paginate EDGAR `submissions` for deep 8-K history and
  raise the 8-K anchor fraction above ~80 %, removing the late-entry 10-Q-fallback
  bias for the older tradeable years.

## Honesty note

The seasonal-RW SUE, the next-session-after-8-K entry, the 60-day drift window, the
2×2, and the gate were all pre-registered before any number existed; the committed
cell is read as-is. The β-guardrail firing is reported as the primary diagnostic
precisely so the fail is not over-claimed as a clean refutation of post-earnings
drift — it is a clean fail of *this construction* on *this universe*, and the
mega-arm null is the honest premium read. Everything is additive and read-only:
`make test` 1750+ pass, regression goldens **byte-identical**.
