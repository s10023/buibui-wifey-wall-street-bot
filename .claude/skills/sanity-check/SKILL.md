---
name: sanity-check
description: >
  Full project health check: run the mechanical sweep (`make sanity-checks`),
  then the judgement passes it cannot cover — wiring semantics, per-skill claim
  checks, MEMORY.md currency, architecture review.
  Invoke weekly, after any large refactor or merge, when the user says
  "/sanity-check", or asks "is everything wired up", "do the docs match",
  or "anything stale".
allowed-tools: Bash, Read, Edit
effort: high
---

# Sanity Check Skill

**Run weekly, or after any large refactor/merge.**

Everything mechanical is now code. This file holds what a script cannot decide.

## Phase 1 — Run the mechanical sweep

```bash
make sanity-checks
```

Eight checks in `tools/sanity_checks.py`.

| Check | Asks |
| --- | --- |
| `fork-drift` | Does a doc name a make target, timeframe, `--strategy` or `SYMBOL` the code does not have? |
| `parent-leakage` | Does a **skill** present a crypto-parent artifact as current? |
| `missing-paths` | Does a doc name a repo path in backticks that is not there? |
| `context-coverage` | Does every top-level package reach `.claude/context/`? |
| `router-wiring` | Do the three hand-maintained router lists agree — disk, import tuple, registration loop? |
| `config-strategies` | Does every `[strategy_params.X]` key name a real strategy? |
| `cli-documented` | Does every `wifey` subcommand appear in README? |
| `regression-surface` | Does CLAUDE.md name every path CI's regression filter fires on? |

**Unlike `/post-branch`'s sweep, this one gates.** It exits non-zero on any finding,
and the same sweep runs in `make test`
(`tests/test_sanity_checks.py::test_working_tree_is_clean`) **and** as an
unconditional step in CI's `markdownlint` job. That second placement is the point:
the test job sits behind a `**/*.py` paths filter, so on a docs-only PR — the exact
change this guards — the pytest gate never runs. CLAUDE.md's rule is what requires
it: *a self-check outside CI is not a check.*

**Every leg is CI-portable.** A gitignored path (`config/stocks.json`) is expected to
be absent rather than read directly, so a clean checkout degrades that leg to a
printed note instead of crashing; the three legs needing project imports report
`SKIPPED` where nothing is installed. **A degraded leg is a note, never a finding** —
counting it would leave the sweep permanently red in CI, and a check that is never
green stops being read.

**Every allowlist entry carries its reason inline.** An entry without one is how
a check decays into a no-op.

## Phase 2 — Already gated elsewhere; do not re-check by hand

| Was | Now |
| --- | --- |
| Registry cross-reference | `tests/test_signal_registry.py` — set-diffs all three registries **and** `_SWEEP_STRATEGIES`, and checks `SIGNAL_REGISTRY`/`DETECTOR_REGISTRY` bind the same function object |
| Schema ↔ positional INSERT arity | `tests/test_schema_insert_arity.py`, in `make test` |
| Tests that name a unit but never call it | `make check-orphan-tests` (advisory) |
| Docs indexes | `tests/test_docs_index.py` — a stale `INDEX.md` is a red suite |
| CI hygiene | `make lint-py`, `make typecheck`, `make test`, `make lint-md`, `poetry check` — those *perform an action*; run them, do not "check" them |

`check-orphan-tests` has two verdicts and they need different responses.
**`not-importable`** means the unit exists only as a closure, so no test can call
it and the tests can only re-implement it — **extraction is a prerequisite for
fixing such a bug, not scope creep**. `cli/main.py::build_parser` was split out
of `main()` for exactly this reason. **`not-called`** is ordinary drift after an
extract or rename. A genuine false positive goes in `EXEMPT_CLASSES` **with its
reason**.

Neither a green suite nor any check here can see the sibling trap in CLAUDE.md:
**a test that asserts a config value parsed can never detect that the parsed
value produces nothing.**

## Phase 3 — Judgement: wiring semantics

The sweep proves lists agree. It cannot read intent.

