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
effort: high
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

- Parent clone present as a **sibling of this checkout** (`../buibui-moon-trader-bot`)
  with a readable `origin/main`. `tools/sync_parent.py` derives the path this way rather
  than from a literal, so it holds across hosts. Its checked-out branch does not
  matter — every parent read is ref-based against `origin/main` and nothing touches the
  parent working tree, so the parent can stay parked on a feature branch while you scan.
- State file `project_parent_sync_state.md` exists in wifey memory, or the skill
  bootstraps from the fork commit `635ed5a` on first run.

## Invocation

```bash
make wifey-sync-parent                 # incremental from the state pointer
make wifey-sync-parent FULL=1          # full fork -> HEAD audit
make wifey-sync-parent NO_FETCH=1      # local refs only (offline)
make wifey-sync-parent FROM=<hash>     # override start point
make wifey-sync-parent BUMP_TO=<hash>  # DO NOT USE — see Workflow step 5
```

Direct: `PYTHONPATH=. poetry run python tools/sync_parent.py [flags]`.

**`BUMP_TO=` rewrites `project_parent_sync_state.md` to a stub and wipes the triage
body.** Advance the pointer by hand-editing `last_synced_hash` in that file's
frontmatter instead. The flag is documented here only so it is recognised and refused.

## Output

- `docs/plans/parent-sync/parent-sync-<date>.md` — bucket summary table, a
  **Workstreams** table, then four sections (SKIP / PORT / EVALUATE /
  ALREADY-APPLIED). PORT and EVALUATE entries are full detail blocks with parent
  paths → wifey targets, the parent MEMORY excerpt, and a suggested approach
  (`verify-only` / `cherry-pick-with-edits` / `re-implement`).
  **In-repo and gitignored, deliberately not `/tmp`** — the report is the triage
  artifact and a review spans days, and `/tmp` clears on reboot.
- The **Workstreams** table clusters the range's PRs into multi-PR campaigns
  (e.g. a `live-parity (backtest engine port)` row spanning several PRs) so a large
  range reads as a handful of themes rather than a flat PR list. Clustering is
  driven by `WORKSTREAM_RULES` in `tools/sync_parent.py` — ordered (regex, label)
  rules with a conventional-commit `type(scope)` fallback. Add a rule when the
  parent starts a new multi-PR campaign whose subjects don't already cluster.
- A pointer-bump hint on stdout.

## Workflow

1. Run `make wifey-sync-parent`. If it reports a fail-fast error (parent missing,
   no readable `origin/main`, malformed state), surface it to the user and stop
   — do not auto-clone, auto-fetch, or auto-bump.

   **Re-derive the range every run; don't trust a carried shortlist.** A handoff
   naming "the remaining candidates" describes the parent as of the last scan, and the
   parent keeps merging in the meantime. State the count both ways every run: *"167 PRs
   in range, 74 undecided, 11 never triaged."*

   **Derive "never triaged" by diffing reports, not by grepping the state file.** When
   the pointer was held, the range restarts at it and re-lists PRs an earlier round
   already ruled. Those rulings often sit in that round's report rather than in
   `project_parent_sync_state.md`, so a grep for `#NNN` there reads ruled PRs as new.
   Compare the PR sets of the previous `parent-sync-<date>.md` and this one; the PRs
   only in the new report are the untriaged set.
