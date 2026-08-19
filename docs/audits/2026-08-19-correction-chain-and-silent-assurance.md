# The correction chain, and assurance that cannot see its own class

**Date:** 2026-08-19
**Verdict:** **BOUNDED.** Across one cross-repo review, at least eight filed claims moved under
checking, and **four of them were filed while correcting or building on their predecessor** — under
sustained attention, with the pattern explicitly named and under active discussion between two
sessions. It did not converge. **"Be more careful" is therefore excluded as the remedy**: the
elevated-attention condition is exactly where it failed. What is bounded is the class, not the
rate — every instance had a correct frame and a wrong checkable detail, and the mechanisms are
enumerable and mechanically addressable. The rate itself is not estimated here and this
enumeration is **not** exhaustive.
**Audit:** this file

## Why this exists

A cross-repo workflow review compared this repo against its crypto parent, then fixed seven defects
in the mechanical sweeps (#229). The defects are recorded in that PR and in the tool docstrings.
This audit records the *other* result, which has no home in a diff: **how often the reviewing
sessions themselves filed something false, and in what shape.**

It is filed as a bounded finding rather than a complaint because the shape is stable, and because
the obvious remedy is the one the evidence rules out.

## The chain

Eight instances tracked contemporaneously. **W** = this repo's session, **B** = the parent's.

| # | By | Claim as filed | What was true | Filed while correcting? |
| --- | --- | --- | --- | --- |
| 1 | W | "a hook fires at 19.7 KB" (MEMORY.md cap) | No such hook existed. Filed after reading **2 of 5** settings files | — |
| 2 | W | `make status` reported MEMORY.md's size | It used `du -k` — disk blocks — overstating by up to 4 KB | — |
| 3 | B | "8 dedicated `/post-branch` PRs, #214–#228" | The window was too narrow; the arc starts at #206 | — |
| 4 | W | "46 commits touched those files" | **40.** `git log --all` counts a squash-merged branch's pre-squash commits | **yes — correcting 3** |
| 5 | W | "the operator ruled on W7 and applied it themselves" | **B applied it**, on its operator's diff-approval. One approved decision, not convergence | — |
| 6 | B | "`_is_quoted` has two reachable false negatives" | One. `begin == 0` cannot occur: `citations()` calls `_anchor_after` with `start = t.end()` of a target that must precede the anchor | **yes — correcting W's finding** |
| 7 | B | "I recognised the short-SHA trap from your filed scar" | The scar was in neither the ported code nor B's tree. The cause was inferred, and a provenance was attributed afterwards | — |
| 8 | W | "the prose travelled and prevented the mistake" | Unfounded. W verified where the text sat in **its own** repo and treated that as evidence about B's | **yes — building on 7** |

The count did not stop at eight. Later in the same session: a probe reported "does not reproduce"
for a defect that does reproduce; a mutation run reported a result it had not produced; and a PR
body carried a superseded class count, a stale line count, and a heading that miscounted its own
list. Each was caught, and **the fact that the enumeration keeps extending is the finding, not a
footnote to it.**

## The mechanisms, in ascending order of how hard they are to reach

**1. Reading a source that cannot answer the question.** Instance 1 read two of five settings files
and reported on all five. Instance 5 read a file-changed notification — which names no actor — and
reported who acted. In both, silence in the consulted source was read as confirmation.

**2. A metric that answers a near-neighbour of the question.** Instance 2 measured disk blocks and
reported bytes. Instance 4 counted commits reachable from every ref and reported commits on `main`.
Neither is a careless reading; both are the wrong instrument returning a confident number.

**3. Confabulated provenance.** Instance 7 inferred a cause correctly from first principles, then
attributed the inference to a plausible source without checking it. **A remembered provenance never
presents as a claim — it presents as recall**, so it does not trigger the checking a claim would.

**4. Declining to check a source you have, because the claim flatters your own prior work.**
Instance 8 is the worst of the set. The falsifier was one `grep` against a readable local path, and
it was not run until the other session volunteered the correction. The claim arrived wrapped in a
compliment to this repo's practice, and was upgraded rather than audited. **This mechanism is
motivational, not informational** — mechanical re-derivation cannot reach a question you never open.

## The parallel class: assurance that cannot see the thing it is trusted for

The same shape appears in tooling, where a check reports truthfully and has nothing to say — and
"nothing to say" renders identically to "nothing to find". Six surfaces observed:

| Surface | Reports | Cannot see |
| --- | --- | --- |
| A partial file scan | "no hook found" | The three files it did not open |
| A change notification | that a file changed | Who changed it |
| `_is_quoted` (pre-#229) | no citation | The one it silently suppressed |
| An exact-match API filter | an empty result | That the key was malformed |
| `handoff-size` (pre-#229) | clean | Anything at all, once the stamp it keyed on was absent |
| A leg that degrades to `SKIPPED` | a green sweep | That its subject no longer exists |

⚠ **Prior art was this repo's, and it was scoped too narrowly.**
`.claude/skills/post-branch/SKILL.md` already carried, from #214 on 2026-08-14: *"an exact-match
query that returns EMPTY rather than ERRORING on a malformed key is indistinguishable from a true
negative."* That was filed as a property of one API. It is not an API property. **The shape was
known; the scope was wrong** — which is itself an instance of the class this audit describes.

The most consequential member is not in the table, because it is not a bug. A secrets scanner
reported this repo's history clean, and that result had been standing in for a decision the scanner
cannot make: whether an identifier in the history is one the operator is willing to publish. **A
clean scan answered "are there keys here"; it was read as "is this safe to expose."** Details and
the standing pre-flip guard are in the handoff, deliberately not restated here.

## What follows

**Excluded as the remedy: more care.** Four of eight were filed *during* correction, by two
independent sessions, with the pattern named. If attention were the lever it would have worked by
instance 4.

**Not excluded, and cheap:**

- **Re-derive mechanically rather than recall.** Every instance had a one-command falsifier that
  was not run: `git log` without `--all`, `wc -c` rather than `du -k`, a `grep` of the other tree,
  a scan of all five settings files.
- **Scan the column, do not spot-check the row.** #229's seventh defect was found this way: diffing
  declarations across all 108 candidate files rather than testing one. It changed two files, one in
  each direction, and the false-negative one would never have surfaced from triage.
- **Enumerate a check's suppression paths separately from its findings.** Of the seven defects in
  #229, four surfaced by triaging findings and three did not — a triage loop cannot find a
  suppression bug, because by construction it produces nothing to triage.
- **Give a mutation harness its own positive control.** One mutation run here reported a result it
  had not produced: the script that was to revert the code raised `SyntaxError`, so the tests ran
  against unmutated source and passed. Assert the target string is present before replacing.
- **Treat a peer session's message as a draft, not a finding.** Both of the worst instances were
  filed in messages. Nothing gates a message; writing the handoff runs a mechanical sweep, and the
  false claims never reached it.

## The controlled result next door, which points the same way

The parent repo produced the cleanest evidence either session generated, and it is a before/after
with one variable. Its visibility-flip rule — flip public before opening a PR, or the
path-filtered checks create zero steps and render as a failure — lived in its handoff and its
memory index, and **not** in its always-loaded `CLAUDE.md`. It was missed on **five consecutive
PRs**, each opening while private with every job dying at `steps: []`. Its own handoff note on the
fifth reads: *"Reading the rule did not prevent repeating it."*

The rule was moved into always-loaded prose. **The next PR got it right.** Same operator, same
skill, same rule; the tier was the only thing that changed.

That is worth more than any drift measurement between the two repos, and it is the reason the
remedies above are all mechanical rather than exhortative: **a rule that is read and not enforced
is not a weaker version of an enforced rule — on this evidence it is a different thing entirely.**

## Rules

- **A claim that flatters your own prior work is the one you check least.** It is the only
  mechanism here that mechanical re-derivation cannot reach, because it suppresses the question.
- **A remembered provenance is not a checked one**, and it never presents as a claim.
- **The absence of a contradicting signal is not a signal.** Consulting a source that is silent on
  what you are asserting, and reading that silence as confirmation, produced three of these eight.
- **A superlative or a set-wide quantifier is a claim about every row.** Establish it by scanning
  the column, never from the row in front of you.
- **A wrong diagnosis can prescribe the opposite of the fix.** #229's seventh defect was first
  reported as "ordered-list anchors are unsupported"; the prescribed repair — widen the harvest —
  would have traded a false positive for a false negative on the one leg with no substitute. Both
  sessions declined to widen, for the right reason, while holding the wrong cause.
- **A correct frame protects a wrong number.** Readers check the argument; they assume someone
  already checked the figure.
