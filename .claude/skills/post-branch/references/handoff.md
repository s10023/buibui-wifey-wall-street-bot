# Handoff, PR state and safety notes

Supports Phase 6 (handoff, re-verify) and Safety rails of `SKILL.md`.

## Push rules and rebase

**Push rules**: default `git push`, no force. `--force-with-lease` only with
explicit approval — and `guard-destructive` blocks it even then, deliberately.
Approval makes a push *intended*, not *safe*; ask the operator to run it. Never
push to `main` from this skill.

**Rebase** only if a relevant doc lives on `main` but not the branch, and only on
explicit OK.

## Standing handoff versus /handoff

This is the STANDING handoff, and mattpocock's `/handoff` does not replace it: that skill writes a
one-off portable doc to `%TEMP%` for forking a side task, and the operator invokes it.

## Targeted edits only

**Update with targeted `Edit`s — never `Write` the whole file.** Its standing back
half — the four groups and the NOT-queued list — is exactly what a
template does not reproduce, so an overwrite destroys it silently and the loss is
invisible until a session re-litigates something already ruled out.

## Four labelled groups

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

## Prune every run

**The gate is `handoff-size` in phase 1, and it has an external referent.** It fails
the file above `HANDOFF_MAX_LINES` (240) — a real threshold, checked against `wc -l`,
not against a number the file carries about itself. If you want to know the size, run
`wc -l` or `make status`; don't have the file track its own line count, since a
self-describing stamp needs its own maintenance cycle and reads clean when merely
absent rather than when accurate. It also warns inside the last `HANDOFF_WARN_MARGIN`
(20) lines below the cap. Treat that warning as the cue to re-home a cluster now. Do not
shuffle text until the file fits: that is not pruning, and the cap gets breached again
on the next append.

## Re-homing runs first

**Re-homing is a separate pass and it runs first**: move the rule to its durable home,
`grep` the destination to confirm it landed, and only then cut the narrative. A prune
that drops a guard is a regression disguised as hygiene. A re-home into a tracked file
should already be in phase 5's commit (see there). If you find one only now, it is a
new commit, so say so rather than folding it silently into the PR.

## What to delete each run

Delete every run: merged PRs beyond the most recent one or two · completed tasks and
closed findings · "what #N found" narratives once the lesson is in group C · shipped
skill-fix items (**outright — no `DONE in #146` tombstones**) · answered open
questions.

The test for every line: **if the next session never reads this, does it do something
wrong?** If no, cut it.

**Read a closed section before deleting it — live rules hide inside blocks headed
"DONE" or "CLOSED".** Prune by moving to the durable home, never by deleting outright;
if a rule has no committed home yet, that is a signal to write one, not to keep the
block.

## Operator actions must resolve in this repo

Name the exact command, target or unit, **and verify it exists here before writing
it**. The `buibui-signal-watch` units belong to the crypto parent, not wifey; wifey
dispatch is the one-shot `make go-live`, by hand or via the opt-in
`wifey-signal-watch.timer` that runs it. **A unit existing on the machine is not
evidence it belongs to this repo**: read `WorkingDirectory`, check
`grep -n '<target>:' Makefile`, check `wifey <cmd> --help`. `wifey-signal-watch.*` and
`buibui-signal-watch.*` differ by prefix alone, so a name is not even a weak signal,
and neither is "it fired recently".

## Why the PR state goes stale

This skill writes the handoff *before* the merge, so its most prominent instruction is
the first thing to go stale, and the handoff is the artifact that survives a session
delete.

## Re-query every PR

Re-query **every** PR named in the handoff and rewrite the table to match. If one
merged, update the "first move" line too — the next session should start on a task,
not merge something already merged. If the local branch still exists, say so; deleting
it is standing habit here.

## Fill the PR number

**Fill MEMORY.md's `#NNN` here too**, from this same query — phase 4 wrote that bullet
before the PR existed. MEMORY.md lives outside the repo, so this costs no commit and no
CI.

## No PR opened

**No PR opened → strike the placeholder; there is no query to fill it from.** Phase 4
omits the number because phase 5 is *assumed* to create the PR. Where the branch
stayed local, **remove** the placeholder and write the SHA plus "local-only, still
amendable" — an absent number is a state to report, and a slot shaped like a PR
reference left unfilled reads as a lost lookup, inviting the nearest plausible number
to be mistaken for a fact.

## Confirm every edit

- **Confirm every edit, except the two surfaces marked `unprompted: true`.**
  This skill proposes; the user approves. The exceptions are **`MEMORY.md`** and
  the **handoff** (`docs/plans/next-conversation-prompt.md`), which the standing
  account-level protocol says to write unprompted: the account-level CLAUDE.md's
  "Session hygiene" section (`~/.claude` here, `~/.claude-personal` on the old Linux
  box) — read the rule there, never restate it here, so this rail and that protocol
  cannot drift apart.
  **"Untracked" is the wrong scope for the carve-out.**
  `git ls-files docs/plans/ | wc -l` returns 0, and that same tree holds
  `pundit-calls.jsonl` and the thesis inbox — single-copy research that most needs a
  human. So: confirm git-tracked surfaces **and** untracked single-copy data alike,
  and let the exception be **two named files**, never a directory class.

## PR summary path

Write it through `/pr-summary`; do not compose it from scratch. It takes the body shape from
mattpocock's `pr` skill (Summary visual, Evidence, Merge Danger) and adds this repo's title
rules, the honest-tick gate checklist under Evidence, `Closes #n`, and the Claude Code footer. **The path flattens `/` to `-`**: every branch here is `docs/…`,
`feat/…`, `fix/…` or `chore/…`, so a literal `docs/plans/pr-<branch>.md` names a
directory that does not exist and the write fails.
