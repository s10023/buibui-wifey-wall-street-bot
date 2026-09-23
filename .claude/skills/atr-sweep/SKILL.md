---
name: atr-sweep
description: >
  Find the optimal ATR SL multiplier per strategy × timeframe by running a sweep
  and translating the winner into `atr_sl_multiplier` TOML overrides.
  Invoke when the user says "/atr-sweep", asks about "atr_sl_multiplier",
  "ATR-based stops", "fee drag", or any stop-loss sizing tune — and after
  any SL-related code change.
allowed-tools: Bash, Read, Edit, Write
---

# ATR SL Multiplier Sweep

Dormant while the TA book is frozen (see CLAUDE.md → Fork lineage); run only for maintenance the
user asks for.

Run an ATR SL multiplier sweep to find the optimal `atr_sl_multiplier` value per strategy × TF.
Prints a comparison table (like `tp_r_values`) showing avg R at each multiplier value.

## How it works

- **Signal detection runs once** — the ATR multiplier only affects SL placement, not which signals fire.
- For each multiplier value, every signal's SL = `N × ATR14` at the signal candle.
- `min_sl_pct` still applies — widens any ATR-derived SL that lands too close to entry.
- SL priority per trade: structural SL (e.g. pivot low from `eqh_eql`) → ATR-based → fixed `sl_pct`.
- Per-strategy `tp_r` overrides from `[strategy_params]` still apply during the sweep.

### `atr_sl_floor` is required for structural strategies

Every active production strategy emits a structural `sl_price` on every
signal, which short-circuits the ATR branch. Without the floor, every
multiplier column in the sweep is identical — the ATR sweep is a no-op.
Run the sweep with the floor on:

```bash
wifey backtest --config <toml> --atr-sl-floor --atr-sl-values 0.5 1.0 1.5 2.0 2.5
```

### Joint tp_r × ATR sweeps

Once this skill surfaces a winning multiplier, the natural follow-up is
to re-sweep `tp_r` at that multiplier (TP scales with SL distance). Use
`wifey param-sweep` with the same floor flags for IS/OOS-validated
joint sweeps:

```bash
wifey param-sweep --strategy <s> --symbol <sym> --timeframe <tf> \
  --param tp_r=1.0:5.0:0.5 --since 2025-09-12 --day-filter tue_thu \
  --atr-sl-floor --atr-sl-multiplier <winning_mult>
```

The methodology and the per-tp_r-aggregate decision rule live in the parent's
(`buibui-moon-trader-bot`) memory, in `project_f9_joint_sweep_findings.md` inside its checkout's
memory tree — resolve the path with `tools/claude_home.py`'s `memory_dir()` rather than hardcoding
it (CLAUDE.md → Fork lineage). Its numbers are crypto-cohort: port the method, never the values.

Or in TOML:

```toml
atr_sl_floor = true   # top-level
# or
[backtest]
atr_sl_floor = true
```

With the floor on, structural SLs are widened to `max(structural_dist,
atr_mult × ATR14)`. Wider structural SLs still win; ATR only ratchets
tight stops up. The flag defaults `False` so live production configs
are unaffected.

## Config / TOML

Add to any backtest TOML (e.g. `config/signal_watch.toml`):

```toml
# ATR SL sweep — prints comparison table (like tp_r_values)
atr_sl_multiplier_values = [0.5, 1.0, 1.5, 2.0, 2.5]

# Once you've found the winner, commit it:
# atr_sl_multiplier = 2.0
```

Per-strategy override (goes inside `[strategy_params.STRATEGY]`):

```toml
[strategy_params.eqh_eql]
atr_sl_multiplier = 1.2        # strategy-wide
atr_sl_multiplier_4h = 0.8     # 4h-specific override
```

## CLI

```bash
# Sweep via config
make wifey-backtest CONFIG=config/signal_watch.toml

# Sweep via CLI flags (no TOML needed) — floor on
wifey backtest --config config/signal_watch.toml --atr-sl-floor --atr-sl-values 0.5 1.0 1.5 2.0 2.5

# Single fixed multiplier (floor on)
wifey backtest --config config/signal_watch.toml --atr-sl-floor --atr-sl-multiplier 2.0

# Single-combo mode (floor on)
wifey backtest --symbol AAPL --strategy bos --interval 4h --atr-sl-floor --atr-sl-multiplier 1.5
```

## Output format

```text
ATR SL Multiplier Comparison (aggregated across symbols)
══════════════════════════════════════════════════════════
  Strategy              TF      0.5×    1.0×    1.5×    2.0×    2.5×
  ──────────────────────────────────────────────────────────────────
  bos                   4h    +0.08R  +0.22R  +0.31R  +0.28R  +0.19R
  bos                   1d    -0.05R  +0.12R  +0.18R  +0.14R  +0.09R
  eqh_eql               4h    +0.15R  +0.38R  +0.45R  +0.41R  +0.33R
  ...
  ──────────────────────────────────────────────────────────────────
  Pick the multiplier column where avg R peaks per strategy × TF row.
```

