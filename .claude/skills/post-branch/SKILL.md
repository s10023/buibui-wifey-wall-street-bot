---
name: post-branch
description: >
  Post-branch docs sweep + handoff — diff the branch's behaviour changes against
  the doc surfaces (CLAUDE.md, README.md, MEMORY.md, Makefile, docker-compose.yml,
  .claude/context/*.md, .claude/skills/*/SKILL.md)
  and propose targeted edits where they've drifted, then run a pre-merge
  readiness check and offer a fresh-conversation handoff prompt. Use BEFORE
  `gh pr create`, while the branch is still local-only, and fold the resulting
  "Documentation updates" section into the initial PR body.
  Skip for pure refactors, bug fixes covered by tests, dependency bumps, and
  lint-only commits — the behaviour gate (Step 1) decides. Confirm every edit
  before writing; never force-push without explicit OK. Also triggers on the
  user saying "/post-branch", "wrap up the branch", "docs check",
  "pre-merge check", or "next conversation prompt".
allowed-tools: Bash, Read, Edit, Write
---

# Post-Branch Docs Sweep

The mental model: a PR's diff is the source of truth for what changed. The
docs are claims about how the codebase behaves. After a PR introduces new
flags, scripts, defaults, files, or commands, those claims often go stale —
sometimes silently. This skill walks a fixed list of doc surfaces, diffs
each one against the PR's actual behaviour, surfaces the drift, and proposes
edits the user can approve.

It runs **before the PR exists** — on a branch that is committed and pushed but
not yet opened. Its job is not to gatekeep the PR but to catch doc drift while
fixing it is still free.

**Why before, not after** (user decision, 2026-08-06): these are private repos on
the free tier, so GitHub Actions minutes are a hard budget. Sweeping after the PR
is open means a second push to an open PR, and every push re-runs the full matrix
— five checks here (`markdownlint`, `Trivy filesystem scan`, `lint-typecheck-test`,
`frontend-check`, `Regression tests`) for what is usually a two-file docs edit.
Sweeping first costs one CI run instead of two and the reviewer sees the same
final tree either way. Practical consequence: **Step 6 composes the initial
`--body` rather than editing an existing one**, and Steps 7→6 swap order.

## Order of operations (the step numbers are historical — follow THIS)

The steps below are numbered from when the sweep ran post-PR. The numbering is
kept so existing references still resolve, but the running order is now:

1. Steps 1–5c — behaviour gate, changed artifacts, doc walk, surface checks,
   MEMORY.md, **SoT reconcile**, **claims audit**. All pure local work; no PR,
   no `gh`, no network. Steps 5b and 5c write nothing to the repo (the SoT lives
   outside it, and the claims audit edits prose the branch already has), so both
   are free of CI either way.
2. **Step 7** — commit the doc edits and `git push -u origin <branch>`.
3. **Step 6** — compose the "Documentation updates" section.
4. `gh pr create --body …` with that section already **in** the initial body.
5. Steps 10a (pre-merge check), 10b (handoff), **10c last** (re-verify PR state).

Steps 6 and 7 are therefore swapped relative to their numbers, and everything
touching `gh` moves after step 4. Step 10c genuinely needs a PR to exist, so it
stays where it is — and it stays **last**.

---

## Doc-surface configuration

Each entry is a class of doc that might need updating when behaviour
changes. **When porting this skill to another repo, edit only this block —
the rest of the workflow stays the same.**

```yaml
surfaces:
  - id: claude_md
    path: CLAUDE.md
    purpose: Authoritative project context for Claude Code (project structure, key commands, code style, agent skills)

  - id: readme
    path: README.md
    purpose: User-facing project overview (CLI subcommands, install, quickstart)

  - id: memory_md
    path: ~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/MEMORY.md
    purpose: Cross-session memory; "Current State" section MUST be updated every session
    always_update: true   # see Step 5

  - id: makefile
    path: Makefile
    purpose: Make targets — every `wifey.py` subcommand should have a `wifey-*` wrapper
    scope: any_referencing_changed_artifact

  - id: docker_compose
    path: docker-compose.yml
    purpose: Long-running services (daemons → restart:unless-stopped) and one-shot tools (profiles:[tools])
    scope: any_referencing_changed_artifact

  - id: context_docs
    path_glob: ".claude/context/*.md"
    purpose: Long-form module references (analytics, tools, signals, web, config)
    scope: any_referencing_changed_artifact + new_module_presence   # see below

  - id: skill_docs
    path_glob: ".claude/skills/*/SKILL.md"
    purpose: Workflow instructions that name tools, flags and file paths — they drift exactly like CLAUDE.md does
    scope: any_referencing_changed_artifact
    lint: manual   # see below

# Files that, if changed, almost always require a doc walk:
behavior_signal_globs:
  - "wifey.py"
  - "cli/**/*.py"
  - "Makefile"
  - "docker-compose.yml"
  - ".github/workflows/**/*.yaml"   # NOT *.yml — every workflow here is .yaml
  - "pyproject.toml"
  - "config/strategy_params.toml"
  - "config/*signal_watch*.toml"

# Files that almost never require a doc walk (internal-only refactor space):
behavior_skip_globs:
  - "analytics/**/_*.py"          # underscore-private package internals
  - "analytics/**/*.py"            # detector / signal / store internals (per-PR judgement)
  - "tests/**"
  - "**/*_test.py"
  - "poetry.lock"
  - "*.parquet"
  - "tests/fixtures/**"
```

