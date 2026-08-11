---
name: sync-parent
description: >
  Detect-and-recommend pipeline for porting upstream parent-repo
  (buibui-moon-trader-bot) changes into the wifey fork. Scans parent PRs merged
  since the last sync point, classifies each SKIP / PORT / EVALUATE /
  ALREADY-APPLIED, and writes a context-rich report to
  docs/plans/parent-sync/parent-sync-<date>.md.
  Invoke when the user says "/sync-parent", asks to "check the parent repo",
  "port upstream changes", "what changed in the parent", or after a known parent
  refactor.
allowed-tools: Bash, Read
---

# Sync from parent repo

Read-only catch-up tool. Surfaces parent-repo PRs that may be worth porting into
wifey, classifies them, and enriches each with the parent MEMORY excerpt + a
suggested approach. **It never edits wifey code** — a human ports.

## When to use

- Periodic catch-up with the parent repo.
- Before or after a known upstream refactor.
- Bootstrap (first run) — scans the full fork → HEAD range.

## Prerequisites

- Parent clone present at `/home/kng/repo/buibui-moon-trader-bot` with a readable
  `origin/main`. **Its checked-out branch does not matter** — every parent read is
  ref-based against `origin/main` and nothing touches the parent working tree, so
  the parent can stay parked on a feature branch while you scan.
- State file `project_parent_sync_state.md` exists in wifey memory, or the skill
  bootstraps from the fork commit `635ed5a` on first run.

## Invocation

```bash
make wifey-sync-parent                 # incremental from the state pointer
make wifey-sync-parent FULL=1          # full fork -> HEAD audit
make wifey-sync-parent NO_FETCH=1      # local refs only (offline)
make wifey-sync-parent FROM=<hash>     # override start point
make wifey-sync-parent BUMP_TO=<hash>  # advance the pointer, no scan
```

Direct: `PYTHONPATH=. poetry run python tools/sync_parent.py [flags]`.

## Output

- `docs/plans/parent-sync/parent-sync-<date>.md` — bucket summary table, a
  **Workstreams** table, then four sections (SKIP / PORT / EVALUATE /
  ALREADY-APPLIED). PORT and EVALUATE entries are full detail blocks with parent
  paths → wifey targets, the parent MEMORY excerpt, and a suggested approach
  (`verify-only` / `cherry-pick-with-edits` / `re-implement`).
  **In-repo and gitignored, deliberately not `/tmp`** — the report is the triage
  artifact and a review spans days. A `/tmp` clear destroyed the 2026-07-29 report
  with 57 of 67 PRs still undecided, forcing a full re-scan.
- The **Workstreams** table clusters the range's PRs into multi-PR campaigns
  (e.g. a `live-parity (backtest engine port)` row spanning 6 PRs) so a large
  range reads as a handful of themes rather than a flat PR list. Clustering is
  driven by `WORKSTREAM_RULES` in `tools/sync_parent.py` — ordered (regex, label)
  rules with a conventional-commit `type(scope)` fallback. Add a rule when the
  parent starts a new multi-PR campaign whose subjects don't already cluster.
- A pointer-bump hint on stdout.

## Workflow

1. Run `make wifey-sync-parent`. If it reports a fail-fast error (parent missing,
   no readable `origin/main`, malformed state), **surface it to the user and stop**
   — do not auto-clone, auto-fetch, or auto-bump.
2. Read `docs/plans/parent-sync/parent-sync-<date>.md`. Summarise the
   **Workstreams** table (the major multi-PR campaigns) and the bucket counts for
   the user.
3. For each **PORT** / **EVALUATE** candidate the user wants: open a fresh Claude
   session, paste the PR number + the parent MEMORY excerpt from the report, and
   do the actual port work there (this skill does not edit code).
4. Once the user confirms every PR in the range has been decided, advance the
   pointer: `make wifey-sync-parent BUMP_TO=<to_hash>` (the exact command is
   printed at the end of the report).

## Notes

- **Never hand-triage from `git log --oneline`. Re-run the tool when the range moves.**
  On 2026-08-03 the parent was 21 commits past the last scan point; those commits were
  eyeballed from a `git log` listing instead of re-running `make wifey-sync-parent`, and
  #519 (a portable CLAUDE.md policy) was read past. The tool would have bucketed it
  **PORT** — `CLAUDE.md` exists in wifey, so it resolves as a direct path, which is
  neither `removed`/`skip` nor an EVALUATE path. Selective reading is exactly the
  failure this skill exists to prevent.
- **The parent's own "what's fork-ready" note is a hint, never the scope.** The parent's
  memory sometimes names a payload it thinks is portable. That reflects *its* view of
  its own work; it is not a substitute for this repo's classifier, and anything outside
  that list silently drops. Same 2026-08-03 incident: the named payload was taken as the
  scope, so five ingest PRs were only found by pulling an unrelated thread, and #519 was
  never on any thread.
- **Docs-only PRs are not automatically SKIP.** A parent PR touching only `CLAUDE.md` or
  `.claude/skills/**` changes how every future session behaves, which is higher leverage
  than most code. Beware subjects that sound repo-local but aren't: #519 reads as "cap
  the memory index" and memory lives *outside* the repo — but the **policy** lives in
  `CLAUDE.md`, which is in-repo and fully portable. Only the data is external.
- The parent squash-merges every PR (one commit, `(#N)` suffix) — there are no
  merge commits, which is why the tool groups by subject, not `git log --merges`.
- ALREADY-APPLIED is a confidence flag, never an auto-removal. Always verify.
- Sweep findings (`tp_r`, ATR multipliers) land in EVALUATE: methodology may
  transfer, values won't (equity cohort ≠ crypto cohort).
