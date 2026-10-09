# `migrations/` — hand-run one-shot DB migrations

Nine numbered scripts, plus a one-time seed for the applied record (below). Routine schema changes do not go here — they belong in
`analytics/store/schema.py`'s migration list, which runs automatically on connect. This directory
is only for changes that list cannot express: a column's type changing, or existing row values
being rewritten.

Nothing runs these for you. They are not in the Makefile, not in `make db-update`, and not in CI.
Each is invoked by hand, once, and is not idempotent in the sense of being safe to re-reason about
— read the docstring before touching either.

## The one invariant all nine scripts enforce

`<db>.bak` must be a byte copy of the DB, or the script refuses to start: every script calls
`analytics/store/migration_bak.py::require_fresh_bak` (unconditionally in `001`, under `--apply`
in the rest), and `tests/test_migration_bak.py` fails on a `migrations/0*.py` that does not, or
whose apply path writes past a stale copy. `analytics.db.bak` is an undated byte copy, not a
backup; `make backup` is the real snapshot. Take a `make backup`, then cut the `.bak`
immediately before every `--apply` (`Copy-Item analytics.db analytics.db.bak -Force`). A
successful apply leaves the `.bak` stale on purpose, since it is the undo, so a re-run needs a
fresh copy too.

The guard compares bytes because nothing cheaper carries freshness. It tested existence only
until 2026-10-09, and two stale copies would have passed it: on 2026-09-03 the `.bak` was 14
days old and 94 MB smaller, and on 2026-10-09 a pre-`009` copy had exactly `analytics.db`'s size
while differing from byte 8,192. DuckDB never shrinks its file on delete, and `Copy-Item`
preserves the mtime. Measured on duckdb 1.5.6, byte equality is not too strict: a read-only or a
no-write read-write open leaves the file identical, and a clean close checkpoints and removes
`<db>.wal`. A pending `.wal` (the file alone is not the whole DB) and an unreadable DB (on Windows
a writer locks it against reads) refuse rather than compare. The compare takes about 0.4 s on the
276 MB DB.

Do not assume an existing `.bak` is redundant with a dated sibling: one such copy differed from
`analytics.db.bak.2026-08-19` byte-for-byte despite an identical size and mtime, so it was
preserved as `analytics.db.bak.2026-08-20` rather than overwritten.

## `run_id` rewriting is a property of the table, not of the migration

001 and 002 rewrite `run_id` and cascade it to `backtest_trades`; 003 and 004 do neither. `run_id`
is a hash over `backtest_runs`' parameters, so changing any hashed column changes the row's
identity — which is why neither of those could use a plain `UPDATE`: flipping the value in place
would leave the old row behind and manufacture a fake before/after pair. 002 additionally checks
for run_id collisions and aborts rather than silently merging two measurements.

003 and 004 target `signal_alert_outcomes`, whose key is
`{symbol}-{tf}-{strategy}-{open_time}-{direction}` — pure identity, with no measured value in it.
So an in-place `UPDATE` is correct there and no cascade exists. Check what the target table's key
is made of before assuming either pattern applies; the answer, not the precedent, decides.

## 001_day_filter_text.py — `backtest_runs.day_filter` BOOLEAN → TEXT

Fork-era (2026-05-14). Converts `TRUE → "tue_thu"` (was: suppress Mon + Fri) and `FALSE → "off"`,
then recomputes every `run_id` and cascades to `backtest_trades`.

**Run:** `python migrations/001_day_filter_text.py [--db PATH]` — no dry-run flag; it writes on
invocation once the `.bak` guard passes.

## 002_adr_threshold_executed.py — `adr_suppress_threshold` declared → executed

2026-08-11. The column is provenance — it answers "what suppressed the signals behind this row" —
and both writers had been storing the config's declared value unconditionally, so 2,091 of 3,246
rows (64.4%) claimed a gate that never touched them (1,974 by timeframe, 117 by `adr_exempt`). A
declared-but-not-executed value makes the audit trail corroborate a gate that did not run. Same
defect class as #144's `days`.

