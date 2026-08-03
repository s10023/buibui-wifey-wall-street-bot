---
name: post-branch
description: >
  Post-branch docs sweep + handoff — diff the branch's behaviour changes against
  the doc surfaces (CLAUDE.md, README.md, MEMORY.md, Makefile, docker-compose.yml,
  .claude/context/*.md, .claude/skills/*/SKILL.md)
  and propose targeted edits where they've drifted, then run a pre-merge
  readiness check and offer a fresh-conversation handoff prompt. Use IMMEDIATELY
  after `gh pr create` succeeds, BEFORE reporting the PR URL back to the user.
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

It runs **after** the PR exists. Its job is not to gatekeep the PR but to
catch doc drift before merge — when fixing it is still cheap.

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
    purpose: Long-form module API references (analytics.md, signals.md, web.md)
    scope: any_referencing_changed_artifact

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

Read the PR's diff:

```bash
gh pr view <PR#> --json title,body,baseRefName,headRefName,files
git diff main...<branch> -- .
git log main..<branch> --oneline
```

(If `<PR#>` is omitted, infer from the current branch with
`gh pr view --json number`.)

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

Skip signals — **stop here** (after MEMORY.md update) if the PR is purely:

- Internal refactor that preserves the public API surface (byte-identical
  re-export shim, registry/key order preserved, etc.)
- Bug fix with a regression test added and no behaviour change
- Dependency version bump with no API change
- Lint/format-only commit
- Test-only changes
- Comment/docstring edits inside source files (not in the doc surfaces)
- Regression-fixture refresh (`make regression-update`) with goldens unchanged

**Strong refactor signals** — these almost always trigger user-facing doc
edits because they change paths users / docs reference:

- A module listed in CLAUDE.md's "Project Structure" was renamed, moved, or
  reduced to a re-export shim (the path users `import` from is now stale)
- The CLI subcommand surface changed (`buibui --help` differs)
- A new `make buibui-*` target lands

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

3. **Decide if an edit is warranted.** Bias toward minimal, targeted edits.
   Don't rewrite docs that aren't affected. If a `README.md` doesn't mention
   the changed artifact at all and never did, leave it alone.

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

- Module API references (analytics.md, signals.md, web.md) match the
  current package layout. These are the most refactor-sensitive docs.

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
- **markdownlint cannot see these files.** `.claude` is in the ignore globs
  of `.markdownlint-cli2.jsonc` in both this repo and the crypto parent, so
  `make lint-md` skips the whole tree — and the exclusion still wins even if
  you pass an explicit path from the repo root. Run it from *outside* the
  repo, and **pass `--config` explicitly**:

  ```bash
  cd /tmp && npx markdownlint-cli2 --fix \
    --config "$HOME/repo/<repo>/.markdownlint.json" \
    "$HOME/repo/<repo>/.claude/skills/<name>/SKILL.md"
  ```

  Both halves matter. Without leaving the repo the ignore glob drops the
  file; without `--config` you silently lint against markdownlint's
  *defaults* rather than the repo's rules, so `MD013` (line length) fires on
  every prose line while a real violation hides in the noise. Dropping
  `--fix` is fine for a check-only pass.

  This tree has never been linted: a full pass over `.claude/**/*.md` reported
  **249 issues across 18 of 26 files** on 2026-08-03 (120 MD060, 49 MD031,
  32 MD032, 28 MD022, 20 MD040). Clearing that backlog is a dedicated
  `chore(lint)` pass and a hard prerequisite for dropping `!.claude` from the
  ignore globs — the other order turns CI red on the spot.

---

## Step 5 — MEMORY.md update (always)

Regardless of the behaviour gate, **always update MEMORY.md's "Current
State"** at the end of every session. This is project policy (CLAUDE.md
"Session Memory Protocol"):

- Set "Last session" entry to today's date + branch name + one-line summary
- Move the previous "Last session" entry to "Previous session"
- Convert any relative dates ("Thursday") to absolute (`2026-05-01`)
- Update / remove "Open questions / pending decisions" as appropriate

This step runs even when the behaviour gate skipped the user-facing doc
walk, because MEMORY.md tracks **what changed in the session**, not just
behaviour-visible changes.

---

## Step 6 — Update the PR body

Once edits are approved and applied (or the gate decided no edits were
needed), append a "Documentation updates" section to the PR body so
reviewers see the doc reasoning:

```markdown
## Documentation updates

- `CLAUDE.md`: rewrote Project Structure entry for `analytics/store/` after
  data_store.py reduced to a re-export shim
- `README.md`: no change needed (no CLI surface change)
- `MEMORY.md`: Current State updated with strat-2 summary
```

Use the three-step fetch → append → push sequence:

```bash
# 1. Fetch the current body
gh pr view <PR#> --json body --jq .body > /tmp/pr_body.md

# 2. Append the new section (Edit tool, or heredoc)
cat >> /tmp/pr_body.md <<'EOF'

## Documentation updates

- `<file>`: <what changed>
EOF

# 3. Push the new body
gh pr edit <PR#> --body-file /tmp/pr_body.md
```

If the original PR body already has a "Documentation updates" section, open
`/tmp/pr_body.md` in the Edit tool and update it in place — don't append a
duplicate.

---

## Step 7 — Commit and push

Commit doc edits as a single follow-up commit on the PR branch:

```bash
git add <files>
git commit -m "docs: sync docs with PR behavior changes"
git push
```

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
Makefile           — no change needed: no new CLI commands
docker-compose.yml — no change needed: no new processes
.claude/context/*  — updated: analytics.md (store/ paths) | no change needed
.claude/skills/*   — updated: <skill> | no change needed: <reason>
PR summary         — written to /tmp/pr-<branch>.md
PR body            — appended "Documentation updates" section
pre-merge          — clean | <blocker> (see Step 10a)
handoff prompt     — written to docs/plans/next-conversation-prompt.md | declined
PR state re-check  — #<num>: <OPEN | MERGED>, handoff table rewritten to match
```

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
