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

Orphans carry a **tier** (`OrphanRating.tier`, from sister PR #608): `undeclared anywhere`
versus `declared by another config`. Both mean the rating's own daemon will never scan the
cell, so the tier **labels a finding and never suppresses one** — but the fix differs, and
the split is real here because `signal_watch`'s **22** declared cells are a strict subset of
`signal_watch_weekdays`' **32**, leaving **10** that only one config declares. `main` decides
the tier from `cells_declared_elsewhere(declared_by_config, config)`, which is extracted
rather than inlined because `main` itself has no test — an inlined union would have been the
one part of the tier decision nothing could falsify. The parent's direction-aware half was
deliberately **not** ported: it exists upstream because all three of its configs carry
`strategy_timeframes_long`/`_short` narrowing, and this repo declares no such key, so
`declared_cells` is direction-agnostic and a direction-aware check reports the same set.

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

## post_branch_checks.py — every mechanical `/post-branch` check, in one run

Ten checks that used to be **16 shell blocks embedded in `post-branch/SKILL.md`**, which a
session had to notice and copy by hand. That is the failure CLAUDE.md names as *a hand walk
is not the walk*, and it is why the same defects kept recurring: the skill's answer to each
one was more prose, and prose cannot enforce. Extracting them cut the skill from **1,649 to
~600 lines** and made the checks testable — `tests/test_post_branch_checks.py` gives each a
**positive control**, which the prose versions never had.

Checks: `queue-items` · `handoff-symbols` · `new-files` · `new-modules` · `new-targets` ·
`negative-claims` · `doc-indexes` · `md-atx` · `memory-cap` · `handoff-size`.

Two are new and fix defects the prose form structurally could not:

- **`queue-items`** — nothing swept the handoff's own task list for work the branch just
  finished, so a completed item survived under a heading reading *"Settled — do not
  re-litigate"*, i.e. as an instruction to redo it. Confirmed three times. The earlier
  mitigation keyed on added Python **symbols**, so a docs-only branch defeated it entirely;
  this keys the handoff's own distinctive tokens against the diff **content**, which every
  branch has. It fired correctly on its own introducing branch.
- **`new-files`** — the prose probed `basename`, and every skill's basename is the shared
  constant `SKILL.md`, which matches CLAUDE.md's generic *"Skills live in
  `.claude/skills/<name>/SKILL.md`"*. A fabricated skill therefore reported COVERED — the
  exact false positive that check's own `-w` rule exists to prevent. `probe_names` probes
  the **parent directory** when the basename names a role rather than a file
  (`SKILL.md`, `README.md`, `__init__.py`, `INDEX.md`).

⚠ **Untracked files count as added.** `git diff` cannot see them in any form, so the
presence checks used to report zero on precisely the branch they existed for; the skill
answered that with "remember to `git add -A` first", one more hand-step to forget. Reading
`git status --porcelain` makes them correct either way.

Advisory by design — a finding is a candidate to dismiss in seconds, never an automatic
edit. The asymmetry is the point: a false positive costs a glance, a silent miss ships a doc
that enumerates every sibling but one and reads as complete. The Makefile is deliberately
**not** an enumerating doc; a build rule is not documentation.

**Run:** `make post-branch-checks` (passes `--exit-zero`), or
`PYTHONPATH=. poetry run python tools/post_branch_checks.py [--check NAME] [--exit-zero]`
to let it exit 1 on findings.

## docs_index.py — generated audit + spec indexes

Generates `docs/audits/INDEX.md` (18 verdicts) and `docs/superpowers/specs/INDEX.md`
(14 specs) — 32 documents that nothing indexed. CLAUDE.md cites 9 of the 18 audits
inline; the other 9 and 13 of the 14 specs had no surface listing them at all. Ported
from parent #600. `tests/test_docs_index.py` regenerates both and compares byte-for-byte,
so a new audit or spec **fails CI until it is indexed** — the enforcement half, without
which the index is the silent-surface class again. Output is deterministic (no
generated-at timestamp) precisely so `--check` can compare bytes.

**Nothing is guessed.** Date and title come from the filename prefix and the H1 (100%
reliable across both corpora). A verdict line is emitted **only** where it reads as prose
under a Verdict heading or in the inline `**Verdict: …**` form — **8 of 18** here — and
every other row gets an em dash, with the index header stating that coverage out loud.
Table rows, blockquotes, list items and `**Date:**` metadata lines each have a named
negative test; the last is live in `2026-06-21-experiment-1-residual-xsmom.md`, whose H1
matches the Verdict-heading pattern and is immediately followed by `**Date:**`.

### Two divergences from parent #600

**A wrapped verdict is read as a whole paragraph in BOTH forms.** Upstream joined
paragraphs under a Verdict *heading* but left the inline form reading a single line. This
corpus hard-wraps at ~80 columns, so on the first run **6 of the 8** readable verdicts
published as mid-sentence fragments — and because the cut fell on a word boundary with no
ellipsis, a fragment was **indistinguishable from a complete, shorter verdict**. The worst
was `2026-08-12-exit-mfe-mae-diagnostic-rerun.md`, cut at *"…SUPERSEDED at the cohort
level, and"* — dropping the clause that records the finding **reversed direction**, which
is the whole point of that audit. The inline pattern also now consumes an `=` separator
(`**Verdict = FAIL.**`), which otherwise leaked into the cell as a leading `= FAIL`.
**The same defect is live upstream** (2 of its 4 inline verdicts) — raise it there.

The join needed a stop rule, and the first version did not have one: several audits open
with `**Date:** / **Verdict:** / **Audit:** / **Spec:**` on **consecutive lines with no
blank between**, which markdown calls one paragraph, so joining onward merged the *Audit*
field into the verdict and turned two clean one-line verdicts into run-on text crediting a
make target. `_continues_paragraph` therefore stops at a bold field label (`_BOLD_FIELD`)
as well as at a heading, table, blockquote or list — and **both** extraction paths share
it, since the Verdict-heading path had the identical latent flaw. Stopping early costs a
few words; not stopping fabricates a verdict out of adjacent metadata.

**The reconcile column states its own floor.** `0 of N` renders identically whether the
detector found nothing or could never fire, and those are different facts. This corpus has
**zero** spec-reconcile audits, so 0 is the only reachable value; `spec_reconcile_audits()`
is read separately from the per-spec join so the page can say *no reconcile has been
written up* rather than publish a bare `0 of 14` that reads as *14 specs went
unreconciled* — a claim about the specs the data cannot support. (Same class as the
`exits/` MFE median: check the floor is reachable before quoting the number.) A spec counts
as reconciled only when an audit naming its filename says so **in its own filename or H1** —
keying on "reconcile" anywhere in the body mislabelled a doc reconciling two *findings*
upstream, and that case is a regression test here.

**Run:** `make docs-index` (write) / `make docs-index-check` (verify, writes nothing), or
`poetry run python tools/docs_index.py [--check] [--audit-dir PATH] [--spec-dir PATH]`

## distil_power.py — price a hypothesis BEFORE it is written into the inbox

The **G3 gate of `/research-distil`**. Prints the effect size the gate demands at the
declared `n` and trial family, so a claim is *priced* rather than estimated. Ported
verbatim from parent HEAD, with only the module docstring's precedent re-flavored;
`tests/test_distil_power.py` + `tests/test_research_guards_power.py` (48 cases) passed
here with **zero** code adaptation, because `dsr.py` and `psr.py` are byte-identical
across the two repos.

```bash
PYTHONPATH=. poetry run python tools/distil_power.py \
  --units {per_trade|per_alert|per_book_day} \
  --n-obs N --n-trials K --sr-variance V \
  [--n-series S --n-eff E] [--sd SD] [--bar R] [--corpus-best C]
```

`PYTHONPATH=.` is required — the bare invocation dies on `ModuleNotFoundError`, the same
shape as `route_dedup.py`. Exit 2 on a declared-error argument combination.

**Why it exists at all**: trial count dominates n, and it is not close. Re-derived here
against wifey's own `required_sharpe` — holding the trial family at 20, a **21× range of
n** (100 → 2,100) moves the bar **1.17×**; holding n at the live ledger's 267, going from
**1 to 320 trials** moves it **15.9×** (0.101 → 1.611). So a skill that reads three books
and emits forty hypotheses inflates the trial family until every cell is unreachable,
including ones that would have passed alone. The tool is what makes that arithmetic
tracked rather than recalled. Reproduce:

```python
from analytics.research_guards import required_sharpe
required_sharpe(100, n_trials=20, sr_variance=0.25) / required_sharpe(2100, n_trials=20, sr_variance=0.25)
required_sharpe(267, n_trials=320, sr_variance=0.25) / required_sharpe(267, n_trials=1, sr_variance=0.25)
```

**Three flags carry the traps.** `--units` is mandatory with **no default**, because a
figure that looks portable silently changes meaning with the panel — `regime.py` carried
crypto bar counts across the fork, so its "90-day" ATR window really spanned ~270 sessions
on `4h` (RTH is 2 bars/day, not 6) and 12.02% of `4h` labels moved when it was corrected.
`--n-series`/`--n-eff` must be supplied together — one alone raises, and omitting both on
a pooled multi-symbol panel overstates `n`. ⚠ **This repo has no measured `n_eff`**, so
every undeflated run prints an **upper bound** on n and therefore a bar smaller than the
true one; never present such a pass as having margin it did not measure.

`UNREACHABLE` is a **successful output**, not a failure: more data of that shape cannot
fix it, only a smaller trial family can. The null-containment verdict is delegated to
`analytics.audit_guard.powered_null` and is never restated in the tool.

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

## gapfill_audit.py — edge-hunt #5 audit for the gap-fill magnet sleeve

Read-only **edge-hunt #5** audit (PR #198) for `analytics/gapfill/`: runs the pre-registered
four-arm family (`broad_ls` gated / `broad_ls_range` / `reversal_control` / `long_only`) over
the breadth universe (1d) at 0/2/8 bps, prints DSR/PBO/boot-CI + realized β + `corr_reversal`,
plus the descriptive gap population. `build_grid`/`_grid_frame` are the testable units.
**Read `corr_reversal` before `sharpe`** — the magnet construction is mechanically a gap-fade.
**Verdict = EXCLUDED, direction REFUTED** (`docs/audits/2026-08-14-edge-hunt-5-gapfill-magnet.md`).
**⚠ Its "0 bps" column is not cost-free** — `fee_pct` defaults to 1bp; see `velocity_audit.py`.

**Run:** `make wifey-gapfill-audit` or
`PYTHONPATH=. poetry run python tools/gapfill_audit.py [--slippage-bps N]`

## velocity_audit.py — edge-hunt #6 audit for the velocity-alternation sleeve

Read-only **edge-hunt #6** audit for `analytics/velocity/` (thesis H-007): runs the
pre-registered four-arm family (`broad_ls` gated / `depth_control` / `duration_control` /
`long_only`) over the breadth universe (1d), printing DSR/PBO/boot-CI, realized β, **beta-hedged
Sharpe**, alpha t, correlation to both decomposition controls, and **gross turnover**.
`build_grid`/`_grid_frame` are the testable units.

**It is the only sleeve audit with a genuinely cost-free tier.** The three tiers are
`gross (fee=0, slip=0)` / `live (2bps+1bp)` / `stressed (8bps+1bp)` — every sibling audit's
"0 bps" column still bills `fee_pct`'s 1bp default, so only this one can separate "the signal is
absent" from "the costs ate it". **Read `corr_depth`/`corr_duration`, then `turnover`, then
`realized_beta`, and only then `sharpe`** — and when the β guardrail fires, read
`hedged_sharpe` INSTEAD of `sharpe`.
**Verdict = EXCLUDED as a null** (`docs/audits/2026-08-14-edge-hunt-6-velocity-alternation.md`).

**Run:** `make wifey-velocity-audit` or
`PYTHONPATH=. poetry run python tools/velocity_audit.py [--slippage-bps N]`

## expand_universe_sp500.py — one-shot universe expander

One-shot universe expander (experiment #1, PR #98): snapshots the pre-expansion `kind=="stock"`
symbols to `config/universe_sp100_snapshot.json`, then merges the current S&P 500 constituents
(+GICS sectors) into `config/universe.json` as `kind=stock/delisted=False` members (existing
entries idempotently preserved). `merge_constituents` is the pure testable unit. Constituents
source: Wikipedia `pandas.read_html` by default (sends a browser UA; needs an HTML parser), or
`--from-csv PATH` (columns `Symbol,Sector`) for a deterministic/offline run.

**Run:** `PYTHONPATH=. poetry run python tools/expand_universe_sp500.py [--from-csv sp500.csv]`

**Pair it with `stamp_universe_listed.py`** — merged constituents arrive with no `listed` key,
which is the *permissive* value, so an unstamped expansion silently widens every
`min_history_days` cohort.

## stamp_universe_listed.py — `listed` history-seam stamper

Reconciles `config/universe.json`'s optional `listed` date against DB first-1d-bar ground truth
(N3 residual (a), 2026-08-13). `resolve_listed(members, first_bars, floor)` is the pure testable
unit; `first_bar_dates` is the one DB read (read-only, one grouped query, same
`statistics_propagation` workaround as `universe_coverage.py`). Report-only unless `--write`.

**The floor is the whole design.** 1d history starts at the backfill's `--since` (2018-01-01 →
first NYSE session **2018-01-02** via `analytics/trading_calendar.py`), and **477 of 505** members
share that first bar, so a bar *on* it cannot distinguish a truncated survivor from a listing that
day. Only a first bar **strictly after** the floor is stamped — the conservative bound README
already documented. The 2 members reaching 2007 stay `None`. `LISTED_TIMEFRAME` is a constant, not
a flag: stamping from `4h` (history starts 2024-05-16) would mark every survivor as a 2024 listing.

**Why it existed as a defect:** `listed` was stamped on **3 of 505** members by hand, and an absent
date reads as "full-history survivor", so `min_history_days` filtered almost nothing — a 1-year
floor dropped **0** members while `FDXF` sat on **17 bars**. The 2026-08-13 run added **23**
(0 corrections, 0 removals — it reproduced all three hand-stamped dates exactly, which is the
check that validated the rule). A member with **no** 1d bars is left untouched and reported loudly
rather than silently un-stamped.

**Run:** `make universe-stamp-listed` (add `WRITE=1` to apply) or
`PYTHONPATH=. poetry run python tools/stamp_universe_listed.py [--write] [--since YYYY-MM-DD]`

## exit_audit.py — exit MFE/MAE diagnostic

Read-only **exit MFE/MAE diagnostic** for the `analytics/exits/` package (PR #96 / parent #433),
diagnose mode only. Prints coverage, the overall win/loss/expired cohort roll-up, the
per-(strategy, tf, direction) table, and the exit spec §2 verdict grid inline over the live
`signal_alert_outcomes` ledger. No `--replay` (the exit-policy A/B ships with the deferred #437
port).

**Run:** `make wifey-exit-audit` or
`PYTHONPATH=. poetry run python tools/exit_audit.py [--min-n N] [--csv PATH]`

## The four gate-decision replays — and why none of them is runnable work today

`bos_routing_audit.py`, `regime_gate_replay.py`, `regime_threshold_sweep.py` and
`direction_filter_replay.py` all answer the same shape of question: *should a `[bias]` gate flip
from `soft` to `hard`, or be re-routed?* That product is an ENABLE/DISABLE/threshold decision on a
TA gate, which is **inside the frozen category** (CLAUDE.md → Sleeve verdicts). They are
documented here as read-only diagnostics; **running one is fine, acting on it is a user ruling.**

None is wired to a Makefile target — all four are hand-run.

**Two live config blocks cite a PRE-FORK audit.** `[bias.regime.per_strategy] bos =
["high_vol", "range"]` and the `[bias.direction_filter]` justification both quote
`bos_routing_audit.py` at **2026-05-13, n=72,643**. The fork is dated **2026-05-14**, so that
run happened in the parent on **crypto** data. Wifey's `backtest_trades` holds **1,342** `bos`
rows today (24,238 total), so re-running here samples a different, ~54× smaller population.
Neither block changes dispatch right now — both gates are `mode = "soft"` — but treat the cited
avg_r figures as inherited, not measured here. → [[project_crypto_era_inherited_flags]]

## bos_routing_audit.py — T2a full-sample routing probe for `bos`

Re-segments existing `backtest_trades` history for `bos` across
`timeframe × regime × session × volume_state × htf_alignment × direction`, answering "is there
ANY cell with `n >= 30` where `bos` is net-positive?" Full-sample probe, **not** WFO — a positive
cell is a candidate for WFO confirmation, never a promotion on its own. Reuses production helpers
so cell labels match live gate semantics (`analytics.regime.classify_series`, the backtest
`_is_low_volume`/`_is_volume_spike`, and a rolling 4h EMA-50 slope matching the
`[bias.htf_ema]` default anchor). Writes a CSV; makes no DB writes.

**Run:** `PYTHONPATH=. poetry run python tools/bos_routing_audit.py [--db PATH] [--out PATH]`
(default `--out /tmp/bos_routing_audit.csv` — write it to `docs/plans/` instead if you want it
to survive, `/tmp` is cleared).

## regime_gate_replay.py — soft→hard flip evidence for `[bias.regime]`

Replays the v2 Phase 2 regime gate against historical `backtest_trades`, computing `avg_r` on the
subset hard mode would have suppressed vs the subset it would have kept. Deliberately the
**empirical substitute for "wait 2 weeks in soft mode"** — same decision data from history rather
than forward observation. Regime is classified off the most recent **CLOSED** 4h candle at entry
(`_regime_at_entry`), mirroring the live drop-the-in-progress-bar rule.

Its stated decision rule: suppressed `avg_r <= 0` at `n >= 100` justifies the flip; kept >
suppressed means the gate concentrates edge; suppressed `avg_r > 0` at `n >= 100` means the gate
drops winners. **A raw split like this is a point estimate** — pair it with a significance test
before quoting a delta. → [[project_flag_deltas_need_significance_tests]]

**Run:** `PYTHONPATH=. poetry run python tools/regime_gate_replay.py [--db PATH]`

## regime_threshold_sweep.py — slope-threshold sensitivity for the regime classifier

Re-runs `regime_gate_replay`'s annotation across a grid of candidate
`_SLOPE_TREND_THRESHOLD` values in `analytics/regime.py`, reporting suppressed/kept `n` and
`avg_r` plus `lift = kept_avg_r − suppressed_avg_r` per threshold. Tests whether the live 0.5%
default mis-labels exhaustion as trend: if some threshold separates cleanly the §6 mapping is
salvageable, and if none does, the mapping itself is the problem.

**This is a threshold sweep in the literal frozen sense** — it selects a parameter value. Read it
as diagnosis of the mapping, not as a source of a new constant.

**Run:** `PYTHONPATH=. poetry run python tools/regime_threshold_sweep.py [--db PATH]`

## direction_filter_replay.py — soft→hard flip evidence for `[bias.direction_filter]`

The T2c sibling of `regime_gate_replay`, same decision rule and same substitute-for-waiting
rationale. Replays `[bias.direction_filter]` plus per-strategy `suppress_long` / `suppress_short`
against `backtest_trades` and compares suppressed vs kept `avg_r`, with a per-strategy breakdown.

Two standing caveats. It reads the flags from a config you pass (`--config`, default
`config/signal_watch.toml`), so **the answer depends on which of the two live configs you name** —
they are different populations. And `suppress_long`/`suppress_short` are themselves crypto-era
inherited flags; `bos.suppress_long` was found one grep away from the #141 sweep that missed it.

**Run:** `PYTHONPATH=. poetry run python tools/direction_filter_replay.py [--db PATH]
[--config config/signal_watch.toml]`

## combo_health.py — post-refresh spot-check for the co-fire tables

Spot-checks `backtest_combos` and `backtest_cross_tf_combos` after a combo/cross-TF refresh:
totals, freshness (rows from runs in the last N hours), the `day_filter` distribution, and the
count of rows meeting the live alert gates plus the top viable combos. Gate defaults mirror
`[combo]` in `config/strategy_params.toml` (same-TF `tue_thu` + `avg_r >= 1.0`; cross-TF
`tue_thu` + `avg_r >= 0.0`).

**Both tables currently hold 0 rows**, so the tool reports empty and the live co-fire gate is
inert — no alert can carry a confluence tag. That is a *data* gap, not a tool fault: repopulating
means running the combo sweeps, which is frozen sweep work. Read an empty report as "never
refreshed", not "refresh failed".

**Run:** `PYTHONPATH=. poetry run python tools/combo_health.py [--db PATH] [--fresh-hours N]`
after `make wifey-combo-backtest` / `make wifey-cross-tf-backtest` (both `SAVE=1`).

## warning_value_audit.py — do the W1–W8 alert warnings predict avg_r?

Ported from parent #492. **This is the tool that WIRED `analytics/audit_guard.py`** — the guard
sat hostless through Phase N2 because the obvious host (`tools/gate_audit.py`) needs
`low_volume`/`volume_spike` on `backtest_trades`, columns wifey never had, and skipping that
migration fails **silently** via `fillna(False)`. This audit needs neither: it re-derives its
flags from OHLCV. Closing that gap closed SoT N2.

Regenerates each historical trade's six candle-warning flags through `analytics/warning_audit.py`
(which imports the live `alert_formatter` helpers rather than reimplementing them) and emits a
pre-committed SUPPRESS-CANDIDATE / REVERSE / COSMETIC / INSUFFICIENT verdict per
(warning × direction) — bootstrap CI clearing ±`bar` **and** a Holm-adjusted p, one family per
source. `backtest_trades` is primary and deduped across saved runs on
`(symbol, tf, strategy, direction, signal_time)`; `signal_alert_outcomes` is corroboration only.
Read-only.

**`_tf_ms` delegates to `parse_timeframe_secs` — do not "simplify" it back to a literal map.**
Upstream's map spells the weekly timeframe `1w`; every equity surface here uses `1wk`, which
carries 497 signals, so a verbatim copy raises `KeyError` on the first real run.
`TestTimeframeLength` pins this and was mutation-checked in both directions.

**Result (2026-08-13, CORRECTED same day): 11 of 12 backtest cells INSUFFICIENT, one
SUPPRESS-CANDIDATE — `w5_wick_rejection`/long** (n=316 warned at −0.315R vs −0.037R clean; Holm
p=0.002; sign holds on all three timeframes and on 5 of 6 strategies). **Not shipped as a gate** —
the live substrate has n=1 for that cell.

⚠ **The first run reported those 11 cells as COSMETIC and that was an artifact** (parent #617,
ported 2026-08-13p). COSMETIC was awarded on `n >= min_n`, a sample-size floor that says a test
*ran* but never that it could have *seen* anything. Under the honest test — `powered_null`, the
CI strictly inside ±bar — **0 of 11 survive** and **0 of 12 cells** have a CI inside ±0.05R
(half-width median **4.1×** the bar, range 1.8×–6.2×; **4 of 11** point estimates *exceed* the
bar, worst `w1_marubozu`/long at **+0.289R**, CI [−0.007, +0.615]). The honest reading is **"we
cannot tell"**, never "the warnings are decoration". The W5 lead is unchanged — only negative
labels can move. Verdict + caveats: `docs/audits/2026-08-13-warning-value-audit.md`.

**Run:** `make wifey-warning-value-audit` (`ARGS="--source live|backtest|both --min-n N --out PATH"`).

## pundit_score.py — read-only scorer for the Stream-C pundit ledger

Ported from the parent. Resolves every `docs/plans/pundit-calls.jsonl` call against stored
OHLCV → hit-rate + R proxies per author × setup-family × direction, plus a machine-readable
`docs/plans/pundit-priors.json` sidecar. Level parsing, family tagging, roll-up and
output shape are byte-identical to the parent so the two ledgers stay comparable.

**Descriptive priors only — NO gate is implemented, and the docstring used to imply one**
(parent #582's A7, ported 2026-08-13). It read *"audit_guard gates come later, only if a
cell earns n>=30"*, which reads as a live threshold; the only implemented construct is
`--min-n` (default 5), which renders a `⚠` marker and changes no output. **Nothing happened
when a cell crossed 30.** The claim is wrong a second and worse way here than upstream:
wifey *does* have a wired `audit_guard` since #183 (`analytics/warning_audit.py`), but it
scores the **W1–W8 signal warnings** and no code path joins it to pundit cells — so the
sentence read as a forward reference to something that had since arrived. `AUDIT_ELIGIBLE_N
= 30` does **not** restore a gate: `audit_eligible_cells()` returns cell *keys*, and
`render_report` prints a NOTE saying in words that none fires, so the crossing stops being
silent. **Deciding what a pundit prior should gate is an open research question** — do not
wire this to anything without answering it. Preventive today: the largest author cell is
**n=13** of 19 ledger rows, so the NOTE fires on **zero** cells (`make wifey-pundit-score`).

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

### A hyphenated range OVERRIDES a `/`-ladder (measured 2026-08-13)

`parse_level_field` emits `zones` and `numbers` separately, and `select_level` prefers a
zone. So **any** hyphenated range in a `target` field — including a clarifying
parenthetical after a valid ladder — replaces the intended level. Reproduced against the
production functions at `ref_close=64,000`, `role="target"`: `67,000 / 70,362.23 / 82,000`
→ **67,000**, and the same string plus `(or 65k-68k)` → **65,000** long / **68,000** short.

The zone resolves to whichever edge price reaches **first**, so the error runs in *both*
directions: a long lands nearer (manufacturing an optimistic WIN) and a short lands further
(stranding the row OPEN). **This corrects the upstream note in parent #616**, which states
the error "only ever pushes the target further away" — true of its short example, not of
the mechanism. Write-side rule and the `make wifey-pundit-score` round-end check live in
`/ingest-video` step 8 and `/ingest-x` step 5.

### The level-negation guard (parent #589, ported 2026-08-12)

`parse_level_field` drops **all** candidates when a field's *head* negates the level
(`_NEGATION_HEAD_RE`). `_UNSPECIFIED_MARKERS` cannot catch this on its own — it matches the
**whole stripped string**, so bare `not specified` was caught while
`"not stated (implied ~454 resistance)"` fell through to `_NUM_RE` and returned the
parenthetical as the level.

**The sanity gate is no backstop and cannot be made into one.** Its window is
`0.2×–5.0× ref_close`, and the worst form of the phantom number *is* a level near
`ref_close` — it passes, and with exactly one sane candidate `select_level` returned it at
`low_confidence = False`, so a fabricated call was indistinguishable from a real one at the
highest confidence label the scorer has.

**Anchored at the head deliberately.** A negation that *trails* a stated level qualifies its
**provenance**, not its existence — `"~420 (current market, no explicit entry stated)"` is a
real level. Those keep their candidates and set `ParsedField.hedged`, which `select_level`
ORs into `low_confidence`, downgrading rather than deleting a genuine call. An
"anywhere in the text" match — the obvious first design — destroys that second class.

Measured on this repo's **19-row / 33 populated-level-field** ledger (script:
`docs/plans/scripts/pundit_negation_impact.py`; **do not import the parent's 603-field
counts as wifey's**): **2 fields stop fabricating a level, 1 keeps its level at reduced
confidence, 30 unchanged.** Only **one** of the two reaches a published number, and the
discriminating check is why — a parse-layer count is not a scorer-layer count:

| row | field | before | after |
| --- | --- | --- | --- |
| `luckychartape` TSLA short | stop `"not stated (implied ~454 resistance)"` | `454.00`, conf **ok**, **R +3.27** | dropped, conf **low**, R — (ATR-R 6.36) |
| `benjaminjcowen` SLV long | target `"not stated (qualitative; 1970s analog…)"` | `1970` **already rejected** by the gate — ~**38×** SLV's 52.16 reference close, against a 5.0× ceiling | no published change |

`other/short` therefore flips **+1.13 → −1.00 avg R**, and that +3.27 was the ledger's *largest*
positive-R win (the only other is `fenggemeigu` MSFT at +1.35) and the only one that rested on a
fabricated level. **`fenggemeigu` — the one author with a rankable `n` — does not move at all**
(−0.41 either way), which settles the same question it settled upstream: the negative floor on
the only rankable author is real, not a parsing artifact. **Any `pundit-priors.json` generated
before 2026-08-12 carries the fabricated record — regenerate rather than reasoning from it.**

### `avg R` ships its own denominator (parent #602, ported 2026-08-12)

**`avg_r` and `n` are different populations, and the report used to print them adjacent.**
`r` needs a stated stop (`score_call`: `if risk is not None and risk > 0`), so a call that
stopped out necessarily has one while a win scored against a target often does not —
`avg_r` describes a loss-enriched subsample while `n` / `resolved` describe the whole cell.

Measured here 2026-08-12 (19 calls, 8 resolved; script:
`docs/plans/scripts/pundit_r_coverage.py`, which calls the production scorer — **do not carry
the parent's 43%/79% across**, that is a 203-row crypto ledger sharing no rows with this one):

| cohort | resolved | with `r` | coverage |
| --- | --- | --- | --- |
| WIN | 3 | 1 | **33%** |
| LOSS | 5 | 5 | **100%** |

**The censoring is worse here than upstream and it inverts the headline.** Every loss carries
an `r`; a third of wins do. `fenggemeigu` reads `avg R` **−0.41** over `r_n=6` while the
complete `atr_r` sample over all 7 resolved calls is **+0.80** — the two disagree in **sign**,
where the parent's only disagreed in significance. `luckychartape` is the mechanism in one
row: a WIN whose stop was the fabricated level dropped by #589, so it now contributes
`ATR-R 6.36` and **nothing at all** to `avg R` (`— (0/1)`).

The fix is disclosure, not a new statistic: `CellStats.r_coverage`, a
`avg R (r_n/resolved)` cell in both report tables, and `r_n` / `r_coverage` / `atr_r_n` in the
priors JSON. **`avg ATR-R` now leads `avg R` in the column order** because it is the complete
sample. Nothing in this fork reads the sidecar yet, so this is free to re-key.

**Transferable rule: a mean and a count printed side by side assert a shared denominator.**
When they do not share one, the disclosure belongs *in the cell*, not in a footnote — a reader
comparing two authors' `avg R` is comparing two different populations and nothing on the row
says so.

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

### Its OHLCV is a third universe, and nothing else refreshes it

`make go-live` syncs `config/stocks.json` — 13 ETF and equity **proxies**. The ledger records the
index and futures **underlyings** a pundit actually quoted (`^GSPC`, `GC=F`, `^TNX`), so the two
sets barely intersect and syncing one never refreshed the other. Measured 2026-08-15 right after an
operator `CATCH_UP=1 make go-live`: the mega-caps reached 2026-08-14 while eight ledger symbols sat
at 2026-08-04 and `^TNX` had **no bars at all** — and go-live reported success throughout, because
`pundit_score` degrades a stale symbol to `STALE` rather than erroring. A permanently-unresolving
ledger is indistinguishable from one where nothing has triggered yet.

Fixed 2026-08-17 by `wifey analytics {sync,backfill} --pundit`, wrapped as `make wifey-pundit-sync`
/ `make wifey-pundit-backfill`. Three properties worth keeping:

- **It resolves from the ledger at run time**, never from a second hardcoded list. A frozen list
  would reproduce the original defect one level over — the ledger gains symbols as calls are routed.
- **An empty resolve exits non-zero rather than falling back to the watchlist.** A fallback would
  resync the same 13 names go-live already covers and report success, which is exactly the shape
  being fixed.
- **`--universe` and `--pundit` are mutually exclusive at argparse level**, so a conflicting pair is
  rejected outright instead of silently resolving by precedence.

`INVALID_LEDGER_SYMBOLS` (in `utils/config_validation.py`) is the single definition of "not a
symbol", imported by both the loader and this scorer — if they diverge, the sync path fetches
symbols the scorer discards, or skips ones it scores. `tests/test_analytics_runner.py` pins the
identity.

After scoring, the ledger's own state is the check that the refresh worked: 22 rows, 0 `STALE`,
0 `UNRESOLVABLE` as of 2026-08-17.

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
while `fetch_x_post` stays pure. **Thread path** `walk_thread` (parent #591) recovers one
author's self-thread by following `in_reply_to_status_id_str` **upward** from the tail,
returning a `ThreadChain` (posts root→leaf with `thread_pos`, plus `notes`). **The direction is
a hard constraint, not a choice: the endpoint has no replies/children field, so a thread is
reachable only from its LAST post — a bookmarked parent yields nothing below it.** `XPost`
gained `in_reply_to_id` / `in_reply_to_author` / `conversation_count` / `thread_pos`, **all
defaulted** because `_load_cached` does `XPost(**raw)` and any pre-existing cache entry lacks
the keys (wifey's `.cache/x-posts/` is empty, so this is inherited belt-and-braces here — and
`_load_cached` already catches `TypeError` as a cache miss, so it fails safe either way). The
walk stops at the root, on an author change (climbing further would attribute another pundit's
words to the bookmarked author), at `max_hops` (25), or on an unavailable hop — every stop but
the root records a note, and it never raises. It **reads** the per-id cache but deliberately
does **not write** it: an entry written here with empty `photo_paths` would make a later ingest
of that post skip its chart download. ⚠ `conversation_count` counts the whole conversation's
replies (everyone's) and is **not** thread length. Backs the `/ingest-x` skill (ported from
parent #466/#467; spec `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`).

**Run:** `PYTHONPATH=. poetry run python tools/x_fetch.py <url…> [--batch] [--thread] [--json] [--force] [--min-delay/--max-delay S] [--cache-dir/--media-root DIR]`

## x_route.py — routing decision and shared level sign-check

Pure routing decision **and** the shared level sign-check for the ingest skills.
`route_target(content_type, verdict, *, retrospective=False, rejected=False) -> str | None`
is the content-type gate
setup/mechanic/claim → the research pipeline's 4-bucket verdict taxonomy on the claim path →
Stream A `thesis-inbox.md` / B `mechanics-backlog.md` / C `pundit-calls.jsonl`, or drop.

**Two keyword-only suppressors drop a `setup`** — `retrospective` and `rejected`, both
defaulting to `False` (parent #521, ported 2026-08-13). A `setup` with either returns
`None`; a `mechanic`/`claim` is unaffected on purpose, since neither has an entry to
decline and honouring the flag there would let one mis-set field delete a routable item.

**They were missing here for 13 days and the gap was invisible.** wifey ported this file
on 2026-07-31 (#123); the parent added the suppressors on 2026-08-01 (#521) — a timing
artifact, not an equity divergence. What hid it: **wifey's #130 cites `#518/#521` in its
own title** while porting only #521's `route_dedup` half, so the PR number reads as
"already applied" to any check keyed on citations. **A PR cited in a port commit is not
evidence that every half of it landed** — #521 touched `x_route.py` *and*
`tests/test_x_route.py`, and #130 touched the first by 14 lines and the second not at all.

Live consequence while it was missing: `/ingest-video`'s pass 1 *does* set `retrospective`
on a setup lifted from a channel's intro recap, and nothing read it — so the call routed
to `pundit-calls.jsonl` carrying **today's** `call_ts_utc` and was scored on an
already-resolved trade. **Zero rows were actually damaged** (all 19 ledger rows predate
the flags and none carries either field), so this is preventive.

**`unattributable` is the parent's third suppressor and is deliberately absent** — it
belongs to the relay-attribution work (#558), which this fork has refused. **Callers must
pass the flags explicitly**; defaulting to `False` means a forgetful call site fails open
and silently, which is exactly how the original gap survived.

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

## yt_feed.py — YouTube channel auto-feed backing `/ingest-feed`

Ported from parent #515, taken at **parent HEAD** rather than at #515's merge commit, so
six follow-ups (#516, #529, #535, #558, #582, #585) land with it (724 → 894 lines).
**#516 is the one that matters operationally** — it added `load_dotenv()`, without which
`YOUTUBE_API_KEY`
in `.env` is invisible and every API subcommand fails; porting #515 literally would have
shipped a feed that could not authenticate.

Read-only `poll` of each configured channel's uploads playlist (Data API v3,
`YOUTUBE_API_KEY`, ~2–3 units/channel/day, **never `search.list`**) + `backfill` deep pager
(floor ignored, ledger respected). **`poll --since` NARROWS the floor only**
(`max(floor, since)`, shared `_parse_since` with `backfill`): the floor records what the
operator already declined, so honouring an earlier `--since` would resurface it — reaching
below the floor stays `backfill`'s job, and that asymmetry is the whole difference between
the two subcommands. `mark` is the **ONLY** writer, stamped post-review-gate, so the
wifey-#68 watermark-on-send defect class is structurally impossible: no fetch-time writes,
no moving watermark, static per-channel `floor_ts`. Plus `resolve` (handle → ready-to-paste
TOML block) and `hint` (pure local config read; resolves **ahead of** the API-key gate, so
it needs no `YOUTUBE_API_KEY`).

Config is the gitignored `config/youtube_channels.toml` (committed `.example`). State is
`docs/plans/yt-feed-state.json` (gitignored, atomic writes, loud-abort on malformed).
Injected HTTP `get` → network-free request-shape tests (70 of them).

**Two wifey-specific divergences, both re-derived here rather than inherited:**

- **`item_cap` is nearly inert in this repo.** It defaults to `video_marks.ITEM_CAP`,
  imported not re-literalled — so it correctly picks up wifey's **12** (the parent's is
  5, raised here in #128 alongside a `MIN_ITEM_SPECIFICITY` floor). But `/ingest-video`
  requires `item_cap + len(TAIL_OFFSETS_S) <= FRAME_CAP`, i.e. **≤ 13**, so the usable
  range is 13..13. **`yt_feed.py` does not validate this** — it imports `FRAME_CAP` only
  to estimate tokens — and a larger value silently degrades kept items to
  `vision_confidence: "low"`. The parent has 8 of headroom and never hit the ceiling.
- **`hint` EXITS 1 when `config/youtube_channels.toml` is absent** (`load_feed_config`
  raises `SystemExit`; it does not return `matched: false`). That file may legitimately
  not exist here, since `/ingest-video`'s primary mode in this repo is a hand-pasted URL
  with no follow list — so its step-3 call is guarded with `|| true` and a missing config
  is treated as `matched: false`, not as an error.

**Run:** `PYTHONPATH=. poetry run python tools/yt_feed.py poll|backfill|mark|resolve|hint`

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

## sync_parent.py — parent-repo PR triage for `/sync-parent`

Enumerates parent PRs merged since the memory-held sync pointer, groups them by the `(#N)`
squash suffix (the parent has **no merge commits**), translates each touched parent path to a
wifey target, and classifies SKIP / PORT / EVALUATE with an ALREADY-APPLIED confidence overlay.
Read-only on both repos except the state file, which only `--bump-to` writes.

**The report is the triage artifact, so it is written in-repo:**
`docs/plans/parent-sync/parent-sync-<date>.md` (gitignored via `docs/plans/`, directory created
on demand). It was `/tmp` until 2026-08-11 — a `/tmp` clear destroyed the 2026-07-29 report with
**57 of 67 PRs still undecided**, forcing a full re-scan. A reviewer decides PRs against this
file over days; it has to outlive a reboot.

**The parent's checked-out branch does not matter.** Every parent read is ref-based against
`origin/main` (`cat-file` / `fetch` / `log` / `show` / `rev-parse`) and nothing touches the
parent working tree, so the only real precondition is that `origin/main` resolves. A
checked-out-branch guard blocked scans outright on 2026-06-17 and 2026-08-11 and was removed.

**ALREADY-APPLIED reads SYMBOLS, and a MODIFIED symbol's name is not evidence** (fixed
2026-08-13q). A signature-only change re-emits its own `def` line, so the name sits on both
sides of the diff and a name-presence grep matches wifey's *old* copy. Parent **#521** is the
worked example — its entire payload was two kwargs on an existing `route_target`, and the
resolver returned **HIGH / ALREADY-APPLIED** while wifey had only the two-arg version, a real
missed port that stood until #186 closed it by hand. `extract_symbol_changes` now splits
`added` from `modified`; a modified symbol's evidence is the **identifiers the change
introduced** (restricted to syntactically-used tokens, since a bare `\w+` sweep harvests
docstring prose and the wifey grep is repo-wide), and modify-only with no new identifier
reports **UNKNOWN** — "cannot tell", never "applied". Parsing is hunk-scoped: `git show`
without `--format=` prepends the commit message, whose prose has no diff prefix and otherwise
reads as context, subtracting the very identifiers the change introduced.

**Two further limits to know before trusting a bucket.** The classifier defaults to EVALUATE whenever a
path resolves, so at a wide range the counts degrade (2026-08-11: 0 SKIP / 39 PORT / 107
EVALUATE over 156 PRs). And **buckets cannot see portability** — a mechanical check of whether
the touched files exist in wifey is the cheap filter, and it inverts the parent's own ranking:
on 2026-08-11 `fix(xsmom)` #572 touched **0** files present here despite wifey owning
`analytics/xsmom/`.

**Run:** `make wifey-sync-parent [FROM=<hash>] [FULL=1] [NO_FETCH=1] [BUMP_TO=<hash>]`. Never
`--bump-to` while the state file doubles as a memory — it rewrites the file to a stub and wipes
the triage body; hand-edit the frontmatter pointer instead.
