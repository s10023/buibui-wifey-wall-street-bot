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

For UI / API changes: `make web-build` (production bundle) or `make web-dev` (Vite dev server).

For routine DB refresh after backtest/strategy changes: `make db-update` (= `db-update-backtest` → `db-update-recalibrate` → `regression-update`).

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
| `analytics/` | Analytics data layer (DuckDB): `store/`, `strategies/` (18 detector modules, **16** registered for dispatch), `backtest/`, `signal/`, `stats/`, `research_guards/`, `sweep_guard.py`, `audit_guard.py`, plus the data-ingest + quality + calendar layer | `context/analytics.md` |
| `analytics/{forecast,xsmom,lowvol,xasset,pead,exits}/` | The P2/P3 research sleeves and the exit diagnostic — **verdicts below** | `context/analytics.md` |
| `signals/` · `utils/` | Alerting + dedup daemon (detection lives in `analytics/`); shared Telegram / yfinance / EDGAR clients and the two config-universe loaders | `context/signals.md` |
| `web/` | FastAPI backend + Svelte 5 / Vite UI | `context/web.md` |
| `tools/` | One-shot analysis, audit, and research-ingest scripts; not part of the daemon or CLI surface | `context/tools.md` |
| `trade/open_trades.py` | Legacy Binance Futures order opener (manual/CLI use). **Phase A out of scope** — Phase B replaces it with an equities broker adapter (broker TBD) | — |
| `tests/` | pytest suite; tests import from lib modules and pass mock dependencies directly | — |
| `migrations/` | One-shot DB migration scripts, run by hand. Only `001_day_filter_text.py` (fork-era, 2026-05-14); routine schema changes go through `analytics/store/schema.py`'s migration list instead | — |
| `config/` | `stocks.json` (gitignored 13-symbol live watchlist), `universe.json` (committed 508-member research breadth universe), `strategy_params.toml` (shared base inherited via `extends`) | `context/config.md` |

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
| `exits/` MFE-MAE diagnostic (PR #96) | **INCONCLUSIVE** — the instrument works (22/22 resolved alerts scored, 0 skipped) but the live ledger is too young (n=22) for an exit-fixable-vs-entry-broken call. Re-run as it matures |

**The TA detector book is frozen** — no new boolean detectors, no tp_r / gate / threshold sweeps
(inherited category verdict). A guardrail firing (`lowvol`, `pead`) means the *construction* failed
its own neutrality precondition, so that cell is not evidence about the underlying premium.

### Footguns — read before touching these

**CRITICAL — `analytics/store/_common.py::_upsert`** uses explicit `conn.register` /
`conn.unregister` in try/finally. Never switch to the implicit replacement scan (it causes malloc
heap corruption) and never drop the try/finally.

**`DEFAULT_DB_PATH` lives in `analytics/store/_common.py`** (re-exported via `analytics.store` and
`analytics.data_store`) — import from either re-export, never redefine it in a runner.

**Causality is enforced by a test, not by review.** `tests/test_lookahead.py` (Phase 0.2) is a
truncated-series property test asserting no detector or backtest fill depends on bars after its
`open_time`; the written audit is `docs/redesign/phase0-lookahead-audit.md`. It surfaced one real
leak (`bos` read a centered swing window but stamped the signal at the swing bar) — fixed
2026-06-09 by stamping at the confirmation bar; `_KNOWN_LOOKAHEAD_DETECTORS` is now empty and all
16 detectors pass.

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
| `sanity-check` | `/sanity-check` | Full project health check: git hygiene, docs sync, wiring audit, architecture review, skills freshness | Weekly or after any large refactor |
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
| `post-branch` | `/post-branch` | Behaviour-gated docs sweep: diff branch changes against CLAUDE.md / README.md / MEMORY.md / Makefile / docker-compose.yml / `.claude/context/`, propose targeted edits, append "Documentation updates" to PR body. Skips for pure refactors. | Immediately after `gh pr create`, before reporting the PR URL |
| `stats-dashboard` | `/stats-dashboard` | Stats page architecture, card inventory, adding new cards, timezone constraints | When working on Stats page or `stats_lib.py` |
| `db-update` | `/db-update` | Routine `make db-update`: backtest (3 configs) → recalibrate → regression golden refresh | After any detector / strategy / config change that affects ratings or fixtures |
| `data-backfill` | `/data-backfill` | OHLCV ingestion via `wifey analytics backfill` / `sync` | First-time setup, wiped DB, new symbol or timeframe, filling a data gap |
| `confluence-backtest` | `/confluence-backtest` | Cross-TF (`--cross-tf`) and same-TF (`--combo`) co-firing backtests; HTF/LTF pair sweeps; post-run spot-check via `tools/combo_health.py` | After adding a strategy, changing entry logic, tuning the live `[combo]` gate, or to confirm combo tables are healthy after a refresh |
| `frontend-svelte` | `/frontend-svelte` | Svelte 5 + Vite UI workflow for `web/ui/` — pages, stores, lightweight-charts, dev/build commands | Any work under `web/ui/`; pair with `/frontend-design` for visual work |
| `journal-trade` | `/journal-trade` | Capture a manual trade into the gitignored `docs/plans/journal/` (structured frontmatter + Thesis/Plan/Execution/Outcome/Retrospective narrative); MYT→UTC timestamps; ground-truth feeder for a future AI trade-card / live outcome loop / trade-management heuristics | When the user says "journal my trade", pastes trade-execution details, or a logged trade closes |
| `sync-parent` | `/sync-parent` | Detect-and-recommend parent-repo PRs to port into wifey; writes a classified report to `/tmp/parent-sync-<date>.md` | Periodic catch-up with the parent, or after a known upstream refactor |
| `ingest-x` | `/ingest-x` | Ingest one or more X/Twitter post URLs into the research pipeline: syndication fetch (no auth/scraping, `tools/x_fetch.py` — randomized cooldown + dedup cache) → per-post vision-extract in a **sonnet** subagent carrying a self-contained inline rubric → classify (content-type gate + the 4-bucket verdict taxonomy, `tools/x_route.py`) → routing-dedup `check` (`tools/route_dedup.py`; `already_routed` blocks, near-duplicate candidates are advisory) → one consolidated review digest → route into Stream A `thesis-inbox.md` / B `mechanics-backlog.md` / C `pundit-calls.jsonl` (all gitignored under `docs/plans/`) → `mark` each append. Text + still images + quoted-tweet; video detected-and-skipped | When the user says "/ingest-x", pastes one or more x.com/twitter.com status URLs, or "ingest this/these X post(s)" |
| `ingest-video` | `/ingest-video` | Ingest one or more YouTube/X video URLs (batched) into the research pipeline: fetch metadata + transcript (`tools/video_fetch.py` — yt-dlp captions, Groq `whisper-large-v3` fallback, dedup cache + cooldown) → **sibling-repo check** (grep this repo's *and* the crypto parent's note frontmatter for `video_id`, never the filename; route by **subject** — equities/macro/gold/oil/DXY/bonds here, crypto → parent, both-subject video legitimately yields rows in both) → pass-1 **sonnet** subagent (text-only) segments + ranks candidate items → deterministic call-time resolution (`tools/video_calltime.py`, never LLM date arithmetic) → transcript-driven frame selection (`tools/video_marks.py`) + extraction (≤15 frames) → pass-2 **sonnet** subagent (vision) chart-corrects and emits item JSON → routing-dedup `check` per item + `pairs` per video (`tools/route_dedup.py`) → one digest → one approval → route via the shared `tools/x_route.py` taxonomy into Stream A/B/C plus a per-video note (`docs/plans/video-notes/`) → `mark` each append | When the user says "/ingest-video", pastes a YouTube/X video URL, or "ingest this video" |

**Always load `/frontend-design` before any Svelte/CSS/UI changes.**

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, `config/stocks.json`, or IDE-specific files
- **Per-repo git identity is mandatory** before any commit: this account uses `s10023 <ngkhaijian@gmail.com>` (global config inherits a work identity and will mis-attribute commits). Verify via `git config --local user.email` before committing. SSH alias `git@github.com-personal:...` is also required for s10023 remotes — see auto-memory `reference_ssh_host_aliases.md` for the full recipe.
- **Immediately after `gh pr create` succeeds, invoke `/post-branch` — before reporting the PR URL to the user.** Its own Step 1 behaviour gate decides whether a docs sweep is warranted, so this is cheap even on a pure refactor. Prose in the Agent Skills table demonstrably is not enough: the skill fired zero times across PRs #123–#125 here, each time on the main thread, including the session that was repairing it. A local `PostToolUse` hook on `Bash` greps for `gh pr create` and emits an advisory reminder (never blocking). **It is not in git** — `.gitignore` excludes `.claude/*`, so `.claude/settings.json` is machine-local; **re-add it after a reclone**, mirroring the crypto parent's `.claude/settings.json`.
- **`gh` commands in this repo must pass `--repo s10023/buibui-wifey-wall-street-bot` explicitly.** The user's `gh` default repo is intentionally set to the parent `s10023/buibui-moon-trader-bot` (primary project), so `gh pr view N` / `gh pr list` / `gh pr create` without `--repo` will resolve against the parent and either fail or target the wrong repo. This is a preference, not a fix-to-be-found — do not run `gh repo set-default` to "solve" it.