The `behavior_signal_globs` and `behavior_skip_globs` are heuristics, not
absolute rules. A move that adds a new public symbol *is* user-facing even
under `analytics/**`. Always read the diff before deciding.

---

## Step 1 — Behaviour gate: is this PR user-facing?

Before walking any docs, decide if the PR changes behaviour a user or
operator would notice. **If not, stop after MEMORY.md update — don't churn
docs for invisible changes.**

Read the branch's diff. There is normally **no PR yet**, so this is pure git —
no `gh` call, and no network:

```bash
git diff main...HEAD --stat
git diff main...HEAD -- .
git log main..HEAD --oneline
```

(Running late, on a branch whose PR already exists? `gh pr view <PR#> --json
title,body,baseRefName,headRefName,files` still works — but prefer the git form,
which is faster, offline, and correct in both cases.)

User-facing signals — **walk the docs** if any are present:

- New CLI subcommand or flag (`wifey.py`, `cli/`)
- New Make target or changed default
- New TOML config key or changed default
- New environment variable
- Renamed or moved file referenced from docs
- New error class users will see (new exit code, new alert format)
- New external dependency or system requirement
- Behaviour change to an existing public command
- New long-running daemon or one-shot tool (docker-compose)
- **The diff touches `analytics/store/schema.py`** — see the DB-migration block below

### DB migrations are operator-facing even when nothing else in the PR is

If the diff touches `analytics/store/schema.py`, the doc surfaces do not cover
what the operator needs to know, because a schema change **applies silently at
the next `init_schema`** — there is no command to run and no output to read.
The handoff (Step 10) must therefore state, explicitly:

1. **When it applies** — next `init_schema`, i.e. the next process that opens
   the DB. Name that process if it is a scheduled one.
2. **Whether existing rows stay readable**, and if not, what breaks.
3. **Whether any manual step is needed** — a backfill, a `make db-update`, a
   `clean-db`, or nothing.
4. **Whether it is self-healing**, and *why*. A nullable column on a table whose
   rows age out on their own needs no backfill; a nullable column on a table
   that accumulates does.

PR #151 is the worked example: `backtest_cache` gained two nullable columns, and
because the cache key includes `last_candle_ts`, pre-migration rows aged out
within one bar and read as "abstain" until they did — additive, no manual step.
None of that is derivable from the diff, and it was written by hand because
nothing prompted for it.

Also confirm the positional-INSERT guard still passes
(`tests/test_schema_insert_arity.py`, in `make test`): adding a column to a
table written by a bare `VALUES (?,…)` or an `INSERT … SELECT` requires updating
that statement in the same PR.

Skip signals — **stop here** (after MEMORY.md update) if the PR is purely:

- Internal refactor that preserves the public API surface (byte-identical
  re-export shim, registry/key order preserved, etc.)
- Bug fix with a regression test added and no behaviour change
- Dependency version bump with no API change
- Lint/format-only commit — **the formatting itself, not the linter's
  configuration.** Editing `.markdownlint*`, `pyproject.toml`'s ruff/mypy
  blocks, or a CI job's globs/path filters changes what the build *enforces*,
  which is operator-facing however mechanical the accompanying diff looks.
  Walk the docs for those.
- Test-only changes
- Comment/docstring edits inside source files (not in the doc surfaces)
- Regression-fixture refresh (`make regression-update`) with goldens unchanged

That carve-out is not hypothetical: PR #133 was a `chore(lint)` whose diff was
245 whitespace and fence-tag fixes plus one deleted glob line. Read as
"lint-only" it would have skipped the walk — and missed both that it had
falsified this file's own step 4 and that the CI job silently skipped the very
tree it was triggered by. **Judge the gate on what the change enforces, not on
what the diff looks like.**

**Strong refactor signals** — these almost always trigger user-facing doc
edits because they change paths users / docs reference:

- A module listed in CLAUDE.md's "Project Structure" was renamed, moved, or
  reduced to a re-export shim (the path users `import` from is now stale)
- The CLI subcommand surface changed (`wifey --help` differs)
- A new `make wifey-*` target lands

When in doubt, ask the user: *"This PR touches X. I see [signals]; want me
to walk the docs, or is this internal-only?"*

---

## Step 2 — Identify changed artifacts

From the diff, build a concrete list the doc walk will key off:

- Each new/renamed/deleted **file** (especially modules listed in CLAUDE.md
  Project Structure)
- Each new **CLI flag/subcommand** in `wifey.py` / `cli/`
- Each new **Make target** (lines added like `^[a-z_-]+:` in `Makefile`)
- Each new **TOML config key** or changed default in `config/*.toml`
- Each module that became a **shim** (line count drops drastically and body
  is just `from X import …`) — the path users `import` from now points to
  thin re-exports rather than real code

Keep this list short and concrete — it's the basis for every doc diff.

---

## Step 3 — Walk each doc surface

For each surface in the config, do the following:

1. **Locate the relevant files.** Use `path` or `path_glob`. For `scope:
   any_referencing_changed_artifact`, grep the doc tree for the artifact
   name (script name, flag, Make target, module path).

