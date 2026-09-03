# `migrations/` — hand-run one-shot DB migrations

Six scripts. **Routine schema changes do NOT go here** — they belong in
`analytics/store/schema.py`'s migration list, which runs automatically on connect. This directory
is only for changes that list cannot express: a column's *type* changing, or existing row *values*
being rewritten.

**Nothing runs these for you.** They are not in the Makefile, not in `make db-update`, and not in
CI. Each is invoked by hand, once, and is not idempotent in the sense of being safe to re-reason
about — read the docstring before touching either.

## The one invariant ALL SIX scripts enforce

**A `.bak` must exist alongside the DB or the script refuses to start**
(`001:45-49`, `002:63-65`, `003:73-75`, `004:119-121`, `006:95-97`). Note what this is and is not: `analytics.db.bak` is an
*undated, unverified byte copy*, not a backup. `make backup` is the real snapshot. Create the
`.bak` anyway — the guard is what stands between a bad migration and an unrecoverable DB.

## `run_id` rewriting is a property of the TABLE, not of migrations

**001 and 002 rewrite `run_id` and cascade it to `backtest_trades`; 003 and 004 do neither.** `run_id`
is a hash over `backtest_runs`' parameters, so changing any hashed column changes the row's
identity — which is why neither of those could use a plain `UPDATE`: flipping the value in place
would leave the old row behind and manufacture a fake before/after pair. 002 additionally
**checks for run_id collisions and aborts** rather than silently merging two measurements.

003 and 004 target `signal_alert_outcomes`, whose key is
`{symbol}-{tf}-{strategy}-{open_time}-{direction}` — pure identity, with no measured value in it.
So an in-place `UPDATE` is correct there and no cascade exists. **Check what the target table's
key is made of before assuming either pattern applies**; the answer, not the precedent, decides.

## 001_day_filter_text.py — `backtest_runs.day_filter` BOOLEAN → TEXT

Fork-era (2026-05-14). Converts `TRUE → "tue_thu"` (was: suppress Mon + Fri) and
`FALSE → "off"`, then recomputes every `run_id` and cascades to `backtest_trades`.

**Run:** `python migrations/001_day_filter_text.py [--db PATH]` — **no dry-run flag**; it writes
on invocation once the `.bak` guard passes.

## 002_adr_threshold_executed.py — `adr_suppress_threshold` declared → executed

2026-08-11. The column is **provenance** — it answers "what suppressed the signals behind this
row" — and both writers had been storing the config's *declared* value unconditionally, so
**2,091 of 3,246 rows (64.4%)** claimed a gate that never touched them (1,974 by timeframe, 117 by
`adr_exempt`). A declared-but-not-executed value makes the audit trail corroborate a gate that did
not run. Same defect class as #144's `days`.

**It rewrites only rows the current code and config govern** — the **518** written at or after
PR #142 (`54cef12`, 2026-08-06T09:07:01Z). Earlier rows are left alone deliberately: before #142
`_filter_signals_by_adr` had no timeframe guard, so on `1d`/`1wk` the gate genuinely *did* run
(degenerately — see `docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md`), and #141 had not
yet moved `adr_exempt` into the shared base. Their `0.80` is defensible as written, and the config
state at write time is recorded nowhere, so it cannot be reconstructed. **Rewriting them would
replace one false claim with another.**

The migration is provably rating-neutral because `recalibrate_lib` already assumed the corrected
semantics — the fix *restored* a calibration rather than choosing a new one.

**Run:** `python migrations/002_adr_threshold_executed.py [--db PATH] [--apply]` — **dry-run by
default**; prints what would change and exits without writing.

## 003_outcome_r_implied_tp.py — the ledger credited the DECLARED target

2026-08-14. `signal_alert_outcomes.rr_ratio` recorded the *configured* `tp_r` while `tp_price`
held a detector's **structural** TP when it had one; `_scan_forward` walked `tp_price` and
credited `rr_ratio`, so an alert whose TP sat 2.0R away booked 5.0R. **34 of 298** rows diverged
(up to 3.0R): 8 resolved wins worth **+13.50R** of phantom credit, and **4 still open**. Pooled
live `avg_r` moved **−0.1247 → −0.1752**. Same declared-vs-executed family as 002.

