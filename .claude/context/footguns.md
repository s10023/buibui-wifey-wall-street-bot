# Footgun narratives

CLAUDE.md keeps each footgun's **rule**, its **enforcing mechanism**, and the **transferable
lesson** — the part a session needs before it acts. The discovery narrative and the full measured
impact live in the linked committed audit.

This file is the committed home for the entries that have **no** audit of their own. Read it when
you are about to change one of these mechanisms and want to know what the rule was bought with.
Everything else is reachable from CLAUDE.md's own pointers.

## DuckDB read-only opens and `IOException`

On duckdb 1.5.5 only reader-vs-reader shares a database file. A writer refuses a read-only opener
with the *identical* `Conflicting lock` message a second writer gets — verified 2026-08-12 by
holding a connection from a child process. So `read_only=True` buys nothing against a live writer,
and code that assumed otherwise was silently wrong.

DuckDB also raises one exception class for every I/O failure. Three handlers caught bare
`duckdb.IOException` and reported a missing or corrupt database as "busy, try again in a few
seconds" — advice that can never come true — while `main.py`'s `except: pass` started the API with
no schema and no complaint. Both `web/` messages also blamed "the signal-watch daemon", which this
fork does not have.

⚠ **The third was missed because the enumeration was scoped to a DIRECTORY.** Two were fixed in
`web/` on 2026-08-12; `signal_runner.py`'s per-symbol `sync` handler was not found until
2026-08-20, and until then this paragraph and `db_retry.py`'s own docstring both said "two
callers in `web/`" — a count that reads as complete and so stops anyone looking for a third.
Its failure mode was the worst of the three: it logged "will retry" and the cycle went on to
scan, alert and backfill outcomes against stale data, and `make go-live` runs `--once`, so the
promised retry had no next cycle to happen in. **Scope an enumeration by the PREDICATE it claims
(here, `git grep 'except duckdb.IOException'`), never by the directory you happened to be
reading.** Both directions are pinned by
`tests/test_signal_runner.py::TestSyncIoErrorsAreNarrowed`.

The retry helper is **preventive, not a repair**. Upstream's premise is colliding systemd timers;
wifey has no daemon. Its budget deliberately does not outlast a `make db-update` sweep, because a
job that collides with one should fail loudly rather than hang.

`web/api/routers/stats.py`'s cache write is the one deliberate exception to the retry rule: it sits
on the request path, where a ~52s retry would block the very response the cache exists to speed up.

## `INSERT OR REPLACE` with a partial row

`upsert_signal_outcome` took a 16-column row. The scanner — its only production caller — passes no
`outcome` / `outcome_r` / `outcome_filled_at_ms` key, so `row.get(col)` returned NULL and a
re-detection erased a resolved label.

Fixed 2026-08-12 with `ON CONFLICT … DO UPDATE` plus `COALESCE(excluded.x, x)` on the three outcome
columns. **Preventive, not a repair**: across all 295 events exactly one re-write has ever occurred
(`ADBE-1d-eqh_eql`, 31 minutes after first detection on 2026-06-05, while still unresolved), so no
resolved label is known to have been destroyed.

The damage was invisible from the other writer's side — `backfill_outcomes` issues a direct
`UPDATE`, so it never collided.

Ordering also masked it: `signal_runner.py:350` re-derives the label right after the scan, so a
loss surfaces only if the backfill throws, and that call sits in a "logged but never blocks the
cycle" try/except.

The retraction this cost. Thirteen ledger rows stamped 2026-08-11 were first read as re-detections.
They were **first inserts in both tables** — a catch-up scan discovering historical signals and
resolving them in the same cycle. What discriminates is that the two writers hold opposite conflict
policies: `signals` is `INSERT OR IGNORE` (first write wins) while the ledger was
`INSERT OR REPLACE` (last write wins), so `signals.fired_at <> outcomes.fired_at` *proves* a
re-write happened. Find the query that discriminates before quoting a count as evidence of a
mechanism.

## The sleeve gate's MinTRL leg

All four equity sleeves (`xsmom`, `lowvol`, `xasset`, `pead`) declared `_GATE_SHARPE = 0.7` as their
*"pre-registered net-of-cost bar"* and then ANDed it with `n_obs >= min_trl`, where
`min_track_record_length` was called with `target_sr` equal to an annualized Sharpe of 1.0.

MinTRL against a non-zero target asks *"can I confirm Sharpe ≥ 1?"*, so it returns `inf` for any
sample at or below that target: no amount of data confirms a hypothesis the sample contradicts. The
effective bar was therefore ~2.174 at n=500, ~1.585 at n=2000, ~1.478 at n=3000. The whole 0.7–1.58
band cleared every threshold the code *named* and was rejected by one it did not.
`_DEPLOY_SHARPE = 1.0` was inert for the same reason — only consulted on a cell that already
passed, and a passing cell was already above 1.58.

**No recorded verdict rested on this.** The gate is read on four committed cells (`xsmom`'s
residual grid, `lowvol`, `xasset`, `pead`; `forecast` computes `min_trl` and applies no gate at
all), and each already fails on DSR, PBO, `boot_lo`, or the 0.7 Sharpe leg — all of which bind
before MinTRL.

