---
name: backtest-run
description: >
  Quick reference for every `wifey backtest` CLI flag and `make wifey-backtest`
  invocation — sweep, combo, cross-TF, save, since, day-filter, ATR, fees.
  Invoke when the user says "/backtest-run", asks to "run a backtest",
  "what's the flag for X", or wants to plan a sweep / combo / cross-TF run.
allowed-tools: Bash, Read
---

# Backtest Run — Quick Reference

Common `wifey backtest` invocations and `make wifey-backtest` targets.

## Most common invocations

### Full sweep (all symbols × strategies × TFs from config)

```bash
make wifey-backtest CONFIG=config/signal_watch.toml

# With the weekdays day_filter variant
make wifey-backtest CONFIG=config/signal_watch_weekdays.toml
```

### Full sweep + save results to DB

```bash
make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1
```

Saves to `backtest_runs` and `backtest_trades` tables in `analytics.db`. Required before `wifey recalibrate` can update star ratings.

### Single symbol + strategy + TF

```bash
wifey backtest --symbol AAPL --strategy engulfing --interval 1h
wifey backtest --symbol MSFT --strategy pin_bar --interval 4h --tp-r 3.0
wifey backtest --symbol AAPL --strategy bos --interval 1h --atr-sl-multiplier 1.5
```

### Single strategy, all symbols

```bash
wifey backtest --config config/signal_watch.toml --strategy engulfing
```

### Day filter (suppress Mon + Fri signals)

```bash
wifey backtest --config config/signal_watch.toml --day-filter tue_thu

# Options: off | weekdays | tue_thu (default from TOML: tue_thu)
```

### TP sweep (TOML only — no CLI flag for multi-value sweep)

```toml
# config/signal_watch.toml
tp_r_values = [1.0, 1.5, 2.0, 2.5, 3.0]
```

```bash
make wifey-backtest CONFIG=config/signal_watch.toml
```

### ATR SL sweep

```bash
# Via TOML — needs both keys (floor is required; without it the sweep is a no-op for structural strategies)
# atr_sl_multiplier_values = [0.5, 1.0, 1.5, 2.0, 2.5]
# atr_sl_floor = true
make wifey-backtest CONFIG=config/signal_watch.toml

# Via CLI — always pass --atr-sl-floor
wifey backtest --config config/signal_watch.toml --atr-sl-floor --atr-sl-values 0.5 1.0 1.5 2.0 2.5
```

### Stable anchored window (recommended for saved runs)

```bash
wifey backtest --config config/signal_watch.toml --since 2025-09-12 --save
```

### Custom lookback window

```bash
wifey backtest --symbol AAPL --strategy ote_entry --interval 4h --days 365
# Or anchored:
wifey backtest --symbol AAPL --strategy ote_entry --interval 4h --since 2025-09-12
```

## All CLI flags

```text
wifey backtest
  --config FILE            TOML config file; CLI flags override TOML values
  --symbol SYMBOL          Single symbol (e.g. AAPL)
  --strategy STRATEGY      Single strategy name
  --interval TF            Timeframe: 1h | 4h | 1d | 1wk
  --days N                 Lookback in days (default: 200; floating window)
  --since YYYY-MM-DD       Anchor start date — use for saved/comparable runs (e.g. 2025-09-12)
  --tp-r FLOAT             Take-profit ratio (e.g. 2.0)
  --sl-pct FLOAT           Stop-loss % (e.g. 0.02)
  --min-sl-pct FLOAT       Minimum SL % to prevent fee-drag explosion
  --atr-sl-multiplier N    ATR-based SL: N × ATR14
  --atr-sl-values N...     Multi-value ATR sweep (space-separated)
  --atr-sl-floor           Widen structural SLs by max(structural, N × ATR14) — required for ATR sweep to bite on structural strategies
  --day-filter MODE        off | weekdays | tue_thu
  --save                   Persist results to DB (same as SAVE=1)
  --min-trades N           Hide combos below N trades
  --secondary-symbol SYM   Secondary symbol for bos
```

## Config files

| File | Description |
| --- | --- |
| `config/signal_watch.toml` | Default: tue_thu day filter, equity timeframes (4h/1d/1wk) |
| `config/signal_watch_weekdays.toml` | Weekdays (Mon–Fri), same equity TF surface |

## Viewing saved runs

Saved runs are stored in `analytics.db` in the `backtest_runs` table. View them via the web UI Backtest tab, or query directly:

```bash
# Via web API (if server is running)
curl http://localhost:8000/api/backtest/runs

# Via DuckDB CLI
duckdb analytics.db "SELECT strategy, timeframe, symbol, avg_r, closed_trades FROM backtest_runs ORDER BY created_at DESC LIMIT 20"
```

## After running

```bash
# Update star ratings from saved DB results
wifey recalibrate          # dry-run
wifey recalibrate --apply --config config/signal_watch.toml  # writes confidence_ratings
```

## Task: run a backtest

When the user asks to run a backtest:

1. Confirm scope: single combo vs full sweep?
2. Confirm config: which TOML file? (`signal_watch.toml` is the default)
3. Confirm whether to save results: add `SAVE=1` if persisting to DB
4. Run the appropriate command above
5. If sweep output has TP/ATR tables, use `/backtest-findings` workflow to interpret
6. If saving: consider running `wifey recalibrate` after
