---
name: db-update
description: >
  Routine DB refresh after backtest or strategy changes — runs `make db-update`
  which chains backtest (all 3 signal_watch configs) → recalibrate → regression
  golden-fixture refresh.
  Invoke when the user says "/db-update", asks to "refresh the DB", "rerun all
  backtests", "update star ratings", or after any detector / strategy / config
  change that should be reflected in the live ratings and golden fixtures.
allowed-tools: Bash, Read
---

# DB Update — Routine Pipeline

`make db-update` is the trusted, chained refresh of analytics state across all
three signal_watch configs. Use it whenever:

- A detector function changes (entry / SL / TP logic)
- A strategy is added or removed
- A `config/signal_watch*.toml` value changes (`tp_r`, `volume_suppress`,
  `min_avg_r`, `strategy_timeframes`, etc.)
- Star ratings feel stale or drift from current backtest results
- You need fresh golden fixtures before committing a behaviour change

## What `make db-update` does

```
make db-update
  ├─ db-update-backtest      backtest both signal_watch configs with SAVE=1
  │    ├─ buibui-backtest CONFIG=config/signal_watch.toml          SAVE=1
  │    └─ buibui-backtest CONFIG=config/signal_watch_weekdays.toml SAVE=1
  ├─ db-update-recalibrate   recalibrate both configs with APPLY=1
  │    ├─ buibui-recalibrate CONFIG=config/signal_watch.toml          APPLY=1
  │    └─ buibui-recalibrate CONFIG=config/signal_watch_weekdays.toml APPLY=1
  └─ regression-update       refresh tests/fixtures/golden_*.json
```

`signal_watch.toml` (tue_thu) + `signal_watch_weekdays.toml` (weekdays) are the
two production day_filter profiles — `make db-update` keeps both calibrated.

## CLI

```bash
# Full chain (most common)
make db-update

# Each leg can run alone:
make db-update-backtest      # backtests only — populates backtest_runs/trades
make db-update-recalibrate   # recalibrate only — assumes backtest_runs are fresh
make regression-update       # golden fixtures only — for tests/test_regression.py
```

## After the chain

1. **Review golden diffs** before committing:
   ```bash
   git diff tests/fixtures/golden_*.json
   ```
   Large diffs are expected after detector or config changes; small diffs after
   recalibration-only runs. If a diff is unexpectedly massive, stop and
   investigate before committing.

2. **Restart the live signal-watch daemon** so it picks up the new ratings from
   `confidence_ratings`. Star ratings are loaded once per cycle and drive the
   `min_avg_r` quality gate.

3. **Commit** the golden file changes alongside whatever change motivated the
   update — they belong in the same PR.

## When NOT to use

- For a single-combo or one-off backtest, use `/backtest-run` or
  `make buibui-backtest` directly. `db-update` always touches all 3 configs and
  rewrites every golden file — overkill for a single-strategy investigation.
- For a tp_r refresh on one config, use `/wfo-sweep` (per-config WFO chain) —
  it's the trusted production path for tp_r tuning.

## Implementation files

| File | Role |
|------|------|
| `Makefile` | `db-update`, `db-update-backtest`, `db-update-recalibrate`, `regression-update` targets |
| `analytics/backtest_runner.py` | `--save` writes `backtest_runs` + `backtest_trades` |
| `analytics/recalibrate_lib.py` | Reads `backtest_runs`, writes `confidence_ratings` |
| `tests/test_regression.py` | Compares pipeline output to `tests/fixtures/golden_*.json`; `--update-golden` rewrites them |
| `scripts/extract_regression_fixture.py` | Pre-step for `regression-update` — extracts the parquet input fixtures |
