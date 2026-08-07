---
name: signal-watch
description: >
  Signal daemon workflow, TOML config reference, and end-to-end signal flow
  (sync → detect → dedup → backtest gate → Telegram → persist).
  Invoke when the user says "/signal-watch", configures, debugs, starts, or
  modifies the live signal scanner — even for minor TOML changes or strategy
  additions.
allowed-tools: "*"
---

# Signal Watch Daemon

24/7 signal detection daemon — scans symbols × strategies × TFs on each new candle close, deduplicates, and sends Telegram alerts.

## What it does

1. **Sync candles**: fetches latest OHLCV from yfinance, stores in `analytics.db`
2. **Detect signals**: runs all configured strategies via `analytics/strategies/` detectors
3. **Dedup**: two-layer cooldown in `signals/cooldown_store.py`:
   - Candle watermark per `(symbol, tf, strategy)` — never re-alerts same candle
   - Cooldown timer per `(symbol, strategy, direction)` — default 1h between alerts
4. **Backtest filter** (`gates.py::passes_ev_gate`): runs mini-backtest per signal; suppresses if
   avg_r below threshold (hard mode). The `min_trades` guard counts the **tested direction**, and
   the gate **fails open** below it — so raising `min_trades` makes the gate suppress *less*, not
   more. A suppressed signal is dropped before step 6, so it leaves no ledger row at all
5. **Telegram**: sends formatted alert via `utils/telegram.py`
6. **Persist**: saves passing signals to `signals` table in `analytics.db`

## Signal flow

```text
candle close
  → data_sync.py (incremental OHLCV fetch)
  → signal_lib.scan_symbol() (run detectors per strategy)
  → cooldown_store.py (candle watermark + timer dedup)
  → signal_lib._compute_backtest() (backtest filter)
  → alert_formatter.format_signal_alert() (Telegram message)
  → telegram.py (send)
  → data_store.upsert_signals() (persist to DB)
```

## Starting the daemon

```bash
# With config file (recommended)
wifey signal watch --config config/signal_watch.toml

# With Telegram alerts (or set telegram = true in TOML)
wifey signal watch --config config/signal_watch.toml --telegram

# Make alias
make wifey-signal-watch CONFIG=config/signal_watch.toml

# Override specific params via CLI (CLI takes precedence over TOML)
wifey signal watch --config config/signal_watch.toml --timeframes 1h 4h --strategies bos engulfing
```

Note: the subcommand is `signal watch` (two words), not `signal-watch`.

## TOML config structure

Full example at `config/signal_watch.toml`. Key fields:

```toml
# Symbols (omit = all from config/stocks.json)
# symbols = ["AAPL", "MSFT"]

timeframes = ["4h", "1d"]
telegram = true
min_sl_pct = 0.005

# Day filter: "off" | "weekdays" | "tue_thu"
# Filters on each bar's OPEN weekday. Weekly bars are stamped Monday, so pairing
# "tue_thu" or "no_monfi" with "1wk" discards 100% of its signals —
# load_signal_config now REFUSES that combination (here and in
# strategy_timeframes) rather than letting it scan and dispatch nothing.
day_filter = "tue_thu"

# EMA-50 trend gate for bos
smt_trend_filter = 1

# Active strategies list
strategies = ['bos', 'engulfing', 'pin_bar', ...]

# Global TP ratio for alert SL/TP display
# tp_r = 2.0

# ATR-based SL (overrides sl_pct when set)
# atr_sl_multiplier = 2.0

# Cooldown between same (symbol, strategy, direction) alerts
# cooldown_seconds = 3600

# Per-strategy TF restrictions
[strategy_timeframes]
trend_day = ["4h", "1d"]
ote_entry = ["4h", "1d"]

# Per-strategy tp_r / sl_pct / volume overrides
[strategy_params.engulfing]
tp_r = 3.0
# volume_suppress = true     # per-strategy override (None = inherit global)
# volume_spike_boost = true  # spike candles (>3× mean) bypass suppress gate


# Backtest filter config (hard mode = suppress signal if directional avg_r below min_avg_r)
[backtest]
mode = "hard"
days = 365
min_trades = 12
min_trades_4h = 5
min_trades_1d = 2
min_trades_1wk = 1
min_avg_r = 0.0          # suppress signals with directional avg_r below this threshold
min_avg_r_z = 1.64       # ...and only when the shortfall is significant (0.0 = legacy rule)
# volume_suppress = false  # global fallback; per-strategy override takes precedence
# volume_spike_boost = false
```