It rewrites only rows the current code and config govern — the 518 written at or after PR #142
(`54cef12`, 2026-08-06T09:07:01Z). Earlier rows are left alone deliberately: before #142
`_filter_signals_by_adr` had no timeframe guard, so on `1d`/`1wk` the gate genuinely did run
(degenerately — see `docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md`), and #141 had not
yet moved `adr_exempt` into the shared base. Their `0.80` is defensible as written, and the config
state at write time is recorded nowhere, so it cannot be reconstructed. Rewriting them would
replace one false claim with another.

The migration is provably rating-neutral because `recalibrate_lib` already assumed the corrected
semantics — the fix restored a calibration rather than choosing a new one.

**Run:** `python migrations/002_adr_threshold_executed.py [--db PATH] [--apply]` — dry-run by
default; prints what would change and exits without writing.

## 003_outcome_r_implied_tp.py — the ledger credited the declared target

2026-08-14. `signal_alert_outcomes.rr_ratio` recorded the configured `tp_r` while `tp_price` held
a detector's structural TP when it had one; `_scan_forward` walked `tp_price` and credited
`rr_ratio`, so an alert whose TP sat 2.0R away booked 5.0R. 34 of 298 rows diverged (up to 3.0R):
8 resolved wins worth +13.50R of phantom credit, and 4 still open. Pooled live `avg_r` moved
−0.1247 → −0.1752. Same declared-vs-executed family as 002.

Two columns, two rules. `rr_ratio` is rewritten on every divergent row — `exits/mfe_mae.py`'s win
clamp used to read it as an R level directly; that consumer re-derives the target via
`implied_tp_r` instead, so this migration is no longer what stands between it and a declared
value. `outcome_r` is rewritten on wins only, because a loss books −1.0 and an expired row marks
to market off `sl_price` — neither ever read `rr_ratio`. Both values are re-derived from the row's
own stored geometry via the production `implied_tp_r`, never from config, so nothing is
reconstructed.

There is no era cutoff, and that absence is itself established from the data rather than assumed:
an era split would show up as resolved wins whose `outcome_r` matches the implied target while
disagreeing with `rr_ratio`, and on the pre-migration `.bak`, 45 of 45 wins credited exactly
`rr_ratio` and 0 credited anything else, across the table's whole `fired_at_ms` range. The file's
own history cannot answer this question: it predates the `analytics/signal/` split and the fork's
history was copied, so `git show <old-sha>:<path>` returns nothing. The other historical writer,
`tools/backfill_null_tp_outcomes.py`, forces the pct fallback (`struct_sl=struct_tp=0.0`), which
makes implied equal declared by construction, so its rows never diverge. Idempotent — it compares
stored against derived, so a re-run finds nothing.

**Run:** `python migrations/003_outcome_r_implied_tp.py [--db PATH] [--apply]` — dry-run by
default, and the dry run opens the DB `read_only` so it cannot write or hold a write lock. Unlike
001/002 this one has tests: `tests/test_migration_003_implied_tp.py`.

## 004_live_ledger_net_of_cost.py — the ledger was gross, the backtest was net

2026-08-19. `run_backtest` charged its trades from Phase 0.4 onward and the outcome resolver
charged nothing, so `outcome_r` was a gross figure sitting in the same units as a net one — biasing
every backtest-vs-live comparison in favour of live, the opposite direction from what a reader
would assume. All 292 resolved rows are restated; pooled `avg_r` moves −0.2050 → −0.2192, mean
drag 0.0141R (range 0.0015–0.0489), total −4.1298R. All 292 priced from a real cost context, 0
widest-bucket fallbacks, 0 unpriceable. The 32 open rows are untouched by design — they are
charged when they resolve.

It adds a column, and the migration adds it itself: `outcome_cost_r` is in `schema.py` too, but a
DB not opened by the app since that change would not have it, so the migration runs its own
`ALTER`. A migration that only works after something else has run is a migration that fails in the
field.

