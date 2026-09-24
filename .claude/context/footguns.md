# Footgun narratives

CLAUDE.md keeps each footgun's rule, its enforcing mechanism, and the transferable lesson — the
part a session needs before it acts. The discovery narrative and the full measured impact live in
the linked committed audit.

This file is the committed home for the entries that have no audit of their own. Read it when you
are about to change one of these mechanisms and want to know what the rule was bought with.
Everything else is reachable from CLAUDE.md's own pointers.

## DuckDB read-only opens and `IOException`

On duckdb 1.5.5 only reader-vs-reader sharing works: a writer refuses a read-only opener with the
same `Conflicting lock` message a second writer gets (verified 2026-08-12 by holding a connection
from a child process). `read_only=True` does not protect against a live writer.

DuckDB also raises one exception class for every I/O failure, so a bare `except duckdb.IOException`
cannot tell "database missing or corrupt" apart from "another process holds it". Narrow every
handler with `is_lock_conflict`: an unnarrowed handler reports a missing or corrupt database as
"busy, try again in a few seconds" (advice that can never come true), and `main.py`'s bare
`except: pass` around the startup open silently served an API with no schema and no complaint.

Scope an enumeration of call sites by the predicate it claims (`git grep 'except
duckdb.IOException'`), never by the directory you happened to be reading. One caller,
`signal_runner.py`'s per-symbol `sync` handler, sat outside `web/` and had the worst failure mode
of the three: it logged "will retry" and the cycle went on to scan, alert and backfill outcomes
against stale data, with no next cycle to retry in under `make go-live --once`. Both directions
are pinned by `tests/test_signal_runner.py::TestSyncIoErrorsAreNarrowed`; since the handler was
extracted as `_sync_watched_series`, `TestSyncWatchedSeriesDirect` also pins the `ValueError`
fallback.

`connect_with_retry` is preventive, not a repair for a collision already in flight. Two systemd
timers open `analytics.db`: `wifey-backup.timer` (08:10 / 13:10 UTC) and
`wifey-signal-watch.timer` (08:30 UTC). Their 20-minute separation is a property of the schedule,
and `Persistent=true` suspends it — after a resume from suspend both missed fires are queued at
once, which is the collision the retry logic exists for. `RandomizedDelaySec` (120 / 60) spreads
them but guarantees neither ordering nor non-overlap, so the retry budget and
`backup-analytics.sh`'s own lock retry are what carry that case rather than the timetable. Neither
unit is a daemon — both are `Type=oneshot` — and the retry budget deliberately does not outlast a
`make db-update` sweep, because a job that collides with one should fail loudly rather than hang.

`web/api/routers/stats.py`'s cache write is the one deliberate exception to the retry rule: it
sits on the request path, where a ~52s retry would block the response the cache exists to speed
up.

## `INSERT OR REPLACE` with a partial row

A writer that owns only some of a table's columns must not replace the whole row.
`upsert_signal_outcome` takes a 16-column row, but the scanner — its only production caller —
passes no `outcome` / `outcome_r` / `outcome_filled_at_ms` key, so `row.get(col)` returned NULL on
those columns and a re-detection could erase a resolved label. `ON CONFLICT … DO UPDATE` with
`COALESCE(excluded.x, x)` on the three outcome columns fixes it: a re-detection can only add a
value, never null one out.

This is preventive rather than a repair for observed damage: across all 295 events exactly one
re-write has ever occurred (`ADBE-1d-eqh_eql`, 31 minutes after first detection on 2026-06-05,
while still unresolved), so no resolved label is known to have been destroyed. The other writer,
`backfill_outcomes`, issues a direct `UPDATE` and never collided, and `signal_runner.py:350`
re-derives the label right after the scan inside a try/except that logs but never blocks the
cycle, so a loss would have surfaced only if the backfill itself threw.

To tell a re-detection from a first insert, compare the two writers' conflict policies rather than
counting same-day rows: `signals` is `INSERT OR IGNORE` (first write wins) while the ledger was
`INSERT OR REPLACE` (last write wins), so `signals.fired_at <> outcomes.fired_at` proves a
re-write happened. Thirteen ledger rows once sharing a stamp date read as re-detections on a raw
count, until this comparison showed they were first inserts in both tables — a catch-up scan
discovering and resolving historical signals in the same cycle. Find the query that discriminates
before quoting a count as evidence of a mechanism. Test both directions: "never update outcome"
passes the obvious test and breaks every caller that legitimately sets one.

