# Tools Reference

`tools/` holds one-shot analysis and audit scripts — they are not part of the daemon or CLI
surface. Each is run via `PYTHONPATH=. poetry run python tools/<name>.py` (with script-specific
flags) or an equivalent `make` target.

## strategy_edge_audit.py — Phase 0 strategy edge audit

Aggregates `backtest_trades` by (strategy × tf × regime × session) + combo uplift;
deterministic KILL/DEMOTE/KEEP rule. See `docs/redesign/buibui-redesign-phase0.md`.

**Run:** `PYTHONPATH=. poetry run python tools/strategy_edge_audit.py`

## universe_coverage.py — N3 read-only OHLCV coverage report

Read-only OHLCV coverage report over the research breadth universe (`config/universe.json`);
per `(member × timeframe)` bars present + date range, a missing-symbol roll-up, and a header
carrying the universe lifecycle-bias caveat (`ResearchUniverse.describe()`). Pure read (`ohlcv`
table, `read_only=True`); `build_coverage_rows`/`summarize` are the testable units.

**Run:** `make universe-coverage` or
`PYTHONPATH=. poetry run python tools/universe_coverage.py [--db PATH] [--timeframes 4h 1d 1wk]`

## dead_surface_check.py — cells where declaration and output disagree

Reports `(strategy × timeframe)` cells in both directions of the mismatch — the data-driven half
of the silent-surface enforcement (the static half is `tests/test_makefile_invocations.py`). Both
halves key off the same declared set, `analytics.signal_config.declared_cells` (every strategy ×
its `strategy_timeframes` override, else the config's `timeframes`), which lives in
`signal_config` so the two questions cannot drift apart.

**Declared but dead** (`find_dead_cells`): joins the declared set against `backtest_runs` scoped
by `day_filter` and flags any cell with no runs, or runs but zero `total_signals`.

**Rated but undeclared** (`find_orphan_ratings`): the inverse. Scans `confidence_ratings` for the
config's TOML stem and flags any row whose `(strategy, tf)` the config no longer declares,
worst-stars-first. `recalibrate` has no notion of the current config and `upsert_confidence_ratings`
never deletes, so a dropped cell keeps its stars and takes a fresh timestamp on a stale value on
every refresh — measured 2026-08-06: `fib_golden_zone × 4h` sat at 3-star +0.4688, the
second-highest-rated cell in the whole `signal_watch` table, 2.5 months after the strategy left the
config. A dead cell surfaces as a zero and reads as absence; an orphan surfaces as a number and
reads as evidence, which is the asymmetry that let it survive.

Orphans carry a tier (`OrphanRating.tier`): `undeclared anywhere` versus
`declared by another config`. Both mean the rating's own daemon will never scan the cell, so the
tier labels a finding
and never suppresses one — but the fix differs, and the split is real because `signal_watch`'s 22
declared cells are a strict subset of `signal_watch_weekdays`'s 32, leaving 10 that only one config
declares. `main` decides the tier from `cells_declared_elsewhere(declared_by_config, config)`,
extracted rather than inlined because `main` itself has no test. The parent's direction-aware half
stays behind on the sync queue: it exists upstream because all three of its configs carry
`strategy_timeframes_long`/`_short` narrowing, and this repo declares no such key, so
`declared_cells` is direction-agnostic and a direction-aware check would report the same set.

The undeclared-rating case is the one that matters, because a plain "did it run?" check cannot see
it: `signal_watch.toml` held 338 `1wk`/`tue_thu` rows with zero closed trades for three months, so
the surface looked covered. Emptiness is indistinguishable from coverage unless something
explicitly asks.

`_KNOWN_DEAD_CELLS` is a `(day_filter, strategy, timeframe)` allowlist mirroring
`tests/test_lookahead.py::_KNOWN_LOOKAHEAD_DETECTORS` and should only ever shrink; it reached empty
on 2026-08-06 (its five entries were resolved, though the diagnosis recorded beside three of them
turned out to be wrong — the module's docstring keeps that as a caution). There is no allowlist for
orphaned ratings: the fix is to prune them, not to accept them.

Pure read (`read_only=True`). Exits 1 on any non-allowlisted dead cell or any orphaned rating;
`--strict` also fails on allowlisted dead cells, which is how you verify the list can shrink.
Inside `make db-update` it never blocks the refresh, but the completion banner is conditional on
it.

**Run:** `make check-dead-surfaces` or
`poetry run python tools/dead_surface_check.py [--config PATH ...] [--db PATH] [--strict]`

## orphan_test_audit.py — test classes that NAME a unit but never CALL it

The third mechanical enforcement check, alongside `dead_surface_check.py` (data-driven) and
`tests/test_makefile_invocations.py` (static). It exists because a green suite proves nothing about
whether a test exercises its subject: `TestEvGate` held five tests that never invoked the EV gate,
because the gate was `def _passes_ev_gate` nested inside `run_scan_cycle` and therefore
unimportable. Every test re-implemented the comparison inline — one asserted the defect as the
expectation, another reduced to `assert None is None` — and all five passed against any
implementation.

Two verdicts:

- **`not-importable`**: the subject matches a closure and no module-level callable. The unit is
  unreachable from a test, so the tests can only re-implement it. Extraction is a prerequisite for
  the fix, not scope creep.
- **`not-called`**: an importable callable matches but no test in the class calls it. Ordinary
  drift after an extract or rename.

Matching is token-based and directional: the callable's name must contain the class's subject
tokens contiguously and in order, so `ev_gate` matches `_passes_ev_gate` while `p_r` matches
neither. An earlier substring formulation produced 95 findings on a clean tree, nearly all junk. A
class whose subject matches nothing at all is not reported — descriptive names
(`TestWatermarkOnSend`) are legitimate and were most of that noise.

`EXEMPT_CLASSES` is keyed `"<test file>::<class>"` and every entry carries its reason inline; an
entry without one lets the check decay into a no-op. The three current entries are the same
false-positive shape — the subject reached one indirection away, via a local test helper or a CLI
`main()`.

Heuristic, so it is advisory and not part of `make test`, unlike `tests/test_schema_insert_arity.py`,
which is deterministic and runs in the suite. Exit 0 by default; `--strict` exits 1 on findings.
Verified against the pre-fix tree, where it isolates `TestEvGate` and names `_passes_ev_gate`;
clean on HEAD.

Its `EXEMPT_CLASSES` lookup must use `as_posix()`, never `str(path.relative_to(REPO_ROOT))`: the
keys are written with forward slashes, and on Windows `str()` renders backslashes, so all four
exemptions missed and the check reported four findings it had already ruled on. The defect is
platform-dependent — on Linux `str()` and `as_posix()` agree — which is why it needed a positive
test rather than reasoning from Linux behavior; the same sites also need `read_text(encoding=...)`
rather than relying on `PYTHONUTF8=1` from `make`. Pinned by `tests/test_orphan_test_audit.py`.

Asserting that no exempt class appears in the findings is a vacuous check here: with backslash keys
the intersection is empty whether the lookup works or misses entirely. The control that actually
observes the channel asserts the emitted keys contain no backslash, plus a liveness check that each
exempt class still exists in the tree.

**Run:** `make check-orphan-tests` or
`poetry run python tools/orphan_test_audit.py [--strict]`

## post_branch_checks.py — every mechanical `/post-branch` check, in one run

Thirteen checks, extracted from shell blocks that used to live inline in `post-branch/SKILL.md` and
had to be noticed and copied by hand. Extraction cut the skill from 1,649 to ~600 lines and made
the checks testable: `tests/test_post_branch_checks.py` gives each a positive control, which the
prose versions never had.

Checks: `queue-items` · `handoff-symbols` · `new-files` · `new-modules` · `new-targets` ·
`amended-targets` · `negative-claims` · `doc-indexes` · `md-atx` · `memory-cap` · `handoff-size` ·
`stale-anchors` (engine in `stale_anchors.py`, below) · `sensitive-terms` (the pre-flip gate,
ported from the parent; asks the tracked tree, this branch's commit content and its commit
messages, because a flip republishes the whole history and no file edit reaches a message, and
since #373 every Issue and PR title, body, comment and review comment, open and closed, read over
REST through `session_digest.fetch_gh_pages`. PRs are in because they publish on the same flip
and `--text` sees only a body not yet posted. Any failed or partial read is an `UNREADABLE`
finding, never clean. Since #448 it also screens each title a rename replaced, from the `renamed`
events of the repo-wide `issues/events` listing, under that listing's own `MAX_EVENT_PAGES` cap
(20 pages and 25 renames on 2026-10-09, against ~23 new events a day). An absent
`.claude/sensitive-terms.txt` is a finding reading `NOT CONFIGURED`, never a skip, and terms are
masked in the output).

### amended-targets

The only leg that fires on a doc that is correct about the artifact: `new-targets` matches an
added `+target:` line, so an existing target that gains an override or changes a default leaves
every presence check green while the doc's enumeration goes one short. Three pure functions:
`changed_line_numbers` walks hunk headers and blames a deletion on the position it vacated
(skipping it would make a recipe line removed from a target invisible); `targets_by_line` maps
each line to its owning recipe, with `.PHONY:` failing the declaration regex and, being
un-indented, also clearing the current target; `check_amended_targets` intersects them and
subtracts targets this branch added, so `new-targets` keeps that case.

`names_token` matches on `-` boundaries explicitly rather than relying on `\b`, because `-` is a
non-word character and `\bwifey-backtest\b` matches inside `wifey-backtest-extra` — nearly every
target here is hyphenated. `check_new_targets` keeps the plain `\b` deliberately, as the parent
left it: there a substring hit reads as documented, so it fails in the quieter direction, and
widening it could newly fire on real branches. `_read_each` keys on `as_posix()`: on Windows
`rglob` yields backslashes, and this report gets pasted into handoffs, so a separator mismatch
would make an allowlist match nothing.

Verified by a counterfactual: adding `$(if $(SINCE),…)` to `wifey-universe-sync` emits `AMENDED:
wifey-universe-sync — re-read .claude/context/tools.md, CLAUDE.md, deploy/README.md`. Ported from
the parent.

### Handoff-dependent legs

`queue-items`, `handoff-symbols` and `handoff-size` report `SKIPPED: no handoff file` rather than
clean when the handoff is absent, which it is on a worktree or a fresh clone since the file is
gitignored. One `_handoff_leg` carries the decision for all three, because "no finding" and "no
handoff" are different states and only one of them is green — a leg reporting a plain clean result
while its siblings skip would be invisibly wrong on exactly that case.

`handoff-size` speaks twice: over `HANDOFF_MAX_LINES` (240) it says prune before adding, and inside
the `HANDOFF_WARN_MARGIN` (20) band below the cap it warns that the next append breaches it (#346).
A cap that is silent while the file sits on it invites shuffling text to fit instead of re-homing.

### The `--text` surface

`--text <file>` (`make post-branch-text FILE=<path>`) is the pre-posting surface for the
sensitive-terms gate and runs alone, without any git surface: a PR title or body still being
composed is neither the tree, a commit nor on GitHub yet, so the leg reports clean on one naming
every term. Repeatable, `FILE=-` reads
stdin, exit 1 on a hit (`make` collapses that to its own 2, as with `wait_ci`). It prints line
numbers and a masked term, never the matching line, because the match sits inside the very prose
being screened. Belongs in phase 5 — a posted body is public on landing and a later edit does not
unpublish it.

### negative-claims

The corpus query must reach every line, never a bare `git grep -nI -e x`, which matches every line
containing the letter x rather than every line. That earlier form read 15% of the corpus
(1,892 of 12,277 non-blank lines) and said nothing about the other 85%: 46 of 69 claim-shaped lines
in the corpus at the time carried no `x` at all, including the Makefile's "The 505-member research
universe has NO scheduled refresher," a claim later falsified while this leg reported nothing about
it. The tests inject a fake runner, so the real argv was never exercised, and every triage figure
ever quoted for this leg was measured on the truncated slice. `TestCorpusQueryReachesEveryLine`
pins the argv and the behavior separately, with an `x`-free line as the positive control, since a
fake-runner test cannot see an argv regression on its own.

On the fixed corpus the leg reports 13.2 findings + 18.2 soft per run against 8.5 while reading 15%
of the tree — 4.2x quieter per corpus line it actually reaches. A finding needs a backticked or
punctuated token; a hit on bare English ("there is no **state**") is demoted to a named re-read
note rather than dropped, and the subject is read to the left of `has no` as well as the right,
since that is often where the discriminating noun sits.

`NEGATIVE_CLAIM_EXEMPT` narrows findings, keyed `(path, token)` → reason. A hit is dropped only
when every matched token is exempt, so one unexempt token still reports the line — an entry narrows
rather than deletes, and the count is printed, never swallowed. `tests/test_post_branch_checks.py`
fails a dead entry against the real file so the allowlist keeps an external referent. Do not scope
a finding to a window around the regex match instead of the whole line: these claim lines are 3-6
KB paragraphs, and scoping to a window suppressed the leg's only true positive in testing, where
the regex match sat roughly 1,400 characters after the sentence a branch had actually falsified.
The line is the unit for this reason, and `attribution` stays off the allowlist because on two
lines it is the claim's own subject.

A rewrite re-adds every claim it keeps, and each one then scopes in on its own re-added text: #307
reported 39 findings, every one an unchanged absence claim. `demote_unchanged_in_rewrites` moves a
finding into the note when its file's added lines reach `REWRITE_FRACTION` (0.5) of the file and
its whitespace-squashed text already sits in `git show main:<path>` (#344). A lightly edited doc, a
new file or a reworded claim still reports. The line is still the unit, so a claim rewrapped across
a line boundary demotes only when the whole current line was already contiguous on `main`.

The phrasing allowlist is the leg's main blind spot. Measured 2026-08-26: of eight absence claims
one branch falsified, the leg caught none — all eight named the missing thing directly, in a bare
existential or possessive-negation form, a phrasing with no allowlist entry because no prior
incident had used it.
Two of the eight sat in `deploy/`, outside `NEGATIVE_CLAIM_PATHS` and unreachable at any regex, so
the path and the phrasing widened together. The tree's own emphasis convention hides a further case
("has **no daemon at all**"), so the pattern must allow a bold marker mid-phrase.

Three properties bound the leg's cost:

- `has no` is anchored to a subject that is not a third party — `wifey`, `this
  repo|fork|tree|skill`, `the fork|repo`, or any definite noun phrase that is not `the parent` or
  `the endpoint` — plus an intervening-adverb slot and a line-initial arm for a claim whose subject
  sits on the previous line. Unanchored, it matches mostly claims about what something else lacks
  ("yfinance OHLCV has no taker data," "the endpoint has no children field"), which no wifey branch
  can falsify, and measures 57.5 findings per run.
- `claim_subject_tokens` is a scoping fallback, not a wider token list: it lets a claim line with no
  backticked token be scoped by its own subject noun instead of being unscopable and therefore
  reported on every branch forever (widening the regex without this fallback took such lines from 0
  to 11). It can only remove a report; a subject that is all stopwords, or that runs off the end of
  its line, still reports.
- Price the triage load, not just the catch: on the fixed corpus the leg reports 13.2 findings +
  18.2 soft per run over six branches, against 8.5 while reading 15% of the tree — 4.2x quieter per
  corpus line. A check that is never clean trains dismissal.
  `TestTheLegIsCleanOnAnUNRELATEDBranch` pins the property that made it shippable (zero claim lines
  report unconditionally) against the real tree. Fix a newly-noisy line by scoping or exempting
  that one sentence, never by growing `_SUBJECT_STOP` until the number goes away.

### queue-items, new-files and the unscoped negative-claims fix

`queue-items` exists because nothing previously swept the handoff's own task list for work the
branch just finished, so a completed item could survive under a heading like "Settled — do not
re-litigate" — i.e. as an instruction to redo it. An earlier mitigation keyed on added Python
symbols, so a docs-only branch defeated it; this keys the handoff's own distinctive tokens against
the diff content, which every branch has.

`new-files` exists because a plain basename probe reported a fabricated skill as covered: every
skill's basename is the shared constant `SKILL.md`, which matches CLAUDE.md's generic "Skills live
in `.claude/skills/<name>/SKILL.md`." `probe_names` probes the parent directory instead when the
basename names a role rather than a file (`SKILL.md`, `README.md`, `__init__.py`, `INDEX.md`).

`negative-claims` originally shipped unscoped: `check_negative_claims` took no diff argument,
grepped the tree for absence language, and reported the same findings on every branch regardless of
what that branch touched, while the skill's own table described it as asking about "something this
branch just added." It now intersects the claim line's distinctive tokens against the diff's added
lines — a removal makes an absence claim more true, so only additions count — and the remainder
becomes a `note:` rather than a finding. A claim line with no extractable token is still reported,
not suppressed: this leg fails open on purpose, because a miss ships a doc denying something now
present.

Untracked files count as added: `git diff` cannot see them at all, so the presence checks read
`git status --porcelain` rather than relying on the diff, and are correct whether or not a new file
has been `git add`ed yet.

### new-modules

Asks two questions, because a presence probe alone cannot answer the second. Where the context docs
merely mention a package, it asks whether the module is named at all (the original word-boundary
probe). Where the docs keep an inventory of a package's members — a quorum of two backticked
siblings, below which the mentions are treated as incidental prose and the leg falls back to the
probe — it compares the documented member set against `ls` and reports the set difference.

The second question exists because the probe form reported a real omission as covered: measured
2026-08-21, `analytics/research_guards/sharpe.py` landed while `.claude/context/analytics.md`
enumerated ten of the package's eleven members, and the word "sharpe" appears throughout that file
as ordinary prose. Tightening the regex cannot fix this class — the hit is a real token in real
prose, so no boundary rule separates them; the check has to change what it asks. Backticks are the
discriminator, because docs write a file as `sharpe.py` and a concept as plain "sharpe," and only
the former is a claim about the package's contents.

This is the `skill-claims` split in code: a mechanical half asks whether the artifact exists, a
semantic half asks whether it still means what the claim says, and only the second needs an
external referent outside the check itself (here, `ls`) — the same shape as `/stats-dashboard`'s
card count and `missed_ports.py`'s undeclarable `PORTED` set. A count, a
presence probe and an allowlist all pass on any error that conserves their own shape.

Advisory by design: a finding is a candidate to dismiss in seconds, never an automatic edit. A false
positive costs a glance; a silent miss ships a doc that enumerates every sibling but one and reads
as complete. The Makefile is deliberately not an enumerating doc — a build rule is not
documentation.

### Uncovered steps

The sweep closes by naming the `/post-branch` phases it does not reach (`UNCOVERED_STEPS` /
`uncovered_notice()`), so passing the mechanical half cannot feel like passing the whole review. A
rule that competes with a nearby rule loses to whichever is read last, so the fix belongs on
reachability rather than another written reminder.

wifey cites `Phase N` where the parent cites `Step N`, and that divergence is deliberate: the
parent's phases are table rows with no headings, so a phase citation there is a dead anchor its own
`stale_anchors` correctly flags. Here the skill has real `## Phase N` headings and
`tools/stale_anchors.py` resolves `phase N` against them (`_HEAD_TYPED`), so the citation is a
checked, live anchor. `TestUncoveredSteps::test_every_cited_phase_resolves_in_the_skill` pins it.
Port the rule, re-derive the reason, rather than porting the parent's phrasing verbatim. Ported from
the parent.

The notice is suppressed for `--text` (which screens one composed PR body under time pressure and
wants no step list) and for any `--check` run, a deliberate partial invocation.

**Run:** `make post-branch-checks` (passes `--exit-zero`), or
`PYTHONPATH=. poetry run python tools/post_branch_checks.py [--check NAME ...] [--exit-zero]` to let
it exit 1 on findings. `--check` is repeatable (`action="append"`) — without it,
`--check memory-cap --check handoff-size` would run `handoff-size` alone and print a
complete-looking clean sweep. An unknown check name aborts the whole run rather than silently
shortening it. A bare call without `PYTHONPATH=.` works since #436; before that it died
`ModuleNotFoundError: No module named 'tools'` with exit 1, the same code as "findings".

## wait_ci.py — did CI settle, and did it actually RUN?

Two gates in one tool. `--pr <n>` (`make wait-ci PR=<n>`) waits on a PR's own checks; `--branch main
--min-jobs 5` (`make wait-ci-main`) is the flip-back gate — main's push run must reach a job-count
floor before the repo goes private again, because flipping kills whatever is created after it and
`Regression tests` is not created until ~4 minutes in. The branch mode wraps the same tested tool
rather than fresh hand-rolled shell at this step, since hand-rolled waiters have been wrong here
before (one reported `jobs=0` against a live `total_count=2`).

- A job-count floor, never "nothing pending": an empty result satisfies "no check is unresolved,"
  so a naive loop exits instantly and renders identically to all-green. `is_settled` requires the
  floor and `completed == total`; dropping either half restores a real defect, and `TestIsSettled`
  pins both plus the vacuous input.
- A `gh` failure raises; it is never turned into data. Returning `""` on a non-zero exit left an
  unreadable `actions/runs` response with every step count `None` and printed a false green
  asserting exactly what it had failed to observe. That state is now exit 4. Transient failures
  are retried inside the poll loop; an unrecoverable one propagates, and so does a named 4xx
  other than 408/429 (`GhError.permanent`), which no retry fixes.
- Every read is REST, never GraphQL: the PR path resolves its head over `pulls/<n>` (re-read each
  poll, so a mid-wait push gates the new commit) and lists `commits/<sha>/check-runs`, because a
  cloud session's proxy refuses `gh pr view --json` with a 403. Auth comes from
  `session_digest.owner_env`: an explicit `GH_TOKEN` wins, else the s10023 lookup, else ambient
  auth, and the lookup never raises. Ported from parent #887 and #893.

A run CANCELLED by a newer push is SUPERSEDED, not failed. A concurrency group holds one running
and one pending run, and a newer push cancels the pending one before it creates any jobs, so the
cancel shows on the RUN only; `was_cancelled` checks jobs, then runs. On a cancel the branch gate
re-reads the head: moved means it prints SUPERSEDED and gates on the new SHA inside the same
deadline; unmoved and short of the floor means CANCELLED, exit 1, at once. A cancelled row is never
billing. Ported from parent #880.

The branch gate counts `push`-event runs only. A `main` SHA also carries a GitHub-managed `dynamic`
run ("Configured Graph Update: pip in /.") created several minutes after the push runs finish;
unfiltered, a live run reported `jobs=6` where CLAUDE.md, this file and the tool's own constant all
say 5. `test_unfiltered_includes_the_dependency_graph_job` is the characterization test pinning the
exclusion.

`steps` prints as executed/declared, never declared alone. A job behind a `dorny/paths-filter`
declares its whole step list on every diff and skips the body on most of them, so a bare `steps=14`
on a docs-only PR would read as "the heavy leg ran" — the opposite of what happened.
`step_counts` splits the two by counting the steps GitHub reports as `skipped`; `?/N` means only the
executed half was unobservable and `?` means neither was. An exhausted allowance still declares
nothing, so `steps=0/0` settles as billing regardless of the executed/declared split. This closes
the hole memory `reference_ci_steps_counts_skipped` names: a green job with a healthy step count can
have run nothing. Ported from the parent.

`steps=0` alone is not the billing discriminator: a job GitHub never created settles `SKIPPED`
declaring nothing, which is what `Regression tests`'s `needs: lint-typecheck-test` produces from a
single failed test. Billing requires a FAILED conclusion at zero declared steps — an exhausted
allowance leaves chained jobs skipped too, so the failing row settles the matrix and its skips never
do. CLAUDE.md states the SKIPPED-vs-FAILURE tell under CI quota; the code reads `conclusion` before
branching on `steps` for the same reason.

Exit codes: `0` green and observed, `1` genuine failure, `2` timeout, `3` billing (FAILED at
`steps=0`), `4` settled green but step counts unreadable. `make` collapses all of them to its own
`2`, so call the script directly when the code matters.

**Run:** `make wait-ci PR=<n>` · `make wait-ci-main` · or
`PYTHONPATH=. poetry run python tools/wait_ci.py --branch main --min-jobs 5 [--timeout-min N]`.

## stale_anchors.py — citations of a section number that no longer exists

The engine behind `post_branch_checks`'s `stale-anchors` leg. Document A cites a numbered section of
document B — `/post-branch` "Step 10b", `/sanity-check` "§4a" — and B later renumbers itself.
No existing check could see this: `handoff-symbols` keys on symbols, and a section number is not a
symbol.

It is a library, not a CLI: there is no `__main__` and no argparse, so a bare
`poetry run python tools/stale_anchors.py` prints nothing and exits 0, indistinguishable from a
clean sweep. Reach it through `make post-branch-checks`, which passes scope.

Three decisions carry it:

- Quoting is tested as an enclosing span, never as the two adjacent characters. A quotation marks a
  mention rather than a use. The adjacent-character form missed a quotation wrapping
  target-plus-anchor as one phrase, and because `_QUOTES` is a `str`, `"" in _QUOTES` is `True`, so
  an anchor ending the line silently short-circuited to "quoted" and was dropped. Only the
  end-of-line branch was ever reachable, since `citations()` calls `_anchor_after` with
  `start = t.end()` of a target that must precede the anchor, so `begin == 0` cannot occur. An
  unterminated quotation now yields no span and therefore reports, because a false positive costs a
  glance while a suppressed citation is invisible.
- Kinds must agree, unless the citation is untyped. `§4a` names "the section numbered 4a"
  without claiming a kind, so it matches any declaration; `Step 6` and `Phase 6` are typed and must
  agree — a label-only comparison would call those two a match, and Step to Phase is the exact
  rename that keeps recurring.
- The ordered-list fallback is conditional. A document with no numbered heading (`/db-update`,
  `/ingest-x`) numbers itself through its column-0 ordered list, so those items are its steps. But
  `/post-branch` says "Phases, not step numbers" while carrying three column-0 rubric lists numbered
  1..5 — harvesting those unconditionally would silently validate every dead `/post-branch` "Step N"
  citation, blinding the check to its own founding defect.

Scope extends beyond the repo: of 6 dead (or would-be-dead, under mutation testing) citations
observed, 2 sat in the memory tree, which no repo-scoped check can reach. It runs over `.claude/`,
the four current-state files and `memory/*.md`. This is also why the check is not in CI-gating
`sanity_checks`: CI cannot see the memory tree at all. Dated trees are excluded (`is_dated_path`) on
the fork-drift leg's reasoning — a citation in a dated record was correct when written.

A first run found 3 real dead citations a hand sweep had missed: `/ingest-x` "step 5" cited twice
(`.claude/context/tools.md`, `/ingest-video`) when that skill's Flow stops at step 4, and a
surviving `/post-branch` "step 10b" in `memory/feedback_handoff_prompt_location.md`. Mutation-checked
against the live tree by renumbering post-branch's `Phase 6` heading, which surfaced 3 further live
citations, one in the memory tree — confirming the wider scope is load-bearing.

Known hole, pinned rather than papered over (`TestKnownHoles`): a document's own sub-label sitting
beside another document's name is syntactically indistinguishable from a citation of it —
`/ingest-x` wrote "handed to `/ingest-video` (1b)" where the label 1b was `/ingest-x`'s own.
Suppressing that by "the source declares this anchor too" was tried and reverted: documents share
small integers, and it dropped a real cross-doc finding. Only target-then-anchor is read, within a
bounded window, so a citation far from its target is invisible.

**Run:** via `make post-branch-checks`, or
`PYTHONPATH=. poetry run python tools/post_branch_checks.py --check stale-anchors`.

## child_env.py — the env for a child Python whose output the parent decodes

`python_child_env(base=None, *, drop=())` copies `os.environ` (or `base`), removes the `drop`
keys and sets `PYTHONUTF8=1`. `encoding="utf-8"` on a `subprocess` call fixes only how the parent
decodes; on Windows a child Python still writes piped stdout/stderr as cp1252, and one non-ASCII
byte then fails the parent's strict decode (Issue #411; `tools/route_dedup.py --help` emits 16).
`tests/test_explicit_encoding.py::test_child_python_writes_utf8` requires every text-mode call
whose literal argv starts with an interpreter (`sys.executable`, `python*`, `poetry run python`,
or a named interpreter variable) to pass `env=python_child_env(...)`, directly or through a
local name assigned from it, or to set `errors=`. A non-literal argv must do the same or appear
in `NON_LITERAL_ARGV_ALLOWLIST` with its reason, and an entry that stops matching fails the test.

## claude_home.py — the one derivation of this checkout's memory tree

Five call sites — `cadence_check`, `post_branch_checks`, `sync_parent`, `deploy/backup-analytics.sh`
and the Makefile — used to derive the Claude project directory independently, and every one broke
after the move to the Windows host: two carried the old Linux box's absolute home as a tracked
literal, so no environment variable could rescue them, and the other three folded `/` alone, which
leaves a `C:\Users\…` path untouched and yields a "slug" that is itself drive-absolute.

The class never raises; it fails toward absence. An unresolvable memory tree reads as absent to
every consumer: the backup script warns and records `files: 0`, `cadence_check` prints a note,
`post_branch_checks` measures the MEMORY.md cap against a file it never found, and `make status`
prints `?`. It therefore reads as "nothing to do" even on a host where the tree is present but
merely unlocated.

`slugify_path` takes text rather than a `Path`, which is the testability decision: Linux CI can then
assert the Windows rule and a Windows box the POSIX one, where a `Path` argument would resolve
against the running host and make one of the two assertions unwritable. Both separators and the
drive colon fold, so `C:\Users\User\repo\x` becomes `C--Users-User-repo-x` and `/home/kng/repo/x`
becomes `-home-kng-repo-x`.

Root selection tests `projects/<slug>`, never the config root's own existence. An earlier version
probed whether `~/.claude-personal` existed and took it if so; that directory can appear on a dev
box while both profiles are in use without ever holding this project, so every consumer read absent
against a tree that was present under `~/.claude` all along. Both roots can exist; only one holds
the tree, so root existence is a proxy and the project directory is what gets tested. Order is the
tie-break and only the tie-break — with a tree under both, the more specific `.claude-personal`
still wins. `CLAUDE_CONFIG_DIR` collapses the candidate list to one, since an explicit setting must
not be second-guessed by a probe. `claude_home()` is derived from `project_dir()` rather than
computed beside it, so the two cannot disagree about which root won. Finding no tree anywhere falls
back rather than raising, since every consumer already degrades to a printed note.

**Run:** nothing — it is a library. `tests/test_claude_home.py` pins both platform rules, the probe
order and the fallback; `tests/test_backup_local_coverage.py` pins that the shell script and the
Python consumers resolve to the same place.

## cadence_check.py — which recurring tasks are overdue

`make cadence-check` reads `docs/plans/task-marks/` — one file per task holding an ISO-8601 UTC
timestamp — and reports what is past its period. `make cadence-stamp TASK=<slug>` records a run.
Ported from the parent's `daily_check.py` task-mark block; the parent's other ~1,650 lines are
crypto-specific (signal-watch mtime, the xsmom executor, a majors cross-section) and are not taken.

Three divergences from the parent, each because the parent's reason does not hold here:

- The checker itself is tracked. The parent's lives under gitignored `docs/plans/` and its own
  docstring admits a reclone loses it — the same failure this repo fixed by inverting `.claude/` to
  a denylist, so repeating it here would reimport a defect. The marks stay gitignored: they are
  per-machine state, and `docs/plans/` is already covered wholesale by `make backup`.
- The mark's content is authoritative, not its mtime. The parent writes an ISO line and then reads
  `st_mtime`, so the content it writes has no consumer. mtime also moves for reasons that are not
  runs (an editor open, a restore that does not preserve times), and that failure direction reports
  fresher than reality. An unparseable mark here reads as overdue, never as fresh.
- Stamping is a flag rather than a shell redirect. The parent tells each skill to run
  `date -u +%FT%TZ > …/<task>`, where a typo silently writes the wrong file and a missing directory
  fails the write. `--stamp` creates the directory and refuses a slug that is not a declared task.

A missing mark reads as overdue on purpose — the fail-safe direction, since the opposite mistake
would report "fresh" for a task that has never run once.

Advisory, and must never enter `make test` or a CI job: the marks are gitignored, so a fresh clone
sees every one absent and would report every task permanently overdue. `--exit-nonzero` exists for
a human who wants a shell condition.

The four inclusion rules — rots silently, named consequence, one cheap field, exactly one action
clears it — and the reason each rejected candidate fails one (`make backup`, `make go-live`,
`/db-update`, `/ingest-feed`) live beside `TASKS` in the module, so the table cannot grow into noise
without someone stating which rule the new line satisfies.

The audit-verdict to owner join rides in the same report, ported from the parent's
`daily_check.py`. An audit whose verdict recommends action and that nothing names is a finding
with no owner: a research chain of audits has an owner at every link except the last, since each
link's owner is the next audit and the terminal recommendation is owned by nobody. It reads
`docs/audits/INDEX.md`, never the audit bodies — that index's currency is already gated by
`tests/test_docs_index.py`. Ownership means a GitHub Issue (open or closed) or a SoT row naming the
audit's filename; green is reachable two ways (do the work, or record where it was already done).
Issues are read over REST through `session_digest.fetch_issue_items`, which pages by hand because
the cloud proxy refuses both GraphQL and `--paginate`'s `repositories/{id}` links; the SoT is read
when this machine has one, and the report names the sources it read. It is not a `Task`: no mark,
no cadence — an observed-state join, hosted here because the audits are in the repo, their owners
are not, and no pytest can see both.

Three divergences from the parent's join. The predicates are re-derived against wifey's FOUND /
BOUNDED / EXCLUDED / BLOCKED taxonomy rather than the parent's BUILD / NO-EDGE; `INSUFFICIENT` is
deliberately off the settled list, because the corpus's one live SUPPRESS-CANDIDATE rides in an
"INSUFFICIENT on 11 of 12 cells" verdict, and a global veto would silently skip the row carrying a
recommendation. Rows match by date shape, not the parent's `| 2026-` prefix, which goes blind at the
new year with every row silently dropped — a 0-rows parse prints as parser drift, never as clean.
The predicates are under real pytest (`tests/test_cadence_check.py`, including the parent's
strip-the-owner mutation and an empty-SoT positive control on the committed index), whereas the
parent's module executes on import and its only proof is a hand-run sibling that duplicates the
regexes; being importable here means code and test cannot drift apart structurally. The blind
bracket (`31/41 readable, 10 state it in a table`) prints because a green line is a claim about the
readable rows only.

## backup_check.py — is the newest snapshot actually recent?

`make backup-check` runs `tools/backup_check.py`, which reads `$WIFEY_BACKUP_ROOT` (default
`~/backups/wifey`) and reports the age of the newest verified snapshot. `make backup` fails
`cadence_check.py`'s inclusion rule 4, since a scheduled `wifey-backup.timer` clears it and no human
action does — this tool is the observed-state probe that exclusion points to, because the real risk
is the timer stopping silently, and a mark cannot see that.

A mark could not do this job because alerting is failure-only (`OnFailure=` starts `wifey-alert@`),
so a timer with nothing to report and a timer that stopped firing are indistinguishable from the
Telegram side. `deploy/README.md` states the transferable rule: a scheduled job can only attest to
the step it performs, so for a green light to mean "the data is current," something has to check the
input's age rather than the copy's exit code. This reads the tree the off-site leg copies from.

The two tiers are different artifacts and are graded separately. `daily/` holds verified snapshots
each carrying `MANIFEST.json`; `weekly/` holds a format-independent parquet export and carries no
manifest at all, by design. Grading them together reports every weekly dir as a malformed snapshot —
a permanent warning about a directory that is exactly as the backup script intends — so the daily
tier alone decides the verdict and the weekly archive is reported beside it as informational against
its own 7-day bar. Pinned by `TestTiersAreDifferentArtifacts`, whose control asserts that a
manifest-less dir under `daily/` is still flagged, so the exemption cannot widen.

Four properties matter:

- The manifest's content is authoritative, not the directory's mtime. `captured_at_utc` is read from
  inside the file — mtime moves for things that are not runs (a restore that does not preserve
  times, an editor, an `rclone` round-trip) and fails in the direction that reports fresher than
  reality. The weekly tier has no manifest, so it falls back to the date stamped into the directory
  name, still a recorded decision rather than a filesystem side effect.
  `test_a_fresh_mtime_cannot_rescue_an_old_manifest` is the only test separating the two fields; a
  mutation adding an mtime fallback fails 8 tests.
- Every unreadable state reads as stale, never as fresh — a missing manifest, a corrupt one, a
  missing field and an unparseable timestamp all funnel to the same verdict, matching the off-site
  script's stance that a missing `MANIFEST.json` is a fault rather than "nothing to do."
- An absent root is its own verdict (`NO BACKUP ROOT`), not a stale one, since "the timer broke" and
  "this machine never backed up at all" want different actions.
- Advisory, and must never enter `make test`, `make sanity-checks` or a CI job — the backup root is
  machine-local single-copy state no clone has. `--exit-nonzero` opts in.

Two known holes. It measures the local tree only and cannot see whether the off-site mirror received
the snapshot, since that needs a network `rclone` call and a probe that fails when the laptop is
offline would report a backup problem for a connectivity one — the off-site leg's own success plus a
fresh source here is the two-part answer. And it refuses nothing: wiring a staleness refusal into
`deploy/backup-offsite.sh` is a candidate fix in `deploy/README.md` and stays a deliberate user call,
because a guard that costs you the backup is worse than the gap it closes.

## freshness_check.py — did the scheduled work actually run?

`make freshness-check` runs `tools/freshness_check.py`, applying `backup_check.py`'s premise — all
`wifey-*` units alert through `OnFailure=wifey-alert@%N.service`, which cannot tell a quiet timer
from a stopped one — to the two surfaces the backup probe cannot see. Ported from the parent's
`ohlcv_freshness.py`, whose motivating measurement was 22 of 25 universe symbols frozen for eleven
weeks, all `TRADING`, nothing broken and nothing watching.

### Sessions, not wall-clock

The parent computes `age_bars = (now - newest) / bar_ms`, which is correct on a 24h tape and wrong
here in the direction that reds everything forever: `4h` RTH is 2 bars/day, not 6, so a healthy
two-session-old series would read ~12 bars behind, with every Monday adding a phantom weekend. Wifey
counts NYSE sessions via `analytics/trading_calendar.py::nyse_sessions` and multiplies by
`cost_model.BARS_PER_DAY` — the single shared table, imported rather than forked, so a change there
reaches this too. `TestRthDivergenceFromParent` pins the divergence. `sessions_elapsed` floors at
zero, so a bar stamped in the future cannot read as freshness with room to spare.

### The watermark is dated but ungraded

An early build graded the `signal_state.json` watermarks against the timer's daily cadence and
printed STALE at 4 sessions on a healthy system, with `fired_at_ms` run evidence on only two of the
three intervening sessions — the third carries no row, which is not evidence of no run, since a
scan detecting nothing new writes nothing and this channel can confirm a run happened but never
that one did not. The watermark advances on dispatch, not on every run, and dispatch is intermittent
by design: `day_filter = tue_thu` suppresses on the bar's open weekday, so a Mon run (Fri bars) and a
Tue run (Mon bars) can never alert. There is deliberately no signal-side tolerance constant, since
the cadence is a consequence of `day_filter` rather than a schedule and a hand-picked constant would
red a healthy system.

Run-liveness lives on the ohlcv leg instead: a `go-live` run's first act is a watchlist sync, so
fresh watchlist bars are the evidence a run happened, and that quantity has a declared cadence. The
signal leg's only finding is no watermarks at all — a scan has never run, or the state file is
unreadable. Primary and `:wife` watermarks are dated separately because they mean different things;
a primary mark ahead of `:wife` is the normal resting state, not a fault.

### Two tiers

A series is scheduled when some declared `Cadence` covers it. Two exist: the watchlist cadence
(`wifey-signal-watch.timer`, `4h`/`1d`, one fire per trading day) and the universe cadence
(`wifey-universe-sync.timer`, `4h`/`1d`/`1wk`, one fire a week). When both cover a series the
tightest gap wins, since a watchlist name is refreshed daily whether or not the weekly timer also
touches it.

Whether the universe tier exists at all is read from the box, not assumed: `universe_timer_enabled`
asks the host's own scheduler, and `main` passes the active members to `evaluate_ohlcv` only when
that returns enabled. The units are opt-in and nothing in the repo installs them, so "the universe
has a cadence" is true on one machine and false on the next.

It dispatches per host. `host_platform.is_windows()` picks the reader: Linux shells out to
`systemctl --user is-enabled` and accepts `enabled`/`enabled-runtime`; Windows runs
`Get-ScheduledTask -TaskPath '\wifey\'` and accepts `Ready`/`Running`. `task_name_for_unit` is the
one transform between the two naming schemes (`wifey-universe-sync.timer` becomes
`wifey-universe-sync`), shared with `deploy/windows/install-tasks.ps1` and pinned by a test, because
a probe looking in the wrong folder returns "not enabled" rather than an error. On Windows the field
is `State`, never existence: `install-tasks.ps1` registers `wifey-backup-offsite` and disables it on
purpose, since `rclone sync` mirrors deletions and this host has no remote of its own yet — "the task
is there" and "the task will fire" are different answers, and only the second licenses grading a
cadence.

A genuine reader failure — no `systemctl`, no PowerShell, a timeout, an unregistered task, a
permission error — degrades to "not enabled," the direction that cannot invent faults. That
degradation is only correct while the platform in question truly cannot run the job; treat it as a
live assumption rather than a permanent one when a new scheduling mechanism becomes available.

Measured 2026-08-26, both worlds on the same tree: timer off gives 26 graded and 1,131 unscheduled;
timer on gives 1,115 graded, 42 unscheduled, 4 findings. All four were true positives (`SATS`
delisted, `EA` wound down post-acquisition). Grading unconditionally on a box with no timer would
print ~1,100 findings, and a leg that is never green stops being read.

Those four findings are no longer reachable, by design rather than regression: `EA`, `EQR` and
`SATS` were flagged `delisted` on 2026-09-02, and `read_universe_symbols` now excludes delisted
members, since `analytics_runner` resolves `--universe` through `active_symbols()` and nothing
refreshes a delisted name. The counts above describe the pre-flag tree; the graded population is now
the 502 active members.

A weekly bar cannot be graded on the daily footing. A `1wk` bar stamps on the week's Monday open and
closes Friday, so a perfectly refreshed weekly series trails a daily one by four sessions for reasons
that are not staleness. `tolerance_sessions_for` is `base + cadence gap + max(0, sessions_per_bar -
1)`, with `sessions_per_bar` the reciprocal of `cost_model.BARS_PER_DAY`, imported rather than a new
constant — without the third term every weekly series would red forever. `4h` and `1d` close inside a
session, so the term is 0 for both and their shipped tolerances are unaffected; pinned by a
regression test.

The residual absence is still a hazard: the pundit ledger has no timer at all, and a pooled
cross-section that reaches unrefreshed names mixes stale series with the fresh watchlist inside one
query — the fresh set being precisely the mega-cap tilt `4h`'s 21% coverage already carries, so
recency skew and coverage skew compound in the same direction. Nothing errors; `n_eff`, breadth
counts and any `1wk` panel are just quietly wrong.

An empty member set — an absent or unreadable watchlist, or a universe timer that is not enabled —
puts those series in the unscheduled tier, which degrades toward "nothing was graded" rather than
"everything is graded against a cadence it does not have." `universe_symbols` defaults to empty for
the same reason: a caller that has not checked the timer must not get the graded tier by accident.
Every reader (`read_watermarks`, `read_scheduled_symbols`, `read_universe_symbols`, `read_series`)
degrades the same way; `read_series` returns `None` on a lock conflict too, since a writer holding
the DB is not a finding about the data.

Advisory, never in `make test` / `make sanity-checks` / CI — both legs read machine-local
single-copy state no clone has. The pure grading half is in `make test`
(`tests/test_freshness_check.py`), positive control included: `TestScheduledTierHasTeeth` constructs
a stale scheduled series at wifey's real 2026-06-18 freeze date and asserts it is caught, plus that a
fresh one is not. `--exit-nonzero` opts in for a shell condition, and the unscheduled tier
deliberately cannot make it fire.

The ohlcv leg also lists **level breaks** (#469): `read_level_breaks` runs one window-`lag` SQL
pass over every stored series and keeps the pairs `data_quality.classify_level_break` calls
`gapped`, a series that resumed after a gap at a different level. It is network-free and reports
only the gapped kind, which has no measured false positive on analytics.db (it lists BNY `4h` on
2026-10-09). `OhlcvReport.ok` ignores it: a break is a data-identity finding, not staleness.

It also lists **unrecorded migrations** (#467): every `migrations/0*.py` with no
`schema_migrations` row, read from the record and never from the scripts' predicates (see
`.claude/context/migrations.md`). `None` means the DB was unreadable and renders nothing; it is
not a claim that every migration is recorded. Advisory like the breaks.

`collect()` is the I/O half `main` and `session_digest.py` share, and `timer_enabled(timer)` is the
generic form of `universe_timer_enabled`, so the digest can ask the same question of
`wifey-signal-watch.timer`.

## session_digest.py — what is broken, overdue and open, in one screen

`make session-digest` runs `tools/session_digest.py`, which replaces asking "what's next" at session
start. It composes the probes above rather than re-implementing them — `freshness_check.collect`,
`backup_check.evaluate`, `cadence_check.evaluate` — and adds the one question none of them asks
directly: is `wifey-signal-watch` scheduled on this box at all? The 2026-09-18 outage was exactly
that: the laptop move left no `\wifey\` tasks, the watchlist froze for 7 sessions, and
`freshness-check` said so only to whoever ran it. It also lists open GitHub Issues (via
`gh auth token --user s10023`, since the gh default account may be another) sorted p1→p3 with
untriaged last, and the handoff's `## ▶` headings. A freshness-check level break becomes an AMBER
`probable wrong-instrument series` line, so it reaches the daily Telegram digest, and an
unrecorded migration becomes an AMBER `unrecorded migration` line.

Two consumers. The `SessionStart` hook prints it into the model's context with a banner telling the
model to lead with every RED line. The `wifey-daily-check` job (09:15 UTC, after signal-watch and the
08:10 backup) runs `TELEGRAM=1` and sends to the personal channel every day, green included: the
send is the heartbeat, so a missing message means the scheduler stopped. It cannot report "the tasks
were never installed", being one of them; the hook covers that case.

Two properties are load-bearing: it exits 0 unless `EXIT_NONZERO=1` (a probe that raises becomes a
`BROKE` line, never a traceback), and a failed Issue fetch prints `BROKE could not fetch open Issues`
rather than an empty list. The pure half is tested in `tests/test_session_digest.py`.

The off-site line (#443) reads the last `=== <ts> |` block `job.sh` wrote to
`logs/wifey-backup-offsite.log`: RED `off-site backup failed` when that run did not print
`off-site backup OK` (a run under 2h old with no result is still uploading), RED
`off-site backup stale` when the last good run is over 2 days old, since a task that stops
firing writes no header at all, and AMBER when an enabled task never logged. It runs only on
Windows with the task enabled: systemd units log to the journal, not `logs/`.
`backup-check` cannot replace it, because it grades the local tree, which is how the
daily Telegram stayed green from 10-07 to 10-08 while every off-site run refused.

The core line (#423) is the survival core's state, OV-1 × VM (ruled in #429), from
`analytics/overlay/live.py`: exposure, the MA leg with sessions held and the close that would flip
it, VM's `σ̂` and weight, and the as-of close. `collect_core` reads `^GSPC` read-only. A locked or
absent DB is AMBER `core unreadable`, like the freshness probe. `core_findings` is AMBER
`core stale` when a closed NYSE session is missing, through `live.py::missing_sessions`, which the
web UI's core card shares; a bar dated today counts as closed from 21:00 UTC, the later DST close,
so a pre-open run expects yesterday's close. No watchlist carries
`^GSPC` and `go-live` never syncs it, so `TELEGRAM=1` runs `make core-sync` first, and a failed
sync never blocks the send.

## host_platform.py — which scheduler this box actually has

`tools/host_platform.py` is one predicate, `is_windows()`, wrapping `os.name` (not `sys.platform`).
It exists so the host test has a single name rather than that comparison re-spelled at each call
site, and so a test can monkeypatch one symbol instead of the interpreter's own attribute — patching
`os.name` also repoints `pathlib`, and every `Path(...)` under that patch raises.

Its consumers are `freshness_check.timer_enabled` (behind `universe_timer_enabled` and the session digest), which reads systemd on Linux and Task
Scheduler on Windows (see that section), and `venv_bootstrap`, which picks between
`.venv/Scripts/python.exe` and `.venv/bin/python` and between two swap mechanisms. A platform test
belongs behind a named predicate like this one only once the platforms genuinely differ in answer
rather than in availability — treating "not Linux" as a failure that degrades to "not enabled" is
correct only while no non-Linux box can run the job at all, and becomes silently wrong the moment a
Windows scheduled task exists.

## venv_bootstrap.py — a hand-run script that degrades instead of failing

`reexec_into_venv(root)` replaces the current process with the same argv under `root/.venv`. It
returns normally — never raises, never exits — when the sentinel is already set (so a broken venv
cannot loop), when the venv is absent (a fresh clone, CI, `make preflight`'s clone), or when already
inside it, which is what makes it safe to call unconditionally.

The "already inside it" test must be `sys.prefix`, never `sys.executable`: on POSIX
`.venv/bin/python` is a symlink to the system interpreter, so comparing resolved executables reports
the venv and a bare `python3` as the same path and the swap never fires.

Only a script that keeps going and renders something that looks like an answer needs this bootstrap
— the discriminator is whether a wrong interpreter fails loudly. Measured 2026-09-22:
`python tools/sanity_checks.py` under the wrong interpreter exits 0 printing `0 finding(s)` with
three of eight legs reading `SKIPPED  (project dependencies are not installed)`, the same words the
legs that skip legitimately use, so the wrong interpreter is invisible inside a healthy-looking
report. `freshness_check.py` dies on an immediate `ModuleNotFoundError` traceback and so does not
need the bootstrap; `cadence_check.py`, `backup_check.py`, `post_branch_checks.py` and
`orphan_test_audit.py` import nothing third-party and cannot degrade at all. Its one call site is
scoped to `__main__`, because `tests/test_sanity_checks.py` imports that module and a swap at import
time would fire mid-collection.

Two divergences from the parent, both measured, since a verbatim copy is worse than no port here.
The interpreter is `Scripts/python.exe`; the parent probes `bin/python` and returns when it is
absent, which on this box is a permanent silent no-op. And `os.exec*` does not work on this host:
`os.execve` with an env dict segfaults (exit 139), and `os.execv` with an absolute path exits 0
having run nothing the caller can see — child orphaned, stdout never reaching the console, exit code
lost — identically from `cmd.exe`, so not an MSYS artifact. Windows therefore swaps via
`subprocess.run` and raises `SystemExit` carrying the child's code; POSIX keeps the parent's
`os.execve`.

`_venv_first_path` prepends the venv's script directory to `PATH`, because each fix covers only the
resolver it names — `sys.path` for repo imports, the interpreter for third-party imports, `PATH` for
subprocesses. wifey's one bootstrapped script shells out only to `git`, so this half has no current
consumer here but ships because landing the interpreter swap alone would leave a documented defect
one level down. Prepended rather than appended so a stale system copy cannot shadow a pinned one.

The end-to-end test must derive the foreign interpreter from `sys.base_prefix` rather than
`shutil.which("python3")`, which under `poetry run` resolves to the venv itself and produces a
green-by-skip result. It carries a negative control asserting the degraded run is observable;
without that control a pass is satisfied by the swap working or by never reaching a degraded run.

## worktree_venv.py — which venv `make` uses from a linked worktree

A `.claude/worktrees/` checkout holds tracked files only, so it has no `.venv`, and Poetry keys its
cache env on the project directory: `poetry run` there creates an empty env and every gate dies on
`ModuleNotFoundError` (#453). Poetry adopts an exported `VIRTUAL_ENV`, so the Makefile runs this
helper at parse time and exports what it prints, the main checkout's `.venv`. It prints nothing
outside a linked worktree (`--git-dir` equals `--git-common-dir`), when the worktree has its own
`.venv`, or when the main venv has no interpreter (layout from `venv_bootstrap._venv_python`). The
Makefile skips it when `VIRTUAL_ENV` is already set or `.venv` exists. `make preflight` inherits the
borrowed value and `subprocess_env` drops it, as it does a caller-set one (#449), so the clone
installs its own venv; the recipe must not unset it through `env`, which cannot exec the Store
`python3` alias (#461). It
resolves the common dir itself because `git rev-parse --path-format=absolute` needs git 2.31 and
this host runs 2.28. A bare `poetry run` outside `make` still lands in the empty env.

## clone_preflight.py — does the suite pass on a machine that is not this one?

`make preflight` clones HEAD into a temp dir, runs `poetry install --no-root` against the clone's
own lock, and runs `make test`'s exact argv there. Ported from the parent.

It replaces that branch's `make test` rather than adding to it: `PYTEST_ARGS` mirrors the `test:`
recipe argument-for-argument, and `TestWiredIntoTheWorkflow` pins that against the Makefile so the
claim has an external referent.

What it catches that no local run can: a gitignored path that exists on this box and nowhere else is
invisible to every check that runs here — `config/stocks.json`, `.claude/sensitive-terms.txt`,
`docs/plans/` and `analytics.db` are all absent on a clean clone. Two defects share that symptom: a
test can depend on a local file, so CI reds; or production code can load a local file it does not
need, so the CLI is broken on a clean clone while a hermetic test passes anyway.

CI is already this gate, being a clean checkout; what this closes is timing, not detection — on a
private repo, detection after a push costs a metered Actions cycle, a red PR, and a visibility flip
to read the failure at all.

The dirty-tree refusal is the load-bearing part. A clone sees committed state only, so a run against
an uncommitted tree tests stale HEAD and reports green — the same invisible pass the gate exists to
kill. It refuses before taking any clone, and `test_refuses_before_taking_any_clone` asserts the
clone's absence rather than only the exit code. That is also why it belongs in `/post-branch` phase
5, after the doc commits, rather than phase 1's sweep.

Two cheaper alternatives are structurally wrong and are not used: a foreign working directory makes
every relative path absent at once, committed assets included; monkeypatching the `DEFAULT_*`
constants is partial by construction, since `DEFAULT_DB_PATH` is re-exported into `analytics.store`
and `analytics.data_store`, which capture it at import.

Two known holes: it cannot see an absolute default (`$HOME/…`), because `$HOME` is identical in the
clone — `EXTERNAL_ROOTS` in `deploy/backup-analytics.sh` is exactly that shape — and it only reaches
a production-code-loads-a-local-file defect where a test exercises the path; neither mechanism sees
an untested CLI branch.

The clone's interpreter is pinned before the install: when preflight runs from a virtualenv (as
`make preflight` does), `seed_venv_argv` creates `<clone>/.venv` on that same python, and Poetry
adopts it. Left alone, Poetry builds the venv on whatever python Poetry runs under, which on the
cloud host is 3.11 against the 3.13 floor (#397, ported from parent #880). Adoption holds only
while no other venv is active: Poetry prefers `VIRTUAL_ENV`, then `CONDA_PREFIX`, over the
in-project `.venv`, so `subprocess_env` drops both from the clone's environment (#449). The
interpreter probe stays as the backstop: a clone whose `poetry run` still cannot start Python reports INFRA, never a
red suite.

Exit codes: `0` pass, `1` the suite failed (a real finding), `2` REFUSED (dirty tree), `3` INFRA (the
clone, venv seed, install or interpreter probe died). `make` collapses all of them to its own 2, so branch on the printed banner or
call the module directly.

First run, 2026-08-20: passed, 3,123 passed / 4 skipped in the clone against 3,124 / 3 locally, so
the suite is clean-clone-safe. The one-test delta is the finding:
`test_pundit_score.py::test_live_ledger_rows_all_survive_the_new_guards` guards
`docs/plans/pundit-calls.jsonl`, which is gitignored, so it runs only on the operator's box and its
assertion has never been evaluated by CI and never can be — by design, since the code comments this
explicitly (`# gitignored; absent on a fresh clone`), but the asymmetry (silently passes locally,
skips in CI, neither surface
reporting that it ran nowhere meaningful) is exactly the shape this gate exists to expose. Suite
portion 295s in the clone against 254s locally (+16%), before the clone and `poetry install`.

**Run:** `make preflight`, or `python3 tools/clone_preflight.py [--repo R] [--dest D] [--dry-run]`.
Bare `python3` on purpose — stdlib-only, so the gate still runs when the dev venv is the thing that
is broken.

## sanity_checks.py — every mechanical `/sanity-check` check, in one run

Eight checks: `fork-drift` (invocable artifacts a doc names but the code lacks — make targets,
timeframes, `--strategy`, `SYMBOL`), `parent-leakage`, `missing-paths`, `context-coverage`,
`router-wiring`, `config-strategies`, `cli-documented`, `regression-surface`.

Three of those degrade to `SKIPPED  (project dependencies are not installed)` when run under the
wrong interpreter, which is why `__main__` re-execs into the venv (see `venv_bootstrap.py` above):
`_load_code_facts` swallows any import failure for CI portability, and that same property makes a
wrong-interpreter run print exit 0, `0 finding(s)`, with three legs quietly not run.

`regression-surface` reads the globs CI's regression paths-filter fires on straight out of
`.github/workflows/lint.yaml` and asserts CLAUDE.md names each one. It keys on the block mentioning
`tests/test_regression.py`, never on a job name or position, because a second `filters:` block
exists in that file (the frontend one) and a positional read would silently grade the wrong one. An
empty filter is a finding, not a pass: if the workflow moves, the leg must say it can no longer see
what it grades rather than reporting clean against nothing. The comparison is verbatim rather than
paraphrased, because a paraphrase (`analytics/backtest/` for `analytics/**/*.py`) is not diffable by
any tool, and this doc's own trigger list has previously diverged from CI's filter in both
directions — narrower on `analytics/` and `config/`, wider on `tests/fixtures/`. Only the narrowing
direction is harmful: over-running the gate costs ~8s, under-running it costs a metered Actions
cycle. Ported from the parent. Same shape as `post_branch_checks.py` —
pure functions over text, git injected as `runner`, one `Finding` per thing a human must look at —
with two deliberate differences.

It gates rather than advises, and runs in two CI places. `tests/test_sanity_checks.py` asserts the
working tree is clean, which puts it inside `make test`; CI's `markdownlint` job also runs it
unconditionally, because the test job sits behind a `**/*.py` paths filter and would never fire on a
docs-only PR — exactly the change these checks guard.

Every leg is CI-portable, which is what surfaced two defects in the earlier prose form: a shell block
that read the gitignored `config/stocks.json` directly would have crashed in a clean checkout, and a
`MISSING` allowlist calibrated on a developer machine where `config/youtube_channels.toml` happens to
exist. `check_missing_paths` asks git whether a path is expected to be absent; the watchlist leg
degrades to a printed note, and the three legs needing project imports report `SKIPPED` where
nothing is installed. A degraded leg is a note, never a finding — counting it would leave the sweep
permanently red in CI.

`parent-leakage` is scoped to `.claude/` while its siblings are not, and that asymmetry is
load-bearing: widening it to CLAUDE.md, README or `docs/system-overview.md` returns hits that are all
correct history (the fork-lineage paragraph, the sister-memory pointer, the README's "forked from"
line). A skill instructs an action, so a parent artifact named there is invocable rather than
historical, and only that surface is worth flagging. Pinned by `test_scope_stops_at_dot_claude`.

Extraction found three defects in the inherited code, none by reading:

- The skill's documented expectations were stale in two of three legs — it claimed "no leakage hits"
  and "exactly these ten `MISSING` paths," where the real numbers were 3 and 9, and all three leakage
  hits were legitimate. A check that reports known-good noise gets skimmed.
- The symbol pattern capped at `[A-Z]{2,6}`, so a 7-character `BTCUSDT` was reported as
  `symbol=BTCUSD` — a finding naming a string that appears nowhere.
- `cli/main.py` built its argparse tree inside `main()`, so the CLI surface could not be read
  without being run. Extracting `build_parser` was a prerequisite, and the check immediately found
  `wifey param-audit` documented nowhere in README.

Every allowlist entry carries its reason inline; an entry without one lets a check decay into a
no-op.

**Run:** `make sanity-checks`, or
`PYTHONPATH=. poetry run python tools/sanity_checks.py [--check NAME] [--exit-zero]`. It runs
stdlib-only too (`python3 tools/sanity_checks.py`), which is how the CI step works.

## docs_index.py — generated audit + spec indexes

Generates `docs/audits/INDEX.md` (18 verdicts) and `docs/superpowers/specs/INDEX.md` (14 specs) — 32
documents that nothing else indexes; CLAUDE.md cites only 9 of the 18 audits inline. Ported from the
parent. `tests/test_docs_index.py` regenerates both and compares byte-for-byte, so a new audit or
spec fails CI until it is indexed. Output is deterministic (no generated-at timestamp), so `--check`
can compare bytes.

Nothing is guessed. Date and title come from the filename prefix and the H1 (100% reliable across
both corpora). A verdict line is emitted only where it reads as prose under a Verdict heading or in
the inline `**Verdict: …**` form — 8 of 18 here — and every other row gets an em dash, with the index
header stating that coverage. Table rows, blockquotes, list items and `**Date:**` metadata lines each
have a named negative test.

### Two divergences from the parent

A wrapped verdict is read as a whole paragraph in both the heading and inline forms. The parent
joined paragraphs under a Verdict heading but left the inline form reading a single line; this
corpus hard-wraps at ~80 columns, so on a mid-sentence cut a fragment can be indistinguishable from a
complete, shorter verdict — one case reversed an audit's finding by dropping its qualifying clause.
The inline pattern also consumes a `=` separator (`**Verdict = FAIL.**`), which would otherwise leak
into the cell as a leading `= FAIL`. The same defect is live in the parent (2 of its 4 inline
verdicts); raise it there rather than treating it as fixed by this port.

The join needs a stop rule: several audits open with `**Date:** / **Verdict:** / **Audit:** /
**Spec:**` on consecutive lines with no blank between, which markdown treats as one paragraph, so
joining onward merges the Audit field into the verdict and turns a clean one-line verdict into run-on
text crediting a make target. `_continues_paragraph` stops at a bold field label (`_BOLD_FIELD`) as
well as at a heading, table, blockquote or list, and both extraction paths share it. Stopping early
costs a few words; not stopping fabricates a verdict out of adjacent metadata.

The reconcile column states its own floor. `0 of N` renders identically whether the detector found
nothing or could never fire, and those are different facts — this corpus has zero spec-reconcile
audits, so `spec_reconcile_audits()` is read separately from the per-spec join and the page says "no
reconcile has been written up" rather than publishing a bare `0 of 14` that reads as a claim the data
cannot support (the same class as the `exits/` MFE median: check the floor is reachable before
quoting the number). A spec counts as reconciled only when an audit naming its filename says so in
its own filename or H1 — keying on "reconcile" anywhere in the body mislabeled a doc reconciling two
findings, and that case is a regression test here.

**Run:** `make docs-index` (write) / `make docs-index-check` (verify, writes nothing), or
`poetry run python tools/docs_index.py [--check] [--audit-dir PATH] [--spec-dir PATH]`

## distil_power.py — price a hypothesis BEFORE it is written into the inbox

The G3 gate of `/research-distil`. Prints the effect size the gate demands at the declared `n` and
trial family, so a claim is priced rather than estimated. Ported verbatim from the parent, with only
the module docstring's precedent re-flavored; `tests/test_distil_power.py` +
`tests/test_research_guards_power.py` (48 cases) passed here with zero code adaptation, because
`dsr.py` and `psr.py` are byte-identical across the two repos.

It cannot price a hypothesis that is not Sharpe-shaped, and there is no error for that case.
`--units` is `per_trade | per_alert | per_book_day`, and the whole model asks what Sharpe clears the
DSR gate — a calendar-conditioning claim (a seasonal or political-cycle effect, whose unit is a year
and which has no book, no trades and no Sharpe) has no honest way through it. Passing one of the
three existing units to get a number out is exactly the defect the module docstring exists to
prevent: a figure that looks portable and silently changes meaning with the panel. Use the
two-sample MDE instead — `(z_0.975 + z_0.80) · sd · sqrt(1/n1 + 1/n2)` with `sd` measured from the
panel — and say so explicitly. Worked example, both halves:
`docs/audits/2026-08-20-h001-h002-midterm-cycle-power-precheck.md`. The `--bar`/`--sd` legs are still
usable on their own for the `powered_null` containment question; it is only the Sharpe half that
does not port.

```bash
PYTHONPATH=. poetry run python tools/distil_power.py \
  --units {per_trade|per_alert|per_book_day} \
  --sr-footing {per_obs|annual} [--periods-per-year P] \
  --n-obs N --n-trials K --sr-variance V \
  [--n-series S --n-eff E] [--sd SD] [--bar R] [--corpus-best C]
```

A bare invocation without `PYTHONPATH=.` works since #436 (see `route_dedup.py` below for the
bootstrap). Exit 2 on a declared-error argument combination.

Trial count dominates n, and by a wide margin. Re-derived against wifey's own `required_sharpe`:
holding the trial family at 20, a 21x range of n (100 to 2,100) moves the bar 1.17x; holding n at the
live ledger's 267, going from 1 to 320 trials moves it 15.9x (0.101 to 1.611). A skill that reads
three books and emits forty hypotheses inflates the trial family until every cell is unreachable,
including ones that would have passed alone. Reproduce:

```python
from analytics.research_guards import required_sharpe
required_sharpe(100, n_trials=20, sr_variance=0.25) / required_sharpe(2100, n_trials=20, sr_variance=0.25)
required_sharpe(267, n_trials=320, sr_variance=0.25) / required_sharpe(267, n_trials=1, sr_variance=0.25)
```

Four flags carry the traps. `--sr-footing` is mandatory because `research_guards.psr` is
per-observation while every filed sleeve Sharpe is annualized: the H-023/H-024 recipe fed the
annualized trial variance (0.0652) and corpus best (0.41) beside `n_obs` in sessions, and the tool
printed a 0.3047 per-day bar — 4.84 annualized — as REACHABLE against 0.41; the consistent bar is
0.83. `annual` requires `--periods-per-year`, divides `--sr-variance` by it, reads `--corpus-best`
and `--bar` as annualized Sharpes (so `--sd` is refused) and prints both footings. On
`per_book_day`, whose year is fixed at 252, a `per_obs` declaration implying an annualized
dispersion or corpus best above 3 is refused; `per_trade` and `per_alert` have no fixed year and no
such check. Audit: `docs/audits/2026-09-27-distil-power-units-retraction.md`. `--units` is mandatory with no default, because a figure that looks
portable silently changes meaning with the panel — `regime.py` carried crypto bar counts across the
fork, so its "90-day" ATR window really spanned ~270 sessions on `4h` (RTH is 2 bars/day, not 6) and
12.02% of `4h` labels moved when it was corrected. `--n-series`/`--n-eff` must be supplied together —
one alone raises, and omitting both on a pooled multi-symbol panel overstates `n`, printing an
undeflated upper bound rather than the true bar; never present such a pass as having margin it did
not measure. The repo has a measured `n_eff` (`make wifey-n-eff`, ~2.96 at `1d` on the 505-member
universe, 2026-08-20), so there is no reason to run undeflated.

On the `per_obs` footing, `--corpus-best` without `--sd` is not a pass. The comparison converts the required Sharpe into effect
units, which needs `--sd`; without it, the tool must name the missing input rather than falling
through to the same bare `VERDICT REACHABLE` a cleared bar prints, since `/research-distil`'s G3 gate
mandates running this tool and "did not compare" must not read as "passed." Ported from the parent.
Treat a `REACHABLE` verdict as a claim to check rather than a result to quote.

`UNREACHABLE` is a successful output, not a failure: more data of that shape cannot fix it, only a
smaller trial family can. The null-containment verdict is delegated to
`analytics.audit_guard.powered_null` and is never restated in the tool.

## n_eff.py — how many INDEPENDENT series a pooled panel actually carries

`make wifey-n-eff` (`ARGS="--source universe --timeframe 1d"`). Wraps
`analytics/research_guards/correlation.py::effective_independent_series`, ported from the parent's
`analytics/forecast/attribution.py`. Under an equicorrelation approximation with mean pairwise
correlation `rho`, `k` series carry the noise reduction of only `n_eff = k / (1 + (k-1)·rho)`, so a
naive pooled t-stat is inflated by `sqrt(k / n_eff)`.

It exists because `distil_power.py` accepts `--n-series` / `--n-eff` and deflates by them but has no
way to measure the second on its own — `effective_n` returns `n_obs` undeflated when both are
omitted, so every power calculation before this tool existed was either undeflated or used a
borrowed figure. H-001/H-002 did not go through this tool: `distil_power` cannot price a
calendar-cycle claim, so they used a two-sample MDE, where a pooled `sd` carries the same correlation
problem.

### Measured 2026-08-20 (first run)

| Panel | k | mean rho | `n_eff` | t inflation |
| --- | --- | --- | --- | --- |
| universe `1d` | 504 | +0.3365 | 2.96 | 13.05x |
| universe `1wk` | 503 | +0.3459 | 2.88 | 13.22x |
| universe `4h` | 105 (21%, size-tilted) | +0.1958 | 4.92 | 4.62x |
| watchlist `1d` | 13 | +0.5189 | 1.80 | 2.69x |

The 505-member universe is worth about three independent bets, not 505. `n_eff` approaches `1/rho`
as `k` grows (1/0.3365 = 2.97 against a measured 2.96), so adding names buys almost nothing once `k`
is large — breadth is capped by the correlation, not the roster. Going from 13 to 504 names is 39x
the symbols for 1.6x the `n_eff`.

The parent's `n_eff` of 2.92 does not transfer: that figure is 25 crypto perps at rho 0.315, and it
moves on its own panel (14 series gives 1.97, three gives 1.42). The near-agreement with wifey's 2.96
is the `1/rho` asymptote, not portability.

### Two things the tool refuses to do

- It withholds the `distil_power` flags when `measured` is False. An unmeasurable panel and an
  uncorrelated one both carry a deflator of 1.0; emitting flags for the first would launder "could
  not tell" into "no correction needed" — the same distinction as `audit_guard`'s `INSUFFICIENT` vs
  `powered_null`. Exit code 1, and the banner says so.
- It reports coverage every run rather than assuming it. `4h` reaches 105 of 505 and that subset is
  size-tilted, so a deflator measured there describes large caps only.

### The pivot trap

An early implementation pivoted every symbol onto one union `open_time` index and called
`pct_change` across it. When symbols sit on different stamp grids, consecutive union rows belong to
different symbols, so almost every return goes NaN — measured against the live DB at `1wk`, this
dropped 505 of 505 symbols that a per-entity implementation keeps (503 of 505). It failed in the safe
direction (a refusal, not a wrong number), and `1d`'s grids happen to align, so that timeframe's
headline of 2.96 is identical before and after the fix. Compute a per-entity series on its own index;
alignment is the correlation step's job, not the return step's. Pinned by
`tests/test_n_eff_tool.py::TestLoadReturns::test_misaligned_stamp_grids_do_not_null_the_panel`, which
gives two symbols zero index overlap.

`config/stocks.json` is keyed by symbol but also carries N1's `universe_policy` block, so a bare
`sorted(json.load(f))` returns it as a 14th ticker. Go through
`utils.config_validation.load_stocks_config`, which pops it.

## live_outcomes_report.py — read-only signal_alert_outcomes spot-check

Read-only spot-check of `signal_alert_outcomes` after the T2 backfill worker runs; reports the
resolved/open mix, per-(strategy, tf, direction) win rate + avg_r, and per-strategy aggregate.
Stop-gap until a Stats UI card lands.

**Run:** `PYTHONPATH=. poetry run python tools/live_outcomes_report.py [--days N] [--min-n N]`

## forecast_audit.py — G2 audit for the EWMAC trend sleeve

Read-only **G2 audit** for the `analytics/forecast/` EWMAC trend sleeve (PR #91): portfolio
Sharpe/Sortino/max-DD with DSR/PBO/boot-CI/MinTRL stamps, a cost-sensitivity sweep
(0/2/8/16 bps), a breadth (universe vs majors) contrast, the per-speed Sharpe table, and a
`--weight-study` mode. Universe from `load_research_universe().stocks()`; majors default
`AAPL,MSFT,NVDA,AMZN,GOOGL,META`. `build_g2_report_row` is the testable unit.

**Run:** `make wifey-forecast-audit` or
`PYTHONPATH=. poetry run python tools/forecast_audit.py [--majors …] [--weight-study]`

## xsmom_audit.py — G3 audit for the cross-sectional momentum sleeve

Read-only **G3 audit** for the `analytics/xsmom/` cross-sectional momentum sleeve (PR #92):
breadth contrast (universe vs majors), dollar-neutral gate, beta-attribution table
(equal-weight market + SPY proxy, loaded separately so the ETF never enters the demean),
forward-persistence table, cost-sensitivity sweep (0/2/8/16 bps), per-speed XS Sharpe, each with
DSR/PBO/boot-CI/MinTRL + `corr_to_trend`. Universe from `load_research_universe().stocks()`;
majors default `AAPL,MSFT,NVDA,AMZN,GOOGL,META`; `--market-proxy` default `SPY`.
`build_xs_report_row` is the testable unit.

**Run:** `make wifey-xsmom-audit` or
`PYTHONPATH=. poetry run python tools/xsmom_audit.py [--majors …] [--market-proxy SPY]`

## xsmom_residual_audit.py — experiment #1 audit for residualized XS-momentum

Read-only **experiment #1** audit (PR #98) for the residualized XS-momentum sleeve
(`analytics/xsmom/residual.py`): runs the pre-registered `{mega,broad}×{raw,residual+skip}` 2×2
over the breadth universe (1d) at 0/2/8 bps, prints each cell's headline + DSR/PBO/boot-CI/
MinTRL/corr_to_trend, the long-only top-quintile leg Sharpe, and the PASS/FAIL on the committed
`broad_residual_skip` cell. Mega arm = `config/universe_sp100_snapshot.json` ∩ active universe.
`build_grid`/`long_only_sharpe` are the testable units. **Verdict = FAIL**
(`docs/audits/2026-06-21-experiment-1-residual-xsmom.md`).

**Run:** `make wifey-xsmom-residual-audit` or
`PYTHONPATH=. poetry run python tools/xsmom_residual_audit.py [--slippage-bps N]`

## lowvol_audit.py — edge-hunt #2 audit for the low-beta/BAB sleeve

Read-only **edge-hunt #2** audit (PR #100) for the `analytics/lowvol/` low-beta/BAB sleeve: runs
the pre-registered `{beta,vol}×{beta-neutral L/S, long-only quintile}` 2×2 over the breadth
universe (1d) at 0/2/8 bps, prints each cell's headline + DSR/PBO/boot-CI/MinTRL + the
realized-portfolio-beta diagnostic + alpha t-stat, and the PASS/FAIL (+ deploy-grade flag) on
the committed `beta_neutral_ls` cell. `build_grid`/`_grid_frame` are the testable units.
**Verdict = FAIL** (`docs/audits/2026-06-22-edge-hunt-2-lowvol-bab.md`).

**Run:** `make wifey-lowvol-audit` or
`PYTHONPATH=. poetry run python tools/lowvol_audit.py [--slippage-bps N]`

## xasset_audit.py — edge-hunt #3 audit for the cross-asset TSMOM sleeve

Read-only **edge-hunt #3** audit (PR #102) for the `analytics/xasset/` cross-asset TSMOM sleeve:
runs the pre-registered `{broad,commodity}×{long-short,long-flat}` 2×2 over the frozen 13-ETF
basket (1d) at 0/2/8 bps, prints each cell's headline + DSR/PBO/boot-CI/MinTRL + the realized
equity-β to SPY, and the PASS/FAIL (+ deploy-grade flag) on the committed `broad_ls` cell.
`build_grid`/`_grid_frame` are the testable units. Backfill the basket first with
`make wifey-xasset-backfill` (13 ETFs, 1d from 2007-03-01). **Verdict = FAIL (clean — equity-β
guardrail held)** (`docs/audits/2026-06-23-edge-hunt-3-cross-asset-tsmom.md`).

**Run:** `make wifey-xasset-audit` or
`PYTHONPATH=. poetry run python tools/xasset_audit.py [--slippage-bps N]`

## pead_backfill.py — one-shot EDGAR earnings ingestion

One-shot EDGAR earnings ingestion (edge-hunt #4, PR #104): loops the breadth universe, resolves
CIKs, pulls `companyfacts` + `submissions`, matches each quarter to its 8-K item-2.02
announcement (10-Q `filed` fallback), upserts `earnings_facts`. Pure unit = `build_rows`;
network in `main`.

**Run:** `make wifey-pead-backfill` (or `tools/pead_backfill.py [--limit N] [--db PATH]`)

## insider_backfill.py — H-024 phase 1, EDGAR Form 4 ingestion

One-shot ingest for the **first non-price sleeve** (design and frozen pre-registration:
`docs/superpowers/specs/2026-08-29-h024-insider-routine-opportunistic-design.md`). Loops the
research universe, walks each CIK's Form 4 index, fetches each filing's ownership XML, upserts
`insider_transactions`. Pure units = `build_rows` / `collect_filings`; network in `main`.

`filings.recent` is a window, not a full history: SEC documents it as the most recent 1,000
filings, confirmed on one company (AAPL, 2026-08-29: exactly 1,000 entries reaching back only to
2015-06-10, with one shard covering 1994-2015). `collect_filings` also walks every `filings.files`
shard whose `filingTo` lands on or after the window start (skipping the rest keeps a full run off
the 1990s), since a fetcher reading `recent` alone returns a truncated history that reads exactly
like a quiet insider.

`primaryDocument` points at the XSL-rendered HTML (`xslF345X06/form4.xml`); the machine-readable
XML is the bare filename under the same accession. `raw_document_name` is that one-line strip —
fetching the prefixed path yields a document carrying no ownership elements.

The window starts 3 years before the study (`--since 2015-01-01` by default): the classifier needs
a trade in each of three preceding years, so a run covering only 2018 onward leaves every insider
unclassifiable.

Requires `EDGAR_CONTACT_EMAIL` (see the `edgar_client` note below). The run's last line is the
phase-1 acceptance observable: parse coverage against its 80% floor, printed PASS/FAIL. A
footnote-only price counts as a failure, not a drop, while a derivative-only filing counts as
clean-but-empty — conflating those two would let a parser report 100% coverage by construction.

**Run:** `make wifey-insider-backfill` (or
`tools/insider_backfill.py [--since ISO] [--stride N] [--limit N] [--symbols A,B]
[--max-filings-per-symbol N] [--db PATH] [--resume] [--max-consecutive-failures N]`)

A long run is interrupt-safe only under `--resume`. The per-filing and per-symbol handlers both
warn and continue, so a dropped connection never crashes a run — it permanently skips those
filings while still printing a done banner and a coverage figure that looks plausible but has
holes. `--resume` skips only symbols carrying a completion marker in `insider_backfill_progress`,
written after the upsert and only when the symbol had zero fetch errors, so an interrupted or
partially-failed symbol is retried in full. Without the flag a restart re-walks from symbol 1
(safe via the upsert, just wasteful).

The marker key carries `since`, and parse failures deliberately do not deny one. A marker attests
to the window it covered, so widening `--since` correctly re-runs everything rather than reading
the old completion as coverage. A code-M option exercise carries no price and reports a parse
failure on every run, so counting those as errors would deny the symbol a marker forever and
re-fetch it on each pass — only fetch errors block a marker.

A sustained outage aborts after `--max-consecutive-failures` symbols (default 5) rather than
walking the universe recording empty results; re-run with `--resume`, nothing is lost.

`--limit` alone takes the head, and `config/universe.json` is grouped by sector, so `--limit 15`
is fifteen Information Technology mega-caps, not a sample. Measured 2026-09-01, that group carries
18,549 Form 4 documents (CRM 4,175 + ACN 2,922, ~38%), making the head simultaneously the slowest
slice in the universe and the least informative, since parse failures concentrate among small and
older filers it contains none of. Pair it with `--stride`, which spreads the pick across sectors
(measured: 7 sectors vs 1).

`--max-filings-per-symbol` spreads across each symbol's range and must not become a head cap:
`collect_filings` returns newest-first and recent filings are the most uniform, so taking the
first N measures the easy end and biases coverage optimistically — worse than no cap, since the
floor exists to catch exactly the documents it would drop. Same defect as the sector one, one
level down: sectors for symbols, filing vintage for documents. Both pinned by
`tests/test_insider_backfill.py`; the sampling rationale is spec Amendment 1.

Runtime is round-trip bound, not throttle bound: ~2.1 documents/second measured against
`www.sec.gov/Archives` (the 0.12s `_MIN_INTERVAL` is not the binding constraint). The full
501-stock backfill is 20-40 hours, an overnight job. A coverage pilot wants breadth, not depth:
`--stride 10 --limit 50 --max-filings-per-symbol 40` gives ~2,000 documents across 50 companies in
every sector, ~16 min.

## insider_cohort.py — H-024 phase 2, routine/opportunistic cohort shape

Reads `insider_transactions`, applies `analytics/insider/classify.py` and prints the split.
**It computes no return and has no access to a price** — the pre-registration puts the first look
at one inside phase 3's gated report.

**Run:** `make wifey-insider-cohort`, wrapping `tools/insider_cohort.py`
(`[--db PATH] [--symbols A,B] [--since YEAR]`). Read-only; no network, no
`EDGAR_CONTACT_EMAIL`.

Prints the split in three units that disagree by design: rows, trade-days and insiders. A tranched
sale is one trade-day and several rows, so a split quoted without its unit compares to nothing;
the working paper's ~55% is a trade share, which makes "routine share of classified rows" the only
comparable line. The share is over classified rows: folding unclassifiable insiders into
"opportunistic" would inflate that arm with insiders the rule never examined.

The phase-1 sample cannot answer this question, which is the tool's main finding to date.
`--max-filings-per-symbol 40` is right for the coverage observable, whose validity comes from
filer diversity, and wrong here, because the classifier needs a per-insider calendar and thinning
a company's filings thins every one of its insiders'. Measured 2026-09-03: the capped draw
classifies 3.5% of rows with 0 routine across 47 companies, against 46.0% on the 6 uncapped names,
which supply 97.7% of every classified row in the table. The cap biases the label, not just the
count — a thinned calendar cannot exhibit a same-month streak, so everything it does classify
falls to opportunistic. A sample designed for one observable is not a sample for another, and
nothing in the stored data announces that the unit changed from document to insider-year.

Do not quote the 74.0% routine share as a panel figure: it is six technology mega-caps, and the
10b5-1-era explanation for its gap to the working paper's 55% is a hypothesis nothing here has
tested.

The observability-divergence block (printed unconditionally, no flag) is a probe, not an
alternative rule: it re-runs the frozen classifier against only filings public on 1 January and
measured 1 of 6,981 (insider, year) labels (0.0%), which licenses the frozen trade-date reading
rather than merely assuming it. Spec Amendment 3.

## insider_audit.py — H-024 phase 3, the gated trial family

Runs the four frozen trials and their four routine-arm placebos over the research universe (`1d`)
and prints the cohort split, each cell's headline with DSR / PBO / boot-CI, the realized equity-β
to SPY, the paired trial-minus-placebo reversal test, and PASS/FAIL on the pre-committed `T1`.
Read-only; no writes, no network, no `EDGAR_CONTACT_EMAIL`.

**Run:** `make wifey-insider-audit`, wrapping `tools/insider_audit.py`
(`[--db PATH] [--symbols A,B]`).

This is the row's first look at a return. Ingestion, the coverage observable, the classifier and
the cohort shape were all built and reported without one, which makes this output a test rather
than a search, so everything it prints is reportable as-is, including a null.

Books are priced gross and net side by side, because the two verdicts read differently: a sleeve
that is negative before costs is a signal failure, and one that is positive gross and negative net
is a cost failure. The gross column is the live cost model with every charge zeroed rather than a
second model, so nothing but the prices differs between the two runs.

The DSR family is the four trials, never the eight books. Placebos are controls; deflating against
eight would silently raise the bar the sleeve was pre-registered to clear. Pinned by
`tests/test_insider_report.py::TestDsrFamilyIsTheFourTrials`, whose control drops a real trial and
asserts the DSR does move — a family-size test that only asserted invariance would pass on a report
that ignored the family entirely.

The reversal observable prints three states. A pair reads `measurable` from the books, not from
their difference: two never-funded books produce an all-zero difference and a CI of `[0, 0]`,
which satisfies "indistinguishable" while establishing nothing (`audit_guard`'s lesson that
`INSUFFICIENT` and a powered null are different verdicts, and collapsing them prints the confident
one). An all-zero difference between two funded books is a real null and is reported as one.

Weights are trailing dollar ADV, not market cap: there is no market-cap series in this repo, so
spec Amendment 4 rules the substitute in. It is a liquidity weight, so the book tilts to
high-turnover names, and any comparison to the working paper's 82 bps/mo carries that tilt.

Do not run it on a symbol subset to get "a result." `--symbols` exists for reproducing a recorded
draw, not for sampling — a non-pre-registered draw is the error Amendments 1 and 3 were written
about, and it spends the first look at a return on a panel that cannot be the registered one.

It needs the full uncapped backfill (`insider_backfill_progress` certifies 497 symbols over
779,914 rows as of 2026-09-19), and it needs real memory: the panel is ~2,190 sessions x 498 names,
and the run has died on a box whose kernel paged pool leaked to 42.8 GiB of a 15.4 GiB machine. A
gate run under memory pressure is uninterpretable, since an OOM and a real failure look identical.

## overlay_audit.py — OV-1, the first overlay-class audit

Runs the frozen OV-1 pre-registration (edge-pillars spec § Phase 2): buy-and-hold on the French
`Mkt-RF + RF` against the 200-session MA filter earning French `RF` when flat, gated on ulcer index
with Sharpe non-inferiority (`analytics/overlay/report.py`). Read-only against `analytics.db`; it
downloads the French zip per run unless given `--french-zip`, and prints the file's CRSP build line
and a sha256 prefix so the audit names the exact file it read. The SPY cross-check fetches SPY's
total-return close from yfinance at run time (`--no-spy` skips it).

**Run:** `make wifey-overlay-audit ARGS=--precheck` first, then `make wifey-overlay-audit`
(`[--db PATH] [--french-zip PATH] [--precheck] [--no-spy]`).

`--precheck` prints the panel dates, the calendar facts and both bootstrap half-widths, then
exits before any point estimate. The full run prints the same block first. It takes about ten
minutes: each of the eight bootstrap CIs (two legs × primary, 5 bps, execution lag, SPY) is ~5,000
Python-loop stationary resamples over ~24,500 sessions.

The verdict function returns `INSUFFICIENT` when the leg-1 CI straddles zero and leg 3 is not
excluded. OV-1's pre-registration named no verdict for that case (the tool printed `UNREGISTERED`
when the audit ran); the VM amendment named it, which changes no OV-1 reading.

## vm_overlay_audit.py — VM as a paired increment over OV-1

Runs the frozen VM pre-registration (edge-pillars spec § Amendment 1) on OV-1's frame and exact
panel, reusing `overlay_audit.py`'s loaders and constants. Four arms (`bh`, `ov`, `vm`, `ovvm`);
the headline is the increment test `ovvm` against `ov`, the spec falsifier `vm` against `bh`, and
`vm` against `ov` is reported. It refuses to run if the VM frame does not span OV-1's panel exactly.

**Run:** `make wifey-vm-audit ARGS=--precheck` first, then `make wifey-vm-audit`
(`[--db PATH] [--french-zip PATH] [--precheck] [--no-spy]`). Pass the same `--french-zip` OV-1
read, or the end date moves.

`--precheck` prints the panel, `σ_target`, the pre-1952 Saturday-to-weekday volatility ratio, the
VM weight's mean and the gated half-widths. The full run computes ~34 bootstrap CIs (three rows ×
two legs at the primary, 5 bps, execution lag, real-time target and SPY), so it takes well over
half an hour; run it in the background.

## tom_audit.py — TOM on beta-hedged returns

Runs the frozen TOM pre-registration (edge-pillars spec § Amendment 2) from the French file
alone, reusing `overlay_audit.py`'s loader and cost constants. Two arms (`bh`, `tom`), gated on the
hedged series by `analytics/tom/report.py::evaluate_tom`.

**Run:** `make wifey-tom-audit ARGS=--precheck` first, then `make wifey-tom-audit`
(`[--french-zip PATH] [--precheck]`). Without `--french-zip` it downloads the current file, so pass
the audited zip to reproduce its numbers.

`--precheck` prints both panels, the window's share of sessions, the count of months whose window
is not four sessions, and the hedged-Sharpe CI half-widths, and no point estimate. The full run
computes seven bootstrap CIs at 5,000 resamples each.

## edgar_client.py — the SEC User-Agent contract

Measured 2026-08-29: a User-Agent carrying a URL is refused (HTTP 403) by both SEC hosts, with or
without parentheses. An unset `EDGAR_CONTACT_EMAIL` means a hard 403 everywhere, so
`make wifey-pead-backfill` cannot run on an unconfigured box and fails as though SEC were down.
Probe matrix (`data.sec.gov` / `www.sec.gov`): name+email 200/200, name only 200/403, URL with
parens 403/403, URL without parens 403/403. `_user_agent()` raises `EdgarContactMissing` when the
contact is absent or has no `@`, so an unconfigured box fails loud at the call site instead of
three frames away.

The contact reaches that check only because the entry points load `.env`: `edgar_client` reads
`os.environ`, so `tools/insider_backfill.py` and `tools/pead_backfill.py` must call
`load_dotenv()` as their first statement, or a box with `EDGAR_CONTACT_EMAIL` set exactly where
`.env.example`, the README and the error message all say to put it will still die on
`EdgarContactMissing`. Pinned by `tests/test_edgar_user_agent.py` (the loader is patched to raise,
so the test fixes the ordering as well as the call).

Retry is bounded and applies to transient shapes only. `_open_with_retry` is the single place
`_last_call` advances, so both `_get_json` and `_get_bytes` share one throttle clock and a retry
storm cannot breach the SEC's 10 req/s ceiling. It retries 429 and 5xx, `URLError` and
`TimeoutError` — 4 attempts, 1.5s doubling — and raises everything else on the first attempt. 403
and 404 are excluded deliberately: a 403 is the User-Agent contract above and a 404 is a document
that does not exist, so retrying either burns the budget for no reason and buries a configuration
error under what looks like flakiness. Both backfills swallow per-item exceptions, so an
unretried transient failure there costs a filing permanently.

## pead_audit.py — edge-hunt #4 audit for the PEAD-lite sleeve

Read-only **edge-hunt #4** audit (PR #104) for the `analytics/pead/` PEAD-lite sleeve: runs the
pre-registered `{broad,mega}×{long-short,long-only}` 2×2 over the breadth universe (1d) at
0/2/8 bps, prints each cell's DSR/PBO/boot-CI/MinTRL + realized equity-β to SPY + the
8-K-vs-10-Q coverage diagnostic, and the PASS/FAIL on committed `broad_ls`.
`build_grid`/`_grid_frame` are the testable units. Backfill first with `make wifey-pead-backfill`.
**Verdict = FAIL (β-guardrail FIRED on broad_ls; mega-arm null)**
(`docs/audits/2026-06-23-edge-hunt-4-pead-lite.md`).

**Run:** `make wifey-pead-audit` or
`PYTHONPATH=. poetry run python tools/pead_audit.py [--slippage-bps N]`

## gapfill_audit.py — edge-hunt #5 audit for the gap-fill magnet sleeve

Read-only **edge-hunt #5** audit (PR #198) for `analytics/gapfill/`: runs the pre-registered
four-arm family (`broad_ls` gated / `broad_ls_range` / `reversal_control` / `long_only`) over
the breadth universe (1d) at 0/2/8 bps, prints DSR/PBO/boot-CI + realized β + `corr_reversal`,
plus the descriptive gap population. `build_grid`/`_grid_frame` are the testable units.
**Read `corr_reversal` before `sharpe`** — the magnet construction is mechanically a gap-fade.
**Verdict = EXCLUDED, direction REFUTED** (`docs/audits/2026-08-14-edge-hunt-5-gapfill-magnet.md`).
Its "0 bps" column is not cost-free: `fee_pct` defaults to 1bp; see `velocity_audit.py`.

**Run:** `make wifey-gapfill-audit` or
`PYTHONPATH=. poetry run python tools/gapfill_audit.py [--slippage-bps N]`

## velocity_audit.py — edge-hunt #6 audit for the velocity-alternation sleeve

Read-only **edge-hunt #6** audit for `analytics/velocity/` (thesis H-007): runs the
pre-registered four-arm family (`broad_ls` gated / `depth_control` / `duration_control` /
`long_only`) over the breadth universe (1d), printing DSR/PBO/boot-CI, realized β, **beta-hedged
Sharpe**, alpha t, correlation to both decomposition controls, and **gross turnover**.
`build_grid`/`_grid_frame` are the testable units.

**It is the only sleeve audit with a genuinely cost-free tier.** The three tiers are
`gross (fee=0, slip=0)` / `live (2bps+1bp)` / `stressed (8bps+1bp)` — every sibling audit's
"0 bps" column still bills `fee_pct`'s 1bp default, so only this one can separate "the signal is
absent" from "the costs ate it". **Read `corr_depth`/`corr_duration`, then `turnover`, then
`realized_beta`, and only then `sharpe`** — and when the β guardrail fires, read
`hedged_sharpe` INSTEAD of `sharpe`.
**Verdict = EXCLUDED as a null** (`docs/audits/2026-08-14-edge-hunt-6-velocity-alternation.md`).

**Run:** `make wifey-velocity-audit` or
`PYTHONPATH=. poetry run python tools/velocity_audit.py [--slippage-bps N]`

## expand_universe_sp500.py — one-shot universe expander

One-shot universe expander (experiment #1, PR #98): snapshots the pre-expansion `kind=="stock"`
symbols to `config/universe_sp100_snapshot.json`, then merges the current S&P 500 constituents
(+GICS sectors) into `config/universe.json` as `kind=stock/delisted=False` members (existing
entries idempotently preserved). `merge_constituents` is the pure testable unit. Constituents
source: Wikipedia `pandas.read_html` by default (sends a browser UA; needs an HTML parser), or
`--from-csv PATH` (columns `Symbol,Sector`) for a deterministic/offline run.

**Run:** `PYTHONPATH=. poetry run python tools/expand_universe_sp500.py [--from-csv sp500.csv]`

**Pair it with `stamp_universe_listed.py`** — merged constituents arrive with no `listed` key,
which is the *permissive* value, so an unstamped expansion silently widens every
`min_history_days` cohort.

## stamp_universe_listed.py — `listed` history-seam stamper

Reconciles `config/universe.json`'s optional `listed` date against DB first-1d-bar ground truth
(N3 residual (a), 2026-08-13). `resolve_listed(members, first_bars, floor)` is the pure testable
unit; `first_bar_dates` is the one DB read (read-only, one grouped query, same
`statistics_propagation` workaround as `universe_coverage.py`). Report-only unless `--write`.

**The floor is the whole design.** 1d history starts at the backfill's `--since` (2018-01-01 →
first NYSE session **2018-01-02** via `analytics/trading_calendar.py`), and **477 of 505** members
share that first bar, so a bar *on* it cannot distinguish a truncated survivor from a listing that
day. Only a first bar **strictly after** the floor is stamped — the conservative bound README
already documented. The 2 members reaching 2007 stay `None`. `LISTED_TIMEFRAME` is a constant, not
a flag: stamping from `4h` (history starts 2024-05-16) would mark every survivor as a 2024 listing.

**Why it existed as a defect:** `listed` was stamped on **3 of 505** members by hand, and an absent
date reads as "full-history survivor", so `min_history_days` filtered almost nothing — a 1-year
floor dropped **0** members while `FDXF` sat on **17 bars**. The 2026-08-13 run added **23**
(0 corrections, 0 removals — it reproduced all three hand-stamped dates exactly, which is the
check that validated the rule). A member with **no** 1d bars is left untouched and reported loudly
rather than silently un-stamped.

**Run:** `make universe-stamp-listed` (add `WRITE=1` to apply) or
`PYTHONPATH=. poetry run python tools/stamp_universe_listed.py [--write] [--since YYYY-MM-DD]`

## exit_audit.py — exit MFE/MAE diagnostic

Read-only **exit MFE/MAE diagnostic** for the `analytics/exits/` package (PR #96, ported from the
parent), diagnose mode only. Prints coverage, the overall win/loss/expired cohort roll-up, the
per-(strategy, tf, direction) table, and the exit spec §2 verdict grid inline over the live
`signal_alert_outcomes` ledger. No `--replay` (the exit-policy A/B ships with the deferred #437
port).

**Run:** `make wifey-exit-audit` or
`PYTHONPATH=. poetry run python tools/exit_audit.py [--min-n N] [--csv PATH]`

## The four gate-decision replays — and why none of them is runnable work today

`bos_routing_audit.py`, `regime_gate_replay.py`, `regime_threshold_sweep.py` and
`direction_filter_replay.py` all answer the same shape of question: *should a `[bias]` gate flip
from `soft` to `hard`, or be re-routed?* That product is an ENABLE/DISABLE/threshold decision on a
TA gate, which is **inside the frozen category** (CLAUDE.md → Sleeve verdicts). They are
documented here as read-only diagnostics; **running one is fine, acting on it is a user ruling.**

None is wired to a Makefile target — all four are hand-run.

**Two live config blocks cite a PRE-FORK audit.** `[bias.regime.per_strategy] bos =
["high_vol", "range"]` and the `[bias.direction_filter]` justification both quote
`bos_routing_audit.py` at **2026-05-13, n=72,643**. The fork is dated **2026-05-14**, so that
run happened in the parent on **crypto** data. Wifey's `backtest_trades` holds **1,342** `bos`
rows today (24,238 total), so re-running here samples a different, ~54× smaller population.
Neither block changes dispatch right now — both gates are `mode = "soft"` — but treat the cited
avg_r figures as inherited, not measured here. → [[project_crypto_era_inherited_flags]]

## bos_routing_audit.py — T2a full-sample routing probe for `bos`

Re-segments existing `backtest_trades` history for `bos` across
`timeframe × regime × session × volume_state × htf_alignment × direction`, answering "is there
ANY cell with `n >= 30` where `bos` is net-positive?" Full-sample probe, **not** WFO — a positive
cell is a candidate for WFO confirmation, never a promotion on its own. Reuses production helpers
so cell labels match live gate semantics (`analytics.regime.classify_series`, the backtest
`_is_low_volume`/`_is_volume_spike`, and a rolling 4h EMA-50 slope matching the
`[bias.htf_ema]` default anchor). Writes a CSV; makes no DB writes.

**Run:** `PYTHONPATH=. poetry run python tools/bos_routing_audit.py [--db PATH] [--out PATH]`
(default `--out /tmp/bos_routing_audit.csv` — write it to `docs/plans/` instead if you want it
to survive, `/tmp` is cleared).

## regime_gate_replay.py — soft→hard flip evidence for `[bias.regime]`

Replays the v2 Phase 2 regime gate against historical `backtest_trades`, computing `avg_r` on the
subset hard mode would have suppressed vs the subset it would have kept. Deliberately the
**empirical substitute for "wait 2 weeks in soft mode"** — same decision data from history rather
than forward observation. Regime is classified off the most recent **CLOSED** 4h candle at entry
(`_regimes_at_entries`), mirroring the live drop-the-in-progress-bar rule.

Regime resolution at entry must be positional, never arithmetic: `_regime_at_entry` flooring
`entry_time` to a UTC 4h boundary is correct on a 24/7 tape but impossible on an RTH equity one,
where 4h bars stamp 13:30/17:30 UTC and 0 of 105,708 4h bars in the DB are UTC-4h aligned (a single
offset, 90 minutes). An arithmetic floor misses every lookup, `fillna("unknown")` turns each miss
into a fall-open, and the tool would report 0 suppressed of 2,849 trades under
`HOLD — insufficient suppressed trades`, indistinguishable from a genuine sample shortage.
`regime_threshold_sweep.py` imports the same helper and shares the exposure;
`direction_filter_replay.py` does no bar alignment and is unaffected. Live is unaffected too, since
`scanner.py` reads `_series.iloc[-2]`.

`test_lookup_hits_previous_closed_candle` cannot catch this class: it asserts the classifier's
`"unknown"` output on a UTC-aligned grid below its minimum history, which is the bug's own symptom
and passes identically either way. `test_rth_entry_does_NOT_fall_open` is the assertion that
actually fails against an arithmetic-floor implementation (mutation-checked: 0 of 6 RTH lookups hit
under the modulo); a UTC-aligned case is retained as a non-regression for the crypto shape.

The decision rule is per cell, then combined under a single-switch constraint. Each suppressed
(strategy x regime) cell earns an `analytics/audit_guard.py` verdict — a block-bootstrap CI on the
suppressed slice's mean R that must clear ±`bar`, and a Holm-adjusted p-value below `alpha`
across the family of tested cells. `ENABLE` means that slice reliably loses, so dropping it helps;
`DISABLE`/`CONCENTRATE` means it reliably wins, so dropping it costs; `INSUFFICIENT` means the run
cannot tell.

The cells are combined, never pooled: `mode` is one global switch, so a single reliably-winning
cell blocks the flip regardless of how many cells or how much volume point the other way. An
n-weighted mean cannot express that, which is why the pooled aggregates are printed only as
`DESCRIPTIVE — NOT decision-bearing`. Pinned by
`TestFlipVerdictCombinesCellsNotPools::test_one_blocking_cell_vetoes_a_dominant_losing_aggregate`,
which asserts the fixture satisfies the pooled-mean FLIP condition and still comes back blocked —
without both halves it would pass against a pooling implementation and could not detect a revert.

Live-DB verdict 2026-08-19: `DO NOT FLIP` (pooled suppressed -0.1585 <= 0 with kept -0.0153 above
it — the pooled-mean rule's own FLIP condition, inverted by the per-cell rule). The blocker is
`ema`/`high_vol`: n=172, avg_r +0.5497, CI [+0.085, +1.023], Holm-adj p=0.001, verdict `DISABLE`.
`bos`/`trend` (n=897, -0.3540, CI [-0.462, -0.246], p=0.000) is a genuine `ENABLE` and is the cell
that carried the old pooled aggregate.

The significance test demotes one of three cells previously cited as evidence for flipping:
`ema`/`range` (+0.3291, n=110) comes back `INSUFFICIENT` (CI [-0.210, +0.966] straddles zero at
adj p=0.117), so "three of six cells pointed the other way" overstates it — only one survives a
significance test. The flip is blocked either way.

A `DO NOT FLIP` verdict here is not a clean bill for the config: `bos`/`high_vol` (-0.3852, n=655,
the worst cell in the table) is a kept cell, so it is never tested and the tool says nothing about
it. That is a separate, still-open question about the inherited crypto calibration — the `bos`
override exists because a 2026-05-13 crypto audit found `high_vol` was bos's best regime, and on
equities it is bos's worst. Blocking the flip does not fix that.

Every figure above is in-sample — 13 symbols, 2025-06-06 to 2026-08-11, one pass, no
out-of-sample split. It is evidence against flipping, never evidence for a replacement mapping.
See [[project_flag_deltas_need_significance_tests]].

**Run:** `PYTHONPATH=. poetry run python tools/regime_gate_replay.py [--db PATH]`

## regime_threshold_sweep.py — slope-threshold sensitivity for the regime classifier

Re-runs `regime_gate_replay`'s annotation across a grid of candidate `_SLOPE_TREND_THRESHOLD`
values in `analytics/regime.py`, reporting suppressed/kept `n` and `avg_r` plus
`lift = kept_avg_r - suppressed_avg_r` per threshold. Tests whether the live 0.5% default
mis-labels exhaustion as trend: if some threshold separates cleanly the mapping is salvageable, and
if none does, the mapping itself is the problem.

This is a threshold sweep in the literal frozen sense — it selects a parameter value. Read it as
diagnosis of the mapping, not as a source of a new constant.

**Run:** `PYTHONPATH=. poetry run python tools/regime_threshold_sweep.py [--db PATH]`

## direction_filter_replay.py — soft→hard flip evidence for `[bias.direction_filter]`

The T2c sibling of `regime_gate_replay`, same decision rule and same substitute-for-waiting
rationale. Replays `[bias.direction_filter]` plus per-strategy `suppress_long` / `suppress_short`
against `backtest_trades` and compares suppressed vs kept `avg_r`, with a per-strategy breakdown.

Two standing caveats. It reads the flags from a config you pass (`--config`, default
`config/signal_watch.toml`), so **the answer depends on which of the two live configs you name** —
they are different populations. And `suppress_long`/`suppress_short` are themselves crypto-era
inherited flags; `bos.suppress_long` was found one grep away from the #141 sweep that missed it.

**Run:** `PYTHONPATH=. poetry run python tools/direction_filter_replay.py [--db PATH]
[--config config/signal_watch.toml]`

## combo_health.py — post-refresh spot-check for the co-fire tables

Spot-checks `backtest_combos` and `backtest_cross_tf_combos` after a combo/cross-TF refresh:
totals, freshness (rows from runs in the last N hours), the `day_filter` distribution, and the
count of rows meeting the live alert gates plus the top viable combos. Gate defaults mirror
`[combo]` in `config/strategy_params.toml` (same-TF `tue_thu` + `avg_r >= 1.0`; cross-TF
`tue_thu` + `avg_r >= 0.0`).

**Both tables currently hold 0 rows**, so the tool reports empty and the live co-fire gate is
inert — no alert can carry a confluence tag. That is a *data* gap, not a tool fault: repopulating
means running the combo sweeps, which is frozen sweep work. Read an empty report as "never
refreshed", not "refresh failed".

**Run:** `PYTHONPATH=. poetry run python tools/combo_health.py [--db PATH] [--fresh-hours N]`
after `make wifey-combo-backtest` / `make wifey-cross-tf-backtest` (both `SAVE=1`).

## warning_value_audit.py — do the W1–W8 alert warnings predict avg_r?

Ported from the parent. This is the tool that wired `analytics/audit_guard.py` into a real host:
the obvious host, `tools/gate_audit.py`, needs `low_volume`/`volume_spike` on `backtest_trades`,
columns wifey never had, and skipping that migration fails silently via `fillna(False)`. This audit
needs neither, since it re-derives its flags from OHLCV.

Regenerates each historical trade's six candle-warning flags through `analytics/warning_audit.py`
(which imports the live `alert_formatter` helpers rather than reimplementing them) and emits a
pre-committed SUPPRESS-CANDIDATE / REVERSE / COSMETIC / INSUFFICIENT verdict per
(warning x direction) — bootstrap CI clearing ±`bar` and a Holm-adjusted p, one family per source.
`backtest_trades` is primary and deduped across saved runs on
`(symbol, tf, strategy, direction, signal_time)`; `signal_alert_outcomes` is corroboration only.
Read-only.

`_tf_ms` must delegate to `parse_timeframe_secs` rather than a literal map: the parent's map spells
the weekly timeframe `1w`, while every equity surface here uses `1wk` (497 signals), so a literal
map raises `KeyError` on the first real run. `TestTimeframeLength` pins this and was
mutation-checked in both directions.

Result (2026-08-13): 11 of 12 backtest cells INSUFFICIENT, one SUPPRESS-CANDIDATE —
`w5_wick_rejection`/long (n=316 warned at -0.315R vs -0.037R clean; Holm p=0.002; sign holds on all
three timeframes and on 5 of 6 strategies). Not shipped as a gate, since the live substrate has n=1
for that cell.

The honest verdict for those 11 cells is INSUFFICIENT, not COSMETIC: a sample-size floor
(`n >= min_n`) says a test ran but never that it could have seen anything. Under `powered_null` —
the CI strictly inside ±bar — 0 of 11 survive and 0 of 12 cells have a CI inside ±0.05R (half-width
median 4.1x the bar, range 1.8x-6.2x; 4 of 11 point estimates exceed the bar, worst
`w1_marubozu`/long at +0.289R, CI [-0.007, +0.615]). The honest reading is "we cannot tell," never
"the warnings are decoration" — only negative labels can move under this correction, so the W5 lead
is unchanged. Verdict and caveats: `docs/audits/2026-08-13-warning-value-audit.md`.

**Run:** `make wifey-warning-value-audit` (`ARGS="--source live|backtest|both --min-n N --out PATH"`).

## pundit_score.py — read-only scorer for the Stream-C pundit ledger

Ported from the parent. Resolves every `docs/plans/pundit-calls.jsonl` call against stored
OHLCV → hit-rate + R proxies per author × setup-family × direction, plus a machine-readable
`docs/plans/pundit-priors.json` sidecar. Level parsing, family tagging, roll-up and
output shape are byte-identical to the parent so the two ledgers stay comparable.

Descriptive priors only — no gate is implemented, though an earlier docstring implied one by
saying "audit_guard gates come later, only if a cell earns n>=30." The only implemented construct
is `--min-n` (default 5), which renders a marker in the report and changes no output — nothing
happens when a cell crosses 30. wifey does have a wired `audit_guard` (`analytics/warning_audit.py`),
but it scores the W1-W8 signal warnings and no code path joins it to pundit cells.
`AUDIT_ELIGIBLE_N = 30` does not restore a gate: `audit_eligible_cells()` returns cell keys, and
`render_report` prints a note saying in words that none fires, so the crossing stops being silent.
Deciding what a pundit prior should gate is an open research question — do not wire this to
anything without answering it. The largest author cell is n=13 of 19 ledger rows, so the note
fires on zero cells today (`make wifey-pundit-score`).

`load_ledger` enforces three field domains at the read boundary via the pure
`analytics/pundit_{direction,horizon,authors}.py` guards (see `context/analytics.md`): a violation
becomes a per-line warning naming the line and the value, rather than a silent wrong number
downstream. `horizon` is the one that mattered here — an unrecognized value took two silent `.get`
fallbacks (`SCORE_TIMEFRAME`'s wrong bar series and `SESSION_WINDOWS`'s wrong window), where the
parent has only the latter. The `author` guard changes the priors JSON key shape (`@fenggemeigu`
becomes `fenggemeigu`); a future Brief/Card port must join on the normalized key. No scored number
changed: the committed 19-row ledger produces a byte-identical report apart from the author column.

### A hyphenated range overrides a `/`-ladder

`parse_level_field` emits `zones` and `numbers` separately, and `select_level` prefers a zone. Any
hyphenated range in a `target` field — including a clarifying parenthetical after a valid ladder —
therefore replaces the intended level. Reproduced against the production functions at
`ref_close=64,000`, `role="target"`: `67,000 / 70,362.23 / 82,000` resolves to 67,000, and the same
string plus `(or 65k-68k)` resolves to 65,000 long / 68,000 short.

The zone resolves to whichever edge price reaches first, so the error runs in both directions: a
long lands nearer (manufacturing an optimistic WIN) and a short lands further (stranding the row
OPEN) — it is not one-directional. Write-side rule and the `make wifey-pundit-score` round-end
check live in `/ingest-video` step 8 and `/ingest-x` step 4.

### The level-negation guard

`parse_level_field` drops all candidates when a field's head negates the level
(`_NEGATION_HEAD_RE`). `_UNSPECIFIED_MARKERS` cannot catch this alone, since it matches the whole
stripped string: bare `not specified` was caught while
`"not stated (implied ~454 resistance)"` fell through to `_NUM_RE` and returned the parenthetical
as the level.

The sanity gate is no backstop for this and cannot be made into one. Its window is
`0.2x-5.0x ref_close`, and the worst form of a phantom number is a level near `ref_close` — it
passes the gate, and with exactly one sane candidate `select_level` returns it at
`low_confidence = False`, so a fabricated call is indistinguishable from a real one at the highest
confidence label the scorer has.

The negation check is anchored at the head deliberately. A negation that trails a stated level
qualifies its provenance, not its existence — `"~420 (current market, no explicit entry stated)"`
is a real level. Those keep their candidates and set `ParsedField.hedged`, which `select_level`
ORs into `low_confidence`, downgrading rather than deleting a genuine call; an "anywhere in the
text" match would destroy that second class.

Measured on this repo's 19-row / 33 populated-level-field ledger (script:
`docs/plans/scripts/pundit_negation_impact.py`; do not carry the parent's field counts across, its
ledger is a different, non-overlapping population): 2 fields stop fabricating a level, 1 keeps its
level at reduced confidence, 30 unchanged. Only one of the two reaches a published number, because
a parse-layer count is not a scorer-layer count:

| row | field | before | after |
| --- | --- | --- | --- |
| `luckychartape` TSLA short | stop `"not stated (implied ~454 resistance)"` | `454.00`, conf ok, R +3.27 | dropped, conf low, R -- (ATR-R 6.36) |
| `benjaminjcowen` SLV long | target `"not stated (qualitative; 1970s analog...)"` | `1970` already rejected by the sanity gate (~38x SLV's 52.16 reference close, against a 5.0x ceiling) | no published change |

`other/short` therefore flips +1.13 to -1.00 avg R, and that +3.27 was the ledger's largest
positive-R win (the only other is `fenggemeigu` MSFT at +1.35) and the only one resting on a
fabricated level. `fenggemeigu` — the one author with a rankable `n` — does not move at all (-0.41
either way), confirming the negative floor on the only rankable author is real, not a parsing
artifact. Any `pundit-priors.json` generated before this fix carries the fabricated record;
regenerate rather than reasoning from it.

### `avg R` ships its own denominator

`avg_r` and `n` are different populations, and an earlier report printed them adjacent without
saying so. `r` needs a stated stop (`score_call`: `if risk is not None and risk > 0`), so a call
that stopped out necessarily has one while a win scored against a target often does not — `avg_r`
describes a loss-enriched subsample while `n` / `resolved` describe the whole cell.

Measured 2026-08-12 (19 calls, 8 resolved; script: `docs/plans/scripts/pundit_r_coverage.py`, which
calls the production scorer — do not carry the parent's coverage figures across, that is a 203-row
crypto ledger sharing no rows with this one):

| cohort | resolved | with `r` | coverage |
| --- | --- | --- | --- |
| WIN | 3 | 1 | 33% |
| LOSS | 5 | 5 | 100% |

The censoring here inverts the headline: every loss carries an `r`; a third of wins do.
`fenggemeigu` reads `avg R` -0.41 over `r_n=6` while the complete `atr_r` sample over all 7
resolved calls is +0.80 — the two disagree in sign. `luckychartape` is the mechanism in one row: a
WIN whose stop was the fabricated level above now contributes `ATR-R 6.36` and nothing at all to
`avg R` (`-- (0/1)`).

The fix is disclosure, not a new statistic: `CellStats.r_coverage`, an
`avg R (r_n/resolved)` cell in both report tables, and `r_n` / `r_coverage` / `atr_r_n` in the
priors JSON. `avg ATR-R` leads `avg R` in the column order because it is the complete sample.

A mean and a count printed side by side assert a shared denominator. When they do not share one,
the disclosure belongs in the cell, not in a footnote — a reader comparing two authors' `avg R` is
comparing two different populations and nothing on the row says so otherwise.

### Nine documented divergences from the parent

(1) through (8) cover everything that touches the tape, because equities are a sessioned market;
(9) is a correctness fix that is not equity-specific.

- (1) scoring frame follows the horizon (`1h` intraday / `1d` swing+unspecified — several ledger
  symbols have no 1h bars at all)
- (2) no call-candle containment — the reference is the last bar fully closed at or before the
  call, so after-hours and weekend calls still resolve
- (3) thesis entries fill at the next open, not the call bar's close (the parent's convention is a
  look-ahead)
- (4) gap-aware direction-aware level crossing — a long stops at `min(open, stop)` rather than
  needing `low <= stop <= high`
- (5) adverse-first resolved by the open (opened-beyond-stop means loss at open; both-intrabar
  keeps the parent's stop-wins rule)
- (6) windows counted in NYSE sessions (2 / 21 / 10) via `analytics/trading_calendar.py`, not
  wall-clock
- (7) month-anchored years stripped before level parsing, since US index levels share the
  1,900-2,100+ band with year strings (an early run read "...starting Aug-Sep 2026" as a 2,026
  target on a 7,436 index)
- (8) staleness measured against the last closed session, not wall-clock now, else every symbol
  reads STALE overnight; also folds in the null-symbol guard that previously existed only as
  `/ingest-video` skill prose
- (9) `_geometry_note` delegates to `x_route.check_level_order` and covers the target leg as well
  as the stop: a wrong-sided stop only yields a nonsense R, but a wrong-sided target is already in
  profit at the fill and books an instant WIN at ~0.00 R — a phantom statistic rather than a
  visible error, which is how a mis-written "unless it reclaims 29,200" stop produced a 100% hit
  rate with zero warnings. Such a row is now `UNSCORED` with the offending pair named. The parent
  was ported from the same code and likely carries this latent defect; raise it on the next
  `/sync-parent` rather than assuming it was fixed upstream.

Read-only; no schema change, goldens untouched.

**Run:** `make wifey-pundit-score` or
`PYTHONPATH=. poetry run python tools/pundit_score.py [--as-of ISO] [--min-n N]`

### Its OHLCV is a third universe

`make go-live` syncs `config/stocks.json` — 13 ETF and equity proxies. The ledger records the index
and futures underlyings a pundit actually quoted (`^GSPC`, `GC=F`, `^TNX`), so the two sets barely
intersect and syncing one never refreshed the other. Measured 2026-08-15 right after an operator
`CATCH_UP=1 make go-live`: the mega-caps reached 2026-08-14 while eight ledger symbols sat at
2026-08-04 and `^TNX` had no bars at all, while go-live reported success throughout, because
`pundit_score` degrades a stale symbol to `STALE` rather than erroring — a permanently-unresolving
ledger is indistinguishable from one where nothing has triggered yet.

Fixed by `wifey analytics {sync,backfill} --pundit`, wrapped as `make wifey-pundit-sync` /
`make wifey-pundit-backfill`. Three properties worth keeping:

- It resolves from the ledger at run time, never from a second hardcoded list. A frozen list would
  reproduce the original defect one level over, since the ledger gains symbols as calls are
  routed.
- An empty resolve exits non-zero rather than falling back to the watchlist. A fallback would
  resync the same 13 names go-live already covers and report success, reproducing the defect.
- `--universe`, `--pundit` and `--core` are mutually exclusive at argparse level, so a
  conflicting pair is rejected outright instead of silently resolving by precedence.

`--core` (`make core-sync`) resolves to `^GSPC` for the survival-core line. It is a flag rather
than `--symbols ^GSPC` because `poetry.exe` on Windows strips the caret from argv, so that
invocation syncs `GSPC`, logs "run backfill first" and exits 0.

`INVALID_LEDGER_SYMBOLS` (in `utils/config_validation.py`) is the single definition of "not a
symbol," imported by both the loader and this scorer — if they diverge, the sync path fetches
symbols the scorer discards, or skips ones it scores. `tests/test_analytics_runner.py` pins the
identity.

After scoring, the ledger's own state is the check that the refresh worked: 22 rows, 0 `STALE`,
0 `UNRESOLVABLE` as of 2026-08-17.

## backfill_null_tp_outcomes.py — one-shot retro migration

One-shot retro migration, ported from the parent: reconstructs the pct-fallback SL/TP for legacy
`signal_alert_outcomes` rows written with NULL `tp_price` (before `_resolve_outcome_sl_tp`), then
resolves them via `backfill_outcomes`. Read-only by default; `--apply` gated; idempotent.

**Run:** `PYTHONPATH=. poetry run python tools/backfill_null_tp_outcomes.py [--config config/signal_watch.toml] [--apply]`

## x_fetch.py — read-only X/Twitter post fetcher

Read-only X/Twitter post fetcher via the public syndication endpoint
(`cdn.syndication.twimg.com/tweet-result`; no auth or scraping — a non-empty `token` is required
but its value is not validated, so a fixed dummy suffices). URL to `XPost` (text + full-res
`?name=orig` chart URLs + `video_present`/`is_thread`/`is_quote` flags + best-effort
`quoted_text`/`quoted_author` from the nested quoted tweet) + `download_photos` (charts to
gitignored `.cache/x-media/<id>/`); graceful `Unavailable` on
protected/deleted/tombstone/non-200/non-dict. Batch path `fetch_x_batch` fetches N URLs once each
with a randomized cooldown between network fetches only (default 4-12s; skipped before the first
fetch and on cache hits) plus a per-id dedup cache (`.cache/x-posts/<id>.json`, giving zero network
on re-runs); `sleep`/`rng`/`get` are injected for deterministic, network-free tests while
`fetch_x_post` stays pure.

The thread path, `walk_thread`, recovers one author's self-thread by following
`in_reply_to_status_id_str` upward from the tail, returning a `ThreadChain` (posts root to leaf
with `thread_pos`, plus `notes`). The direction is a hard constraint, not a choice: the endpoint
has no replies/children field, so a thread is reachable only from its last post — a bookmarked
parent yields nothing below it. `XPost` carries `in_reply_to_id` / `in_reply_to_author` /
`conversation_count` / `thread_pos`, all defaulted, because `_load_cached` does `XPost(**raw)` and
a pre-existing cache entry may lack the keys; `_load_cached` catches `TypeError` as a cache miss
either way. The walk stops at the root, on an author change (climbing further would attribute
another pundit's words to the bookmarked author), at `max_hops` (25), or on an unavailable hop —
every stop but the root records a note, and it never raises. It reads the per-id cache but
deliberately does not write it: an entry written here with empty `photo_paths` would make a later
ingest of that post skip its chart download. `conversation_count` counts the whole conversation's
replies (everyone's) and is not thread length. Backs the `/ingest-x` skill, ported from the parent
(spec `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`).

**Run:** `PYTHONPATH=. poetry run python tools/x_fetch.py <url…> [--batch] [--thread] [--json] [--force] [--min-delay/--max-delay S] [--cache-dir/--media-root DIR]`

## x_route.py — routing decision and shared level sign-check

Pure routing decision and the shared level sign-check for the ingest skills.
`route_target(content_type, verdict, *, retrospective=False, rejected=False) -> str | None` is the
content-type gate (setup/mechanic/claim), routed through the research pipeline's 4-bucket verdict
taxonomy on the claim path to Stream A `thesis-inbox.md` / B `mechanics-backlog.md` / C
`pundit-calls.jsonl`, or dropped.

Two keyword-only suppressors drop a `setup` — `retrospective` and `rejected`, both defaulting to
`False`, ported from the parent. A `setup` with either returns `None`; a `mechanic`/`claim` is
unaffected on purpose, since neither has an entry to decline and honoring the flag there would let
one mis-set field delete a routable item. Callers must pass the flags explicitly — defaulting to
`False` means a forgetful call site fails open and silently.

While these suppressors were missing here, `/ingest-video`'s pass 1 could set `retrospective` on a
setup lifted from a channel's intro recap with nothing reading it, so the call would route to
`pundit-calls.jsonl` carrying today's `call_ts_utc` and be scored on an already-resolved trade.
Porting a fix by citing the PR that introduced it is not evidence every half of that PR landed — a
citation naming the right PR number can still leave a sibling file (here, `tests/test_x_route.py`)
untouched. No ledger rows were actually damaged, since all rows predate the flags.

`unattributable` is the parent's third suppressor and is deliberately absent here — it belongs to
relay-attribution work this fork has refused.

`check_level_order(direction, *, entry, stop, target) -> str` is the sign-check: a long must
satisfy `stop < entry < target`, a short `target < entry < stop`; every pair whose legs are both
present is judged, equality counts as a violation (zero risk / zero reward), and an unjudgeable
direction warns rather than passing silently.

`check_row_levels` applies it to a ledger-shaped row (`<role>_px` overriding the free text via
`first_level`, which strips three classes of number that read as a level but are not one).

### Three number classes stripped by `first_level`

- Month-anchored years — `MONTH_YEAR_RE`, the single definition also imported by
  `pundit_score.py` as its divergence 7
- Percentage ranges via `PCT_RE`, since the `%` binds to the second number
- Chart timeframes via `TIMEFRAME_RE` (Latin `4h`/`1d`/`15m`/`1wk` + CJK `小时`/`日线`/`分钟`)

The first two are the single definitions, also imported by `tools/route_dedup.py`'s
`normalize_levels`, which has no sanity gate of its own; `TIMEFRAME_RE` is deliberately not shared,
because `normalize_levels` is already immune by a different mechanism — its `_MIN_LEVEL = 100.0`
floor drops a `4h` reading as `4.0` as sub-$100. `TIMEFRAME_RE` exists because a gold long whose
entry read "break above the 4h descending trendline" sign-checked as "stop 4000 on the wrong side
of entry 4" — that instance warned loudly, but the same artifact passes silently whenever the
stripped number happens to out-rank a real leg (a long with entry `4h` and a stop of 3 reads
4 > 3 = OK), which is the fake-WIN class the guard exists to catch. Its `(?<![\d.])` lookbehind is
load-bearing: without it `\b` matches at the decimal point, so `4.5m` matches its own `5m` tail and
leaves a bare `4.` behind.

Consumed by `/ingest-x` + `/ingest-video` step 8 through the `--check-levels` CLI (JSONL from a
file or stdin; advisory — prints, exits 1 if any row warned, never rewrites or drops) and by
`tools/pundit_score.py` on the read side. It warns and never drops, because the failure mode being
fixed is silence. Stdlib only, no I/O beyond the CLI's read.

### Known blind spot

The pairwise rule cannot judge a row stating one level and nothing else — that shape is caught
read-side only, where the scorer substitutes the market price for a missing entry. This is the
dominant shape, not an edge case: in one measured batch, 7 of 11 candidate rows were one-legged (a
lone "defense point" and nothing else), 1 had zero legs, and the only full entry/stop/target triple
was degenerate — the support level served as both entry and stop, which the check correctly
flagged as zero risk. Encode such a row as entry+target with the stop left unstated rather than
inventing a gap the pundit never gave.

**Run:** no standalone CLI invocation of its own — consumed via the `--check-levels` CLI
(JSONL from a file or stdin) inside `/ingest-x` + `/ingest-video` step 8, and read-side by
`tools/pundit_score.py`.

## route_dedup.py — routing dedup for the ingest sinks

Routing dedup for the ingest sinks (edge case the fetch caches structurally cannot cover: they
dedup *fetches*, nothing dedups *routing*).

### Machinery

Two layers with different machinery:

- **identity** — `RoutedItem` ledger at `docs/plans/routed-ledger.json`, keyed
  `(source_id, round(item_ts, 1), sink)`; `load_ledger`/`append_routed`/`remove_routed`/
  `is_routed`, atomic tmp+`os.replace` write, loud `SystemExit` on a malformed/unversioned file
  rather than a silent reset
- **semantic** — `find_similar` scores a claim against a sink's split entries —
  `3.0 × |shared levels| + 10.0 × jaccard(terms)` ≥ `_MIN_SCORE` 3.0; `find_source_duplicate_pairs`
  does the same item-vs-item over one source's *pending* items, which `find_similar`
  structurally cannot see because every check runs before the approval that writes anything

Advisory except for `already_routed` — it surfaces candidates for the review digest and never
drops a row. CLI `check | mark | unmark | pairs | seed`; `mark` runs strictly after the sink write
(marking at check time is the watermark-on-send defect class).

`--sink` is gated on `KNOWN_SINKS` because of the ledger key, not for tidiness: `is_routed` keys on
`(source_id, item_ts, sink)` via `_key`, so a sink outside those three full paths is dedup-blind —
it writes a ledger row no later round can ever match — while `find_similar` stays lenient on an
unrecognized sink because scoping genuinely cannot apply there. The membership check sits at the
CLI boundary for that reason. Ported from the parent as prevention rather than a repair for an
existing problem here.

A bare `python3 tools/route_dedup.py` must work. That invocation puts `tools/` on `sys.path`
rather than the repo root, so the `tools.x_route` import dies with `ModuleNotFoundError` unless a
`sys.path` bootstrap runs first — scoped to tools that actually import from the repo, since in one
that does not it would be dead code masking the breakage the moment the first import appears. The
guarantee is `test_bare_invocation_works`, which runs the module with `PYTHONPATH` stripped from
the environment and was mutation-checked, never a comment beside the bootstrap line. The parent
hits the same class on `analytics.*`; the rule ports even though the failing module name differs.

Since #436 the rule covers every `tools/*.py` with a `__main__`, and
`tests/test_tools_bare_invocation.py` is the one guarantee, with two legs. The runtime leg runs each
entry point's `--help` with `PYTHONPATH` dropped and requires exit 0, behind a positive control: a
probe with no bootstrap, run through the same harness, must die, or `PYTHONPATH` leaked and every pass
is vacuous. The static leg requires a `sys.path.insert` call in any entry point that imports from a
repo package at any depth, because `--help` exercises module-level imports only:
`session_digest.py` imports from the repo only inside functions, so it passed `--help` bare while
every probe read BROKE. A new tool is discovered by the glob, so it needs no registration.

### Seven divergences from the parent

Ported from the parent with seven divergences, every one found by running the ported code against
the live sinks rather than by reading it. Four of the seven (1, 3, 6, 7) trace to the single
structural fact that wifey's Stream A sink is a markdown table where the parent's is prose:

- (1) Stream A splits on table rows, not the parent's level-two headings — the parent's splitter
  returns one blob for the whole live file, so every comparison would silently score against it
  while `semantic_scope` still reported `all-entries`
- (2) `normalize_levels` also strips month-anchored years and percentages, reusing `x_route`'s
  `MONTH_YEAR_RE`/`PCT_RE` single definitions, since US index levels share the year band
- (3) URLs are stripped before both scoring layers, since Stream A rows embed the source deep-link
  and its `&t=124s` offset reads as a price level (measured: the parent reads `124.0` off a live
  row)
- (4) bare 4-digit years in `_YEAR_BAND` (1900-2099) are not levels — before this rule 3 of 28 live
  Stream A pairs flagged and all three were year artifacts; the rule keys on how the number is
  written (a level in that band carries a separator or decimal, a year never does), at the stated
  cost of losing a bare `2050` level
- (5) the ledger key truncates `item_ts` to whole seconds (`_ts_key`) where the parent rounds to
  1dp — an offset can arrive both as the row's float `ts` (`566.81`) and as the deep link it
  persists (`&t=566s`, truncated), and `round()` maps those to different keys, so a seeded ledger
  would silently fail to block a re-route
- (6) term overlap is containment over the shorter side, not jaccard (`_overlap` + `_MIN_DENOM`) —
  a Stream A entry is a whole row including a gap-note column, so jaccard scored a near-verbatim
  restatement of a live row at 1.86 and never fired, where containment reads 0.89
- (7) the pipeline's own verdict vocabulary is excluded from term matching (`_STOPWORDS` derived
  from `x_route.VERDICTS`) — `ALREADY-TESTED` is stamped on every entry and `already`+`tested`
  carried all three remaining live flags, the same defect `_PUNDIT_CONTENT_FIELDS` fixes for
  Stream C in the shape a table takes

### Inherited limits

Two inherited limits are documented rather than fixed: sub-$100 names contribute no numeric
evidence (`_MIN_LEVEL`), and Stream C's `same-source` scope is blind to one author restating a call
across uploads, deliberately — two calls a week apart are two genuine observations the scorer
resolves against different bars.

### Calibration

On the live sinks: 0/28 Stream A and 0/7 same-source Stream C pairs flagged (highest non-flagging
score 2.5 vs a 3.0 threshold), while restatements of known-duplicate rows score 8.9 / 8.8 against
the correct row — discriminating, not inert, which matters because an inert check is
indistinguishable from a clean one.

Consumed by `/ingest-x` (step 3 check, step 4 mark) and `/ingest-video` (step 7 check +
`pairs`, step 8 mark).

**Run:** `make wifey-route-dedup-seed` (`APPLY=1` to write; read-only otherwise, idempotent,
10/10 live rows seeded unmodified) to seed the ledger. CLI subcommands:
`check | mark | unmark | pairs | seed`.

## video_calltime.py — pure call-time resolution

Pure call-time resolution, the `/ingest-video` look-ahead guard, kept out of prompt-space
deliberately since LLM date arithmetic is a known failure mode and this field decides whether every
author's hit rate is honest. `resolve_call_ts` prefers a video's stated in-video time but bounds it
(`stated < publish`, `publish − stated ≤ STATED_TS_MAX_LEAD_H` (168h); a naive/offset-less stated
value is rejected rather than assumed-UTC unless `stated_date_only` is set, in which case it uses a
conservative end-of-day clamped below publish), else falls back to `publish_ts_utc`. `is_backlog`
flags `publish → ingested` lag greater than `BACKLOG_THRESHOLD_H` (24h), computed from publish
time, so it describes ingest lag rather than the pundit's own lag.

**Run:** `PYTHONPATH=. poetry run python tools/video_calltime.py --publish <iso> [--stated <iso>] [--date-only] --stated-raw "<quote>" [--ingested <iso>]`

The naive-date carve-out is keyed on the `--date-only` flag, never on the value's shape, because
`/ingest-video` tells its pass-1 subagent to emit `YYYY-MM-DD` and `_parse_aware` requires an
explicit offset — a bare date without the flag returns `None` and the publish fallback wins. Two
properties make the flag-based carve-out safe where a blanket rejection is not: a date carries no
zone to lose, and end-of-day normalization can only move the result later, away from the
look-ahead-permitting direction. A time component is discarded rather than trusted, since the flag
asserts there is none.

## yt_feed.py — YouTube channel auto-feed backing `/ingest-feed`

Ported from the parent at parent HEAD rather than at the original PR's merge commit, so several
follow-up fixes land with it (724 to 894 lines) — notably `load_dotenv()`, without which
`YOUTUBE_API_KEY` in `.env` is invisible and every API subcommand fails.

Read-only `poll` of each configured channel's uploads playlist (Data API v3, `YOUTUBE_API_KEY`,
~2-3 units/channel/day, never `search.list`) plus `backfill` deep pager (floor ignored, ledger
respected). `poll --since` narrows the floor only (`max(floor, since)`, shared `_parse_since` with
`backfill`): the floor records what the operator already declined, so honoring an earlier
`--since` would resurface it — reaching below the floor stays `backfill`'s job, and that asymmetry
is the whole difference between the two subcommands. `mark` is the only writer, stamped
post-review-gate, so the watermark-on-send defect class is structurally impossible here: no
fetch-time writes, no moving watermark, static per-channel `floor_ts`. Plus `resolve` (handle to
ready-to-paste TOML block) and `hint` (pure local config read, resolving ahead of the API-key gate,
so it needs no `YOUTUBE_API_KEY`).

Config is the gitignored `config/youtube_channels.toml` (committed `.example`). State is
`docs/plans/yt-feed-state.json` (gitignored, atomic writes, loud-abort on malformed). Injected HTTP
`get` gives network-free request-shape tests (70 of them).

Two wifey-specific divergences, both re-derived here rather than inherited:

- `item_cap` is nearly inert in this repo. It defaults to `video_marks.ITEM_CAP`, imported rather
  than re-literalled, so it correctly picks up wifey's 12 (the parent's is 5). But `/ingest-video`
  requires `item_cap + len(TAIL_OFFSETS_S) <= FRAME_CAP`, i.e. at most 13, so the usable range is
  13..13. `yt_feed.py` does not validate this — it imports `FRAME_CAP` only to estimate tokens —
  and a larger value would silently degrade kept items to `vision_confidence: "low"`. The parent
  has 8 of headroom and never hits the ceiling.
- `hint` exits 1 when `config/youtube_channels.toml` is absent (`load_feed_config` raises
  `SystemExit`; it does not return `matched: false`). That file may legitimately not exist here,
  since `/ingest-video`'s primary mode in this repo is a hand-pasted URL with no follow list, so
  its step-3 call is guarded with `|| true` and a missing config is treated as `matched: false`,
  not as an error.

**Run:** `PYTHONPATH=. poetry run python tools/yt_feed.py poll|backfill|mark|resolve|hint`

`mark` must extract `--ingested`/`--skipped` from `argv` before argparse sees them, because a
YouTube id like `-mx3UwwJ5P4` starts with a hyphen and argparse reads the leading `-` as a flag,
silently dropping it from an `nargs="*"` list. Since `mark` is the sole writer of consumption
state, a swallowed id is never recorded and the video re-presents forever with no other symptom —
no error, no partial write, nothing downstream that looks wrong. Pinned by
`tests/test_yt_feed.py::TestDashLeadingVideoIds` (5 tests).

`route_dedup --source-id` is a deliberate contrast and is left unchanged: it takes one value, so
`--source-id=<id>` works natively and the space form fails loudly, which its `--help` says. Rank a
fix by whether anything could have seen the failure, not by its blast radius — a read-path tool
that dies in front of you costs a retry, while a write-path tool that silently drops one argument
costs a ledger nobody knows is wrong.

## video_fetch.py — read-only YouTube/X video fetcher

Read-only YouTube/X video fetcher: every yt-dlp call goes through
`_YT_DLP = ("yt-dlp", "--js-runtimes", "node")`, never a bare `["yt-dlp", …]`, because yt-dlp
>=2026.07.04 enables only deno by default — without an available JS runtime every media path 403s
while captions still resolve, so the failure masquerades as one unlucky video. `--js-runtimes` is
additive, and the `yt-dlp-ejs` runtime dep backs it.

At least two independent causes produce that identical symptom; the JS runtime is only one of
them. The media fetch also 403s when the extractor client is left to yt-dlp's own default
selection, which is why `_ensure_local_media` pins
`--extractor-args youtube:player_client=android`. Measured 2026-08-19, with `node` installed and
`--js-runtimes node` already in effect: the default pick `android_vr` 403s, while
`android` / `mweb` / `web_embedded` all download; `tv` fails to load, and `web_safari` / `ios` fail
differently ("requested format is not available" against `bv*[height<=1080]`), so they are not
substitutes. A yt-dlp version bump alone does not fix this, and `poetry.lock` is not involved. The
3-attempt retry does not cover it either, since it was written for an intermittent 403 and this one
is deterministic. Pinned by
`tests/test_video_fetch.py::test_local_media_download_pins_the_extractor_client`, which asserts the
flag/value pair adjacently because the regression shape is an absent flag. Both causes are silent
in the same direction — captions resolve either way, so the vision pass returns chart-uncorrected
items that look fine. Diagnose by running the download, never by reading the code.

- `fetch_meta` (yt-dlp `--dump-json` to `VideoMeta` including publish time)
- `fetch_transcript` (existing captions in any language first, else Groq `whisper-large-v3` over
  extracted opus audio — `split_audio` chunks past the 25MB cap using `duration_s` for
  offset-correct per-chunk timestamps). Returns a `TranscriptResult` carrying the segments, the
  chosen `lang` and a `source` of `manual_captions` / `auto_captions` / `asr_whisper` /
  `captions_unknown` / `asr_whisper_captions_missed`. `captions_unknown` is not folded into
  `auto`, because "we did not ask" and "we asked and it was ASR" are different claims. An ASR
  transcript is a materially weaker source than an author-written one, and every item, `raw_quote`
  and call-time derives from that text.
- `asr_whisper_captions_missed` is the fifth value and the one to act on, since it is recoverable
  by re-running that video, unlike plain `asr_whisper`. `fetch_transcript` must not run yt-dlp,
  discard the result, and glob for `sub*.vtt`: a transient HTTP 429 then produces an empty list
  byte-identical to the one a caption-less video produces, permanently and silently downgrading the
  note with no record that a track ever existed. `_download_captions` returns `(vtts, missed)` and
  retries once (`_CAPTION_ATTEMPTS = 2`, `sleep` injected so the suite stays fast). The
  discriminator is the metadata, not the empty glob: `_sub_langs` always appends a last resort, so
  a caption-less video legitimately requests a track that cannot land while yt-dlp still exits 0; a
  miss means the metadata listed tracks and none arrived, plus any non-zero exit. Relaxing that to
  "no vtt landed" mislabels every caption-less video, which is why
  `test_a_genuinely_caption_less_video_stays_plain_asr` exists. The no-Groq path says
  `caption download failed` rather than `no captions available` so the distinction survives to the
  one surface a human reads.
- `parse_vtt` is `parse_vtt_cues` (one raw segment per cue) then `normalise_captions` (#330):
  inline tags are stripped, entities decoded, and on a track carrying inline word timings
  (YouTube's auto-caption signature) each cue keeps only the words after its overlap with the
  previous cue, so every segment carries the start time of the cue that introduced its words.
  An author-written track keeps every cue's words. A whitespace-only line ends a cue only once
  it has text: YouTube opens each cue with a one-space line, which used to drop the track's
  first cue. `tests/fixtures/youtube_auto_captions.vtt` depends on those one-space lines, so
  `trailing-whitespace` excludes `tests/fixtures/*.vtt`. Transcripts cached under `.cache/video/`
  before this stay raw until re-fetched with `--force`.
- `_sub_langs` decides which caption tracks are even requested, and asking wrong costs the whole
  transcript. yt-dlp returns `language: null` on a large slice of the follow list, and requesting
  `en` alone means a Chinese upload with an author-written `zh-Hant` track gets "no subtitles for
  the requested languages" and falls through to ASR, worst exactly where ASR is weakest. It widens
  the request with the codes `--dump-json` already returned (same call, no extra quota), resolves a
  regional `lang` down to its base (`en-US` becomes `en`), and caps the list at
  `_MAX_SUB_LANGS = 6` so no video can request a translate matrix — the parent measured 157 auto
  codes led by `ab`/`aa`/`af`, answered with HTTP 429 partway through, leaving the transcript's
  language decided by which file survived the rate limit. Ported from the parent.
- `Chapter` / `_parse_chapters` / `recap_window_s` — a video's own leading recap chapter answers
  per video what `intro_recap_s` answers per channel, and overrides it in both directions (a
  shorter chapter window must narrow the trim too, or the override is just a bigger constant).
  Chapters ride in on the `--dump-json` call `fetch_meta` already makes, so this costs parsing, not
  quota; `_parse_chapters` drops a malformed entry rather than failing the fetch, since the list is
  author-supplied. `recap_window_s` returns `0.0` for "no answer here," leaving the constant in
  charge — the parent found no chapters at all on about half its corpus.
  `_RECAP_TITLE_HINTS` deliberately excludes `intro`: the parent shipped it, then removed it after
  a leading chapter titled "Intro" marked the first 26% of an educational upload as a position
  recap, on a channel configured `intro_recap_s: 0` — exactly wifey's setting for both live
  channels, so the unamended list would reproduce that defect here on day one. An introduction
  opens content; a recap replays prior calls, and only the second is what the window trims.
  "review" and 概述 are the same shape and are unmeasured — treat a sighting on either as this
  defect again, not a new one. The chapter window is the only trim that can fire here, both
  channels being at 0, so a false positive has no constant to fall back to and costs the whole
  trim. `_meta_from_cache` must coerce chapters back into `Chapter` objects, since `asdict`
  flattens them to dicts and a frozen dataclass does no coercion — without the coercion the field
  would claim `tuple[Chapter, ...]` while holding dicts, and `recap_window_s` would die on
  `chapter.title` at the first cache hit; mypy cannot see this because `**` builds the lie at
  runtime.
- `extract_frames` (one ffmpeg seek per caller-supplied `FrameMark`, never speculative; retries the
  whole download-and-seek on total failure only — 3 attempts spaced by `_FRAME_RETRY_BACKOFF_S`,
  since a partial result means those marks individually failed to seek)
- `fetch_video_batch` (per-video dedup cache at `.cache/video/<id>/asset.json`, randomized cooldown
  between network fetches only, one failing video never kills the batch)

`run`/`get`/`sleep`/`rng` injected so the suite is network-free. Backs `/ingest-video`, ported from
the parent (spec `docs/superpowers/specs/2026-07-28-ingest-video-design.md`).

**Run:** `PYTHONPATH=. poetry run python tools/video_fetch.py <url…> --batch --json [--force] [--min-delay/--max-delay S] [--cache-dir DIR]` (frame extraction has no CLI —
`/ingest-video` calls `extract_frames` directly after `video_marks.select` picks timestamps)

## video_marks.py — pure, stdlib-only frame selection

Pure, stdlib-only frame selection (transcript-driven, deliberately NOT scene-change — that
returns ~100 near-identical talking-head frames and misses the one annotated chart). Owns
`TranscriptSegment` (imported by `video_fetch.py`) and `FrameMark`.

`select(segments, item_ts, duration_s, *, cap=FRAME_CAP, window_s=DEDUP_WINDOW_S, sample_s=SAFETY_SAMPLE_S) -> list[FrameMark]` merges 4 trigger classes — item boundaries (pass-1
ranked candidate timestamps), deictic phrases ("look here", "这里", …), spoken price levels near
a symbol mention, a low-rate safety sample, and **tail anchors** (`tail_marks` — the safety grid
stops at `floor(duration/sample_s)*sample_s` and so structurally cannot reach the closing
seconds, which is exactly where a summary slide lives; `TAIL_OFFSETS_S=(2.0, 60.0)` spread wider
than `DEDUP_WINDOW_S` so dedupe cannot collapse the pair, and weighted at *item* tier so the cap
never trims them first) — dedupes within `window_s`, ranks by weight then earliness, caps at
`cap`.

Also owns `keep_items(candidates, *, cap=ITEM_CAP, min_specificity=MIN_ITEM_SPECIFICITY) -> (kept, dropped)` (2026-08-04) — the pass-1 cutoff, moved out of the skill's prose for the same
reason `video_calltime.py` was: a truncation rule stated only in a prompt drifts, and a silently
lost call is indistinguishable from a video that never made one. Total and lossless (every
candidate lands in exactly one bucket; each dropped row carries a `drop_reason`), ranked by
specificity desc then ts asc.

### `ITEM_CAP` tie-break bias

The tie-break has a measured directional bias: ts-asc means a tie at the cap resolves in favor of
earlier material, so a pundit who opens with macro and closes with single names loses the single
names first. Measured 2026-08-05: `ITEM_CAP=12` bound on all four @fenggemeigu videos (26/15/39/36
candidates), and on one video it dropped the NVDA setup and both GOOGL items despite those two
names being in the video's own title, because they sat past 1,090s behind a gold/DXY block. The cap
is doing what it is specified to do; a thin-looking Stream C yield from a dense video should read
as a cap artifact rather than as a video that made no calls. The floor binds first and stops a thin
video padding low-specificity vibes up to the cap just because slots exist; the cap binds on a
dense one.

A-priori constants: `ITEM_CAP=12`, `MIN_ITEM_SPECIFICITY=3`, `FRAME_CAP=15`,
`DEDUP_WINDOW_S=45`, `SAFETY_SAMPLE_S=300` — `ITEM_CAP` is bounded by
`ITEM_CAP + len(TAIL_OFFSETS_S) <= FRAME_CAP` (ceiling 13; past it kept items lose their frame
and silently degrade to `vision_confidence: "low"`).

**Run:** no CLI — pure library, called directly by `/ingest-video` (`select()` for frame marks,
`keep_items()` for the pass-1 item cutoff).

## multi_symbol_wfo.py — multi-symbol pooled-trades WFO sweep

For each (strategy × TF × day_filter) cell, runs `run_param_sweep` per symbol over a fixed
cohort and pools OOS trades across symbols by `tp_r`, picking the `tp_r` with the highest pooled
OOS `avg_r` (subject to `pooled_n ≥ 10`).

### Six `--cells` lists (hardcoded inline)

- `--cells t-a` — original T-A combined-direction multi-symbol-pending markers
- `--cells task-a` (default) — 4 directional-gap strategies × 3 TFs × 2 day_filters, including
  1wk
- `--cells inside-bar` — inside_bar audit: 6-cell directional sweep retiring the crypto-era
  `tp_r_long`/`tp_r_short` override
- `--cells pin-bar` — pin_bar audit: same 6-cell shape, retiring the `tp_r_long=5.0`/
  `tp_r_short=3.0` crypto override
- `--cells candle-resweep` — tp_r re-sweep at the PR #33 ATR multipliers (Phase 1: 3 candle
  patterns × 3 TFs × 2 day_filters = 18 cells; pair with `--fixed-atr` so each cell's
  `atr_sl_multiplier_<tf>` + `atr_sl_floor` flow from the TOML into `run_param_sweep` at sweep
  time)
- `--cells phase2-resweep` — Phase 2 of the same ATR-floor re-sweep (9 remaining strategies with
  `atr_sl_floor=true`: hammer_hanging_man, doji, morning_evening_star, bos, trend_day, orb,
  eqh_eql, ema, order_block; 50 cells total — 8 strategies × 3 TFs × 2 day_filters + orb × 4h ×
  2; also requires `--fixed-atr`)

### Flags

- `--direction {long,short,both,combined}` switches between pooling
  `BacktestResult.closed_trades` (combined) and `long_closed_trades` / `short_closed_trades`
  (directional, Task A); `both` prints all three tables per cell
- `--cell strategy/tf/day_filter` filters to one cell for spot-checks
- `--fixed-atr` (off by default) loads `atr_sl_multiplier` + `atr_sl_floor` per cell from
  `config_label`'s TOML via `analytics.signal_config.load_signal_config`; required for
  `candle-resweep` (without it, the sweep replays pre-PR-#33 SL geometry and the resulting tp_r
  winners are stale)
- `--live-parity` (off by default) replays the live gate stack (regime + direction_filter + F8
  HTF-EMA + ADR bias + cooldown) inside each cell's `run_param_sweep` so the reported `n`/`avg_r`
  reflect the live-filtered population instead of raw detector signals — the principled fix for
  the `wfo-filter-divergence` debt (loads each cell's config via `load_backtest_config`,
  force-enables the gates, pre-builds the regime/HTF-slope series per symbol via the
  `backtest_runner` helpers); the cross-strategy `conflict_resolver` is not applied since the
  tool sweeps one strategy at a time

**Run:** `PYTHONPATH=. poetry run python tools/multi_symbol_wfo.py [--cells task-a|t-a|inside-bar|pin-bar|candle-resweep|phase2-resweep] [--direction long|short|both|combined] [--cell strategy/tf/day_filter] [--fixed-atr] [--live-parity]`

## sync_parent.py — parent-repo PR triage for `/sync-parent`

Enumerates parent PRs merged since the memory-held sync pointer, groups them by the `(#N)`
squash suffix (the parent has **no merge commits**), translates each touched parent path to a
wifey target, and classifies SKIP / PORT / EVALUATE with an ALREADY-APPLIED confidence overlay.
Read-only on both repos except the state file, which only `--bump-to` writes.

The report is the triage artifact, so it is written in-repo:
`docs/plans/parent-sync/parent-sync-<date>.md` (gitignored via `docs/plans/`, directory created on
demand). Writing it to `/tmp` is unsafe: a `/tmp` clear once destroyed a report with 57 of 67 PRs
still undecided, forcing a full re-scan, since a reviewer decides PRs against this file over days
and it has to outlive a reboot.

The parent's checked-out branch does not matter. Every parent read is ref-based against
`origin/main` (`cat-file` / `fetch` / `log` / `show` / `rev-parse`) and nothing touches the parent
working tree, so the only real precondition is that `origin/main` resolves.

ALREADY-APPLIED reads symbols, and a modified symbol's name alone is not evidence: a signature-only
change re-emits its own `def` line, so the name sits on both sides of the diff and a
name-presence grep matches wifey's old copy too. One parent PR is the worked example — its entire
payload was two kwargs on an existing `route_target`, and the resolver returned HIGH /
ALREADY-APPLIED while wifey had only the two-arg version, a real missed port that stood until
closed by hand. `extract_symbol_changes` splits `added` from `modified`; a modified symbol's
evidence is the identifiers the change introduced (restricted to syntactically-used tokens, since a
bare `\w+` sweep harvests docstring prose and the wifey grep is repo-wide), and modify-only with no
new identifier reports UNKNOWN — "cannot tell," never "applied." Parsing is hunk-scoped: `git show`
without `--format=` prepends the commit message, whose prose has no diff prefix and otherwise reads
as context, subtracting the very identifiers the change introduced.

Two further limits to know before trusting a bucket. The classifier defaults to EVALUATE whenever a
path resolves, so at a wide range the counts degrade (measured 2026-08-11: 0 SKIP / 39 PORT / 107
EVALUATE over 156 PRs). And buckets cannot see portability: a mechanical check of whether the
touched files exist in wifey is the cheap filter, and it inverts the parent's own ranking — measured
2026-08-11, one `fix(xsmom)` PR touched 0 files present here despite wifey owning
`analytics/xsmom/`.

**Run:** `make wifey-sync-parent [FROM=<hash>] [FULL=1] [NO_FETCH=1] [BUMP_TO=<hash>]`. Never
`--bump-to` while the state file doubles as a memory — it rewrites the file to a stub and wipes the
triage body; hand-edit the frontmatter pointer instead. The tool itself must never print
`--bump-to` as a suggested next step (`format_report` and `main()` both name the hand-edit
instead, and the flag's own `--help` says `DESTRUCTIVE`) — a printed suggestion to run it would
destroy the very rulings the report exists to hold. `test_header_and_range` pins that the report
names `last_synced_hash` and does not contain `--bump-to`, so the presence half keeps the absence
assertion from passing vacuously.

The import-dependency filter (`docs/plans/scripts/missed_ports.py`) is gitignored, so nothing
mechanical proves it ran, and no lint, test or CI leg reaches `docs/plans/`. A shortlist built
without it is blind to every greenfield port — the class that hid `/ingest-feed` through two syncs,
because a file-existence check scores a new file ~0 by construction. Confirm it printed
`scan range: <from>..origin/main`: a dead filter and an empty result render identically otherwise.
