---
name: sync-parent
description: >
  Detect-and-recommend pipeline for porting upstream parent-repo
  (buibui-moon-trader-bot) changes into the wifey fork. Scans parent PRs merged
  since the last sync point, classifies each SKIP / PORT / EVALUATE /
  ALREADY-APPLIED, and writes a context-rich report to /tmp/parent-sync-<date>.md.
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

- Parent clone present at `/home/kng/repo/buibui-moon-trader-bot`, checked out on `main`.
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

- `/tmp/parent-sync-<date>.md` — summary table + four sections (SKIP / PORT /
  EVALUATE / ALREADY-APPLIED). PORT and EVALUATE entries are full detail blocks
  with parent paths → wifey targets, the parent MEMORY excerpt, and a suggested
  approach (`verify-only` / `cherry-pick-with-edits` / `re-implement`).
- A pointer-bump hint on stdout.

## Workflow

1. Run `make wifey-sync-parent`. If it reports a fail-fast error (parent missing,
   not on `main`, malformed state), **surface it to the user and stop** — do not
   auto-clone, auto-checkout, or auto-bump.
2. Read `/tmp/parent-sync-<date>.md`. Summarise the bucket counts for the user.
3. For each **PORT** / **EVALUATE** candidate the user wants: open a fresh Claude
   session, paste the PR number + the parent MEMORY excerpt from the report, and
   do the actual port work there (this skill does not edit code).
4. Once the user confirms every PR in the range has been decided, advance the
   pointer: `make wifey-sync-parent BUMP_TO=<to_hash>` (the exact command is
   printed at the end of the report).

## Notes

- The parent squash-merges every PR (one commit, `(#N)` suffix) — there are no
  merge commits, which is why the tool groups by subject, not `git log --merges`.
- ALREADY-APPLIED is a confidence flag, never an auto-removal. Always verify.
- Sweep findings (`tp_r`, ATR multipliers) land in EVALUATE: methodology may
  transfer, values won't (equity cohort ≠ crypto cohort).
