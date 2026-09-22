# `.claude/hooks/` — PreToolUse guards and advisories

**Four** `PreToolUse` hooks fire on `Bash`. **Three are files here; the fourth is inline jq in
`.claude/settings.json`, which is what registers all four** — so the count of files in this
directory is not the count of hooks.

⚠ **Every one of them was DEAD on the Windows host until 2026-09-21, and nothing said so.**
Each wrapper ran `exec python3 "$f"`; here `python3` resolves to the Windows Store App
Execution Alias, a reparse point returning **Permission denied / exit 126**. Only exit 2
blocks, so `guard-destructive.py` failed open on **every** command — `rm -rf`, `git reset --hard`,
force-push, DB wipes, none of them guarded. ⚠ **The stub cannot be detected by a test
operator**: `[ -f ]` and `[ -x ]` both report true and `wc -c` on it is denied. So the wrappers
now ORDER their candidates — `.venv/Scripts/python.exe`, `.venv/bin/python`, `python3`,
`python` — preferring the interpreter everything else in this repo already runs on.
`tests/test_hook_wiring.py` is the gate: it reads the wrapper string out of `settings.json`
and asserts the guard blocks end to end with exit 2, plus a benign command still passing, plus
a missing file still failing OPEN.

`guard-shell-hygiene.py` is the newest, ported from parent #743/#744/#753 at parent HEAD.
**Three of its six upstream rules are deliberately absent**: the `/card` rule has no subject
here, and the duplicate-waiter and edit-during-suite rules both need `pgrep`, which this host
does not have. Porting those would have shipped rules that can never fire — the same dead-check
class as PRs #301 and #302. Its gate list is RE-DERIVED against this repo's Makefile, pinned by
`tests/test_guard_shell_hygiene.py`.

⚠ **The rule you will actually meet: it fires on a GATE PIPED INTO `tail` or `head`.** The
pipeline exits with `tail`'s status, so the gate's own failure is masked and a red run reads as
green — the same class CLAUDE.md documents for `make preflight` and `wait_ci.py`, where `make`
collapses every failure to its own exit 2 and you must read the banner. **Redirect and then read
the file** rather than piping:

```bash
make <gate> > <log> 2>&1; echo "exit=$?"; tail -8 <log>
```

⚠ **`guard-destructive.py` matches the whole command PAYLOAD**, so a heredoc or a JSON probe
merely *containing* a hazard string is blocked even though nothing destructive would run — hit
twice on 2026-09-21. Write commit bodies and scripts to a FILE and pass `-F` or a path; a file is
invisible to the matcher. CLAUDE.md carries the commit-message half of this; the generalisation
is that the guard reads text, not intent.

⚠ **Tracked since the 2026-08-20 denylist inversion.** Before that `.gitignore` allowlisted over
`.claude/*`, every artifact class defaulted to ignored, and these died silently on clone —
CLAUDE.md carried "re-add it after a reclone" for all three. They now survive a reclone, and
because they are tracked `.py`, **ruff and mypy strict cover them like any other module**.

## `guard-destructive.py` — blocks, exit 2

Refuses the catastrophic-Bash set (recursive force deletes, hard resets, force-pushes, DB
wipes). If it blocks you, **surface it rather than working around it silently**.

⚠ **The wrapper in `settings.json` must fail OPEN on a missing file.** `python3` exits **2**
when it cannot open a script, and 2 is the *block* code — so a missing hook would block every
Bash call in the session rather than none. Hence
`f="…/guard-destructive.py"; if [ -f "$f" ]; then exec python3 "$f"; fi`. Now that the file is
tracked this is belt-and-braces, but keep it: a worktree or a partial checkout can still lack it.

⚠ **It matches the COMMAND PAYLOAD, so quoting a hazard trips it — including in documentation.**
CLAUDE.md records this for commit and PR bodies ("write to a file and pass `-F`/`--body-file`; a
heredoc is the command payload, a file is invisible to it"). **The rule is more general than the
sentence that carries it**: it applies to *any* heredoc, not just a commit message. Observed
2026-08-20 — a `python3 - <<'PY'` heredoc whose payload described what this hook blocks was
itself blocked, correctly. The fix is identical: write the script to a file and run the file.

## `advise-foreground-run.py` — advises, never blocks

Nudges `make test` / `make test-regression` / CI waits toward `run_in_background: true`.

**It keys on the `run_in_background` tool PARAMETER, not the command string** — a foreground and
a background run are byte-identical as commands, so no string match could separate them. It is
**head-anchored** so it does not fire on its own documentation. `--selftest` pins both
discriminators; run it after any edit.

## The inline `gh pr create` advisory (in `settings.json`)

Greps the command for `gh pr create` and emits a `/post-branch` reminder. It **has to be
`PreToolUse`**: a `PostToolUse` hook cannot fire before the PR exists, so it could not enforce
"sweep while the branch is still local-only" at all.

## Changing a hook

⚠ **This paragraph used to say there was no test target and no CI step. That stopped being
true on 2026-09-21.** `tests/test_hook_wiring.py` and `tests/test_guard_shell_hygiene.py` live
in `tests/`, so `make test` and CI's `lint-typecheck-test` job both run them; `make lint-py`
and `make typecheck` cover the hook sources as before. `advise-foreground-run.py --selftest`
still exists and is still the odd one out.

**Put a new hook's tests in `tests/`, not beside the hook.** `pyproject.toml` sets
`testpaths = ["tests"]`, so a test file in this directory — where the parent keeps its copies
— is collected by NOTHING. It would pass review, run never, and read exactly like coverage.
A `--selftest` has the same problem one step removed: it only runs when a session remembers,
and CLAUDE.md already names *a self-check outside CI is not a check*.

**Test the WIRING, not just the module.** The 2026-09-21 defect was entirely in the wrapper
string in `settings.json`; every hook module was fine. A test that imports the module passes
either way, so `test_hook_wiring.py` reads the wrapper out of `settings.json` and drives it
through `sh`.

**An advisory hook that stops firing is silent by construction** — the same shape as the
off-site backup's failure-only alerting — so after editing one, trigger it deliberately once
and confirm the banner appears.
