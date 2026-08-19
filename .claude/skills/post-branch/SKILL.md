---
name: post-branch
description: >
  Post-branch docs sweep + handoff — diff the branch's behaviour changes against
  the doc surfaces (CLAUDE.md, README.md, MEMORY.md, Makefile, docker-compose.yml,
  .claude/context/*.md, .claude/skills/*/SKILL.md,
  docs/plans/next-conversation-prompt.md)
  and propose targeted edits where they've drifted, then run a pre-merge
  readiness check and offer a fresh-conversation handoff prompt. Use BEFORE
  `gh pr create`, while the branch is still local-only, and fold the resulting
  "Documentation updates" section into the initial PR body.
  Skip for pure refactors, bug fixes covered by tests, dependency bumps, and
  lint-only commits — the behaviour gate decides. Confirm every edit
  before writing; never force-push without explicit OK. Also triggers on the
  user saying "/post-branch", "wrap up the branch", "docs check",
  "pre-merge check", or "next conversation prompt".
allowed-tools: Bash, Read, Edit, Write
---

# Post-Branch Docs Sweep

A PR's diff is the source of truth for what changed. The docs are claims about
how the codebase behaves. After a PR adds flags, scripts, defaults, files or
commands, those claims go stale — often silently. This skill walks the doc
surfaces, surfaces the drift, and proposes edits.

**It runs before the PR exists**, on a branch that is committed but not opened.
Private repo, free tier, hard Actions budget: sweeping after the PR is open means
a second push, and every push re-runs the full five-check matrix. Sweeping first
is one CI run instead of two, with identical review signal.

## Running order

Phases, not step numbers. (Earlier versions numbered steps in one order and ran
them in another, which was a standing source of error.)

⚠ **Watch for a phase whose output depends on a fact a LATER phase creates.**
Three found so far — the handoff stamp before the PR-state rewrite, the claims
audit before the commit it audits, and MEMORY.md's `#NNN` before the PR exists. Do not reorder the whole step to fix one: name the single
field that lands later, and the phase that lands it.

| Phase | What | Costs CI? |
| --- | --- | --- |
| **1** | `make post-branch-checks` — every mechanical check | no |
| **2** | Behaviour gate — is this PR user-facing? | no |
| **3** | Doc walk — judgement, keyed off the diff | no |
| **4** | Always-run: MEMORY.md, SoT reconcile, claims audit | no |
| **5** | Commit + push, then compose the PR body, then `gh pr create` | one run |
| **6** | Pre-merge check, handoff, re-verify PR state, **stamp LAST** | no |

Phases 1 and 4 run **regardless** of the phase-2 gate. MEMORY.md and the SoT live
outside the repo and the handoff is gitignored, so none of them ever costs CI.

---

## Phase 1 — Run the mechanical sweep

```bash
make post-branch-checks
```

Eleven checks, one command, in `tools/post_branch_checks.py`. They used to be
sixteen shell blocks embedded in this file, which a session had to notice and
copy by hand — *a hand walk is not the walk*, and the same defects recurred
because prose cannot enforce.

| Check | Asks |
| --- | --- |
| `queue-items` | Does this branch **close** a task the handoff still lists as to-do? |
| `handoff-symbols` | Does the handoff make a claim about a symbol or file this branch touched? |
| `new-files` | Does an added non-Python operator file reach a doc that enumerates by name? |
| `new-modules` | Does an added module reach `.claude/context/`? |
| `new-targets` | Does an added Make target reach a doc? |
| `negative-claims` | Does a doc assert the absence of something this branch just added? |
| `doc-indexes` | Is a generated `INDEX.md` stale? (a red suite, not a lint nit) |
| `md-atx` | Did a wrapped `#123` land in column 1 and become an MD018 heading? |
| `memory-cap` | Is MEMORY.md over 6 Current State bullets or ~17KB? |
| `handoff-size` | Does the handoff's line-count stamp match the file? |
| `stale-anchors` | Does a doc cite a numbered section (`Step 3`, `§4a`) its target no longer has? |

