# Doc walk notes

Supports Phase 3 of `SKILL.md`.

## What the name grep misses

**A stale value hides under a correct key.** The name grep matches the key, so a
doc naming the right key with the wrong value reads as a hit and passes review.
When a PR changes what a key *does*, compare the documented value against the
current one.

**Removals have no new symbol to grep.** A PR that removes, narrows or disables a
behaviour introduces nothing to search for, so the walk comes back clean. Grep
the name of the artifact that was *constrained*, not of the thing that replaced
it.

**The doc can be right and the code wrong.** When a PR changes behaviour a doc
already describes, diff the doc's claim against the **pre-fix** code too. If the
doc already described the corrected behaviour, the bug was a bug rather than a
design choice, and the fix restored a calibration instead of choosing one — which
changes how the PR body should describe it. A doc/code mismatch is not
automatically doc drift.

**A changed verdict needs a wording grep, not a filename grep.** Prose that
*quotes* a verdict lives nowhere near the file that declares it. Pick two or
three distinctive tokens from the retracted sentence — a number, a metric name, a
coined phrase — and grep those; the verdict word itself is too common to
discriminate. A single retracted statistic can survive on several surfaces at once.

## Diff the identifiers

**Rewrote or compressed a doc? Diff the identifiers, not the prose.** Reading a
rewrite for "what looks missing" does not work — it reads complete, because it
was written to.

## No positional parameters in skill code blocks

**Use no `$1` in a skill code block.** A shell positional there can render substituted
rather than literal, so a copied helper silently reads a path nobody passed. Named
variables render literally.

## Discovered-fact questions

Every check so far asks whether the docs drifted from the code. None asks whether
the branch **learned** something durable about behaviour that did not change. A
fix usually costs an investigation, and the investigation is the expensive part
with no artifact — the diff records the repair, the commit message the reasoning,
and neither is a surface a future session reads.

Ask explicitly: *what does this branch now know about existing behaviour that it
did not know when it started?* For each answer:

1. **Durable, or session state?** How the code behaves is durable; "the DB was
   stale on this machine" is not.
2. **Would a future session go looking for it?** If yes → `.claude/context/*.md`.
   If it would silently damage a session that never thought to ask → CLAUDE.md's
   footgun block.
3. **Already pinned by a test?** Then the test is the enforcement and the doc is
   the *pointer* — write the pointer, don't restate the rationale.

## Easy-to-miss discovered facts

Two shapes are easy to miss: **a fact established by ruling something OUT** (a
scan over an empty set, which nothing keyed on the diff can find), and **a
near-miss you did not ship** (record the decision and its cost, or the next
session re-derives the same dead end).

A discovered fact is a claim, so it goes through the claims audit like any other.
Re-derive it from the code before writing it down.

## CLAUDE.md surface

- **CLAUDE.md** — Project Structure entries match real homes; Key Commands and CLI
  resolve; the skills table is current. **CLAUDE.md must not re-absorb context-doc
  content.** Two sources of truth, one of them invisible, always rots the invisible
  one. If a PR adds module detail here, move it.

## Makefile surface

- **Makefile** — every CLI subcommand has a `wifey-<name>` wrapper; script
  wrappers are bare (`backup`, not `wifey-backup`). `tools/*.py` is genuinely
  mixed, so this is a **judgement**: a tool the operator runs as part of a
  documented workflow wants a target; one another tool calls does not. Following
  a `new-targets` hit to what it *executes* is also a cheap directory check — it
  is how `scripts/` was found missing from Project Structure entirely.

## Skills surface

- **`.claude/skills/*/SKILL.md`** — a skill naming a tool, flag, path or constant
  drifts exactly like CLAUDE.md. **Ported skills drift against the fork, not just
  against time**: check every `buibui-*` target, `buibui --help`, and parent-repo
  memory path against this repo's equivalents — one wrong path can send a session's
  Current State update into the parent repo. `.claude/` is covered by `make lint-md`,
  so a skill edit lints like any other file.

  **A changed rule usually has siblings, and finding them depends on whether it has an
  enforcement locus.** A rule enforced in code gives you a string only a
  rule-carrying skill would cite, so `grep -rl "<that symbol>" .claude/skills/*/SKILL.md`
  decides the set. A prompt-side rule has no such locus, and that is the common case,
  not the edge one — every candidate spelling is either prose a sibling would phrase
  differently or a field name it mentions anyway. There the grep can only narrow the
  candidates; read each hit for the rule's *substance*.

## Handoff surface

- **The handoff** is the surface with no other check: gitignored, so no reviewer
  ever sees its drift, and its group A is headed *"Settled — do not
  re-litigate"*, which means a stale line there **instructs** rather than merely
  misinforms. `queue-items` and `handoff-symbols` cover it mechanically; read
  their hits here and fix in phase 6.
