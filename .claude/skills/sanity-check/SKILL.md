---
name: sanity-check
description: >
  Full project health check across five dimensions: CI hygiene, wiring audit,
  docs sync, skills freshness, architecture review.
  Invoke weekly, after any large refactor or merge, when the user says
  "/sanity-check", or asks "is everything wired up", "do the docs match",
  or "anything stale".
allowed-tools: Bash, Read, Edit
---

# Sanity Check Skill

Run a full periodic health check of the buibui-moon-trader-bot codebase. **Run weekly, or after any large refactor/merge.**

This check covers five dimensions: CI hygiene, wiring audit, documentation sync, skills freshness, and architecture review.

---

## 1. CI checks (run first — block on failures)

```bash
make lint-py       # ruff format + lint
make typecheck     # mypy strict
make test          # pytest
make lint-md       # markdownlint-cli2
poetry check       # lockfile consistency
```

Report pass/fail for each.

---

## 2. Wiring audit (critical — catches silent failures)

This is the most important section. Wiring bugs cause silent failures (missing signals, 500s in UI, DB never written).

Check all of the following:

### Strategy registry completeness
Every strategy must appear in ALL of these locations or it silently breaks:
- `analytics/indicators_lib.py` — `STRATEGY_REGISTRY` dict entry
- `analytics/indicators_lib.py` — `DETECTOR_REGISTRY` dict entry (except `funding_reversion`, `smt_divergence` — they have explicit branches in `backtest_runner.py`)
- `signals/registry.py` — `SIGNAL_REGISTRY` entry (all except `seasonality` and legacy `fibonacci_retracement`)
- `tests/` — at least one test for the detector function

Run this to get a cross-reference:
```bash
grep -n '"[a-z_]*":' analytics/indicators_lib.py | grep -E "(STRATEGY|DETECTOR)_REGISTRY"
grep -n 'name=' signals/registry.py
```

Compare the two lists. Flag any strategy in STRATEGY_REGISTRY but not DETECTOR_REGISTRY (or vice versa), and any in DETECTOR_REGISTRY but not SIGNAL_REGISTRY.

### Config wiring
- Does every `[strategy_params.X]` key in `config/signal_watch.toml` correspond to a real strategy name in `STRATEGY_REGISTRY`?
- Does `backtest_config.py:BacktestSweepConfig` include all flags exposed by `wifey.py` CLI?
- Does `signal_config.py:SignalWatchConfig` include all fields read from the `[backtest]` section of signal_watch.toml?

### API router completeness
- Every router in `web/api/routers/` must be imported and registered in `web/api/main.py`
- Every Pydantic model in `web/api/models/` must be used by at least one router

### Data pipeline
- `data_sync.py` syncs OHLCV — confirm it's wired into `analytics_runner.py` and `signal_runner.py`
- `upsert_signals` in `data_store.py` — confirm it's called from `signal_lib.py:run_scan_cycle()`
- `upsert_backtest_run` / `upsert_backtest_trades` — confirm called from `backtest_runner.py` when `SAVE=1`

### Thin wrapper / pure lib boundary
- `*_runner.py` files must NOT contain business logic — only: create client, open DB, call lib, close
- `*_lib.py` files must NOT import `binance_client`, make network calls, or open DB connections at module level

---

## 3. Documentation sync

Check these in parallel:

### README.md
- Does `## Usage` reflect all current `buibui` subcommands? Verified set:
  `monitor`, `signal`, `analytics`, `backtest`, `digest`, `param-sweep`,
  `param-audit`, `recalibrate`, `web`. Note `signal` and `monitor` are
  parent groups (`buibui signal watch`, `buibui signal test`,
  `buibui monitor price`, `buibui monitor position`).
- Does `## Directory Structure` list all current top-level modules?
- Are any sections referencing removed features?

### CLAUDE.md
- Does `## Project Structure` match actual files on disk?
- Does `## Agent Skills` table list all skills currently in `.claude/skills/`?