2. **Read the doc.** Look for:
   - Outdated examples (old flag names, removed scripts)
   - Missing entries (new flag/script/target absent from the listing)
   - Broken file paths (post-rename, post-shim)
   - Stale defaults
   - Stale module-purpose descriptions ("module X holds Y" when Y has moved
     to the package next door)
   - **Negative claims** — sentences asserting the thing the PR just built
     does *not* exist ("not ported", "no reader", "until that port lands").
     These are the most dangerous class and the easiest to miss; see Step 3b.
   - **A stale VALUE under a correct KEY** — see 3.1 below.
   - **A behaviour the PR REMOVED or NARROWED** — see 3.2 below.
   - **A doc that needs no edit because it was right all along** — see 3.3.

3. **Decide if an edit is warranted.** Bias toward minimal, targeted edits.
   Don't rewrite docs that aren't affected. If a `README.md` doesn't mention
   the changed artifact at all and never did, leave it alone — **but "doesn't
   mention it" has to survive Step 3b first.** A doc can be entirely about the
   artifact while never naming it, by describing its absence.

4. **Propose the edit.** Show the user a unified-diff-style proposal:

   ```diff
   # CLAUDE.md (line 47)
   - - `data_store.py` — DB schema, upsert/query helpers, `confidence_ratings`, …
   + - `store/` — package: `schema.py`, `signals.py`, `backtest_runs.py`,
   +   `backtest_cache.py`, `confidence.py`, `combos.py`, `stats_cache.py`.
   +   `data_store.py` is a re-export shim for the 30+ external import sites.
   ```

   Wait for confirmation before writing.

5. **Apply via the `Edit` tool.** Never use `Write` to overwrite a doc —
   always targeted edits.

### 3.1 — A stale VALUE hides under a correct KEY

The name grep in step 1 matches on the key, so a doc that names the right key
with the wrong value reads as a hit and then passes review. **When a PR changes
what a config key DOES, grep the key across every doc surface and compare the
documented VALUE against the config's current value**, not just the key's
presence. Confirmed on PR #146, and it had bitten before that.

### 3.2 — Removals have no new symbol to grep

Steps 2–3 key off the changed artifact's name. A PR that *removes*, *narrows*,
or *disables* a behaviour introduces no new symbol, so the entire walk has
nothing to search for and comes back clean. **Grep the name of the artifact that
was constrained — not the name of the thing that replaced it.** Confirmed on
PR #143. Note this is the mirror of Step 3b: 3b covers additions falsifying
negative claims, 3.2 covers removals leaving positive claims behind.

### 3.3 — The doc can be right and the code wrong

The whole sweep is framed as "did the docs drift from the code". Check the other
direction too: **when a PR changes behaviour that a doc already describes, diff
the doc's claim against the PRE-fix code, not only the post-fix code.**

If the doc already described the corrected behaviour, that is evidence the bug
was a *bug* rather than a design choice — and the fix restored a calibration
instead of choosing a new one, which changes how the PR body and handoff should
describe it. PR #150 is the case: `README.md` said the EV gate's guard was
"applied to directional trade count" and the ladder was "calibrated from DB p25
directional counts". Both were false of the code and true of the intent. The doc
needed no edit, and that silence was the most informative thing in the sweep.

**A doc/code mismatch is not automatically doc drift.**

---

## Step 3b — Negative-claim sweep (run whenever the PR ADDS something)

Steps 2–3 search the docs for the changed artifact's **name**. That finds every
doc that already talks about the thing. It structurally cannot find the docs
that talk about the thing's **absence** — and a PR that adds a capability turns
every such sentence into a false statement in one commit.

This is not hypothetical. PR #127 added `tools/pundit_score.py` and three
surfaces asserted the repo had no scorer. Two named the file, so the name grep
caught them. The third did not have to: had it read *"routed rows accumulate
unscored"* with no filename, the sweep would have passed clean while leaving an
instruction telling the next session to **tell the user something false**.

So run a second grep keyed on absence-language, not on the artifact. Note the
`-o`: it prints the **matched phrase** rather than the line, which matters here
because CLAUDE.md's Project Structure entries run to several thousand characters
each and printing whole matching lines buries the signal (the first draft of this
step did exactly that — 49 hits, most of them unreadable walls).

```bash
git grep -nEio \
  "(never|not) (yet )?ported|no (reader|host|consumer)\b|this repo has no|\
until (that|the) port lands|silent accumulator|accumulates? unscored|\
is not (yet )?(available|implemented|wired)" \
  -- CLAUDE.md README.md Makefile docker-compose.yml .claude
```

Output is `path:line:phrase`, one short line per hit — measured at **14 hits on
this repo, 2026-08-04**. Open only the ones whose surrounding topic overlaps this
PR; most are true statements about unrelated gaps and must be left alone. Judge
by topic, not by keyword.

Scope notes, all deliberate:

- `docs/audits/` and `docs/redesign/` are **excluded**. They are dated historical
  records, and a past-tense negative claim in them is correct by construction —
  including them added ~35 hits, none actionable.
- Keep the pattern list *narrow*. Generic phrases (`for now`, `unwired`,
  `stop-gap`, bare `does not have`) each pulled in double-digit false positives
  for no additional catch.
- This file matches itself. Expected — skip `post-branch/SKILL.md` hits.

