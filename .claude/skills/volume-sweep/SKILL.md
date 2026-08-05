---
name: volume-sweep
description: >
  Test the `volume_suppress` flag per strategy by reading the "Volume Impact"
  split in backtest output (High Vol vs Low Vol avg R) and committing the
  winner to TOML.
  Invoke when the user says "/volume-sweep", adds a new strategy, changes entry
  logic, or asks about "volume_suppress", "volume spike boost", or
  "low-volume signals".
allowed-tools: Bash, Read, Edit, Write
---

# Volume Suppression Testing

Test whether filtering low-volume candles improves strategy performance. Uses the per-strategy `volume_suppress` flag in `[strategy_params.X]` TOML blocks (A14b — implemented).

## What volume_suppress does

Suppresses signals where the signal candle's volume is below 1.5× the 20-candle rolling mean. Low-volume candles are considered noise — signals during illiquid conditions have less reliable follow-through.

The "Volume Impact" split table is **always printed** in backtest output regardless of whether `volume_suppress` is enabled. This lets you assess impact before committing to the filter.

## How to test

Run any backtest and look for the "Volume Impact" section in the output:

```bash
make wifey-backtest CONFIG=config/signal_watch.toml
```

The split table shows aggregated results across all symbols × TFs:

```text
  Strategy               Low-vol  Avg R    Normal  Avg R    Delta
  bos                        736 -0.32R      1104 -0.20R   +0.11R   ← suppress
  pin_bar                   1950 +0.22R       512 -0.00R   -0.22R   ← do NOT suppress
  engulfing                 1336 +0.33R       292 +0.30R   -0.03R   ← neutral
```

Decision threshold:

- Delta > +0.05R → `volume_suppress = true` (normal-vol signals win)
- Delta < -0.05R → `volume_suppress = false` (explicitly keep low-vol signals)
- |Delta| ≤ 0.05R → neutral (omit the flag entirely — inherits global default)

## Where to set volume_suppress

### Per-strategy (A14b — implemented)

```toml
[strategy_params.bos]
tp_r = 3.0
volume_suppress = true        # A14b: normal-vol wins Δ=+0.11R

[strategy_params.pin_bar]
tp_r = 3.0
volume_suppress = false       # A14b: low-vol edge Δ=-0.22R — never suppress
```

Resolution order: per-strategy → global `[backtest].volume_suppress` (default false).

### Global fallback

```toml
[backtest]
volume_suppress = true   # applies to all strategies with no per-strategy override
```

## A14b findings (2026-04-06 — at current per-strategy tp_r)

### signal_watch.toml (tue_thu day filter)

| Strategy | Low-vol | Normal | Delta | Decision |
| --- | --- | --- | --- | --- |
| bos | +0.63R | +1.41R | +0.78R | **true** |
| orb | -0.16R | +0.18R | +0.33R | **true** |
| doji | +0.26R | +0.50R | +0.24R | **true** |
| eqh_eql | -0.47R | -0.30R | +0.17R | **true** (reversal from A13) |
| bos | -0.32R | -0.20R | +0.11R | **true** |
| ote_entry | -0.22R | -0.11R | +0.11R | **true** |
| marubozu | -0.07R | -0.50R | -0.43R | **false** |
| hammer_hanging_man | +0.17R | -0.18R | -0.35R | **false** |
| fvg | +0.09R | -0.15R | -0.24R | **false** |
| pin_bar | +0.22R | -0.00R | -0.22R | **false** |
| morning_evening_star | +0.26R | +0.12R | -0.14R | **false** (reversal from A13) |
| engulfing | +0.33R | +0.30R | -0.03R | neutral |
| eqh_eql | -0.12R | -0.11R | +0.01R | neutral |
| fvg | -0.22R | -0.18R | +0.03R | neutral |
| inside_bar | +0.11R | +0.15R | +0.03R | neutral |
| order_block | -0.20R | -0.22R | -0.03R | neutral |
| trend_day | -0.07R | -0.02R | +0.05R | neutral (borderline) |

Key reversals vs A13 (old tp_r=2.0):

- **eqh_eql**: A13 said don't suppress (-0.11R delta); at current tp_r now +0.17R → **suppress**
- **morning_evening_star**: A13 said suppress (+0.10R delta); at current tp_r now -0.14R → **don't suppress**

Configs use config-specific sweeps — weekdays/all configs have slightly different decisions. See inline comments in each TOML.

## Workflow

```bash
# 1. Run sweep to see volume split table
make wifey-backtest CONFIG=config/signal_watch.toml

# 2. For each strategy compute Delta = normal_avg_r - low_vol_avg_r
#    > +0.05R → volume_suppress = true
#    < -0.05R → volume_suppress = false
#    |Δ| ≤ 0.05R → omit (neutral)

# 3. Add volume_suppress to [strategy_params.X] in TOML
# 4. Repeat for weekdays and all configs separately (day filter changes trade population)

# 5. Run quality gates
make lint-py && make typecheck && make test
```

## Implementation files

| File | Role |
| ------ | ------ |
| `analytics/backtest_lib.py` | `format_volume_split()` — volume split table; `run_backtest(volume_suppress=bool)` — skips low-vol signals when True |
| `analytics/backtest_config.py` | `StrategyOverride.volume_suppress: bool \| None`; `BacktestSweepConfig.volume_suppress: bool`; `effective_volume_suppress(strategy)` |
| `analytics/signal_config.py` | `StrategyOverride.volume_suppress: bool \| None`; `BacktestFilterConfig.volume_suppress: bool`; `SignalWatchConfig.effective_volume_suppress(strategy)` |
| `analytics/signal_lib.py` | `_resolve_volume_suppress()` — per-event lookup in live daemon loop |
| `analytics/backtest_runner.py` | Passes `cfg.effective_volume_suppress(strategy)` to every `run_backtest()` call |
| `config/signal_watch.toml` | Per-strategy `volume_suppress` in each `[strategy_params.X]` block |
| `config/signal_watch_weekdays.toml` | Same — weekdays-specific decisions |