Check with:
```bash
ls .claude/skills/*/SKILL.md
```
Compare against the table in `CLAUDE.md` — flag any skill directory with no entry in the table, or any table entry with no corresponding `SKILL.md`.

### MEMORY.md
Path: `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`
- Is **Current State** up to date with recent changes?
- Are completed items marked ✅ in the To-Do List?
- Are any open questions resolved that should be cleared?

---

## 4. Skills freshness audit

Each skill in `~/.claude/skills/` documents a workflow. Skills can go stale when the codebase evolves. Check:

For each skill, verify the **key claims** are still true:

| Skill | What to verify |
|-------|---------------|
| `atr-sweep` | `--atr-sl-values` CLI flag exists in `wifey.py`; `format_atr_sl_sweep_table` exists in `backtest_lib.py` |
| `volume-sweep` | `volume_suppress` field in `BacktestSweepConfig`; `effective_volume_suppress(strategy)` on `BacktestSweepConfig` |
| `backtest-findings` | Min-trades thresholds still match `recalibrate_lib.py` defaults |
| `recalibrate` | `buibui recalibrate` subcommand wired in `wifey.py`; `--config` + `--apply` flags present; `confidence_ratings` DB table exists |
| `new-strategy` | 4-file checklist still accurate; `DETECTOR_REGISTRY` is still the single source of truth |
| `signal-watch` | `buibui signal watch` subcommand exists; TOML field names match `signal_config.py`; `min_avg_r` (not `filter_threshold`) in `[backtest]` section |
| `pr-summary` | Template sections match what's in the skill body |
| `backtest-run` | All CLI flags listed match what `buibui backtest --help` outputs |
| `stats-dashboard` | Card count matches actual Stats.svelte; live vs cached split still accurate |
| `investigate-strategy` | `make buibui-signal-test` Makefile target exists; `--at` UTC interpretation still correct |

Flag any stale claims and update the skill file.

---

## 5. Architecture review (use code-reviewer agent)

Launch a `feature-dev:code-reviewer` agent with this checklist:

- **Dead code**: Unused imports, functions, variables, or orphaned files not referenced anywhere?
- **Duplicate logic**: Any logic duplicated between modules that should be shared?
- **Type annotations**: All public functions annotated (including `-> None` for tests)?
- **Hardcoded values**: Magic numbers/strings that should be constants or config?
- **TODO/FIXME markers**: Any stale markers to clean up?

```bash
grep -rn "TODO\|FIXME" --include="*.py" . | grep -v ".venv"
```

---

## Output format

Report results as a table with one row per check:

| # | Dimension | Check | Status | Action needed |
|---|-----------|-------|--------|---------------|
| 1 | CI | lint-py | ✅ | — |
| 2 | CI | typecheck | ✅ | — |
| 3 | CI | test | ✅ | — |
| 4 | CI | lint-md | ✅ | — |
| 5 | CI | poetry check | ✅ | — |
| 6 | Wiring | Strategy registry cross-ref | ✅/❌ | ... |
| 7 | Wiring | Config fields | ✅/❌ | ... |
| 8 | Wiring | API router registration | ✅/❌ | ... |
| 9 | Wiring | Thin wrapper boundary | ✅/❌ | ... |
| 10 | Docs | README subcommands | ✅/❌ | ... |
| 11 | Docs | CLAUDE.md structure | ✅/❌ | ... |
| 12 | Docs | MEMORY.md current state | ✅/❌ | ... |
| 13 | Skills | Skill files vs CLAUDE.md table | ✅/❌ | ... |
| 14 | Skills | Stale claims audit | ✅/❌ | ... |
| 15 | Arch | Dead code / duplicates | ✅/❌ | ... |

At the end:
- List all ❌ items with concrete next steps
- Update MEMORY.md: add today's sanity check date and any open findings