## The sleeve gate's MinTRL leg

All four equity sleeves (`xsmom`, `lowvol`, `xasset`, `pead`) declared `_GATE_SHARPE = 0.7` as
their pre-registered net-of-cost bar and then ANDed it with `n_obs >= min_trl`, where
`min_track_record_length` was called with `target_sr` equal to an annualized Sharpe of 1.0.

MinTRL against a non-zero target asks "can I confirm Sharpe ≥ 1?", so it returns `inf` for any
sample at or below that target — no amount of data confirms a hypothesis the sample contradicts.
The effective bar was therefore ~2.174 at n=500, ~1.585 at n=2000, ~1.478 at n=3000. The whole
0.7–1.58 band cleared every threshold the code named and was rejected by one it did not.
`_DEPLOY_SHARPE = 1.0` was inert for the same reason — only consulted on a cell that already
passed, and a passing cell was already above 1.58.

No recorded verdict rested on this. The gate is read on four committed cells (`xsmom`'s residual
grid, `lowvol`, `xasset`, `pead`; `forecast` computes `min_trl` and applies no gate at all), and
each already fails on DSR, PBO, `boot_lo`, or the 0.7 Sharpe leg — all of which bind before
MinTRL.

Re-targeting to `target_sr = 0` was considered and rejected as a no-op, which is the sharper half
of the lesson. MinTRL round-trips with PSR, and `deflated_sharpe_ratio` is PSR with the benchmark
at the expected-max Sharpe, which is never negative — so `DSR ≥ 0.95` strictly implies
`min_trl(0) ≤ n_obs` and the leg could never fire. Measured: of 124,882 DSR-passing draws out of
300,000, zero would have been blocked. A fix that turns a mis-calibrated guard into an unfirable
one is not a fix. The parent excludes `min_trl` for the same reason and calls the four-leg form "a
documented recurring error".

The expression was inlined four times in production plus a fifth time inside
`tests/test_xsmom_residual_report.py`, which re-derived it to build its own expected value and so
passed against any implementation — nothing but extraction to one definition could have made the
leg set legible. That extraction was proven verdict-neutral over 2,985,984 exhaustive combinations
(including NaN and ±inf) plus 200k random draws, 0 mismatches. Removing the MinTRL conjunct is
neutral by a monotonicity argument instead: dropping a conjunct can only turn `False` into `True`,
so the only cells at risk are ones blocked solely by MinTRL, and
`TestRecordedVerdictsAreUnchanged` pins that none of the four recorded cells is one.

Reproduction script (calls the production functions rather than restating their arithmetic):
`docs/plans/scripts/sleeve_gate_mintrl_bar.py` — that tree is gitignored and single-copy.

## Bar counts read as calendar spans

Three instances share the same crypto-inherited shape: a bar count treated as a calendar span on
an RTH tape, where it is only exact on a 24/7 one.

**Forward windows.** A forward window fetched as
`max(candle_ts) + (max_hold + 2) * tf_ms` is exact on a 24/7 tape and covered 0.0% of real equity
windows here. Thirty `4h` bars span ~132 `4h` units of wall-clock (p95 150, max 161), and 14 `1d`
bars span ~20 (p95 22). The truncation is silent — it marks would-be winners to market at the last
fetched bar — and it biases an A/B, because a short window cannot touch a policy whose time-stop
fires at bar 3 but truncates the long-held baseline. The tell was a positive control against
production sitting at 96–99% rather than 100%, high enough to read as rounding noise. Fetch
forward windows to `get_latest_open_time` rather than deriving a horizon from a bar count.

