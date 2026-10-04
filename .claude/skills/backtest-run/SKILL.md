---
name: backtest-run
description: >
  Quick reference for every `wifey backtest` CLI flag and `make wifey-backtest`
  invocation — sweep, combo, cross-TF, save, since, day-filter, ATR, fees.
  Invoke when the user says "/backtest-run", asks to "run a backtest",
  "what's the flag for X", or wants to plan a sweep / combo / cross-TF run.
allowed-tools: Bash, Read
effort: low
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
wifey backtest --symbol AAPL --strategy engulfing --interval 4h
wifey backtest --symbol MSFT --strategy pin_bar --interval 1d --tp-r 3.0
wifey backtest --symbol AAPL --strategy bos --interval 4h --atr-sl-multiplier 1.5
```

### Single strategy, all symbols

```bash
wifey backtest --config config/signal_watch.toml --strategy engulfing
```

### Day filter (suppress Mon + Fri signals)

```bash
wifey backtest --config config/signal_watch.toml --day-filter
```

On `backtest`, `--day-filter` is a bare switch: passing it forces `tue_thu`; omitting it leaves the
TOML's own `day_filter` in effect. `param-sweep` and `param-audit` take an explicit mode instead —
see "All CLI flags" below.

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

This list is generated from `cli/backtest.py` and `cli/_common.py` (25 flags). Re-derive rather
than hand-edit: `grep -n 'add_argument' cli/backtest.py cli/_common.py`.

```text
wifey backtest
  # target selection
  --config FILE            TOML config file for sweep mode
  --symbol SYMBOL          Primary symbol for single-combo mode (e.g. AAPL)
  --strategy STRATEGY      Strategy for single-combo mode
  --interval TF            Candle timeframe for single-combo mode (default: 4h)
  --symbols S [S ...]      Symbols to sweep (overrides --config)
  --strategies S [S ...]   Strategies to sweep (overrides --config)
  --timeframes TF [TF ...] Timeframes to sweep (overrides --config)

  # window
  --days N                 Lookback period in days (default: 90)
  --since YYYY-MM-DD       Anchor start date for stable runs. Overrides --days when set

  # risk / cost
  --sl-pct FLOAT           Stop loss as a decimal fraction (default: 0.02 = 2%)
  --tp-r FLOAT             Take profit in R multiples (default: 2.0)
  --min-sl-pct FLOAT       Minimum SL distance as a fraction of price (default: unset = TOML/0.0)
  --fee-pct FLOAT          Taker fee, decimal, charged on entry+exit (default: 0.0)
  --atr-sl-multiplier N    ATR-based SL: N × ATR14 (overrides --sl-pct when set)
  --atr-sl-values N [N...] ATR SL multiplier sweep: comparison table across values
  --atr-sl-floor           F9: use atr_sl_multiplier × ATR14 as a floor on structural sl_price

  # filtering / output
  --day-filter             store_true — suppresses Mon+Fri; takes no value (see note below)
  --min-trades N           Hide combos below this trade count in the sweep table (default: 20)
  --save                   Persist aggregate results to backtest_runs (same as SAVE=1)

  # confluence modes — see /confluence-backtest
  --combo                  Co-firing confluence backtests across all strategy pairs
  --window N               Co-firing window: ±N candles for pair detection (default: 5)
  --workers N              Parallel workers for combo backtest (default: min(4, cpu_count-1))
  --cross-tf               Cross-TF co-firing backtests (HTF context + LTF entry)
  --htf-ltf P [P ...]      HTF:LTF pairs, e.g. '1d:4h 4h:1h 1wk:1d'
  --window-hours N         Cross-TF lookback: hours back to search for an HTF signal

  # live-parity gates
  --live-parity            Master switch: enable every live-only gate
  --with-<gate>            Force one gate True    (generated per gate)
  --without-<gate>         Force one gate False   (cancels the master switch for that gate)
```

`--day-filter` on `backtest` is a bare switch, not a mode — `cli/backtest.py:251` is
`action="store_true"`. `wifey backtest --day-filter tue_thu` fails with
`error: unrecognized arguments: tue_thu`. The same flag name carries three different signatures
across subcommands: `param-sweep` / `param-audit` take a mode (`cli/param.py:250`, `:372` —
`choices=["off","weekdays","tue_thu"]`), and `recalibrate` takes a free-form string
(`cli/recalibrate.py:43`). Check the subcommand you are invoking, not the flag name.

`--min-sl-pct` is a `backtest` flag. It reaches both modes: single-combo directly, sweep as a
`None`-sentinel override of the TOML value, mirroring `--atr-sl-multiplier`. Pinned by
`tests/test_cli.py::TestBacktestMinSlPctFlag`, whose two default cases are the positive control —
they fail if the flag starts overriding a value nobody set.

`--live-parity`'s per-gate flags are generated from the gate set at `cli/backtest.py:358`, so
`--without-<gate>` names track the code and are not listed individually here. Whatever gate set
executed is hashed into `run_id` via `cfg.live_parity.identity()` — see CLAUDE.md's footgun on
`upsert_backtest_run` and `live_parity`.

## Config files

| File | Description |
| --- | --- |
| `config/signal_watch.toml` | Default: tue_thu day filter, timeframes 4h/1d — 1wk is absent because tue_thu discards every weekly bar |
| `config/signal_watch_weekdays.toml` | Weekdays (Mon–Fri); adds 1wk, which only survives because weekdays includes Monday |

## Viewing saved runs

Saved runs are stored in `analytics.db` in the `backtest_runs` table. View them via the web UI Backtest tab, or query directly:

```bash
# Via web API (if server is running)
curl http://localhost:8000/api/backtest/runs

# Via DuckDB CLI
duckdb analytics.db "SELECT strategy, timeframe, symbol, avg_r, closed_trades FROM backtest_runs ORDER BY run_at_ms DESC LIMIT 20"
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
