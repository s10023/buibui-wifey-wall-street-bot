---
name: post-branch
description: >
  Post-branch docs sweep and handoff: diff the branch's behaviour changes against
  the doc surfaces, propose edits where they drifted, run the pre-merge readiness
  check and write the fresh-conversation handoff. Run it before `gh pr create`,
  while the branch is local-only, and fold its "Documentation updates" section
  into the initial PR body. Its behaviour gate skips pure refactors, bug fixes
  covered by tests, dependency bumps and lint-only commits. Confirm every edit
  before writing. Also triggers on "/post-branch", "wrap up the branch", "docs
  check", "pre-merge check" or "next conversation prompt".
allowed-tools: Bash, Read, Edit, Write
effort: high
---

# Post-Branch Docs Sweep

A PR's diff is the source of truth for what changed. The docs are claims about
how the codebase behaves. After a PR adds flags, scripts, defaults, files or
commands, those claims go stale — often silently. This skill walks the doc
surfaces, surfaces the drift, and proposes edits.

It runs before the PR exists, on a branch that is committed but not opened. Detail: [references/sweep-checks.md](references/sweep-checks.md#why-the-sweep-runs-before-the-pr).

## Running order

Phases, not step numbers — order matters, and phases run in the order below.

**Watch for a phase whose output depends on a fact a later phase creates.** The
handoff is written before the PR-state rewrite, the claims audit before the commit it
audits, and MEMORY.md's `#NNN` before the PR exists. Don't reorder the whole phase to
fix one of these: name the single field that lands later, and the phase that fills it.

| Phase | What | Costs CI? |
| --- | --- | --- |
| **1** | `make post-branch-checks` — every mechanical check | no |
| **2** | Behaviour gate — is this PR user-facing? | no |
| **3** | Doc walk — judgement, keyed off the diff | no |
| **4** | Always-run: MEMORY.md, Issue reconcile, claims audit | no |
| **5** | Commit + push, then compose the PR body, then `gh pr create` | one run |
| **6** | Pre-merge check, handoff, **re-verify PR state last** | no |

Phases 1 and 4 run **regardless** of the phase-2 gate. MEMORY.md lives outside the
repo, the handoff is gitignored and Issues are not commits, so none of them ever costs
CI.

If phase 1 and the doc edits already ran on this HEAD, do not re-walk them: re-run only the legs a later edit touches (`PYTHONPATH=. poetry run python tools/post_branch_checks.py --check <leg>`, repeatable) and say in the output block which phases were inherited and from which commit. Phases 4–6 still run in full. Detail: [references/sweep-checks.md](references/sweep-checks.md#inherited-phases).

## Gotchas

- **Sweep before the PR.** Every push to an open PR re-runs the whole matrix, so this runs while the branch is local-only ([why](references/ci-visibility-flip.md#the-visibility-flip-decision)).
- **Vacuous legs.** `memory-cap` and `handoff-size` score the previous session on the phase-1 run; re-run after phase 6 ([detail](references/sweep-checks.md#vacuous-legs-and-not-configured)).
- **`NOT CONFIGURED` is a finding.** An absent `.claude/sensitive-terms.txt` is not a pass ([detail](references/sweep-checks.md#vacuous-legs-and-not-configured)).
- **PR text is a fourth surface.** The sweep cannot see a title or body; screen them with `make post-branch-text`, repo-relative `FILE=`, chained with `&&` ([detail](references/sweep-checks.md#screening-a-pr-title-and-body)).
- **A name grep misses removals and stale values.** Grep the constrained artifact and compare values, not keys ([detail](references/doc-walk.md#what-the-name-grep-misses)).
- **Memory cap is bytes too.** Re-home the largest bullet before trimming the new one ([detail](references/reconcile-and-claims.md#memorymd-cap)).
- **Commit messages are audited prose.** Amend an unpushed commit; never add a wording commit ([detail](references/reconcile-and-claims.md#commit-messages-and-comments-are-audited-prose)).
- **Claims need a reproduction.** Hunt the six shapes, above all a rate with no null and a set-wide claim from a spot check ([detail](references/reconcile-and-claims.md#the-six-shapes)).
- **The flip is about the account's allowance, not the diff.** Confirm it with the user every time ([detail](references/ci-visibility-flip.md#the-visibility-flip-decision)).
- **`steps=0` is billing, not code.** `FAILURE` at `steps=0` means a spent allowance; `SKIPPED` is a path filter ([detail](references/ci-visibility-flip.md#three-traps-in-a-hand-rolled-waiter)).
- **Do not flip back early.** Wait for `make wait-ci-main`; `Regression tests` is created about 4 minutes in ([detail](references/ci-visibility-flip.md#why-merging-is-not-the-last-step)).
- **Enumerate runs to requeue.** Never assume a count, and a short SHA silently returns zero runs ([detail](references/ci-visibility-flip.md#enumerate-the-runs)).
- **Targeted `Edit`s on the handoff.** A `Write` destroys its standing back half ([detail](references/handoff.md#four-labelled-groups)).
- **Operator actions must resolve here.** `buibui-signal-watch.*` is the parent's unit ([detail](references/handoff.md#operator-actions-must-resolve-in-this-repo)).
- **Re-verify PR state last.** Fill or strike MEMORY.md's `#NNN` from the same query ([detail](references/handoff.md#why-the-pr-state-goes-stale)).

---

## Phase 1 — Run the mechanical sweep

```bash
make post-branch-checks
```

Thirteen checks, one command, in `tools/post_branch_checks.py`. A hand walk is not the
walk — run the check rather than reproducing it by eye.

The thirteen checks are `queue-items`, `handoff-symbols`, `new-files`, `new-modules`, `new-targets`, `amended-targets`, `negative-claims`, `doc-indexes`, `md-atx`, `memory-cap`, `handoff-size`, `stale-anchors` and `sensitive-terms`; what each asks is in the table. Detail: [references/sweep-checks.md](references/sweep-checks.md#the-thirteen-checks).

**`memory-cap` and `handoff-size` are vacuous on the phase-1 run; re-run `make post-branch-checks` after phase 6 and read them then. `sensitive-terms` reading `NOT CONFIGURED` is a finding, not a skip.** Detail: [references/sweep-checks.md](references/sweep-checks.md#vacuous-legs-and-not-configured).

**Those three legs cannot see a PR title or body — screen it with `--text` before you
post.** A body is neither the tree nor a commit, so the sweep reports `clean` on one
naming every term: correctly, and uselessly. Write the body to a file (which CLAUDE.md
requires anyway — a heredoc is the command payload and trips the destructive guard),
then:

```bash
make post-branch-text FILE=docs/plans/pr-<branch>.md
printf '%s' "$TITLE" | make post-branch-text FILE=-
```

Pass a repo-relative `FILE=` (or pipe with `FILE=-`), never an absolute Windows path, and chain the post behind the screen with `&&`. Detail: [references/sweep-checks.md](references/sweep-checks.md#repo-relative-file-and-chaining).

`FILE=-` reads stdin. The screen gates (exit 1 on a hit, make shows 2), so read the banner. It belongs in phase 5, beside `make preflight`, before `gh pr create`. Detail: [references/sweep-checks.md](references/sweep-checks.md#screening-a-pr-title-and-body).

Every finding is a candidate to dismiss in seconds, never an automatic edit; `queue-items` reports relevance, not closure. Detail: [references/sweep-checks.md](references/sweep-checks.md#how-the-checks-behave).

Triage a presence check as a prompt to judge, not an automatic edit: `trade/` is not a finding, renames are not covered, `docker-compose.yml` is checked by hand, and `negative-claims`' `note:` line carries one remainder that is work. Detail: [references/sweep-checks.md](references/sweep-checks.md#triaging-the-presence-checks).

### What the sweep deliberately does NOT cover

Judgement, all of it: the behaviour gate, the doc walk, the discovered-fact
sweep, the claims audit, the Issue reconcile, and everything touching `gh`. Those
are phases 2–6. A green sweep is not a green branch.

---

## Phase 2 — Behaviour gate: is this PR user-facing?

Read the diff (`git diff main --stat`, then `git diff main`). Use
**`git diff main`**, two dots — this skill runs before its own commit, so
`main...HEAD` diffs two identical trees and every check passes *vacuously*.

**Walk the docs** if any of these are present:

- New CLI subcommand or flag; new Make target or changed default
- New TOML key, changed default, or new environment variable
- Renamed/moved file referenced from docs; new error class, exit code or alert format
- New external dependency or system requirement
- Behaviour change to an existing public command
- New long-running daemon or one-shot tool
- The diff touches `analytics/store/schema.py` (see below)
- Anything on the notification list (see below)

Path heuristics: `wifey.py`, `cli/`, `Makefile`, `pyproject.toml`, the TOML configs, `deploy/**` and the workflows almost always need a walk; `tests/**` and fixtures rarely do. A new public symbol is user-facing even under `analytics/**`. Detail: [references/sweep-checks.md](references/sweep-checks.md#behaviour-signal-globs).

**Stop after phase 4** if the PR is purely: an internal refactor preserving the
public API, a bug fix with a regression test and no behaviour change, a
dependency bump, test-only changes, docstring edits inside source files, or a
regression-fixture refresh with goldens unchanged.

**Judge "lint-only" on what the change enforces** (linter config, CI globs), not on how cosmetic the diff looks. Detail: [references/sweep-checks.md](references/sweep-checks.md#lint-only-means-formatting).

**Strong refactor signals** (a Project Structure module renamed, moved or shimmed; the CLI surface changed; a new `make wifey-*` target) mean walk the docs; when in doubt, ask. Detail: [references/sweep-checks.md](references/sweep-checks.md#strong-refactor-signals).

### DB migrations are operator-facing even when nothing else is

State in the handoff when the migration applies, whether existing rows stay readable, whether a manual step is needed, and whether it is self-healing and why. Confirm `tests/test_schema_insert_arity.py` still passes. Detail: [references/sweep-checks.md](references/sweep-checks.md#db-migrations-are-operator-facing).

### Notification surface — decide it, never default to it

Needs an explicit decision if the PR adds or changes a scheduled job, an irreversible or outward-facing action, a latching state transition, a failure path visible only in an unread log, or a periodic summary a human must act on. Record one of four verdicts: `always`, `on-change`, `on-failure-only` or `never` (state why in one line). Detail: [references/sweep-checks.md](references/sweep-checks.md#notification-verdicts).

A channel whose only signal is failure is unfalsifiable: if a path is `on-failure-only`, confirm something else proves it alive. Detail: [references/sweep-checks.md](references/sweep-checks.md#failure-only-channels).

---

## Phase 3 — Walk each doc surface

```yaml
surfaces:
  - {id: claude_md,   path: CLAUDE.md,          purpose: project structure, commands, footguns, verdicts}
  - {id: readme,      path: README.md,          purpose: CLI surface, install, quickstart}
  - {id: memory_md,   path: <memory>/MEMORY.md, purpose: Current State, always_update: true, unprompted: true}
  - {id: makefile,    path: Makefile,           scope: any_referencing_changed_artifact}
  - {id: compose,     path: docker-compose.yml, scope: any_referencing_changed_artifact}
  - {id: context,     glob: .claude/context/*.md,      scope: + new_module_presence}
  - {id: skills,      glob: .claude/skills/*/SKILL.md, scope: any_referencing_changed_artifact}
  - {id: handoff,     path: docs/plans/next-conversation-prompt.md, written_at: phase_6, unprompted: true}
```

**When porting this skill to another repo, edit only that block.**

For each surface: locate it, read it, decide if an edit is warranted, **propose
it as a diff and wait for confirmation**, then apply with `Edit` — never `Write`.
The two `unprompted: true` surfaces are the exception and are written without
asking; the rail in **Safety rails** carries the authority for that. Bias to
minimal, targeted edits. Look for outdated examples, missing entries,
broken paths, stale defaults, stale module-purpose descriptions, and:

Three traps the name grep misses: a stale value under a correct key, a removal with no new symbol to grep, and a changed verdict (grep distinctive tokens, not the filename). Also diff the doc's claim against the pre-fix code. Detail: [references/doc-walk.md](references/doc-walk.md#what-the-name-grep-misses).

**Rewrote or compressed a doc? Diff the identifiers, not the prose.** Detail: [references/doc-walk.md](references/doc-walk.md#diff-the-identifiers).

```bash
NEW=<path>                 # the doc as it stands on this branch
OLD=/tmp/old.md
git show "main:$NEW" > "$OLD"
comm -23 <(grep -oE '`[^`]+`' "$OLD" | sort -u) \
         <(grep -oE '`[^`]+`' "$NEW" | sort -u)   # in old, gone from new
```

Use no `$1` in a skill code block. Detail: [references/doc-walk.md](references/doc-walk.md#no-positional-parameters-in-skill-code-blocks).

Triage every remainder: each is either deliberately re-homed or an omission, and
you must say which. **Re-homing counts as covered only if you can name the
destination file** — and verify it there before cutting.

### Discovered-fact sweep — run whenever the branch investigated something

Ask what this branch now knows about existing behaviour that it did not know when it started. Durable facts go to `.claude/context/*.md`, or CLAUDE.md's footgun block if ignorance would silently damage a session; where a test already pins it, write the pointer. Detail: [references/doc-walk.md](references/doc-walk.md#discovered-fact-questions).

Two shapes are easy to miss: a fact established by ruling something OUT, and a near-miss you did not ship. A discovered fact is a claim, so it goes through the claims audit. Detail: [references/doc-walk.md](references/doc-walk.md#easy-to-miss-discovered-facts).

### Surface-specific notes

- **CLAUDE.md** — Project Structure entries match real homes; Key Commands and CLI resolve; the skills table is current; it must not re-absorb context-doc content. Detail: [references/doc-walk.md](references/doc-walk.md#claudemd-surface).
- **README.md** — CLI list matches `wifey --help`; quickstart still works.
- **Makefile** — every CLI subcommand has a `wifey-<name>` wrapper; script wrappers are bare; `tools/*.py` is a judgement. Detail: [references/doc-walk.md](references/doc-walk.md#makefile-surface).
- **docker-compose.yml** — daemons get `restart: unless-stopped`; one-shot tools
  get `profiles: [tools]`.
- **`.claude/skills/*/SKILL.md`** — a skill naming a tool, flag, path or constant drifts like CLAUDE.md; check ported skills against this fork and grep for siblings of a changed rule. Detail: [references/doc-walk.md](references/doc-walk.md#skills-surface).
- **The handoff** has no other check (gitignored); read the `queue-items` and `handoff-symbols` hits here and fix in phase 6. Detail: [references/doc-walk.md](references/doc-walk.md#handoff-surface).

---

## Phase 4 — Always-run, regardless of the gate

### MEMORY.md

Rewrite "Last session" to today's date, branch and a one-line summary, rolling per the live index (N comes from it, never from this sentence); the oldest bullet rolls verbatim into `memory/project_session_log_<month>.md`, and you grep the log to confirm. Detail: [references/reconcile-and-claims.md](references/reconcile-and-claims.md#the-n-way-roll).

**The `#NNN` is the one field this phase cannot know**: write the bullet without it and let phase 6's re-verify fill it. Detail: [references/reconcile-and-claims.md](references/reconcile-and-claims.md#the-pr-number-is-filled-later).

Respect the cap: at most 6 bullets, "Last session" at most 2 lines, every other bullet 1 line, index under ~17KB. If the roll pushes the index over, re-home the largest bullet to its topic file (grep the destination) before trimming the new one. CLAUDE.md's Session memory protocol is the authority. Detail: [references/reconcile-and-claims.md](references/reconcile-and-claims.md#memorymd-cap).

### Issue reconcile

Planning lives in GitHub Issues (CLAUDE.md, Fork lineage), so this step reconciles
them, and it is the only place this skill files open work.

1. **Close what the branch finishes.** Put `Closes #N` in the phase-5 PR body for each Issue the branch completes; a partial finish says which part in a comment, and you reconcile to what you verified. Detail: [references/reconcile-and-claims.md](references/reconcile-and-claims.md#closing-issues).
2. **File what the branch found.** Every to-do, open question, defect or follow-up
   the branch surfaced and will not do becomes an Issue now, labelled per CLAUDE.md
   (one priority, one kind, one `effort:` level, `blocked` and `cloud-ok` where
   they apply). Never park
   one in the handoff as "not filed yet", in MEMORY.md's Current State, or in a
   markdown to-do under `docs/plans/`. Over REST, with the body in a file:
   `gh api repos/s10023/buibui-wifey-wall-street-bot/issues -f title='…' -F body=@<file> -f 'labels[]=p3' -f 'labels[]=mechanics'`.
   `gh issue create` and `gh issue list` go through GraphQL, which cloud sessions are
   refused.
3. **Touch the reference surfaces only when the branch changes them** (`docs/north-star.md`, `memory/project_todo_master.md`); neither takes queue rows. Detail: [references/reconcile-and-claims.md](references/reconcile-and-claims.md#reference-surfaces).

**A stale Issue is worse than a missing one**: re-read every `blocked` Issue the branch touches and name what would unblock it. Detail: [references/reconcile-and-claims.md](references/reconcile-and-claims.md#stale-and-blocked-issues).

### Claims audit — run whenever the branch adds prose

Nothing else checks whether the **numbers the branch itself asserts** are true.
Extract every quantitative claim from the branch's new prose — commit messages, audit
docs, PR body, CLAUDE.md and context additions, the handoff, and the code comments and
docstrings the diff adds — **and for each, name the query or command that reproduces
it.** A claim whose reproduction you cannot state is not ready: cut it, soften it to
what you measured, or measure it. Record the reproduction in the commit or the audit
doc, not just the session.

A commit message, code comment or docstring is audited prose too. On an unpushed branch repair a commit with `git commit --amend -F <file>`; after a push, correct the claim in the PR body. Detail: [references/reconcile-and-claims.md](references/reconcile-and-claims.md#commit-messages-and-comments-are-audited-prose).

Six shapes to hunt:

1. A claim about a mechanism supported only by a count
2. A claim inherited from the handoff
3. An upstream number quoted as this repo's
4. A rate with no null
5. A set-wide claim built from a spot check
6. A claim about a series, checked only against the repo

Explanations of each shape. Detail: [references/reconcile-and-claims.md](references/reconcile-and-claims.md#the-six-shapes).

---

## Phase 5 — Commit, push, then open the PR

Run phase 6's handoff re-home pass before this commit, so a rule moved into a tracked file lands in it. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#re-home-before-the-commit).

```bash
git add <files>
git commit -F <file>      # -F, never a heredoc: quoting a hazard trips the guard
git push -u origin <branch>
```

**Then run the clean-clone pre-flight, and let it replace this branch's `make test`.**

```bash
make preflight            # background it; it runs the whole suite in a fresh clone
```

Scope preflight with a positive check, never a judgement: if the diff has no Python and no test reads a changed path (`grep -rl <changed-path> tests/`), say in the PR body which gate you ran instead, naming the two greps. The default stays run. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#scoping-preflight).

Preflight closes timing, not detection: a gitignored path absent on a clean clone is only caught here, before a metered Actions cycle. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#what-preflight-closes).

It must run after the commits (it refuses on a dirty tree). **`make` collapses the exit code, so read the banner**: `REFUSED` and `INFRA` are not suite failures. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#preflight-exit-codes-and-blind-spots).

**Decide the visibility flip before `gh pr create`, from the account's allowance, never your diff's paths.** Skip the flip only if the last run on this repo executed real steps; `steps=0` with `FAILURE` is billing, while `SKIPPED` is a path filter. Otherwise flip and requeue the enumerated runs. Confirm the flip with the user every time. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#the-visibility-flip-decision).

**A flip is a repo-wide event: sweep every open PR before the flip**, reading step counts first. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#sweep-every-open-pr-before-the-flip).

```bash
GH_TOKEN=$(gh auth token --user s10023) gh pr list \
  --repo s10023/buibui-wifey-wall-street-bot --state open \
  --json number,title,headRefOid
```

Not a `post_branch_checks` leg (it needs `gh`); phase 6's flip-back gate closes the pair. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#the-sweep-is-not-a-check-leg).

Then compose the **Documentation updates** section and pass it in the *initial* `--body`; never open the PR and then edit its body. Write PR and Issue bodies with one line per paragraph or bullet. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#composing-the-pr-body).

**Screen the composed title and body before they post** — they are the fourth exposure
surface and the only indexable one, and the sweep's three git legs cannot see either
(phase 1). Do it while the text is still a local file:

```bash
make post-branch-text FILE=docs/plans/pr-<branch>.md \
  && printf '%s' "$TITLE" | make post-branch-text FILE=- \
  && GH_TOKEN=$(gh auth token --user s10023) gh pr create \
       --repo s10023/buibui-wifey-wall-street-bot --title "$TITLE" \
       --body-file docs/plans/pr-<branch>.md
```

One chain, so a hit or a missing file stops the post; the body path is repo-relative. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#one-chain).

```markdown
## Documentation updates

- `CLAUDE.md`: <what changed>
- `README.md`: no change needed (no CLI surface change)
- `MEMORY.md`: Current State updated  (never committed)
```

**State the no-change surfaces explicitly, with the reason.** "no change needed:
internal refactor only" is useful to a reviewer; silence is not.

**MEMORY.md and the SoT are never committed** — they live outside the repo. The
handoff is gitignored. If those are all this phase produced, say so and move on.

Push rules: default `git push`, no force; `--force-with-lease` only with explicit approval, and `guard-destructive` blocks it anyway; never push to `main` from this skill. Rebase only on explicit OK. Detail: [references/handoff.md](references/handoff.md#push-rules-and-rebase).

---

## Phase 6 — Pre-merge, handoff, re-verify

### Pre-merge readiness

Flag, do not fix: uncommitted changes, unpushed commits, `CONFLICTING` /
`DIRTY`, failing required checks, `CHANGES_REQUESTED`.

```bash
PYTHONPATH=. poetry run python tools/wait_ci.py --pr <n>     # resolves the SHA, prints steps=
```

### The post-merge flip-back gate

**Merging is not the last step when the repo was flipped public**: wait for main's own run before flipping back. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#why-merging-is-not-the-last-step).

```bash
make wait-ci-main          # waits for >=5 COMPLETED push jobs on main, then says
                           # "safe to flip the repo back to private."
```

Use that tool rather than a hand-rolled waiter. Its three traps all look like a real failure: `steps=0` is billing, `total_count: 0` is a third state and not requeueable, and a wait must gate on a check-count floor. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#three-traps-in-a-hand-rolled-waiter).

**Requeueing a `steps=0` run** (only after the repo is public — a run that exists
can be re-run in place, which is what turns a billing-dead PR green):

```bash
GH_TOKEN=$(gh auth token --user s10023) gh api \
  "repos/s10023/buibui-wifey-wall-street-bot/actions/runs?head_sha=<FULL-40-char-sha>" \
  --jq '.workflow_runs[] | "\(.id) \(.name) \(.conclusion)"'
GH_TOKEN=$(gh auth token --user s10023) gh api \
  -X POST repos/s10023/buibui-wifey-wall-street-bot/actions/runs/<id>/rerun
```

Enumerate the runs; never assume how many. Sweeping every open PR is a phase 5 decision. Detail: [references/ci-visibility-flip.md](references/ci-visibility-flip.md#enumerate-the-runs).

Wait in the background; never a foreground `gh pr checks --watch`.

### The handoff

Offer — don't auto-write — a fresh-conversation prompt at
`docs/plans/next-conversation-prompt.md`. Gitignored but in-repo, so it survives
a session delete. Keep updating that same file.

This is the STANDING handoff; mattpocock's `/handoff` does not replace it. Detail: [references/handoff.md](references/handoff.md#standing-handoff-versus-handoff).

**Update with targeted `Edit`s, never `Write` the whole file**: an overwrite destroys the standing back half. Detail: [references/handoff.md](references/handoff.md#targeted-edits-only).

Front-half shape: one-line context · PR-state table (a **snapshot**, with
"re-verify first") · just shipped · state of the world · reference · the next
1–3 Issue numbers, in order. The handoff sequences Issues; it never holds a
to-do of its own, so anything that would be a new task is filed in phase 4 first.

**Carry the standing blocks forward verbatim**, refreshing only their dated "state
at" lines: the free-data-arc honest exit, the TA freeze, the `gh` rules (never
`gh auth switch`; `--repo` always), the daily operator check, and the accumulated
findings list. The skill-fix queue and open questions are Issues now: if an older
handoff still carries either, file each entry as an Issue and drop the block.

Keep the standing blocks in four labelled groups, ordered by when needed: **A** Settled, **B** Workflow, **C** Evidence, **D** Environment. Append a new finding to the group it belongs to. Detail: [references/handoff.md](references/handoff.md#four-labelled-groups).

#### Prune every run — carry-forward is not append-only

The gate is `handoff-size` in phase 1 (`HANDOFF_MAX_LINES` 240, warning within 20); treat the warning as the cue to re-home a cluster. Detail: [references/handoff.md](references/handoff.md#prune-every-run).

**Re-homing is a separate pass and it runs first**: move the rule to its durable home, `grep` the destination, then cut the narrative; a re-home into a tracked file belongs in phase 5's commit. Detail: [references/handoff.md](references/handoff.md#re-homing-runs-first).

Delete merged PRs beyond the last one or two, completed tasks, "what #N found" narratives, shipped skill-fix items and answered questions, but read a closed section before deleting it and prune by moving to a durable home. The test for every line: if the next session never reads this, does it do something wrong? Detail: [references/handoff.md](references/handoff.md#what-to-delete-each-run).

#### Operator actions must resolve in this repo

Name the exact command, target or unit and verify it exists in this repo before writing it; `buibui-signal-watch.*` belongs to the parent. Detail: [references/handoff.md](references/handoff.md#operator-actions-must-resolve-in-this-repo).

### Re-verify PR state — last content edit, never skip

The handoff is written before the merge, so its PR table is the first thing to go stale. Detail: [references/handoff.md](references/handoff.md#why-the-pr-state-goes-stale).

```bash
gh pr view <PR#> --repo s10023/buibui-wifey-wall-street-bot \
  --json state,mergedAt --jq '"\(.state) \(.mergedAt)"'
```

Re-query **every** PR named in the handoff and rewrite the table to match; if one merged, update the "first move" line too. Detail: [references/handoff.md](references/handoff.md#re-query-every-pr).

**Fill MEMORY.md's `#NNN` here too**, from this same query (it costs no commit). Detail: [references/handoff.md](references/handoff.md#fill-the-pr-number).

**No PR opened → strike the placeholder**: write the SHA plus "local-only, still amendable", never an unfilled PR-shaped slot. Detail: [references/handoff.md](references/handoff.md#no-pr-opened).

**This is the last action of all** — there is no stamp to write after it.

---

## Output format

```text
phase 1 sweep      — <n> findings triaged: <what> | clean
behaviour gate     — walked | skipped (<reason>)
CLAUDE.md          — updated: <what> | no change needed: <reason>
README.md          — …
MEMORY.md          — Current State rolled N-way per the live index  (never committed)
Issue reconcile    — #<n> closed, #<n> filed | no Issue affected
claims audit       — <n> claims, each with its reproducing query | no new prose
Makefile / compose — no change needed: <reason>
.claude/context/*  — …
.claude/skills/*   — …
handoff            — <before> → <after> lines  (gitignored)
PR body            — Documentation updates folded into the initial --body
pre-merge          — clean | <blocker>
PR state re-check  — #<num>: <OPEN | MERGED>, table rewritten to match
```

Be explicit. "no change needed: internal refactor only" is useful; silence is not.

## Safety rails

- **Confirm every edit, except the two surfaces marked `unprompted: true`** (`MEMORY.md` and the handoff), per the account-level CLAUDE.md's "Session hygiene" section; confirm git-tracked surfaces and untracked single-copy data alike, and let the exception be two named files, never a directory class. Detail: [references/handoff.md](references/handoff.md#confirm-every-edit).
- **Don't rename or move files.** Propose the edit in place, flag the path
  separately.
- **Never `Write` over a doc.** Always targeted `Edit`.
- **No force-push without explicit OK**, and the guard blocks it regardless.
- **Stop on uncertainty** — show the doc snippet and the diff hunk, and ask.
- **Draft PRs stay draft.** Don't flip to ready as a side effect.

## When NOT to run

The PR is closed or merged (open a follow-up `docs:` PR instead) · the user said
"skip docs" · it is a bot PR · the branch has no diff. Say so and stop.

## PR summary

Write it through `/pr-summary`; do not compose it from scratch. The path flattens `/` to `-`. Detail: [references/handoff.md](references/handoff.md#pr-summary-path).
