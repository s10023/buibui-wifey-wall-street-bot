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
Three found so far — the handoff written before the PR-state rewrite, the claims
audit before the commit it audits, and MEMORY.md's `#NNN` before the PR exists. Do not reorder the whole step to fix one: name the single
field that lands later, and the phase that lands it.

| Phase | What | Costs CI? |
| --- | --- | --- |
| **1** | `make post-branch-checks` — every mechanical check | no |
| **2** | Behaviour gate — is this PR user-facing? | no |
| **3** | Doc walk — judgement, keyed off the diff | no |
| **4** | Always-run: MEMORY.md, SoT reconcile, claims audit | no |
| **5** | Commit + push, then compose the PR body, then `gh pr create` | one run |
| **6** | Pre-merge check, handoff, **re-verify PR state LAST** | no |

Phases 1 and 4 run **regardless** of the phase-2 gate. MEMORY.md and the SoT live
outside the repo and the handoff is gitignored, so none of them ever costs CI.

---

## Phase 1 — Run the mechanical sweep

```bash
make post-branch-checks
```

Twelve checks, one command, in `tools/post_branch_checks.py`. They used to be
sixteen shell blocks embedded in this file, which a session had to notice and
copy by hand — *a hand walk is not the walk*, and the same defects recurred
because prose cannot enforce.

**The sweep now closes by naming the phases below that it does NOT reach**, so passing
it cannot feel like passing the walk. Suppressed for `--text` and for a single `--check`.

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
| `memory-cap` | Is MEMORY.md over 6 Current State bullets or ~17KB? **Phase 6 reading** |
| `handoff-size` | Is the handoff past `HANDOFF_MAX_LINES` (240)? **SKIPPED when the handoff is absent — never clean.** **Phase 6 reading** |
| `stale-anchors` | Does a doc cite a numbered section (`Step 3`, `§4a`) its target no longer has? |
| `sensitive-terms` | Would a visibility flip publish a work identifier? Tracked tree · this branch's commit **content** · this branch's commit **messages**. The PR title/body is a **fourth** surface none of these reach — `--text`, below |

⚠ **`memory-cap` and `handoff-size` are VACUOUS in phase 1 — ignore them here.** They measure
files that **phases 4 and 6 write**, so on the first run they score the *previous* session's
state and report clean regardless of what this branch will do. Re-run
`make post-branch-checks` after phase 6 and read them then; that run is the gate. This is a
fourth instance of the ordering trap named at the top of this file, and the only one **inside**
the mechanical sweep rather than the prose.

⚠ **`sensitive-terms` reading `NOT CONFIGURED` is a FINDING, not a skip.** The term list
(`.claude/sensitive-terms.txt`) is **gitignored by policy** — a tracked list of the words you
are hiding is the leak it exists to prevent — so it dies on a reclone, and before a flip
"did not run" and "passed" must not look alike. **Only the operator can enumerate the terms.**
The commit-**message** leg is the one no file edit reaches: the flip republishes the whole
history, so scrubbing a term in a later commit does not unexpose it. Terms are **masked** in the
output because this report gets pasted into handoffs and PR bodies that are themselves tracked
or backed up, and main's accepted historical baseline is deliberately not re-reported.

⚠ **Those three legs CANNOT see a PR title or body — screen it with `--text` before you post.**
A body is neither the tree nor a commit, so the sweep reports `clean` on one naming every term:
correctly, and uselessly. It happened live on #245, whose first draft named all three in the
very window the gate exists to make safe. Write the body to a file (which CLAUDE.md requires
anyway — a heredoc is the command payload and trips the destructive guard), then:

```bash
make post-branch-text FILE=docs/plans/pr-<branch>.md
printf '%s' "$TITLE" | make post-branch-text FILE=-
```