Three properties make this worth doing on every additive PR:

- One command, bounded output, no judgement needed to *run* it.
- Its false-positive mode is harmless (read a line, move on); its false-negative
  mode ships a doc that actively misleads the next session.
- **In a fork it doubles as a port check.** A doc copied from the parent can
  carry the parent's negative claim about *this* repo — true when written, in the
  other repo's context, and quietly wrong here.

Anything this turns up is proposed through the normal Step 3 flow. Prefer
replacing the negative claim with the positive fact plus how to use it, rather
than merely deleting the sentence — the sentence existed because a reader needed
to know the answer, and they still do.

---

## Step 4 — Surface-specific checks

### CLAUDE.md

- "Project Structure" section: every module listed should match its real
  current home. If a `*.py` file is now a shim, rename or annotate to
  point at the package that holds the real code.
- "Key Commands" / "CLI" sections: every subcommand should still resolve.
- "Agent Skills" table: skills added/removed since last sweep are listed.

### README.md

- CLI subcommand list matches `wifey --help`.
- Quickstart still works (commands referenced still exist).

### Makefile

- Every `wifey.py` subcommand has a `make wifey-<name>` target.
- Every public daemon has a `docker-up` / `docker-down` line.

### docker-compose.yml

- Long-running daemons → `restart: unless-stopped`.
- One-shot tools → `profiles: [tools]` so they don't auto-start.

### `.claude/context/*.md`

- Module references match the current package layout. These are the most
  refactor-sensitive docs.

- **`any_referencing_changed_artifact` is BLIND TO OMISSION — this is how
  `analytics.md` and `signals.md` rotted.** That scope greps the doc tree for
  the changed artifact's name. When a PR *adds* a package, grepping for `pead`
  finds zero hits, so the sweep concludes "no change needed" — when the correct
  conclusion is the exact opposite: the doc is missing a module. A scope that
  can only detect drift in things the doc already mentions can never detect the
  module it has never heard of. Same defect shape as markdownlint's `!.claude`
  glob: the check reported green because it could not see the files.

  **So for context docs, run a presence check, not only a mention grep.** For
  every module added or renamed in this PR, confirm the matching context doc
  gained an entry.

  **Key it off the branch diff, not off a directory walk.** The old version of
  this check looped over top-level directories, which made it blind one level
  down: `analytics/` is documented, so a brand-new `analytics/<sleeve>/` package
  — or any new module inside an already-documented package — never tripped it.
  That is the same omission blindness this bullet is about, reproduced in the
  check meant to catch it. The diff knows exactly what is new:

  ```bash
  # every module this branch ADDS vs. what the context docs actually document
  git diff main...HEAD --diff-filter=A --name-only -- '*.py' | while read -r f; do
    case $f in tests/*|docs/*|migrations/*) continue;; esac
    grep -rqs -e "$f" -e "$(basename "$f" .py)" .claude/context/ \
      || echo "UNDOCUMENTED: $f"
  done
  ```

  Renames need the same treatment — swap `--diff-filter=A` for `--diff-filter=R`
  and check the new path. A hit here is a prompt to judge, not an automatic
  edit: a private helper module may legitimately not warrant a context entry.

  Two top-level packages are knowingly absent and are **not** findings:
  `trade/` (empty placeholder, both files 0 bytes) and `migrations/` (still
  undocumented — it is carried debt, tracked in the handoff).

- **CLAUDE.md must not re-absorb this content.** The 2026-08-05 split left
  CLAUDE.md holding a package index plus verdicts and footguns, and the context
  docs holding the detail. Measured at the time of that split: CLAUDE.md's
  `analytics/` bullet held 155 tokens that `context/analytics.md` did not, and
  `context/signals.md` still claimed 20 strategies (naming the long-deleted
  `funding_reversion`) where CLAUDE.md correctly said 18. Both files described
  the same packages; the auto-loaded one was visibly wrong so it got maintained,
  and the on-demand one silently diverged. **Two sources of truth, one of them
  invisible, always rots the invisible one.** If a PR adds module detail to
  CLAUDE.md's Project Structure, move it.

### `.claude/skills/*/SKILL.md`

- A skill that names a tool, flag, path or constant drifts exactly like
  CLAUDE.md does. Grep the skill tree for the changed artifact's name — a
  renamed flag or a moved module leaves a skill quietly instructing the next
  session to run something that no longer exists.
- **Ported skills drift against the fork, not just against time.** This tree
  came from the crypto parent, so a skill can be internally consistent and
  still wrong here: check every `buibui-*` Make target, `buibui --help`, and
  `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/…` path
  against this repo's equivalents. Four such artifacts survived in this file
  alone until 2026-08-03, including a memory path that would have sent a
  session's `Current State` update into the parent repo.
