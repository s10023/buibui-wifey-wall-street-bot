# CLAUDE.md

This file provides instructions for Claude Code when working in this repository.

**Model delegation policy.** The main thread (Fable) is the orchestrator/tech lead — design, judgment, review, and routing stay here; don't burn main-thread quota on bulk mechanical work. Delegate down by tier: **sonnet** subagents for high-volume execution with a self-contained inline brief (vision extraction, file sweeps, boilerplate, test triage); **haiku** for trivial one-shot lookups; **opus** subagents only as a quota escape valve for long *parallel* research — Opus sits below Fable in capability, so this conserves limits, it does not buy better thinking. Every subagent brief must be drift-proof: goal + success metric + rubric inline, no SoT/memory re-reads. Verify subagent/background work directly (`ps`, `journalctl`, `git status`) — self-reports can be stale. **Only 2 subagents run at once** — a 3rd launch is blocked outright, so any fan-out wider than 2 must be pipelined by hand in pairs and budgeted for the wall-clock; a batch dispatched optimistically stalls on the blocked launch.

## Fork lineage

This repo is a fork of `s10023/buibui-moon-trader-bot` (parent), forked 2026-05-14 and frozen at parent commit `635ed5a`. It is being repurposed from a Binance crypto bot into a yfinance-backed US-equities signal bot.

