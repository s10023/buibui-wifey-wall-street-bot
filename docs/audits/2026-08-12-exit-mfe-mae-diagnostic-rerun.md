# Exit MFE/MAE Diagnostic — Re-run at n=264 (2026-08-12)

**Verdict: the 2026-06-20 INCONCLUSIVE call is SUPERSEDED at the cohort level, and
it resolved in the opposite direction to the thin read that preceded it. The
typical live loss is NOT "just wrong" — it trades green first. The
edge-specific half of the re-run trigger is still unmet.**

The 2026-06-20 audit scored 22 resolved alerts, called the ledger too young, and
set an explicit re-run trigger: *"target ≥30 resolved per cohort, and ideally per
`(strategy, tf, direction)` cell."* The ledger now holds **264 resolved rows**,
scored **264 of 264 with 0 skipped** — full OHLCV coverage again. Two of three
cohorts clear the floor (loss 196, win 45); `expired` at 23 does not, and **0 of
30 loss cells** reach 30, so the per-cell assignment the exit spec is built
around remains out of reach.

## How this was produced

- Engine unchanged: `analytics/exits/mfe_mae.py` (read-only, no DB writes).
- Command: `make wifey-exit-audit ARGS="--min-n 30 --csv …"` against the live
  `analytics.db`.
- Population: `signal_alert_outcomes`, **295 rows — 264 resolved, 31 open**; 13
  symbols, 10 strategies; signal candles spanning **2026-05-20 → 2026-08-06**.
  `4h` 175 / `1d` 89 / `1wk` **0**.
- Follow-on analysis (gitignored, durable): `docs/plans/scripts/exit_audit_cohort_depth.py`,
  `docs/plans/scripts/exit_audit_gate_era_split.py`.
- Excursions are gross of costs; net realized PnL lives in `outcome_r`.

## Cohort roll-up (all cells, `min_n=1`)

| outcome |   n | mfe_mean | mfe_p50 | mae_mean | mae_p50 | reach_05 | reach_10 | tp_r_p50 | bars_held_p50 | outcome_r_mean |
| ------- | --: | -------: | ------: | -------: | ------: | -------: | -------: | -------: | ------------: | -------------: |
| expired |  23 |   +1.805 |  +1.624 |   +0.421 |  +0.440 |   +0.913 |   +0.652 |   +5.000 |       +14.000 |         +0.957 |
| loss    | 196 |   +0.909 |  +0.560 |   +1.711 |  +1.428 |   +0.546 |   +0.352 |   +4.500 |        +4.000 |         −1.000 |
| win     |  45 |   +3.144 |  +2.500 |   +0.339 |  +0.264 |   +1.000 |   +1.000 |   +2.500 |        +4.000 |         +3.144 |

Per-`(strategy, tf, direction)` cell table at `min_n=30`: **still no rows.**

## The headline flipped — and the confound check STRENGTHENS it

| loss cohort | n | mfe_p50 | reach_05 | reach_10 | bars_held_p50 |
| --- | --: | --: | --: | --: | --: |
| 2026-06-20 | 15 | **+0.000** | 0.400 | 0.133 | **1.0** |
| 2026-08-12 | 196 | **+0.560** | 0.546 | 0.352 | **4.0** |

The 2026-06-20 audit read `mfe_p50 = 0.0` as *"the median loss never traded green
before stopping"* — the "just wrong: exit can't help" signature. **That reading was
substantially an artifact of the measurement convention, not an observation.**
`_excursion_for_row` computes a loss's MFE from `fav[:-1]` — every bar except the
exit bar, the deliberate adverse-first anti-bias rule — so **a loss resolved on its
first held bar has `mfe_r == 0.0` by construction.** At `bars_held_p50 = 1.0`, the
median loss in that run was *structurally forced* to zero. Verified directly: **39
of 196** current losses (19.9%) resolved on bar 1, and **all 39 carry `mfe_r == 0`,
none otherwise.**

Conditioning on the population that *could* exhibit an excursion does not dissolve
the finding — it sharpens it:

| loss population | n | mfe_p50 | mae_p50 | reach_05 | reach_10 |
| --- | --: | --: | --: | --: | --: |
| all losses | 196 | +0.560 | 1.428 | 0.546 | 0.352 |
| held ≥2 bars | 157 | +0.828 | 1.424 | 0.682 | **0.439** |
| held ≥3 bars | 132 | +1.054 | 1.390 | 0.780 | 0.515 |

Of the **157** losses that could show excursion, **69 reached ≥1R before stopping —
43.9% (Wilson 95% CI 36.4%–51.8%)**, and 107 reached ≥0.5R (68.2%, CI 60.5%–74.9%).
That is the exit spec's *"went +1R then reversed: breakeven / trail candidate"*
pattern, and its interval excludes the 13.3% the n=15 run reported.

**Do not read the ≥2 / ≥3-bar rows as "the real number".** Surviving bar 1 without
touching the stop is itself selection on favorable movement, and more bars mechanically
give the running max more chances — those rows are biased *up* exactly as the pooled
row is biased *down* by the 39 definitional zeros. The truth is bracketed by the two.
The conclusion is robust across the bracket because **even the unconditional 35.2%
(CI 28.9%–42.1%) is incompatible with the 13.3% that produced the entry-broken read.**