**Re-targeting to `target_sr = 0` was considered and rejected as a no-op**, which is the sharper
half of the lesson. MinTRL round-trips with PSR, and `deflated_sharpe_ratio` *is* PSR with the
benchmark at the expected-max Sharpe, which is never negative. So `DSR ≥ 0.95` strictly implies
`min_trl(0) ≤ n_obs` and the leg could never fire. Measured: of 124,882 DSR-passing draws out of
300,000, zero would have been blocked. A "fix" that turns a mis-calibrated guard into an unfirable
one is not a fix.

The parent excludes `min_trl` for the same reason and calls the four-leg form *"a documented
recurring error"*.

Why the divergence hid: the expression was inlined four times in production plus a fifth time
inside `tests/test_xsmom_residual_report.py`, which re-derived it to build its own expected value
and so passed against any implementation. Extraction to one definition is what made the leg set
legible at all.

Neutrality evidence. The extraction was proven verdict-neutral over 2,985,984 exhaustive
combinations (including NaN and ±inf) plus 200k random draws, 0 mismatches. The removal is neutral
by a monotonicity argument instead: dropping a conjunct can only turn `False` into `True`, so the
only cells at risk are ones blocked *solely* by MinTRL, and `TestRecordedVerdictsAreUnchanged` pins
that none of the four recorded cells is one.

Reproduction script (calls the production functions rather than restating their arithmetic):
`docs/plans/scripts/sleeve_gate_mintrl_bar.py` — note that tree is gitignored and single-copy.

## Bar counts read as calendar spans

Three instances, all the same crypto-inherited shape.

**Forward windows.** Upstream #437 fetched a trade's forward window as
`max(candle_ts) + (max_hold + 2) * tf_ms`. Exact on a 24/7 tape; it covered **0.0%** of real equity
windows here. Thirty `4h` bars span ~132 `4h` units of wall-clock (p95 150, max 161), and 14 `1d`
bars span ~20 (p95 22). The truncation is silent — it marks would-be winners to market at the last
fetched bar — and it biases an A/B, because a short window cannot touch a policy whose time-stop
fires at bar 3 but truncates the long-held baseline. The tell was that the positive control against
production sat at 96–99% rather than 100%, high enough to read as rounding noise.

**Regime history windows.** `analytics/regime.py` carried a private crypto table (`4h: 6`,
`1h: 24`) against `cost_model.py`'s correct RTH values — one repo, both values, the whole life of
the fork. `history_window = bars_per_day * _ATR_HISTORY_DAYS` therefore looked back 540 bars ≈ 270
sessions where it said 90. **12.02%** of `4h` labels moved (`1h` 12.52%), and the dispatch/ratings
blast radius is **zero**: `[bias.regime] mode = "soft"` keeps 34/34 events in every regime, verified
with a `mode="hard"` control that drops 30–32.

**Why deduping beat correcting.** Swapping the values alone would have hidden two further defects,
both found only because deduping put the whole key set in scope. `1wk` was *missing* from regime's
table, so `tools/strategy_edge_audit.py` raised on every run against the real DB (1,056 `1wk`
trades). And once present, the `max(50, …)` `min_periods` floor *exceeded* the 18-bar `1wk` window,
which pandas rejects outright.

`.claude/context/analytics.md` had said "90-day rolling" all along: the doc was right and the code
was wrong, so this restored a documented calibration rather than choosing a new one.

## Average of averages beside a sum of counts

`get_backtest_win_rates` summed `closed_trades` across a cell's symbols but took a plain `mean()`
of `avg_r`, so a 1-trade symbol moved the star rating exactly as far as a 50-trade one — while
`min_trades` guarded the *pooled* count.

Impact, fixed 2026-08-13: **38 of 160 `confidence_ratings` rows changed stars, 21 crossed zero** (22
star moves on `signal_watch`, 16 on weekdays); coverage unchanged. Ratings drive alert stars and
which direction leg dispatches when both fire (`analytics/signal/gates.py:150`), so the sign flips
are operational.

The tell was inside the row, not outside it: `win_rate` was already
`SUM(win_count)/SUM(trades)`, so one row disagreed with itself about its denominator.

A correct idiom already in the file did not propagate. `query_strategy` / `query_tf` in
`digest_lib.py` had used `SUM(avg_r * closed_trades) / SUM(closed_trades)` all along while four
sibling queries drifted to `AVG(avg_r)`. The expression is now `digest_lib::_pooled`, defined once
for all six, because inlining is what let them diverge.

Masking numerator and denominator together is preventive here: a directional average is NULL
exactly when that direction has no trades, and counting those trades in the divisor alone would
invent them at R=0. Measured 0 of 3,268 rows have `n > 0 AND avg_r IS NULL`.

The fix was confined to a producer, so consumers needed re-deriving rather than re-running: it
could not move `backtest_runs` or the regression goldens because `_build_confidence_ratings_map`
returns `None` while `conflict_resolver` is off, and `make db-update-recalibrate` alone was
sufficient.

No fixture in either test file had put two symbols in one cell, so the entire cross-symbol path was
untested. Seventeen tests were added, each mutation-checked in both directions.
