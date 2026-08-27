---
name: wfo-sweep
description: >
  Full automated Walk-Forward Optimization chain for a config TOML —
  `param-audit` → `param-sweep` → apply `tp_r` to TOML → backtest →
  recalibrate → commit. One invocation runs the whole chain end-to-end.
  Invoke when the user says "/wfo-sweep", calls a config "stale", asks to
  "refresh tp_r" or "rerun WFO", or after adding a new strategy that needs
  validated `tp_r` values. This is the trusted production path for tp_r
  (`/config-refresh` covers non-tp_r dimensions only).
allowed-tools: Bash, Read, Edit, Write
---

# WFO Sweep — Full Automated Parameter Refresh

Runs the complete Walk-Forward Optimization chain on a TOML config without any manual pasting or intermediate steps.

## What it does

1. Reads the target config TOML → extracts symbols, TFs, fee_pct, day_filter, current tp_r values
2. **Phase 1 (Audit)**: runs `wifey param-audit` per TF on a single liquid symbol — fast pass to identify which strategies have OOS edge
3. **Phase 2 (Sweep)**: for strategies with edge, runs `wifey param-sweep` per strategy × symbol
4. **Phase 3 (Apply)**: applies decision rules → updates TOML with new tp_r values
5. **Phase 4 (Validate)**: runs backtest + recalibrate, shows diff, asks to apply stars

## Input

User invokes `/wfo-sweep` with an optional config argument:

- `/wfo-sweep` — defaults to `config/signal_watch.toml`
- `/wfo-sweep config/signal_watch_weekdays.toml`
- `/wfo-sweep all` — runs on both signal_watch TOMLs sequentially
  (`strategy_params.toml` is the shared base they inherit, never a run target)

## Step-by-step execution

### Step 1: Parse config

Read the target TOML:

```bash
cat config/signal_watch.toml
```

Extract:

- `timeframes` — list of TFs to sweep (e.g. `["4h", "1d", "1wk"]`)
- `symbols` — if set; otherwise default to `["AAPL", "MSFT", "NVDA"]`
- `fee_pct` — from `[backtest].fee_pct` or top-level, default `0.0005`
- `day_filter` — passed as `--day-filter` to every `param-audit` and `param-sweep` call so WFO runs on the correct trade population for this config
- Current tp_r per strategy — read from `[strategy_params.<name>]` blocks

### Step 2: Phase 1 — Audit (all TFs, single symbol)

For each TF in config's `timeframes`:

```bash
poetry run python wifey.py param-audit \
  --symbol AAPL \
  --timeframe <TF> \
  --since 2025-09-12 \
  --fee-pct <fee_pct> \
  --day-filter <day_filter>
```

Optional F9 joint-sweep flags (when auditing strategies that emit
structural SL prices and you want the floor applied during scoring):
`--atr-sl-floor --atr-sl-multiplier <N>`. Without the floor the ATR
branch is dead for every active strategy — leave it off for a vanilla
audit.

Parse the audit table output. For each strategy × TF, note:

- `Best OOS avg_r` — positive = edge exists
- `OOS n` — trade count on OOS portion
- Whether OOS avg_r > current tp_r-implied edge

Strategies to skip (never sweep):

- `seasonality` — not in SIGNAL_REGISTRY
- Strategies where OOS avg_r < 0 AND current TOML already has a reasonable tp_r

Build a **candidate list**: strategies where OOS avg_r > 0 and OOS n ≥ min_trades threshold.

Min trades by TF: `4h→10, 1d→5, 1wk→2` (global fallback 20) — the referent is
`config/strategy_params.toml`'s top-level `min_trades_*` keys; re-read them
rather than this line if they diverge.

### Step 3: Phase 2 — Deep sweep (candidates only)

For each (strategy, TF) in candidate list, run on each symbol in config:

```bash
poetry run python wifey.py param-sweep \
  --strategy <strategy> \
  --symbol <symbol> \
  --timeframe <TF> \
  --since 2025-09-12 \
  --fee-pct <fee_pct> \
  --day-filter <day_filter> \
  --top-n 10
```

Optional joint `tp_r × atr_sl_multiplier` sweep: append
`--atr-sl-floor --atr-sl-multiplier <N>` to score every tp_r in the grid
with the F9 floor on at multiplier `N`. Useful for follow-up after an
ATR-sweep winner — e.g. `--atr-sl-multiplier 2.0 --atr-sl-floor` plus
`--param tp_r=1.0:5.0:0.5` finds the best tp_r at that multiplier. The
methodology lives in the PARENT's memory
(`~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/project_f9_joint_sweep_findings.md`)
— its numbers are crypto-cohort, so port the method, never the values.

