---
name: config-refresh
description: >
  Full TOML refresh from a TP sweep — fixes `strategy_timeframes` gaps, updates
  `tp_r` per strategy × timeframe, validates, and commits.
  Invoke when the user says "/config-refresh", calls a `signal_watch*.toml` "stale",
  asks to "refresh the config", "fix the timeframes", or after a detector rewrite
  or new strategy add.
allowed-tools: Bash, Read, Edit, Write
---

# Config Refresh — Non-tp_r TOML Update

End-to-end workflow to bring any `signal_watch_*.toml` up to date on
**non-tp_r dimensions**: structural gaps in `strategy_timeframes`,
`day_filter`, `volume_suppress` flags, and other entries that don't need
walk-forward validation.

> **Use `/wfo-sweep` for tp_r refresh.** Full-dataset sweeps have no
> in-sample / out-of-sample split and produce overfit `tp_r` values.
> `/wfo-sweep` is the trusted production path (param-audit → param-sweep →
> apply with IS/OOS gating).

## When to run

- Signal watch config has drifted on non-tp_r entries (timeframe gaps after
  adding a strategy, stale `volume_suppress` flags)
- After a detector rewrite that changes which TFs make sense for a strategy
- When `signal_watch_weekdays.toml` drifts behind `signal_watch.toml` on
  `strategy_timeframes` or volume flags
- Use `/wfo-sweep` afterwards to refresh `tp_r` with proper validation

## Step 0 — Identify structural gaps

Before running the sweep, check `[strategy_timeframes]` for missing entries vs the reference config:

```bash
# Reference config is signal_watch.toml (tue_thu)
# Target config is the one being refreshed (e.g. signal_watch_weekdays.toml)
```

Every strategy in the `strategies = [...]` list should have an entry in `[strategy_timeframes]`
unless it genuinely runs well on ALL timeframes (rare).

**Missing entry = strategy runs on every TF in `timeframes = [...]`, including ones it was never validated on.**

Cross-reference with the reference config's `[strategy_timeframes]`. If the reference suppresses a TF,
the target should too (unless the day filter changes the distribution enough to unlock it — the sweep will show).

## Step 1 — Update `strategy_timeframes`

Based on the gap analysis from Step 0, add or remove TFs:

- **Add missing entries** for strategies that lacked any TF restriction in the
  target config but appear in the reference config
- **Remove TFs showing `n/a`** in recent backtest output (zero trades on that
  day filter) — no point including them
- **Remove TFs consistently negative** across all tp_r values — hard mode will
  suppress at runtime, but excluding at TF level keeps logs cleaner
- **Do NOT remove TFs** based on marginal negative results alone — the hard
  mode `min_avg_r` gate handles those at runtime

```toml
[strategy_timeframes]
morning_evening_star = ["4h", "1d"]   # 1wk suppressed (no edge)
order_block          = ["1d", "1wk"]  # 4h suppressed (negative)
bos                  = ["4h", "1d"]   # 1wk excluded: 0% wins
```

**`1wk` here is subject to the same day-filter guard as the top-level list.** The
filter reads each bar's OPEN weekday and weekly bars are stamped Monday, so a
config on `tue_thu` / `no_monfi` cannot carry `1wk` in `strategy_timeframes`
either — `load_signal_config` raises rather than scanning it into the void. The
`order_block` line above is only valid on a `weekdays` / `off` config.

## Step 2 — Review `volume_suppress` flags per strategy

Cross-reference each `[strategy_params.X].volume_suppress` flag against the
latest "Volume Impact" split in backtest output. Use `/volume-sweep` for the
deep walkthrough; here we just sync flags.

Decision threshold (per `/volume-sweep`):

- Δ (Normal − Low Vol) > +0.05R → `volume_suppress = true`
- Δ < −0.05R → `volume_suppress = false`
- |Δ| ≤ 0.05R → omit the flag entirely (inherits global default)

Two preconditions gate the `true` branch — read `/volume-sweep`'s STOP section before
setting it:

- It requires `adr_exempt = true` on the same strategy, or `load_signal_config`
  raises. The ADR gate and this flag select for opposite bars (range/volume correlate
  ~+0.65), so together they discard ~99% of signals silently.
- The Δ is untested by itself and needs a significance test before shipping. No
  strategy currently sets this flag, so in practice this step confirms the flags stay
  absent; any addition needs a measured, tested justification.

## Step 3 — Sync `day_filter` and other top-level fields

Confirm `day_filter`, `min_sl_pct`, and any other top-level fields match the
intent of this config (tue_thu vs weekdays). These rarely change but
drift in if a global change was made to one config and not the other.

## Step 4 — Validation run

Run a backtest without `tp_r_values` to confirm the config parses and produces
the expected trade population:

```bash
make wifey-backtest CONFIG=config/signal_watch_weekdays.toml
```

If trade counts changed materially, run `/wfo-sweep` next to refresh `tp_r`
against the new population.

## Step 5 — Commit

```bash
git add config/signal_watch_weekdays.toml
git commit -m "chore(config): refresh signal_watch_weekdays.toml — timeframes + volume flags"
```

For tp_r refresh, follow up with `/wfo-sweep config/signal_watch_weekdays.toml`.

## What NOT to change in this workflow

- **`tp_r`** — use `/wfo-sweep` (param-audit + param-sweep with IS/OOS gating).
  Full-dataset sweeps overfit.
- **ADR gate** (`[bias]` section, `adr_exempt` flags) — validate separately; skip unless explicitly asked
- **Per-symbol overrides** (`[strategy_params.X.AAPL]`) — require per-symbol backtest runs
- **`min_trades` thresholds** — only change if backtest window changes significantly

## Key behavioural differences between configs

| Config | Day filter | Notable differences vs reference |
| -------- | ----------- | ---------------------------------- |
| `signal_watch.toml` | tue_thu | Reference config; most swept |
| `signal_watch_weekdays.toml` | weekdays | Mon/Fri included; broader equity coverage |

## Files

| File | Role |
| ------ | ------ |
| `config/signal_watch_weekdays.toml` | Primary target for this skill |
| `config/signal_watch.toml` | Reference config (tue_thu) |
| `analytics/signal_config.py` | `SignalWatchConfig`, `BacktestFilterConfig` |
| `analytics/backtest_config.py` | `BacktestSweepConfig` |

## Task: refresh a config (non-tp_r)

When the user asks to refresh non-tp_r entries on a signal_watch TOML:

1. Read the target TOML and the reference `signal_watch.toml`
2. Identify missing `strategy_timeframes` entries (Step 0)
3. Update `strategy_timeframes` based on the gap analysis (Step 1)
4. Sync `volume_suppress` flags using `/volume-sweep` decision threshold (Step 2)
5. Confirm `day_filter` and other top-level fields match intent (Step 3)
6. Validation run (Step 4)
7. Commit (Step 5)
8. If `tp_r` also feels stale, follow up with `/wfo-sweep <target.toml>`
9. Update PR summary MD if on a feature branch
