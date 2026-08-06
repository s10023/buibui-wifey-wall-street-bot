# Confidence ratings: rated cells the config never scans, and live rows superseding sweep rows

**Date:** 2026-08-06
**Scope:** `analytics/recalibrate_lib.py`, `analytics/store/confidence.py`,
`analytics/recalibrate_runner.py`, `analytics/signal_config.py`,
`tools/dead_surface_check.py`, `Makefile`
**Status:** two defects fixed, one measured and queued

## Summary

The star ratings in `confidence_ratings` were wrong on two independent axes, and
neither had a failure mode. Ratings are read by the Backtest UI, the Telegram
star line, `signal_runner`'s startup override, and the backtest conflict
resolver's tiebreaker, so a wrong rating is displayed *and* acted on.

| | Defect | Status |
| --- | --- | --- |
| A | Cells the config no longer declares kept their ratings, refreshed with a new timestamp on every recalibrate | **fixed** |
| B | The research sweep walks the full symbol × TF × strategy product, ignoring `strategy_timeframes` | **measured, queued** |
| C | Live-EV-gate rows superseded sweep rows in the ratings computation | **fixed** |

A was the queued task. B and C were found while re-deriving it; C is the larger
error.

## A — rated but undeclared

`compute_recalibrated_ratings` rebuilt ratings from `backtest_runs` with no
notion of what the config currently declares, and `upsert_confidence_ratings`
is insert-or-replace with no delete. Two independent reasons a rating outlives
its declaration, so fixing either alone is insufficient: the filter stops new
orphans, the delete removes the ones already on disk.

**23 rows across 9 distinct cells** (the earlier "9 orphans" figure counted
cells; the row count is 23 because each cell carries up to three directions):

| Config | Cells | Rows |
| --- | --- | --- |
| `signal_watch` | `fib_golden_zone × {4h, 1d}`, `liquidity_sweep × {4h, 1d}`, `bos × 4h` | 11 |
| `signal_watch_weekdays` | `fib_golden_zone × {4h, 1d}`, `liquidity_sweep × {4h, 1d}` | 12 |

`fib_golden_zone` and `liquidity_sweep` left both configs on 2026-05-21 and were
re-rated on every refresh for the 2.5 months since. Worst instances:

- **`fib_golden_zone × 4h` — 3★ at +0.4688, the second-highest-rated cell in the
  whole `signal_watch` table**, above every live strategy except `orb`.
- `fib_golden_zone × 1d` **long** — **5★ at +1.6250**, the highest-starred row in
  that table, resting on 7 closed trades last measured in May.

**`bos × 4h` is a different shape and was not in the original report.** `bos` is
still a live strategy; #143 retired it from `4h` on `signal_watch` earlier the
same day via `strategy_timeframes`. Its rating was therefore not stale at all —
it was *freshly recomputed from rows generated the same afternoon*, for a cell
the daemon will never scan. This is the B defect surfacing as an A symptom, and
it is why A's fix keys on `declared_cells` (which honours `strategy_timeframes`)
rather than on "is the strategy still in `strategies`".

## C — the live EV gate's rows outvoted the sweep's

`get_backtest_win_rates` keeps "the latest run per (strategy, timeframe,
symbol)" to drop superseded param sweeps. It had no notion of *provenance*, and
`backtest_runs` has two writers:

- the sweep (`_collect_sweep_results`) — all strategies competed through the
  conflict resolver, live-parity gates on, config window, `sweep_id` set;
- the live EV gate (`signal.scanner`, one row per direction-leg evaluated) —
  **one** strategy, no live-parity params, no conflict resolver, window from
  `signal_runner`, `sweep_id` **NULL**.

Because live rows are written continuously and sweep rows only on `db-update`,
the live row is almost always the newer one, so it silently replaced the sweep
row for that symbol.

Measured before the fix:

- **94 live rows**, all on `tue_thu`, accumulated 2026-06-09 → 2026-08-06.
- **42 of the 316** (strategy, tf, symbol) inputs to `signal_watch`'s ratings —
  **13%** — were live rows, at a **median 4 closed trades against the sweep's 8**.
- They touched **15 of the 22 declared cells**.
- **Five cells sat on the wrong side of zero**, i.e. the wrong star tier:
  `inside_bar × 4h`, `order_block × 4h`, `pin_bar × 4h`, `trend_day × 1d`,
  `engulfing × 1d`.
- `orb × 4h` read **+0.0011** against a sweep-only **+0.2126**; `trend_day × 4h`
  ran on n=105 instead of n=223, so the contamination *thinned* the sample as
  well as skewing it.
