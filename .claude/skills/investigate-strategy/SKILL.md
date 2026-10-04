---
name: investigate-strategy
description: >
  Debug why a strategy did or didn't fire on a specific candle by replaying
  detectors against historical DB data via `wifey signal test`.
  Invoke when the user says "/investigate-strategy", asks why a signal
  "fired", "missed", or "didn't fire", or mentions "signal test", "replay",
  "investigate", "diagnose", or "debug strategy".
allowed-tools: "*"
effort: high
---

# Investigate Strategy — Signal Test Reference

Use `wifey signal test` (or `make wifey-signal-test`) to replay a detector against historical candles and see exactly what would have fired, with full alert formatting.

## Key concept

The signal test is **read-only and offline**: no DB writes, no cooldown, no latest-candle-only filter. It runs the detector over the lookback window ending at `--at`, picks the most recent signal, and prints the formatted alert. If nothing prints, the detector found no signal in that window.

## Time zone note

All `--at` timestamps are **interpreted as UTC**. The output displays in **MYT (UTC+8)**. Convert before passing:

| Event time (MYT) | Pass as `--at` (UTC) |
| --- | --- |
| 7am MYT Apr 8 | `"2026-04-08 23:00:00"` ← (Apr 7 23:00 UTC) |
| 9pm MYT Apr 8 | `"2026-04-08 13:00:00"` |
| midnight MYT Apr 8 | `"2026-04-07 16:00:00"` |

## Most common invocations

### Single strategy, single symbol + TF, pinned to a candle

```bash
make wifey-signal-test SYMBOL=AAPL TIMEFRAME=1h STRATEGY=order_block AT="2026-04-08 13:00:00"

# Equivalent direct call
poetry run python wifey.py signal test \
  --symbol AAPL --timeframe 1h --strategy order_block \
  --at "2026-04-08 13:00:00"
```

### Multiple strategies, multiple symbols

```bash
make wifey-signal-test \
  SYMBOL="AAPL MSFT" \
  TIMEFRAME="1h 4h" \
  STRATEGY="order_block bos fvg" \
  AT="2026-04-08 13:00:00"
```

### Without `--at` — finds latest signal in lookback

```bash
make wifey-signal-test SYMBOL=AAPL TIMEFRAME=1h STRATEGY=engulfing
```

### Filter by direction

```bash
make wifey-signal-test SYMBOL=AAPL TIMEFRAME=1h STRATEGY=bos DIRECTION=short AT="2026-04-08 13:00:00"
```

### Send to Telegram as well

```bash
make wifey-signal-test SYMBOL=AAPL TIMEFRAME=1h STRATEGY=fvg TELEGRAM=1
```

### Load TP/SL from config

```bash
make wifey-signal-test CONFIG=config/signal_watch.toml SYMBOL=AAPL TIMEFRAME=1h STRATEGY=bos
```

### Extend lookback window (default 200 candles)

```bash
make wifey-signal-test SYMBOL=AAPL TIMEFRAME=1h STRATEGY=ote_entry LOOKBACK=400 AT="2026-04-08 13:00:00"
```

### Use `--at` with Unix ms timestamp

```bash
make wifey-signal-test SYMBOL=AAPL TIMEFRAME=1h STRATEGY=ote_entry AT=1744117200000
```

## All Makefile variables

| Variable | CLI flag | Description |
| --- | --- | --- |
| `SYMBOL` | `--symbol` | One or more symbols (space-separated) |
| `TIMEFRAME` | `--timeframe` | One or more TFs: `1h 4h 1d 1wk` |
| `STRATEGY` | `--strategy` | One or more strategy names |
| `AT` | `--at` | Pin to this UTC candle (ISO or Unix ms) |
| `LOOKBACK` | `--lookback` | Number of candles to load (default 200) |
| `DIRECTION` | `--direction` | Filter to `long` or `short` only |
| `TELEGRAM` | `--telegram` | Also send via Telegram |
| `CONFIG` | `--config` | TOML to inherit tp_r/sl_pct defaults |

## All strategy names (for --strategy)

```text
seasonality  wick_fill  marubozu  orb  fvg  bos  eqh_eql
order_block  trend_day  engulfing  pin_bar  inside_bar
hammer_hanging_man  doji  morning_evening_star  ote_entry  ema
```

## Rare-signal note

Structural detectors (`eqh_eql`, `order_block`, `ote_entry`) fire far less often than
candlestick ones, so the default `LOOKBACK=200` will often return 0 on a higher timeframe.
Widen the window or pin the candle with `--at`:

```bash
make wifey-signal-test SYMBOL=AAPL TIMEFRAME=4h STRATEGY=eqh_eql LOOKBACK=400
make wifey-signal-test SYMBOL=AAPL TIMEFRAME=4h STRATEGY=eqh_eql AT="2026-03-29 20:00:00"
```

## Investigation workflow

When asked why a strategy did or didn't fire:

1. **Identify the candle**: convert event time to UTC for `--at`
2. **Run signal test**: `make wifey-signal-test SYMBOL=... TIMEFRAME=... STRATEGY=... AT=...`
3. **If signal found** but at unexpected time: note the `open_time` in the output — that's when it ACTUALLY fired
4. **If no signal found**: check detector logic for the likely gate:
   - `eqh_eql`: needs TWO swing highs (or lows) within `tolerance_pct` (default 0.003) to form the pool, then a candle that wicks past it AND closes back inside
   - `bos`: requires close above prior swing high, not just a wick
   - `fvg`: gap must exist in the right direction between candle[i-2] and candle[i]
   - `order_block`: requires a specific candle sequence near the OB
5. **Widen the pivot window** for `eqh_eql`: `swing_n` (default 5 → 11-candle centred window) sets how structurally significant a pivot must be; a smaller `swing_n` yields more candidate pools

## Common "why didn't it fire" root causes

| Strategy | Most common miss reason |
| --- | --- |
| `eqh_eql` | No two swing pivots landed within `tolerance_pct` (0.003), so no liquidity pool formed — or the candle wicked past but also *closed* past (a break, not a raid) |
| `bos` | Price wicked above swing high but closed BELOW (wick, not close = no BOS) |
| `ote_entry` | Price didn't retrace to 61.8–78.6% fib zone |
| `engulfing` | Body didn't fully engulf prior candle body, OR `min_range_pct` gate filtered it |
| `order_block` | OB candle not found in lookback, OR mitigation not detected |
