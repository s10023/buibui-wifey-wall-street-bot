---
name: recalibrate
description: >
  Update strategy star ratings in the `confidence_ratings` DB table from
  accumulated backtest_runs. Ratings feed Backtest UI stars, Telegram alerts,
  and the live signal-watch quality gate.
  Invoke automatically after any `make wifey-backtest SAVE=1`. Also triggers on
  the user saying "/recalibrate", asking about "star ratings", "confidence
  score", or "strategy quality".
allowed-tools: Bash
---

# Recalibrate Strategy Star Ratings

Updates strategy confidence (star) ratings in the `confidence_ratings` DB table based on accumulated
backtest results in `analytics.db`. Ratings feed the Backtest UI stars, Telegram alerts, and
the live signal filter's quality gate.

## What recalibration does

1. Reads `backtest_runs` table — aggregates avg_r per `(strategy, timeframe)` across all symbols
2. Maps avg_r → 1–5 stars (see thresholds below) — combined, long, and short directions
3. Dry-run (default): prints a diff of old vs new ratings
4. `--apply` with `--config`: writes combined + directional (long/short) stars to `confidence_ratings` DB table, keyed by `(config_name, strategy, tf, direction)`, and **prunes rows for cells the config no longer declares**
5. `--apply` without `--config`: legacy fallback that patched `confidence=N` into `indicators_lib.py` — **dead in this fork**, that file was removed in strat-3. Always pass `--config`

**Two filters on the input population, both easy to forget and both silent when wrong**
(2026-08-06):

- **Declared cells only.** With `--config`, only `(strategy, timeframe)` pairs the config
  actually scans are rated — `declared_cells`, which honours `strategy_timeframes`.
  `backtest_runs` is a permanent record, so without this a cell keeps its stars long after
  leaving the config: `fib_golden_zone × 4h` showed 3★ +0.4688, the second-highest-rated
  cell in the `signal_watch` table, 2.5 months after removal. Existing rows are deleted by
  `prune_undeclared_confidence_ratings` (the upsert cannot remove them); a declared cell
  that is merely *unrated this run* is left alone.
- **Sweep rows only** (`sweep_id IS NOT NULL`). The live EV gate also writes to
  `backtest_runs` — one row per direction-leg, single strategy, no live-parity params, no
  conflict resolver. Dedup is "latest per (strategy, tf, symbol)", so those newer rows used
  to *supersede* the competed sweep rows: 42 of 316 inputs on `signal_watch` (13%), 15 of
  22 declared cells, five of them landing on the wrong side of zero.
- **The same two writers also shared a `run_id`** until 2026-08-07, so the live gate's
  `INSERT OR REPLACE` *overwrote* the sweep row outright — meaning the filter above could
  not recover the competed measurement, it dropped that symbol. `signal_watch` was rated on
  263 of 312 sweep rows (`trend_day × 4h` on 3 of 13 symbols). Each writer now stamps an
  `origin`, and one `make db-update` restores the full grid. **If a cell's ratings look
  thin, check its symbol count before its avg_r.**

Run `make check-dead-surfaces` after applying — it fails on any orphan that survived.

**Prefer `--config` path** — it keeps ratings per-config and doesn't touch source code.

Stars flow through to:

- Backtest UI tab → `stars`, `long_stars`, `short_stars` columns (JOINed from `confidence_ratings` at query time)
- `GET /api/strategies?config=<name>` → per-config star overrides served to the UI
- Telegram alerts → star display in signal alerts
- `signal_lib` hard mode gate → suppresses signals below `min_avg_r` threshold

## Star rating thresholds

```text
avg_r < 0          → 1★
0   ≤ avg_r < 0.2  → 2★
0.2 ≤ avg_r < 0.5  → 3★
0.5 ≤ avg_r < 0.9  → 4★
avg_r ≥ 0.9        → 5★
```

Strategies with fewer trades than `--min-trades` are skipped (rating unchanged).
Default `--min-trades`: 10 combined; 5 directional (splits have fewer trades per direction).

## CLI commands

```bash
# Dry-run (default) — shows diff, no changes
wifey recalibrate --config config/signal_watch.toml

# Apply — writes to confidence_ratings DB table keyed by config_name
wifey recalibrate --config config/signal_watch.toml --apply

# Legacy apply (no --config) — DEAD in this fork (indicators_lib.py was removed); always pass --config
# wifey recalibrate --apply

# Adjust minimum trade threshold
wifey recalibrate --config config/signal_watch.toml --min-trades 20 --apply

# Make alias (dry-run, no --config)
make wifey-recalibrate
```

## Standard workflow

```bash
# 1. Run backtest and save
make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1

# 2. Dry-run to preview changes
wifey recalibrate --config config/signal_watch.toml

# 3. Apply if the diff looks correct
wifey recalibrate --config config/signal_watch.toml --apply

# 4. Update regression golden files to capture new metrics
make regression-update

# 5. Review golden file diffs before committing
git diff tests/fixtures/golden_*.json

# 6. Restart signal watch daemon to pick up new ratings
```

## What the output looks like

```text
Strategy Recalibration Report
══════════════════════════════════════════════════════════
  Strategy              TF    Trades  Win%  Avg R   Old★  New★  L★  S★
  ──────────────────────────────────────────────────────────────────────
  engulfing             1h       328   58%  +0.42R   3★  → 3★    3   3  (unchanged)
  ote_entry             4h        34   62%  +1.42R   3★  → 5★    5   4  ★ CHANGED
  eqh_eql               1h       201   44%  -0.08R   3★  → 1★    1   2  ★ CHANGED
  pin_bar               1h       175   61%  +0.51R   4★  → 4★    4   3  (unchanged)
  ...

  Dry-run mode — no changes applied. Use --apply to write to confidence_ratings table.
```

## Implementation files

| File | Role |
| ------ | ------ |
| `analytics/recalibrate_lib.py` | `get_backtest_win_rates(declared=…)` (the one place the input population is filtered — report and both rating paths share it), `compute_recalibrated_ratings()`, `compute_directional_ratings()`, `write_confidence_to_db()`, `write_confidence_to_source()` (legacy) |
| `analytics/recalibrate_runner.py` | Thin wrapper: opens DB, calls lib, prints report; `--config` derives `config_name`, `day_filter`, `adr_suppress_threshold`, `declared` |
| `analytics/signal_config.py` | `declared_cells(cfg)` — shared with `tools/dead_surface_check.py`, which asks the same question from the other end |
| `analytics/store/confidence.py` | `confidence_ratings` PK `(config_name, strategy, tf, direction)`; `prune_undeclared_confidence_ratings()` |
| `wifey.py` | `wifey recalibrate [--config FILE] [--apply] [--min-trades N]` |
