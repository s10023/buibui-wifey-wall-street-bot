# `.claude/hooks/` — PreToolUse guards and advisories

Four `PreToolUse` hooks fire on `Bash`. Three are files in this directory; the fourth is inline
jq in `.claude/settings.json`, which is what registers all four, so the file count here is not
the hook count.

## Wrapper interpreter order

Each wrapper in `settings.json` tries interpreters in this order: `.venv/Scripts/python.exe`,
`.venv/bin/python`, `python3`, `python`. Keep that order: on this host `python3` resolves to the
Windows Store App Execution Alias, a reparse point that returns exit 126 (permission denied)
rather than running the script, and only exit 2 blocks a `PreToolUse` hook — so a wrapper that
ran bare `python3` would fail open on every command it was meant to gate. The stub cannot be
detected by a test operator either: `[ -f ]` and `[ -x ]` both report true against it, and `wc -c`
on it is denied. That is why the fix is a fallback order rather than a stub check.
`tests/test_hook_wiring.py` reads the wrapper string out of `settings.json` and asserts the guard
blocks end to end with exit 2, that a benign command still passes, and that a missing hook file
still fails open.

Each wrapper exits 0 when its script is missing, because `python3`'s own exit code for "cannot
open script" is 2, the same code that means "block" — a missing hook file must not block every
Bash call in the session. Keep this guard even though the hook files are tracked: a worktree or a
partial checkout can still lack one.

## guard-destructive.py — blocks, exit 2

Refuses the catastrophic-Bash set: recursive force deletes, hard resets, force-pushes, DB wipes.
If it blocks you, surface it rather than working around it.

It matches the command payload, not intent, so a heredoc or a JSON probe that merely contains a
hazard string is blocked even though nothing destructive would run. Write commit bodies and
scripts to a file and pass `-F` or a path instead — a file is invisible to the matcher. This is
the general form of the commit-message rule CLAUDE.md documents: it applies to any heredoc, not
only a commit message.

## guard-shell-hygiene.py — advises, never blocks

Shell habits that silently produce a wrong result. It always exits 0 and speaks through
`hookSpecificOutput.additionalContext`, once per rule per session, because every pattern it
matches has honest uses. Three rules:

- `waiter`: an `until`/`while` loop around `pgrep`/`pidof`. A background job already re-invokes
  the session when it exits, and `pgrep -f 'pytest tests/'` matches the polling shell's own argv.
- `piped-gate`: a gate from `_GATE` piped into `tail` or `head`. The pipeline exits with `tail`'s
  status, so a red run reads as green. Redirect, then read the file:
  `make <gate> > <log> 2>&1; echo "exit=$?"; tail -8 <log>`.
- `gh-auth-switch`: `gh auth switch` mutates gh's global active account for every session on the
  machine; scope the account to one command with `GH_TOKEN=$(gh auth token --user s10023)`.

Ported from the parent, with `_GATE` re-derived against this repo's Makefile and `tools/`;
`tests/test_guard_shell_hygiene.py` pins it. The parent's duplicate-waiter and
edit-during-live-suite rules are not ported, because both need `pgrep`, which this host lacks,
and its `/card` rule has no subject here. Porting a rule that can never fire ships a dead check.

## advise-foreground-run.py — advises, never blocks

Nudges `make test`, `make test-regression` and CI waits toward `run_in_background: true`. It keys
on the `run_in_background` tool parameter rather than the command string, because a foreground
and a background run are byte-identical as commands and no string match could separate them. It
is head-anchored so it does not fire on its own documentation. Run `--selftest` after any edit;
it pins both discriminators.

## The inline `gh pr create` advisory (in settings.json)

Greps the command for `gh pr create` and emits a `/post-branch` reminder. This has to be
`PreToolUse`: a `PostToolUse` hook fires after the PR already exists, so it could not enforce
"sweep while the branch is still local-only" at all.

## Changing a hook

Put a new hook's tests in `tests/`, not beside the hook. `pyproject.toml` sets
`testpaths = ["tests"]`, so a test file placed in this directory — where the parent keeps its
copies — is collected by nothing: it would pass review, never run, and read exactly like
coverage. A `--selftest` script has the same problem one step removed, since it only runs when a
session remembers to invoke it, and a self-check outside CI is not a check.

Test the wiring, not just the module. `tests/test_hook_wiring.py` reads the wrapper string out of
`settings.json` and drives it through `sh`, because a test that only imports the hook module
passes whether or not the wrapper string in `settings.json` is correct — the wrapper string is a
surface of its own, separate from the module it invokes.

`tests/test_hook_wiring.py` and `tests/test_guard_shell_hygiene.py` live in `tests/`, so
`make test` and CI's `lint-typecheck-test` job both run them; `make lint-py` and `make typecheck`
cover the hook sources like any other tracked `.py` module.

An advisory hook that stops firing is silent by construction, the same shape as the off-site
backup's failure-only alerting. After editing one, trigger it deliberately once and confirm the
banner appears.
