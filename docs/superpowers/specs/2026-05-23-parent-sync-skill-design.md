# Parent-sync skill design

- **Date**: 2026-05-23
- **Status**: Approved (design phase)
- **Topic**: `/sync-parent` skill — detect-and-recommend pipeline for porting upstream parent-repo changes into the wifey fork
- **Next step**: writing-plans skill produces the implementation plan

## Context

This repo was forked from `s10023/buibui-moon-trader-bot` (parent) on 2026-05-14, frozen at parent commit `635ed5a`. It is being repurposed from a Binance crypto bot into a yfinance-backed US-equities signal bot.

Parent and fork are both active. Parent continues to ship strategy/gate/sweep/bugfix work that may apply to wifey; wifey has independently removed crypto-specific modules (`binance_client`, `live_price/`, `live_position/`, `cme_gap_lib`, `funding_rates`, `open_interest`, `taker_buy_volume`, `cvd_divergence`, `smt_*`, `funding_extreme`) and rewired to yfinance.

A plain `git merge` or cherry-pick will not work: paths/modules have diverged enough that VCS-level operations produce broken trees. A category-aware port flow is required.

This spec defines a skill that **detects and recommends** parent changes for porting — read-only, no automated edits.

## Goals

1. On each invocation, enumerate parent commits since the last sync point, group them by PR, and classify each PR into `SKIP` / `PORT` / `EVALUATE` / `ALREADY-APPLIED`.
2. Produce a markdown report at `/tmp/parent-sync-<date>.md` that gives a human reviewer enough context (parent MEMORY excerpt, suggested wifey target paths, suggested approach) to decide on each PORT/EVALUATE candidate without re-reading the parent repo from scratch.
3. Persist a sync pointer (last-scanned parent commit hash) in wifey memory so subsequent runs are incremental.

## Non-goals

- No automated edits to wifey code. The skill is read-only.
- No automated `git merge` / `git cherry-pick` against either repo.
- No tracking of *outcomes* (whether a PORT was actually performed) beyond the pointer-bump signal.
- Sweep findings (`tp_r`, ATR multipliers) are classified `EVALUATE` not auto-applied — values are cohort-dependent and don't transfer 1:1 between crypto and equities.

## Design decisions

These decisions were made during the brainstorming session (2026-05-23). Each binds a design dimension to a specific choice with rationale.

| Decision | Choice | Rationale |
| --- | --- | --- |
| Automation level | Detect + recommend only | Read-only is low-risk; humans port. Avoids automated miswiring given path divergence. |
| Scan range per run | Incremental (last-sync → parent HEAD) | Smaller, focused reports. Bootstrap covered by skipping the state file on first run. |
| State location | Wifey memory topic file | Consistent with the project's memory-driven workflow. Not in version control. |
| Unit of change | Per merged PR | Matches how parent's MEMORY.md is organized; PR descriptions hold the *why*. |
| Bucket rules | Default path-glob rules (this spec, §Components) | Encoded inline; YAGNI on data-driven config until divergence grows. |
| Already-applied detection | Content-grep heuristic with confidence flag | Catches independent ports during bootstrap; never auto-removes anything. |
| Report location | Ephemeral `/tmp/parent-sync-<date>.md` | Matches existing `/tmp/next-conversation-prompt-wifey.md` convention. Memory state file holds the durable record. |
| Approach shape | Context-enriched classifier (path translation + parent MEMORY excerpt + suggested approach) | Highest leverage for the human reviewer; divergence map is small enough (~15 entries) to maintain. |

## Architecture

Two artefacts:

1. **`.claude/skills/sync-parent/SKILL.md`** — high-level workflow instructions for Claude, matching the existing skill convention (peer to `sanity-check`, `atr-sweep`, etc.). Invoked as `/sync-parent`.
2. **`tools/sync_parent.py`** — single-file Python helper (pure stdlib + `subprocess`). Read-only on both repos except for the memory state file on `--bump-to`.

