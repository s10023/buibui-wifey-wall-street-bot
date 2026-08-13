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

Run a full periodic health check of the buibui-wifey-wall-street-bot codebase. **Run weekly, or after any large refactor/merge.**

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

- `analytics/strategies/<name>.py` — the `detect_*` module itself
- `analytics/strategies/_registry.py` — `STRATEGY_REGISTRY` entry
- `analytics/strategies/_registry.py` — `DETECTOR_REGISTRY` entry
- `signals/registry.py` — `SIGNAL_REGISTRY` entry
- `tests/` — at least one test for the detector function

**`analytics/indicators_lib.py` no longer exists** — it was removed in strat-3
and the registries live in `analytics/strategies/_registry.py`. This section
grepped the dead path until 2026-08-05, which does not error visibly enough:
the grep simply matches nothing, and an empty result reads exactly like "no
problems found" in the repo's most important wiring check.

Cross-reference by importing the registries rather than grepping for them —
a grep silently returns nothing when a path or a literal changes shape:

```bash
poetry run python -c "
from analytics.strategies._registry import STRATEGY_REGISTRY, DETECTOR_REGISTRY
from signals.registry import SIGNAL_REGISTRY
print('STRATEGY', len(STRATEGY_REGISTRY), '| DETECTOR', len(DETECTOR_REGISTRY), '| SIGNAL', len(SIGNAL_REGISTRY))
print('STRATEGY - DETECTOR:', set(STRATEGY_REGISTRY) - set(DETECTOR_REGISTRY))
print('DETECTOR - SIGNAL  :', set(DETECTOR_REGISTRY) - set(SIGNAL_REGISTRY))
"
```

