# `.claude/hooks/` — guards, advisories and the session digest

`.claude/settings.json` registers every hook, on four events: `SessionStart` (the session digest),
`UserPromptSubmit` and `PostToolUse` on `Bash` (lifecycle advisories), and `PreToolUse` on `Bash`
(two guards, three advisories), on `Edit|Write|NotebookEdit|MultiEdit` (the branch guard) and on
`Skill` (the usage log, which also runs on `UserPromptSubmit`). Every hook is a Python file run
through the same wrapper; none is inline shell.

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
- `piped-gate`: a gate from `_GATE` whose exit status is swallowed, either by piping the gate's
  own segment into `tail` or `head`, or by ending the command on a segment that always succeeds
  (an `_ALWAYS_OK` command such as `echo`, `tail` or `cat`, or a pipeline into `tail`/`head`).
  Either way the shell returns that command's 0, so a red run reads as green. A pipe after
  `rc=$?` in a command that ends `exit $rc` is silent, unlike upstream's segment-spanning form. Capture the status and exit with it last:
  `make <gate> > <log> 2>&1; rc=$?; tail -8 <log>; exit $rc`. The advice until parent #779
  ended in `tail` and so was itself a swallow.
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

## advise-lifecycle.py — advises, never blocks

One file, three events, keyed on `hook_event_name`:

- `PreToolUse` on `gh pr create`: the `/post-branch` reminder. It has to be `PreToolUse`: a
  `PostToolUse` hook fires after the PR already exists, so it could not enforce "sweep while the
  branch is still local-only".
- `PostToolUse` on `gh pr merge`: wait for `make wait-ci-main`, flip back private, close resolved
  Issues, prune the branch, append the handoff.
- `UserPromptSubmit` on "deleting sesh": the close-out checklist (to-dos become Issues, handoff,
  MEMORY.md).

It replaced an inline `jq -r … | grep -q …` one-liner. This host has no `jq`, so that pipe failed,
`|| true` swallowed it, and the reminder never fired here; `TestNoHookDependsOnJq` pins the
absence. Matching is head-anchored (start of the first line, after a shell separator, or after a
`VAR=value` prefix such as `GH_TOKEN=$(…)`), and quoted strings are blanked first, so a
`grep -E "a|gh pr create"` does not read the regex `|` as a pipe. A `\`-continued chain counts as
one logical line, so `/post-branch`'s phase-5 recipe, which puts `gh pr create` on a continuation
line, still matches. The `gh pr create` reminder stays silent when that segment's `--body-file` /
`-F` file carries a `## Documentation updates` heading, the evidence the skill already ran (#381).
An inline `--body`, stdin, an unreadable file or a chained `git commit -F` still fire.
`tests/test_advise_lifecycle.py` pins both directions.

## guard-branch.py — advises, never blocks

`PreToolUse` on `Edit|Write|NotebookEdit|MultiEdit`: the first edit of a tracked file while on
`main` gets a "branch off latest main" reminder, once per session per branch, never for gitignored
paths. Ported from the parent with only its rationale pointer changed.

## log-skill-usage.py — logs, never blocks

`PreToolUse` on `Skill` and `UserPromptSubmit` (#395): appends one tab-separated line per skill
invocation (UTC timestamp, source `tool` or `prompt`, name, args cut to 80 characters) to the
gitignored `.claude/skill-usage.log`. Both events, because a typed `/name` loads the skill without
a `Skill` tool call; a log fed by `PreToolUse` alone would score the skills the operator types most
as unused. The prompt side logs every leading `/name`, built-ins included, and the summary judges
only `.claude/skills/` against the log.

It cannot block. Every path in the script returns 0 and swallows its own failure, and its wrapper
runs the interpreter without `exec` and then ends `exit 0`, so the wrapper's exit code is final
whatever Python does. `make status` prints a one-line count (invocations since the first entry,
repo skills with none); `poetry run python .claude/hooks/log-skill-usage.py --summary` prints the
per-name table and the never-invoked list. Read it as evidence before demoting a skill, not as a
verdict: the log is per machine, starts empty on a fresh clone, and a cloud session's copy dies
with its container. It is not in the session digest, which reports reds rather than repo shape.
`tests/test_log_skill_usage.py` pins the parsing and summary; `TestSkillUsageLogWiring` in
`tests/test_hook_wiring.py` drives both wrappers end to end.

## The SessionStart digest (`tools/session_digest.py`)

Not a file here, but registered here: `SessionStart` (`startup|resume|clear`) runs
`tools/session_digest.py`, whose stdout reaches the model, not the operator's screen, so the
digest tells the model to lead its first reply with any RED line. Its wrapper exits 0 on every
path; a digest must never block a session. Narrative: `.claude/context/tools.md`.

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

`tests/test_hook_wiring.py`, `tests/test_guard_shell_hygiene.py`, `tests/test_advise_lifecycle.py` and
`tests/test_log_skill_usage.py` live in `tests/`, so
`make test` and CI's `lint-typecheck-test` job both run them; `make lint-py` and `make typecheck`
cover the hook sources like any other tracked `.py` module.

An advisory hook that stops firing is silent by construction, the same shape as the off-site
backup's failure-only alerting. After editing one, trigger it deliberately once and confirm the
banner appears.