## Reading the results

1. Find the column where avg R peaks for each strategy × TF row — that's your optimal multiplier.
2. **All rows perfectly flat?** You forgot `--atr-sl-floor` (or `atr_sl_floor = true`). Without it the ATR branch is dead for structural strategies. Re-run with the floor on.
3. With the floor on, expect best multipliers to cluster at the high end (2.0–2.5×) — structural SLs are usually too tight to begin with.
4. Note: TP scales with SL distance. A wider ATR-floored SL also widens the `tp_r × dist` target, so win-rate gains net out against harder TP. Any `atr_sl_multiplier` TOML commit should be paired with a `tp_r` re-sweep at the chosen multiplier per cell.
5. After finding winners, set `atr_sl_multiplier` + `atr_sl_floor` (or per-strategy override) in TOML and re-run with `SAVE=1`:

   ```bash
   make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1
   ```

## Implementation files

| File | What's there |
| ------ | ------------- |
| `analytics/backtest/engine.py` | `_compute_atr14()`, `run_backtest(atr_sl_multiplier, atr_sl_floor)` — ATR SL path (structural → ATR → sl_pct); `atr_sl_floor=True` widens structural SLs via `max(structural_dist, atr_mult × ATR14)` |
| `analytics/backtest/formatters.py` | `format_atr_sl_sweep_table()` — comparison table formatter |
| `analytics/backtest_runner.py` | `atr_sweep_mode` branch in `run_backtest_sweep()`; threads `atr_sl_floor` through all 3 call sites |
| `analytics/backtest_config.py` | `atr_sl_multiplier_values: list[float]` + `atr_sl_floor: bool` on `BacktestSweepConfig`; loadable top-level or under `[backtest]` |
| `analytics/signal_config.py` | `atr_sl_multiplier` + `atr_sl_floor` on `SignalWatchConfig` / `StrategyOverride` / `SymbolOverride` (live signal-watch path; per-symbol+TF override hierarchy) |
| `analytics/signal/atr_floor.py` | `_apply_atr_floor()` — live SL widener; mirrors `engine.py` math (max(structural_dist, atr_mult × ATR14)) and recomputes `tp_price = entry ± tp_r × new_sl_dist` so live R:R matches the backtest cell |
| `analytics/signal/scanner.py` | Calls `_apply_atr_floor` at top of Phase 3 (before conflict/dedup/bias/DB) so every downstream code path sees the corrected sl/tp |
| `cli/backtest.py` | `--atr-sl-values`, `--atr-sl-multiplier`, `--atr-sl-floor` CLI flags |

## Which config to sweep?

Two production signal_watch configs exist:

- `config/signal_watch.toml` — `day_filter = "tue_thu"` (Tue–Thu only)
- `config/signal_watch_weekdays.toml` — `day_filter = "weekdays"` (Mon–Fri)

**Default: sweep `signal_watch.toml` only.** ATR sizing is a volatility question, not a day-of-week question — one sweep gives the baseline. The two configs already diverge in their `[strategy_params]` tp_r overrides (calibrated separately in F6), which absorbs most of the Mon/Fri difference.

**Run both when:** the user explicitly asks, OR after live use shows weekdays underperforming tue_thu (Mon/Fri candles are wider/more volatile and may warrant a different multiplier).

When sweeping both: run sequentially, produce separate findings, and write per-strategy `atr_sl_multiplier` overrides into each TOML independently.

## Task: run ATR sweep

When the user asks to run an ATR sweep or find optimal ATR multipliers:

1. Ask: "Which config — `signal_watch.toml` (tue_thu), `signal_watch_weekdays.toml`, or both?" Default to `signal_watch.toml` if not specified.
2. Suggest range: `[0.5, 1.0, 1.5, 2.0, 2.5]` — wifey's TFs (4h/1d/1wk) are all swing-grade, so there is no scalping range here
3. Add `atr_sl_multiplier_values = [...]` to the chosen TOML (or use `--atr-sl-values` CLI flag to avoid editing the file)
4. Run with the floor on: `wifey backtest --config <file> --atr-sl-floor --atr-sl-values <vals>` (skipping the floor is the #1 way to get a useless sweep)
5. Read the output table — identify peak column per strategy × TF. If rows are flat across all columns, the floor was off — re-run.
6. Translate findings into `atr_sl_multiplier` values in `[strategy_params.X]` of the same TOML, set `atr_sl_floor = true` once at the top level, and **re-sweep `tp_r` at the chosen multiplier** per cell before committing — TP scales with SL distance.
7. Re-run with `SAVE=1` to persist the winning config to DB
8. If sweeping both configs: repeat steps 3–7 for the second config independently