**Two columns, two rules.** `rr_ratio` is rewritten on every divergent row (it WAS read as an R
level by `exits/mfe_mae.py`'s win clamp — that consumer now re-derives the target via
`implied_tp_r`, so this migration is no longer what stands between it and a declared value); `outcome_r` on **wins only**,
because a loss books −1.0 and an expired row marks to market off `sl_price` — neither ever read
`rr_ratio`. Both values are re-derived from the row's own stored geometry via the production
`implied_tp_r`, never from config, so nothing is reconstructed.

**No era cutoff, deliberately — and the contrast with 002 is the point.** 002 had to establish a
commit boundary; 003 established there is none, **from the data rather than from git**. An era
split would show up as resolved wins whose `outcome_r` matches the *implied* target while
disagreeing with `rr_ratio` — measured on the pre-migration `.bak`, **45 of 45** wins credited
exactly `rr_ratio` and **0** credited anything else, across the table's whole `fired_at_ms` range.
The file's history cannot answer this: it predates the `analytics/signal/` split and the fork's
history was copied, so `git show <old-sha>:<path>` returns nothing.
The other historical writer, `tools/backfill_null_tp_outcomes.py`, forces the
pct fallback (`struct_sl=struct_tp=0.0`), which makes implied ≡ declared by construction, so its
rows never diverge. **Idempotent** — it compares stored against derived, so a re-run finds nothing.

**Run:** `python migrations/003_outcome_r_implied_tp.py [--db PATH] [--apply]` — **dry-run by
default**, and the dry run opens the DB `read_only` so it cannot write or hold a write lock.
Unlike 001/002 this one has tests: `tests/test_migration_003_implied_tp.py`.

## 004_live_ledger_net_of_cost.py — the ledger was GROSS, the backtest was NET

2026-08-19. `run_backtest` charged its trades from Phase 0.4 onward and the outcome resolver
charged nothing, so `outcome_r` was a gross figure in the same units as a net one — biasing every
backtest-vs-live comparison **in favour of live**, the opposite direction to the one a reader
assumes. **All 292 resolved rows** restated; pooled `avg_r` **−0.2050 → −0.2192**, mean drag
**0.0141R** (range 0.0015–0.0489), total −4.1298R. All 292 priced from a real cost context, **0**
widest-bucket fallbacks, **0** unpriceable. The 32 open rows are untouched by design — they are
charged when they resolve.

**Adds a column, and the migration adds it itself.** `outcome_cost_r` is in `schema.py` too, but a
DB not opened by the app since that change would not have it, so the migration runs its own
`ALTER`. *A migration that only works after something else has run is a migration that fails in the
field.*

**No era cutoff, and for a stronger reason than 003's.** 003 had to *establish from the data* that
there was one era; here it is structural — the resolver charged zero from the first row to the
last. The guard is `outcome_cost_r IS NULL`, i.e. **state rather than a date**, which makes it
idempotent by construction and means a row written by the fixed resolver is never re-charged.
⚠ Compounding here would be **undetectable**, because gross is not stored independently — it is
only recoverable as `outcome_r + outcome_cost_r`, which a second charge would also shift. That is
why `tests/test_migration_004_net_of_cost.py` pins the second run as a no-op.

**NULL is not 0.0.** A row without geometry cannot be priced and stays NULL; stamping `0.0` would
claim a measured zero *and* hide the row from any later run.

**Refuses when the two live configs disagree on a cost basis**, rather than picking one:
`signal_alert_outcomes` records no config provenance, so rows are not attributable. They agree
today because `[backtest.cost_model]` lives in the shared base, and the test asserts it.

**Run:** `python migrations/004_live_ledger_net_of_cost.py [--db PATH] [--apply]` — **dry-run by
default**, opening the DB `read_only` so it cannot write or hold a write lock. Tested, like 003.

⚠ **What it does NOT fix:** a gap *through* the stop still books −1.0R, and `engine.py:1116` does
the same, so that absence is **shared** and does not bias the comparison — but it is ~7× larger
(−0.10R/row vs −0.014R). Audit: `docs/audits/2026-08-19-live-ledger-net-of-cost.md`.

## 005_symmetric_gap_fill.py — the ledger booked the LEVEL, not the fill

A bar that OPENS beyond a level fills at the open, not at the level. The resolver booked a
flat `-1.0` for every loss and `implied_tp_r` for every win regardless; `engine.py` did the
same, so the absence was SHARED. Both fixed forward through the one shared
`analytics/backtest/fills.py`; this restates the 264 already-written win/loss rows.