**Every finding is a candidate to dismiss in seconds, never an automatic edit.**
The asymmetry is deliberate: a false positive costs a glance, a silent miss ships
a doc that enumerates every sibling but one and reads as complete.

Three properties the checks rely on, worth knowing before you change them:

- **Word boundaries are load-bearing.** A bare substring match reports a module
  "documented" on a hit that has nothing to do with it, and *a false-positive
  presence check is worse than none, because it reports covered.*
- **A basename that names a role cannot identify a file.** Every skill is
  `SKILL.md`, so probing that matches CLAUDE.md's generic sentence about where
  skills live; `probe_names` probes the parent directory for those. Same for
  `__init__.py`, `INDEX.md`, `README.md`.
- **Untracked files count as added.** `git diff` cannot see them, which used to
  make the presence checks report zero on exactly the branch they existed for.

The Makefile is deliberately **not** in the enumerating-doc list. A build rule is
not documentation, and including it would let a file that appears in no prose
report COVERED. Check what a proposed addition would newly mark covered before
adding it.

**Triaging the presence checks:**

- A hit is a prompt to judge, not an automatic edit — a private helper module may
  legitimately not warrant a context entry.
- `migrations/` **is** in scope (documented at `.claude/context/migrations.md`),
  so a new migration script gets checked like any other module.
- `trade/` is knowingly absent and is **not** a finding — it is an empty
  placeholder, both files 0 bytes.
- **Renames are not covered.** The checks key on additions; swap in
  `git diff main --diff-filter=R --name-only` and check the new path by hand.
- **`negative-claims` prints a `note:` line for claims it scoped out.** Those are absence
  sentences elsewhere in the tree that this diff does not touch — not dismissed, just not
  yours. A claim it cannot scope (no backticked token) is **reported**, so an odd-looking
  hit with `(no token to scope on)` means "could not rule this out", not "certainly stale".
- **`docker-compose.yml` is not covered either.** Check by hand that a new
  daemon got `restart: unless-stopped` and a new one-shot tool got
  `profiles: [tools]`, plus its `docker-up` / `docker-down` lines.

### What the sweep deliberately does NOT cover

Judgement, all of it: the behaviour gate, the doc walk, the discovered-fact
sweep, the claims audit, the SoT reconcile, and everything touching `gh`. Those
are phases 2–6. A green sweep is not a green branch.

---

## Phase 2 — Behaviour gate: is this PR user-facing?

Read the diff (`git diff main --stat`, then `git diff main`). Use **`git diff
main`**, two dots — this skill runs before its own commit, so `main...HEAD`
diffs two identical trees and every check passes *vacuously*.

**Walk the docs** if any of these are present:

- New CLI subcommand or flag; new Make target or changed default
- New TOML key, changed default, or new environment variable
- Renamed/moved file referenced from docs; new error class, exit code or alert format
- New external dependency or system requirement
- Behaviour change to an existing public command
- New long-running daemon or one-shot tool
- The diff touches `analytics/store/schema.py` (see below)
- Anything on the notification list (see below)

Path heuristics, not absolute rules — always read the diff before deciding:

```yaml
behavior_signal_globs:   # touching these almost always needs a walk
  - wifey.py · cli/**/*.py · Makefile · docker-compose.yml · pyproject.toml
  - config/strategy_params.toml · config/*signal_watch*.toml
  - deploy/**                     # scripts AND systemd units are operator-facing
  - .github/workflows/**/*.yaml   # ⚠ .yaml, NOT .yml — every workflow here is .yaml
behavior_skip_globs:     # internal-only refactor space
  - analytics/**/_*.py · analytics/**/*.py (per-PR judgement) · tests/**
  - poetry.lock · *.parquet · tests/fixtures/**
```

⚠ A move that adds a new **public symbol** is user-facing even under
`analytics/**`.