## Loss signature by timeframe — two populations, same direction

| tf | population | n | mfe_p50 | mae_p50 | reach_05 | reach_10 |
| --- | --- | --: | --: | --: | --: | --: |
| `4h` | all | 139 | +0.645 | 1.445 | 0.554 | 0.410 |
| `4h` | held ≥2 | 109 | +1.127 | 1.434 | 0.706 | 0.523 |
| `1d` | all | 57 | +0.514 | 1.382 | 0.526 | 0.211 |
| `1d` | held ≥2 | 48 | +0.604 | 1.356 | 0.625 | 0.250 |

`4h` carries the effect roughly twice as strongly as `1d` at every cut. Both point
the same way, so the verdict does not rest on the mixture, but any policy built on
this should be `4h`-first rather than applied flat.

## Expired cohort — loud signature, still under the floor

n=23 (up from 1), **20 of them `1d`**. `tp_r_p50 = 5.00` against `mfe_p50 = 1.624`,
with **65.2%** reaching ≥1R and a **positive** `outcome_r_mean` of +0.957. That is
the textbook *"TP unreachably far: lower `tp_r` / add a partial at 1R"* pattern —
but it is below the 30 floor this audit's own trigger set, and **lowering `tp_r` is
frozen** (the TA threshold-sweep freeze). Recorded, not acted on. A partial-at-1R
rule is an exit-policy change rather than a threshold re-pick, which puts it inside
the scope of #437 and outside the freeze — that distinction is worth keeping
straight if the cohort is revisited at n≥30.

## What this does NOT establish

- **Not a net-PnL claim.** The candidate pool is **69 of 264 resolved rows (26.1%)**,
  each currently booked at −1.000R. That is an **upper bound on what an exit rule
  could recover** and says nothing about what the same rule would cost the 45 wins,
  whose median MFE is +2.5R — a breakeven stop that saves a −1R also caps a +3R if
  price retraces through it first. Separating those requires the **path** replay
  (exit spec §3–§5, parent PR #437); MFE/MAE geometry alone cannot answer it.
- **Not per-edge.** 0 of 30 loss cells reach n=30; the deepest are `trend_day × 4h`
  short (28) and long (28). Pooled across outcomes only 2 of 32 cells clear 30. The
  spec's whole point is edge-specific policy assignment, and that is still blocked.
- **Not a claim about today's gate.** See below.

## The population predates the current gate — and the ledger has stopped growing

**All 264 resolved rows have signal candles at or before 2026-08-06**, and the
newest row of *any* status is an `open` row at 2026-08-06 13:30 UTC. #151 (the EV
gate's significance test, which freed 106 + 226 legs into dispatch) merged
**2026-08-07**. So the era split is degenerate — **every row is pre-#151** — and
this diagnostic characterises the *narrow*-gate population. Group C's rule applies
directly: a forecast is conditional on the configuration it was measured under.

That also settles the handoff's standing watch item (*"if the ledger does not grow,
something downstream of the gate is dropping rows"*) as **still open, with the
suggested mechanism not yet confirmed**. What is established:

- `make go-live` is manual, and the only live-gate evaluation since 2026-08-06 is
  **2026-08-11 12:25 UTC** (`backtest_runs` where `sweep_id IS NULL`), which is
  **08:25 ET — pre-open**.
- That run evaluated **35 rows across 13 distinct cells**, so detectors did fire,
  and it wrote **zero** ledger rows.

Whether that zero is normal (dedup against `signal_state.json`, or every leg gated)
or a real drop is **not determined here** — it needs the dispatch path read against
that run, and it is a separate question from this diagnostic. Flagged, not diagnosed.

## Recommendation on #437 (exit-policy A/B): the data blocker is CLEARED, the design blocker is NOT

The 2026-06-20 audit backlogged #437 on two compounding reasons. Their status has
diverged:

1. **"No data" — cleared at the cohort level.** 264 resolved rows with a
   confirmed, interval-separated exit-fixable pool is enough to justify building
   the replay. It is *not* enough for the per-edge policy assignment, so a first
   cut should be a single pooled policy (`4h`-first), not per-cell.
2. **"Missing substrate" — unchanged.** #437's headline metric upstream is
   portfolio Sharpe via the P1 paper book (`portfolio/`), which wifey does not
   have. The substitution still has to be decided — per-trade R Sharpe via
   `analytics/backtest/stats_overfit.sharpe_ratio` + `research_guards`, the path
   the forecast/xsmom sleeves used. That is design work, and it is the real
   remaining gate.

**This is a user decision, not an automatic next step**: #437 is a build, the
roadmap sits on option (d) ("bank correctness, let the ledgers mature"), and the
measured pool — while real — is bounded above by 26.1% of rows before any offset
against the win cohort is counted.

## Re-run trigger for the next pass

- **Expired cohort to n≥30** (currently 23) — the only cohort still short.
- **Any loss cell to n≥30** (currently max 28, `trend_day × 4h`) — unlocks the
  per-edge table.
- **Post-#151 rows.** The whole ledger is pre-#151; re-run once a material number
  of rows have accumulated under the current gate, and split on the era before
  pooling. `docs/plans/scripts/exit_audit_gate_era_split.py` already does the split
  and the two-proportion test.