⚠ **SYMMETRIC — and it nearly shipped one-sided.** The 2026-08-19 audit measured only
gapped losses. The mirror is bigger (26.1% of wins gap through their target vs 21.1% of
losses through their stop), so the adverse-only version would have moved pooled `avg_r`
-0.2192 -> -0.3218 against a symmetric **-0.2553** — a ~65% overstatement, in the direction
that makes sleeves look worse against exactly the stop-free benchmark the fix was for.

Three properties worth copying:

- **Cost is CARRIED OVER, not recomputed.** `live_cost_r` never reads the exit *price*, and
  the fill rule never changes which bar resolved, so every cost input is unchanged.
  Recomputing would re-derive the same number while adding a second way for 004's basis to
  drift.
- **Scoped to `outcome IN ('win','loss')`.** An `expired` row marks to market at the last
  close, so a fill rule cannot reach it — and its `outcome_filled_at_ms` moves with the
  fetch window, which put 10 rows in the mismatch column for reasons unrelated to the
  migration. Scope a restatement to the rows the change can actually reach.
- **Idempotent BY VALUE, not by a state flag.** Only rows whose recomputed value differs
  are written, so a re-run reports zero changes and no "already restated" column was added
  to record a one-off.

⚠ **Its first draft hit the RTH bar-count footgun.** The fetch window was
`latest + (max_hold + 2) * tf_secs`; `4h` RTH is 2 bars/day, so that is short by ~6x, and
the replica check reported 6 of 264 unreproducible. Bounding at the table's own
`MAX(open_time)` took it to **264/264**. The symptom was quiet — six rows conservatively
skipped, a count small enough to read as data rather than as a query bug.

**Run:** `python migrations/005_symmetric_gap_fill.py [--db PATH] [--apply]` — dry-run by
default; prints the replica check, the loss/win split of changed rows, and before/after
pooled `avg_r`.

## 006_purge_frozen_tail_bars.py — the provider kept quoting a stopped tape

The first migration to target `ohlcv`, and the first to DELETE rather than rewrite. When a
name stops trading the provider forward-fills its final print at zero volume for as long as
the series is still carried; `EA` and `EQR` each ended with a huge-volume session followed by
four such bars. They are not observations, and in a pooled cross-section they read as
consecutive exact-zero returns with no variance.

⚠ **POSITION IS THE DISCRIMINATOR; THE ROW SHAPE ALONE IS NOT — and this nearly shipped the
other way.** The defect was first described as "zero volume AND an unchanging close", which
is how it presents. Measured over the whole DB that predicate matches **1,368 rows**, of which
only **9** are the dead tails — the other **1,359** sit mid-history in live names (808 `SW`,
231 `AMCR`, 188 `^GSPC`, 36 `^TNX`). Requiring the run to **terminate the series** leaves
exactly those 9, across 3 (symbol, timeframe) series; the nearest surviving frozen row is 81
bars from its series end (`^TYX 1d`, full-column scan). The two populations do not overlap, so
the rule has margin — the naive one would have deleted 188 S&P 500 index bars.

⚠ **Zero volume alone is not even a hint.** `DX-Y.NYB`, `^TNX` and `^TYX` are permanently
zero-volume and healthy. 12,507 zero-volume rows are stored and this rule removes **9**; the other
12,498 are *left alone* rather than certified correct — the measurement supports the first claim
and not the second. Every figure reproduces by swapping the run predicate in the script's
`_FROZEN_TAIL_SQL`.

Deletion rather than a flag: `ohlcv` has no quarantine column, and the ingest path expresses
the same decision by never storing the row (`data_quality.frozen_tail_idx`). A deleted bar is
recoverable by refetch and would simply be re-quarantined, so no unique observation is lost.
Idempotent by value — the rule is recomputed each run, so a second run reports zero.

**Run:** `python migrations/006_purge_frozen_tail_bars.py [--db PATH] [--apply]` — dry-run by
default; prints the table total, the zero-volume count it is NOT touching, and the per-series
purge list.

## The transferable rule

**A migration must respect code eras.** "Fix every row" is the wrong default when the column
records what the code *did*: rows written under different code recorded different — and possibly
correct — things. Establish the commit boundary first, then migrate only forward of it. The
enforcing mechanism that keeps this from recurring is
`effective_adr_threshold(declared, timeframe, adr_exempt=…)`, a **required** kwarg on
`upsert_backtest_run`, so a new call site cannot omit it. Full narrative:
the `002_adr_threshold_executed.py` docstring, and CLAUDE.md → Footguns.
