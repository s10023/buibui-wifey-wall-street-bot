# `migrations/` — hand-run one-shot DB migrations

Three scripts, all already applied. **Routine schema changes do NOT go here** — they belong in
`analytics/store/schema.py`'s migration list, which runs automatically on connect. This directory
is only for changes that list cannot express: a column's *type* changing, or existing row *values*
being rewritten.

**Nothing runs these for you.** They are not in the Makefile, not in `make db-update`, and not in
CI. Each is invoked by hand, once, and is not idempotent in the sense of being safe to re-reason
about — read the docstring before touching either.

## The one invariant ALL THREE scripts enforce

**A `.bak` must exist alongside the DB or the script refuses to start**
(`001:45-49`, `002:63-65`, `003:67-69`). Note what this is and is not: `analytics.db.bak` is an
*undated, unverified byte copy*, not a backup. `make backup` is the real snapshot. Create the
`.bak` anyway — the guard is what stands between a bad migration and an unrecoverable DB.

## `run_id` rewriting is a property of the TABLE, not of migrations

**001 and 002 rewrite `run_id` and cascade it to `backtest_trades`; 003 does neither.** `run_id`
is a hash over `backtest_runs`' parameters, so changing any hashed column changes the row's
identity — which is why neither of those could use a plain `UPDATE`: flipping the value in place
would leave the old row behind and manufacture a fake before/after pair. 002 additionally
**checks for run_id collisions and aborts** rather than silently merging two measurements.

003 targets `signal_alert_outcomes`, whose key is
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

**Two columns, two rules.** `rr_ratio` is rewritten on every divergent row (it is read as an R
level by `exits/mfe_mae.py`'s `max(prior_fav, rr_ratio)` win clamp); `outcome_r` on **wins only**,
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

## The transferable rule

**A migration must respect code eras.** "Fix every row" is the wrong default when the column
records what the code *did*: rows written under different code recorded different — and possibly
correct — things. Establish the commit boundary first, then migrate only forward of it. The
enforcing mechanism that keeps this from recurring is
`effective_adr_threshold(declared, timeframe, adr_exempt=…)`, a **required** kwarg on
`upsert_backtest_run`, so a new call site cannot omit it. Full narrative:
the `002_adr_threshold_executed.py` docstring, and CLAUDE.md → Footguns.