Expected as of 2026-08-05: `STRATEGY 17 | DETECTOR 16 | SIGNAL 16`, with
`STRATEGY - DETECTOR == {'seasonality'}` (a stats helper, not a dispatchable
alert) and `DETECTOR - SIGNAL == set()`. Any other difference is a real wiring
bug. Note `funding_reversion` and `smt_divergence` are **gone** (stripped at the
fork / T5b) — they survive only in comments explaining their removal, so do not
treat them as registry exceptions.

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
- `*_lib.py` files must NOT make network calls or open DB connections at module level. (The fork's `utils/binance_client.py` is gone; the equivalent live clients are `utils/yfinance_client.py` and `utils/edgar_client.py`, both of which must stay side-effect-free at import time.)

### Tests that name a unit but never call it

```bash
make check-orphan-tests
```

Advisory, not part of `make test`. Two verdicts, both actionable:

- **`not-importable`** — the unit exists only as a **closure**, so no test can
  call it and the tests can only re-implement it. This is the #150 shape:
  `TestEvGate` held five tests that never invoked the EV gate because it was
  `def _passes_ev_gate` nested inside `run_scan_cycle`; one asserted the defect
  as the expectation, another reduced to `assert None is None`, and all five
  passed against any implementation. **Extraction is a prerequisite for fixing
  such a bug, not scope creep.**
- **`not-called`** — an importable callable matches the class's subject, but no
  test in the class calls it. Ordinary drift after an extract or rename.

A clean tree prints `✅`. Findings are advisory — **review, never auto-fix**, and
if one is a genuine false positive add it to `EXEMPT_CLASSES` in
`tools/orphan_test_audit.py` **with its reason** (three are listed there today;
all three are the subject being reached one indirection away, via a local test
helper or a CLI `main()`). An allowlist entry without a reason is how the check
decays into a no-op.

Note that neither this check nor a green suite can see the sibling trap in
CLAUDE.md: **a test that asserts a config value PARSED can never detect that the
parsed value produces nothing.**

### Schema ↔ positional INSERT arity

Enforced by `tests/test_schema_insert_arity.py`, so it runs in `make test` — no
separate command. It exists because nothing mechanically tied a table's DDL to
its positional INSERT: `put_backtest_cache` writes `VALUES (?,…)` with a
hand-counted placeholder list, and `schema.py` states the constraint in prose.

If it fails, do **not** relax the test — the INSERT is almost certainly wrong.
Two things to know before touching it:

- The truth side is the **real schema** (`init_schema` against an in-memory
  DuckDB, read back from `information_schema.columns`), not parsed DDL text.
  Four `backtest_runs` columns exist **only** in the migration list and never in
  `CREATE TABLE`, so a text-parsing check would report a false failure on the
  very table it is meant to guard.
- `INSERT … SELECT` is checked by **name order**, not just count, because that
  form is positional — a transposition of two same-typed columns is otherwise
  silent.

---

## 3. Documentation sync

Check these in parallel:

### README.md

- Does `## Usage` reflect all current `wifey` subcommands? Verified set
  (checked against `wifey.py --help`, 2026-08-05): `signal`, `analytics`,
  `backtest`, `digest`, `param-sweep`, `param-audit`, `recalibrate`, `web`.
  `signal` and `analytics` are groups (`wifey signal watch`,
  `wifey signal test`, `wifey analytics backfill`, `wifey analytics sync`).
  **There is no `monitor` subcommand** — it and the entire `monitor/` package
  were removed in T16-partial (2026-05-15); equity price/position monitoring
  lives in the web UI. Re-derive this set from `--help` rather than trusting
  the list above.
- Does `## Directory Structure` list all current top-level modules?
- Are any sections referencing removed features?

### CLAUDE.md

- Does `## Project Structure` match the packages on disk? **Since the
  2026-08-05 split it is a package *index* plus a verdict/footgun block, not a
  file listing** — so check that every row's package still exists and that its
  `Deep reference` pointer resolves to a real `.claude/context/*.md`, NOT that
  every file appears here. Detail belongs in the context docs; if you find
  module-level detail creeping back into CLAUDE.md, move it out.
- Do the context docs cover every package? `any_referencing_changed_artifact`
  greps cannot detect a package the docs have never heard of, so run a presence
  check:

  ```bash
  for d in */; do d=${d%/}
    case $d in tests|docs|config|scripts|__pycache__|.*) continue;; esac
    grep -rqs "$d" .claude/context/ || echo "UNDOCUMENTED: $d"; done
  ```

- Does `## Agent Skills` table list all skills currently in `.claude/skills/`?

Check with:

```bash
ls .claude/skills/*/SKILL.md
```

Compare against the table in `CLAUDE.md` — flag any skill directory with no entry in the table, or any table entry with no corresponding `SKILL.md`.

### MEMORY.md

Path: `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/MEMORY.md`
(**this** repo's memory — NOT the crypto parent's. A ported path here sends the
session's `Current State` update into the wrong repo, silently.)

- Is **Current State** up to date with recent changes?
- Are completed items marked ✅ in the To-Do List?
- Are any open questions resolved that should be cleared?

---

## 4. Skills freshness audit

Each skill in `.claude/skills/` (project-local, committed) documents a workflow. Skills can go stale when the codebase evolves. Check:

### 4a. Fork-drift sweep (mechanical — run this FIRST)

This tree was forked from the crypto parent, so a skill can be internally consistent
and still name artifacts that exist only upstream. **Every instance found so far was a
command that fails outright on invocation, and none were found by reading — only by
running a check.** On 2026-08-05 this sweep found 16 skill files instructing
`make buibui-*` (0 such targets exist), `--interval 15m` (rejected by `_INTERVAL_CONFIG`),
`BTCUSDT` symbols, four removed strategies, and `config/coins.json`.

```bash
# `poetry run` is required — data_fetcher imports yfinance.
poetry run python3 - <<'PY'
import re, json, pathlib
from analytics.strategies._registry import STRATEGY_REGISTRY
from analytics.data_fetcher import _INTERVAL_CONFIG
targets = set(re.findall(r'^([a-zA-Z][\w-]*):', pathlib.Path('Makefile').read_text(), re.M))
strats, tfs = set(STRATEGY_REGISTRY), set(_INTERVAL_CONFIG)
syms = set(json.load(open('config/stocks.json')))
# Only CODE-FORMATTED invocations count: a backtick, a `$ ` prompt, or line start.
# Bare prose ("make sense", "make money", "make it visible") is not a claim about a
# make target and produced 6 of 7 hits when the scope widened past `.claude/` (#174).
MAKE_RE = re.compile(r'(?:^|`|\$ )make ([a-z][a-z0-9-]+)', re.M)
PROSE = {'target'}   # a sentence that wrapped onto a new line ("...there is no\nmake target.")
# These two files quote the anti-patterns in order to hunt for them.
SELF = {'sanity-check/SKILL.md', 'post-branch/SKILL.md'}
# Scope = every CURRENT-STATE surface, not just `.claude/`. Dated trees
# (docs/audits, docs/redesign, docs/superpowers, docs/plans) are deliberately
# absent: a past-tense claim there is correct by construction. `.claude`-only
# scope is how docs/system-overview.md kept saying "Binance Futures" for three
# months across four post-fork commits (#174).
SURFACES = sorted(pathlib.Path('.claude').rglob('*.md')) + [
    pathlib.Path(p) for p in ('CLAUDE.md', 'README.md', 'docs/system-overview.md')
]
bad = []
for f in SURFACES:
    if any(str(f).endswith(s) for s in SELF) or not f.exists():
        continue
    t = f.read_text()
    for m in MAKE_RE.finditer(t):
        # a trailing '-' means a glob/placeholder ("make wifey-*", "make wifey-<name>")
        if m.group(1).endswith('-') or m.group(1) in PROSE or m.group(1) in targets:
            continue
        bad.append((f, 'make-target', m.group(1)))
    for pat in (r'--(?:interval|timeframe)s? ([0-9]+[a-z]+)', r'TIMEFRAMES?="?([0-9]+[a-z]+)'):
        bad += [(f, 'timeframe', m.group(1)) for m in re.finditer(pat, t) if m.group(1) not in tfs]
    for m in re.finditer(r'(?:--strategy |STRATEGY=)([a-z_]+)', t):
        if m.group(1) not in strats and not m.group(1).startswith(('my_', '<')):
            bad.append((f, 'strategy', m.group(1)))
    for m in re.finditer(r'(?:--symbols? |SYMBOL="?)([A-Z]{2,6})', t):
        if m.group(1) not in syms and m.group(1) not in {'SYMBOL', 'TF'}:
            bad.append((f, 'symbol', m.group(1)))
for f, k, v in bad:
    print(f'{f}: {k}={v}')
print(f'{len(bad)} bad refs')
PY

# Parent-repo artifact leakage. Exclusions are all BY DESIGN: /sync-parent and
# /ingest-video address the parent directly; /post-branch and this file quote the
# pattern in order to hunt for it; context/ documents real legacy code paths.
git grep -nE 'buibui-moon-trader-bot|`buibui |make buibui-|coins\.json|~/\.claude/skills' -- .claude \
  | grep -vE 'sync-parent/SKILL\.md|ingest-video/SKILL\.md|post-branch/SKILL\.md|sanity-check/SKILL\.md|context/'

# Referenced repo paths that no longer exist (same widened scope as the block above)
git grep -ohE '`(analytics|signals|utils|web|tools|tests|cli|config|migrations)/[A-Za-z0-9_/.]+`' \
  -- .claude CLAUDE.md README.md docs/system-overview.md \
  | tr -d '`' | sort -u | while read -r p; do [ -e "$p" ] || echo "MISSING $p"; done
```

Expected: `0 bad refs`, no leakage hits, and **exactly these ten** `MISSING` paths, all
deliberate — template placeholders (`tests/test_my_strategy.py`,
`web/ui/src/pages/Foo.svelte`); files named *because* they are gone
(`analytics/indicators_lib.py`, `utils/binance_client.py`, `config/coins.json`); files
named *because they were never ported* (the parent's `tools/gate_audit.py`, `cli/card.py`,
`config/pundit_roster.toml`); and `config/youtube_channels.toml`, which is gitignored by
design (only `.example` is committed). Anything else is drift.

Keep the exclusion lists tight. They exist so the sweep stays silent when clean — a
check that reports known-good noise gets skimmed, which is how the drift it looks for
accumulated in the first place.

**Two traps, both hit while extending this sweep in #174.**

**`git grep -- $VAR` silently matches NOTHING in zsh.** This shell does not word-split
unquoted parameter expansions, so a pathspec list held in a variable arrives as one
bogus path and the command exits 0 with no output — indistinguishable from clean. It
produced a false "0 hits" that briefly passed as proof the tree was clean. **Write
pathspecs literally**, or `${=VAR}` if you must use one, and sanity-check any zero by
running the same pattern against a commit you know is dirty (`git grep … main -- …`).

**A prose-marker grep for `binance|BTCUSDT|liquidity_sweep|…` was BUILT, MEASURED, and
REJECTED — do not add one.** Over the current-state surfaces it returns ~40 hits and
**almost all are correct history**: TOML changelog comments recording *why*
`liquidity_sweep` was pruned, CLAUDE.md's fork-lineage paragraph, context docs naming a
removed strategy as removed. The discriminating signal is not *mentions a crypto
artifact* but *presents one as current* — which is semantic, and a grep cannot see it.
Shipping it would have added 40 known-good lines to a check whose whole value is being
silent when clean. The checks above stay keyed to **invocable** artifacts (make targets,
timeframes, `--strategy`/`SYMBOL` flags, repo paths), which are falsifiable.

### 4b. Per-skill claim checks

For each skill, verify the **key claims** are still true:

| Skill | What to verify |
| ------- | --------------- |
| `atr-sweep` | `--atr-sl-values` CLI flag exists in `wifey.py`; `format_atr_sl_sweep_table` exists in `backtest_lib.py` |
| `volume-sweep` | `volume_suppress` field in `BacktestSweepConfig`; `effective_volume_suppress(strategy)` on `BacktestSweepConfig` |
| `backtest-findings` | Min-trades thresholds still match `recalibrate_lib.py` defaults |
| `recalibrate` | `wifey recalibrate` subcommand wired in `wifey.py`; `--config` + `--apply` flags present; `confidence_ratings` DB table exists |
| `new-strategy` | 4-file checklist still accurate; `DETECTOR_REGISTRY` is still the single source of truth |
| `signal-watch` | `wifey signal watch` subcommand exists; TOML field names match `signal_config.py`; `min_avg_r` (not `filter_threshold`) in `[backtest]` section |
| `pr-summary` | Template sections match what's in the skill body |
| `backtest-run` | All CLI flags listed match what `wifey backtest --help` outputs |
| `stats-dashboard` | Card count matches actual Stats.svelte; live vs cached split still accurate |
| `investigate-strategy` | `make wifey-signal-test` Makefile target exists; `--at` UTC interpretation still correct |

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
| --- | ----------- | ------- | -------- | --------------- |
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
