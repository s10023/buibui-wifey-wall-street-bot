# The EV gate blocked on point estimates: half its suppressions were noise

Date: 2026-08-07 · Branch: `fix/ev-gate-significance-test` · Follows #150
Scripts: `docs/plans/scripts/ev_gate_significance_impact.py`,
`ev_gate_min_trades_diff.py`, `sd_approx_check.py`

## Summary

`min_avg_r = 0.0` made the live EV gate suppress a signal whenever the directional
`avg_r` was **negative by any margin**, with no regard for dispersion. Measured on the
post-#150 blocked population with the real per-cell, per-direction sd of `pnl_r`:

| | blocked legs | \|t\| < 1 (indistinguishable from 0) | zero-variance, n<5 | defensible |
| --- | --- | --- | --- | --- |
| `signal_watch` | 207 | 84 (41%) | 6 | 93 (45%) |
| `weekdays` | 384 | 141 (37%) | 70 | 138 (36%) |

A cell at −0.006R was suppressed exactly as hard as one at −1.01R. Because a blocked leg
is dropped before the outcome writer (#150), each of those also destroyed a
`signal_alert_outcomes` row — under option (d) ("bank correctness, let the ledgers
mature") the gate was deleting the very evidence the roadmap is waiting on.

Fixed by requiring the shortfall to be **distinguishable from zero** before blocking:
`z = (threshold − avg_r) / (sd/√n) ≥ min_avg_r_z`, default **1.64** (one-sided 95%).
The threshold itself is unchanged at 0.0.

## Why this is correctness, not the frozen threshold-selection

The freeze targets *selecting a threshold value to maximise avg R*. This changes neither
the threshold nor its direction — it changes the **decision rule** from "is the point
estimate negative" to "is the shortfall real", which is the repo's own standing rule
([[project_flag_deltas_need_significance_tests]]: *"a raw sweep Δ is a point estimate, not
a result — test it before committing a flag"*) applied to the live gate. `min_avg_r` stays
at 0.0 and is untouched. **User decision, 2026-08-07**, taken with the measurements above
in hand.

## Not multiplicity-corrected, deliberately

An earlier draft of this recommendation said to Benjamini–Hochberg correct across cells.
**That was wrong for this surface and was withdrawn before implementation.** BH guards
against *selecting* a winner from many candidates — the sweep's problem, where PRs #142
and #143 correctly applied it. The live gate is not selecting: it makes an independent call
per leg as each signal fires, and the two error costs are comparable (a false block loses
one ledger row; a false pass sends one alert). Correcting over ~200–400 simultaneous cells
gives Bonferroni z ≈ 3.5, at which a **fail-open** gate blocks almost nothing — the
correction would silently disable the gate rather than sharpen it.

## Design finding — the cached path has no trades, and the cheap fix failed

`bt_results` holds `BacktestResult | BacktestSnapshot`. The snapshot is the *normal*
steady-state path (cache hit on the same closed candle) and stores aggregates only, so it
cannot compute an sd. Failing open there would have made the gate bite only on cache
misses — non-deterministic suppression driven by cache state, worse than either extreme.

Before taking a schema change, the cheap alternative was measured with a **pre-registered**
pass rule (median |rel err| ≤ 10% and ≤ 2% verdict flips): approximate the sd from win rate
under a two-point +tp_r/−1 payoff, `sd ≈ √(p(1−p))·(tp_r+1)`. It failed decisively —
median error **10.6%**, p90 **100%**, flipping the gate's verdict on **28.5%** of legs
(`sd_approx_check.py`). Real trades carry expiries, cost-model drag and structural exits,
so the binary assumption does not hold.

So `backtest_cache` gained `r_long_sd` / `r_short_sd` (nullable, appended last in both the
DDL and the migration — `put_backtest_cache`'s INSERT is positional, the same constraint
`backtest_runs.universe_policy` documents). NULL means "no dispersion estimate" and the
gate abstains; rows cached before the migration age out within one bar because the cache
key includes `last_candle_ts`. `BacktestResult.long_pnl_sd` / `short_pnl_sd` and the
snapshot's matching properties give the gate **one interface** across both types.

## Zero-variance cells need their own rule

A directional sample where every trade returned the same R (nearly always a run of full
stop-outs at −1R) has SE = 0 and an infinite t — degenerate, but not uninformative. An
unbroken run is evidence once it is long enough. `_ZERO_VARIANCE_MIN_TRADES = 5`: of the
zero-variance blocks, only **6 of 12** (`signal_watch`) and **4 of 74** (weekdays) had 5+
trades behind them; the rest were 2–4 identical trades and now abstain.

This was found by inspection of my own first bucketing, which lumped zero-variance cells
into "|t| ≥ 2 — the gate earning its keep" and so overstated that bucket at 48%/40%
instead of 42%/35%.

## Measured impact (live path, declared 365d, both configs)

| | blocks before → after | freed | newly blocked |
| --- | --- | --- | --- |
| `signal_watch` | 207 → **101** (−51.2%) | 106 | **0** |
| `weekdays` | 384 → **158** (−58.9%) | 226 | **0** |

Per timeframe: `signal_watch` 4h 80→22, 1d 127→79. `weekdays` 4h 119→44, 1d 149→69,
**1wk 116→45**. Zero legs become newly blocked — the rule can only relax a block, never
create one. Freed legs are the intended profile: `orb 4h MSTR short n=6 avg_r −0.013
sd 2.450`, `eqh_eql 4h MSFT short n=5 avg_r −0.027 sd 2.233`.

Ratings, sweeps and goldens are unaffected: `passes_ev_gate` has exactly one caller
(`scanner.py:751`), and `make test-regression` passes against HEAD's goldens.

## The `1wk` floor was considered and deliberately NOT changed

`min_trades_1wk = 1` lets a single directional trade decide, and 56 weekdays blocks (15%)
rested on exactly that. Raising the floor was rejected as redundant: at n=1 there is no
dispersion estimate, so the significance test abstains anyway — identical behaviour through
one mechanism instead of two. Confirmed by measurement (1wk freed 71 of 116 blocks without
the ladder being touched). Touching the ladder would also be a threshold change, reopening
what #150 closed. **Latent**: if `min_avg_r_z` is ever set to 0.0, `min_trades_1wk = 1`
becomes live again.

## Transferable rules

1. **A threshold on a point estimate is not a decision rule, it is a coin flip with extra
   steps.** Before trusting any `x < threshold` gate, ask what the standard error of `x`
   is at the sample sizes that actually reach it.
2. **Multiplicity correction belongs where selection happens.** Applying BH to an
   independent per-item operational decision is not conservative, it is wrong — and on a
   fail-open gate it silently disables the thing it was meant to strengthen.
3. **Measure the cheap alternative against a pre-registered bar before taking the
   expensive one.** The sd approximation looked plausible and failed at 28.5% verdict
   flips; without the bar written down first, "median 10.6%" could have been argued either
   way.
4. **When a cached type shadows a computed one, a new statistic must be added to both or
   the gate degrades silently on whichever path is hotter.** Here the cached path was the
   common one, so the degradation would have been the normal case.
5. **A default that ships "off to be flipped later" does not get flipped** (#149, 2.5
   months). `min_avg_r_z` ships at 1.64 in both the dataclass and the loader.