Path translation map lives inline in `tools/sync_parent.py` as `PARENT_TO_WIFEY_PATHS: dict[str, str | None]` (~15 entries; `None` = SKIP).

Sync state lives at `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/project_parent_sync_state.md`. YAML frontmatter holds the last-synced hash; body holds a short run history.

Parent repo access: assumes local clone at `/home/kng/repo/buibui-moon-trader-bot/`. Skill performs `git -C <path> fetch origin main` before scanning. Read-only — never modifies parent.

Wifey repo access: read-only. Content-grep for already-applied detection runs against wifey working tree via `git grep`.

## Components

### `.claude/skills/sync-parent/SKILL.md`

Five sections matching the project's existing skill convention:

1. **When to use** — periodic catch-up; before/after upstream refactor; bootstrap (first run).
2. **Prerequisites** — parent local clone present, on `main`; memory state file exists or skill creates it on bootstrap.
3. **Invocation** — `make wifey-sync-parent` wraps `PYTHONPATH=. poetry run python tools/sync_parent.py`.
4. **Output** — `/tmp/parent-sync-<date>.md` plus pointer-bump hint on stdout.
5. **Post-run actions** — port-decision flow ("for each PORT, open a fresh Claude session and feed the PR# + parent-MEMORY excerpt"); pointer bump via `--bump-to <hash>` once user is satisfied that the scanned range is fully reviewed.

### `tools/sync_parent.py`

Single-file Python (pure stdlib + `subprocess`). Functional decomposition:

| Function | Purpose |
| --- | --- |
| `load_sync_state() -> str` | Read state file; fall back to `FORK_COMMIT` on bootstrap |
| `fetch_parent_commits(from_hash) -> list[Commit]` | `git -C <parent> log --merges <hash>..origin/main` |
| `group_into_prs(commits) -> list[PR]` | Parse `Merge pull request #N` titles + squash PRs identified by `(#N)` in subject |
| `classify_pr(pr) -> Bucket` | Path-glob bucket rules |
| `extract_memory_entry(pr_number) -> str \| None` | Grep parent `MEMORY.md` for matching PR; return surrounding paragraph |
| `detect_already_applied(pr) -> Confidence` | Extract added symbols from diff; `git grep` wifey; LOW/MEDIUM/HIGH/UNKNOWN |
| `translate_paths(pr_files) -> list[WifeyPath]` | Apply `PARENT_TO_WIFEY_PATHS` |
| `suggest_approach(pr, bucket, confidence) -> str` | Heuristic returns one of three labels (see below) |
| `format_report(prs) -> str` | Markdown header + bucket-summary table + per-PR detail blocks |
| `main()` | CLI orchestrator |

### CLI surface

```bash
poetry run python tools/sync_parent.py                  # incremental from state file
poetry run python tools/sync_parent.py --from <hash>    # override start point
poetry run python tools/sync_parent.py --full           # fork → HEAD (audit)
poetry run python tools/sync_parent.py --bump-to <hash> # update state, no scan
poetry run python tools/sync_parent.py --no-fetch       # use local refs only
```

### Constants block (top of file)

```python
PARENT_REPO_PATH = Path("/home/kng/repo/buibui-moon-trader-bot")
FORK_COMMIT = "635ed5a"
WIFEY_MEMORY_DIR = Path.home() / ".claude-personal" / "projects" / "-home-kng-repo-buibui-wifey-wall-street-bot" / "memory"
PARENT_MEMORY_PATH = Path.home() / ".claude-personal" / "projects" / "-home-kng-repo-buibui-moon-trader-bot" / "memory" / "MEMORY.md"
PARENT_TO_WIFEY_PATHS: dict[str, str | None] = {
    "analytics/indicators_lib.py": "analytics/strategies/_registry.py",
    "analytics/cme_gap_lib.py": None,
    "utils/binance_client.py": None,
    "monitor/live_price.py": None,
    "monitor/live_position.py": None,
    "trade/open_trades.py": None,
    # ... ~15 entries total; expanded as divergence grows
}
SKIP_GLOBS = ["**/funding_rates*", "**/open_interest*", "**/cvd_divergence*", "**/smt_*"]
```