**Note**: `filter_threshold` was renamed to `min_avg_r`. Update any old TOML that still has the old key.

**Both sample-size knobs fail OPEN.** `min_trades` and `min_avg_r_z` each cause the gate to
*abstain* when unmet, so raising either makes the daemon dispatch **more**, not less — at
`min_trades = 20` the `1wk` gate reaches 100% bypass, i.e. off. Check which way the knob turns
before tuning it.

## Stopping the daemon

The daemon runs in a poll loop — Ctrl+C to stop. State (candle watermarks + cooldown timers) is persisted to `signal_state.json` and survives restarts.

## Testing signal detection without the daemon

Run a single scan cycle manually:

```bash
# Backtest a strategy (validates detection logic)
wifey backtest --symbol AAPL --strategy engulfing --interval 1h

# Force a scan cycle (via Python — no CLI yet)
python -c "
import duckdb
from analytics.signal_lib import scan_symbol
from analytics.data_store import DEFAULT_DB_PATH
conn = duckdb.connect(str(DEFAULT_DB_PATH))
from analytics.signal_config import SignalWatchConfig
from analytics.data_store import get_ohlcv
import time
ohlcv = get_ohlcv(conn, 'AAPL', '1h', 0, int(time.time() * 1000))
result = scan_symbol(conn, 'AAPL', '1h', ['engulfing'], ohlcv)
print(result)
"
```

## Viewing fired signals

```bash
# Via web UI — Signal Feed tab
# Via web API
curl http://localhost:8000/api/signals

# Via DuckDB
duckdb analytics.db "SELECT * FROM signals ORDER BY ts DESC LIMIT 20"
```

## Config files

| File | Description |
| --- | --- |
| `config/signal_watch.toml` | Default: tue_thu filter, curated strategy list |
| `config/signal_watch_weekdays.toml` | Weekdays (Mon–Fri) |

## Key implementation files

| File | Role |
| ------ | ------ |
| `analytics/signal_lib.py` | `scan_symbol()`, `run_scan_cycle()` — core detection loop |
| `analytics/signal_runner.py` | Thin wrapper: creates client, opens DB, poll loop |
| `analytics/signal_config.py` | `SignalWatchConfig`, `BacktestFilterConfig`, `load_signal_config()` |
| `signals/cooldown_store.py` | Two-layer dedup: candle watermark + cooldown timer (JSON-persisted) |
| `signals/alert_formatter.py` | `format_signal_alert()` → Telegram Markdown message |
| `signals/registry.py` | `SIGNAL_REGISTRY` — 20 actionable strategies |

## Task: configure or debug signal watch

When the user asks to set up, change, or debug the signal watch daemon:

1. Check which config file is in use: `config/signal_watch.toml` is the default
2. For strategy changes: edit `strategies` list in TOML
3. For TF changes: edit `timeframes` and/or `[strategy_timeframes]`
4. For TP changes: edit `[strategy_params.X].tp_r`
5. For backtest filter changes: edit `[backtest]` sub-table
6. Test changes: run a quick backtest first (`wifey backtest --strategy X --interval Y`)
7. Start daemon: `make wifey-signal-watch CONFIG=config/signal_watch.toml`
8. Monitor logs for signal detections and filter decisions