**Regime history windows.** `analytics/regime.py` carried a private crypto table (`4h: 6`,
`1h: 24`) against `cost_model.py`'s correct RTH values. `history_window = bars_per_day *
_ATR_HISTORY_DAYS` therefore looked back 540 bars ≈ 270 sessions where it said 90. 12.02% of `4h`
labels moved (`1h` 12.52%), and the dispatch/ratings blast radius is zero: `[bias.regime]
mode = "soft"` keeps 34/34 events in every regime, verified with a `mode="hard"` control that
drops 30–32.

Deduping the crypto table against the correct one, rather than just swapping the values, surfaced
two further defects that a value swap alone would have hidden: `1wk` was missing from regime's
table, so `tools/strategy_edge_audit.py` raised on every run against the real DB (1,056 `1wk`
trades); and once present, the `max(50, …)` `min_periods` floor exceeded the 18-bar `1wk` window,
which pandas rejects outright. `.claude/context/analytics.md` had said "90-day rolling" all along
— the doc was right and the code was wrong, so this restored a documented calibration rather than
choosing a new one.

Re-derive any crypto-inherited bar-count constant against equity bar counts before trusting it —
`4h` RTH is 2 bars/day, not 6.

## Average of averages beside a sum of counts

Pool a mean over its denominator. `get_backtest_win_rates` summed `closed_trades` across a cell's
symbols but took a plain `mean()` of `avg_r`, so a 1-trade symbol moved the star rating exactly as
far as a 50-trade one, while `min_trades` guarded the pooled count instead. The tell was inside the
row, not outside it: `win_rate` was already `SUM(win_count)/SUM(trades)`, so one row disagreed with
itself about its denominator.

Fixed 2026-08-13: 38 of 160 `confidence_ratings` rows changed stars, 21 crossed zero (22 star moves
on `signal_watch`, 16 on weekdays); coverage unchanged. Ratings drive alert stars and which
direction leg dispatches when both fire (`analytics/signal/gates.py:150`), so the sign flips are
operational.

A correct idiom already in the file had not propagated: `query_strategy` / `query_tf` in
`digest_lib.py` had used `SUM(avg_r * closed_trades) / SUM(closed_trades)` all along while four
sibling queries drifted to `AVG(avg_r)`. The expression is now `digest_lib::_pooled`, defined once
for all six, because inlining is what let them diverge. Masking numerator and denominator together
is preventive here: a directional average is NULL exactly when that direction has no trades, and
counting those trades in the divisor alone would invent them at R=0 — measured 0 of 3,268 rows have
`n > 0 AND avg_r IS NULL`.

The fix was confined to a producer, so consumers needed re-deriving rather than re-running: it
could not move `backtest_runs` or the regression goldens, because `_build_confidence_ratings_map`
returns `None` while `conflict_resolver` is off, and `make db-update-recalibrate` alone was
sufficient. No fixture in either test file had put two symbols in one cell, so the entire
cross-symbol path was untested; seventeen tests were added, each mutation-checked in both
directions.

## A seam that disables a side effect, and the `:-` that re-enables it

A seam that exists to disable a side effect must be tested for the disabled case. Testing that a
notifier fires proves the wiring; it says nothing about the one input the seam was added for.

`deploy/windows/job.sh`'s notifier seam was `${WIFEY_NOTIFY:-deploy/notify-failure.sh}`. The `:`
form substitutes the default when the variable is unset or empty, so a test setting
`WIFEY_NOTIFY=""` — the obvious way to disable a notifier — resolved to the real one and sent two
live Telegram messages to the operator's personal channel on 2026-09-18. It is `${VAR-default}`
now, which substitutes only when the name is genuinely unset. The two forms differ in exactly one
character and the wrong one is the one people type from memory, so this is caught only by a test
that asserts silence, not by review.

wifey has two bot tokens, so a notifier misfire has two chances to reach a channel with a human
audience. Measurement: memory `project_session_log_2026-09`.

## A gate run under memory pressure is uninterpretable, not red

Check paged pool and free physical memory before filing any red result as real. This box leaked
paged pool to 42.8 GiB on 15.4 GiB of RAM (cleared by a reboot on 2026-09-19); under that pressure
an OOM and a real failure are indistinguishable, and the tells are easy to chase as the wrong
defect: `MemoryError` on a 1.58 MiB allocation, and `poetry` itself failing with `WinError 1450`.

`tests/test_pead_report.py` timed out twice under the leak and then passed at 25s against the 30s
cap on a rebooted box, so that red was pressure, not a latent bug. The residual fragility is
separate and real: ~17% headroom on the suite's two slowest tests, confirmed structural across two
run shapes a day apart.
