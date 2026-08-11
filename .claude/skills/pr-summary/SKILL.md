---
name: pr-summary
description: >
  Write a PR title, summary, and test plan to `docs/plans/pr-<branch>.md` — slashes
  in the branch name flattened to `-` — after a branch is complete
  (lint/typecheck/tests green, commit done). Never returns the content inline.
  Invoke automatically when a branch finishes — do not wait. Also triggers on
  the user saying "/pr-summary", "PR summary", "write a PR", or "finish up
  the branch".
allowed-tools: Bash, Write, Read
---

# PR Summary

Write a PR title + summary + test plan after finishing a branch. Always write to
`docs/plans/pr-<branch>.md` with slashes flattened to `-` (see Output location) —
never return as inline text.

## When to use

After every branch is complete: lint/typecheck/tests pass, commit done. Do not wait to be asked.

## Output location

Always write to `docs/plans/pr-<flattened-branch-name>.md` (gitignored via `docs/plans/`,
but inside the repo and therefore durable). **Not `/tmp`:** the user deletes conversations
and reboots clear `/tmp`, so a summary parked there evaporates exactly when a fresh session
would want it. This mirrors the handoff, which moved to `docs/plans/` for the same reason.
Return only the file path, not the content inline.

**Flatten every `/` in the branch name to `-` first.** This repo's branch convention is
`docs/`, `feat/`, `fix/`, `chore/`, so a raw `docs/plans/pr-<branch>.md` is
`docs/plans/pr-fix/outcome-resolution-closed-bars.md` — a path under a directory that
does not exist. The write then fails, or a session silently invents its own flattening
and the next session cannot find the file. Since this skill's whole contract is "return
only the file path", a path nobody can predict defeats it.

Derive it exactly this way, so every session picks the same name:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
OUT="docs/plans/pr-$(printf '%s' "$BRANCH" | tr '/' '-').md"
```

`fix/outcome-resolution-closed-bars` ⇒ `docs/plans/pr-fix-outcome-resolution-closed-bars.md`.

## Template

```md
## PR Title

`<type>(scope): short imperative description under 70 chars`

## Background

<1-2 sentences: what problem or gap this addresses, why it matters now, and any relevant context (e.g. strategy source, prior limitation, user-facing impact)>

Reviewers should understand the motivation before the mechanics.

## Summary

- <bullet 1>
- <bullet 2>
- <bullet 3>

## How it works

<1-3 paragraphs or bullets explaining the implementation — keep it readable for someone who hasn't seen the code>

## Params / Config

<table or bullets of new params, defaults, where configured — omit if none>

## Test plan

Items already verified by CI at commit time are pre-ticked. Manual items remain unchecked.

**Only tick a command that has already returned.** "Verified at commit time" means the
result is in hand — not that the command is running and expected to pass. A gate still
in flight gets `[ ]` plus a note, and is ticked once it finishes. A PR body is durable
and gets read as a claim about what was checked, so a hopeful tick is a false statement
even when the run later goes green.

- [x] `make test` — <N> passed
- [x] `make lint-py` — ruff clean
- [x] `make typecheck` — mypy clean
- [x] `make lint-md` — markdownlint clean (only if MD files changed)
- [ ] Manual: <item 1>
- [ ] Manual: <item 2>

## Stats

- Tests: <N> total (<+N> new)
- Files changed: <list>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Note on GitHub CLI

`gh` **works** for this project — verified 2026-08-03: `gh api user` resolves as
`s10023`, and `gh pr list --repo s10023/buibui-wifey-wall-street-bot` returns the
merged PRs 120 through 124. This repo is the user's own fork, so there is no
collaborator permission to lack. This section used to claim `gh pr create` fails with a
collaborator permission error; that was a port artifact from the crypto parent,
stale in both repos, and it cost PRs a manual paste for no reason. Still write the file
at the flattened `docs/plans/pr-<branch>.md` (it is the deliverable of this skill, and
useful as a `--body-file`), but do not tell the user the CLI is unavailable.

Two repo-specific rules apply to every `gh` invocation here:

- **Always pass `--repo s10023/buibui-wifey-wall-street-bot`.** The user's `gh`
  default repo points at the crypto parent on purpose, so a bare `gh pr create`
  targets the wrong repo. This is a preference, not a bug — never "fix" it with
  `gh repo set-default`.
- If `gh` fails with "Could not resolve to a Repository", that is the account,
  not the permission: run `gh auth switch --user s10023`. Don't debug `gh`
  config past that, and leave the active account on `s10023` afterwards.

## Conventional commit types for PR titles

- `feat(scope):` — new feature or behavior
- `fix(scope):` — bug fix
- `refactor(scope):` — code restructure, no behavior change
- `test(scope):` — new or updated tests only
- `docs(scope):` — documentation only
- `build(scope):` — build system / dependencies
- `chore(scope):` — maintenance (cleanup, config)

## Task: write a PR summary

When the user asks to write a PR summary or after finishing a branch:

1. Get the current branch name: `git branch --show-current`
2. Get commit list: `git log main..HEAD --oneline`
3. Get files changed: `git diff main..HEAD --stat`
4. Draft the PR title (under 70 chars, conventional commit format)
5. Write background context — why this change exists, not just what it does
6. Write summary bullets — 3–5 key changes
7. Write "How it works" — implementation details for reviewers
8. Fill in Params/Config section if any new TOML keys or CLI flags were added
9. Fill in test plan — check CI items, list remaining manual verification steps
10. Write to `docs/plans/pr-<branch-name>.md`, slashes flattened to `-`
11. Return only the file path