**Stop after phase 4** if the PR is purely: an internal refactor preserving the
public API, a bug fix with a regression test and no behaviour change, a
dependency bump, test-only changes, docstring edits inside source files, or a
regression-fixture refresh with goldens unchanged.

⚠ **"Lint-only" means the formatting, not the linter's configuration.** Editing
`.markdownlint*`, ruff/mypy blocks, or a CI job's globs changes what the build
*enforces*, which is operator-facing however mechanical the diff looks. One
`chore(lint)` PR was 245 whitespace fixes plus one deleted glob line — and that
line had silently excluded the tree the job was triggered by. **Judge the gate on
what the change enforces, not on what the diff looks like.**

**Strong refactor signals** — a module in CLAUDE.md's Project Structure was
renamed, moved or reduced to a re-export shim; the CLI surface changed; a new
`make wifey-*` target landed. When in doubt, ask.

### DB migrations are operator-facing even when nothing else is

A schema change applies **silently at the next `init_schema`** — no command to
run, no output to read. The handoff must state, explicitly:

1. **When it applies** — the next process that opens the DB. Name it if scheduled.
2. **Whether existing rows stay readable**, and if not, what breaks.
3. **Whether a manual step is needed** — backfill, `make db-update`, `clean-db`, none.
4. **Whether it is self-healing, and why.** A nullable column on a table whose
   rows age out needs no backfill; on a table that accumulates, it does.

Confirm `tests/test_schema_insert_arity.py` still passes: adding a column to a
positionally-written table requires updating that statement in the same PR.

### Notification surface — decide it, never default to it

Telegram is an operator-facing output and belongs in this gate, but it is on no
doc surface, so the decision gets made by whoever happens to think of it. Three
paths: the **personal** channel (long+short), the **wife** channel (BUY-only, a
human audience), and `deploy/notify-failure.sh`.

Needs an explicit decision if the PR adds or changes: a scheduled job; an
irreversible or outward-facing action; a latching state transition (the
*transition* is the event, not the state); a failure path visible only in an
unread log; or a periodic summary a human must act on.

Record one of four verdicts: `always` (a human must act every time, or the
channel needs a heartbeat) · `on-change` (only transitions matter) ·
`on-failure-only` (correct for jobs nobody reads when healthy) · `never` (**state
why**, in one line).

⚠ **A channel whose only signal is failure is unfalsifiable** — you cannot tell
healthy from broken without a heartbeat, and the delivery path gets exercised for
the first time on the day you need it. If a path is `on-failure-only`, confirm
something else proves it alive. **Volume is the counterweight**: multiply by the
schedule before choosing `always`, and remember the wife channel is a person.

---

## Phase 3 — Walk each doc surface

```yaml
surfaces:
  - {id: claude_md,   path: CLAUDE.md,          purpose: project structure, commands, footguns, verdicts}
  - {id: readme,      path: README.md,          purpose: CLI surface, install, quickstart}
  - {id: memory_md,   path: <memory>/MEMORY.md, purpose: Current State, always_update: true}
  - {id: makefile,    path: Makefile,           scope: any_referencing_changed_artifact}
  - {id: compose,     path: docker-compose.yml, scope: any_referencing_changed_artifact}
  - {id: context,     glob: .claude/context/*.md,      scope: + new_module_presence}
  - {id: skills,      glob: .claude/skills/*/SKILL.md, scope: any_referencing_changed_artifact}
  - {id: handoff,     path: docs/plans/next-conversation-prompt.md, written_at: phase_6}
```

**When porting this skill to another repo, edit only that block.**

For each surface: locate it, read it, decide if an edit is warranted, **propose
it as a diff and wait for confirmation**, then apply with `Edit` — never `Write`.
Bias to minimal, targeted edits. Look for outdated examples, missing entries,
broken paths, stale defaults, stale module-purpose descriptions, and:

**A stale VALUE hides under a correct KEY.** The name grep matches the key, so a
doc naming the right key with the wrong value reads as a hit and passes review.
When a PR changes what a key *does*, compare the documented value against the
current one.

**Removals have no new symbol to grep.** A PR that removes, narrows or disables a
behaviour introduces nothing to search for, so the walk comes back clean. Grep
the name of the artifact that was *constrained*, not of the thing that replaced
it.

**The doc can be right and the code wrong.** When a PR changes behaviour a doc
already describes, diff the doc's claim against the **pre-fix** code too. If the
doc already described the corrected behaviour, the bug was a bug rather than a
design choice, and the fix restored a calibration instead of choosing one — which
changes how the PR body should describe it. A doc/code mismatch is not
automatically doc drift.

**A changed verdict needs a WORDING grep, not a filename grep.** Prose that
*quotes* a verdict lives nowhere near the file that declares it. Pick two or
three distinctive tokens from the retracted sentence — a number, a metric name, a
coined phrase — and grep those; the verdict word itself is too common to
discriminate. One PR left four surfaces carrying a retracted statistic.

**Rewrote or compressed a doc? Diff the IDENTIFIERS, not the prose.** Reading a
rewrite for "what looks missing" does not work — it reads complete, because it
was written to.

```bash
toks() { grep -oE '`[^`]+`' "$1" | sort -u; }
git show main:<path> > /tmp/old.md
comm -23 <(toks /tmp/old.md) <(toks <path>)   # in old, gone from new
```

Triage every remainder: each is either deliberately re-homed or an omission, and
you must say which. **Re-homing counts as covered only if you can name the
destination file** — and verify it there before cutting.

### Discovered-fact sweep — run whenever the branch INVESTIGATED something

Every check so far asks whether the docs drifted from the code. None asks whether
the branch **learned** something durable about behaviour that did not change. A
fix usually costs an investigation, and the investigation is the expensive part
with no artifact — the diff records the repair, the commit message the reasoning,
and neither is a surface a future session reads.

Ask explicitly: *what does this branch now know about existing behaviour that it
did not know when it started?* For each answer:

1. **Durable, or session state?** How the code behaves is durable; "the DB was
   stale on this machine" is not.
2. **Would a future session go looking for it?** If yes → `.claude/context/*.md`.
   If it would silently damage a session that never thought to ask → CLAUDE.md's
   footgun block.
3. **Already pinned by a test?** Then the test is the enforcement and the doc is
   the *pointer* — write the pointer, don't restate the rationale.

Two shapes are easy to miss: **a fact established by ruling something OUT** (a
scan over an empty set, which nothing keyed on the diff can find), and **a
near-miss you did not ship** (record the decision and its cost, or the next
session re-derives the same dead end).

⚠ A discovered fact is a claim, so it goes through the claims audit like any
other. Re-derive it from the code before writing it down.

### Surface-specific notes

- **CLAUDE.md** — Project Structure entries match real homes; Key Commands and
  CLI resolve; the skills table is current. ⚠ **CLAUDE.md must not re-absorb
  context-doc content.** Two sources of truth, one of them invisible, always rots
  the invisible one. If a PR adds module detail here, move it.
- **README.md** — CLI list matches `wifey --help`; quickstart still works.
- **Makefile** — every CLI subcommand has a `wifey-<name>` wrapper; script
  wrappers are bare (`backup`, not `wifey-backup`). `tools/*.py` is genuinely
  mixed, so this is a **judgement**: a tool the operator runs as part of a
  documented workflow wants a target; one another tool calls does not. Following
  a `new-targets` hit to what it *executes* is also a cheap directory check — it
  is how `scripts/` was found missing from Project Structure entirely.
- **docker-compose.yml** — daemons get `restart: unless-stopped`; one-shot tools
  get `profiles: [tools]`.
- **`.claude/skills/*/SKILL.md`** — a skill naming a tool, flag, path or constant
  drifts exactly like CLAUDE.md. ⚠ **Ported skills drift against the fork, not
  just against time**: check every `buibui-*` target, `buibui --help`, and
  parent-repo memory path against this repo's equivalents. One such path would
  have sent a session's Current State update into the parent repo.
  `.claude/` is covered by `make lint-md`, so a skill edit lints like any other
  file — keep it that way.
- **The handoff** is the surface with no other check: gitignored, so no reviewer
  ever sees its drift, and its group A is headed *"Settled — do not
  re-litigate"*, which means a stale line there **instructs** rather than merely
  misinforms. `queue-items` and `handoff-symbols` cover it mechanically; read
  their hits here and fix in phase 6.

---

## Phase 4 — Always-run, regardless of the gate

### MEMORY.md

Rewrite "Last session" to today's date + branch + a one-line summary. **The roll
is THREE-way**: today → **Last session**, the existing Last session → **Prior
session**, the existing Prior session → verbatim into
`memory/project_session_log_<month>.md`. Grep the log afterwards to confirm it
landed. Convert relative dates to absolute. Update
`memory/project_open_questions.md`.

⚠ **The `#NNN` is the one field this phase cannot know** — phase 5 creates the
PR. Do the roll here regardless: the roll is the part that gets skipped, and it
needs nothing from the PR. Write the bullet with the number left out, and phase
6's re-verify fills it from the same `gh` query that rewrites the handoff.

Respect the cap — this step is where it gets broken. Current State holds at most
**6 bullets**, "Last session" at most 2 lines, every other bullet exactly 1.
`memory-cap` in phase 1 checks it. Writing a rich multi-sentence entry feels like
diligence and is the mechanism by which the index once grew to 57% Current State;
the index is re-read every session, so that bloat is billed per conversation.

⚠ **Check the live file rather than the last person's description of it.** This
step has been wrong in both directions — once naming a bullet that did not exist,
then denying one that does. CLAUDE.md's Session Memory Protocol is the authority.

### SoT reconcile

Ask: **does this branch close, change or contradict a row in the SoT**
(`memory/project_todo_master.md`)? If yes, reconcile it now — move the row to
Closed with a one-line verdict, per that file's own rule ("never delete").

Nothing auto-updates the SoT; the session-memory wiring all touches MEMORY.md.
**A stale row is worse than a missing one**, because it reads as current
evidence — three stale rows once described shipped code as remaining work, and a
session picking up from them would have rebuilt it. **Reconcile to what you
verified, not to what is tidy**: a row that shipped with a known residual gap
gets the gap written down, not a blanket close.

### Claims audit — run whenever the branch ADDS PROSE

Nothing else checks whether the **numbers the branch itself asserts** are true.
Extract every quantitative claim from the branch's new prose — commit messages,
audit docs, PR body, CLAUDE.md and context additions, the handoff — **and for
each, name the query or command that reproduces it.** A claim whose reproduction
you cannot state is not ready: cut it, soften it to what you measured, or measure
it. Record the reproduction in the commit or the audit doc, not just the session.

⚠ **A commit message is audited prose, and by phase 4 it usually already
exists.** The phase order puts the audit before the commit, but the normal flow
commits while the work is fresh, so a failing claim is typically already written.
On an unpushed branch the repair is `git commit --amend -F <file>`, never a
follow-up wording commit that leaves the false claim in the history the PR ships.
After a push, amend plus force-push needs explicit OK — otherwise correct the
claim in the PR body and say the message predates it.

Five shapes to hunt:

1. **A claim about a MECHANISM supported only by a COUNT.** A count is consistent
   with many mechanisms. Demand the query that rules the *others* out.
2. **A claim inherited from the handoff counts as the branch's own.** If the
   branch repeats it, the branch owns it.
3. **An upstream number quoted as this repo's.** Re-derive here, or say
   "preventive, not a repair".
4. **A RATE with no null.** "X% of Y does Z within N" is not a finding until it
   states what fraction of an arbitrary comparable does Z. This audit checks
   whether a number is *true*, never whether it is *informative*, so the null has
   to be demanded explicitly.
5. **A SET-WIDE claim built from a SPOT CHECK.** *every*, *none*, *all N*, *in
   zero cases*, *the only* — a sentence quantified over a set can only be
   established by scanning the whole column. The tell is grammatical rather than
   numerical, so it is cheap to spot once you look for it.

---

## Phase 5 — Commit, push, then open the PR

```bash
git add <files>
git commit -F <file>      # -F, never a heredoc: quoting a hazard trips the guard
git push -u origin <branch>
```

⚠ **Then decide the visibility flip, before `gh pr create`** — this phase covered
only the exit until now. Phase 2 has already read the diff: if it touches
`**/*.py` or `web/ui/**`, the PR needs the repo **public**, or its three
path-filtered checks create zero steps and settle at `steps=0`, which renders
exactly like a real failure. **Confirm the flip with the user on every
occasion** — CLAUDE.md makes the mechanics standing authorisation and the timing
not, because the window republishes the parent's pre-fork commits. A docs-only
diff skips it. Phase 6's flip-back gate closes the other half of the pair.

Then compose the **Documentation updates** section and pass it in the *initial*
`--body`. That ordering is the whole payoff — do not open the PR and then edit
its body.

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

**Push rules**: default `git push`, no force. `--force-with-lease` only with
explicit approval — and `guard-destructive` blocks it even then, deliberately.
Approval makes a push *intended*, not *safe*; ask the operator to run it. Never
push to `main` from this skill.

**Rebase** only if a relevant doc lives on `main` but not the branch, and only on
explicit OK.

---

## Phase 6 — Pre-merge, handoff, re-verify

### Pre-merge readiness

Flag, do not fix: uncommitted changes, unpushed commits, `CONFLICTING` /
`DIRTY`, failing required checks, `CHANGES_REQUESTED`.

```bash
poetry run python tools/wait_ci.py --pr <n>     # resolves the SHA, prints steps=
```

### The post-merge FLIP-BACK gate — this phase used to stop before it

⚠ **Merging is not the last step when the repo was flipped public.** Merging starts
a *fresh* run on `main`, and flipping to private kills whatever is created after
the flip — `Regression tests` `needs:` lint-typecheck-test and is not created until
~4 minutes in, so an early flip leaves it at `steps=0` and main looks red for
billing reasons rather than code ones.

```bash
make wait-ci-main          # waits for >=5 COMPLETED push jobs on main, then says
                           # "safe to flip the repo back to private."
```

**Do not hand-roll this waiter.** One session's version reported `jobs=0` against
a live `total_count=2` by swallowing a `gh` failure into "empty means zero". The
other trap — exiting on "nothing pending", which is vacuously true while the
chained job does not yet exist — is the one the `--pr` gate hit as #194. Both are
already encoded in the tool, which is exactly why the flip-back gate should be
that same tested tool rather than fresh shell.

**Use that tool rather than hand-querying `gh`.** It settles the billing question
directly, and the manual path has three traps that all render identically to a
real failure:

- **`steps=0` is billing, not code.** Every job fails in 2–5s with zero steps
  when the Actions allowance is exhausted. ⚠ Duration alone never settles it — a
  healthy `markdownlint` finishes in 7s, and a **pass** in seconds needs the same
  check as a fail. `steps` is the discriminator. **Never open a debugging session
  on that shape.**
- **`total_count: 0` is a third state and is not requeueable** — a PR opened while
  the repo was private may have no run at all. ⚠ But that endpoint exact-matches,
  so a **short SHA also returns 0, silently**. The free discriminator: *if the
  rollup is green, the query is wrong, not the CI.* The transferable rule — **an
  exact-match query that returns EMPTY rather than ERRORING on a malformed key is
  indistinguishable from a true negative.**
- **A wait must gate on a check-count FLOOR, not on "nothing pending".** An empty
  rollup satisfies "no check is unresolved", so the loop exits immediately and
  reads as all-passed. Assert the count reaches 5, treat `""` as pending (a queued
  check's conclusion is the empty string, not `null`), and count with
  `jq 'length'` — `wc -w` scores `Trivy filesystem scan` as two.

**Requeueing a `steps=0` run** (only after the repo is public — a run that exists
can be re-run in place, which is what turns a billing-dead PR green):

```bash
GH_TOKEN=$(gh auth token --user s10023) gh api \
  "repos/s10023/buibui-wifey-wall-street-bot/actions/runs?head_sha=<FULL-40-char-sha>" \
  --jq '.workflow_runs[] | "\(.id) \(.name) \(.conclusion)"'
GH_TOKEN=$(gh auth token --user s10023) gh api \
  -X POST repos/s10023/buibui-wifey-wall-street-bot/actions/runs/<id>/rerun
```

**Enumerate the runs; never assume how many.** There are three workflow files,
but a PR does not always get three runs — `Docker Build` is path-filtered, so a
dependency bump gets 3 and a typical feature branch gets 2. A fixed count in an
instruction is right when written and wrong after one workflow edit.

**When a flip happens, sweep EVERY open PR**, not just this one: the public window
is a repo-wide event and it is the only moment other PRs' checks can run.
Dependabot PRs are where this bites, because nobody is watching them.

Wait in the background; never a foreground `gh pr checks --watch`.

### The handoff

Offer — don't auto-write — a fresh-conversation prompt at
`docs/plans/next-conversation-prompt.md`. Gitignored but in-repo, so it survives
a session delete. Keep updating that same file.

**Update with targeted `Edit`s. NEVER `Write` the whole file.** Its standing back
half — the four groups, the queue, the NOT-queued list — is exactly what a
template does not reproduce, so an overwrite destroys it silently and the loss is
invisible until a session re-litigates something already ruled out.

Front-half shape: one-line context · PR-state table (a **snapshot**, with
"re-verify first") · just shipped · state of the world · reference · 1–3
suggested next tasks with file paths.

**Carry the standing blocks forward verbatim**, refreshing only their dated "state
at" lines: the free-data-arc honest exit, the TA freeze, the `gh` rules (never
`gh auth switch`; `--repo` always), the daily operator check, the accumulated
findings list, the skill-fix queue, and open questions.

Keep them in **four labelled groups**, ordered by when they are needed — flattened
into one blockquote they reached ~90 lines and a reader could no longer tell a
prohibition from a `PYTHONPATH` reminder:

| Group | Holds | Read when |
| --- | --- | --- |
| **A — Settled** | Concluded arcs, freezes, ruled-out work | Before proposing a task |
| **B — Workflow** | Ordering, `gh`, subagent cap, background tests, MEMORY cap | Before running one |
| **C — Evidence** | Re-derive-the-mechanism, window anchors, multiplicity, provenance | Before quoting a number |
| **D — Environment** | Script recipe, import paths, absent libraries | On demand |

Append a new finding to the group it belongs to — a methodology lesson is **C**,
not a new bullet at the end of **B**.

#### PRUNE every run — carry-forward is not append-only

**Measure at the SESSION open and refuse to finish higher.** On a multi-task
session the binding baseline is that first reading, never the previous task's
stamp — so a later task may spend what an earlier one freed, and the gate
stops being whatever the session decides. A stamp only *measures*;
with nothing gating, "capture this session's lesson" beats "prune" every run and
the file ratchets — it reached 300 lines with the prune four runs overdue. A
growth *refusal* rather than a cap, deliberately: a cap can force deleting a live
rule, a refusal only forces you to pay for each new line by re-homing an old one.
If a session genuinely must end higher, say so in the stamp.

**Re-homing is a separate pass and it runs FIRST**: move the rule to its durable
home, `grep` the destination to confirm it landed, and only then cut the
narrative. A prune that drops a guard is a regression disguised as hygiene.

Delete every run: merged PRs beyond the most recent one or two · completed tasks
and closed findings · "what #N found" narratives once the lesson is in group C ·
shipped skill-fix items (**outright — no `DONE in #146` tombstones**) · answered
open questions.

The test for every line: **if the next session never reads this, does it do
something wrong?** If no, cut it.

⚠ **Read a closed section before deleting it — live rules hide inside blocks
headed "DONE" or "CLOSED".** Prune by MOVING to the durable home, never by
deleting outright; if a rule has no committed home yet, that is a signal to write
one, not to keep the block.

**The stamp is ONE line**, near the top. A multi-line stamp cannot describe the
file containing it — adding lines changes the count, so the figure is stale the
moment it is written, and the next run "corrects" it into a different wrong
number. It was wrong eleven runs running for exactly this reason; the fix is the
shape, not more care.

```markdown
Line count: <new> (prev <n-1>, <n-2>, <n-3>, <n-4>) — <one clause: why up or down>.
```

Order is fixed: read `wc -l` → make every content edit, **the PR-state rewrite
below included** → read `wc -l` again → one last `Edit` replacing the stamp line
→ **re-read and confirm it matches**. That last verification is the whole fix and
the positive control the rule never had; `handoff-size` in phase 1 also catches a
mismatch.

⚠ **The stamp comes AFTER the PR-state rewrite, not before it.** Stamping first
leaves the count wrong whenever that rewrite adds or drops a line — the ordering
manufactures the very defect `handoff-size` then reports, and it reads as correct
on the runs where the rewrite happens to be line-neutral. The two rules are not
in conflict: only the `gh` query has to be fresh, and nothing requires the
rewrite to be textually last.

#### Operator actions must resolve in THIS repo

Name the exact command, target or unit, **and verify it exists here before
writing it**. A previous handoff carried "restart signal watch" for a daemon this
repo does not have — the `buibui-signal-watch` units belong to the crypto parent.
Wifey dispatch is the manual one-shot `make go-live`. **A unit existing on the
machine is not evidence it belongs to this repo**: read `WorkingDirectory`, check
`grep -n '<target>:' Makefile`, check `wifey <cmd> --help`.

### Re-verify PR state — LAST content edit, never skip

This skill writes the handoff *before* the merge, so its most prominent
instruction is the first thing to go stale, and the handoff is the artifact that
survives a session delete.

```bash
gh pr view <PR#> --repo s10023/buibui-wifey-wall-street-bot \
  --json state,mergedAt --jq '"\(.state) \(.mergedAt)"'
```

Re-query **every** PR named in the handoff and rewrite the table to match. If one
merged, update the "first move" line too — the next session should start on a
task, not merge something already merged. If the local branch still exists, say
so; deleting it is standing habit here.

**Fill MEMORY.md's `#NNN` here too**, from this same query — phase 4 wrote that
bullet before the PR existed. MEMORY.md lives outside the repo, so this costs no
commit and no CI.

**The stamp follows this**, and it is the last action of all.

---

## Output format

```text
phase 1 sweep      — <n> findings triaged: <what> | clean
behaviour gate     — walked | skipped (<reason>)
CLAUDE.md          — updated: <what> | no change needed: <reason>
README.md          — …
MEMORY.md          — Current State rolled 3-way  (never committed)
SoT reconcile      — <row> closed | no SoT row affected  (never committed)
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

- **Confirm every edit.** This skill proposes; the user approves.
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

Follows `.claude/skills/pr-summary/SKILL.md` exactly — read it rather than
composing from scratch. ⚠ **The path flattens `/` to `-`**: every branch here is
`docs/…`, `feat/…`, `fix/…` or `chore/…`, so a literal `docs/plans/pr-<branch>.md`
names a directory that does not exist and the write fails.