`FILE=-` reads stdin, and the flag is repeatable on a direct invocation. Unlike the sweep it
**gates** — exit 1 on a hit, though ⚠ through `make` you see make's own 2, so read the banner.
It needs no git surface. Output is line numbers plus a masked term and **never the matching
line** — quoting context would reproduce what the masking withholds. It reads the same
gitignored list, so `NOT CONFIGURED` is a finding here too, and an unreadable `FILE` exits 2
rather than rendering as a clean one-check run. **This belongs in phase 5, beside
`make preflight`, not after `gh pr create`** — a posted body is public the moment it lands and
editing it later does not unpublish it.

⚠ **`queue-items` reports RELEVANCE, not closure**, and prints the tokens it matched so you can
dismiss in a glance. Expect false positives from area vocabulary, and expect one item to match
itself. **Do not "fix" it by matching action phrases** — its first true positive also matched on
nouns; the reasoning is pinned in `check_queue_items`'s docstring.

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
- **`negative-claims` prints a `note:` line with three remainders, and one of them is
  work.** Claims *scoped out* are absence sentences elsewhere in the tree this diff does not
  touch — not dismissed, just not yours. Claims *exempt* carry a reason inline in
  `NEGATIVE_CLAIM_EXEMPT`. But the note also names claims whose only diff hit was a bare
  English word — ⚠ **those say "RE-READ, do not assume"**, and they are listed by
  `path:line` precisely so you open the paragraph. This leg's only confirmed true positive
  was found that way, by a human re-reading a paragraph rather than a matched clause.
  A claim it cannot scope at all is still **reported**, so `(no token to scope on)` means
  "could not rule this out", not "certainly stale".
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
NEW=<path>                 # the doc as it stands on this branch
OLD=/tmp/old.md
git show "main:$NEW" > "$OLD"
comm -23 <(grep -oE '`[^`]+`' "$OLD" | sort -u) \
         <(grep -oE '`[^`]+`' "$NEW" | sort -u)   # in old, gone from new