2. **Run both portability filters, and say which findings came from which.** They have
   opposite blind spots, so either one alone produces a shortlist that silently omits a
   whole class:
   - **File-existence** (the report's own bucketing): does the parent's changed
     path exist here? Kills false ports; scores every **greenfield** port ~0,
     because a new file cannot exist in wifey yet.

     **A bucket label is not a ruling.** The classifier defaults to `EVALUATE`
     whenever a path resolves, so most modify-only PRs carry a label and no ruling —
     the import filter sees ADDED files only, so no scan closes that gap. Run the
     file-existence pass over every remaining PR, and bucket by **substantive
     surface**, which is not the same as presence:

     | Surface | Reading |
     | --- | --- |
     | `README.md`, `CLAUDE.md`, `.claude/context/*.md`, `*/INDEX.md`, `docs/plans/*` | **Not port surface.** Present in both repos *by construction*, so presence proves nothing |
     | `Makefile`, `.github/`, `deploy/`, `tools/`, `analytics/`, `signals/`, `tests/`, `.claude/skills/` | **Real surface.** These carry shared mechanism |
     | `poetry.lock`, `pyproject.toml` alone | **Never a port** — dependabot runs independently per repo |

     Bucketing by substantive surface, not mere presence, is what turns false
     "partials" into real candidates. Ruling buckets are **PORT** (defect verified
     present here) · **ALREADY-APPLIED** · **EVALUATE** (judgement, not a missing
     fact) · **NO PORT**. Expect ALREADY-APPLIED to be large — some of it is the
     parent porting *wifey's* work back. **A port queue is a claim about the fork, not
     a record of it**, so verify each against an artifact in this tree, never against
     the note that recorded it.
   - **Import-dependency**: `PYTHONPATH=. python docs/plans/scripts/missed_ports.py`,
     using this repo's venv interpreter (`.venv/Scripts/python.exe` on Windows,
     `.venv/bin/python` on Linux)
     — does the new module's import set resolve against wifey? Finds greenfield
     ports; silent on modify-only PRs.

     **Confirm it printed a scan range before trusting the shortlist — this filter
     can fail silently and look like a clean result.** It is gitignored, so no lint,
     test or CI leg reaches it, and a shortlist built without it is blind to every
     greenfield port. A working run prints `scan range: <from>..origin/main`; anything
     else is a dead filter, not an empty result. It reads the range from
     `last_synced_hash` in `memory/project_parent_sync_state.md`, so never edit a
     range into the script — `--range` is for a deliberate wider scan only. **Prune
     its `PORTED` set from the run's own dead-entry report, never from memory**: every
     run prints the entries falling outside the scanned range. Do not delete the
     `PORTED` set outright — the rulings in that memory are prose, so the set cannot
     be derived, and its staleness is only *visible*, not self-correcting.

   Both filters share a **third** blind spot: a file with no imports at all scores
   `SELF-CONTAINED` no matter what it wraps — a `SKILL.md` has no Python imports, so
   the import filter can promote a skill-only PR as a ready greenfield port even when
   the module or CLI it wraps does not exist here. **For a docs-only or skill-only PR,
   check that its subject exists here, not its imports**: read the commands and
   modules the prose invokes and confirm each resolves.

   **What this scan structurally cannot see.** It keys on **merged** parent PRs and on
   paths, so four classes score zero hits while being real work. Check these by hand
   each run:
   - **An unmerged parent PR.** By the time a PR appears in the merged-range scan, the
     window in which you needed to know about it may have already closed — check
     `gh pr list --state open` on the parent, not just the merged range.
   - **A prose-only convention** that lives in a `CLAUDE.md` or `SKILL.md` paragraph
     and changes no path wifey watches.
   - **A measurement** rather than a change — a number the parent derived that alters
     a decision here.
   - **A rule whose reason is repo-specific** (see below).

   **Port the rule, re-derive the reason.** A rationale is a claim about *this*
   repo's costs, coverage and constraints — verify it here before writing it down,
   even when the rule itself transfers unchanged. The parent's regression-gate bullet
   justifies a path list because the gate is expensive there; here
   `make test-regression` runs in ~8s, so the identical rule needs the *opposite*
   justification — the list marks a coverage gap, not a cost. Copying the reason
   verbatim would teach the next session to skip a gate that is nearly free. **A wrong
   reason is worse than a wrong number, because it is not checkable against
   anything.**

   Don't stop at the Workstreams table and the bucket counts — at a wide range they
   carry almost no signal, since the classifier defaults to EVALUATE whenever a path
   resolves. Stopping there is what produces a shortlist blind to every greenfield
   port.
3. **Neither filter is evidence the defect exists here — that is a third question.**
   Before writing any code, enumerate the upstream fix's **preconditions** one at a
   time and check each against wifey; a high-overlap, correct-upstream candidate can
   still be inert here if its preconditions fail. Where the port is warranted,
   re-derive its measured impact **on this repo's data** — an upstream count is never
   wifey's, and "preventive, not a repair" is a legitimate finding.