- **Config fields**: does `backtest_config.py:BacktestSweepConfig` include every
  flag the backtest parser exposes? The flags live in both `cli/backtest.py` and
  `cli/_common.py` — a shared helper adds several (`--min-sl-pct` among them), so a
  grep scoped to `cli/backtest.py` alone under-reports the set. Does
  `BacktestSweepConfig` also cover every key in **`config/strategy_params.toml`**'s
  `[backtest]` section? That section lives in the shared base, reached via `extends` —
  neither `signal_watch*.toml` carries a `[backtest]` section at all (they carry
  `extends` / `timeframes` / `day_filter` / `strategies` plus two tables), so scoping
  the check to those files scans an empty set and reports clean. Two keys resolve
  indirectly and a literal field comparison mis-flags them: `min_trades_<tf>` by
  prefix-strip (`backtest_config.py:343`) and `live_parity` as a sub-table (`:475`).
  `[backtest]` has **two** consumers: of its 10 keys, `mode`, `min_avg_r` and
  `cache_enabled` belong to `BacktestFilterConfig` in `signal_config.py` — the live EV
  gate, read at `:658`/`:663` and `scanner.py:687` — not to the sweep. Diff each key
  against both before calling one unmapped.
- **Pydantic models**: is every model in `web/api/models/` used by a router?
- **Data pipeline**: is `data_sync.py` wired into `analytics_runner.py` and
  `signal_runner.py`? Is `upsert_signals` in `data_store.py` called from
  `signal_lib.py:run_scan_cycle()`? Are `upsert_backtest_run` /
  `upsert_backtest_trades` called from `backtest_runner.py` when `SAVE=1`? Two of those
  paths resolve only through a star-import shim, so a literal `grep` returns a false
  negative: `analytics/signal_lib.py` is `from analytics.signal import *`, and
  `run_scan_cycle` actually lives in `analytics/signal/scanner.py`. Verify a shim with
  `hasattr` on the imported module rather than grep — the shim is the public surface,
  so the sentence above is true through it.
- **Thin wrapper / pure lib boundary**: `*_runner.py` holds no business logic —
  create client, open DB, call lib, close. One documented exception:
  `backtest_runner.py` owns the TA sweep pipeline (`.claude/context/analytics.md`,
  "sweep orchestrator") and stays as is while the book is frozen, so it is not a finding.
  `*_lib.py` makes no network call and
  opens no DB connection at module level; `utils/yfinance_client.py` and
  `utils/edgar_client.py` must stay side-effect-free at import time.

## Phase 4 — Judgement: per-skill claim checks

Verify each skill's **key claims** are still true. This is semantic — a symbol
can exist and mean something else.

| Skill | What to verify |
| --- | --- |
| `atr-sweep` | `--atr-sl-values` in `cli/backtest.py`; `format_atr_sl_sweep_table` reachable as `analytics.backtest_lib.format_atr_sl_sweep_table`. `backtest_lib.py` is a star-import shim — a grep there returns a false negative; real home `analytics/backtest/formatters.py`. Use `hasattr`, per phase 3 |
| `volume-sweep` | `volume_suppress` and `effective_volume_suppress(strategy)` on `BacktestSweepConfig` |
| `backtest-findings` | Min-trades thresholds match **`config/strategy_params.toml`** — top-level `min_trades_*` for the sweep column, `[backtest].min_trades_*` for the daemon column, not `recalibrate_lib.py`: its `min_trades` defaults (10 pooled / 5 directional) are a *different* gate |
| `recalibrate` | `wifey recalibrate` wired; `--config` + `--apply` present; `confidence_ratings` exists |
| `new-strategy` | The 4-file checklist; `_REGISTRY_EXCLUDED` still names the real opt-outs. It lives in `tests/test_signal_registry.py`, not in `analytics/strategies/` — currently `{"seasonality"}`, consistent with 17 `KNOWN_STRATEGIES` and 16 in `DETECTOR_REGISTRY` |
| `signal-watch` | TOML field names match `signal_config.py`; `min_avg_r`, not `filter_threshold`. `filter_threshold` does appear in `signal_config.py` and that is not drift — the loader *raises* on it as a dead key, so presence is the guard, not the defect |
| `pr-summary` | Template sections match the skill body |
| `backtest-run` | Flags listed match `wifey backtest --help` |
| `stats-dashboard` | **Inventory**, never the count: does every card rendered in `Stats.svelte` appear in the tables, and does every listed row correspond to a real card? Plus the live vs cached split. Not every row is a `card-title` — `Weekly Current State` renders as a live banner (`web/ui/src/pages/Stats.svelte`, keyed on `stats.weekly_current_state`), so extracting card titles alone under-counts and manufactures a finding |
| `investigate-strategy` | `make wifey-signal-test` exists; `--at` UTC interpretation |

