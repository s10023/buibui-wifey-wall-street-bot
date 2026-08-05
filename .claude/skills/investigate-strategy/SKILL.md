---
name: investigate-strategy
description: >
  Debug why a strategy did or didn't fire on a specific candle by replaying
  detectors against historical DB data via `buibui signal test`.
  Invoke when the user says "/investigate-strategy", asks why a signal
  "fired", "missed", or "didn't fire", or mentions "signal test", "replay",
  "investigate", "diagnose", or "debug strategy".
allowed-tools: "*"
---

# Investigate Strategy — Signal Test Reference

Use `buibui signal test` (or `make buibui-signal-test`) to replay a detector against historical candles and see exactly what would have fired, with full alert formatting.

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
make buibui-signal-test SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=order_block AT="2026-04-08 13:00:00"

# Equivalent direct call
poetry run python wifey.py signal test \
  --symbol BTCUSDT --timeframe 1h --strategy order_block \
  --at "2026-04-08 13:00:00"
```

### Multiple strategies, multiple symbols

```bash
make buibui-signal-test \
  SYMBOL="BTCUSDT ETHUSDT" \
  TIMEFRAME="1h 4h" \
  STRATEGY="order_block bos fvg" \
  AT="2026-04-08 13:00:00"
```

### Without `--at` — finds latest signal in lookback

```bash
make buibui-signal-test SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=engulfing
```

### Filter by direction

```bash
make buibui-signal-test SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=bos DIRECTION=short AT="2026-04-08 13:00:00"
```

### Send to Telegram as well

```bash
make buibui-signal-test SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=fvg TELEGRAM=1
```

### Load TP/SL from config

```bash
make buibui-signal-test CONFIG=config/signal_watch.toml SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=bos
```

### Extend lookback window (default 200 candles)

```bash
make buibui-signal-test SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=fib_golden_zone LOOKBACK=400 AT="2026-04-08 13:00:00"
```

### Use `--at` with Unix ms timestamp

```bash
make buibui-signal-test SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=ote_entry AT=1744117200000
```

## All Makefile variables

| Variable | CLI flag | Description |
| --- | --- | --- |
| `SYMBOL` | `--symbol` | One or more symbols (space-separated) |
| `TIMEFRAME` | `--timeframe` | One or more TFs: `15m 1h 4h 1d` |
| `STRATEGY` | `--strategy` | One or more strategy names |
| `AT` | `--at` | Pin to this UTC candle (ISO or Unix ms) |
| `LOOKBACK` | `--lookback` | Number of candles to load (default 200) |
| `DIRECTION` | `--direction` | Filter to `long` or `short` only |
| `TELEGRAM` | `--telegram` | Also send via Telegram |
| `CONFIG` | `--config` | TOML to inherit tp_r/sl_pct defaults |

## All strategy names (for --strategy)

```text
seasonality  wick_fill  marubozu  orb  liquidity_sweep  fvg  bos
funding_reversion  smt_divergence  eqh_eql  order_block  cvd_divergence
trend_day  engulfing  pin_bar  inside_bar  hammer_hanging_man  doji
morning_evening_star  fib_golden_zone  ote_entry
```

## SMT divergence note

`smt_divergence` is supported. The secondary is resolved automatically from `coins.json smt_secondary`.

Because SMT fires rarely (~2 signals per 200 days on 1h), the default `LOOKBACK=200` (~8 days) will often return 0. Use `LOOKBACK=400` or pin with `--at`:

```bash
make buibui-signal-test SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=smt_divergence LOOKBACK=400
make buibui-signal-test SYMBOL=BTCUSDT TIMEFRAME=1h STRATEGY=smt_divergence AT="2026-03-29 20:00:00"
```

## Investigation workflow

When asked why a strategy did or didn't fire:

1. **Identify the candle**: convert event time to UTC for `--at`
2. **Run signal test**: `make buibui-signal-test SYMBOL=... TIMEFRAME=... STRATEGY=... AT=...`
3. **If signal found** but at unexpected time: note the `open_time` in the output — that's when it ACTUALLY fired
4. **If no signal found**: check detector logic for the likely gate:
   - `liquidity_sweep`: fib extension 1.13/1.27 required above pivot (the wick must go deep into the extension zone, not just barely past the prior high)
   - `bos`: requires close above prior swing high, not just a wick
   - `smt_divergence`: fires 5 candles AFTER the pivot is confirmed, AND requires close below EMA50 (bearish) or above EMA50 (bullish)
   - `fvg`: gap must exist in the right direction between candle[i-2] and candle[i]
   - `order_block`: requires a specific candle sequence near the OB
5. **Compare with pivot-sweep mode** for `liquidity_sweep`: add `MIN_SL_PCT=0` to test without sweep mode but check if `use_fib_extension=False` version fires

## Common "why didn't it fire" root causes

| Strategy | Most common miss reason |
| --- | --- |
| `liquidity_sweep` | Wick exceeded pivot high but didn't reach 1.13 fib extension (large prior range → high fib threshold) |
| `smt_divergence` | Fires 5 candles after pivot (delay), OR trend_filter blocked (wrong side of EMA50), OR signal test unsupported |
| `bos` | Price wicked above swing high but closed BELOW (wick, not close = no BOS) |
| `fib_golden_zone` | Price didn't retrace to 61.8–78.6% fib zone |
| `engulfing` | Body didn't fully engulf prior candle body, OR `min_range_pct` gate filtered it |
| `order_block` | OB candle not found in lookback, OR mitigation not detected |