### Suggested-approach labels

The `suggest_approach` heuristic returns one of three labels per PORT/EVALUATE candidate:

- **`verify-only`** — already-applied confidence is HIGH. The port appears to be already in wifey. User should diff against the parent change to confirm completeness; usually no code edits needed.
- **`cherry-pick-with-edits`** — all touched parent paths map to surviving wifey paths via `PARENT_TO_WIFEY_PATHS`, and the change is mechanical (bug fix, refactor, test). User can usually take the parent diff, apply with minor path-rename edits, and ship.
- **`re-implement`** — significant path translation needed (e.g., parent change spans modules that were split, renamed, or partially removed in wifey), or the change requires equity-cohort re-validation (e.g., sweep finding, threshold tweak). User should treat the parent change as inspiration, not a diff to apply, and re-derive the change against wifey's structure.

### Bucket classification rules

**SKIP** (auto, no human review):

- Touches files removed in fork: `binance_client.py`, `live_price/`, `live_position/`, `cme_gap_lib`, `trade/open_trades.py`
- Touches removed data fields: `funding_rates`, `open_interest`, `taker_buy_volume`
- Touches removed strategies: `cvd_divergence`, `smt_divergence`, `smt_pairs`, `funding_extreme`
- Crypto-only test fixtures (BTCUSDT/ETHUSDT/SOLUSDT-only)

**PORT** (high confidence portable):

- Bug fixes in surviving modules (`analytics/`, `signals/`, `web/`, `utils/`, `cli/`)
- Detector logic refinements on surviving strategies
- Test improvements on surviving tests
- Infra changes (CLI, DB schema, web UI, Makefile)

**EVALUATE** (judgment call):

- Sweep findings (`tp_r`, ATR multipliers) — methodology may transfer; values won't
- New strategies introduced in parent
- Bias / regime / gate behavior changes — likely portable but cohort-sensitive
- TOML config changes on surviving strategies
- Files not in `PARENT_TO_WIFEY_PATHS` (unmapped fallback)

**ALREADY-APPLIED** (content-based check):

- For each parent change, skill extracts representative added symbols/strings (new function names, new constants, new strategy ids) and `git grep`s wifey for them.
- If most match, flag with confidence (LOW/MEDIUM/HIGH). Never auto-removed from the report; human verifies.

## Data flow

### End-to-end execution

1. User invokes `/sync-parent` (skill) or `make wifey-sync-parent` (direct).
2. Skill instructions tell Claude to verify parent path exists and is on `main`, then run the helper.
3. Python script execution:
   1. Parse CLI args (`--from`, `--full`, `--bump-to`, `--no-fetch`).
   2. Load sync state → `last_hash` (bootstrap to `FORK_COMMIT` if absent).
   3. `git -C <parent> fetch origin main` (unless `--no-fetch`).
   4. `git -C <parent> log <last>..origin/main --merges` → ordered merge commits.
   5. For each commit: pull `--name-only` diff + commit body to extract PR # and touched files.
   6. Group commits into PRs (`Merge pull request #N` OR `(#N)` in subject for squash merges).
   7. For each PR, run the per-PR pipeline (below).
   8. Format markdown report; write to `/tmp/parent-sync-<date>.md`.
   9. Print summary + pointer-bump hint to stdout.
4. User reviews the report. For each PORT/EVALUATE candidate: open a fresh Claude session and paste PR# + parent MEMORY excerpt to do the actual port work.
5. User runs `--bump-to <hash>` once satisfied that all PRs in the scanned range have been decided on.

### Per-PR pipeline

```text
classify_pr(pr) → bucket
  ↓
extract_memory_entry(pr.number) → str | None
  ↓
detect_already_applied(pr) → Confidence
  ↓
translate_paths(pr.files) → list[WifeyPath]
  ↓
suggest_approach(pr, bucket, confidence) → str
  ↓
emit detail block
```

### Report shape