**A count is not an inventory — route the claim by asking what a wrong restatement
would do to the check.** If restating a claim wrongly would leave the mechanical check
green, the claim belongs in the semantic half and needs an **external referent** —
something outside the check that the check can be wrong about. `stats-dashboard` is the
worked example: a card *count* can be accidentally right while the table underneath
lists the wrong rows — a scalar over a set passes on any error that conserves
cardinality. The same shape can recur in code: a presence check asking "does the token
appear?" reports covered on a real token sitting in unrelated prose. **Tightening a
probe cannot fix this class** — change *what the check asks*, not how precisely.

There is no "Agent Skills table" in CLAUDE.md to reconcile against. Each skill's
description is already loaded every session, so such a table would be pure
duplication — a check for one would match nothing, and matching nothing reads exactly
like "no problems found."

## Phase 5 — Judgement: docs currency

- **MEMORY.md** — **this** repo's, never the crypto parent's; a ported path sends Current
  State into the wrong repo, silently. **Resolve the path, never spell it as a
  literal** — a hardcoded machine home directory breaks on the next host move.
  `make cadence-check` prints the resolved path; `tools/claude_home.py::memory_dir`
  derives it. Is Current State current? Are resolved open questions cleared?
- **CLAUDE.md**: does every Project Structure row's package still exist, and does
  its `Deep reference` pointer resolve? It is a package *index* plus a
  verdict/footgun block, not a file listing — if module detail creeps back in,
  move it to the context doc.
- **README**: does anything reference a removed feature?

## Phase 6 — Judgement: architecture review

Run it inline; do not launch a subagent. Missing annotations and dead imports are already
gated by mypy strict and ruff, and unit-naming drift by `make check-orphan-tests`, so what
is left is stale markers plus a read for duplicated logic and hardcoded values in files
changed since the last run.

```bash
git grep -nE '\b(TODO|FIXME|XXX|HACK)\b' -- '*.py'
git grep -cE '\bdef\b' -- '*.py' | head -1   # positive control: must print a hit
```

A zero from the first line counts only if the control printed a hit, since a pattern
the grep engine cannot parse also returns nothing.

## Two traps, both live

**A prose-marker grep for `binance|BTCUSDT|liquidity_sweep|…` was tried and
rejected — do not add one.** Over the current-state surfaces it
returns ~40 hits that are almost all correct history: TOML changelog comments
recording *why* `liquidity_sweep` was pruned, CLAUDE.md's fork-lineage paragraph,
context docs naming a removed strategy as removed. The discriminating signal is
not *mentions a crypto artifact* but *presents one as current*, which is
semantic. This is why `parent-leakage` is scoped to `.claude/` while its siblings
are not: a skill instructs an action, so a parent artifact there is invocable
rather than historical. Widening it returns four hits, all true statements about
the fork's origin — pinned by `test_scope_stops_at_dot_claude`.

**`git grep -- $VAR` silently matches nothing in zsh.** Unquoted parameter
expansions are not word-split here, so a pathspec list in a variable arrives as
one bogus path and the command exits 0 with no output — indistinguishable from
clean. Write pathspecs literally, or `${=VAR}`, and sanity-check any zero by
running the same pattern against a commit you know is dirty
(`git grep <pattern> main -- <path>`).

## Output format

```text
mechanical sweep   — clean | <n> findings triaged: <what>
already-gated      — make test / check-orphan-tests: pass | <failure>
wiring semantics   — <finding> | clean
per-skill claims   — <skill>: <stale claim> | all current
docs currency      — MEMORY.md / CLAUDE.md / README: <what changed>
architecture       — <finding> | clean
```

End by listing every ❌ with a concrete next step, and update MEMORY.md's Current
State with the date and any open findings.

**Then stamp the run — this is the final step:**

```bash
make cadence-stamp TASK=sanity-check
```

`make cadence-check` reports this skill as OVERDUE until you do, and a **missing mark reads
as overdue on purpose** — so skipping the stamp is indistinguishable from skipping the run,
which is the intended direction. Stamp only a run that actually happened.