- **Sister memory** (parent's accumulated wisdom — strategy edges, regime classifier history, F8/F9/T2 work, sweep findings, gate architecture) lives at `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`. Read it when working on a feature that exists in both repos (back-port / forward-port). Skip parent's `Current State` and any crypto-specific findings (`smt_pairs`, `funding_reversion`, BTC/ETH/SOL cells, CME gap).
- **Active work** (since 2026-06-10): driven by the goal-anchored **master to-do** at `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/project_todo_master.md` (north star + acceptance gates G1–G4 — the single source of truth; this file no longer carries a task queue). Hybrid scope: correctness + universe groundwork now (Phase 0.3c purged CV → 0.1 universe-as-of policy → 0.4 cost model; research-guards parity; breadth universe + lifecycle data); XS-momentum forecasts + paper sizing wait for the parent to pass gate G1. **TA detector and sweep work is frozen** — no new boolean detectors, no tp_r/gate/threshold sweeps (inherited category verdict; see memory `project_parent_fresh_eyes_port.md`). Phase B (order layer + broker pick) stays deferred, gated G3→G4.
- **Fork bring-up queue retired**: the original plan-§0.e queue (T6 → … → T16) shipped in full between 2026-05-14 and 2026-06-04; see `docs/superpowers/specs/2026-04-10-tradfi-equity-fork-design.md` / `docs/superpowers/plans/2026-04-10-tradfi-equity-fork.md` for the historical design.

## Project Overview

Buibui Wifey Wall Street Bot — a yfinance-backed US-equities signal bot (Phase A: signals only). Analytics/backtest stack (DuckDB), a multi-strategy signal engine with Telegram alerts, and a FastAPI + Svelte web UI. Phase B = order layer (broker TBD), deferred. Python 3.11+, managed with Poetry.

## Key Commands

After making **any** Python code change:

```bash
make lint-py        # ruff format + lint
make typecheck      # mypy strict
make test           # full pytest suite (no coverage; use `make test-cov` for that)
```

For Markdown changes: `make lint-md` — this covers `.claude/` (skills and context) as of 2026-08-05, so a skill edit lints like any other file and CI fails on a violation. No special invocation is needed; do not re-add a `!.claude` exclusion to `.markdownlint-cli2.jsonc` (the tree accumulated 245 issues while it was excluded, and the excluded-tree failure mode is silent — see `/post-branch` step 4).

After adding or renaming a file in `docs/audits/` or `docs/superpowers/specs/`: `make docs-index`.
Both `INDEX.md` files are **generated** (`tools/docs_index.py`) and
`tests/test_docs_index.py` regenerates them and compares byte-for-byte, so an unindexed
audit or spec **fails the suite** rather than rotting quietly. `make docs-index-check`
verifies without writing.

For UI / API changes: `make web-build` (production bundle) or `make web-dev` (Vite dev server).

For routine DB refresh after backtest/strategy changes: `make db-update` (= `db-update-backtest` → `db-update-recalibrate` → `regression-update` → `check-dead-surfaces`). The last step reports `(strategy × timeframe)` cells where declaration and output disagree in **either** direction — declared-but-dead (detector never fires, so a dead surface can't hide behind rows that merely exist) and rated-but-undeclared (a `confidence_ratings` row outliving the config that produced it). It never blocks the refresh, but the completion banner is **conditional on it**: a failure prints a warning instead of `✅`. Run `make check-dead-surfaces` alone for a non-zero exit. **A golden diff from that run is usually DATA DRIFT, not your change** — `regression-update` re-derives the fixture parquets from a DB that has moved on. The success banner prints the falsifier (`git checkout -- tests/fixtures/ && make test-regression`); if it passes, revert the goldens rather than shipping them. It does **not** cover a `confidence_ratings` star move — attribute those by re-running the sweep twice over one fixed window (`/db-update` step 3).

To snapshot the irreplaceable state: `make backup` (`make backup-dry-run` to see what it
would capture). It writes a **verified** copy of `analytics.db` plus the whole of
`docs/plans/` to `~/backups/wifey`. Both trees are gitignored and single-copy —
`git ls-files docs/plans/ | wc -l` returns **0**, so `git clean -xdf` deletes the entire
research pipeline's output with no prompt, and `analytics.db.bak` is an undated unverified
byte copy, not a backup. Coverage is a **denylist over a wholesale copy**, deliberately not
the parent's allowlist: an allowlist over a single-copy tree defaults to UNCOVERED, and the
parent's own script records its list being found short twice. Full rationale, restore
procedure, and the opt-in timer: `deploy/README.md`.

## CLI

`wifey.py` is the single CLI entry point with subcommands:

- `wifey signal watch | test` — live signal daemon / historical replay
- `wifey analytics backfill | sync` — OHLCV ingestion (will switch to yfinance in T2–T5)
- `wifey backtest` — run/save backtests (sweep, combo, cross-TF modes)
- `wifey digest` — pre-canned analytics queries
- `wifey param-audit | param-sweep` — WFO parameter tools
- `wifey recalibrate` — refresh star ratings
- `wifey web` — start FastAPI backend

Each Makefile `wifey-*` target wraps the equivalent CLI invocation.

> The legacy `wifey monitor price | position` subcommand and the entire `monitor/` package were removed in T16-partial (2026-05-15). Equity price/position monitoring lives in the FastAPI + Svelte web UI under `web/`.

## Project Structure

Package-level map. **The deep reference lives in `.claude/context/`, and the pointers below are the
only path a session has to it — follow them before working in an area.** Verdicts and footguns stay
HERE on purpose: they are the guard rail against re-litigating settled research, and a guard rail
behind a pointer is not a guard rail.

**What a footgun entry holds, and what it does not** (convention set 2026-08-13). This file is
auto-loaded on **every** session, so its size is a per-conversation tax. Each entry therefore keeps
only what a session must know *before it acts*: the **rule**, the **enforcing mechanism** (the
required kwarg, the refusing loader, the asserting test), any **operational constant** still in
force, and the **transferable lesson**. The discovery narrative — how it was found, the full
measured impact, the rejected alternatives — belongs in the linked **committed** audit, and was
moved there for the nine entries that have one. **Do not re-expand a narrative here**, and **do not
compress an entry whose only home is `docs/plans/` — that tree is gitignored and single-copy**, so
the text would simply be destroyed. If a footgun has no committed audit, write one first.

| Package | What it is | Deep reference |
| --- | --- | --- |
| `wifey.py` · `cli/` | Thin CLI entry shim delegating to `cli.main:main`; argparse subcommand package (`signal` / `analytics` / `backtest` / `digest` / `param` / `recalibrate` / `web`) with `_common.py` helpers | — |
| `analytics/` | Analytics data layer (DuckDB): `store/`, `strategies/` (18 detector modules, **16** registered for dispatch), `backtest/`, `signal/`, `stats/`, `research_guards/`, `sweep_guard.py`, `audit_guard.py` (hosted by `warning_audit.py` since 2026-08-13), `db_retry.py`, plus the data-ingest + quality + calendar layer | `context/analytics.md` |
| `analytics/{forecast,xsmom,lowvol,xasset,pead,gapfill,exits}/` | The P2/P3 research sleeves and the exit diagnostic — **verdicts below** | `context/analytics.md` |
| `signals/` · `utils/` | Alerting + dedup daemon (detection lives in `analytics/`); shared Telegram / yfinance / EDGAR clients and the two config-universe loaders | `context/signals.md` |
| `web/` | FastAPI backend + Svelte 5 / Vite UI | `context/web.md` |
| `tools/` | One-shot analysis, audit, and research-ingest scripts; not part of the daemon or CLI surface | `context/tools.md` |
| `trade/` | **Empty placeholder — both files are 0 bytes.** The parent's Binance Futures opener was dropped at fork time; nothing replaced it. `make wifey-open-trades` *ran* the empty file and exited 0 behind a success banner until 2026-08-06, and now fails loudly. Phase B (equities broker, TBD) is where an order layer would land | — |
| `tests/` | pytest suite; tests import from lib modules and pass mock dependencies directly | — |
| `migrations/` | One-shot DB migration scripts, run by hand — `001_day_filter_text.py` (fork-era) and `002_adr_threshold_executed.py` (2026-08-11, dry-run by default). Both refuse to start without a `.bak`, and both rewrite `run_id` + cascade to `backtest_trades`. Routine schema changes go through `analytics/store/schema.py`'s migration list instead | `context/migrations.md` |
| `config/` | `stocks.json` (gitignored 13-symbol live watchlist), `universe.json` (committed 505-member research breadth universe), `strategy_params.toml` (shared base inherited via `extends`), `youtube_channels.toml` (gitignored `/ingest-feed` follow list; `.example` committed) | `context/config.md` |
| `deploy/` | `backup-analytics.sh` (verified local snapshot of `analytics.db` + the whole gitignored `docs/plans/` tree), `notify-failure.sh`, and **opt-in** `wifey-*` systemd user units. Nothing installs them; there is still no wifey daemon | `deploy/README.md` |

### Sleeve verdicts — do NOT rebuild a shelved sleeve

**Seven** sleeves have been built and measured on equities. **Every one is non-positive.** The
free-data edge-hunt arc was **CONCLUDED** 2026-06-24 (synthesis
`docs/audits/2026-06-24-honest-exit-free-data-edge-arc.md`); do not start a new free-data hunt
without an explicit user go. **One such go was given 2026-08-14** and produced edge-hunt #5
(`gapfill/`, EXCLUDED) — a reopening is per-candidate and does not un-conclude the arc.

| Sleeve | Verdict |
| --- | --- |
| `forecast/` EWMAC trend (P2, PR #91) | **G2 = FAIL** — portfolio Sharpe −0.05, negative even *pre-cost*, so a signal failure not a cost failure |
| `xsmom/` cross-sectional momentum (P3, PR #92) | **G3 = FAIL** — combined Sharpe −0.156 @2bps, negative at 0bps; `corr_to_trend` +0.62, so not a diversification win either |
| `xsmom/residual.py` residualised XS (experiment #1, PR #98) | **FAIL** — committed `broad_residual_skip` cell Sharpe +0.15 @2bps, DSR 0.44, boot_lo<0. The long-only leg's +0.88 is survivorship/beta-confounded and is **not** the gated cell |
| `lowvol/` low-beta / BAB (edge-hunt #2, PR #100) | **FAIL** — committed cell Sharpe −0.069 @2bps, DSR ~0.03. The realized-beta guardrail **FIRED** (β +3.9, not ≈0) → a fail of *this construction's neutrality*, not a clean BAB-premium test |
| `xasset/` cross-asset TSMOM (edge-hunt #3, PR #102) | **FAIL (clean)** — `broad_ls` +0.41 cost-free / +0.36 @2bps, never ≥0.7; PBO ~0.79. The equity-β guardrail **held** (β −0.083), so the construction diversified as designed and the premium is simply too weak in free-ETF proxies |
| `pead/` PEAD-lite (edge-hunt #4, PR #104) | **FAIL** — `broad_ls` +0.10 @2bps, DSR 0.20. The β guardrail **FIRED** (β ≈ +113, governor saturation on sparse daily cohorts); the controlled mega arm (β −0.40) showed *negative* drift (−0.53) |
| `gapfill/` gap-fill magnet (edge-hunt #5, PR #198) | **EXCLUDED — the direction is REFUTED, not merely unsupported.** Cost-free the magnet returns **−0.460**, so gaps *continue* rather than revert; the post-hoc inverse is **+0.392**, below the 0.7 bar before a single bp. Gated `broad_ls` fails all four legs (−1.333 @0bps, DSR 0.000, boot_lo −1.984). At **~211× daily gross turnover** the 1bp fee alone costs ~0.9 Sharpe, so neither direction is tradeable. **90.3% of gaps fill within 60 sessions — but a matched placebo level fills 88.9%, so the gap-specific lift is +1.5pp** (peaking +5.7pp at 5 sessions, decaying to nothing by 60). **Never quote the 90.3% without the null**: at 60 sessions it is 98.3% reproduced by an arbitrary level the same distance away. The descriptive claim is true, almost entirely diffusion, and inert — which is the whole finding. Range-regime conditioning changes nothing (−1.317). β guardrail fired (−1.564) but the cell fails on every surviving leg independently. Ran only because the user reopened the concluded arc for it |
| `exits/` MFE-MAE diagnostic (PR #96) | **EXIT-FIXABLE at the cohort level** (re-run 2026-08-12, n=264, 264/264 scored). Supersedes the n=22 INCONCLUSIVE call **and reverses its direction**: of the 157 losses that could show excursion, **43.9%** reached ≥1R before stopping (CI 36.4–51.8%), vs the 13.3% that produced the earlier "entry-broken" read. Still blocked per-edge (0 of 30 loss cells reach n=30) and the whole ledger is **pre-#151**. Audit: `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md` |
| `exits/` policy replay A/B (PR #194, parent #437) | **BOUNDED** (reframed 2026-08-14b) — **the lever's ceiling is +0.368R of paired uplift and it buys no measurably profitable book.** All three arms beat `fixed` on a paired bootstrap CI clear of zero (day-clustered too), and the effect is **entirely the time lever**: `time_only` **+0.317R** [+0.173, +0.468] *exceeds* the full `composite` **+0.297R**, so bolting breakeven+partial onto a time-stop makes it worse — the 43.9% figure above shows a lever *could* work, it does not rank it against levers it never measured. **But a paired CI certifies "A beats B", never "A makes money"**: no arm's own mean R clears zero once the **31** ET session days rather than the 267 alerts are the unit, and the swept maximum over the 10×2 time-stop grid is arm-level t **+2.52** against a Bonferroni bar of **2.81** — the *paired* maximum (t +4.91) clears it, the arm-level one does not. Baseline avg_r −0.176, itself only t −1.84; both parameters in-sample. **"Mean R of an open position peaks at bar 3" was a MISLABEL** — that table is the arm's own avg_r at `time_stop=k`, i.e. the sensitivity sweep shifted by the baseline; positions genuinely still open at bar k *improve* monotonically (+0.283 → +0.951) because a stop removes losers first, so the honest claim is about the marginal bar over the whole book, never a position's own trajectory. Audit: `docs/audits/2026-08-14-exit-policy-ab-v1.md` |

**The TA detector book is frozen** — no new boolean detectors, no tp_r / gate / threshold sweeps
(inherited category verdict). A guardrail firing (`lowvol`, `pead`) means the *construction* failed
its own neutrality precondition, so that cell is not evidence about the underlying premium.

### Footguns — read before touching these

**CRITICAL — `analytics/store/_common.py::_upsert`** uses explicit `conn.register` /
`conn.unregister` in try/finally. Never switch to the implicit replacement scan (it causes malloc
heap corruption) and never drop the try/finally.

**`read_only=True` does NOT let a second PROCESS in — and a bare `except duckdb.IOException` is
therefore a trap.** On duckdb 1.5.5 only reader-vs-reader shares; a writer refuses a read-only
opener with the *identical* `Conflicting lock` message (verified 2026-08-12 by holding a
connection from a child process). Two consequences. **Open through
`analytics/db_retry.py::connect_with_retry`, not `duckdb.connect`, at every write site in
`analytics/` and `web/` — with one deliberate exception**: `web/api/routers/stats.py`'s cache
write is on the request path, where a ~52s retry would block the response the cache exists to
speed up. And **narrow every `except duckdb.IOException` with `is_lock_conflict`**: DuckDB
raises that one class for *all* I/O failures, so the two `web/` handlers were reporting a
missing or corrupt database as "busy, try again in a few seconds" — advice that can never come
true — and `main.py`'s `except: pass` started the API with no schema and no complaint. Both
also blamed "the signal-watch daemon", which **this fork does not have**. The retry itself is
**preventive, not a repair** (upstream's premise is colliding systemd timers; wifey has no
daemon), and its budget deliberately does *not* outlast a `make db-update` sweep — a job that
collides with one should fail loudly, not hang.

**`DEFAULT_DB_PATH` lives in `analytics/store/_common.py`** (re-exported via `analytics.store` and
`analytics.data_store`) — import from either re-export, never redefine it in a runner.

**Causality is enforced by a test, not by review.** `tests/test_lookahead.py` (Phase 0.2) is a
truncated-series property test asserting no detector or backtest fill depends on bars after its
`open_time`; the written audit is `docs/redesign/phase0-lookahead-audit.md`. It surfaced one real
leak (`bos` read a centered swing window but stamped the signal at the swing bar) — fixed
2026-06-09 by stamping at the confirmation bar; `_KNOWN_LOOKAHEAD_DETECTORS` is now empty and all
16 detectors pass.

**Two filters that are each individually sane can cancel each other out, and nothing will say so.**
`[bias] adr_suppress_threshold` (quiet small-range bars) and `volume_suppress` (≥1.5× volume
bars) select on quantities correlated at **+0.65**, so declaring both without `adr_exempt = true`
discards ~99% of a strategy's signals — it ran `doji × 1d` at **0** signals against 1,247 raw
fires. **Enforced**: `load_signal_config::voided_volume_gates` now refuses the pairing.
**Rule**: a per-strategy flag whose correctness depends on a second flag must live in the
**shared base**, not one day-filter config — and *a test that asserts a config value PARSED can
never detect that the parsed value produces nothing*.
Audit: `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

**A gate can be undefined on a timeframe and still look like it is working.** The ADR gate needs
**>1 bar per calendar day**; `1d`/`1wk` have exactly one, so the ratio silently stops measuring
exhaustion and its direction guard makes `chasing` true **by construction**, dropping 100% of
`doji`/`ema`/`trend_day` signals above threshold. It stays dispersed (p25 0.71 / p75 1.21), which
is why it reads as functional. **Enforced**: `adr_gate_applies(timeframe)` — intraday only,
unknown timeframes fall closed, and `timeframe` is a **required** arg so mypy forces every call
site to state it. **Rules**: parity by shared function *name* is not parity (live is `1h`-derived
and was never degenerate); re-derive a crypto-inherited constant against equity bar counts (`4h`
RTH is **2** bars/day, not 6); and a green test proves the code matches the fixture, never that
the fixture matches production. `check-dead-surfaces` cannot see this class — it finds exact
zeros, and a 77–82% haircut leaves the cell non-empty.
Audit: `docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md`.

**A gate that fails open has no loud failure mode, and a config value that is RECORDED is more
dangerous than one that is ignored.** `[backtest] days = 365` reached neither the OHLCV cache nor
`run_scan_cycle` (both took the **90**-day default) while the writer recorded 365, so the audit
trail corroborated the wrong window; `passes_ev_gate` returns `True` below `min_trades`, so the
narrow window never failed and the hard gate was a no-op on **71%** of direction-legs.
**Enforced**: a single `bt_days` feeds both surfaces. **Rules**: cross-check a recorded parameter
against a recorded **observable** (`data_end_ms - data_start_ms` found this in one query); and
when a value reaches its consumer **through a cache, fixing the consumer's argument fixes
nothing** — find who populates the cache first.
Audit: `docs/audits/2026-08-06-live-ev-gate-window.md`.

**A row key derived from PARAMETERS does not identify a MEASUREMENT, and `INSERT OR REPLACE` turns
that into silent data loss.** `backtest_runs` has **four** writers; when the key hashed only the
param tuple, the daemon's single-strategy row *replaced* the competed sweep row in place and
flipped `sweep_id` to NULL — the runner produced **312** rows and the table held **263**.
**Enforced**: an `origin` discriminator naming the *writer*, a **required** kwarg on
`upsert_backtest_run` (`"sweep"` is unsuffixed so historical run_ids still resolve).
**Rules**: when a table has more than one writer the writer belongs in the **key**, since a
provenance *column* cannot help if the loser is deleted before any query runs; and check a
producer's output count against what the consumer stored — all 263 rows held correct values, only
the count was wrong. Audit: `docs/audits/2026-08-07-backtest-runs-writer-collision.md`.

**`conflict_resolver` is the ONE live-parity gate the sweep must never run, and that is
load-bearing, not an oversight.** It **reads `confidence_ratings`**, so switching it on inside the
sweep that *produces* them closes a loop: measured over three consecutive passes it went 108 → 66
rows differing — damping but **not converged**, with cells still oscillating at iteration 3.
Five gates are on in `config/strategy_params.toml` (the **shared base**, deliberately not a
Makefile flag — a flag is how the two surfaces diverged unnoticed for ~2.5 months); with five,
two consecutive passes differ on **0 of 160** rows. **Enforced**: `TestSharedBaseGateState`.
**Rules**: a gate that reads a table its own pipeline writes is not a gate, it is a fixed-point
iteration — check data-flow direction before enabling one in the producer of its own input; and
"it converges" needs three points, not two. The live path is unaffected — it reads ratings already
written. Audit: `docs/audits/2026-08-07-live-parity-ratings-sweep.md`.

**A sample-size guard that counts a different population than the one it tests is not a weak guard,
it is not a guard.** The live EV gate compared the **combined** closed-trade count against
`min_trades` and then tested a **directional** `avg_r`, so a long verdict could rest entirely on
short trades — **53 of 260** blocked legs had fewer trades in the tested direction than the config
demands, **19** of them a single one. No `min_trades` value fixes it. **Enforced**: the gate counts
`long_closed_trades` / `short_closed_trades`; `README.md` had documented the directional semantics
all along, so the fix *restored* a calibration rather than choosing a new one.
**Rules**: a gate that fails open inverts the meaning of "stricter" (at `min_trades = 20` the `1wk`
gate reaches **100%** bypass); a test that re-implements the code under test can never falsify it
(all five `TestEvGate` tests copied the comparison inline and passed against any implementation, so
**extraction is a prerequisite for the fix**); and suppression upstream of the recorder destroys
evidence, not just output — a blocked leg never reaches `signal_alert_outcomes`.
Audit: `docs/audits/2026-08-07-ev-gate-directional-sample-guard.md`.

**A threshold applied to a POINT ESTIMATE is a coin flip with extra steps.** `min_avg_r = 0.0`
suppressed any negative directional `avg_r` regardless of dispersion: **84 of 207** blocked legs
sat at **|t| < 1**, and a cell at −0.006R was blocked exactly as hard as one at −1.01R.
**Enforced**: `min_avg_r_z` (default **1.64**, one-sided 95%) — the shortfall must be that many
standard errors below the threshold. `min_avg_r` itself is unchanged, so this changed the decision
RULE, not the line, which is why it is correctness and not the frozen threshold-selection.
**Rules**: multiplicity correction belongs where SELECTION happens — BH is right for a sweep and
wrong for a per-leg operational gate, where Bonferroni z ≈ 3.5 would silently disable a fail-open
gate; measure the cheap alternative against a bar you wrote down FIRST (recovering sd from win rate
failed at median **10.6%** error and flipped **28.5%** of verdicts); and when a cached type shadows
a computed one, a new statistic must be added to **both** — `BacktestSnapshot` is the hot path.
Audit: `docs/audits/2026-08-07-ev-gate-significance-test.md`.

**A provenance column that records what was DECLARED is not provenance, and when that column is part
of the row-identity hash, fixing the writer without migrating history MANUFACTURES evidence.**
`backtest_runs.adr_suppress_threshold` stored the declared value flat, so **2,091 of 3,246 rows
(64.4%)** claimed a gate that never touched them — **1,974** purely by timeframe, only **117** by
`adr_exempt`. **Enforced**: `effective_adr_threshold(declared, timeframe, adr_exempt=…)` as a
**required** kwarg on `upsert_backtest_run`. **Rules**: mypy cannot enforce a required kwarg
through a `**dict` splat (12 sites type-checked clean and blew up at runtime — run the suite); a
column in the identity hash cannot be corrected in place without a migration, since flipping the
value changes `run_id` and leaves the old row behind as a fake gated-vs-ungated pair; a migration
must respect **code eras** (only the **518** post-#142 rows were rewritten); and check whether the
consumer already assumed the correct semantics — `recalibrate_lib` did, which is why the migration
is provably rating-neutral. Full narrative: `migrations/002_adr_threshold_executed.py` docstring.

**`INSERT OR REPLACE` with a PARTIAL row silently blanks every column the caller omitted.** This is
the sibling of #148 — there the key collided, here the *row* is right and the payload is short.
`upsert_signal_outcome` took a 16-column row and the scanner (its only production caller) passes no
`outcome` / `outcome_r` / `outcome_filled_at_ms` key, so `row.get(col)` → NULL and **a re-detection
erased a resolved label**. Fixed 2026-08-12 with `ON CONFLICT … DO UPDATE` +
`COALESCE(excluded.x, x)` on the three outcome columns — **preventive, not a repair**: measured
across all 295 events, exactly **one** re-write has ever occurred (`ADBE-1d-eqh_eql`, 31 minutes
after first detection on 2026-06-05, while still unresolved), so no resolved label is known to have
been destroyed. Four transferable rules. **A writer that owns only SOME columns of a table must not
use a whole-row replace** — ask what the other writers set (`backfill_outcomes` issues a direct
`UPDATE`, so it never collided and the damage was invisible from its side). **A defect masked by
ordering is still a defect** — `signal_runner.py:350` re-derives the label right after the scan, so
a loss surfaces only if the backfill throws, and that call sits in a "logged but never blocks the
cycle" try/except. **When you fix a preserve-on-NULL bug, test the OTHER direction too** — "never
update outcome" passes the obvious test and silently breaks every caller that legitimately sets
one. And the one that cost a retraction: **two writers on the same event with OPPOSITE conflict
policies give you a free audit** — `signals` is `INSERT OR IGNORE` (first write wins) while the
ledger was `INSERT OR REPLACE` (last write wins), so `signals.fired_at <> outcomes.fired_at` is a
*proof* that a re-write happened. Before that join, 13 ledger rows stamped 2026-08-11 were read as
re-detections; they were **first inserts in both tables** — a catch-up scan discovering historical
signals and resolving them in the same cycle. **Find the query that DISCRIMINATES before quoting a
count as evidence of a mechanism.**

**A DECLARED threshold is not the EFFECTIVE threshold when a second leg of the same gate is
stricter, and the stricter leg is usually the one nobody wrote down.** All four equity sleeves
(`xsmom`, `lowvol`, `xasset`, `pead`) declare `_GATE_SHARPE = 0.7` as their *"pre-registered
net-of-cost bar"* — and then AND it with `n_obs >= min_trl`, where `min_track_record_length` is
called with `target_sr` equal to an **annualized Sharpe of 1.0**. MinTRL against a non-zero target
asks *"can I confirm Sharpe ≥ 1?"*, so it returns **`inf`** for any sample at or below that target:
no amount of data confirms a hypothesis the sample contradicts. The real bar is therefore ~**2.174**
at n=500, ~**1.585** at n=2000, ~**1.478** at n=3000 — the whole 0.7–1.58 band clears every
threshold the code *names* and is rejected by one it does not. `_DEPLOY_SHARPE = 1.0` is inert for
the same reason: it is only consulted on a cell that already passed, and a passing cell is already
above 1.58. **No recorded verdict rested on this**: the gate is read on **four** committed cells
(`xsmom`'s residual grid, `lowvol`, `xasset`, `pead` — `forecast` computes `min_trl` and applies
no gate at all), and per the sleeve table above each of the four already fails on DSR, PBO,
`boot_lo`, or the 0.7 Sharpe leg, all of which bind before MinTRL. #165 extracted the gate and
documented the divergence; **#166 DROPPED the MinTRL leg** (user call), so `GATE_SHARPE = 0.7`
is now both the declared and the effective bar, and `DEPLOY_SHARPE` discriminates again.
**Re-targeting to `target_sr = 0` was considered and rejected as a NO-OP, not adopted** — and
that is the sharper half of the lesson. MinTRL round-trips with PSR, and `deflated_sharpe_ratio`
*is* PSR with the benchmark at the expected-max Sharpe, which is never negative; so `DSR ≥ 0.95`
**strictly implies** `min_trl(0) ≤ n_obs` and the leg could never fire. Measured: of 124,882
DSR-passing draws out of 300,000, **zero** would have been blocked. **A "fix" that turns a
mis-calibrated guard into an unfirable one is not a fix** — check whether a proposed threshold is
already implied by a leg you kept. The parent excludes `min_trl` for the same reason and calls
the four-leg form *"a documented recurring error"*. Three further transferable rules: **a gate is
identified by its full leg set, not by the constant with a name** — quoting `GATE_SHARPE` as "our
bar" was true of the constant and false of the gate; **when a threshold is a function of the data
(`min_trl` moves with `n_obs`), the bar is not a number and cannot be read off the source** — solve
for it, which took one bisection; and **the duplication is what let the divergence hide** — the
expression was inlined four times in production plus a fifth time inside
`tests/test_xsmom_residual_report.py`, which re-derived it to build its own expected value and so
passed against any implementation. Extraction to one definition is what made the leg set legible
at all. #165's extraction was proven verdict-neutral over 2,985,984 exhaustive combinations
(including NaN/±inf) plus 200k random draws, 0 mismatches; #166's removal is neutral by a
**monotonicity** argument instead — dropping a conjunct can only turn `False` into `True`, so the
only cells at risk are ones blocked *solely* by MinTRL, and
`TestRecordedVerdictsAreUnchanged` pins that none of the four recorded cells is one (each fails
on ≥2 surviving legs). **`min_trl` and `n_obs` are no longer PARAMETERS** of
`passes_sleeve_gate`, so re-adding the leg has to touch every call site — deliberate, not a
default that creeps back. Script (reproduces every number here by calling the production
functions rather than restating their arithmetic):
`docs/plans/scripts/sleeve_gate_mintrl_bar.py`.

**`signal_alert_outcomes.rr_ratio` is the DECLARED target; `tp_price` is the EFFECTIVE one — and the
ledger credited the wrong one.** `scanner.py` stored `rr_ratio = eff_alert_tp_r` (the configured
`tp_r`) while `_resolve_outcome_sl_tp` set `tp_price` to a detector's **structural** TP when it had
one; `_scan_forward` walked `tp_price` and credited `rr_ratio`, so an alert whose TP was 2.0R away
booked 5.0R. **34 of 298** rows diverged (up to **3.0R**): 8 resolved wins worth **+13.50R** of
phantom credit, and **4 still OPEN** — a live defect, not only a historical one.
**Enforced**: one shared `implied_tp_r` in `analytics/signal/outcome_backfill.py`. The resolver
credits the target it **walked**, the scanner records that same target at fire time, and
`analytics/exits/audit.py` imports the one definition instead of keeping the read-side copy that
found this (#165). `migrations/003_outcome_r_implied_tp.py` rewrote history — hand-run, dry-run by
default, and safe in place *because* neither column is in the row identity (contrast #142, where the
same fix needed a new `run_id`). **The pooled live avg_r is −0.1752R; any doc quoting −0.1247R
predates the fix.** **Rules**: a column recording what was *configured* is not a record of what
*happened* (#142/#154); and when a consumer holds both a stored number and the price it ACTS on, it
must derive from the price — crediting the stored one beside a walked level is how the two drift.
Audit: `docs/audits/2026-08-14-exit-policy-ab-v1.md` § "Two defects this port found".

**A BAR COUNT IS NOT A CALENDAR SPAN on an RTH tape — second instance of the crypto-constant rule.**
Upstream #437 fetched a trade's forward window as `max(candle_ts) + (max_hold + 2) * tf_ms`, exact on
a 24/7 tape and covering **0.0%** of real equity windows here: 30 `4h` bars span ~**132** `4h` units
of wall-clock (p95 150, max 161) and 14 `1d` bars span ~**20** (p95 22). The truncation is silent —
it marks would-be winners to market at the last fetched bar — and it **biases an A/B**, because a
short window cannot touch a policy whose time-stop fires at bar 3 but truncates the long-held
baseline. **Enforced**: fetch to `get_latest_open_time`, i.e. remove the trap by construction rather
than re-tune the literal; `TestForwardWindowSpansRthGaps` pins it with RTH-gapped fixture bars and
fails if the time-derived horizon returns. **Rule**: any expression converting bars→time or time→bars
must be checked against `4h` RTH = **2 bars/day**, and the tell is that the positive control against
production sits at 96–99% rather than 100% — high enough to read as rounding noise.

**A statistic can report a value it was DEFINED to report, and a cohort median is where that hides.**
`exits/`'s MFE for a loss comes from `fav[:-1]`, so **a loss resolved on its first held bar has
`mfe_r == 0.0` by construction, not by observation**. The 2026-06-20 audit read `mfe_p50 = 0.0` at
`bars_held_p50 = 1.0` as *"the median loss never traded green"* when the median row was incapable
of any other value; at n=264 the same cell reads +0.560 and **43.9%** of the 157 losses that could
show excursion reached ≥1R — the verdict was wrong in **direction**, and stood ~2 months.
**Rules**: check whether a metric's floor is reachable by every row in the cohort before quoting
its median; when a denominator-like quantity moves between runs the statistic may be measuring it
(`bars_held_p50` went 1 → 4, which is the entire story); and a subgroup chosen to remove one bias
usually introduces its own, so the honest claim is the bracket, not either endpoint.
Audit: `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md`.

**A SAMPLE-SIZE FLOOR IS NOT A POWER TEST, and a null result is a positive claim that needs its
own evidence.** `warning_audit` awarded `COSMETIC` — *"this warning carries no information"* — on
`n_supp >= min_n and n_kept >= min_n`. A floor says a test **ran**; it never says the test could
have **seen** anything, so it cannot separate "the effect is smaller than the bar" from "the CI
is five times the bar and we cannot tell". Measured here: **0 of 11** COSMETIC cells survived,
**0 of 12** had a CI inside ±0.05R, half-width median **4.1×** the bar, and **4 of 11** point
estimates *exceeded* the bar while printing "no gate-grade effect" (worst `w1_marubozu`/long at
**+0.289R**, CI [−0.007, +0.615]). **Enforced**: `audit_guard.CellVerdict.powered_null` — the CI
strictly inside ±`bar`, computed once and read by every consumer so the rule cannot drift;
defaults `False`, so an untested cell establishes nothing. **Rules**: a verdict meaning *ruled
out* must test CI **containment**, never n; `INSUFFICIENT` and a powered null are different
states and collapsing them prints the confident one; **only negative labels can move** under this
correction, which is why it cannot promote a cell; and **the printed legend was wrong in the same
direction as the code** (`COSMETIC = well-powered`), so the output corroborated the defect instead
of exposing it — check a tool's legend against its own predicate.
Audit: `docs/audits/2026-08-13-warning-value-audit.md` (§Correction). Ported from parent #617.

**An AVERAGE OF AVERAGES beside a SUM OF COUNTS is two denominators in one row, and the count is
the one that looks authoritative.** `get_backtest_win_rates` summed `closed_trades` across a cell's
symbols but took a plain `mean()` of `avg_r`, so a 1-trade symbol moved the star rating exactly as
far as a 50-trade one — while `min_trades` guarded the *pooled* count. Same shape as #150 (a
sample-size guard counting a different population than the statistic it tests) and #169 (a mean
published without its denominator). Fixed 2026-08-13: **38 of 160 `confidence_ratings` rows changed
stars, 21 crossed zero** (22 star moves on `signal_watch`, 16 on weekdays), coverage unchanged.
Ratings drive alert stars **and** which direction leg dispatches when both fire
(`analytics/signal/gates.py:150`), so the sign flips are operational. Four transferable rules.
**The tell is inside the row, not outside it** — `win_rate` was already `SUM(win_count)/SUM(trades)`,
so one row disagreed with itself about its denominator; when two aggregates sit side by side, check
they pool the same way before trusting either. **A "correct" idiom already in the file does not
propagate** — `query_strategy` / `query_tf` in `digest_lib.py` had used
`SUM(avg_r * closed_trades) / SUM(closed_trades)` all along while four sibling queries drifted to
`AVG(avg_r)`; the expression is now `digest_lib::_pooled`, defined once for all six, because
inlining is what let them diverge (the #165 lesson again). **Mask numerator and denominator
together** — a directional average is NULL exactly when that direction has no trades, and counting
those trades in the divisor alone invents them at R=0 (preventive here: 0 of 3,268 rows have
`n > 0 AND avg_r IS NULL`). And **a fix confined to a producer needs its consumers re-derived, not
re-run**: this could not move `backtest_runs` or the regression goldens because
`_build_confidence_ratings_map` returns `None` while `conflict_resolver` is off, so
`make db-update-recalibrate` alone was sufficient — verify the feedback path before assuming a full
`db-update`. No fixture in either test file had put two symbols in one cell, so the entire
cross-symbol path was untested; 17 tests added, each mutation-checked in both directions.

**Ingest level-parsing fails SILENTLY, and every instance so far was found by running the code, not
by reading it.** Full narratives in `context/tools.md`; the standing rules:

- A number that reads like a price level often is not one. `tools/x_route.py::first_level` strips
  three classes — month-anchored years (`MONTH_YEAR_RE`), percentage ranges (`PCT_RE`), and chart
  timeframes (`TIMEFRAME_RE`, e.g. a "4h descending trendline"). Two of those are single definitions
  shared with `tools/route_dedup.py` and `tools/pundit_score.py`; do not fork them.
- The sign-check (`check_level_order`) only judges a row whose legs are **both** present. A
  one-legged row is the *dominant* shape in practice (7 of 11 in the 2026-08-05 batch), and it is
  caught read-side only. Encode such a row as entry+target with the stop left unstated rather than
  inventing a level the pundit never gave.
- `tools/route_dedup.py` is **advisory except for `already_routed`** — it never drops a row, and
  `mark` runs strictly *after* the sink write (marking at check time is the #68
  watermark-on-send defect class).

**A cap that silently truncates looks identical to an absence.** `tools/video_marks.py::keep_items`
ranks by specificity desc then ts asc, so a tie at `ITEM_CAP` resolves in favour of *earlier*
material. Measured 2026-08-05: the cap bound on all four @fenggemeigu videos and dropped both GOOGL
items and the NVDA setup from a video that named them in its own title. Read a thin Stream C yield
from a dense video as a cap artifact before concluding the video made no calls.

## Code Style

- **Linter + Formatter**: ruff (replaces black; handles linting, import sorting, and formatting)
- **Type checker**: mypy (strict — `disallow_untyped_defs = true`)
- **All functions must have type annotations** including return types (`-> None` for test methods)
- **Markdown linter**: markdownlint-cli2
- Use `from typing import Any` for mock parameters in tests

## Testing

- Framework: pytest + unittest.mock
- Tests must not make real network calls — lib functions accept a `client` parameter; tests pass a `MagicMock` directly
- Analytics tests use `duckdb.connect(":memory:")` for full DB isolation — never touch the real `analytics.db`
- Run: `make test` (= `pytest tests/ -q --durations=10`, no coverage — nothing gates on it). `make test-cov` when you actually want a coverage report.
- **Regression tests**: `make test-regression` — compares backtest pipeline output to golden JSON files in `tests/fixtures/`; skips if fixture parquets are absent; run `make regression-update` to regenerate golden files after intentional changes
- **A "did NOT change" assertion is satisfied by two worlds — the invariant holding, and the perturbation never arriving — so every one needs a positive control, and the control must observe the channel the guard PROTECTS.** Two of the three sleeve causality guards were vacuous: they passed with the causal shift they guard deleted. Watch three traps. **A cap silently turns a perturbation test into a no-op** — xsmom's fixture perturbed `STRONG`, a ramp pinned at the +20 EWMAC cap, so the guarded input moved by *exactly 0.0*. **A control on a downstream value can fire through a different path** — upstream's `leverage[k+1]` control is FALSE here, since `ew_return_vol`'s own shift carries the bump to `k+1` either way (delta at `k+1` **2.4e+02** vs at `k` **0.0**), so wifey's control asserts the *demeaned forecast at `k`* moved. And **vacuity is per-shift and must be measured** — `test_governor_is_causal` already had teeth, so a missing control block is a smell, not the finding. `make check-orphan-tests` cannot see this class: these tests all call their subject. Audit: `docs/audits/2026-08-13-vacuous-causality-guards.md`.
- **A green suite does not mean a test exercises its subject.** Three mechanical guards exist because prose did not enforce these constraints:
  - `tests/test_schema_insert_arity.py` (in `make test`) ties every positional INSERT to its table's real column list — `INSERT … SELECT` is checked by **name order**, so a transposition of two same-typed columns fails. Adding a column to a table written positionally requires updating that statement in the same PR.
  - `make check-orphan-tests` (advisory, heuristic, **not** in `make test`) reports `Test*` classes that name a unit but never call it. Its `not-importable` verdict means the unit is a **closure** and no test can reach it — extraction is then a prerequisite for a fix, not scope creep (#150: five `TestEvGate` tests never invoked the EV gate, one reduced to `assert None is None`, and all five passed against any implementation).
  - `tests/test_outcome_backfill.py::TestMaxHoldCalibrationCoverage` (in `make test`) walks every `config/signal_watch*.toml` and fails if a declared timeframe has no `DEFAULT_MAX_HOLD_BARS` entry — the outcome resolver now **refuses** an unlisted timeframe (`counts["no_hold_cap"]`) instead of falling back to `max(...)`, which had silently handed `1wk` the `15m` value of 96 bars = **96 weeks**. The transferable rule is about *when* a guard can exist: this one was **unwritable** until `1wk` had a value, because `signal_watch_weekdays` scans `1wk` and the assertion would have been red with no correct way to green it. **A guard whose only fix is a calibration decision has to ship WITH that decision, not before it** — which is why the trap sat latent through #158 and closed only in #159.

## Dependencies

- Managed via Poetry: `poetry install --no-root`
- Runtime: `duckdb` (analytics DB), `pandas` (DataFrames), `pyarrow` (parquet fixture I/O), `exchange-calendars` (NYSE trading calendar — N3 PR2, isolated to `analytics/trading_calendar.py`)
- Dev deps: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs, pandas-stubs
- Never modify `poetry.lock` manually — use `poetry add` / `poetry remove`

## Documentation

When changes affect project structure, CLI commands, features, or behavior, update `README.md` to stay in sync.

## Where knowledge goes (the five surfaces)

**The rule that decides "always-loaded" vs "on-demand": if a session would not KNOW to go look
it up, it must be always-loaded. If it would, it belongs on demand.** Always-loaded content is
paid on every conversation whether relevant or not (~25k tokens standing as of 2026-08-13, see
[[project_context_budget]]), so that cost has to be earned by content whose *absence* causes
silent damage — a footgun you would violate without knowing to ask, a verdict you would
re-litigate without knowing it was settled.

| Surface | Loaded | Committed? | Holds |
| --- | --- | --- | --- |
| `CLAUDE.md` | **always** | yes — shared with anyone who clones | Rules binding on *any* session here regardless of task: commands, conventions, footguns, sleeve verdicts. No personal preferences. |
| `MEMORY.md` index | **always** | no (`~/.claude-personal/`) | A **routing table** — one line per memory, plus Current State. Enough to decide whether to open a file, never the content itself. |
| `memory/*.md` topics | on demand | no | The detail behind an index line: user preferences, feedback + the *why*, project history, references. |
| `.claude/context/*.md` | on demand | yes | Long-form module references, reached via the Project Structure pointers. |
| `docs/plans/next-conversation-prompt.md` | session start | no (gitignored, in-repo) | Live state: what is in flight, queued, or just decided. Pruned every run. |

Two corollaries worth stating, because both were violated before they were written down: a
**footgun keeps its rule inline and its narrative in the linked committed audit** (see Project
Structure above); and **the index is a router, not a store** — content that grows without bound
belongs in a topic file with a one-line pointer, which is why open questions and session logs
were split out.

## Session Memory Protocol

At the end of every session where anything changed (features, bug fixes, refactors, decisions), automatically update the **Current State** section in `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/MEMORY.md`. Do not wait to be asked.

Fields to keep current:

- Last session summary (one line: what changed)
- Open questions / pending decisions (or "none")

Keep the index small — it is read into context every session, so its cost is paid
on every conversation:

- **Current State holds at most 6 bullets.** Adding a 7th means first rolling the
  oldest, verbatim, into `memory/project_session_log_<month>.md`.
- **"Last session" is at most 2 lines; every other bullet is exactly 1 line.** Detail
  belongs in a topic file or the session log, never the index.
- Session logs have no size limit — that is what they are for. Prune by MOVING,
  never by deleting.
- **Open questions live in `memory/project_open_questions.md`**, not inline. They are
  live state, so they cannot be rolled into a dated session log — but left in the
  index they grow without bound (they reached 2,858 characters, the single largest
  item in the file, on 2026-08-03). The index carries a one-line pointer plus the
  count; the topic file carries the questions.

Why this is a hard rule and not a preference: the index is loaded on **every**
session whether or not anything is trimmed, so its size is a per-conversation tax.
Capping it makes each session's update O(1) — add one line, roll one out — instead
of re-reading and re-compressing a growing blob.

## Agent Skills

Skills live in `.claude/skills/<name>/SKILL.md` (project-specific, committed to repo) and are invoked with `/skill-name`. Each encapsulates a recurring workflow so you don't need to re-explain it. Use them proactively.

| Skill | Invoke | When to use | Cadence |
| ----- | ------ | ----------- | ------- |
| `sanity-check` | `/sanity-check` | Full project health check: git hygiene, docs sync, wiring audit, architecture review, skills freshness. §4a is a mechanical **fork-drift sweep** — validates every make target / timeframe / strategy / symbol / repo path named in the **current-state surfaces** (`.claude/`, `CLAUDE.md`, `README.md`, `docs/system-overview.md`) against the real registries. Dated trees (`docs/audits`, `docs/redesign`, `docs/superpowers`, `docs/plans`) are excluded on purpose — a past-tense claim there is correct by construction | Weekly or after any large refactor |
| `atr-sweep` | `/atr-sweep` | Find optimal ATR SL multiplier per strategy × TF; translates to `atr_sl_multiplier` TOML overrides | After any SL-related change or when backtests show high fee drag |
| `wfo-sweep` | `/wfo-sweep` | **Full automated WFO chain**: param-audit → param-sweep → apply → backtest → recalibrate → commit. One command to refresh all tp_r for a config. | When a config feels stale or after any major strategy/detector change |
| `config-refresh` | `/config-refresh` | Full TOML refresh: fix strategy_timeframes gaps, run TP sweep, update tp_r per strategy × TF, commit | When a signal_watch config feels stale, after detector rewrites, or when weekdays config drifts behind signal_watch.toml |
| `backtest-findings` | `/backtest-findings` | Interpret any sweep table (ATR/TP/volume/duration) and commit winners to TOML | After every sweep run |
| `param-sweep-apply` | `/param-sweep-apply` | Auto-apply WFO param-sweep/param-audit results: parse pasted tables, pick best tp_r per strategy × TF, edit TOML, run backtest + recalibrate | Paste results and invoke — use when running sweeps manually outside `/wfo-sweep` |
| `recalibrate` | `/recalibrate` | Update strategy star ratings in the `confidence_ratings` DB table from accumulated backtest runs (feeds Backtest UI stars, Telegram alerts, live signal-watch quality gate) | After any `make wifey-backtest SAVE=1` adds new runs |
| `volume-sweep` | `/volume-sweep` | Test `volume_suppress` per strategy; compare High Vol vs Low Vol avg R | When adding a new strategy; after entry logic changes that affect signal frequency |
| `new-strategy` | `/new-strategy` | Guided 4-file checklist for adding a new strategy (`analytics/strategies/<name>.py`, `_registry.py`, `signals/registry.py`, tests) | Every time a new strategy is added |
| `backtest-run` | `/backtest-run` | Quick reference for all `wifey backtest` invocations and flags | Any time you need a backtest command and can't remember the flags |
| `investigate-strategy` | `/investigate-strategy` | Debug why a strategy did/didn't fire on a specific candle using `wifey signal test` | When asked to investigate, diagnose, or replay a signal |
| `signal-watch` | `/signal-watch` | Signal daemon workflow, TOML config reference, signal flow diagram | When configuring or debugging the live signal scanner |
| `pr-summary` | `/pr-summary` | Write PR title + summary + test plan to `/tmp/pr-<branch>.md` | After finishing any feature branch |
| `post-branch` | `/post-branch` | Behaviour-gated docs sweep: diff branch changes against CLAUDE.md / README.md / MEMORY.md / Makefile / docker-compose.yml / `.claude/context/`, propose targeted edits, append "Documentation updates" to PR body. Three steps run **always**, regardless of the behaviour gate: **5b** reconciles the SoT (`project_todo_master.md` — nothing else updates it, and a stale row reads as current evidence), **5c** audits every quantitative claim the branch's own prose asserts, naming the query that reproduces each, and **5d** runs `make docs-index-check` before the commit that opens the PR (a stale generated `INDEX.md` reddens the suite, and catching it at 10a would cost a second CI matrix). Skips the doc walk for pure refactors. | **Before** `gh pr create`, while the branch is still local-only (saves a duplicate CI matrix) |
| `stats-dashboard` | `/stats-dashboard` | Stats page architecture, card inventory, adding new cards, timezone constraints | When working on Stats page or `stats_lib.py` |
| `db-update` | `/db-update` | Routine `make db-update`: backtest (**2** configs — `signal_watch` + `signal_watch_weekdays`) → recalibrate → regression golden refresh → surface check (declared-but-dead **and** rated-but-undeclared; gates the completion banner) | After any detector / strategy / config change that affects ratings or fixtures |
| `data-backfill` | `/data-backfill` | OHLCV ingestion via `wifey analytics backfill` / `sync` | First-time setup, wiped DB, new symbol or timeframe, filling a data gap |
| `confluence-backtest` | `/confluence-backtest` | Cross-TF (`--cross-tf`) and same-TF (`--combo`) co-firing backtests; HTF/LTF pair sweeps; post-run spot-check via `tools/combo_health.py` | After adding a strategy, changing entry logic, tuning the live `[combo]` gate, or to confirm combo tables are healthy after a refresh |
| `frontend-svelte` | `/frontend-svelte` | Svelte 5 + Vite UI workflow for `web/ui/` — pages, stores, lightweight-charts, dev/build commands | Any work under `web/ui/`; pair with `/frontend-design` for visual work |
| `journal-trade` | `/journal-trade` | Capture a manual trade into the gitignored `docs/plans/journal/` (structured frontmatter + Thesis/Plan/Execution/Outcome/Retrospective narrative); MYT→UTC timestamps; ground-truth feeder for a future AI trade-card / live outcome loop / trade-management heuristics | When the user says "journal my trade", pastes trade-execution details, or a logged trade closes |
| `sync-parent` | `/sync-parent` | Detect-and-recommend parent-repo PRs to port into wifey; writes a classified report to `docs/plans/parent-sync/parent-sync-<date>.md` (in-repo + gitignored — a `/tmp` clear once destroyed a 67-PR triage) | Periodic catch-up with the parent, or after a known upstream refactor |
| `ingest-x` | `/ingest-x` | Ingest one or more X/Twitter post URLs into the research pipeline: syndication fetch (no auth/scraping, `tools/x_fetch.py` — randomized cooldown + dedup cache) → per-post vision-extract in a **sonnet** subagent carrying a self-contained inline rubric → classify (content-type gate + the 4-bucket verdict taxonomy, `tools/x_route.py`) → routing-dedup `check` (`tools/route_dedup.py`; `already_routed` blocks, near-duplicate candidates are advisory) → one consolidated review digest → route into Stream A `thesis-inbox.md` / B `mechanics-backlog.md` / C `pundit-calls.jsonl` (all gitignored under `docs/plans/`) → `mark` each append. Text + still images + quoted-tweet + upward self-thread recovery (`--thread`; bookmark the LAST post — the endpoint has no children field); video handed off to `/ingest-video` | When the user says "/ingest-x", pastes one or more x.com/twitter.com status URLs, or "ingest this/these X post(s)" |
| `ingest-feed` | `/ingest-feed` | Poll the gitignored YouTube follow list (`config/youtube_channels.toml`) for new uploads via `tools/yt_feed.py` — read-only Data API v3 `poll` (~2–3 units/channel/day, never `search.list`) + deep `backfill` + `resolve` handle→TOML + `hint` (pure local read, feeds `/ingest-video` the channel's `intro_recap_s` / `item_cap`) — then hands the picked videos to `/ingest-video`. **`mark` is the ONLY state writer and runs strictly AFTER the review gate** (the #68 watermark-on-send class). Ported from parent #515 at parent HEAD, so #516/#529/#535/#558/#582/#585 land with it | When the user says "/ingest-feed", "what's new on youtube", "poll the channels", or "backfill <channel>" |
| `ingest-video` | `/ingest-video` | Ingest one or more YouTube/X video URLs (batched) into the research pipeline: fetch metadata + transcript (`tools/video_fetch.py` — yt-dlp captions, Groq `whisper-large-v3` fallback, dedup cache + cooldown) → **sibling-repo check** (grep this repo's *and* the crypto parent's note frontmatter for `video_id`, never the filename; route by **subject** — equities/macro/gold/oil/DXY/bonds here, crypto → parent, both-subject video legitimately yields rows in both) → pass-1 **sonnet** subagent (text-only) segments + ranks candidate items → deterministic call-time resolution (`tools/video_calltime.py`, never LLM date arithmetic) → transcript-driven frame selection (`tools/video_marks.py`) + extraction (≤15 frames) → pass-2 **sonnet** subagent (vision) chart-corrects and emits item JSON → routing-dedup `check` per item + `pairs` per video (`tools/route_dedup.py`) → one digest → one approval → route via the shared `tools/x_route.py` taxonomy into Stream A/B/C plus a per-video note (`docs/plans/video-notes/`) → `mark` each append | When the user says "/ingest-video", pastes a YouTube/X video URL, or "ingest this video" |

**Always load `/frontend-design` before any Svelte/CSS/UI changes.**

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- **Always branch off the LATEST `main` — never off another feature branch, and never
  keep working on `main` itself.** Before the first edit of a task:
  `git fetch origin && git switch main && git merge --ff-only origin/main`, then
  `git switch -c <type>/<slug>`. If work has already started on `main`, `git switch -c`
  carries the uncommitted changes over — do that as soon as it is noticed rather than
  committing to `main`. Verify with `git status -b` (should read
  `## <branch>...origin/main` or no upstream yet), not from memory of what branch you
  were on. Squash-merges mean a branch cut from a stale `main` replays work that is
  already in, and a branch cut from another feature branch inherits its whole diff into
  the PR — both surface as review noise rather than as an error.
- Do not commit `.env`, `config/stocks.json`, or IDE-specific files
- **Per-repo git identity is mandatory** before any commit: this account uses `s10023 <ngkhaijian@gmail.com>` (global config inherits a work identity and will mis-attribute commits). Verify via `git config --local user.email` before committing. SSH alias `git@github.com-personal:...` is also required for s10023 remotes — see auto-memory `reference_ssh_host_aliases.md` for the full recipe.
- **Invoke `/post-branch` BEFORE `gh pr create`, while the branch is still local-only** — then fold its "Documentation updates" section into the *initial* `--body`. Its own Step 1 behaviour gate decides whether a docs sweep is warranted, so this is cheap even on a pure refactor. **Steps 5b (SoT reconcile) and 5c (claims audit) run regardless of that gate** and neither writes to the repo, so both are free of CI. Step 10c (re-verify PR state) still runs last, after the PR exists. **Why before, not after** (user decision, 2026-08-06): these are private repos on the free tier and Actions minutes are a hard budget, so a doc-sync commit pushed to an already-open PR re-runs the whole 5-check matrix (`markdownlint`, `Trivy`, `lint-typecheck-test`, `frontend-check`, `Regression tests`) for what is usually a two-file docs edit — one CI run instead of two, with identical review signal. Prose in the Agent Skills table demonstrably is not enough: the skill fired zero times across PRs #123–#125 here, each time on the main thread, including the session that was repairing it. A local `PreToolUse` hook on `Bash` greps for `gh pr create` and emits an advisory reminder (never blocking) — it must be `PreToolUse`, since a `PostToolUse` hook cannot fire before the PR exists and so cannot enforce this ordering at all. **It is not in git** — `.gitignore` excludes `.claude/*`, so `.claude/settings.json` is machine-local; **re-add it after a reclone**.
- **CI-quota workaround: flip the repo PUBLIC before opening a PR, back to PRIVATE after it
  merges** (user decision, 2026-08-11 — this **reverses** the earlier "do not flip public"
  rule). Public repos get unlimited free standard-runner Actions minutes, which is the only
  way to get real CI here: every check had died at the runner in 2–4 seconds without executing
  a step since #144. Verified on #153 — all five checks went green on the first re-run after
  the flip.

  ```bash
  GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-wifey-wall-street-bot \
    --visibility public --accept-visibility-change-consequences
  # ...open PR, let CI run, merge...
  GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-wifey-wall-street-bot \
    --visibility private --accept-visibility-change-consequences
  ```

  **A DOCS-ONLY PR does not need the flip** (user decision, 2026-08-13). Three of the five
  checks sit behind `dorny/paths-filter` on `**/*.py` / `web/ui/**`, so on a `.md`-only diff
  `lint-typecheck-test`, `Regression tests` and `frontend-check` execute **zero steps** —
  measured at 4–7s on #173 and #174. `make lint-md` reproduces CI's `markdownlint`
  **exactly**, so the only check you actually forgo is **Trivy's secret scan**, which has no
  path filter and gates on purpose ("a committed secret is a property of THIS diff"). Read any
  *externally pasted* content in a doc before committing it — this repo had a real PII leak,
  fixed in #145.

  **CI's markdownlint glob is NOT wider than local's, despite the workflow appearing to say
  so** (re-derived 2026-08-13, correcting a standing claim). The job passes
  `globs: **/*.md !venv`, but markdownlint-cli2 still applies the `!` negations in
  `.markdownlint-cli2.jsonc`. The falsifier is arithmetic, not the YAML: **99** tracked `.md`
  files, CI reports **`Linting: 98 files`**, and the one omitted is
  `.github/pull_request_template.md` — which carries a live **MD041** error, so had CI linted
  it the check would have failed rather than passed. Local's 99 is the same 98 plus untracked
  `.pytest_cache/README.md`, i.e. local is a **superset**. Do not "fix" a divergence here
  without re-running that count.

  **Know what the window costs, because flipping back does not undo it.** wifey is not a
  GitHub fork — its history was *copied* — so **389 of 541 commits** are the still-private
  parent's pre-fork research and they are published for the duration. Anything cloned or
  indexed in that window stays out, and any fork created while public is split into its own
  network and **survives the flip back**. This is an IP/history exposure, not a secrets one:
  all 4,519 blobs scanned clean and the one real PII leak was fixed in #145.
  **Flip back promptly after the merge, and check `forks_count` is still 0 before you do.**
- **`gh` commands in this repo must pass `--repo s10023/buibui-wifey-wall-street-bot` explicitly.** The user's `gh` default repo is intentionally set to the parent `s10023/buibui-moon-trader-bot` (primary project), so `gh pr view N` / `gh pr list` / `gh pr create` without `--repo` will resolve against the parent and either fail or target the wrong repo. This is a preference, not a fix-to-be-found — do not run `gh repo set-default` to "solve" it.
