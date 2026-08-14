# MFE Timing Within the Hold Window — Exit Sub-project B, Step 1 (2026-08-14)

**Verdict: the +1R lock level is timing-safe on equities, and the per-tf time-stop
floor is `4h` 4 bars / `1d` 3 bars — a quarter of upstream's crypto `4h` value.
Nothing here is a parameter choice; it is the range inside which a choice is
admissible.**

Step 1 of the parent PR #437 port. `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md`
measured the *magnitude* of the favorable excursion and left the *timing*
unmeasured. A time-stop set shorter than the time winners take to reach their
decisive favorable level clips real winners, so the floor has to exist before the
replay engine can be pointed at anything.

## How this was produced

- Script: `docs/plans/scripts/exit_mfe_timing.py` (read-only, gitignored, durable).
- Window logic copied from `analytics/exits/mfe_mae.py::compute_excursions`
  (bars strictly after the signal candle, up to and including the exit bar) and
  **asserted against that production function's own `mfe_r` / `bars_held` on all
  267 rows before any timing number was printed**. The script refuses to report
  if the replication drifts.
- Timing uses the **raw** favorable path, deliberately unlike `_excursion_for_row`,
  which caps a loss at `fav[:-1]` and a win at `max(prior_fav, rr_ratio)`. Those
  are magnitude conventions for the exit bar; a bar *index* has no such
  convention, so the #156 floor trap (a loss resolved on its first held bar reads
  `mfe 0.0` by construction) does not apply to these columns.

Population: **267 resolved scoreable** rows — win 45 / loss 198 / expired 24, on
`4h` (177) and `1d` (90) only. There are no `15m`, `1h` or `1wk` rows.

## Results

**Winners — bars to first +1R** (the clip-risk check):

| tf | n | held p50 | bars→1R p50 | bars→1R p90 | bars→1R max |
| --- | --- | --- | --- | --- | --- |
| 4h | 33 | 5.0 | 2.0 | 4.0 | 16 |
| 1d | 12 | 3.0 | 1.5 | 2.9 | 4 |

**Every cohort's share that ever touched +1R:**

| cohort | 4h | 1d |
| --- | --- | --- |
| win | 33/33 = 100.0% | 12/12 = 100.0% |
| loss | 62/141 = 44.0% | 12/57 = 21.1% |
| expired | 2/3 = 66.7% | 14/21 = 66.7% |

**Expired — favorable peak timing:**

| tf | n | held p50 | bars→peak p50 | frac-of-hold p50 |
| --- | --- | --- | --- | --- |
| 4h | 3 | 30.0 | 12.0 | 0.40 |
| 1d | 21 | 14.0 | 10.0 | 0.71 |

## Verdict

1. **+1R is timing-safe as a lock level.** Every eventual winner crosses +1R, and
   crosses it early (p50 2 bars on `4h`, 1.5 on `1d`, against held p50 of 5 and 3).
   Locking at +1R cannot systematically pre-empt a winner's move.
2. **Time-stop floor = `4h` 4 · `1d` 3 bars.** These are the winner bars→1R p90s.
   Upstream's crypto floors were `15m` 65 · `1h` 17 · `4h` 7 · `1d` 3 — its `4h`
   value is **1.75× ours**, so inheriting it would have been a silent
   mis-calibration in the *conservative* direction. **Note the p90 is a p90:** the
   `4h` max is 16 bars, so a stop at 4 still marks ~10% of winners to market
   before they touch +1R. The floor bounds the sweep; it is not a recommendation.
3. **`1wk` gets no floor, deliberately.** No `1wk` row has ever resolved, so there
   is nothing to measure. `_policy_for` treats an undeclared floor as "no early
   time-stop" rather than borrowing another timeframe's number — the same refusal
   `_resolve_max_hold` makes, and the same lesson as the `1wk`/`15m` 96-bar trap.
4. **The expired cohort is 9.0% here vs upstream's 39.4%**, and `4h` expired is
   **n=3**. Upstream's whole premise was a 40%-expiry leak. That premise does not
   transfer, which is the single most important difference to carry into the A/B:
   a time-stop is aimed at a cohort this fork barely has.

## What this does NOT establish

The floors are derived from the same 267 rows the A/B then scores, so they are
in-sample. Whether the A/B result survives the choice is a separate question,
answered by the sensitivity curve in
`docs/audits/2026-08-14-exit-policy-ab-v1.md`, not by this document.
