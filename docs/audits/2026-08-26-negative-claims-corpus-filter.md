# `negative-claims` read 15% of its own corpus, and every figure it reported agreed

**Date:** 2026-08-26
**Verdict:** FOUND and FIXED — a coverage defect in a doc-drift gate, not an edge claim. No
sleeve verdict moves and no measured `avg_r` changes. From the #218 extraction until this
branch the leg's corpus query was `git grep -nI -e x`, which is not "every line" but every
line containing the letter `x`; it reached 1,892 of 12,277 non-blank corpus lines, and 98
claim-shaped lines tree-wide were unreachable at any regex. The leg was measured, tuned and
documented against that slice for its whole life, and the suite stayed green throughout
because every test injects a fake runner.

## The question

The skill queue carried a reproducible miss: `.claude/context/tools.md:528` asserted that the
505-member research universe "has no scheduled refresher", #265 falsified it by shipping
`wifey-universe-sync.timer`, and the leg reported 9 findings with none in `tools.md`. The
filed cause was the documented `has no` repo-self anchor — the subject is "Everything else",
which the tool cannot resolve to a repo-self noun, so the line is filtered.

The queue entry is a hypothesis about a fix, so the first job was to re-derive the mechanism.

## Two wrong mechanisms before the right one

**The filed cause is wrong.** Adding anaphoric subjects to the anchor does not catch the line.
The subject `Everything else` is separated from `has no` by a ~90-character em-dash apposition
*and* a line wrap, so no same-line adjacency rule reaches it. Tested directly: the candidate
pattern matches neither line 527, nor line 528, nor the two joined.

**The second hypothesis is also wrong.** Markdown here hard-wraps at ~100 columns, so `git
grep`'s line unit is not the paragraph unit. Joining paragraphs before matching was the
obvious remedy and it gains **2 findings tree-wide**, neither of them the target.

**The operative cause is the corpus query.** `git grep -nI -e x` filters to lines containing
the literal letter `x`. Line 528 contains no `x`, so it was never a candidate to match. The
`has no` anchor was never reached.

| Surface | Lines the leg could see |
| --- | --- |
| Corpus paths, non-blank, tree-wide | 1,892 of 12,277 (15%) |
| CLAUDE.md alone, non-blank | 195 of 879 (22%) |
| Claim-shaped lines with no `x`, defect-era regex | 46 of 69 (67%), unreachable |
| Same, under the widened regex shipped here | 58 of 82 |

## Why it survived eight months

Three properties compounded, and the third is the one worth carrying forward.

1. **The boundary was mocked, so no test exercised it.** Every case in
   `tests/test_post_branch_checks.py` injects a `runner`, which is what makes the checks
   testable without a repository — and it means the real argv had no test at all.
2. **The failure direction was flattering.** A filter nobody declared reads exactly like a
   corpus nobody wrote a claim into. The leg's quietness was legible as a well-tuned scope.
3. **The instrument's own numbers corroborated it.** "Anchoring cuts the corpus 33 → 21
   lines", "8 of 8 catches", "findings per run 0–5 → 1–11" — every figure quoted in the
   docstrings and CLAUDE.md was measured through the filter, so re-deriving any one of them
   reproduced the same truncated view and confirmed it.

⚠ **A measurement taken through a defect cannot detect that defect.** This is the same shape
as the handoff's old `Line count:` stamp, whose only consumer was the check that verified it.

## Measurement

Replayed against the real `check_negative_claims` over the six merged branches to `7519f0b`.

| Design | findings/run | soft/run |
| --- | --- | --- |
| Status quo (`-e x`) | 8.5 | — |
| Corpus fixed, scoping unchanged | 31.7 | — |
| Corpus fixed + tiered scoping (shipped) | 13.2 | 18.2 |

The 31.7 figure is why the corpus fix could not ship alone: this repo's own doctrine is that a
leg which is never clean trains dismissal. The shipped design reports **13.2 against 8.5 while
reading 6.5× the corpus — 4.2× quieter per corpus line**.

The newly-visible population is not mostly noise. On `7519f0b` it includes `CLAUDE.md:195
nothing installs (matched wifey-signal-watch.timer)`, `README.md:902 there is still no (matched
Type=oneshot)` and `deploy/backup-analytics.sh:50 this repo has NO (matched daemon,
signal-watch)` — the `nothing installs` / `still no daemon` family that timer branches falsify,
which is the leg's core purpose.

## What changed

