# Exit MFE/MAE Diagnostic — Equity Verdict (2026-06-20)

**Verdict: INCONCLUSIVE — instrument shipped and working, but the live equity
ledger is too young to deliver a statistical exit-fixable-vs-entry-broken call.**
The §2 MFE/MAE diagnostic (parent PR #433) is now ported and wired
(`make wifey-exit-audit`), scoring **22 of 22** resolved alerts with full
coverage (0 skipped). But 22 resolved trades — 15 loss, 6 win, 1 expired — is far
below any usable cohort floor: the per-`(strategy, tf, direction)` table is empty
at `min_n=20`, and the overall roll-up rests on single- and low-double-digit n.
The diagnostic is the **go/no-go gate** for the exit-policy A/B (#437); at this
ledger depth the gate's honest reading is *"come back when there's data."*

## How this was produced

- Engine: `analytics/exits/mfe_mae.py` (this PR), an additive read-only port of
  the parent's §2 excursion study (PR #433), adapted crypto→equity (the sole
  source change is the `get_ohlcv` import path; the §3–§5 policy/replay/A/B layer
  — parent PR #437 — is deliberately NOT ported here).
- Command: `make wifey-exit-audit ARGS="--min-n 20 --csv /tmp/exit-excursions.csv"`
  against the live `analytics.db`.
- Population: the live-alert ledger `signal_alert_outcomes` — **48 total rows,
  22 resolved** (`win`/`loss`/`expired`), 26 still `open`; 12 distinct symbols;
  the resolved window spans roughly three weeks of 2026 live alerts. Excursions
  are gross of costs (price-path geometry); net realized PnL already lives in
  `outcome_r`.

## Cohort roll-up (all cells, `min_n=1`)

| outcome |  n | mfe_mean | mfe_p50 | mae_mean | mae_p50 | reach_05 | reach_10 | tp_r_p50 | bars_held_p50 | outcome_r_mean |
| ------- | -: | -------: | ------: | -------: | ------: | -------: | -------: | -------: | ------------: | -------------: |
| expired |  1 |   +3.115 |  +3.115 |   +0.504 |  +0.504 |   +1.000 |   +1.000 |   +5.000 |       +14.000 |         +0.152 |
| loss    | 15 |   +0.479 |  +0.000 |   +1.756 |  +1.685 |   +0.400 |   +0.133 |   +4.500 |        +1.000 |         −1.000 |
| win     |  6 |   +3.500 |  +3.000 |   +0.528 |  +0.529 |   +1.000 |   +1.000 |   +3.000 |        +2.500 |         +3.500 |

Per-`(strategy, tf, direction)` cell table at `min_n=20`: **no rows** (no cell
has reached 20 resolved trades).

## Directional read — thin, do not act

These are n-fragile observations, recorded only to orient the next, larger run:

- **Loss cohort (n=15) is mixed, leaning entry-broken.** `mfe_p50 = 0.0` —
  the median loss never traded green before stopping — with a fast `mae_p50`
  of 1.685R. That is the "just wrong: exit can't help" signature for the typical
  loss. But the tail is not empty: `reach_05 = 0.40` and `reach_10 = 0.13`, so
  ~40% of losses touched +0.5R and ~13% touched +1R before reversing to the stop
  — a real (if minority) **breakeven / trail** candidate pool. Net: most losses
  look like entry/SL problems, a meaningful minority look exit-fixable.
- **Expired cohort is n=1** — statistically meaningless. The single expired trade
  reached +3.1R against a `tp_r` of 5.0 (TP set unreachably far), which is the
  textbook "lower tp_r / add a partial" pattern, but one observation proves
  nothing. The live bot's expiry leak (the spec's headline motivation) cannot be
  characterised at n=1.
- **Win cohort (n=6)** is healthy (mfe +3.5R, small adverse excursion) and not
  the question this diagnostic exists to answer.

## Recommendation on #437 (exit-policy A/B): BACKLOG

Do **not** build the exit-policy A/B replay yet. Two reasons compound:

1. **No data.** The A/B would re-resolve 22 trades under candidate policies and
   judge them — a sample that cannot separate policies from noise, let alone
   per-edge. The spec is explicit (§2): *"Do not sweep exit policies before
   this step,"* and this step is currently a no-op for lack of n.
2. **Missing substrate.** #437's headline metric in the parent is portfolio
   Sharpe via the P1 paper book (`portfolio/`), which **wifey does not have**.
   Porting #437 here means first deciding the substitution (per-trade R Sharpe
   via `analytics/backtest/stats_overfit.sharpe_ratio` + `research_guards`, the
   same path the forecast/xsmom sleeves used instead of `portfolio/`). That
   design work is only worth doing once (1) is satisfied.

**Re-run trigger.** Re-run `make wifey-exit-audit` periodically as the live
ledger matures. Build #437 once the diagnostic shows a clear exit-fixable cohort
with adequate n — target ≥30 resolved per cohort, and ideally per
`(strategy, tf, direction)` cell so the policy assignment can be edge-specific
(the spec's whole point). Until then #437 stays backlogged in
`project_parent_sync_state.md`.

## What this PR delivered regardless of the (thin) verdict

- A correct, tested, read-only §2 diagnostic (`analytics/exits/`, 12 tests
  covering the conservative intrabar conventions) and a wired CLI
  (`make wifey-exit-audit`) — the **ready instrument** that will deliver a real
  verdict the moment the ledger is deep enough.
- Confirmation that the live ledger's MFE/MAE plumbing is sound: 22/22 scored,
  0 skipped, full OHLCV coverage for every resolved alert's held window.
- Regression goldens unmoved (additive read-only): the backtest pipeline is
  untouched.
