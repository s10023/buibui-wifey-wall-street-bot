---
name: param-sweep-apply
description: >
  Auto-apply WFO `param-sweep` / `param-audit` results — parse pasted tables,
  pick the best `tp_r` per strategy × timeframe via decision rules, edit TOML,
  then run backtest + recalibrate.
  Invoke when the user says "/param-sweep-apply", pastes a `param-sweep` or
  `param-audit` table, or asks "apply these sweep results to the TOML".
  Complement to `/wfo-sweep` — use this for manual / out-of-chain sweep runs.
allowed-tools: Bash, Read, Edit, Write
---

# Param Sweep Apply — Auto-apply WFO findings to TOML

Given one or more pasted WFO sweep tables (from `wifey param-sweep` or `wifey param-audit`),
automatically determine the best tp_r per strategy × TF and apply to all relevant TOML files.

## Inputs expected from user

Either:

- One or more `param-sweep` tables (strategy × tp_r grid with IS/OOS avg_r, decay, flag)
- One or more `param-audit` tables (strategy × TF with Best IS / Best OOS / verdict)
- Or a mix of both

## Decision rules

### Selecting best tp_r from a sweep table

1. **Filter**: keep only rows where `flag = ok` (drop `⚠ OVERFIT`)
2. **Filter**: keep only rows with OOS n ≥ min_trades threshold for that TF:
   - 1h: 30 | 4h: 20 | 1d: 10 | 1wk: 5
3. **Filter**: keep only rows where `OOS avg_r > 0`
4. **Pick**: highest `OOS avg_r` among remaining rows
5. If **all rows are OVERFIT** → skip strategy × TF, note "fully overfit"
6. If **no rows pass OOS n threshold** → skip, note "insufficient trades"

### Commit-gate refusal (N2)

The sweep footer now prints a commit-gate verdict for the recommended config
(`DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ n ≥ MinTRL` — the multiple-testing correction the
filters above lack). It is **additive** to filters 1–3 (both must hold):

- `✓ COMMIT-GATE: PASS` → committable.
- `✗ COMMIT-GATE: DO-NOT-COMMIT` → **do NOT write the tp_r to TOML.** Report the
  verdict line and its reason (failed DSR / PBO / MinTRL) and skip the write.
- `⚠ COMMIT-GATE: INSUFFICIENT` → not yet decidable (too few trades / trials).
  Skip the write; note "insufficient — gate".

### When to update TOML vs leave unchanged

- Update if new best tp_r **differs from current** by ≥ 0.5 (one step)
- Update if new OOS avg_r is **meaningfully better** (> +0.05R improvement)
- Leave if difference is marginal (< 0.5 step AND < 0.05R improvement)

### TF-specific vs strategy-wide

- If all TFs point to the same tp_r → use strategy-wide `tp_r`
- If one or more TFs differ → use TF-specific keys (`tp_r_1h`, `tp_r_4h`, etc.)
- If a TF is fully overfit or no-edge → note it but do NOT add to strategy_timeframes without explicit user instruction

### Day-filter caveat

WFO sweeps are run against a specific config (usually `signal_watch.toml` with `day_filter = tue_thu`).

- Apply findings to `signal_watch.toml` (tue_thu) freely
- Apply to `signal_watch_weekdays.toml` only for TFs that were already active there

## Execution steps

1. **Parse** all pasted sweep/audit tables — extract strategy, TF, tp_r, OOS avg_r, OOS n, flag
2. **Apply decision rules** above per strategy × TF
3. **Read** `config/signal_watch.toml` — note current tp_r values
4. **Diff** new values vs current — skip unchanged
5. **Edit** `signal_watch.toml` with new values; update comments with WFO OOS data
6. **Edit** `signal_watch_weekdays.toml` for applicable TFs (check file first)
7. **Run**: `make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1`
8. **Run**: `poetry run python wifey.py recalibrate` (dry-run first, show diff)
9. If any stars changed: ask user whether to `--apply`
10. **Report**: table of changes made (strategy | TF | old tp_r | new tp_r | OOS avg_r | OOS n)

## Output format

After completing all steps, print:

```text
Changes applied:
  strategy          TF    old tp_r → new tp_r   OOS avg_r  OOS n
  ──────────────────────────────────────────────────────────────
  morning_evening_star  1h   3.0 → 3.5          +0.339R    162
  trend_day             4h    3.0 → 5.0          +0.519R     53

Skipped (overfit / insufficient trades / marginal):
  pin_bar   1h — fully overfit
  doji      1h  — marginal (+0.023R, 41 trades)

Backtest saved. Recalibration: N changed / M unchanged.
```

## Task: apply sweep findings

When the user pastes sweep or audit results:

1. Follow the decision rules above
2. Apply changes to TOML files
3. Run backtest + recalibrate
4. Print the summary table above
