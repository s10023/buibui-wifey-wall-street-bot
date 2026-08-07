# `backtest_runs` has four writers and one key: the live gate was deleting the sweep's rows

**Date:** 2026-08-07
**Scope:** `analytics/store/backtest_runs.py`, `analytics/signal/scanner.py`,
`analytics/backtest_runner.py`, `web/api/routers/backtest.py`
**Status:** one defect fixed; two larger findings measured and queued

## Summary

The queued task (#146's defect B — "the sweep ignores `strategy_timeframes`")
was re-derived first, as the standing rule requires. Its *numbers* replicate but
its *harm mechanism does not exist*, and two strictly larger defects were found
underneath it.

| | Finding | Status |
| --- | --- | --- |
| 1 | The queued task's harm mechanism is void — the conflict resolver never runs in the routine sweep | **premise disproved** |
| 2 | The sweep that feeds `confidence_ratings` has never run the live-parity gate stack | **measured, queued** |
| 3 | The live EV gate and the sweep share a `run_id`, so the live gate *overwrites* sweep rows | **fixed here** |

Finding 3 is the one fixed in this branch: it is a data-integrity bug that was
destroying measurements on every scan cycle, and it silently undercut #146's own
fix.

## Finding 1 — the queued premise is void

The handoff stated that `bos × 4h`'s 283 signals "enter the Phase-2
conflict-resolver pool for `(symbol, 4h)`, where they can beat and drop a
declared cell's opposing signals", so the declared cells' avg_r was affected.

It is not. `cfg.live_parity.is_on("conflict_resolver")` is **False** for both
configs, so `_build_confidence_ratings_map` returns `None` and
`_resolve_conflicts_for_signals_map` is a no-op. Phase 3 backtests each cell
independently. Measured directly (`docs/plans/scripts/sweep_declared_cells_diff.py`):
`bos × 4h` displaces **zero** declared-cell signals, and of the six undeclared
cells across both configs, five detect nothing at all.

Two corrections to the queued item's scope:

- The enumeration bug has **two** sites, not one. `_collect_sweep_results:463`
  is the main sweep; `_collect_signals_map:358` is the same `itertools.product`
  and feeds the tp_r and ATR sweep modes.
- `BacktestSweepConfig` does not carry `strategy_timeframes` at all, so the
  prescribed one-line fix ("filter the product by `declared_cells`") cannot be
  written as stated — `load_backtest_config` drops the key on load.

The task is real but cosmetic: wasted compute plus undeclared rows that #146
already excluded from ratings.

## Finding 2 — the ratings sweep has never run the live gates

T6 (2026-05-26, six PRs) wired all six live signal gates into the backtest
engine behind a **default-off** `LiveParityConfig`. The default was deliberate:
plan §8 reads "`make db-update` is NOT needed for any task. Default-off
`LiveParityConfig()` ⇒ `run_backtest` output is byte-identical ⇒ regression
goldens do not move." That protected the goldens during the port. **The switch
was then never flipped.**

- No config has ever declared `[backtest.live_parity]` — `git log -S live_parity -- config/`
  is empty, and the only `[backtest.*]` subsection in the shared base is
  `cost_model`.
- `make wifey-backtest` passes no `--live-parity`, and `_resolve_live_parity`
  returns the base config unchanged when neither the master switch nor a
  per-gate override is set.

So `make db-update-backtest` — whose rows are the *only* input to
`confidence_ratings` since #146 — measures the raw signal population, while the
live daemon gates dispatch with all six, and the research that sets policy runs
gated (#143's audit, line 40: "live-parity all gates on"). Every measurement is
ungated; only dispatch is gated.

Measured with `docs/plans/scripts/live_parity_sweep_diff.py` (same window, same
process, gates the only difference):

| Config | closed trades off → on | sign flips |
| --- | --- | --- |
| `signal_watch` | 3371 → 1968 (**−41.6%**) | **6 / 24** |
| `signal_watch_weekdays` | 5884 → 3383 (**−42.5%**) | **8 / 36** |

Cells that cross zero: `eqh_eql × 1d` +0.506 → **−0.126**; `eqh_eql × 4h`
+0.193 → −0.221; `order_block × 1d` −0.133 → +0.144; `order_block × 4h`
−0.047 → +0.239; `inside_bar × 4h` −0.079 → +0.101; `trend_day × 1d` +0.107 →
−0.041. On weekdays, `hammer_hanging_man × 1wk` goes from 20 closed trades to
**0** — a declared, rated cell that live gates eliminate entirely.

These are exactly the quantities `recalibrate` turns into stars and that
`min_avg_r = 0.0` thresholds on. **Not fixed here**: enabling the gates halves
the sample, pushing many cells under `min_trades`, and moves the regression
goldens by design. That is a policy decision, not a bug fix.

## Finding 3 — the live gate overwrites the sweep's rows (fixed)

`_backtest_run_id` hashes
`symbol|timeframe|strategy|days|sl_pct|tp_r|fee_pct|day_filter` plus optional
param suffixes. It does **not** include the writer, and it does not include the
actual data window (`data_start_ms` / `data_end_ms` are stored but unhashed).

`backtest_runs` has four writers:

| Writer | `day_filter` | Collides with sweep? |
| --- | --- | --- |
| `backtest_runner.py:560` (sweep) | config's | — |
| `backtest_runner.py:936` (single-run CLI) | `"off"` | no |
| `web/api/routers/backtest.py:164` (web UI) | `"off"` | no |
| `signal/scanner.py:1229` (live EV gate) | **config's** | **yes** |

The live gate passes `days=backtest_cfg.days` and the config's real
`day_filter`, so for any `(symbol, tf, strategy)` the daemon evaluates, its
run_id is **identical** to the sweep's. `upsert_backtest_run` issues
`INSERT OR REPLACE`, so the daemon's row replaces the sweep's row in place —
flipping `sweep_id` to NULL and substituting a single-strategy, differently
windowed measurement for a competed one.

The code comment above that write says it persists results "so win-rate data
accumulates passively". It did not accumulate; it replaced.

### Measured damage

The runner produced a complete grid; the DB kept 84% of it.

- Replica of `_collect_sweep_results` phase 1–3 for `signal_watch`: **312
  results, 0 skipped** (24 cells × 13 symbols).
- Recorded sweep `e66c86f9`: **263 rows**. 17 of 24 cells lost symbols.
- `bos × 1d` rated on **6 of 13** symbols; `trend_day × 4h` on **3 of 13**;
  `order_block × 4h` on 7 of 13.
- `signal_watch_weekdays` is intact at exactly **468 = 36 × 13** — because it
  never runs live. The two configs were not comparable populations for a second,
  independent reason.

This also means #146's `sweep_id IS NOT NULL` filter does not recover the
competed measurement for those cells — it **drops** them. #146 correctly
diagnosed the two writers as producing rival rows resolved by recency; in fact
they produce the *same* row, and the loser is deleted rather than outvoted.

The handoff's standing rule that the non-sweep writers "hardcode
`day_filter="off"`, which no live config matches" is true for two of the three
and **false for the one that matters** — which is why the writers looked
separable.

### Fix

`_backtest_run_id` gains an `origin` discriminator identifying the *writer*, not
the parameters. `"sweep"` is the default and appends no suffix, so **every
historical sweep run_id is unchanged** (verified by test). The live gate passes
`origin="live_gate"`, the CLI `"single_run"`, the web UI `"web"`.

`upsert_backtest_run` takes `origin` as a **required keyword-only** argument, so
mypy forces every call site — including any future one — to declare which writer
it is. This is the enforcement pattern #142 established with
`adr_gate_applies(timeframe)`: a required arg cannot be silently inherited.
mypy caught a test call site immediately.

`backtest_cache` and `backtest_cross_tf_combos` were checked and each has
exactly one writer, so `backtest_runs` was the only collision surface. The
scanner's other `_backtest_run_id` call (`:636`) deliberately keeps the default
origin — it keys `backtest_cache`, which only the scanner writes; a comment
there says so, to stop a future reader from "aligning" it.

The damaged rows self-heal: the sweep writes at the same legacy run_id, so
`make db-update` restores every overwritten row with correct sweep data, and new
live-gate rows land at their own run_id and never collide again.

## Transferable rules

- **A row key derived from parameters does not identify a measurement.** Two
  code paths can agree on every parameter while measuring different things over
  different windows. When a table has multiple writers, the writer must be part
  of the key — a provenance *column* is not enough if the *key* still collides,
  because the loser is gone before any query runs.
- **`INSERT OR REPLACE` turns a key collision into silent data loss.** There is
  no error, no duplicate, and the survivor looks entirely well-formed. The only
  way this surfaced was counting: the runner produced 312 rows and the table
  held 263.
- **Check a producer's output count against what the consumer actually stored.**
  Not the values — the count. Every value in those 263 rows was correct.
- **"Accumulates passively" is a claim about a key, not about an intent.** A
  writer only accumulates if its key is distinct from every other writer's.
- **A default that exists to protect a migration must have an owner for
  flipping it.** T6's default-off `LiveParityConfig` was right for the port and
  wrong for the eighteen months after it; nothing in the plan assigned the flip,
  so it never happened (Finding 2).
- **Re-derive the handoff's stated mechanism — now eleven-for-eleven.** The
  numbers replicated (253 raw signals against the stated 283, one day of window
  slide) while the mechanism was void, the scope was half-stated (two
  enumeration sites), and the prescribed fix was unwritable as specified.
