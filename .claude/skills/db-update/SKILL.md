---
name: db-update
description: >
  Routine DB refresh after backtest or strategy changes — runs `make db-update`
  which chains backtest (both signal_watch configs) → recalibrate → regression
  golden-fixture refresh → a surface check (declared-but-dead and
  rated-but-undeclared cells) that gates the completion banner.
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

```text
make db-update
  ├─ db-update-backtest      backtest both signal_watch configs with SAVE=1
  │    ├─ wifey-backtest CONFIG=config/signal_watch.toml          SAVE=1
  │    └─ wifey-backtest CONFIG=config/signal_watch_weekdays.toml SAVE=1
  ├─ db-update-recalibrate   recalibrate both configs with APPLY=1
  │    ├─ wifey-recalibrate CONFIG=config/signal_watch.toml          APPLY=1
  │    └─ wifey-recalibrate CONFIG=config/signal_watch_weekdays.toml APPLY=1
  ├─ regression-update       refresh tests/fixtures/golden_*.json
  └─ check-dead-surfaces     report declared-but-dead AND rated-but-undeclared cells
                             (never blocks a refresh, but GATES the ✅ banner)
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
make check-dead-surfaces     # surface report only — EXITS 1 when run directly
```

`check-dead-surfaces` never blocks the chain but **fails on its own** and now **gates
the completion banner**, and it checks the mismatch in both directions.

*Declared but dead* — a cell whose detector never fires costs work every scan cycle and
returns nothing. `signal_watch.toml` carried `1wk` under `tue_thu` for three months
behind 338 backtest rows that all had zero closed trades (#139); rows existing is not
evidence the surface works. `_KNOWN_DEAD_CELLS` holds the accepted ones with reasons and
should only ever shrink — it is **empty** as of 2026-08-06.

*Rated but undeclared* — the inverse, and the reason the banner changed. `recalibrate`
rebuilt ratings from historical `backtest_runs` with no notion of the current config, so
a dropped cell kept its stars forever. On 2026-08-06 this chain printed
`✅ Routine DB update complete` and "no unexpected dead cells" with **nine orphaned cells
present**, one showing 3★ +0.4688 for a strategy removed in May. Recalibrate now prunes
them; if any appear again, something re-created them and the warning says so.

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
  `make wifey-backtest` directly. `db-update` always touches all 3 configs and
  rewrites every golden file — overkill for a single-strategy investigation.
- For a tp_r refresh on one config, use `/wfo-sweep` (per-config WFO chain) —
  it's the trusted production path for tp_r tuning.

## Implementation files

| File | Role |
| ------ | ------ |
| `Makefile` | `db-update`, `db-update-backtest`, `db-update-recalibrate`, `regression-update`, `check-dead-surfaces` targets |
| `tools/dead_surface_check.py` | Reports declared cells with no runs or zero signals; `_KNOWN_DEAD_CELLS` allowlist |
| `analytics/backtest_runner.py` | `--save` writes `backtest_runs` + `backtest_trades` |
| `analytics/recalibrate_lib.py` | Reads `backtest_runs`, writes `confidence_ratings` |
| `tests/test_regression.py` | Compares pipeline output to `tests/fixtures/golden_*.json`; `--update-golden` rewrites them |
| `scripts/extract_regression_fixture.py` | Pre-step for `regression-update` — extracts the parquet input fixtures |