There is no era cutoff, for a stronger reason than 003's: 003 had to establish from the data that
there was one era, while here it is structural — the resolver charged zero from the first row to
the last. The guard is `outcome_cost_r IS NULL`, state rather than a date, which makes it
idempotent by construction and means a row written by the fixed resolver is never re-charged.
Compounding here would be undetectable, because gross is not stored independently — it is only
recoverable as `outcome_r + outcome_cost_r`, which a second charge would also shift. That is why
`tests/test_migration_004_net_of_cost.py` pins the second run as a no-op.

NULL is not 0.0: a row without geometry cannot be priced and stays NULL, since stamping `0.0` would
claim a measured zero and hide the row from any later run at once.

It refuses when the two live configs disagree on a cost basis, rather than picking one, because
`signal_alert_outcomes` records no config provenance and rows are not attributable. They agree
today because `[backtest.cost_model]` lives in the shared base, and the test asserts it.

**Run:** `python migrations/004_live_ledger_net_of_cost.py [--db PATH] [--apply]` — dry-run by
default, opening the DB `read_only` so it cannot write or hold a write lock. Tested, like 003.

What it does not fix: a gap through the stop still books −1.0R, and `engine.py:1116` does the
same, so that absence is shared and does not bias the comparison — but it is ~7× larger
(−0.10R/row vs −0.014R). Audit: `docs/audits/2026-08-19-live-ledger-net-of-cost.md`.

## 005_symmetric_gap_fill.py — the ledger booked the level, not the fill

A bar that opens beyond a level fills at the open, not at the level. The resolver booked a flat
−1.0 for every loss and `implied_tp_r` for every win regardless; `engine.py` did the same, so the
absence was shared. Both are fixed forward through the one shared `analytics/backtest/fills.py`;
this migration restates the 264 already-written win/loss rows.

The fix is symmetric, and it nearly shipped one-sided: the 2026-08-19 audit measured only gapped
losses. The mirror is bigger (26.1% of wins gap through their target vs 21.1% of losses through
their stop), so an adverse-only fix would have moved pooled `avg_r` −0.2192 → −0.3218 against a
symmetric −0.2553 — a ~65% overstatement, in the direction that makes sleeves look worse against
exactly the stop-free benchmark the fix was for.

Three properties worth copying:

- Cost is carried over, not recomputed. `live_cost_r` never reads the exit price, and the fill
  rule never changes which bar resolved, so every cost input is unchanged. Recomputing would
  re-derive the same number while adding a second way for 004's basis to drift.
- Scoped to `outcome IN ('win','loss')`. An `expired` row marks to market at the last close, so a
  fill rule cannot reach it — and its `outcome_filled_at_ms` moves with the fetch window, which
  put 10 rows in the mismatch column for reasons unrelated to the migration. Scope a restatement
  to the rows the change can actually reach.
- Idempotent by value, not by a state flag. Only rows whose recomputed value differs are written,
  so a re-run reports zero changes and no "already restated" column was added to record a one-off.

Its first draft hit the RTH bar-count footgun: the fetch window was
`latest + (max_hold + 2) * tf_secs`, and `4h` RTH is 2 bars/day, so that window was short by ~6x
and the replica check reported 6 of 264 unreproducible. Bounding at the table's own
`MAX(open_time)` took it to 264/264. The symptom was quiet — six rows conservatively skipped, a
count small enough to read as data rather than as a query bug.

**Run:** `python migrations/005_symmetric_gap_fill.py [--db PATH] [--apply]` — dry-run by default;
prints the replica check, the loss/win split of changed rows, and before/after pooled `avg_r`.

## 006_purge_frozen_tail_bars.py — the provider kept quoting a stopped tape

The first migration to target `ohlcv`, and the first to delete rather than rewrite. When a name
stops trading, the provider forward-fills its final print at zero volume for as long as the series
is still carried; `EA` and `EQR` each ended with a huge-volume session followed by four such bars.
They are not observations, and in a pooled cross-section they read as consecutive exact-zero
returns with no variance.

