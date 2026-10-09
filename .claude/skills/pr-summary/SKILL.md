---
name: pr-summary
description: >
  Write a PR body to the main checkout's `docs/plans/pr-<branch>.md`, even from a worktree —
  slashes in the branch name flattened to `-` — and return the PR title beside the path, after a branch is complete
  (lint/typecheck/tests green, commit done). The body takes the shape of mattpocock's `pr`
  skill (Summary visual, Evidence, Merge Danger); this skill adds the repo's title, test-plan
  and output rules. Never returns the body inline. Invoke automatically when a branch
  finishes — do not wait. Also triggers on the user saying "/pr-summary", "PR summary",
  "write a PR", or "finish up the branch".
allowed-tools: Bash, Write, Read, Skill
effort: low
---

# PR Summary

The body's **shape** comes from mattpocock's `pr` skill. This skill owns what that one cannot
know: where the file goes, how the title is written, which gates the body may claim, and how
`gh` is called here.

## Steps

1. Call the Skill tool with `mattpocock-skills:pr` for the body shape and its section guidance.
2. Gather the facts: `git branch --show-current`, `git log main..HEAD --oneline`,
   `git diff main..HEAD --stat`, and the Issue(s) the branch closes.
3. Write the title (rules below).
4. Write the body in the `pr` shape, with this repo's additions (below).
5. Screen it: `make post-branch-text FILE=- < "$OUT"` and fix every finding. It gates;
   through `make`, read the banner rather than the exit code. Stdin, because `$OUT` is
   absolute and the recipe strips a Windows path's backslashes.
6. Write it to the output path. Return exactly two lines and nothing else: the file path, then
   the title.

## Output location

`docs/plans/pr-<flattened-branch-name>.md`, where every `/` in the branch name is flattened to
`-`. The branch convention is `feat/`, `fix/`, `docs/`, `chore/`, so an unflattened name points
into a directory that does not exist, and a path the next session cannot predict defeats the
"return only the path" contract. `docs/plans/` is gitignored but in-repo, so it survives a
session delete and a reboot, unlike `/tmp`.

**Always the main checkout's `docs/plans/`, even from a worktree.** A `.claude/worktrees/`
checkout's own `docs/plans/` dies with the worktree, and the session that opens the PR is in
the main checkout. The main checkout is the parent of git's common dir, which the `cd`
resolves whether `git` prints it relative or absolute (`--path-format=absolute` needs git
2.31; this host runs 2.28, which echoes the flag back as output):

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
MAIN=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
OUT="$MAIN/docs/plans/pr-$(printf '%s' "$BRANCH" | tr '/' '-').md"
```

Return `$OUT` as printed: it is absolute, so it resolves from either checkout. From a
worktree the harness refuses a Write-tool edit outside it, so Write the draft to the scratchpad
and `cp` it to `$OUT` with Bash.

## Title

**The title is not in the file.** The file is passed verbatim as `--body-file`, so a title
section there lands in the PR body as a heading. Return it beside the path, ready for `--title`.

- Conventional commit, under 70 characters: `feat` · `fix` · `refactor` · `test` · `docs` ·
  `build` · `chore`, with a scope.
- **Name the mechanism you changed, not the symptom.** Squash-merge makes the title the
  permanent commit message.
- **No derived number** (line counts, file counts, sizes). A later commit moves it, and
  correcting a title means rewriting `main`; the body is one edit away from correct.

## Body additions to the `pr` shape

- **Summary** opens with one sentence of why, then `Closes #<n>` for each Issue the branch
  finishes, so the merge closes it.
- **Evidence** carries the gate checklist. **Tick only a command that has already returned:**
  a gate still running gets `[ ]` plus a note, and is ticked when it finishes. **Name the gate
  you ran:** `make preflight` replaces `make test` at the final gate, so ticking `make test`
  after running the preflight claims a command you did not run. Say whether
  `make test-regression` applied (the diff touches CI's regression filter in `CLAUDE.md`) or
  was skipped, and why.

  ```markdown
  - [x] `make preflight` — <N> passed on a clean clone
  - [x] `make lint-py` · `make typecheck` · `make lint-md`
  - [ ] Manual: <item>
  ```

- **Merge Danger** names the live surfaces at risk when they apply: the scheduled `go-live`
  runs `make go-live` from this checkout, so a config TOML or schema change is live at its next
  run once `main` is pulled here.
- One line per paragraph or bullet, no hard wraps: GitHub renders a single newline as a break.
- End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- `/post-branch` later appends a `## Documentation updates` section; leave room for it.

## Calling `gh` here

The active `gh` account is the work one and the `gh` default repo is the parent, so prefix the
token inline and always pass the repo (never `export …;`, never `gh auth switch`, never
`gh repo set-default`):

```bash
GH_TOKEN=$(gh auth token --user s10023) gh pr create --repo s10023/buibui-wifey-wall-street-bot --title "<title>" --body-file "$OUT"
```

If `gh pr create` fails with `must be a collaborator` straight after a visibility flip, retry
once before touching auth: GitHub re-evaluates permissions asynchronously after a flip.