- **Corpus query** `-e x` → `-e ""`. The defect.
- **A finding requires a token the author marked** — backticked or hypothesis id — or a prose
  token carrying punctuation, a digit or a capital. A hit on bare English ("there is no
  **state**") is **demoted to a named re-read note, never dropped**: the note lists each
  `path:line`, because this leg's only confirmed true positive was found by a human re-reading
  a paragraph.
- **The subject is read to the LEFT of `has no`**, where the discriminating noun actually sits.
  "The 505-member research **universe** has no scheduled refresher" scoped on `{scheduled,
  refresher}` — words absent from the branch that falsified it.
- **Every marker on a line is read**, not the first. README's "Nothing consumes it — there is
  no codecov/coveralls step" hid its real subject under the second marker.
- **Quoted phrases count as subjects**, and `__main__` survives token extraction; stripping
  `_` had turned it into `main`, a stopword.
- **The `has no` anchor** admits an intervening adverb (`wifey **still** has no daemon` matched
  nothing) and a definite noun phrase that is not a third party. Cost: +0.2 findings/run.
- **Three exemptions**, marker-keyed with reasons: two sentences no branch can settle and one
  that is true by standing policy.

Unconditional-report lines went **8 → 3 → 0**; the remaining three are the exemptions.

## Bounds on the claim

⚠ **This is a coverage fix, not a correctness claim about any doc.** It says the leg can now
see 58 claim lines it could not (46 under the regex as it then stood); it does not say those lines are wrong. Eleven report on this branch
at the time of writing and every one triaged to NO EDIT: seven are standing claims that are
still true, scoped in because the new prose quotes their tokens (`Type=oneshot`, `deploy/`,
`wifey-signal-watch.timer`), and the rest are self-reference — a branch about this check's own
trigger language is the one useless control, as the skill queue already records. ⚠ That count
moves with every further edit to this branch, so re-run rather than quote it.

⚠ **The distinctiveness proxy is imperfect in one known direction.** A distinctive but
plain-lowercase name (`codecov`) reads as generic and is demoted to the note rather than
reported. That is the safe direction — the note still names it, so the cost is a re-read.

⚠ **One exemption carries a deliberate hole.** `("deploy/README.md", "nothing installs")` mutes
both sentences carrying that marker in that file. If the repo ever does install a unit, this
leg will not be what tells you; the policy line in CLAUDE.md is.

⚠ **The leg still cannot see a PR title or body**, and paragraph-level claims split across a
line wrap remain out of reach at the line unit. Neither changed here.

## Decisions

- **Fix the corpus and re-tighten in one PR**, rather than shipping the one-character fix and
  leaving the leg at 31.7/run. A gate that reports 32 items is not a gate.
- **Do not unanchor `has no`.** Measured at 57.5 findings/run; CLAUDE.md already prices this.
- **Do not re-scope to a window around the regex match.** Measured against the pre-#248 tree it
  would have suppressed the leg's only true positive; unchanged by this branch.
- **Delete the stale figures rather than restate them.** They described a tree the leg was never
  reading, and a number is only re-usable if the thing it measured still exists.

## Reproduction

Corpus size, against the pre-branch tree (`7519f0b`), because this branch edits the corpus:

⚠ Paths are inlined rather than held in a variable: this repo's shell is **zsh**, which does
not word-split an unquoted `$VAR`, so `-- $P` passes one argument and both counts return 0.

```bash
git grep -nI -e x  7519f0b -- CLAUDE.md README.md Makefile .claude deploy | wc -l
#   1892  reachable under the old pattern
git grep -nI -e "" 7519f0b -- CLAUDE.md README.md Makefile .claude deploy \
  | grep -cE '^[^:]+:[^:]+:[0-9]+:.*[^[:space:]]'
#  12277  non-blank corpus lines  ->  1892/12277 = 15%
#         (the anchored form matters: a loose `grep -c ':.*[^ :]'` scores 14897,
#          counting blank lines whose `rev:path:lineno:` prefix satisfies it)
git grep -nI -e x  7519f0b -- CLAUDE.md | wc -l    #  195
git show 7519f0b:CLAUDE.md | grep -c .             #  879  ->  22% for this file alone
git show e87b0cd:.claude/context/tools.md | sed -n '528p' | grep -c x   # 0
```

⚠ **The 22% figure is CLAUDE.md's alone and the tree-wide number is 15%** — an earlier draft of
this audit quoted 22% as the corpus-wide rate, generalising one file's ratio to five paths. It
was caught by `/post-branch`'s claims audit, which is the fifth listed shape: a set-wide claim
built from a spot check.

Unreachable claim lines, which must be counted with the tool's OWN regex rather than a loose
absence grep — an earlier draft of this audit said **98** by using the latter, and phase 4's
claims audit caught it:

```python
# against 7519f0b, over NEGATIVE_CLAIM_PATHS, using NEGATIVE_CLAIM_RE
# defect-era regex : 69 claim-shaped,  46 carry no `x`  (67%)
# widened regex    : 82 claim-shaped,  58 carry no `x`
```

The per-run figures replay `check_negative_claims` over `git log --first-parent -6 main`,
substituting a `runner` that rewrites the corpus query and prefixes the tree-ish, so each
branch is scored against the tree **as it stood on that branch** with the diff of that same
commit. `8.5` re-runs it with the pattern forced back to `x`; `31.7` forces the pattern empty
while leaving scoping untouched; `57.5` additionally drops the `has no` anchor. The ratios are
`12277/1892 = 6.49` and `13.2/8.5 = 1.55`, hence **4.2× quieter per corpus line**.

The regression control is `TestCorpusQueryReachesEveryLine`, which pins the argv and the
behaviour separately. Reintroducing `-e "x"` turns the argv leg red and leaves the behaviour
leg green — the behaviour leg's fake runner cannot see the defect, which is the whole point.