Position in the series is the discriminator; the row shape alone is not, and this nearly shipped
the other way. The defect was first described as "zero volume and an unchanging close", which is
how it presents, but that predicate matches 1,368 rows across the whole DB, of which only 9 are
the dead tails — the other 1,359 sit mid-history in live names (808 `SW`, 231 `AMCR`, 188 `^GSPC`,
36 `^TNX`). Requiring the run to terminate the series leaves exactly those 9, across 3 (symbol,
timeframe) series; the nearest surviving frozen row is 81 bars from its series end (`^TYX 1d`, full
-column scan). The two populations do not overlap, so the rule has margin — the naive predicate
would have deleted 188 S&P 500 index bars.

Zero volume alone is not even a hint: `DX-Y.NYB`, `^TNX` and `^TYX` are permanently zero-volume and
healthy. 12,507 zero-volume rows are stored and this rule removes 9; the other 12,498 are left
alone rather than certified correct — the measurement supports the first claim and not the second.
Every figure reproduces by swapping the run predicate in the script's `_FROZEN_TAIL_SQL`.

Deletion rather than a flag, because `ohlcv` has no quarantine column and the ingest path expresses
the same decision by never storing the row (`data_quality.frozen_tail_idx`). A deleted bar is
recoverable by refetch and would simply be re-quarantined, so no unique observation is lost.
Idempotent by value — the rule is recomputed each run, so a second run reports zero.

**Run:** `python migrations/006_purge_frozen_tail_bars.py [--db PATH] [--apply]` — dry-run by
default; prints the table total, the zero-volume count it is not touching, and the per-series purge
list.

## 007_purge_avb_wrong_instrument_tail.py — a wrong instrument under the right ticker

Between 2026-07-17 and 2026-08-24 the provider's history endpoint served a $63–71 tape under
`AVB`, a ticker trading at $184.06, then stopped updating it. Those bars are not observations of
AvalonBay, and they put a fabricated −61.1% session (177.32 → 68.93) into every pooled
cross-section reading the research universe.

This is not the split seam `data_sync.ADJUSTMENT_BASIS_TOL` guards, and conflating the two picks
the wrong repair. A split restates a series consistently and is fixed by re-syncing it; here the
provider serves the wrong security, so a re-backfill cannot help —
`yf.Ticker("AVB").history(start="2018-01-01")` returns 27 rows, the bogus window only, so a refetch
would overwrite the 27 bad bars with the same 27 bad bars and reach none of the 2,127 good ones.
That also makes 007 the first migration whose deleted rows are not recoverable by refetch, where
006's would simply be re-quarantined.

Two predicates, and they must agree. Rows are selected by value (`close < 100`) and date
(`open_time >= 2026-07-01`), because either alone is a claim about a boundary rather than about a
population. The script refuses to run if any `AVB` row matches one and not the other. The margin:
the good bars' minimum low is 118.17 — the low, not the close, being the value that could dip
across a close-based threshold — against the bogus band's maximum high of 70.61, a 1.67× gap with
nothing inside it, and no row of any timeframe between the last good bar (1781755200000) and the
first bogus one (1783915200000). The seam constant sits inside that empty gap rather than on
either edge.

This is not a delisting, so `config/universe.json` is deliberately untouched. `EA`/`EQR`/`SATS`
were flagged `delisted: true` in #281 because they had stopped trading; AVB is alive and quoted.
Flagging it would state something false and drop a live constituent from the breadth universe.
What it becomes instead is a stale series, which `make freshness-check` reports and which repairs
itself when the provider fixes the ticker.

Applied 2026-09-04: 27 `1d` + 7 `1wk` = 34 rows, `AVB` 2603 → 2569, second run reports 0.
Idempotent by value, like 006.

**Run:** `python migrations/007_purge_avb_wrong_instrument_tail.py [--db PATH] [--apply]` —
dry-run by default. Audit: `docs/audits/2026-09-04-split-adjustment-seams.md`.

## 008_realign_off_monday_weekly_bars.py — a 1wk anchor set by the request