**Header** (top of file):

```markdown
# Parent sync report — 2026-05-23

**Range**: 635ed5a..f3a9c2b
**PRs found**: 24
**Pointer-bump command**: `poetry run python tools/sync_parent.py --bump-to f3a9c2b`

## Summary
| Bucket | Count |
|---|---|
| SKIP            | 8 |
| PORT            | 6 |
| EVALUATE        | 7 |
| ALREADY-APPLIED | 3 |
```

**Four sections in order**:

- `## SKIP` — compact table: `PR# | Title | Reason`
- `## PORT` — detail blocks (one per PR)
- `## EVALUATE` — detail blocks (one per PR)
- `## ALREADY-APPLIED` — compact table: `PR# | Title | Confidence | Verify against`

**Per-PR detail block** (PORT and EVALUATE only):

```markdown
## PR #123 — "feat: tighten regime gate thresholds"

- **Bucket**: PORT
- **Confidence already-applied**: LOW
- **Parent commit range**: abc1234..def5678
- **Files touched in parent**:
  - `analytics/regime.py` → wifey: `analytics/regime.py` (direct)
  - `tests/test_regime.py` → wifey: `tests/test_regime.py` (direct)
- **Suggested approach**: cherry-pick-with-edits
- **Parent MEMORY excerpt**:
  > Regime gate thresholds were too tight on 1h — false-positive trend
  > classification killed mean-reversion pairs. New thresholds: ADX 25→22,
  > BBW 0.02→0.025. Validated on 4-symbol cohort, +0.12R uplift.
- **Link**: https://github.com/s10023/buibui-moon-trader-bot/pull/123
```

## Error handling

### Fail-fast errors (exit 1, actionable message)

| Condition | Detection | Message |
| --- | --- | --- |
| Parent repo path missing | `not PARENT_REPO_PATH.exists()` | "Parent repo not found at `<path>`. Clone it first or update `PARENT_REPO_PATH`." |
| Parent not on `main` | `git branch --show-current` ≠ `main` | "Parent must be on `main`. Currently on `<branch>`." |
| Parent fetch fails | non-zero exit from `git fetch` | "Could not fetch parent. Re-run with `--no-fetch` to use local refs." |
| Memory state file malformed | YAML frontmatter parse fails | "Sync state file at `<path>` is malformed. Inspect or delete to re-bootstrap." |
| Last-sync hash not in parent | `git cat-file -e <hash>` fails | "Last-sync hash not found in parent (history rewrite?). Pass `--from <known-hash>`." |
| `/tmp` not writable | `OSError` on report write | "Cannot write report. Check `/tmp` permissions." |
| `--bump-to` hash not in parent | `git cat-file -e <hash>` fails | "Pointer-bump target not in parent history; refusing to bump." |

### Graceful degradation (continue, mark in report)

| Condition | Behavior |
| --- | --- |
| Memory state file missing | Bootstrap mode: use `FORK_COMMIT` as `from`. Print `"BOOTSTRAP: scanning full fork → HEAD"`. |
| Commit not in a PR (direct-to-main) | Synthetic "PR" entry with `#none` and "direct commit" label. |
| MEMORY.md entry not found for PR | Detail block shows `**Parent MEMORY excerpt**: (none found)`. |
| Path in parent not in `PARENT_TO_WIFEY_PATHS` | Wifey-path column shows `(unmapped — investigate)`. Bucket falls back to `EVALUATE`. |
| Symbol extraction yields nothing usable | `Confidence: UNKNOWN`. Skip the grep. |
| `git grep` fails (regex / escape issue) | Catch, mark symbol as `UNKNOWN`. Don't crash the run. |

### Idempotency

- Read-only on both repos except for the memory state file (written only on `--bump-to`).
- Re-running the same `--from` produces the same report.
- `--bump-to` is the only mutating operation; performs a single atomic file rewrite.

### Skill-level error policy

Fail-fast errors → surface message to user, do NOT auto-attempt fixes (no auto-clone, no auto-checkout, no auto-bump). All require user judgment. Graceful warnings → flag in summary but don't block report generation.

