---
name: data-backfill
description: >
  OHLCV ingestion via `wifey analytics backfill` (full history) and
  `wifey analytics sync` (incremental). Use for first-time setup, after a
  wiped DB, when adding a new symbol or timeframe, or when filling a data
  gap before running a backtest.
  Invoke when the user says "/data-backfill", asks to "backfill", "ingest
  OHLCV", "fill the data gap", "add a new symbol", or after `clean-db`.
allowed-tools: Bash, Read
---

# Data Backfill — OHLCV Ingestion

The analytics DB (`analytics.db`, DuckDB) holds OHLCV candles per symbol +
timeframe. Backtests, signal scans, and the web UI all read from it. If the
data is missing or stale, **all downstream output is wrong** — fix it here
first.

## Two modes

| Command | When to use |
| --- | --- |
| `analytics backfill` | First-time setup, wiped DB, new symbol, new timeframe, filling a known gap |
| `analytics sync` | Routine top-up (already wired into the live signal daemon) |

`backfill` walks history from `--since` forward, paginating in 1500-candle
chunks. `sync` reads the latest stored candle per (symbol, tf) and pulls only
what's missing.

## Most common invocations

### Full backfill — all symbols from `stocks.json`, default 1h + 4h

```bash
make wifey-analytics-backfill SINCE=2023-01-01

# Or directly:
wifey analytics backfill --since 2023-01-01
```

### Backfill specific symbols / timeframes

```bash
wifey analytics backfill --symbols AAPL MSFT --timeframes 1h 4h 1d --since 2025-01-01
make wifey-analytics-backfill SYMBOLS="AAPL MSFT" TIMEFRAMES="1h 4h" SINCE=2025-01-01
```

### Fill a known gap

The MEMORY note "Data gap RESOLVED" lists the canonical re-fill date when the
DB is wiped. Currently:

```bash
make wifey-analytics-backfill SINCE=2025-09-12
```

Use this exact anchor date for any saved backtest so results stay comparable.

### Incremental sync (one-shot)

```bash
make wifey-analytics-sync
wifey analytics sync --symbols AAPL --timeframes 1h
```

The signal daemon (`wifey signal watch`) calls sync internally each cycle, so
manual sync is only needed for ad-hoc top-ups before a backtest.

## CLI flags

```text
wifey analytics backfill
  --symbols SYMBOL [SYMBOL ...]    default: all from config/stocks.json
  --timeframes TF [TF ...]         default: 1h 4h
  --since YYYY-MM-DD               default: 2023-01-01

wifey analytics sync
  --symbols SYMBOL [SYMBOL ...]    default: all from config/stocks.json
  --timeframes TF [TF ...]         default: 1h 4h
```

Add new symbols by editing `config/stocks.json` first (gitignored — see
`stocks.json.example`), then re-run backfill for the new symbol.

## Verifying the result

Before kicking off a backtest, sanity-check coverage:

```bash
duckdb analytics.db <<'SQL'
SELECT symbol, timeframe,
       count(*) AS candles,
       to_timestamp(min(open_time_ms)/1000) AS first,
       to_timestamp(max(open_time_ms)/1000) AS last
FROM ohlcv
GROUP BY 1, 2
ORDER BY 1, 2;
SQL
```

Look for:

- A `last` timestamp within the current candle window (otherwise sync first)
- A `first` at or before your intended `--since` for any saved backtest
- Roughly equal candle counts across same-TF symbols (a short symbol means
  partial backfill — re-run with the right `--since`)

## After backfilling

- For a new symbol or timeframe, run `/db-update` so star ratings and golden
  fixtures reflect the new coverage.
- For a gap-fill or data refresh, the daemon will see the new candles on its
  next cycle — no restart needed unless `stocks.json` changed.
- For a wiped DB, follow this order:
  1. `make wifey-analytics-backfill SINCE=2025-09-12`
  2. `make db-update`
  3. Restart `wifey signal watch`

## Implementation files

| File | Role |
| ------ | ------ |
| `analytics/data_fetcher.py` | yfinance fetch + 4h resample from 1h; `fetch_bars()` |
| `analytics/data_sync.py` | `backfill_symbol()`, `sync_symbol()` orchestration; upserts to `ohlcv` |
| `analytics/data_store.py` | `ohlcv` table schema; `upsert_ohlcv()` |
| `analytics/analytics_runner.py` | Thin runner wrappers `run_backfill()`, `run_sync()` |
| `wifey.py` | `analytics backfill` / `analytics sync` subcommands |
| `Makefile` | `wifey-analytics-backfill`, `wifey-analytics-sync` targets |
