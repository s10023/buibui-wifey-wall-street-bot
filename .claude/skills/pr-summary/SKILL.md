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

Write a PR title, summary and test plan once a branch is complete (lint/typecheck/tests
pass, commit done) — do not wait to be asked. Write it to `docs/plans/pr-<branch>.md`
(slashes flattened to `-`); never return the content inline.

## Output location

**Not `/tmp`:** the user deletes conversations and reboots clear `/tmp`, so a summary
parked there would evaporate exactly when a fresh session wants it. `docs/plans/` is
gitignored but inside the repo, so it survives both.

**Flatten every `/` in the branch name to `-` first.** This repo's branch convention is
`docs/`, `feat/`, `fix/`, `chore/`, so a raw `docs/plans/pr-<branch>.md` is
`docs/plans/pr-fix/outcome-resolution-closed-bars.md` — a path under a directory that
does not exist. Derive it exactly this way, so every session picks the same name:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
OUT="docs/plans/pr-$(printf '%s' "$BRANCH" | tr '/' '-').md"
```

`fix/outcome-resolution-closed-bars` ⇒ `docs/plans/pr-fix-outcome-resolution-closed-bars.md`.

## Template

Write PR and Issue bodies without hard line breaks inside a paragraph or bullet: one line
per paragraph or bullet. GitHub renders a single newline in a PR or Issue body as a line
break, so hard-wrapped text renders ragged.

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

`gh` works for this project: this repo is the user's own fork, so there is no
collaborator permission to lack. Write the file at the flattened
`docs/plans/pr-<branch>.md` regardless (it is this skill's deliverable, and doubles as a
`--body-file`).

- **Always pass `--repo s10023/buibui-wifey-wall-street-bot`.** The user's `gh`
  default repo points at the crypto parent, so a bare `gh pr create` targets the wrong
  repo. This is a preference, not a bug — never "fix" it with `gh repo set-default`.
- **Never run `gh auth switch`.** The active account stays on the work account
  permanently; reach s10023 by prefixing the token instead:
  `GH_TOKEN=$(gh auth token --user s10023) gh <cmd> --repo s10023/buibui-wifey-wall-street-bot`.
  If `gh` fails with "Could not resolve to a Repository", that is the account — add the
  `GH_TOKEN` prefix rather than switching.

## Conventional commit types for PR titles

- `feat(scope):` — new feature or behavior
- `fix(scope):` — bug fix
- `refactor(scope):` — code restructure, no behavior change
- `test(scope):` — new or updated tests only
- `docs(scope):` — documentation only
- `build(scope):` — build system / dependencies
- `chore(scope):` — maintenance (cleanup, config)

## Task: write a PR summary

Gather `git branch --show-current`, `git log main..HEAD --oneline` and
`git diff main..HEAD --stat`, then fill in the template above: title, background,
3–5 summary bullets, an implementation walkthrough, params/config if any were added, and
the test plan with CI items pre-ticked. Write the result to
`docs/plans/pr-<branch-name>.md` (slashes flattened to `-`) and return only the file path.