- **`.claude/` is covered by `make lint-md`** as of 2026-08-05 (a dedicated
  `chore(lint)` pass cleared a 245-issue backlog, then dropped `!.claude` from
  the globs — 83 files, 0 issues). A skill edit therefore lints like any other
  file and reddens CI on a violation, with no special invocation to remember.
  Keep it that way: the backlog reached 245 precisely because nothing enforced
  it, and re-adding the exclusion would restart that clock.

  **Historical note — still true of the crypto parent, which keeps the
  exclusion.** When a tree *is* excluded, passing an explicit path does not
  override the glob, and it **does not error**: it silently lints the other
  files and prints `Summary: 0 issues in 0 files`, indistinguishable from a
  clean pass on the file you meant. `--no-globs` is the override, and the
  falsifier is the file count — the output must name as many files as you
  passed:

  ```bash
  npx markdownlint-cli2 --no-globs "<path>/SKILL.md"   # -> "Linting: 1 file"
  ```

  Running from outside the repo works too, but then `--config` is mandatory or
  you lint against markdownlint's *defaults* rather than the repo's rules —
  `MD013` (line length) fires on every prose line while a real violation hides
  in the noise.

---

## Step 5 — MEMORY.md update (always)

Regardless of the behaviour gate, **always update MEMORY.md's "Current
State"** at the end of every session. This is project policy (CLAUDE.md
"Session Memory Protocol"):

- Set "Last session" entry to today's date + branch name + one-line summary
- Move the previous "Last session" entry to "Previous session"
- Convert any relative dates ("Thursday") to absolute (`2026-05-01`)
- Update / remove open questions in `memory/project_open_questions.md`

**Respect the index cap — this step is where it gets broken.** CLAUDE.md's
Session Memory Protocol caps Current State at **6 bullets**, "Last session"
at 2 lines and every other bullet at exactly 1. Adding a 7th bullet means
first rolling the oldest, verbatim, into
`memory/project_session_log_<month>.md`. Writing a rich multi-sentence entry
here feels like diligence and is the exact mechanism by which the index grew
to 57% Current State by 2026-08-03 — and the index is re-read on **every**
session, so that bloat is billed per conversation, not per write.

Check before you finish:

```bash
awk '/^## Current State/,0' <MEMORY.md> | grep -c '^- '   # must be <= 6
```

This step runs even when the behaviour gate skipped the user-facing doc
walk, because MEMORY.md tracks **what changed in the session**, not just
behaviour-visible changes.

---

## Step 5b — SoT reconcile (always, and it is NOT covered by Step 5)

**Ask one question: does this branch close, change, or contradict a row in the
SoT** (`~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/project_todo_master.md`)?
If yes, reconcile it **now, in this same session** — move the row to **Closed**
with a one-line verdict, per that file's own rule ("Move items there with a
one-line verdict; never delete"). Like MEMORY.md it lives outside the repo, so
it is **never committed** and costs no CI.

Cheap way to find the row — search for the item ID and the PR number:

```bash
SOT=~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/project_todo_master.md
grep -n 'N1\|N3\|W4\|#159' "$SOT"     # the IDs and PRs this branch touched
grep -n 'OPEN\|not yet\|unfixed' "$SOT" | grep -i "$TOPIC"
```

