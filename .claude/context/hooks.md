# `.claude/hooks/` — PreToolUse guards and advisories

Three `PreToolUse` hooks fire on `Bash`. **Two are files here; the third is inline jq in
`.claude/settings.json`, which is what registers all three** — so the count of files in this
directory is not the count of hooks.

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

There is no test target for these beyond `advise-foreground-run.py --selftest`, and no CI step
reads this directory. `make lint-py` and `make typecheck` do cover them now. **An advisory hook
that stops firing is silent by construction** — the same shape as the off-site backup's
failure-only alerting — so after editing one, trigger it deliberately once and confirm the
banner appears.
