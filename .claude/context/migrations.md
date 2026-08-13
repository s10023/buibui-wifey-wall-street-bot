# `migrations/` — hand-run one-shot DB migrations

Two scripts, both already applied. **Routine schema changes do NOT go here** — they belong in
`analytics/store/schema.py`'s migration list, which runs automatically on connect. This directory
is only for changes that list cannot express: a column's *type* changing, or existing row *values*
being rewritten.

**Nothing runs these for you.** They are not in the Makefile, not in `make db-update`, and not in
CI. Each is invoked by hand, once, and is not idempotent in the sense of being safe to re-reason
about — read the docstring before touching either.

## The two invariants both scripts enforce

**A `.bak` must exist alongside the DB or the script refuses to start**
(`001:45-49`, `002:63-65`). Note what this is and is not: `analytics.db.bak` is an *undated,
unverified byte copy*, not a backup. `make backup` is the real snapshot. Create the `.bak`
anyway — the guard is what stands between a bad migration and an unrecoverable DB.

**Both rewrite `run_id`, and both cascade it to `backtest_trades`.** `run_id` is a hash over the
run's parameters, so changing any hashed column changes the row's identity. This is the reason
neither change could be done with a plain `UPDATE`: flipping the value in place would leave the
old row behind and manufacture a fake before/after pair. Migration 002 additionally **checks for
run_id collisions and aborts** rather than silently merging two measurements.

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

## The transferable rule

**A migration must respect code eras.** "Fix every row" is the wrong default when the column
records what the code *did*: rows written under different code recorded different — and possibly
correct — things. Establish the commit boundary first, then migrate only forward of it. The
enforcing mechanism that keeps this from recurring is
`effective_adr_threshold(declared, timeframe, adr_exempt=…)`, a **required** kwarg on
`upsert_backtest_run`, so a new call site cannot omit it. Full narrative:
the `002_adr_threshold_executed.py` docstring, and CLAUDE.md → Footguns.
