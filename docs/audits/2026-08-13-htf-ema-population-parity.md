# HTF-EMA Population Parity — Measured and Declined as Net Harmful (2026-08-13)

**Verdict: the population mismatch is REAL and large — 40.1% of the EV gate's
evidence is trades the live F8 HTF-EMA gate would never have produced. Closing it
is nevertheless NET HARMFUL, because the EV gate fails OPEN below `min_trades`:
cutting 40% of the evidence more than doubles the legs where the gate is inert
(116 to 241 of 511). Threading HTF-EMA into `_compute_backtest` would disable the
EV gate on 47% of legs to fix a bias worth +0.0123R at t=+0.34. DECLINED.**

This is the surviving half of the carried "Task 2". Its sibling — the claim that
the EV gate's *ordering* was the defect — was disproven and declined separately in
`docs/audits/2026-08-13-conflict-resolver-gate-ordering.md`.

## The mismatch

`_compute_backtest` takes parameters for `day_filter`, `volume_suppress{,_long,_short}`,
`volume_spike_boost*`, `adr_suppress_threshold` and `adr_exempt` — but **none for
HTF-EMA**. F8 is `enabled = true, mode = "hard"` in the shared base, so it drops
live signals that no backtest ever excluded. It is the only live dropping gate in
that position: `regime` and `direction_filter` are both `mode = "soft"` (log and
keep), and the ADR gate *is* parameterized.

Measured on the live population (13 symbols x `4h`/`1d`, `days = 365`,
`config/signal_watch.toml`): the gate would remove **1,276 of 3,182 trades
(40.1%)** from the evidence the EV gate reads.

## Why closing it makes things worse

`passes_ev_gate` returns `True` when `n_closed < effective_min_trades(timeframe)`
— it **fails open**. The live ladder is `4h = 5`, `1d = 2`. So removing 40% of the
evidence does not make the gate stricter; it makes the gate *absent*.

| | current | HTF-EMA parity |
| --- | --- | --- |
| trades in evidence | 3,182 | 1,906 |
| legs below `min_trades` (gate inert) | 116 | **241** |
| legs with >=1 closed trade | 511 | 511 |

Of **66** EV-verdict flips:

- **37 blocked to passes** — but **33 of those are the gate going inert** (the leg
  fell under `min_trades`), and only **4** are a genuine approval on real n.
- **29 passes to blocked** — parity made measured expectancy *worse* on these,
  which is the opposite of the gate's premise.

So the dominant effect of "more faithful evidence" is **less enforcement**.

## The bias being corrected is not measurable

Across the 468 legs with an `avg_r` on both populations, the shift is
**mean +0.0123R, se 0.0358, t = +0.34** — 183 legs up, 171 down. There is no
systematic lift to recover.

A direct test of the gate's own premise, on the two **disjoint** sets (this is a
clean A/B, not a filter against its own superset):

| set | n | avg_r |
| --- | --- | --- |
| aligned (kept) | 1,810 | +0.0603 |
| opposed (dropped) | 1,212 | −0.0052 |
| **difference** | | **+0.0655R**, se 0.0688, **t = +0.95** |

The point estimate is strikingly close to the `+0.059` lift the config cites from
the crypto-era 24-cell sweep (PR #344) — but at this sample it is **not
distinguishable from zero**. Directionally consistent, statistically unconfirmed.
That is a statement about wifey's equity population, not a refutation of the
parent's number.

## Method

Script: `docs/plans/scripts/htf_ema_population_parity.py` (gitignored, durable).
Every decision calls a production function — `_compute_backtest`,
`_apply_htf_ema_gate` (so deadband, per-strategy anchor and hard-vs-soft semantics
are not re-implemented), `compute_htf_ema_slope`, `passes_ev_gate`, and the
config's `effective_*` resolvers. The parity population is
`dataclasses.replace(res, trades=kept)`; every `BacktestResult` statistic is a
property over `trades`, so the copy recomputes cleanly.

**Causality.** The slope for a signal at time `T` uses only anchor bars that had
CLOSED by `T` (`open_time + anchor_tf_ms <= T`), mirroring the scanner's
drop-the-in-progress-bar rule. `ewm(adjust=False)` is a recursive filter seeded at
index 0, so `ema[i]` over the full series equals `ema[-1]` over `closes[:i+1]`; the
curve is computed once and indexed, never recomputed with future bars. A built-in
cross-check asserts this against `compute_htf_ema_slope` on the truncated series —
**78 points reproduce it exactly**.

## Disposition

**Do not thread HTF-EMA into `_compute_backtest`.** The mismatch is real but the
correction is dominated by the fail-open side effect.

What would actually have to change first is the EV gate's behaviour below
`min_trades` — and that is a calibration decision inside the frozen
threshold-selection category, so it is a user ruling, not a refactor. Recorded
here so the next session does not re-measure this.

## Transferable rules

- **A guard that fails open converts "more faithful evidence" into "less
  enforcement".** Before improving a population's fidelity, check what the
  consumer does when the population gets small — here the fix would have disabled
  the gate on 47% of legs. This is the #150 lesson resurfacing: a fail-open gate
  inverts the meaning of "stricter".
- **A point estimate that matches a prior claim's magnitude is not confirmation.**
  The measured +0.0655R sits almost exactly on the cited +0.059, and is still
  indistinguishable from zero at this n. Check significance against *your* sample,
  not against the agreement.
- **When a filter is applied to a backtest population, count what falls under
  every downstream sample-size floor** — the verdict flips that matter were not
  the ones where expectancy changed, but the ones where n did.