Collect results per (strategy, TF, symbol): best tp_r, OOS avg_r, OOS n, flag.

### Step 4: Phase 3 — Decision rules

For each strategy × TF:

**Picking best tp_r:**

1. Filter: keep only rows with `flag = ok` (drop `⚠ OVERFIT`)
2. Filter: OOS n ≥ min_trades threshold for that TF
3. Filter: OOS avg_r > 0
4. Pick: highest OOS avg_r row → that tp_r is the winner
5. If all overfit → skip, note "fully overfit — keep current"
6. If no rows pass OOS n → skip, note "insufficient trades"

**Commit-gate refusal (N2):** the sweep footer prints a commit-gate verdict
(`DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ n ≥ MinTRL`). It is **additive** to filters 1–3 —
both must hold. Before applying a winner in Step 5:

- `✓ COMMIT-GATE: PASS` → apply normally.
- `✗ COMMIT-GATE: DO-NOT-COMMIT` → **halt the apply for that cell.** Surface the
  verdict + reason in the skipped table; do not write the tp_r.
- `⚠ COMMIT-GATE: INSUFFICIENT` → skip the cell; note "insufficient — gate".

**Global vs per-symbol:**

- If all symbols agree within 0.5 step → use global strategy-level `tp_r`
- If any symbol diverges by > 0.5 → use TF-specific key (e.g. `tp_r_1h`) or per-symbol override in `[strategy_params.<name>.per_symbol.<SYMBOL>]`

**When to update TOML:**

- Update if new tp_r differs from current by ≥ 0.5
- Update if OOS avg_r improvement > +0.05R vs current
- Leave unchanged if marginal (< 0.5 step AND < 0.05R improvement)
- Never add a strategy to `strategy_timeframes` based on sweep alone — only update existing entries

**Cross-config sync:**

- If sweeping `signal_watch.toml` AND `signal_watch_weekdays.toml` exists:
  - For TFs active in weekdays config, apply the same tp_r changes (they share the same market)

### Step 5: Apply changes to TOML

Edit the config file(s) with new tp_r values. Add inline comment with WFO evidence:

```toml
tp_r = 2.5  # WFO OOS: +0.31R, n=47 (2026-04-05)
```

Show a summary table before writing:

```text
Changes to apply:
  strategy            TF    old tp_r → new tp_r   OOS avg_r  OOS n
  ─────────────────────────────────────────────────────────────────
  eqh_eql              1h    2.0 → 2.5             +0.31R     47
  morning_evening_star 1h    3.0 → 3.5             +0.34R     162

Skipped:
  pin_bar   1h — fully overfit
  doji      1h  — marginal (+0.02R, 38 trades)
```

Ask: "Apply these changes? (y/n)"

### Step 6: Phase 4 — Validate

After applying TOML changes, run backtest + recalibrate:

```bash
make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1
```

Then dry-run recalibrate to see star changes:

```bash
poetry run python wifey.py recalibrate --config config/signal_watch.toml
```

Show diff. Ask: "Apply star ratings? (y/n)"

If yes:

```bash
poetry run python wifey.py recalibrate --config config/signal_watch.toml --apply
```

### Step 7: Update golden files

After recalibration, regenerate the regression golden files to capture the new metrics:

```bash
make regression-update
```

Review what changed:

```bash
git diff tests/fixtures/golden_*.json
```

### Step 8: Commit

Stage and commit:

```bash
git add config/signal_watch.toml config/signal_watch_weekdays.toml tests/fixtures/golden_*.json
git commit -m "chore(config): WFO sweep — update tp_r per strategy × TF (2026-04-XX)"
```

## Output format

Final summary:

```text
WFO sweep complete — config/signal_watch.toml
  Updated: N strategies across M TFs
  Backtest saved. Stars: K changed, L unchanged.
  Commit: <hash>
```

## Notes

- `param-audit` is cheap (all strategies, one symbol, per TF) — always run this first as a filter
- `param-sweep` is expensive (full grid per strategy × symbol × TF) — only run for candidates
- If config has no symbols set, default to AAPL/MSFT/NVDA (the 3 most-backtested)
- WFO split default: 70% IS / 30% OOS — do not change unless history < 90d
- Never commit a config where any strategy has OOS avg_r < 0 after the update
- Always pass `--day-filter <day_filter>` to both `param-audit` and `param-sweep` — the WFO must run on the same trade population the config will see in production (tue_thu for signal_watch.toml, weekdays for weekdays, off for all)