- **`signal_watch_weekdays` had zero live rows** — it never runs live. The two
  configs' stars were not computed from comparable populations at all.

Note this is not a preference between two measurements. Whichever you prefer,
a *mixture* — 42 arbitrary rows chosen by which symbols the daemon happened to
evaluate most recently — is indefensible.

## B — the sweep measures cells the daemon does not scan (queued)

`analytics/backtest_runner.py:463` enumerates
`itertools.product(symbols, cfg.timeframes, strategies)`. The live path applies
the per-strategy allow-list inside `scan_symbol`
(`analytics/signal/scanner.py:195-198`). Nothing applies it to the sweep.

| Config | Declared | Swept | Undeclared cells measured |
| --- | --- | --- | --- |
| `signal_watch` | 22 | 24 | `bos × 4h`, `orb × 1d` |
| `signal_watch_weekdays` | 32 | 36 | `ema × 1wk`, `eqh_eql × 1wk`, `orb × {1d, 1wk}` |

Five of the six are harmless — the detector produces 0 signals, which is why
`check-dead-surfaces` never saw them (it only walks the *declared* set).
**`bos × 4h` is not**: 283 signals and 254 closed trades across 3 sweeps. Those
signals enter the Phase-2 conflict-resolver pool for `(symbol, 4h)`, where they
can beat and drop a declared cell's opposing signals — a competitor the live
daemon does not have. So the "live-parity" sweep is not at parity on `4h`, and
the declared cells' own measured avg_r is affected.

Left for its own PR: the fix changes which rows `backtest_runs` receives, moves
`_backtest_run_id` hashes, and needs a separate falsification.

## Fix

- `declared_cells` moved to `analytics/signal_config.py` — one definition shared
  by the tool that asks "which declared cells are dead?" and the recalibrate
  path that asks "which rated cells are undeclared?". Opposite ends of the same
  question; a forked copy would let a cell be dead under one and healthy under
  the other.
- `get_backtest_win_rates` gains `declared` and unconditionally filters to
  `sweep_id IS NOT NULL`. Applied in the one function all three consumers share
  (report, combined ratings, directional ratings) so they cannot disagree.
- `prune_undeclared_confidence_ratings` deletes a config's undeclared rows and
  returns them so the runner can print what it removed. A declared cell that is
  merely *unrated this run* (too few trades) is deliberately left alone.
- The prune runs even when nothing is ratable — an orphan is by definition a row
  no current run overwrites, so gating it on "something to write" would skip the
  worst case.
- `tools/dead_surface_check.py` reports the inverse and exits 1 on it.
- `make db-update`'s completion banner is now conditional on that check.

After applying: `signal_watch` **22 declared / 22 rated**, weekdays **32 / 32**,
zero orphans. Two surviving cells changed star tier
(`inside_bar × 4h` 1★→2★, `orb × 4h` 4★→3★). Golden fixtures are unaffected —
`tests/test_regression.py` runs `run_backtest` on frozen OHLCV with no DB.

## Transferable rules

1. **A dead surface reads as absence; an orphan reads as evidence.** Both are
   silent, but the orphan is worse: `check-dead-surfaces` looks for zeros, and
   an orphan is a *number*, sitting in a sorted table above every live cell. The
   inverse of an existing check is not automatically covered by it, and here the
   two ran side by side for months — `make db-update` printed "no unexpected
   dead cells" with all nine orphans present.
2. **Deduplicating by recency requires a provenance column when a table has more
   than one writer.** "Latest per key" is only correct if every row is the same
   kind of measurement. `backtest_runs` had two writers with different semantics
   and no discriminator in the dedup, so the *more frequent* writer won by
   default — the live gate, which writes on every scan cycle.
3. **Check the population a filter is silent about.** `get_backtest_win_rates`
   already filtered on `day_filter` and `adr_suppress_threshold` with the stated
   purpose of keeping recalibration "inside one execution context". The axis it
   did not name — which code path produced the row — was the one that broke it.
4. **A config value with two consumers needs one definition, not two agreeing
   copies.** `strategy_timeframes` is honoured by the scanner and ignored by the
   sweep, and nothing compares them; that is defect B, and it is what turned
   #143's `bos × 4h` retirement into a fresh orphaned rating hours later.
5. **Verify the drift you find belongs to your change.** The first prune dry-run
   showed 44 changed rows, which no prune can cause. It was pre-existing: live
   rows written 15 minutes after the last recalibrate. Attributing it to the fix
   would have hidden defect C instead of finding it.
