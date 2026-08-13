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

For routine DB refresh after backtest/strategy changes: `make db-update` (= `db-update-backtest` → `db-update-recalibrate` → `regression-update` → `check-dead-surfaces`). The last step reports `(strategy × timeframe)` cells where declaration and output disagree in **either** direction — declared-but-dead (detector never fires, so a dead surface can't hide behind rows that merely exist) and rated-but-undeclared (a `confidence_ratings` row outliving the config that produced it). It never blocks the refresh, but the completion banner is **conditional on it**: a failure prints a warning instead of `✅`. Run `make check-dead-surfaces` alone for a non-zero exit.

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

| Package | What it is | Deep reference |
| --- | --- | --- |
| `wifey.py` · `cli/` | Thin CLI entry shim delegating to `cli.main:main`; argparse subcommand package (`signal` / `analytics` / `backtest` / `digest` / `param` / `recalibrate` / `web`) with `_common.py` helpers | — |
| `analytics/` | Analytics data layer (DuckDB): `store/`, `strategies/` (18 detector modules, **16** registered for dispatch), `backtest/`, `signal/`, `stats/`, `research_guards/`, `sweep_guard.py`, `audit_guard.py`, `db_retry.py`, plus the data-ingest + quality + calendar layer | `context/analytics.md` |
| `analytics/{forecast,xsmom,lowvol,xasset,pead,exits}/` | The P2/P3 research sleeves and the exit diagnostic — **verdicts below** | `context/analytics.md` |
| `signals/` · `utils/` | Alerting + dedup daemon (detection lives in `analytics/`); shared Telegram / yfinance / EDGAR clients and the two config-universe loaders | `context/signals.md` |
| `web/` | FastAPI backend + Svelte 5 / Vite UI | `context/web.md` |
| `tools/` | One-shot analysis, audit, and research-ingest scripts; not part of the daemon or CLI surface | `context/tools.md` |
| `trade/` | **Empty placeholder — both files are 0 bytes.** The parent's Binance Futures opener was dropped at fork time; nothing replaced it. `make wifey-open-trades` *ran* the empty file and exited 0 behind a success banner until 2026-08-06, and now fails loudly. Phase B (equities broker, TBD) is where an order layer would land | — |
| `tests/` | pytest suite; tests import from lib modules and pass mock dependencies directly | — |
| `migrations/` | One-shot DB migration scripts, run by hand. Only `001_day_filter_text.py` (fork-era, 2026-05-14); routine schema changes go through `analytics/store/schema.py`'s migration list instead | — |
| `config/` | `stocks.json` (gitignored 13-symbol live watchlist), `universe.json` (committed 505-member research breadth universe), `strategy_params.toml` (shared base inherited via `extends`), `youtube_channels.toml` (gitignored `/ingest-feed` follow list; `.example` committed) | `context/config.md` |
| `deploy/` | `backup-analytics.sh` (verified local snapshot of `analytics.db` + the whole gitignored `docs/plans/` tree), `notify-failure.sh`, and **opt-in** `wifey-*` systemd user units. Nothing installs them; there is still no wifey daemon | `deploy/README.md` |

### Sleeve verdicts — do NOT rebuild a shelved sleeve

Six sleeves have been built and measured on equities. **Every one is non-positive.** The free-data
edge-hunt arc is **CONCLUDED** (synthesis `docs/audits/2026-06-24-honest-exit-free-data-edge-arc.md`);
do not start a new free-data hunt without an explicit user go.

| Sleeve | Verdict |
| --- | --- |
| `forecast/` EWMAC trend (P2, PR #91) | **G2 = FAIL** — portfolio Sharpe −0.05, negative even *pre-cost*, so a signal failure not a cost failure |
| `xsmom/` cross-sectional momentum (P3, PR #92) | **G3 = FAIL** — combined Sharpe −0.156 @2bps, negative at 0bps; `corr_to_trend` +0.62, so not a diversification win either |
| `xsmom/residual.py` residualised XS (experiment #1, PR #98) | **FAIL** — committed `broad_residual_skip` cell Sharpe +0.15 @2bps, DSR 0.44, boot_lo<0. The long-only leg's +0.88 is survivorship/beta-confounded and is **not** the gated cell |
| `lowvol/` low-beta / BAB (edge-hunt #2, PR #100) | **FAIL** — committed cell Sharpe −0.069 @2bps, DSR ~0.03. The realized-beta guardrail **FIRED** (β +3.9, not ≈0) → a fail of *this construction's neutrality*, not a clean BAB-premium test |
| `xasset/` cross-asset TSMOM (edge-hunt #3, PR #102) | **FAIL (clean)** — `broad_ls` +0.41 cost-free / +0.36 @2bps, never ≥0.7; PBO ~0.79. The equity-β guardrail **held** (β −0.083), so the construction diversified as designed and the premium is simply too weak in free-ETF proxies |
| `pead/` PEAD-lite (edge-hunt #4, PR #104) | **FAIL** — `broad_ls` +0.10 @2bps, DSR 0.20. The β guardrail **FIRED** (β ≈ +113, governor saturation on sparse daily cohorts); the controlled mega arm (β −0.40) showed *negative* drift (−0.53) |
| `exits/` MFE-MAE diagnostic (PR #96) | **EXIT-FIXABLE at the cohort level** (re-run 2026-08-12, n=264, 264/264 scored). Supersedes the n=22 INCONCLUSIVE call **and reverses its direction**: of the 157 losses that could show excursion, **43.9%** reached ≥1R before stopping (CI 36.4–51.8%), vs the 13.3% that produced the earlier "entry-broken" read. Still blocked per-edge (0 of 30 loss cells reach n=30) and the whole ledger is **pre-#151**. Audit: `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md` |

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
`[bias] adr_suppress_threshold` keeps quiet small-range bars; `volume_suppress` keeps ≥1.5×-volume
bars; range and volume correlate at **+0.65**, so declaring both without `adr_exempt = true` discards
~99% of a strategy's signals. Until 2026-08-06 this ran `doji × 1d` at **0** signals against 1,247
raw detector fires, `orb × 4h` at n=1, and **inverted the measured sign** on `engulfing × 1d` and
`bos × 1d`. `load_signal_config::voided_volume_gates` now refuses the pairing (sibling of #139's
`dead_timeframes`). Two transferable rules: a per-strategy flag whose correctness depends on a
second flag must live in the **shared base**, not one day-filter config (`bos`'s `adr_exempt` was
config-local, so weekdays never inherited it); and **a test that asserts a config value PARSED can
never detect that the parsed value produces nothing** — the pre-existing
`test_signal_watch_toml_volume_suppress_flags` asserted `doji`'s flag was `True` and passed for
months while the cell was empty. Audit: `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

**A gate can be undefined on a timeframe and still look like it is working.** The ADR gate's premise
— `consumed_ratio = (cumulative intraday range UP TO this candle) / 14-day ADR` — needs **>1 bar per
calendar day**. `1d` and `1wk` have exactly one, so the "cumulative" range is the whole day's range
and the ratio silently stops measuring exhaustion, becoming a *high-range-day* filter instead (it is
not pinned at 1.0 — it stays dispersed, p25 0.71 / p75 1.21, which is why it reads as functional).
Its direction guard degenerates identically: `move_up` reduces to "closed in the upper half of its
own bar", the same quantity `doji` / `ema` / `trend_day` derive direction from, so `chasing` is true
**by construction** and drops **100%** of their above-threshold signals (`bos` 48% and `eqh_eql` 76%
are the controls — their direction comes from prior structure). Cost: 28–82% of every strategy's
signals on every timeframe either config scans, to buy an effect that survives Benjamini–Hochberg in
**1 of 17 cells**. Fixed 2026-08-06 by `adr_gate_applies(timeframe)` — intraday only, unknown
timeframes fall closed, and `timeframe` is a **required** arg so mypy forces every call site to state
it. Three transferable rules: the live path (`analytics/stats/adr.py`, `1h`-derived, **not**
degenerate) and the backtest path were never running the same gate, so *parity by shared function
name is not parity*; `4h` on US-equity RTH is **2** bars/day, not the 6 the crypto-era 0.80 threshold
assumes, so **re-derive a crypto-inherited constant against equity bar counts before trusting it**;
and the parity test passed for months because its fixture built *"a single 24h day"* — **a green test
proves the code matches the fixture, never that the fixture matches production**. `check-dead-surfaces`
cannot see this class: it finds exact zeros, and a 77–82% haircut leaves the cell non-empty.
Audit: `docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md`.

**A gate that fails open has no loud failure mode, and a config value that is RECORDED is more
dangerous than one that is ignored.** `[backtest] days = 365` reached neither the OHLCV cache nor
`run_scan_cycle` — the daemon took the **90**-day signature default — while `scanner.py:1232` wrote
**365** into `backtest_runs.days`, so the audit trail actively corroborated the wrong window.
`passes_ev_gate` (now in `analytics/signal/gates.py`) returns `True` below `min_trades`, so the
narrow window never failed; it silently made the hard gate a no-op on **71%** of direction-legs
(`signal_watch`; 62% on weekdays), and on
both configs the median cell sat *below its own `min_trades`*. Four of the five `bos × 1d` alerts
dispatched 2026-08-05 sit at avg_r ≈ **−1.0** over 365 days and were never evaluated at all. Two
transferable rules: cross-check a recorded parameter against a recorded **observable** —
`data_end_ms - data_start_ms` found this in one query (94 live rows declaring 365 over exactly
**90.0** days, against 3,117 sweep rows declaring 365 over 365.0); and when a value reaches its
consumer **through a cache, fixing the consumer's argument fixes nothing** — `run_scan_cycle`
prefers a populated `ohlcv_cache` over its own read, so `days=365` alone would have widened the
label and the cache key while the DataFrame stayed 90 days. Find who populates the cache first.
Fixed 2026-08-06 by a single `bt_days` feeding both surfaces. Audit:
`docs/audits/2026-08-06-live-ev-gate-window.md`.

**A row key derived from PARAMETERS does not identify a MEASUREMENT, and `INSERT OR REPLACE` turns
that into silent data loss.** `backtest_runs` has **four** writers; `_backtest_run_id` hashed only
the param tuple, and the live EV gate (`scanner.py:1229`) passes the config's real `days` **and**
`day_filter` — so for any cell the daemon evaluated its run_id was **identical** to the sweep's and
the daemon's single-strategy row *replaced* the competed sweep row in place, flipping `sweep_id` to
NULL. The write's own comment says it accumulates "passively"; it did not accumulate, it replaced.
The runner produced **312** rows for `signal_watch` (24 cells × 13 symbols, 0 skipped) and the table
held **263** — 17 of 24 cells short, `trend_day × 4h` rated on **3 of 13** symbols. This also
undercut #146: `sweep_id IS NOT NULL` cannot recover an overwritten row, it only **drops** it, and
`weekdays` looked healthy (468 = 36 × 13 exactly) purely because it never runs live. Fixed
2026-08-07 by an `origin` discriminator naming the *writer*; `"sweep"` adds no suffix so every
historical run_id is unchanged, and `origin` is a **required** kwarg on `upsert_backtest_run` so
mypy forces each call site to declare itself (same enforcement as `adr_gate_applies`). Three
transferable rules: when a table has more than one writer the writer belongs in the **key**, since a
provenance *column* cannot help if the loser is deleted before any query runs; **check a producer's
output count against what the consumer stored** — every one of those 263 rows held correct values,
only the count was wrong; and `backtest_cache` / `backtest_cross_tf_combos` were checked and have
one writer each, so verify the blast radius rather than assuming it. Audit:
`docs/audits/2026-08-07-backtest-runs-writer-collision.md`.

**`conflict_resolver` is the ONE live-parity gate the sweep must never run, and that is
load-bearing, not an oversight.** Until 2026-08-07 the sweep behind the star ratings ran with
**every** gate off — T6 (2026-05-26) shipped `LiveParityConfig` default-off to keep goldens
byte-identical during the port, no config declared `[backtest.live_parity]`, and
`make wifey-backtest` passes no `--live-parity` — so `confidence_ratings` measured the **raw**
population while the daemon gates dispatch with all six, and the committed `tp_r` values were
calibrated under *ad-hoc* `--live-parity` against a population the routine sweep never produced.
Five gates are now **on** in `config/strategy_params.toml` (the **shared base**, deliberately not a
Makefile flag — a flag is how the two surfaces diverged unnoticed for ~2.5 months). But
`conflict_resolver` **reads `confidence_ratings`**, so switching it on inside the sweep that
*produces* them closes a loop: sweep resolves ties → drops a side → `backtest_runs` changes →
recalibrate writes different ratings. Measured over three consecutive full `backtest + recalibrate`
passes with all six on: **108 → 66** rows differing (18 → 8 star changes) — damping but **not
converged**, with cells still oscillating at iteration 3 (`pin_bar × 1d` long 2★ +0.10 → **4★
+0.76** while `pin_bar × 1wk` long went 4★ → 2★). **`make db-update` must be deterministic or a
real rating change is indistinguishable from a re-run artifact.** With five gates it is: two
consecutive passes differ on **0 of 160** rows, closed trades fall **−33.4%** / **−34.8%** (~4/5 of
the full effect), **no cell loses its rating**, and 52 of 160 rows change stars. Two transferable
rules: **a gate that reads a table its own pipeline writes is not a gate, it is a fixed-point
iteration** — check data-flow direction before enabling one in the producer of its own input; and
**"it converges" needs three points, not two** (1→2 alone looks like transient re-seeding; only
2→3 shows the decay rate, and aggregate decay hides per-cell oscillation). The live path is
unaffected — it reads ratings already written and never feeds its own output back in-cycle. Audit:
`docs/audits/2026-08-07-live-parity-ratings-sweep.md`. Scripts:
`docs/plans/scripts/live_parity_sweep_diff.py`, `live_parity_rating_fallout.py`.

**A sample-size guard that counts a different population than the one it tests is not a weak guard,
it is not a guard.** The live EV gate compared the **combined** closed-trade count against
`min_trades` (`scanner.py:752`) and then tested a **directional** `avg_r` (`:757`), so a long
verdict could rest entirely on short trades. Measured over the declared 365d window: **53 of 260**
blocked legs on `signal_watch` (20%) and **45 of 429** on weekdays (10%) had fewer trades in the
tested direction than the config demands, and **19** / **68** rested on a *single* one, where
dispersion is undefined (`doji × 1d` GOOGL short: n_cmb=3, n_dir=1, avg_r −1.011). **No `min_trades`
value fixes this** — raising it to 10 still admits an n_dir=1 block whenever the opposite direction
carries the count. The cost is not alert volume: a blocked leg is dropped from `passing_events` at
`scanner.py:777` while the outcome writer runs downstream at `:1048`, so it never reaches
`signal_alert_outcomes` — under option (d) ("bank correctness, let the ledgers mature") the gate was
**deleting observations** using non-evidence. `README.md` documented the directional semantics all
along and the ladder was *"calibrated from DB p25 directional counts"* — **the doc was right and the
code was wrong**, so the fix restores the calibrated behaviour rather than choosing a new one.
Fixed 2026-08-07 by counting `long_closed_trades` / `short_closed_trades`; strictly permissive, zero
cells newly blocked. Three transferable rules: **a gate that fails open inverts the meaning of
"stricter"** — at `min_trades = 20` the `1wk` gate reaches **100%** bypass, i.e. the safest-sounding
change switches it *off*; **a test that re-implements the code under test can never falsify it** —
`_passes_ev_gate` was a closure inside `run_scan_cycle` so no test could call it, every `TestEvGate`
test copied the comparison inline, and `test_insufficient_trades_passes` asserted
`len(result.closed_trades) < effective_min_trades()`, writing the defect down as the expectation
(all five passed against any implementation, so extraction is a *prerequisite* for the fix, not
scope creep); and **suppression upstream of the recorder destroys evidence, not just output** —
check where a filter sits relative to the persistence call before judging its cost. Audit:
`docs/audits/2026-08-07-ev-gate-directional-sample-guard.md`. Script:
`docs/plans/scripts/ev_gate_min_trades_diff.py`.

**A threshold applied to a POINT ESTIMATE is a coin flip with extra steps.** `min_avg_r = 0.0`
made the live EV gate suppress any negative directional `avg_r` regardless of dispersion: measured
with the real per-cell, per-direction sd, **84 of 207** blocked `signal_watch` legs (41%) and
**141 of 384** on weekdays sat at **|t| < 1** — indistinguishable from zero. A cell at −0.006R was
blocked exactly as hard as one at −1.01R, and since a blocked leg never reaches
`signal_alert_outcomes`, each was a destroyed ledger row. Fixed 2026-08-07 by `min_avg_r_z`
(default **1.64**, one-sided 95%): the shortfall must be that many standard errors below the
threshold. `min_avg_r` itself is unchanged, which is why this is *correctness* and not the frozen
threshold-selection — it changes the decision RULE, not the line. Blocks **207 → 101** and
**384 → 158**, **zero** newly blocked. Three transferable rules: **multiplicity correction belongs
where SELECTION happens** — BH is right for the sweep (#142 ran 17 cells, 1 survived; #143 ran 5,
2 did) and wrong for a per-leg operational gate, since Bonferroni over ~300 cells gives z ≈ 3.5, at
which a **fail-open** gate blocks almost nothing and the "correction" silently disables the thing
it was meant to sharpen (an earlier draft of this very fix recommended BH; it was withdrawn before
implementation); **measure the cheap alternative against a bar you wrote down FIRST** — recovering
sd from win rate under a two-point payoff looked plausible and failed at median **10.6%** error,
p90 **100%**, flipping **28.5%** of gate verdicts, which is why `backtest_cache` took the schema
change instead; and **when a cached type shadows a computed one, a new statistic must be added to
BOTH** — `BacktestSnapshot` is the *normal* steady-state path (cache hit on the same closed
candle), so omitting it would have degraded the gate on the hot path with no error, leaving
suppression dependent on cache state. Audit:
`docs/audits/2026-08-07-ev-gate-significance-test.md`. Scripts:
`docs/plans/scripts/ev_gate_significance_impact.py`, `sd_approx_check.py`.

**A provenance column that records what was DECLARED is not provenance, and when that column is
part of the row-identity hash, fixing the writer without migrating history MANUFACTURES evidence.**
`backtest_runs.adr_suppress_threshold` stored `cfg.adr_suppress_threshold` flat at both writers
that set it, so **2,091 of 3,246 rows (64.4%)** claimed a gate that never touched them. The
proportions invert the obvious framing: **1,974 were purely the timeframe** (`adr_gate_applies` is
intraday-only since #142), only **117** were `adr_exempt` (`bos`, `eqh_eql`). Two of the four
writers (`web`, `single_run`) were already correct — neither wires `bias_cfg` into `run_backtest`,
so no gate can run there, and their NULL was truth rather than omission. Fixed 2026-08-11 by
`effective_adr_threshold(declared, timeframe, adr_exempt=…)` as a **required** kwarg on
`upsert_backtest_run`. Four transferable rules. **`mypy` cannot enforce a required kwarg through a
`**dict` splat** — the explicit call site failed type-check immediately while **12** sites passing
`**_BT_PARAMS` type-checked clean and blew up at runtime, so a green mypy is not evidence that a
required-argument change is covered; run the suite. **A column in the identity hash cannot be
corrected in place without a migration** — flipping a value changes `run_id`, so
`INSERT OR REPLACE` writes a *new* row and leaves the old one, and the two then satisfy
`digest QUERY=adr_ab`'s join as a fake gated-vs-ungated pair: **661** of them, comparing a June
measurement against an August one. The fix needs a matching-window clause
(`data_start_ms`/`data_end_ms`); `days` cannot stand in, since **every row in the table declares
365** while the oldest were measured over 90. **A migration must respect CODE ERAS** — before #142
`_filter_signals_by_adr` had no timeframe guard, so on `1d`/`1wk` the gate really did run
(degenerately), and #141 had not yet moved `adr_exempt` into the shared base; reconstructing
"executed" for those rows from today's config would replace one false claim with another, so only
the **518** post-#142 rows were rewritten. **Check whether the consumer already assumed the correct
semantics** — `recalibrate_lib` matched `= threshold OR IS NULL` with a comment about "exempt-strategy
runs (NULL)", i.e. the reader was written against semantics the writer never implemented, which is
why the migration is provably rating-neutral (all 6 rating surfaces byte-identical). `adr_ab` itself
was **never alive**: it had zero NULL rows for its entire history, and post-fix its join still cannot
match within a config, because the executed threshold is a pure function of `(strategy, timeframe)`.
Audit: `migrations/002_adr_threshold_executed.py` docstring.

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

**A statistic can report a value it was DEFINED to report, and a cohort median is where that hides.**
`exits/`'s MFE for a loss comes from `fav[:-1]` — every held bar except the exit bar, the deliberate
adverse-first anti-bias rule — so **a loss resolved on its first held bar has `mfe_r == 0.0` by
construction, not by observation**. The 2026-06-20 audit measured `mfe_p50 = 0.0` at
`bars_held_p50 = 1.0` and published *"the median loss never traded green before stopping"* — the
entry-broken verdict — when the median row was structurally incapable of any other value. At n=264
the same cell reads +0.560, and conditioning on the 157 losses that *could* show excursion gives
**43.9% reaching ≥1R** against the 13.3% originally reported: the verdict was wrong in **direction**,
not magnitude, and stood for ~2 months. Three transferable rules: **check whether a metric's floor
or ceiling is reachable by every row in the cohort before quoting its median** — a conservative
convention is anti-bias for the row and can still be a systematic bias for the aggregate; **when a
denominator-like quantity moves between runs, the statistic may be measuring it and not the effect**
(`bars_held_p50` went 1 → 4 across the two runs, which is the entire story); and **a subgroup chosen
to remove one bias usually introduces its own** — surviving bar 1 is selection on favorable movement,
so ≥2-bar rows are biased *up* exactly as the pooled row is biased *down*, and the honest claim is
the bracket, not either endpoint. Audit: `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md`.
Scripts: `docs/plans/scripts/exit_audit_cohort_depth.py`, `exit_audit_gate_era_split.py`.

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
| `sanity-check` | `/sanity-check` | Full project health check: git hygiene, docs sync, wiring audit, architecture review, skills freshness. §4a is a mechanical **fork-drift sweep** — validates every make target / timeframe / strategy / symbol named in `.claude/` against the real registries | Weekly or after any large refactor |
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
| `post-branch` | `/post-branch` | Behaviour-gated docs sweep: diff branch changes against CLAUDE.md / README.md / MEMORY.md / Makefile / docker-compose.yml / `.claude/context/`, propose targeted edits, append "Documentation updates" to PR body. Two steps run **always**, regardless of the behaviour gate: **5b** reconciles the SoT (`project_todo_master.md` — nothing else updates it, and a stale row reads as current evidence) and **5c** audits every quantitative claim the branch's own prose asserts, naming the query that reproduces each. Skips the doc walk for pure refactors. | **Before** `gh pr create`, while the branch is still local-only (saves a duplicate CI matrix) |
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

  **Know what the window costs, because flipping back does not undo it.** wifey is not a
  GitHub fork — its history was *copied* — so **389 of 541 commits** are the still-private
  parent's pre-fork research and they are published for the duration. Anything cloned or
  indexed in that window stays out, and any fork created while public is split into its own
  network and **survives the flip back**. This is an IP/history exposure, not a secrets one:
  all 4,519 blobs scanned clean and the one real PII leak was fixed in #145.
  **Flip back promptly after the merge, and check `forks_count` is still 0 before you do.**
- **`gh` commands in this repo must pass `--repo s10023/buibui-wifey-wall-street-bot` explicitly.** The user's `gh` default repo is intentionally set to the parent `s10023/buibui-moon-trader-bot` (primary project), so `gh pr view N` / `gh pr list` / `gh pr create` without `--repo` will resolve against the parent and either fail or target the wrong repo. This is a preference, not a fix-to-be-found — do not run `gh repo set-default` to "solve" it.
