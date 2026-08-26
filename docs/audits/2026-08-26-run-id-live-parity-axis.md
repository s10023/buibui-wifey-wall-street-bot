# `backtest_runs` identity ignored the live-parity gate set

**Date:** 2026-08-26

**Scope:** `_backtest_run_id` / `upsert_backtest_run` and their four writers; the
`backtest_runs` table as `confidence_ratings` reads it. Ported from parent #694 after
re-derivation — the parent's finding is the same shape on a different axis.

## Verdict

FOUND, and closed in this branch. The `run_id` hash carried thirteen axes and none of them
recorded the live-parity gate set, while the shared base runs five gates on and the CLI can
override any of them per run. Two runs of the identical parameter tuple under different gate
sets therefore measured different populations and hashed to the same id, so
`INSERT OR REPLACE` let one silently replace the other in the table star ratings are built
from — the same defect `origin` closed for writers, one axis over. No migration is owed and
none is written: stale-keyed rows cannot move a rating, and a migration could only guess what
it needed to know.

## What was measured

`backtest_runs` holds 3,308 rows. Grouping on the full set of parameters the key actually
hashes leaves **3,140 tuples resolving to exactly one `run_id`** and 84 to two — and each of those
84 holds exactly one row with a `sweep_id` and one without, which is the `origin` discriminator
doing its job, visible in the data. The key is saturated on parameters: there is no residual axis separating two rows of one
cell, so any live-parity variation collides by construction.

That variation is reachable, not theoretical. `config/strategy_params.toml`'s
`[backtest.live_parity]` block turns five gates on for both live configs, and its own committed
measurement puts the cost at **closed trades −41.6% (`signal_watch`) / −42.5% (`weekdays`)**.
`cli/backtest.py` exposes `--live-parity` plus `--live-parity-<gate>` / `--no-live-parity-<gate>`
on top of that, and `cli/backtest.py:132` writes the resolved result straight onto the sweep
config. Two of the four writers pass it onward; both already read `live_parity` to decide which
ADR branch applies, so the value was in scope at the call site and simply never reached the key.

Whether a row was ever actually overwritten is **not recoverable**, and that is the second half
of the finding: the table had no column recording which gates ran, so the question cannot be
asked of the data. `LiveParityConfig.describe`'s own docstring records that the committed `tp_r`
values were calibrated under an ad-hoc `--live-parity` for ~2.5 months before the block was
declared, which is exactly the window in which an ad-hoc run and the routine sweep would have
been competing for one id.

## Why no migration

Two independent reasons, either sufficient.

`analytics/recalibrate_lib.py` selects sweep rows and then keeps **only the latest run per
(strategy, timeframe, symbol)**, sorting on `run_at_ms` before `drop_duplicates`. A stale-keyed
row is therefore invisible to ratings the moment a fresher row for that cell exists, which the
next `make db-update` produces. The 793 post-flip sweep rows (and their 13,887 `backtest_trades`)
keep their old ids, are superseded on the next refresh, and are reaped by
`make db-prune-backtests` on its 30-day cutoff.

More decisively, a migration cannot do the job honestly. Migration 002 could rewrite `run_id`
because it re-derived the new value from columns the table already stored; here the column that
would say which gates ran is the one this branch adds. Backfilling it means assuming every row
after 2026-08-07 ran under the shared base's five gates — plausible, unverifiable, and wrong for
any hand-run in that window. Rows written **before** 2026-08-07 need nothing: commit `2bd93ec`
added the block that day, so `cfg.live_parity` was default-constructed and its identity is None,
which appends no suffix. Their ids are unchanged, and the 2,431 of them are the majority of the
table.

The new `live_parity` column is therefore NULL for all 3,308 existing rows and means NOT
RECORDED, exactly as `universe_policy` and `cost_model` are NULL for the 2,080 rows that predate
them. A column added later cannot describe rows written earlier.

## What shipped

`LiveParityConfig.identity()` returns a canonical token of the gates that are on, in `GATES`
order, or None when none are — so a default config appends no suffix and every historical id is
unchanged. It is built from `is_on`, the same predicate `run_backtest` consults, so the master
`enabled` switch does not enter it: the resolver expands `enabled` into per-gate values before
the engine sees the config, and folding it in would invent a distinction the engine cannot make.
`cooldown_bars_per_tf` joins the token only while `cooldown` is on, because the map is otherwise
inert and an inert value must not split one cell into two identities.

`live_parity` is a **required** keyword on `upsert_backtest_run`, alongside `origin` and
`adr_suppress_threshold` and for the same stated reason: a defaulted argument cannot distinguish
"this path runs no gates" from "nobody thought about it", and that is the failure being closed.
The sweep and `single_run` writers pass their config's identity; the live EV gate and the web UI
pass None, because `bt_cache._compute_backtest` and the UI handler call `run_backtest` with no
`LiveParityConfig` at all — None is the executed truth there, not an omission.

⚠ **mypy cannot enforce a required kwarg through a `**dict` splat**, which this branch
re-confirmed: the typecheck came back clean on one missing call site while four test call sites
building their own params dict failed only at runtime.

⚠ **`tests/test_schema_insert_arity.py` cannot see test fixtures, and that is a named hole
rather than an oversight.** Its `SCANNED_DIRS` covers `analytics`, `cli`, `signals`, `tools`,
`utils` and `web` — not `tests` — so the **six** test-local positional
`INSERT INTO backtest_runs VALUES (...)` statements this column broke (three in
`test_data_store.py`, three in `test_recalibrate_lib.py`) surfaced only by running the suite,
twice. Extending the scan was measured and rejected: `tests` adds findings the parser cannot
judge — `INSERT … SELECT *`, f-string-built SQL, deliberately partial column lists — across at
least six further files, and the exemption set needed to quiet them is large enough to be the
no-op the guard's own `test_every_insert_is_accounted_for` exists to prevent. **Anyone adding a
`backtest_runs` column should grep `tests/` for that statement before running the suite.**

## Verification

`tests/test_run_id_live_parity_axis.py` (15 tests) covers the token contract, the hash axis with
a positive control on the unchanged-id claim, the collision itself at the table, and the sweep
writer's wiring. The last one matters most: every other assertion still passes if the writer
hardcodes None, which is the defect wearing a different hat. Both mutations were run —
`live_parity=None` at the sweep writer reds the wiring test, and disabling the suffix in the hash
reds the positive control, the distinctness test and the collision regression.

`make test-regression` passes with **no golden moved**, as predicted: the change alters what a
run is *called*, never what it computes.
