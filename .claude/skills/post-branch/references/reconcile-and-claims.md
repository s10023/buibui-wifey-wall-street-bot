# Reconcile and claims audit notes

Supports Phase 4 of `SKILL.md`.

## The N-way roll

Rewrite "Last session" to today's date + branch + a one-line summary. **The roll is
N-way, and N comes from the live index, never from this sentence**: today → **Last
session**, each existing dated session bullet down one slot, and the oldest rolls
verbatim into `memory/project_session_log_<month>.md`. With no `Prior session` bullet
in the index — its current shape — that is a two-way roll; don't add one to match an
older description of this step, since the cap is 6 bullets / ~17KB. Grep the log
afterwards to confirm the rolled bullet landed. Convert relative dates to absolute.
Current State carries pointers (Issue and PR numbers), never open work: an open
question or a next step is an Issue, filed in the reconcile below.

## The PR number is filled later

**The `#NNN` is the one field this phase cannot know** — phase 5 creates the PR. Do the
roll here regardless: it needs nothing from the PR. Write the bullet with the number
left out, and phase 6's re-verify fills it from the same `gh` query that rewrites the
handoff.

## MEMORY.md cap

Respect the cap — this step is where it gets broken. Current State holds at most **6
bullets**, "Last session" at most 2 lines, every other bullet exactly 1. `memory-cap` in
phase 1 checks it. A rich multi-sentence entry feels like diligence but is what grows
the index past its purpose as a router; the index is re-read every session, so that
bloat is billed per conversation.

**The cap is bytes as well as bullets, and the two fail differently.** Bullets are
fixed by the roll; the byte cap is not, and the roll itself can push the index over it.
**Re-home the largest bullet to its topic file before trimming the new one**, then grep
the destination to confirm it landed. The index is a router, so the biggest bullet is
nearly always the one that has quietly become a store — and its topic file usually
already owns every detail it is carrying.

**Check the live file rather than the last person's description of it.** CLAUDE.md's
Session Memory Protocol is the authority.

## Closing Issues

1. **Close what the branch finishes.** Put `Closes #N` in the phase-5 PR body for
   each Issue the branch completes. A branch that finishes only part of an Issue
   says which part in a comment on it, and the Issue stays open with the remainder
   restated. **Reconcile to what you verified, not to what is tidy**: a shipped
   change with a known residual gap gets the gap written down, not a blanket close.

## Reference surfaces

<!-- markdownlint-disable-next-line MD029 -->
3. **Touch the reference surfaces only when the branch changes them.**
   `docs/north-star.md` holds the north star, gates G1–G4, the data-cost policy and the
   frozen list; `memory/project_todo_master.md` holds closed verdicts and the
   deliberately-not-queued list. Neither takes queue rows.

## Stale and blocked Issues

**A stale Issue is worse than a missing one**, because it reads as current
evidence: three stale SoT rows once described shipped code as remaining work, and a
session picking up from them would have rebuilt it.

**Re-read every Issue labelled `blocked` that the branch touches, and name what would
unblock it.** A blocker that has since been written down does not announce itself,
and a blocked item is precisely the one nobody re-reads *because* it is blocked. The
blocker and the blocked item usually sit in different Issues, which is why neither
notices, so link them. **Naming the unblocking condition is the deliverable**; an
item whose blocker you cannot restate is not blocked, it is unexamined.

## Commit messages and comments are audited prose

**A commit message is audited prose, and by phase 4 it usually already exists.** The
normal flow commits while the work is fresh, so a failing claim is typically already
written by the time this phase runs. On an unpushed branch the repair is
`git commit --amend -F <file>`, never a follow-up wording commit that leaves the false
claim in the history the PR ships. After a push, amend plus force-push needs explicit
OK — otherwise correct the claim in the PR body and say the message predates it.

**A code comment or docstring is audited prose too — and it is the surface the author
writes rather than one a reviewer reads.** Everything else on that list gets re-read by
someone deciding whether to merge; a comment reads as the author's own reasoning rather
than as a claim, so nobody re-derives it — a wrong number beside a frozen set is
durable, because it is the justification the next session inherits for not
re-checking the set.

## The six shapes

1. **A claim about a mechanism supported only by a count.** A count is consistent with
   many mechanisms. Demand the query that rules the *others* out.
2. **A claim inherited from the handoff counts as the branch's own.** If the branch
   repeats it, the branch owns it.
3. **An upstream number quoted as this repo's.** Re-derive here, or say "preventive,
   not a repair".
4. **A rate with no null.** "X% of Y does Z within N" is not a finding until it states
   what fraction of an arbitrary comparable does Z. This audit checks whether a number
   is *true*, never whether it is *informative*, so the null has to be demanded
   explicitly.
5. **A set-wide claim built from a spot check.** *every*, *none*, *all N*, *in zero
   cases*, *the only* — a sentence quantified over a set can only be established by
   scanning the whole column. The tell is grammatical rather than numerical, so it is
   cheap to spot once you look for it.
6. **A claim about a series, checked only against the repo.** Grep the memory tree
   too. A branch appending to a running measurement — costs, counts, timings — and
   claiming a new extreme is making a claim about the series' **completeness**, not its
   values, and a series that merely looks monotone is evidence of what somebody
   remembered to write down. No mechanical leg can catch this: `queue-items` and
   `handoff-symbols` read the handoff, every other leg is repo-scoped, and
   `stale-anchors` is the one leg reaching memory but it checks anchors rather than
   data. Ask what else measured the same quantity and never reached the series.