4. For each **PORT** / **EVALUATE** candidate the user wants: open a fresh Claude
   session, paste the PR number + the parent MEMORY excerpt from the report, and do
   the actual port work there (this skill does not edit code). Two rules the report
   cannot express, because both are about the parent's state *now* rather than at the
   merge commit:

   - **When the target is greenfield, port at parent `HEAD`, not at the PR.** The
     report names "PR #N", which reads as an instruction to take that commit — but a
     new file keeps being fixed after it lands, and every one of those follow-ups is
     greenfield too. Run `git log <merge>..origin/main -- <path>` first and take the
     file at `HEAD`. **Re-check any expired NOT-PORTABLE ruling in the same pass** — a
     prior ruling may hold only in the absence of a file that a later parent PR added.
   - **A ported doc inverts its cross-repo direction, and nothing mechanical catches
     it.** Lint, tests, and path checks are all silent on prose that is simply about
     the other repo. Grep every ported doc for `here` / `our` / `this repo` /
     `the sibling` — **and read the example data**, which can carry the same
     inversion (an example follow list or symbol that names the wrong repo).
5. Once the user confirms every PR in the range has been decided, advance the
   pointer. **Bump by hand-editing the `last_synced_hash` in
   `memory/project_parent_sync_state.md`'s frontmatter — never `BUMP_TO=` /
   `--bump-to`**, which rewrites the file to a stub and wipes the triage body. Never
   bump while PRs in the range are still undecided: the next scan starts from the
   pointer, so an early bump drops them from view permanently.

## Notes

- **Never hand-triage from `git log --oneline`. Re-run the tool when the range moves.**
  Eyeballing a `git log` listing instead of re-running `make wifey-sync-parent` reads
  past PRs the tool would have bucketed correctly — selective reading is exactly the
  failure this skill exists to prevent.
- **The parent's own "what's fork-ready" note is a hint, never the scope.** The
  parent's memory sometimes names a payload it thinks is portable. That reflects
  *its* view of its own work; it is not a substitute for this repo's classifier, and
  anything outside that list silently drops.
- **Docs-only PRs are not automatically SKIP.** A parent PR touching only `CLAUDE.md`
  or `.claude/skills/**` changes how every future session behaves, which is higher
  leverage than most code. Beware subjects that sound repo-local but aren't: memory
  lives *outside* the repo, but a **policy about** memory (a cap, a protocol) lives in
  `CLAUDE.md`, which is in-repo and fully portable — only the data is external.
- The parent squash-merges every PR (one commit, `(#N)` suffix) — there are no
  merge commits, which is why the tool groups by subject, not `git log --merges`.
- ALREADY-APPLIED is a confidence flag, never an auto-removal. Always verify.
- **A modified symbol's name is not evidence the port landed.** A signature-only
  change re-emits its own `def` line, so the name sits on both sides of the diff and a
  name-presence grep matches the *old* copy. `extract_symbol_changes` splits `added`
  from `modified`, and for a modified symbol the evidence is the **identifiers the
  change introduced**, not the name. A modify-only PR that introduces no new
  identifier reports **UNKNOWN** — "cannot tell", never "applied".
- **A citation is not evidence either.** A wifey PR can cite a parent PR number while
  porting only part of its payload, so any check keyed on PR numbers can score the
  whole parent PR applied with part of it still missing. Key on files and symbols,
  never on the number.
- Sweep findings (`tp_r`, ATR multipliers) land in EVALUATE: methodology may
  transfer, values won't (equity cohort ≠ crypto cohort).

## Stamp the run — the final step

```bash
make cadence-stamp TASK=sync-parent
```

Stamp after the scan, not after every PR is decided — the cadence being tracked is
*did we look*, and the pointer already records *what was decided*.
`make cadence-check` reports this skill OVERDUE at 7d, and a missing mark reads as
overdue on purpose: nothing in the normal workflow otherwise says the parent has moved.