## Testing

### Unit tests in `tests/test_sync_parent.py`

Pure-stdlib + `pytest` + `pytest-mock`. All subprocess calls mocked — no real git, no network, no real parent repo.

| Test class | Coverage |
| --- | --- |
| `TestBucketClassifier` | `classify_pr` returns correct bucket for crypto-only (SKIP), surviving paths (PORT), sweep findings (EVALUATE), unmapped paths (EVALUATE fallback) |
| `TestPRGrouping` | `group_into_prs` parses merge-commit format, squash format, direct-to-main commits (synthetic `#none`) |
| `TestPathTranslation` | `translate_paths`: mapped → wifey path, `None` → SKIP, missing key → `(unmapped)` |
| `TestAlreadyApplied` | `detect_already_applied` HIGH if all symbols match, LOW if none, MEDIUM if partial, UNKNOWN when no symbols extractable |
| `TestSuggestApproach` | Heuristic: `verify-only` (HIGH confidence), `cherry-pick-with-edits` (surviving paths only), `re-implement` (heavy translation) |
| `TestMemoryExtract` | `extract_memory_entry` finds PR #N reference; returns `None` when not found |
| `TestStateFile` | `load_sync_state` reads YAML frontmatter; bootstraps to `FORK_COMMIT` if missing; raises on malformed YAML |
| `TestCLI` | Argparse handles all flags; `--bump-to` rejects unknown hash |

### Fixtures in `tests/fixtures/sync_parent/`

- `sample_git_log.txt` — captured `git log --merges` output with a mix of merge & squash PRs
- `sample_memory.md` — minimal parent MEMORY.md with 2–3 PR-referenced entries
- `sample_diff.txt` — captured `git show --name-only` output for one PR

### Integration smoke test

`TestSmokeRun` — runs the full `main()` flow against fixture data with `subprocess` mocked. Asserts:

- Report file written to `tmp_path / "parent-sync-<date>.md"`
- Contains expected sections (`# Parent sync report`, `## Summary`, `## SKIP`, `## PORT`, `## EVALUATE`, `## ALREADY-APPLIED`)
- Bucket counts match fixture expectations
- Pointer-bump hint included

### Out of scope for automated tests

- Real git log on parent repo — environment-dependent
- Actual `git grep` against wifey — too slow + working-tree-dependent
- The SKILL.md markdown — instructions for Claude, smoke-tested manually on first invocation
- Real network fetch — mocked

### Lint / type expectations

- `make lint-py` (ruff format + lint) clean
- `make typecheck` (mypy strict) clean — `tools/sync_parent.py` joins the strict file list (191 → 192)
- `make test` adds ~20–30 new test methods across 8 test classes (1048 → ~1070 baseline)

### Manual smoke test (one-time, after first build)

- Run `make wifey-sync-parent` with parent at fork commit (no new history) → empty PR list, no `/tmp` file.
- Advance parent in a scratch branch with one synthetic PR; re-run → expect 1 PR in report.

## Implementation order hint

For the writing-plans skill:

1. State file + sync pointer (foundational; required by everything else)
2. Git log parsing + PR grouping (input pipeline)
3. Bucket classifier + path translation (core logic)
4. Already-applied content-grep + MEMORY.md extraction (enrichment)
5. Report formatter (output stage)
6. CLI + Makefile target
7. SKILL.md (assembled after the helper works)
8. Tests in parallel with each module (TDD-friendly)
9. Manual smoke test on first real parent fetch

## References

- Fork lineage: parent at `s10023/buibui-moon-trader-bot`, fork commit `635ed5a`, fork date 2026-05-14.
- Parent memory: `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`
- Wifey memory: `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/MEMORY.md`
- Existing skill convention: `.claude/skills/sanity-check/SKILL.md`, `.claude/skills/atr-sweep/SKILL.md`
- Related design context: `docs/superpowers/specs/2026-04-10-tradfi-equity-fork-design.md`