Under `period="max"` Yahoo anchors `1wk` bars on the weekday of the symbol's first session, so
ABBV, UNP, LUV, PEG and SATS were Tuesday-stamped and AEE Thursday-stamped for every week, while
the other 502 series were Monday-stamped. A start on or after the first session anchors on Monday;
`data_fetcher._monday_anchored` now re-fetches from there (#323). A re-sync cannot repair the
stored rows, because `sync` upserts on `open_time` and Monday bars would land beside the Tuesday
ones; a stamp shift cannot either, because a Tuesday bar straddles two Monday weeks.

It fetches before it deletes, per symbol, so a series the provider no longer serves keeps its rows:
SATS (`delisted: true`, #281, Yahoo 404) keeps 442 Tuesday rows. It is the one migration that
needs the network, and its dry run makes no calls. Apply it only once the anchor fix is the code
the scheduled sync runs, or the next universe sync appends off-Monday bars again.

**Run:** `python migrations/008_realign_off_monday_weekly_bars.py [--db PATH] [--apply]` —
dry-run by default.

## 009_purge_bny_4h_wrong_instrument.py — the rebranded ticker's previous holder

BNY's `4h` series held 816 bars from 2024-06-17 to 2026-02-06 at $9.33–11.08 (median volume
22,258), then a 104.8-day gap, then real BNY from 2026-05-22 at $139+ (median ~1.0M). By price and
volume the cheap bars are the ticker's previous holder, a ~$10 fund (inferred, not confirmed);
`1d` and `1wk` are clean. #469's level-break scan found it, and the earlier reading of it as a
benign intraday shortfall was wrong. Like 007 it cannot be repaired by refetch: on 2026-10-09
`history(interval="1h")` still served ~$10 bars for Jan–Feb 2026.

Same shape as 007: value (`close < 50`) and date (`open_time < 2026-04-01`) predicates must agree,
scoped to `symbol = 'BNY' AND timeframe = '4h'`. The margin is 137.50 (good minimum low) against
11.10 (bad maximum high). A routine sync will not re-import the rows, because it extends forward
from the newest stored bar; a hand-run full `4h` backfill would, and the ingest warning names it.

**Run:** `python migrations/009_purge_bny_4h_wrong_instrument.py [--db PATH] [--apply]` —
dry-run by default (816 rows on 2026-10-09).

## The applied record — `schema_migrations` (#467)

The scripts keep no state of their own, so whether one reached a given DB could only be
re-derived by hand, and 007 sat unapplied on this host's DB until #445 found it. Every numbered
script's `--apply` now calls `analytics/store/schema_migrations.py::record_applied`, including a
zero-row apply, inside its write transaction where it has one; 008 records once after its
per-symbol loop and names the symbols it kept. The table lives in `analytics.db`, so a restored
snapshot shows that snapshot's applied set. A new script must call it too:
`tests/test_schema_migrations.py` fails on any `migrations/0*.py` that does not.

`make freshness-check` (and the session digest, as AMBER) lists every `0*.py` with no row. It
reads the record, never the predicates, because re-running them is not a sound check: 002's reads
today's config and flags 13 truthful rows that `fa89d57` exempted after they were written, and
008 keeps SATS by design. For the same reason 002's `--apply` refuses once it is recorded.

`seed_applied_2026_10_09.py` records 001–009 once on the DB the #467 probe read (this host's),
with a note per row saying how each was established and `applied_at_utc` left NULL. Do not run it
against any other DB, where its notes would be claims nobody checked; re-run the probe there.

## The transferable rule

A migration must respect code eras. "Fix every row" is the wrong default when the column records
what the code did: rows written under different code recorded different — and possibly correct —
things. Establish the commit boundary first, then migrate only forward of it. The enforcing
mechanism that keeps this from recurring is `effective_adr_threshold(declared, timeframe,
adr_exempt=…)`, a required kwarg on `upsert_backtest_run`, so a new call site cannot omit it. Full
narrative: the `002_adr_threshold_executed.py` docstring, and CLAUDE.md → Footguns.
