# Tools Reference

`tools/` holds one-shot analysis and audit scripts — they are not part of the daemon or CLI
surface. Each is run via `PYTHONPATH=. poetry run python tools/<name>.py` (with script-specific
flags) or an equivalent `make` target.

## strategy_edge_audit.py — Phase 0 strategy edge audit

Aggregates `backtest_trades` by (strategy × tf × regime × session) + combo uplift;
deterministic KILL/DEMOTE/KEEP rule. See `docs/redesign/buibui-redesign-phase0.md`.

**Run:** `PYTHONPATH=. poetry run python tools/strategy_edge_audit.py`

## universe_coverage.py — N3 read-only OHLCV coverage report

Read-only OHLCV coverage report over the research breadth universe (`config/universe.json`);
per `(member × timeframe)` bars present + date range, a missing-symbol roll-up, and a header
carrying the universe lifecycle-bias caveat (`ResearchUniverse.describe()`). Pure read (`ohlcv`
table, `read_only=True`); `build_coverage_rows`/`summarize` are the testable units.

**Run:** `make universe-coverage` or
`PYTHONPATH=. poetry run python tools/universe_coverage.py [--db PATH] [--timeframes 4h 1d 1wk]`

## dead_surface_check.py — cells where declaration and output disagree

Reports `(strategy × timeframe)` cells in **both** directions of the mismatch — the
data-driven half of the silent-surface enforcement (the static half is
`tests/test_makefile_invocations.py`). Both halves key off the same declared set,
`analytics.signal_config.declared_cells` (every strategy × its `strategy_timeframes`
override, else the config's `timeframes`), which lives in `signal_config` precisely so
the two questions cannot drift apart.

**Declared but dead** (`find_dead_cells`) — joins the declared set against `backtest_runs`
scoped by `day_filter` and flags any cell with **no runs** or **runs but zero
`total_signals`**.

**Rated but undeclared** (`find_orphan_ratings`) — the inverse. Scans
`confidence_ratings` for the config's TOML stem and flags any row whose `(strategy, tf)`
the config no longer declares, in every direction, worst-stars-first. `recalibrate` had no
notion of the current config and `upsert_confidence_ratings` never deletes, so a dropped
cell kept its stars and took a **fresh timestamp on a stale value** on every refresh: on
2026-08-06, `fib_golden_zone × 4h` sat at 3★ +0.4688 — second-highest-rated cell in the
whole `signal_watch` table — 2.5 months after the strategy left the config. Note the
asymmetry that let it survive: a dead cell surfaces as a zero and reads as *absence*, an
orphan surfaces as a number and reads as *evidence*.

The second case is the one that matters and the reason a plain "did it run?" check is not
enough: `signal_watch.toml` held **338** `1wk`/`tue_thu` rows with zero closed trades for
three months, so the surface *looked* covered (#139). Emptiness is indistinguishable from
coverage unless something explicitly asks.

`_KNOWN_DEAD_CELLS` is a `(day_filter, strategy, timeframe)` allowlist mirroring
`tests/test_lookahead.py::_KNOWN_LOOKAHEAD_DETECTORS` — **it should only ever shrink**, and
it reached **empty** on 2026-08-06 (its five entries were resolved; the diagnosis recorded
beside three of them turned out to be wrong, which the module's docstring keeps as a
caution). There is no allowlist for orphaned ratings: the fix is to prune them, not to
accept them.

Pure read (`read_only=True`). Exit 1 on any non-allowlisted dead cell **or** any orphaned
rating; `--strict` also fails on allowlisted dead cells, which is how you verify the list
can shrink. Inside `make db-update` it never blocks the refresh, but the completion banner
is conditional on it — it previously printed an unqualified `✅` beside nine orphans, one
of them displaying 3★.

**Run:** `make check-dead-surfaces` or
`poetry run python tools/dead_surface_check.py [--config PATH ...] [--db PATH] [--strict]`

## orphan_test_audit.py — test classes that NAME a unit but never CALL it

The third mechanical enforcement check, alongside `dead_surface_check.py` (data-driven)
and `tests/test_makefile_invocations.py` (static). It exists because a green suite proves
nothing about whether a test exercises its subject: `TestEvGate` held five tests that never
invoked the EV gate, because the gate was `def _passes_ev_gate` **nested inside**
`run_scan_cycle` and therefore unimportable. Every test re-implemented the comparison
inline; one asserted the defect as the expectation, another reduced to `assert None is
None`, and all five passed against any implementation (#150).

Two verdicts:

- **`not-importable`** — the subject matches a **closure** and no module-level callable.
  The unit is unreachable from a test, so the tests can only re-implement it. **Extraction
  is a prerequisite for the fix, not scope creep.**
- **`not-called`** — an importable callable matches but no test in the class calls it.
  Ordinary drift after an extract or rename.

Matching is token-based and **directional**: the callable's name must contain the class's
subject tokens contiguously and in order, so `ev_gate` matches `_passes_ev_gate` while
`p_r` matches neither. An earlier substring formulation produced **95** findings on a clean
tree, nearly all junk. A class whose subject matches nothing at all is deliberately not
reported — descriptive names (`TestWatermarkOnSend`) are legitimate and were most of that
noise.

`EXEMPT_CLASSES` is keyed `"<test file>::<class>"` and every entry carries its reason. The
three current entries are all the same false-positive shape — the subject reached **one
indirection away**, via a local test helper or a CLI `main()`. An entry without a reason is
how the check decays into a no-op, and a check that always prints the same findings is
ignored, which amounts to the same thing.

Heuristic, so it is **advisory and not part of `make test`** — unlike
`tests/test_schema_insert_arity.py`, which is deterministic and therefore runs in the
suite. Exit 0 by default; `--strict` exits 1 on findings. Verified against the pre-#150
tree, where it isolates `TestEvGate` and names `_passes_ev_gate`; clean on HEAD.

**Run:** `make check-orphan-tests` or
`poetry run python tools/orphan_test_audit.py [--strict]`

## live_outcomes_report.py — read-only signal_alert_outcomes spot-check

Read-only spot-check of `signal_alert_outcomes` after the T2 backfill worker runs; reports the
resolved/open mix, per-(strategy, tf, direction) win rate + avg_r, and per-strategy aggregate.
Stop-gap until a Stats UI card lands.

**Run:** `PYTHONPATH=. poetry run python tools/live_outcomes_report.py [--days N] [--min-n N]`

## forecast_audit.py — G2 audit for the EWMAC trend sleeve

Read-only **G2 audit** for the `analytics/forecast/` EWMAC trend sleeve (PR #91): portfolio
Sharpe/Sortino/max-DD with DSR/PBO/boot-CI/MinTRL stamps, a cost-sensitivity sweep
(0/2/8/16 bps), a breadth (universe vs majors) contrast, the per-speed Sharpe table, and a
`--weight-study` mode. Universe from `load_research_universe().stocks()`; majors default
`AAPL,MSFT,NVDA,AMZN,GOOGL,META`. `build_g2_report_row` is the testable unit.

**Run:** `make wifey-forecast-audit` or
`PYTHONPATH=. poetry run python tools/forecast_audit.py [--majors …] [--weight-study]`

## xsmom_audit.py — G3 audit for the cross-sectional momentum sleeve

Read-only **G3 audit** for the `analytics/xsmom/` cross-sectional momentum sleeve (PR #92):
breadth contrast (universe vs majors), dollar-neutral gate, beta-attribution table
(equal-weight market + SPY proxy, loaded separately so the ETF never enters the demean),
forward-persistence table, cost-sensitivity sweep (0/2/8/16 bps), per-speed XS Sharpe, each with
DSR/PBO/boot-CI/MinTRL + `corr_to_trend`. Universe from `load_research_universe().stocks()`;
majors default `AAPL,MSFT,NVDA,AMZN,GOOGL,META`; `--market-proxy` default `SPY`.
`build_xs_report_row` is the testable unit.

**Run:** `make wifey-xsmom-audit` or
`PYTHONPATH=. poetry run python tools/xsmom_audit.py [--majors …] [--market-proxy SPY]`

## xsmom_residual_audit.py — experiment #1 audit for residualized XS-momentum

Read-only **experiment #1** audit (PR #98) for the residualized XS-momentum sleeve
(`analytics/xsmom/residual.py`): runs the pre-registered `{mega,broad}×{raw,residual+skip}` 2×2
over the breadth universe (1d) at 0/2/8 bps, prints each cell's headline + DSR/PBO/boot-CI/
MinTRL/corr_to_trend, the long-only top-quintile leg Sharpe, and the PASS/FAIL on the committed
`broad_residual_skip` cell. Mega arm = `config/universe_sp100_snapshot.json` ∩ active universe.
`build_grid`/`long_only_sharpe` are the testable units. **Verdict = FAIL**
(`docs/audits/2026-06-21-experiment-1-residual-xsmom.md`).

**Run:** `make wifey-xsmom-residual-audit` or
`PYTHONPATH=. poetry run python tools/xsmom_residual_audit.py [--slippage-bps N]`

## lowvol_audit.py — edge-hunt #2 audit for the low-beta/BAB sleeve

Read-only **edge-hunt #2** audit (PR #100) for the `analytics/lowvol/` low-beta/BAB sleeve: runs
the pre-registered `{beta,vol}×{beta-neutral L/S, long-only quintile}` 2×2 over the breadth
universe (1d) at 0/2/8 bps, prints each cell's headline + DSR/PBO/boot-CI/MinTRL + the
realized-portfolio-beta diagnostic + alpha t-stat, and the PASS/FAIL (+ deploy-grade flag) on
the committed `beta_neutral_ls` cell. `build_grid`/`_grid_frame` are the testable units.
**Verdict = FAIL** (`docs/audits/2026-06-22-edge-hunt-2-lowvol-bab.md`).

**Run:** `make wifey-lowvol-audit` or
`PYTHONPATH=. poetry run python tools/lowvol_audit.py [--slippage-bps N]`

## xasset_audit.py — edge-hunt #3 audit for the cross-asset TSMOM sleeve

Read-only **edge-hunt #3** audit (PR #102) for the `analytics/xasset/` cross-asset TSMOM sleeve:
runs the pre-registered `{broad,commodity}×{long-short,long-flat}` 2×2 over the frozen 13-ETF
basket (1d) at 0/2/8 bps, prints each cell's headline + DSR/PBO/boot-CI/MinTRL + the realized
equity-β to SPY, and the PASS/FAIL (+ deploy-grade flag) on the committed `broad_ls` cell.
`build_grid`/`_grid_frame` are the testable units. Backfill the basket first with
`make wifey-xasset-backfill` (13 ETFs, 1d from 2007-03-01). **Verdict = FAIL (clean — equity-β
guardrail held)** (`docs/audits/2026-06-23-edge-hunt-3-cross-asset-tsmom.md`).

**Run:** `make wifey-xasset-audit` or
`PYTHONPATH=. poetry run python tools/xasset_audit.py [--slippage-bps N]`

## pead_backfill.py — one-shot EDGAR earnings ingestion

One-shot EDGAR earnings ingestion (edge-hunt #4, PR #104): loops the breadth universe, resolves
CIKs, pulls `companyfacts` + `submissions`, matches each quarter to its 8-K item-2.02
announcement (10-Q `filed` fallback), upserts `earnings_facts`. Pure unit = `build_rows`;
network in `main`.

**Run:** `make wifey-pead-backfill` (or `tools/pead_backfill.py [--limit N] [--db PATH]`)

## pead_audit.py — edge-hunt #4 audit for the PEAD-lite sleeve

Read-only **edge-hunt #4** audit (PR #104) for the `analytics/pead/` PEAD-lite sleeve: runs the
pre-registered `{broad,mega}×{long-short,long-only}` 2×2 over the breadth universe (1d) at
0/2/8 bps, prints each cell's DSR/PBO/boot-CI/MinTRL + realized equity-β to SPY + the
8-K-vs-10-Q coverage diagnostic, and the PASS/FAIL on committed `broad_ls`.
`build_grid`/`_grid_frame` are the testable units. Backfill first with `make wifey-pead-backfill`.
**Verdict = FAIL (β-guardrail FIRED on broad_ls; mega-arm null)**
(`docs/audits/2026-06-23-edge-hunt-4-pead-lite.md`).

**Run:** `make wifey-pead-audit` or
`PYTHONPATH=. poetry run python tools/pead_audit.py [--slippage-bps N]`

## expand_universe_sp500.py — one-shot universe expander

One-shot universe expander (experiment #1, PR #98): snapshots the pre-expansion `kind=="stock"`
symbols to `config/universe_sp100_snapshot.json`, then merges the current S&P 500 constituents
(+GICS sectors) into `config/universe.json` as `kind=stock/delisted=False` members (existing
entries idempotently preserved). `merge_constituents` is the pure testable unit. Constituents
source: Wikipedia `pandas.read_html` by default (sends a browser UA; needs an HTML parser), or
`--from-csv PATH` (columns `Symbol,Sector`) for a deterministic/offline run.

**Run:** `PYTHONPATH=. poetry run python tools/expand_universe_sp500.py [--from-csv sp500.csv]`

## exit_audit.py — exit MFE/MAE diagnostic

Read-only **exit MFE/MAE diagnostic** for the `analytics/exits/` package (PR #96 / parent #433),
diagnose mode only. Prints coverage, the overall win/loss/expired cohort roll-up, the
per-(strategy, tf, direction) table, and the exit spec §2 verdict grid inline over the live
`signal_alert_outcomes` ledger. No `--replay` (the exit-policy A/B ships with the deferred #437
port).

**Run:** `make wifey-exit-audit` or
`PYTHONPATH=. poetry run python tools/exit_audit.py [--min-n N] [--csv PATH]`

## pundit_score.py — read-only scorer for the Stream-C pundit ledger

Ported from the parent. Resolves every `docs/plans/pundit-calls.jsonl` call against stored
OHLCV → hit-rate + R proxies per author × setup-family × direction, plus a machine-readable
`docs/plans/pundit-priors.json` sidecar. **Descriptive priors only — no ENABLE/DISABLE gating**
(the parent defers that until a cell earns n≥30). Level parsing, family tagging, roll-up and
output shape are byte-identical to the parent so the two ledgers stay comparable.

`load_ledger` enforces three field domains at the read boundary via the pure
`analytics/pundit_{direction,horizon,authors}.py` guards (parent #560/#561/#555 — see
`context/analytics.md`): a violation becomes a per-line warning naming the line and the
value, rather than a silent wrong number downstream. **`horizon` is the one that was live
here** — an unrecognised value took *two* silent `.get` fallbacks (`SCORE_TIMEFRAME`'s wrong
bar series *and* `SESSION_WINDOWS`' wrong window), where the parent has only the latter. The
`author` guard changes the **priors JSON key shape** (`@fenggemeigu` → `fenggemeigu`); nothing
in this fork reads that sidecar yet, so a future Brief/Card port must join on the normalised
key. No scored number changed: the committed 19-row ledger produces a byte-identical report
apart from the author column.

### Nine documented divergences

(1)–(8) cover everything that touches the tape, because equities are a sessioned market, and
(9) is a correctness fix that is not equity-specific:

- **(1)** scoring frame follows the horizon (`1h` intraday / `1d` swing+unspecified — several
  ledger symbols have no 1h bars at all)
- **(2)** no call-candle containment — the reference is the last bar *fully closed* at/before
  the call, so after-hours and weekend calls still resolve
- **(3)** thesis entries fill at the **next open**, not the call bar's close (the parent's
  convention is a look-ahead)
- **(4)** gap-aware direction-aware level crossing — a long stops at `min(open, stop)` rather
  than needing `low <= stop <= high`
- **(5)** adverse-first resolved by the open (opened-beyond-stop → loss at open; both-intrabar
  keeps the parent's stop-wins rule)
- **(6)** windows counted in **NYSE sessions** (2 / 21 / 10) via
  `analytics/trading_calendar.py`, not wall-clock
- **(7)** month-anchored years stripped before level parsing (US index levels share the
  1,900–2,100+ band with year strings — the first run read "…starting Aug-Sep 2026" as a
  2,026 target on a 7,436 index)
- **(8)** staleness measured against the last closed session, not wall-clock now (else every
  symbol reads STALE overnight). Also folds in the null-symbol guard that previously existed
  only as `/ingest-video` skill prose.
- **(9)** `_geometry_note` delegates to `x_route.check_level_order` and covers the **target**
  leg as well as the stop (2026-08-04): a wrong-sided stop only yields a nonsense R, but a
  wrong-sided target is already in profit at the fill and books an instant `WIN` at ~0.00 R —
  a phantom statistic rather than a visible error, which is how a mis-written "unless it
  reclaims 29,200" *stop* produced a 100% hit rate with zero warnings. Such a row is now
  `UNSCORED` with the offending pair named. The parent was ported from the same code and so
  likely carries this latent defect — raise it on the next `/sync-parent` rather than assuming
  it was fixed upstream.

Read-only; no schema change, goldens untouched.

**Run:** `make wifey-pundit-score` or
`PYTHONPATH=. poetry run python tools/pundit_score.py [--as-of ISO] [--min-n N]`

## backfill_null_tp_outcomes.py — one-shot retro migration

One-shot retro migration (ported from parent #410): reconstructs the pct-fallback SL/TP for
legacy `signal_alert_outcomes` rows written with NULL `tp_price` (before
`_resolve_outcome_sl_tp`), then resolves them via `backfill_outcomes`. Read-only by default;
`--apply` gated; idempotent.

**Run:** `PYTHONPATH=. poetry run python tools/backfill_null_tp_outcomes.py [--config config/signal_watch.toml] [--apply]`

## x_fetch.py — read-only X/Twitter post fetcher

Read-only X/Twitter post fetcher via the public syndication endpoint
(`cdn.syndication.twimg.com/tweet-result`; no auth/scraping — a non-empty `token` is required
but its value is not validated, so a fixed dummy suffices). URL → `XPost` (text + full-res
`?name=orig` chart URLs + `video_present`/`is_thread`/`is_quote` flags + best-effort
`quoted_text`/`quoted_author` from the nested quoted tweet) + `download_photos` (charts →
gitignored `.cache/x-media/<id>/`); graceful `Unavailable` on
protected/deleted/tombstone/non-200/non-dict. Batch path `fetch_x_batch` fetches N URLs once
each with a randomized cooldown *between network fetches only* (default 4–12s; skipped before
the first fetch and on cache hits) + a per-id dedup cache (`.cache/x-posts/<id>.json` → zero
network on re-runs); `sleep`/`rng`/`get` are injected for deterministic, network-free tests
while `fetch_x_post` stays pure. Backs the `/ingest-x` skill (ported from parent #466/#467;
spec `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`).

**Run:** `PYTHONPATH=. poetry run python tools/x_fetch.py <url…> [--batch] [--json] [--force] [--min-delay/--max-delay S] [--cache-dir/--media-root DIR]`

## x_route.py — routing decision and shared level sign-check

Pure routing decision **and** the shared level sign-check for the ingest skills.
`route_target(content_type, verdict) -> str | None` is the content-type gate
setup/mechanic/claim → the research pipeline's 4-bucket verdict taxonomy on the claim path →
Stream A `thesis-inbox.md` / B `mechanics-backlog.md` / C `pundit-calls.jsonl`, or drop.

`check_level_order(direction, *, entry, stop, target) -> str` (2026-08-04) is the sign-check: a
long must satisfy `stop < entry < target`, a short `target < entry < stop`; every pair whose
legs are both present is judged, equality counts as a violation (zero risk / zero reward), and
an unjudgeable direction warns rather than passing silently.

`check_row_levels` applies it to a ledger-shaped row (`<role>_px` overriding the free text via
`first_level`, which strips **three** classes of number that read as a level but are not one).

### Three number classes stripped by `first_level`

- Month-anchored years — `MONTH_YEAR_RE`, the single definition also imported by
  `pundit_score.py` as its divergence 7
- Percentage *ranges* via `PCT_RE`, since the `%` binds to the second number
- Chart **timeframes** via `TIMEFRAME_RE` (2026-08-05; Latin `4h`/`1d`/`15m`/`1wk` + CJK
  `小时`/`日线`/`分钟`)

The first two are the single definitions, also imported by `tools/route_dedup.py`'s
`normalize_levels`, which has no sanity gate either; `TIMEFRAME_RE` is deliberately **not**
shared, because `normalize_levels` is already immune by a different mechanism — its
`_MIN_LEVEL = 100.0` floor drops a `4h` → `4.0` as sub-$100. `TIMEFRAME_RE` was added after a
gold long whose entry read "break above the 4h descending trendline" sign-checked as
`stop 4000 on the wrong side of entry 4`; that instance warned *loudly*, but the same artifact
passes **silently** whenever the stripped number happens to out-rank a real leg — a long with
entry `4h` and a stop of 3 reads 4 > 3 = OK — which is the fake-`WIN` class the guard exists to
catch. Its `(?<![\d.])` lookbehind is load-bearing: without it `\b` matches at the decimal
point, so `4.5m` matches its own `5m` tail and leaves a bare `4.` behind).

Consumed by `/ingest-x` + `/ingest-video` step 8 through the `--check-levels` CLI (JSONL from a
file or stdin; **advisory** — prints, exits 1 iff any row warned, never rewrites or drops) and
by `tools/pundit_score.py` on the read side. It warns and never drops, because the failure mode
being fixed is *silence*. Stdlib only, no I/O beyond the CLI's read.

### Known blind spot

The pairwise rule cannot judge a row stating one level and nothing else — that shape is caught
read-side only, where the scorer substitutes the market price for a missing entry. This is the
*dominant* shape, not an edge case: in the 2026-08-05 @fenggemeigu batch **7 of 11 candidate
rows were one-legged** (a lone 防势点 "defense point" and nothing else), 1 had zero legs, and
the only full entry/stop/target triple was degenerate — the support level served as both entry
and stop, which the check correctly flagged as zero risk. Encode such a row as entry+target
with the stop left unstated rather than inventing a gap the pundit never gave.

**Run:** no standalone CLI invocation of its own — consumed via the `--check-levels` CLI
(JSONL from a file or stdin) inside `/ingest-x` + `/ingest-video` step 8, and read-side by
`tools/pundit_score.py`.

## route_dedup.py — routing dedup for the ingest sinks

Routing dedup for the ingest sinks (edge case the fetch caches structurally cannot cover: they
dedup *fetches*, nothing dedups *routing*).

### Machinery

Two layers with different machinery:

- **identity** — `RoutedItem` ledger at `docs/plans/routed-ledger.json`, keyed
  `(source_id, round(item_ts, 1), sink)`; `load_ledger`/`append_routed`/`remove_routed`/
  `is_routed`, atomic tmp+`os.replace` write, loud `SystemExit` on a malformed/unversioned file
  rather than a silent reset
- **semantic** — `find_similar` scores a claim against a sink's split entries —
  `3.0 × |shared levels| + 10.0 × jaccard(terms)` ≥ `_MIN_SCORE` 3.0; `find_source_duplicate_pairs`
  does the same item-vs-item over one source's *pending* items, which `find_similar`
  structurally cannot see because every check runs before the approval that writes anything

**Advisory except for `already_routed`** — it surfaces candidates for the review digest and
never drops a row. CLI `check | mark | unmark | pairs | seed`; `mark` runs strictly *after* the
sink write (marking at check time is the #68 watermark-on-send defect class).

### Seven divergences from the parent

Ported from parent #518/#521 with **seven divergences**, every one found by running the ported
code against the live sinks rather than by reading it, and four of the seven (1, 3, 6, 7)
traceable to the single structural fact that wifey's Stream A sink is a **markdown table** where
the parent's is prose:

- **(1)** Stream A splits on **table rows**, not the parent's level-two headings — the parent's
  splitter returns *one blob* for the whole live file (measured), so every comparison would
  silently score against it while `semantic_scope` still reported `all-entries`
- **(2)** `normalize_levels` also strips month-anchored years + percentages, reusing `x_route`'s
  `MONTH_YEAR_RE`/`PCT_RE` single definitions (US index levels share the year band — the
  PR #128 scorer defect, third consumer)
- **(3)** URLs are stripped before **both** scoring layers, since Stream A rows embed the
  source deep-link and its `&t=124s` offset reads as a price level (measured: the parent reads
  `124.0` off the live H-001 row)
- **(4)** bare 4-digit years in `_YEAR_BAND` (1900–2099) are not levels — before this rule
  **3 of 28 live Stream A pairs flagged and all three were year artifacts**; the rule keys on
  *how the number is written* (a level in that band carries a separator or decimal, a year
  never does), at the stated cost of losing a bare `2050` level
- **(5)** the ledger key **truncates** `item_ts` to whole seconds (`_ts_key`) where the parent
  rounds to 1dp — an offset arrives both as the row's float `ts` (`566.81`) and as the deep link
  it persists (`&t=566s`, truncated), and `round()` maps those to *different* keys, so a seeded
  ledger would silently fail to block a re-route
- **(6)** term overlap is **containment over the shorter side**, not jaccard (`_overlap` +
  `_MIN_DENOM`) — a Stream A entry is a whole row incl. a `gap note` column, so jaccard scored a
  near-verbatim restatement of the live H-002 row at **1.86 and never fired** (containment reads
  0.89)
- **(7)** the pipeline's own verdict vocabulary is excluded from term matching (`_STOPWORDS`
  derived from `x_route.VERDICTS`) — `ALREADY-TESTED` is stamped on every entry and
  `already`+`tested` carried **all three** remaining live flags, the same defect
  `_PUNDIT_CONTENT_FIELDS` fixes for Stream C in the shape a table takes

### Inherited limits

Two inherited limits are documented rather than "fixed": sub-$100 names contribute no numeric
evidence (`_MIN_LEVEL`), and Stream C's `same-source` scope is blind to one author restating a
call across uploads (deliberate — two calls a week apart are two genuine observations the
scorer resolves against different bars).

### Calibration

Calibration after all seven, on the live sinks: **0/28 Stream A and 0/7 same-source Stream C**
pairs flagged (highest non-flagging score 2.5 vs a 3.0 threshold), while restatements of the
live H-002/H-003 rows score **8.9 / 8.8** against the correct row — discriminating, not inert,
which matters because the inert failure is indistinguishable from "checked and clean".

Consumed by `/ingest-x` (step 3 check, step 4 mark) and `/ingest-video` (step 7 check +
`pairs`, step 8 mark).

**Run:** `make wifey-route-dedup-seed` (`APPLY=1` to write; read-only otherwise, idempotent,
10/10 live rows seeded unmodified) to seed the ledger. CLI subcommands:
`check | mark | unmark | pairs | seed`.

## video_calltime.py — pure call-time resolution

Pure call-time resolution, the `/ingest-video` look-ahead guard (kept out of prompt-space
deliberately — LLM date arithmetic is a known failure mode and this field decides whether every
author's hit rate is honest): `resolve_call_ts` prefers a video's stated in-video time but
bounds it (`stated < publish`, `publish − stated ≤ STATED_TS_MAX_LEAD_H` (168h), a
naive/offset-less stated value is rejected not assumed-UTC, date-only → conservative
end-of-day clamped below publish), else falls back to `publish_ts_utc`; `is_backlog` flags
`publish → ingested` lag > `BACKLOG_THRESHOLD_H` (24h) — computed from publish time, so it
describes ingest lag, not the pundit's.

**Run:** `PYTHONPATH=. poetry run python tools/video_calltime.py --publish <iso> [--stated <iso>] [--date-only] --stated-raw "<quote>" [--ingested <iso>]`

## video_fetch.py — read-only YouTube/X video fetcher

Read-only YouTube/X video fetcher: every yt-dlp call goes through
`_YT_DLP = ("yt-dlp", "--js-runtimes", "node")` — never a bare `["yt-dlp", …]` (yt-dlp
≥2026.07.04 enables only **deno** by default; without an available JS runtime every *media*
path 403s while captions still resolve, so the failure masquerades as one unlucky video.
`--js-runtimes` is additive, and the `yt-dlp-ejs` runtime dep backs it).

- `fetch_meta` (yt-dlp `--dump-json` → `VideoMeta` incl. publish time)
- `fetch_transcript` (existing captions in any language first, else Groq `whisper-large-v3` over
  extracted opus audio — `split_audio` chunks past the 25MB cap using `duration_s` for
  offset-correct per-chunk timestamps)
- `extract_frames` (one ffmpeg seek per caller-supplied `FrameMark`, never speculative; retries
  the whole download-and-seek on **total** failure only — 3 attempts spaced by
  `_FRAME_RETRY_BACKOFF_S`, since a partial result means those marks individually failed to
  seek)
- `fetch_video_batch` (per-video dedup cache at `.cache/video/<id>/asset.json`, randomized
  cooldown *between network fetches only*, one failing video never kills the batch)

`run`/`get`/`sleep`/`rng` injected so the suite is network-free. Backs `/ingest-video` (ported
from parent #513; spec `docs/superpowers/specs/2026-07-28-ingest-video-design.md`).

**Run:** `PYTHONPATH=. poetry run python tools/video_fetch.py <url…> --batch --json [--force] [--min-delay/--max-delay S] [--cache-dir DIR]` (frame extraction has no CLI —
`/ingest-video` calls `extract_frames` directly after `video_marks.select` picks timestamps)

## video_marks.py — pure, stdlib-only frame selection

Pure, stdlib-only frame selection (transcript-driven, deliberately NOT scene-change — that
returns ~100 near-identical talking-head frames and misses the one annotated chart). Owns
`TranscriptSegment` (imported by `video_fetch.py`) and `FrameMark`.

`select(segments, item_ts, duration_s, *, cap=FRAME_CAP, window_s=DEDUP_WINDOW_S, sample_s=SAFETY_SAMPLE_S) -> list[FrameMark]` merges 4 trigger classes — item boundaries (pass-1
ranked candidate timestamps), deictic phrases ("look here", "这里", …), spoken price levels near
a symbol mention, a low-rate safety sample, and **tail anchors** (`tail_marks` — the safety grid
stops at `floor(duration/sample_s)*sample_s` and so structurally cannot reach the closing
seconds, which is exactly where a summary slide lives; `TAIL_OFFSETS_S=(2.0, 60.0)` spread wider
than `DEDUP_WINDOW_S` so dedupe cannot collapse the pair, and weighted at *item* tier so the cap
never trims them first) — dedupes within `window_s`, ranks by weight then earliness, caps at
`cap`.

Also owns `keep_items(candidates, *, cap=ITEM_CAP, min_specificity=MIN_ITEM_SPECIFICITY) -> (kept, dropped)` (2026-08-04) — the pass-1 cutoff, moved out of the skill's prose for the same
reason `video_calltime.py` was: a truncation rule stated only in a prompt drifts, and a silently
lost call is indistinguishable from a video that never made one. Total and lossless (every
candidate lands in exactly one bucket; each dropped row carries a `drop_reason`), ranked by
specificity desc then ts asc.

### `ITEM_CAP` tie-break bias

**That tie-break has a measured directional bias:** ts-asc means a tie at the cap resolves in
favour of *earlier* material, and a pundit who opens with macro and closes with single names
therefore loses the single names first. Measured 2026-08-05 — `ITEM_CAP=12` bound on all four
@fenggemeigu videos (26/15/39/36 candidates), and on `EKtmvhDOW20` it dropped the NVDA setup
**and both GOOGL items despite those two names being in the video's own title**, because they
sat past 1,090s behind a gold/DXY block. The cap is doing what it is specified to do; the note
exists so a thin-looking Stream C yield from a dense video is read as a cap artifact rather than
as a video that made no calls. The **floor** binds first and stops a *thin* video padding
low-specificity vibes up to the cap just because slots exist; the **cap** binds on a *dense* one.

A-priori constants: `ITEM_CAP=12`, `MIN_ITEM_SPECIFICITY=3`, `FRAME_CAP=15`,
`DEDUP_WINDOW_S=45`, `SAFETY_SAMPLE_S=300` — `ITEM_CAP` is bounded by
`ITEM_CAP + len(TAIL_OFFSETS_S) <= FRAME_CAP` (ceiling 13; past it kept items lose their frame
and silently degrade to `vision_confidence: "low"`).

**Run:** no CLI — pure library, called directly by `/ingest-video` (`select()` for frame marks,
`keep_items()` for the pass-1 item cutoff).

## multi_symbol_wfo.py — multi-symbol pooled-trades WFO sweep

For each (strategy × TF × day_filter) cell, runs `run_param_sweep` per symbol over a fixed
cohort and pools OOS trades across symbols by `tp_r`, picking the `tp_r` with the highest pooled
OOS `avg_r` (subject to `pooled_n ≥ 10`).

### Six `--cells` lists (hardcoded inline)

- `--cells t-a` — original T-A combined-direction multi-symbol-pending markers
- `--cells task-a` (default) — 4 directional-gap strategies × 3 TFs × 2 day_filters, including
  1wk
- `--cells inside-bar` — inside_bar audit: 6-cell directional sweep retiring the crypto-era
  `tp_r_long`/`tp_r_short` override
- `--cells pin-bar` — pin_bar audit: same 6-cell shape, retiring the `tp_r_long=5.0`/
  `tp_r_short=3.0` crypto override
- `--cells candle-resweep` — tp_r re-sweep at the PR #33 ATR multipliers (Phase 1: 3 candle
  patterns × 3 TFs × 2 day_filters = 18 cells; pair with `--fixed-atr` so each cell's
  `atr_sl_multiplier_<tf>` + `atr_sl_floor` flow from the TOML into `run_param_sweep` at sweep
  time)
- `--cells phase2-resweep` — Phase 2 of the same ATR-floor re-sweep (9 remaining strategies with
  `atr_sl_floor=true`: hammer_hanging_man, doji, morning_evening_star, bos, trend_day, orb,
  eqh_eql, ema, order_block; 50 cells total — 8 strategies × 3 TFs × 2 day_filters + orb × 4h ×
  2; also requires `--fixed-atr`)

### Flags

- `--direction {long,short,both,combined}` switches between pooling
  `BacktestResult.closed_trades` (combined) and `long_closed_trades` / `short_closed_trades`
  (directional, Task A); `both` prints all three tables per cell
- `--cell strategy/tf/day_filter` filters to one cell for spot-checks
- `--fixed-atr` (off by default) loads `atr_sl_multiplier` + `atr_sl_floor` per cell from
  `config_label`'s TOML via `analytics.signal_config.load_signal_config`; required for
  `candle-resweep` (without it, the sweep replays pre-PR-#33 SL geometry and the resulting tp_r
  winners are stale)
- `--live-parity` (off by default) replays the live gate stack (regime + direction_filter + F8
  HTF-EMA + ADR bias + cooldown) inside each cell's `run_param_sweep` so the reported `n`/`avg_r`
  reflect the live-filtered population instead of raw detector signals — the principled fix for
  the `wfo-filter-divergence` debt (loads each cell's config via `load_backtest_config`,
  force-enables the gates, pre-builds the regime/HTF-slope series per symbol via the
  `backtest_runner` helpers); the cross-strategy `conflict_resolver` is not applied since the
  tool sweeps one strategy at a time

**Run:** `PYTHONPATH=. poetry run python tools/multi_symbol_wfo.py [--cells task-a|t-a|inside-bar|pin-bar|candle-resweep|phase2-resweep] [--direction long|short|both|combined] [--cell strategy/tf/day_filter] [--fixed-atr] [--live-parity]`
