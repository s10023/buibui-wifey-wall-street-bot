# CI, preflight and visibility flip notes

Supports Phase 5 and Phase 6 (flip-back gate) of `SKILL.md`.

## Re-home before the commit

**Run phase 6's re-home pass before this commit, not after the push.** When the handoff
prune moves a rule into a tracked file (CLAUDE.md, a skill, `.claude/context/`), that
edit belongs in this phase's commit. Made after the push, it is a second push to an open
PR, which re-runs the whole matrix and moves the head off the SHA you verified. The prune
itself, which only touches the gitignored handoff, still runs in phase 6.

## Scoping preflight

**Scope it with a positive check, never a judgement — the default stays run.** Two
greps decide: does the diff contain Python, and does any test read a changed path
(`grep -rl <changed-path> tests/`)? If both answer no, the clone re-runs the whole suite
only to reproduce `main`'s own result — say in the PR body which gate you ran instead,
naming the two greps. "This looks harmless" is not the discriminator; a gate with no
stated scope makes the correct call look like a deviation, which is how it gets dropped
later on a diff that did need it. Re-derive the cost on this repo rather than assuming
another project's figure — wifey's preflight runs the whole suite in a fresh clone, so
its cost is the suite's own runtime, and running both `make test` and preflight on one
branch pays that cost twice for nothing.

## What preflight closes

A gitignored path that exists on this box and nowhere else is invisible to every local
run — `config/stocks.json`, `.claude/sensitive-terms.txt`, `docs/plans/` and
`analytics.db` are all absent on a clean clone. CI already is that clone, so this
closes **timing, not detection**: catching it here costs a local suite run, catching it
after the push costs a metered Actions cycle, a red PR, and a visibility flip to read
the failure at all.

## Preflight exit codes and blind spots

It must run after the commits — it refuses on a dirty tree, because a clone sees
committed state only and would otherwise test stale HEAD and report green.

**`make` collapses the exit code, so read the banner**: `REFUSED` (dirty tree) and
`INFRA` (clone or install died) are not suite failures. Its two blind spots are stated
in `tools/clone_preflight.py`: an absolute `$HOME` default (`EXTERNAL_ROOTS`' shape),
and a CLI branch no test reaches.

## The visibility flip decision

**Then decide the visibility flip, before `gh pr create`.** **The flip is about the
account's allowance, never about your diff's paths.** While the monthly allowance holds,
a private repo runs the whole matrix for free, so the question this phase asks is
"did the last run on this repo execute real steps?" — a yes means open the PR private.
**Confirm the flip with the user on every occasion** — CLAUDE.md makes the mechanics
standing authorisation and the timing not, because the window republishes the parent's
pre-fork commits. Read the sweep's `sensitive-terms` leg before asking: it screens every
Issue and PR the flip publishes, and `UNREADABLE` there means unscreened, not clean.

The paths filter fires **for** Python, so a Python diff **runs** those jobs; a
**docs-only** diff is what skips them. Paths tell you what a private PR *loses* once
the allowance is gone — never whether to flip, and that reasoning is about *your diff*,
while the Actions allowance is about *the account*. `markdownlint` and `Trivy` have no
path filter, so an exhausted allowance zeroes them too. **The tell is the conclusion,
not the step count: a path-filtered skip reports `SKIPPED`, an exhausted allowance
reports `FAILURE`, both at `steps=0`.** Skip the flip only if the last run on this repo
executed real steps; otherwise flip, and requeue the runs — enumerate them rather than
assuming a fixed count, since a path-filtered workflow (`Docker Build`) may not run for
every diff.

## Sweep every open PR before the flip

**A flip is a repo-wide event, so sweep every open PR — and do it before the flip, not
after.** The public window is the only moment any other PR's checks can run, and while
the repo is public further pushes and re-runs are free. Dependabot PRs are where this
bites, because nobody is watching them. Enumerate the open PRs and read their step
counts first: `steps=0` with conclusion `FAILURE` is exactly the shape a re-run repairs
at zero marginal cost inside a window you are opening anyway.

## The sweep is not a check leg

This is deliberately not a `post_branch_checks` leg — the sweep needs `gh`, and phase 1
is git-only on purpose.

Phase 6's flip-back gate closes the other half of the pair.

## Composing the PR body

Then compose the **Documentation updates** section and pass it in the *initial*
`--body`. That ordering is the whole payoff — do not open the PR and then edit its
body. Write PR and Issue bodies without hard line breaks inside a paragraph or bullet:
one line per paragraph or bullet. GitHub renders a single newline in a PR or Issue body
as a line break, so hard-wrapped text renders ragged.

## One chain

One chain, so a hit or a missing file (both non-zero through `make`) stops the post. The
body path is repo-relative for the reason phase 1 gives.

## Why merging is not the last step

**Merging is not the last step when the repo was flipped public.** Merging starts a
*fresh* run on `main`, and flipping to private kills whatever is created after the
flip — `Regression tests` `needs:` lint-typecheck-test and is not created until ~4
minutes in, so an early flip leaves it at `steps=0` and main looks red for billing
reasons rather than code ones.

## Three traps in a hand-rolled waiter

**Use that tool rather than hand-querying `gh` or hand-rolling a waiter.** It settles
the billing question directly, and a hand-rolled version has three traps that all
render identically to a real failure:

- **`steps=0` is billing, not code.** Every job fails in 2–5s with zero steps when the
  Actions allowance is exhausted. Duration alone never settles it — a healthy
  `markdownlint` finishes in 7s, and a pass in seconds needs the same check as a fail.
  `steps` is the discriminator. Never open a debugging session on that shape.
- **`total_count: 0` is a third state and is not requeueable** — a PR opened while the
  repo was private may have no run at all. But that endpoint exact-matches, so a short
  SHA also returns 0, silently. The free discriminator: *if the rollup is green, the
  query is wrong, not the CI.* The transferable rule — **an exact-match query that
  returns empty rather than erroring on a malformed key is indistinguishable from a
  true negative.**
- **A wait must gate on a check-count floor, not on "nothing pending".** An empty
  rollup satisfies "no check is unresolved", so the loop exits immediately and reads
  as all-passed. Assert the count reaches 5, treat `""` as pending (a queued check's
  conclusion is the empty string, not `null`), and count with `jq 'length'` — `wc -w`
  scores `Trivy filesystem scan` as two.

## Enumerate the runs

**Enumerate the runs; never assume how many.** There are three workflow files, but a
PR does not always get three runs — `Docker Build` is path-filtered, so a dependency
bump gets 3 and a typical feature branch gets 2. A fixed count in an instruction is
right when written and wrong after one workflow edit.

**Sweeping every open PR is a phase 5 decision** — see it there; by the time you reach
this phase the window has already been spent.