**Why this step exists, and why it is separate from Step 5.** Nothing auto-updates
the SoT — the session-memory wiring (CLAUDE.md "Session Memory Protocol", this
skill's Step 5, `/sanity-check`) all touches **MEMORY.md**, not the SoT. **The SoT
predicted this failure in its own "How to use this file" section**; drift recurred
and nobody acted on the trigger, so the step is now here.

**The failure it prevents is misinformation, not clutter.** Measured on this repo
2026-08-12, three rows were stale in a way that would misdirect a fresh session:
`N1` still listed the **0.1 universe-as-of policy** and the **0.4 cost model** as
the remaining work when `analytics/backtest/cost_model.py` and the
`universe_policy` / `cost_model` columns were already live; `N3` asked to "expand
beyond the 13-symbol watchlist … start ~S&P 100" against a committed **505**-member
`config/universe.json`; and `W4` said "next `/sync-parent` scans `90a04e0..HEAD`"
after **two** scans had run. A session picking up work from the SoT would have
rebuilt shipped code.

**A stale row is worse than a missing one**, because it reads as current evidence.
If you are unsure whether a row is still true, do not leave it — either verify it
against the code or mark it unverified with today's date. **Reconcile to what you
verified, not to what is tidy**: a row that shipped *with a known residual gap*
gets the gap written down, not a blanket close.

---

## Step 5c — Claims audit (run whenever the branch ADDS PROSE; before Step 7's commit)

Every other step in this skill checks whether the **docs** drifted from the
**code**. Nothing checks whether the **numbers the branch itself asserts** are
true. On 2026-08-12 two false quantitative claims reached committed documents —
one into an audit doc that merged as #156, one into the #157 PR body, CLAUDE.md
and `.claude/context/analytics.md`. Both cost a retraction commit plus a duplicate
CI matrix: exactly the cost this skill's pre-PR ordering exists to avoid.

**Extract every quantitative claim from the branch's new prose** — commit
messages, audit docs, the PR body, CLAUDE.md / `context/*.md` additions, and the
handoff — **and, for each, name the query or command that reproduces it.** A claim
whose reproduction you cannot state is not ready to ship: cut it, soften it to
what you did measure, or go measure it.

Three shapes to hunt specifically:

1. **A claim about a MECHANISM supported only by a COUNT.** Both 2026-08-12
   failures had this shape — `"the run wrote zero ledger rows"` (grouped by the
   wrong clock) and `"13 rows re-stamped proves re-detection"` (they were first
   inserts in both tables). A count is consistent with many mechanisms. **Demand
   the query that rules the OTHER mechanisms out**, not merely one that produced
   the number. Where two writers use opposite conflict policies, their
   disagreement is a free discriminator.
2. **A claim inherited from the handoff counts as one of the branch's own.**
   #158's "23 rows past their hold window" was carried forward unchallenged for
   two sessions and was an artifact of reading a **bar** count as calendar days.
   If the branch repeats it, the branch owns it.
3. **An upstream number quoted as this repo's.** A ported fix's measured impact
   upstream is not wifey's; re-derive it here or say "preventive, not a repair".

Record the reproduction in the commit message or the audit doc, not just in
the session — that is what makes the next challenge cheap.

---

## Step 6 — Write the "Documentation updates" section (runs AFTER Step 7)

Once edits are approved, applied and **committed** (Step 7), compose a
"Documentation updates" section so reviewers see the doc reasoning:

```markdown
## Documentation updates

- `CLAUDE.md`: rewrote Project Structure entry for `analytics/store/` after
  data_store.py reduced to a re-export shim
- `README.md`: no change needed (no CLI surface change)
- `MEMORY.md`: Current State updated with strat-2 summary
```

**This section goes into the INITIAL `gh pr create --body`** — that is the whole
point of running the sweep first. Do not open the PR and then edit its body; that
second push costs a duplicate CI matrix.

State the no-change surfaces explicitly, with the reason. "no change needed:
internal refactor only" is useful to a reviewer; silence is not.

**Only if you are running late** (the PR already exists — you skipped the gate,
or a reviewer asked mid-flight), fall back to the fetch → append → push sequence:

```bash
gh pr view <PR#> --json body --jq .body > /tmp/pr_body.md
cat >> /tmp/pr_body.md <<'EOF'

## Documentation updates

- `<file>`: <what changed>
EOF
gh pr edit <PR#> --body-file /tmp/pr_body.md
```

If that body already has a "Documentation updates" section, edit it in place —
don't append a duplicate.

---

## Step 7 — Commit and push (runs BEFORE Step 6)

Commit doc edits as a single commit on the branch, then push, **then** open the
PR with the Step 6 section already in its body:

```bash
git add <files>
git commit -m "docs: sync docs with branch behavior changes"
git push -u origin <branch>
# ...then gh pr create --body "<...includes Step 6's Documentation updates...>"
```

Because the sweep now runs pre-PR, this push is part of the *same* CI run that
the PR's first run will use — no duplicate matrix. That is the ordering's entire
payoff, so don't open the PR before this commit lands.

**MEMORY.md is never committed.** It lives outside the repo under
`~/.claude-personal/...`, so it is not part of any project commit — save it
via the `Edit` tool only, and never `git add` it. If the MEMORY.md update is
the only thing this step produced, there is simply nothing to commit here;
say so and move on.

**Push rules:**

- Default: `git push` (no force).
- If a rebase happened, use `--force-with-lease` and **only** with explicit
  user approval. Never `--force`.
- Never push to `main` from this skill. Ever.

---

## Step 8 — Rebase handling (only when needed)

Sometimes a relevant doc lives on `main` but not on the PR branch (e.g. it
landed in a sibling PR). The diff at Step 3 won't surface it. If suspected:

1. Check if the doc exists on main: `git ls-tree main -- <doc-path>`
2. If yes and missing on the PR branch, ask the user:
   *"Doc X is on main but not this branch. Rebase onto main so we can
   update it here, or skip and let the next PR handle it?"*
3. Rebase only on explicit OK:

   ```bash
   git fetch origin main
   git rebase origin/main
   ```

4. Resolve conflicts the user's way, not by force.

---

## Step 9 — Output format

Output a per-surface report so the user has a clear summary:

```text
PR #<num> behaviour gate: <walked | skipped (pure refactor)>

CLAUDE.md          — updated: <what> | no change needed: <reason>
README.md          — updated: <what> | no change needed: <reason>
MEMORY.md          — updated: Current State + <other>  (never committed)
SoT reconcile      — <row> moved to Closed | <row> corrected | no SoT row affected
                     (never committed)
claims audit       — <n> claims, each with its reproducing query | no new prose
Makefile           — no change needed: no new CLI commands
docker-compose.yml — no change needed: no new processes
.claude/context/*  — updated: analytics.md (store/ paths) | no change needed
.claude/skills/*   — updated: <skill> | no change needed: <reason>
PR summary         — written to docs/plans/pr-<branch>.md   (slashes flattened to -)
PR body            — "Documentation updates" folded into the initial --body (1 CI run)
pre-merge          — clean | <blocker> (see Step 10a)
handoff prompt     — written to docs/plans/next-conversation-prompt.md | declined
PR state re-check  — #<num>: <OPEN | MERGED>, handoff table rewritten to match
```

**The PR-summary path flattens `/` to `-`.** Every branch here is `docs/…`,
`feat/…`, `fix/…` or `chore/…`, so a literal `docs/plans/pr-<branch>.md` names a
directory that does not exist and the write fails. `pr-summary/SKILL.md` owns
the rule and the exact derivation; this line is the sibling that referenced the
same artifact without it, which is the blind spot Step 4 describes.

Be explicit. "no change needed: internal refactor only" is useful;
silence is not.

---

## Step 10 — Post-PR handoff

After the doc walk closes, the user usually wants two more things before
moving on: a quick pre-merge readiness check, and a self-contained prompt
they can paste into a fresh conversation when this branch is done. Bake
both in here so the user doesn't have to ask each time.

### 10a — Pre-merge readiness check

Run a short status sweep and report any blockers in one line each:

```bash
git status --short                                      # working tree clean?
git log @{u}..HEAD --oneline 2>/dev/null || true        # unpushed commits?
gh pr view <PR#> --json mergeable,mergeStateStatus,reviewDecision,statusCheckRollup
```

Flag, do not fix:

- Uncommitted changes in the working tree
- Local commits not pushed to the PR branch
- `mergeable: CONFLICTING` or `mergeStateStatus: DIRTY`
- Failing required checks in `statusCheckRollup`
- `reviewDecision: CHANGES_REQUESTED`

Output one line per item. If everything is green, say so explicitly:
`pre-merge: clean — ready when you are.`

### 10b — Fresh-conversation handoff prompt

Offer (don't auto-write) to draft a self-contained prompt the user can
paste into the next conversation. Same shape as `/pr-summary` —
**file-only output, never inline**.

If the user accepts, write to **`docs/plans/next-conversation-prompt.md`** —
gitignored, but inside the repo and therefore durable. **Not `/tmp`:** the
user deletes conversations, and a handoff that evaporates on reboot defeats
the point. Overwrite the existing file rather than starting a new one; it is
a standing document whose whole value is being current, and keeping it so is
a final step of every task, not only of this skill. Structure:

```markdown
# Next conversation — <one-line context>

## Standing context — carry forward VERBATIM

<See "Standing blocks" below — project-level rules and guardrails that
outlive any one PR. Refresh only their dated "state at" lines.>

## READ FIRST — PR state (snapshot, re-verify before acting)

| PR | Branch | Contents | State at write time |
| --- | --- | --- | --- |
| #<num> | `<branch>` | <one line> | OPEN / MERGED |

**This table is a snapshot, not live state.** First move:
`gh pr view <num> --repo s10023/buibui-wifey-wall-street-bot --json state`.
If MERGED, sync main, delete the branch, and start on a task below — do not
re-litigate merged work.

## Just shipped
- PR #<num>: <title> — <one-line outcome / verdict / lift>
- Key finding: <the surprising or load-bearing result, if any>

## State of the world
<2–4 bullets, drawn from MEMORY.md "Current State" + the PR body —
what's live, what's in soft mode, what's still pending. Absolute dates.>

## Reference
- Memory: `~/.claude-personal/projects/<project-slug>/memory/MEMORY.md`
- <Other docs / tools / branches the next session will need>

## Suggested next tasks (pick one, or work in order)

### Task 1 — <name>
<2–4 sentences: what, why, where to start (file paths). Include the
"cheapest move" or "recommended endgame" framing if there's a clear
ranking.>

### Task 2 — <name>
<…>

### Task 3 — <name>
<…>
```

### Standing blocks — carry forward, never regenerate

This file is **overwritten** each run, so anything not in the template above is
silently deleted. Some blocks are standing operational content that belongs to
the project, not to this PR. **Before writing, read the existing
`docs/plans/next-conversation-prompt.md` and carry these forward verbatim**,
refreshing only their dated "state at" lines:

- **The standing guardrails** — the free-data-edge-arc honest exit ("do not
  start a #5 free-data hunt without an explicit user go"), the TA-detector
  freeze, and the `gh` rules (verify `gh api user -q .login` is `s10023`;
  always pass `--repo s10023/buibui-wifey-wall-street-bot`). These are
  standing decisions; a handoff that drops them invites a fresh session to
  redo work already ruled out.
- **The daily operator check** (`CATCH_UP=1 make go-live`) whenever it is
  live — there is no cron, so the handoff is the only thing that surfaces it.
- **Standing findings** — the accumulated gotcha list. Append to it; do not
  replace it with only this PR's findings.
- **Skill-fix queue** and **open questions** — these outlive any one PR.

This exists because a template that overwrites is a template that must name
what survives.

### PRUNE on every run — carry-forward is not append-only

**Standing rule (user, 2026-08-07).** Carrying content forward is not the same
as keeping all of it. Every run, delete from the handoff:

- **Merged PRs** beyond the most recent one or two. The PR-state table is there
  so the next session can verify what is *in flight*; a merged PR from four
  branches ago is git history, not state.
- **Completed tasks and closed findings.** A "Task 2 — ANSWERED, nothing to do"
  entry has done its job once the answer is in group A; keep the *decision*, drop
  the task slot.
- **"What #N found" narratives** once their transferable lesson is in group C.
  The lesson is the asset; the blow-by-blow belongs in the audit doc, which is
  committed and linked.
- **Skill-fix items that shipped.** Delete them outright — the code is the
  record. Do not leave `DONE in #146` tombstones.
- **Answered open questions.** Move the answer to group A, delete the question.

The test to apply to every line: **"if the next session never reads this, does
it do something wrong?"** If no, cut it. Anything worth keeping but not worth
re-reading every session belongs in a memory topic file or an audit doc, linked
by one line — not pasted here.

Left unpruned this file grows monotonically, and past ~500 lines the standing
blocks stop being read at all, which costs more than the deleted content ever
would. **Report the before/after line count** when you rewrite it, so the trend
is visible rather than discovered.

### Operator actions: verify the command resolves in THIS repo

When writing an operator action into the handoff, **name the exact command,
target, or unit — and verify it exists here before writing it.** Prose like
"restart the signal watcher" is not actionable and, worse, can be false.

A previous handoff carried "restart signal watch to pick up new ratings" for a
daemon **this repo does not have**. The `buibui-signal-watch.service`/`.timer`
pair in `systemctl --user` belongs to the *crypto parent*
(`WorkingDirectory=/home/kng/repo/buibui-moon-trader-bot`, `DATA_SOURCE=binance`).
Wifey dispatch is the manual one-shot `make go-live`, and because
`signal_runner.py` loads `confidence_ratings` "once at startup", a one-shot
process picks up a ratings change on its next run automatically. The instruction
was a non-instruction, and disproving it cost a full verification cycle.

Cheap checks before writing one: `grep -n '<target>:' Makefile` for a make
target, `systemctl --user cat <unit> | head -5` for a unit (read
`WorkingDirectory` — not just whether the unit exists), `wifey <cmd> --help` for
a CLI path. **A unit or command existing on the machine is not evidence it
belongs to this repo.**

**Keep the standing context in its four labelled groups — do not re-flatten it.**
Carried verbatim into one undifferentiated blockquote it reached ~90 lines by
2026-08-06, at which point a reader cannot tell a hard prohibition from a
`PYTHONPATH` reminder, and the block's own instruction to read it stops being
followed. The groups are ordered by when a session needs them:

| Group | Holds | When it is read |
| --- | --- | --- |
| **A — Settled decisions** | Concluded arcs, freezes, ruled-out work | Before proposing any task |
| **B — Workflow rules** | `/post-branch` ordering, `gh` invocation, subagent cap, background tests, MEMORY cap | Before running a task |
| **C — Evidence rules** | Re-derive-the-mechanism, the two backtest paths, window anchors, multiplicity, provenance | Before quoting any number |
| **D — Environment gotchas** | Ad-hoc script recipe, import paths, absent libraries | On demand, as a lookup table |

Open the block with a short note saying what it is, why it is long, and which
groups to skim versus read. Append new findings to the group they belong to —
a methodology lesson is **C**, not a new bullet at the end of **B**.

Source the content from:

1. **MEMORY.md "Next focus" section** — the top 1–3 entries are usually the
   right candidates. Convert any relative dates to absolute.
2. **This PR's findings** — if the PR closed an option or unblocked one,
   say so plainly so the next session doesn't re-ask.
3. **Open questions / pending decisions** — pull anything that becomes
   immediately actionable now that this PR shipped.

Keep it tight: 1–3 task suggestions, not a backlog dump. The goal is a
prompt that costs zero context to bring a fresh session up to speed.

Print only the path + a one-line description. Do **not** echo the
contents.

### 10c — Re-verify PR state as the LAST action (never skip)

This skill writes the handoff *before* the merge, so its most prominent
instruction is the first thing to go stale. A PR that merges minutes after
its handoff is written leaves the next session with a wrong opening move,
and the handoff is the one artifact that survives a session delete — so a
stale first line there is the most expensive kind of stale.

Immediately before you report done — after **every** other step, including
any commit and push — re-query every PR named in the handoff, not just the
one this run created:

```bash
gh pr view <PR#> --repo s10023/buibui-wifey-wall-street-bot \
  --json state,mergedAt --jq '"\(.state) \(.mergedAt)"'
```

Then rewrite the state table in place to match. If a PR merged in the
meantime, update the "first move" line too: the next session should be told
to start on a task, not to merge something already merged. If it merged and
the local branch still exists, say so — deleting the merged local branch is
standing habit here, and it is the natural first action for the next session.

One API call per PR. That is the whole cost of the difference between a
handoff that opens the next session productively and one that sends it down
a dead path.

---

## Safety rails (always)

- **Confirm every edit.** This skill is a proposer, not an applier. The
  user always gets a chance to say no.
- **Don't rename or move files.** Path churn breaks others' in-flight
  work. If a doc lives at the wrong path, propose the edit in place and
  flag the path issue separately for the user to triage.
- **Never use `Write` to overwrite a doc.** Always targeted `Edit`.
- **No force-push without explicit OK.** `--force-with-lease` only, after
  the user types yes.
- **Stop on uncertainty.** If you can't tell whether a doc claim is stale,
  show the user the doc snippet and the relevant diff hunk and ask.
- **Draft-PR default:** if `gh pr create` was run with `--draft`, don't flip
  it to ready-for-review as a side effect of this skill.

---

## When the skill should NOT run

- The PR is closed or merged (too late — open a follow-up `docs:` PR).
- The user said "skip docs" explicitly in the prompt.
- The PR is from Dependabot or another bot.
- The branch has no diff yet (PR was created against the wrong base).

In these cases, say so and stop.

---

## PR Summary template

The PR summary itself follows the template in
`.claude/skills/pr-summary/SKILL.md` exactly — read that skill before
writing. Do not compose from scratch or skip sections. The template
requires: PR Title, Background, Summary, How it works, Params/Config,
Test plan (CI items pre-ticked), Stats, and the Claude Code footer.
