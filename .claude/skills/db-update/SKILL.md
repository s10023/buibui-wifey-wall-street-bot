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
effort: high
---

# DB Update — Routine Pipeline

`make db-update` is the trusted, chained refresh of analytics state across both
signal_watch configs (the diagram below is the authority — the Makefile runs **2**, not
3). Use it whenever:

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
returns nothing. Rows existing in `backtest_runs` is not evidence the surface works;
`_KNOWN_DEAD_CELLS` holds the accepted exceptions with reasons and should only ever
shrink.

*Rated but undeclared* — the inverse. `recalibrate` rebuilds ratings from historical
`backtest_runs` with no notion of the current config, so a cell dropped from the TOML
would otherwise keep its stars forever. Recalibrate prunes orphaned cells; if any
reappear, something re-created them and the warning names it.

## After the chain

1. **Read the gate-state banner back.** Each backtest leg prints its resolved
   `LiveParityConfig` before the results table:

   ```text
   live_parity: regime=on direction_filter=on f8_htf_ema=on adr_bias=on conflict_resolver=off cooldown=on
   ```

   Confirm it says `conflict_resolver=off` and the other five `on`. `conflict_resolver`
   reads `confidence_ratings`, which this chain writes, so switching it on inside the
   sweep is a fixed-point iteration rather than a gate — consecutive
   `backtest + recalibrate` passes damp but do not converge. With five gates on, two
   consecutive passes differ on 0 of 160 rows, which is what makes a real rating change
   distinguishable from a re-run artifact. A banner reading `conflict_resolver=on` here
   means stop: the ratings this chain produces would not be reproducible.
   `tests/test_live_parity_config.py::TestSharedBaseGateState` asserts the committed
   config, but only the banner tells you what *this run* executed — an ad-hoc
   `--with-conflict-resolver` would not touch the config.

2. **Review golden diffs** before committing:

   ```bash
   git diff tests/fixtures/golden_*.json
   ```

   Large diffs are expected after detector or config changes; small diffs after
   recalibration-only runs. Stop and investigate an unexpectedly massive diff before
   committing.

3. **Prove the golden diff belongs to your change.** A diff here is usually **fixture
   data drift**, not a behaviour change: `regression-update` re-extracts the input
   parquets from a DB that has moved on since the goldens were written, so the goldens
   shift even when the code is byte-identical. One command falsifies it:

   ```bash
   git checkout -- tests/fixtures/ && make test-regression
   ```

   **If that passes, your code is golden-neutral and the goldens should not ship in
   your PR** — revert them and say so. `make db-update`'s completion banner prints this
   same falsifier command; if you change it here, change it in the Makefile too. The
   falsifier clears only the goldens (the AAPL fixtures) — it says nothing about
   `confidence_ratings`, a wider population (13 symbols, longer window), which needs
   its own attribution below. An unexplained diff is a lead, not noise — chase it
   rather than dismissing it.

   **Attributing a star move needs a second run, not a second look.** The goldens have
   a one-command falsifier; `confidence_ratings` has none, because `regression-update`
   and recalibrate both re-derive from a DB that has moved on. First, establish which
   cells the change can physically reach before reading any diff — a flag scoped to one
   gate and one strategy can only move the cells that gate and strategy touch; anything
   outside that reachable set is drift by construction. Second, for a cell that is in
   reach, re-run the production sweep twice over one fixed window with only the config
   flipped: anchor `start_ms`/`end_ms` once and pass them to both arms
   (`run_backtest_sweep` otherwise anchors on now-minus-`days`, so two ordinary runs
   don't share a window), flip the flag **in memory** rather than editing the TOML, and
   call `_collect_sweep_results` with `sweep_id=None` so neither arm writes. Worked
   example: `docs/plans/scripts/eqh_eql_adr_exempt_rating_isolation.py`.

   Flip the flag on every surface the sweep reads — `cfg.strategy_params` drives the
   legacy pre-filter and what `effective_adr_threshold` records, but when
   `live_parity.adr_bias` is on (it is, in both shipped configs) the engine applies ADR
   from `cfg.live_strategy_params` instead, so flipping only one of the two measures the
   wrong thing and still prints a plausible delta.

4. **No daemon restart is needed — there is no daemon to restart.** The
   `buibui-signal-watch.service`/`.timer` pair in `systemctl --user` belongs to the
   crypto parent (`WorkingDirectory=/home/kng/repo/buibui-moon-trader-bot`,
   `DATA_SOURCE=binance`); a same-shaped `wifey-signal-watch.*` sits beside it, so read
   `WorkingDirectory`, never the name. Wifey dispatch is the one-shot
   `CATCH_UP=1 make go-live`, by hand or via the opt-in `wifey-signal-watch.timer` that
   runs that same target — `Type=oneshot`, so there is no process holding stale
   ratings. `analytics/signal_runner.py:203` loads `confidence_ratings` once at
   startup, which for a one-shot process is every run: a ratings change is picked up by
   the next `make go-live` automatically.

   Ratings do not drive `min_avg_r`. They become `confidence_override` → the
   per-signal star score, which feeds the `conflict_resolver` gate
   (`analytics/signal/gates.py::_apply_conflict_resolver` picks the side with higher
   confidence), the DOW soft-suppress step, the alert's displayed stars, and
   `confidence_at_fire` in the outcome ledger. `min_avg_r` is an independent threshold
   from the config's `[backtest]` block; there is no `min_confidence` gate anywhere in
   the tree.

5. **Commit** the golden file changes alongside whatever change motivated the update —
   they belong in the same PR, unless step 3 showed they are fixture drift.

**A recalibrate can legitimately change ratings with no backtest re-run.**
`db-update-recalibrate` reads whatever is already in `backtest_runs`, so running it
alone — or running the full chain twice — can move stars without any detector or config
change. That is expected, and it is why the sweep must stay **deterministic**: with
`conflict_resolver` off, two consecutive passes differ on 0 of 160 rows, so a real
rating change is distinguishable from a re-run artifact. Flipping `conflict_resolver`
on inside the sweep destroys that property, which is why it stays off — see CLAUDE.md.

## When NOT to use

- For a single-combo or one-off backtest, use `/backtest-run` or
  `make wifey-backtest` directly. `db-update` always touches both signal_watch
  configs (`signal_watch.toml` + `signal_watch_weekdays.toml` — `strategy_params.toml`
  is the shared base they inherit, not a third run) and rewrites every golden file —
  overkill for a single-strategy investigation.
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
