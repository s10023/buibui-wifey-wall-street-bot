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

1. Reads `backtest_runs` table — pools avg_r per `(strategy, timeframe)` across all symbols,
   **weighted by each symbol's closed-trade count** (`sum(avg_r × n) / sum(n)`), so a 1-trade
   symbol cannot move a star as far as a 50-trade one
2. Maps avg_r → 1–5 stars (see thresholds below) — combined, long, and short directions
3. Dry-run (default): prints a diff of old vs new ratings
4. `--apply` with `--config`: writes combined + directional (long/short) stars to `confidence_ratings` DB table, keyed by `(config_name, strategy, tf, direction)`, and **prunes rows for cells the config no longer declares**
5. `--apply` without `--config`: legacy fallback that patched `confidence=N` into `indicators_lib.py` — **dead in this fork**, that file was removed. Always pass `--config`

Two filters on the input population are easy to forget because both fail silently:

- **Declared cells only.** With `--config`, only `(strategy, timeframe)` pairs the config
  actually scans are rated — `declared_cells`, which honours `strategy_timeframes`.
  `backtest_runs` is a permanent record, so without this filter a cell keeps its stars long
  after leaving the config. `prune_undeclared_confidence_ratings` deletes existing rows for
  undeclared cells (the upsert alone cannot remove them); a declared cell that is merely
  *unrated this run* is left alone.
- **Sweep rows only** (`sweep_id IS NOT NULL`). The live EV gate also writes to
  `backtest_runs` — one row per direction-leg, single strategy, no live-parity params, no
  conflict resolver. Dedup is "latest per (strategy, tf, symbol)", so an unfiltered live-gate
  row can supersede the competed sweep row for the same cell. Each writer stamps an `origin`
  so the two no longer collide on one `run_id`; run `make db-update` to restore the full grid
  if a cell's ratings look thin — check its symbol count before its avg_r.

The input population is live-parity gated: `config/strategy_params.toml` declares
`[backtest.live_parity]` with five gates on, so the sweep measures what the daemon would
actually dispatch, not the raw detector output. **`conflict_resolver` stays off inside the
sweep** — it reads `confidence_ratings`, so enabling it there turns the sweep into a
fixed-point loop rather than a gate. If `make db-update` produces different stars on a
re-run with no code change, check that flag first.

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

# 6. Nothing to restart — there is no daemon. Ratings load at startup of each
#    one-shot `make go-live` run, so the next dispatch picks them up automatically.
```

## What the output looks like

```text
Strategy Recalibration Report
══════════════════════════════════════════════════════════
  Strategy              TF    Trades  Win%  Avg R   Old★  New★  L★  S★
  ──────────────────────────────────────────────────────────────────────
  engulfing             4h       328   58%  +0.42R   3★  → 3★    3   3  (unchanged)
  ote_entry             4h        34   62%  +1.42R   3★  → 5★    5   4  ★ CHANGED
  eqh_eql               1d       201   44%  -0.08R   3★  → 1★    1   2  ★ CHANGED
  pin_bar               1d       175   61%  +0.51R   4★  → 4★    4   3  (unchanged)
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