```

⚠ **No `$1` anywhere in that block, deliberately.** A shell positional inside a
skill code block has been observed rendering **substituted** rather than literal, so a
copied helper silently reads a path nobody passed. Named variables render literally.
See the handoff's skill queue for what is and is not established about when that
happens.

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
  ⚠ **A changed rule usually has SIBLINGS, and finding them depends on whether it has
  an enforcement LOCUS.** A rule enforced in code gives you a string only a
  rule-carrying skill would cite, so `grep -rl "<that symbol>" .claude/skills/*/SKILL.md`
  decides the set. **A PROMPT-SIDE rule has no such locus, and that is the common case,
  not the edge one** — every candidate spelling is either prose a sibling would phrase
  differently or a field name it mentions anyway. There the grep can only NARROW the
  candidates; you then have to READ each hit for the rule's *substance*. Ported from
  parent #738. ⚠ **Port the method, not its finding**: upstream measured `/ingest-x`
  missing two Stream C rules `/ingest-video` had, and that divergence does **not**
  reproduce here — both skills carry the "unless it reclaims" trap rule (checked
  2026-09-02).
- **The handoff** is the surface with no other check: gitignored, so no reviewer
  ever sees its drift, and its group A is headed *"Settled — do not
  re-litigate"*, which means a stale line there **instructs** rather than merely
  misinforms. `queue-items` and `handoff-symbols` cover it mechanically; read
  their hits here and fix in phase 6.

---

## Phase 4 — Always-run, regardless of the gate

### MEMORY.md

Rewrite "Last session" to today's date + branch + a one-line summary. **The roll
is N-way, and N comes from the LIVE index, never from this sentence**: today →
**Last session**, each existing dated session bullet down one slot, and the
oldest rolls verbatim into `memory/project_session_log_<month>.md`. With no
`Prior session` bullet in the index — its current shape — that is a two-way
roll; ⚠ do not add one to match an older description of this step, since the
cap is 6 bullets / ~17KB. Grep the log afterwards to confirm the rolled bullet
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

⚠ **The cap is BYTES as well as bullets, and the two fail differently.** Bullets
are fixed by the roll; **17,408 bytes is not**, and the roll can break it on its
own — on 2026-08-20 it pushed the index past the cap, and shaving the new bullet
twice did not bring it back under. **Re-home the LARGEST bullet to its topic file
before trimming the new one**, then grep the destination to confirm it landed.
The index is a router, so the biggest bullet is nearly always the one that has
quietly become a store — and its topic file usually already owns every detail it
is carrying.

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

⚠ **Re-read every item filed as BLOCKED and name what would unblock it.** A
blocker that has since been written down does not announce itself, and a blocked
item is precisely the one nobody re-reads *because* it is blocked. Hit live on
2026-08-21: skill-queue item 18 sat blocked on the SoT's `skill-claims` design,
that design was written days later, and the item stayed blocked until someone
re-read it by accident. This covers the handoff's queues as well as SoT rows —
the blocker and the blocked item usually sit on different surfaces, which is why
neither notices. **Naming the unblocking condition is the deliverable**; an item
whose blocker you cannot restate is not blocked, it is unexamined.

### Claims audit — run whenever the branch ADDS PROSE

Nothing else checks whether the **numbers the branch itself asserts** are true.
Extract every quantitative claim from the branch's new prose — commit messages,
audit docs, PR body, CLAUDE.md and context additions, the handoff, and the code
comments and docstrings the diff adds — **and for each, name the query or
command that reproduces it.** A claim whose reproduction you cannot state is
not ready: cut it, soften it to what you measured, or measure it. Record the
reproduction in the commit or the audit doc, not just the session.

⚠ **A commit message is audited prose, and by phase 4 it usually already
exists.** The phase order puts the audit before the commit, but the normal flow
commits while the work is fresh, so a failing claim is typically already written.
On an unpushed branch the repair is `git commit --amend -F <file>`, never a
follow-up wording commit that leaves the false claim in the history the PR ships.
After a push, amend plus force-push needs explicit OK — otherwise correct the
claim in the PR body and say the message predates it.

⚠ **A code comment or docstring is audited prose too — and it is the surface the
author writes rather than one a reviewer reads.** Everything else on that list
gets re-read by someone deciding whether to merge; a comment reads as the
author's own reasoning rather than as a claim, so nobody re-derives it. Live on
2026-08-21 (#257): the verdict ratchet's frozen grandfather set was justified by
a test-file comment reading *"eight of the ten are cited by name in CLAUDE.md's
footguns"*. It is **seven** — one `grep -c <filename> CLAUDE.md` per member away,
and caught only because that comment was audited off-list. A wrong number beside
a frozen set is durable: it is the justification the next session inherits for
not re-checking the set.

Six shapes to hunt:

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
6. **A claim about a SERIES, checked only against the repo.** ⚠ **Grep the MEMORY
   TREE too.** A branch appending to a running measurement — costs, counts, timings —
   and saying "the first ever to go DOWN" is making a claim about the series'
   **COMPLETENESS**, not its values, and a series that merely looks monotone is
   evidence of what somebody remembered to write down. **No mechanical leg can catch
   this**: `queue-items` and `handoff-symbols` read the handoff, every other leg is
   repo-scoped, and `stale-anchors` is the one leg reaching memory but it checks
   anchors rather than data. So ask what else measured the SAME QUANTITY and never
   reached the series. Ported from parent #733, where the falsifying datapoint sat in
   an account-level memory file, filed as a defect instead of appended.

---

## Phase 5 — Commit, push, then open the PR

```bash
git add <files>
git commit -F <file>      # -F, never a heredoc: quoting a hazard trips the guard
git push -u origin <branch>
```

⚠ **Then run the clean-clone pre-flight, and let it REPLACE this branch's `make test`.**

```bash
make preflight            # background it; it runs the whole suite in a fresh clone
```

⚠ **Scope it with a positive CHECK, never a judgement — and the default stays RUN.**
Two greps decide: does the diff contain Python, and does any test read a changed path
(`grep -rl <changed-path> tests/`)? If both answer no, the clone re-runs the whole suite
only to reproduce `main`'s own result — say in the PR body which gate you ran instead,
naming the two greps. ⛔ **"This looks harmless" is not the discriminator**; a gate with
no stated scope makes the correct call look like a deviation, which is how it gets
dropped later on a diff that DID need it. ⚠ **Re-derive the COST here rather than
porting the parent's**: upstream cites 4,762 tests it could not affect, while wifey's
preflight is ~4 min over ~3,450 tests — smaller, and still the whole reason this scope
line exists, since a session burned exactly that on 2026-09-02 by running `make test`
AND preflight on one branch. Ported from parent #733.

A gitignored path that exists on this box and nowhere else is invisible to every
local run — `config/stocks.json`, `.claude/sensitive-terms.txt`, `docs/plans/`
and `analytics.db` are all absent on a clean clone. CI already is that clone, so
this closes **timing, not detection**: catching it here costs a local suite run,
catching it after the push costs a metered Actions cycle, a red PR, and a
visibility flip to read the failure at all.

It **must** run after the commits — it refuses on a dirty tree, because a clone
sees committed state only and would otherwise test stale HEAD and report green.

⚠ **`make` collapses the exit code, so read the banner**: `REFUSED` (dirty tree)
and `INFRA` (clone or install died) are **not** suite failures. Its two blind
spots are stated in `tools/clone_preflight.py`: an absolute `$HOME` default
(`EXTERNAL_ROOTS`' shape), and a CLI branch no test reaches.

⚠ **Then decide the visibility flip, before `gh pr create`** — this phase covered
only the exit until now. Phase 2 has already read the diff: if it touches
`**/*.py` or `web/ui/**`, the PR needs the repo **public**, or its three
path-filtered checks create zero steps and settle at `steps=0`, which renders
exactly like a real failure. **Confirm the flip with the user on every
occasion** — CLAUDE.md makes the mechanics standing authorisation and the timing
not, because the window republishes the parent's pre-fork commits.

⚠ **"A docs-only diff skips it" is CONDITIONAL — the diff's paths are not sufficient.**
That reasoning is about *your diff*; the Actions allowance is about *the account*.
`markdownlint` and `Trivy` have **no** path filter, so an exhausted allowance zeroes them too:
PR #238 opened docs-only and settled with **all five** checks at `steps=0`. **The tell is the
CONCLUSION, not the step count: a path-filtered skip reports `SKIPPED`, an exhausted allowance
reports `FAILURE`, both at `steps=0`.** So skip the flip only if the last run on this repo
executed real steps; otherwise flip, and requeue the runs (enumerate them — a docs-only diff
gets **two**, not three, since `Docker Build` is itself path-filtered).

⚠ **A flip is a REPO-WIDE event, so sweep EVERY open PR — and do it BEFORE the flip,
not after.** The public window is the only moment any other PR's checks can run, and while
the repo IS public further pushes and re-runs are **free**. Dependabot PRs are where this
bites, because nobody is watching them. **Enumerate the open PRs and read their step counts
first**: `steps=0` with conclusion `FAILURE` is exactly the shape a re-run repairs at zero
marginal cost inside a window you are opening anyway.

```bash
GH_TOKEN=$(gh auth token --user s10023) gh pr list \
  --repo s10023/buibui-wifey-wall-street-bot --state open \
  --json number,title,headRefOid
```

⚠ **This is deliberately NOT a `post_branch_checks` leg** — the sweep needs `gh`, and phase 1
is git-only on purpose. Measured 2026-08-21: #252/#253 sat at `FAILURE` on all six checks with
`steps=0`, were re-run in place during an already-open window, and both went fully green for
nothing. That case was caught only because the operator prompted for it.

Phase 6's flip-back gate closes the other half of the pair.

Then compose the **Documentation updates** section and pass it in the *initial*
`--body`. That ordering is the whole payoff — do not open the PR and then edit
its body.

⚠ **Screen the composed title and body before they post** — they are the fourth
exposure surface and the only indexable one, and the sweep's three git legs
cannot see either (phase 1). Do it while the text is still a local file:

```bash
make post-branch-text FILE=<body-file>
printf '%s' "$TITLE" | make post-branch-text FILE=-
```

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

**Sweeping every open PR is a PHASE 5 decision** — see it there; by the time you reach this phase the window has already been spent.

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

**The gate is `handoff-size` in phase 1, and it has an EXTERNAL referent.** It
fails the file above `HANDOFF_MAX_LINES` (240) — a real threshold, checked
against `wc -l`, not against a number the file carries about itself.

⚠ **The self-describing `Line count:` stamp is GONE, and so is the leg that
checked it.** They died together, deliberately. The stamp cost a
read / `wc -l` / edit / re-read / re-verify cycle on **every** run to maintain a
figure whose only consumer was the check that verified it, and the check
returned `[]` when the stamp was absent — so removing the stamp alone would have
left the leg **vacuously green forever** rather than red. Do not reintroduce
either. If you want to know the size, run `wc -l` or `make status`.

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

#### Operator actions must resolve in THIS repo

Name the exact command, target or unit, **and verify it exists here before
writing it**. A previous handoff carried "restart signal watch" for a daemon this
repo does not have — the `buibui-signal-watch` units belong to the crypto parent.
Wifey dispatch is the one-shot `make go-live`, by hand or via the opt-in
`wifey-signal-watch.timer` that runs it. **A unit existing on the machine is not
evidence it belongs to this repo**: read `WorkingDirectory`, check
`grep -n '<target>:' Makefile`, check `wifey <cmd> --help`. ⚠ That check got
*harder* on 2026-08-25, not easier — `wifey-signal-watch.*` and
`buibui-signal-watch.*` now differ by prefix alone, so a name is no longer even a
weak signal, and neither is "it fired recently".

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

⚠ **No PR opened → STRIKE the placeholder; there is no query to fill it from.**
Phase 4 omits the number because phase 5 is *assumed* to create the PR, and until
now this step had no branch for the operator declining one — so the placeholder
sat waiting for a fill that never came. Hit live 2026-08-25b, where a fake PR
reference stood on two surfaces until a human caught it. Where the branch stayed
local, **remove** the placeholder and write the SHA plus "local-only, still
amendable". An absent number is a state to report, and a slot shaped like a PR
reference gets filled with one: the next session reads `#NNN` as a lost lookup
and the nearest plausible number as a fact.

**This is the last action of all** — there is no stamp to write after it.

---

## Output format

```text
phase 1 sweep      — <n> findings triaged: <what> | clean
behaviour gate     — walked | skipped (<reason>)
CLAUDE.md          — updated: <what> | no change needed: <reason>
README.md          — …
MEMORY.md          — Current State rolled N-way per the live index  (never committed)
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

- **Confirm every edit, except the two surfaces marked `unprompted: true`.**
  This skill proposes; the user approves. The exceptions are **`MEMORY.md`** and
  the **handoff** (`docs/plans/next-conversation-prompt.md`), which the standing
  account-level protocol says to write unprompted:
  **`~/.claude-personal/CLAUDE.md` § "Session hygiene", lines 54-59 as of
  2026-08-20 — read the rule there, never restate it here.** A restated copy is
  exactly how this rail and that protocol came to disagree. Section name *and*
  line range on purpose: a line number drifts silently and a heading does not, so
  the pair catches its own decay.
  ⚠ **"Untracked" is the wrong scope for the carve-out.** `git ls-files
  docs/plans/ | wc -l` returns 0, and that same tree holds `pundit-calls.jsonl`
  and the thesis inbox — single-copy research that most needs a human. So:
  confirm git-tracked surfaces **and** untracked single-copy data alike, and let
  the exception be **two named files**, never a directory class.
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
