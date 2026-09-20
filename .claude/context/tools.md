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

Reports `(strategy × timeframe)` cells in **both** directions of the mismatch — the
data-driven half of the silent-surface enforcement (the static half is
`tests/test_makefile_invocations.py`). Both halves key off the same declared set,
`analytics.signal_config.declared_cells` (every strategy × its `strategy_timeframes`
override, else the config's `timeframes`), which lives in `signal_config` precisely so
the two questions cannot drift apart.

**Declared but dead** (`find_dead_cells`) — joins the declared set against `backtest_runs`
scoped by `day_filter` and flags any cell with **no runs** or **runs but zero
`total_signals`**.

**Rated but undeclared** (`find_orphan_ratings`) — the inverse. Scans
`confidence_ratings` for the config's TOML stem and flags any row whose `(strategy, tf)`
the config no longer declares, in every direction, worst-stars-first. `recalibrate` had no
notion of the current config and `upsert_confidence_ratings` never deletes, so a dropped
cell kept its stars and took a **fresh timestamp on a stale value** on every refresh: on
2026-08-06, `fib_golden_zone × 4h` sat at 3★ +0.4688 — second-highest-rated cell in the
whole `signal_watch` table — 2.5 months after the strategy left the config. Note the
asymmetry that let it survive: a dead cell surfaces as a zero and reads as *absence*, an
orphan surfaces as a number and reads as *evidence*.

Orphans carry a **tier** (`OrphanRating.tier`, from sister PR #608): `undeclared anywhere`
versus `declared by another config`. Both mean the rating's own daemon will never scan the
cell, so the tier **labels a finding and never suppresses one** — but the fix differs, and
the split is real here because `signal_watch`'s **22** declared cells are a strict subset of
`signal_watch_weekdays`' **32**, leaving **10** that only one config declares. `main` decides
the tier from `cells_declared_elsewhere(declared_by_config, config)`, which is extracted
rather than inlined because `main` itself has no test — an inlined union would have been the
one part of the tier decision nothing could falsify. The parent's direction-aware half was
deliberately **not** ported: it exists upstream because all three of its configs carry
`strategy_timeframes_long`/`_short` narrowing, and this repo declares no such key, so
`declared_cells` is direction-agnostic and a direction-aware check reports the same set.

The second case is the one that matters and the reason a plain "did it run?" check is not
enough: `signal_watch.toml` held **338** `1wk`/`tue_thu` rows with zero closed trades for
three months, so the surface *looked* covered (#139). Emptiness is indistinguishable from
coverage unless something explicitly asks.

`_KNOWN_DEAD_CELLS` is a `(day_filter, strategy, timeframe)` allowlist mirroring
`tests/test_lookahead.py::_KNOWN_LOOKAHEAD_DETECTORS` — **it should only ever shrink**, and
it reached **empty** on 2026-08-06 (its five entries were resolved; the diagnosis recorded
beside three of them turned out to be wrong, which the module's docstring keeps as a
caution). There is no allowlist for orphaned ratings: the fix is to prune them, not to
accept them.

Pure read (`read_only=True`). Exit 1 on any non-allowlisted dead cell **or** any orphaned
rating; `--strict` also fails on allowlisted dead cells, which is how you verify the list
can shrink. Inside `make db-update` it never blocks the refresh, but the completion banner
is conditional on it — it previously printed an unqualified `✅` beside nine orphans, one
of them displaying 3★.

**Run:** `make check-dead-surfaces` or
`poetry run python tools/dead_surface_check.py [--config PATH ...] [--db PATH] [--strict]`

## orphan_test_audit.py — test classes that NAME a unit but never CALL it

The third mechanical enforcement check, alongside `dead_surface_check.py` (data-driven)
and `tests/test_makefile_invocations.py` (static). It exists because a green suite proves
nothing about whether a test exercises its subject: `TestEvGate` held five tests that never
invoked the EV gate, because the gate was `def _passes_ev_gate` **nested inside**
`run_scan_cycle` and therefore unimportable. Every test re-implemented the comparison
inline; one asserted the defect as the expectation, another reduced to `assert None is
None`, and all five passed against any implementation (#150).

Two verdicts:

- **`not-importable`** — the subject matches a **closure** and no module-level callable.
  The unit is unreachable from a test, so the tests can only re-implement it. **Extraction
  is a prerequisite for the fix, not scope creep.**
- **`not-called`** — an importable callable matches but no test in the class calls it.
  Ordinary drift after an extract or rename.

Matching is token-based and **directional**: the callable's name must contain the class's
subject tokens contiguously and in order, so `ev_gate` matches `_passes_ev_gate` while
`p_r` matches neither. An earlier substring formulation produced **95** findings on a clean
tree, nearly all junk. A class whose subject matches nothing at all is deliberately not
reported — descriptive names (`TestWatermarkOnSend`) are legitimate and were most of that
noise.

`EXEMPT_CLASSES` is keyed `"<test file>::<class>"` and every entry carries its reason. The
three current entries are all the same false-positive shape — the subject reached **one
indirection away**, via a local test helper or a CLI `main()`. An entry without a reason is
how the check decays into a no-op, and a check that always prints the same findings is
ignored, which amounts to the same thing.

Heuristic, so it is **advisory and not part of `make test`** — unlike
`tests/test_schema_insert_arity.py`, which is deterministic and therefore runs in the
suite. Exit 0 by default; `--strict` exits 1 on findings. Verified against the pre-#150
tree, where it isolates `TestEvGate` and names `_passes_ev_gate`; clean on HEAD.

**Run:** `make check-orphan-tests` or
`poetry run python tools/orphan_test_audit.py [--strict]`

## post_branch_checks.py — every mechanical `/post-branch` check, in one run

Twelve checks that used to be **16 shell blocks embedded in `post-branch/SKILL.md`**, which a
session had to notice and copy by hand. That is the failure CLAUDE.md names as *a hand walk
is not the walk*, and it is why the same defects kept recurring: the skill's answer to each
one was more prose, and prose cannot enforce. Extracting them cut the skill from **1,649 to
~600 lines** and made the checks testable — `tests/test_post_branch_checks.py` gives each a
**positive control**, which the prose versions never had.

Checks: `queue-items` · `handoff-symbols` · `new-files` · `new-modules` · `new-targets` ·
`negative-claims` · `doc-indexes` · `md-atx` · `memory-cap` · `handoff-size` ·
`stale-anchors` (engine in `stale_anchors.py`, below) · `sensitive-terms` (the pre-flip
gate, ported from parent #658; asks the tracked tree, this branch's commit **content** and
its commit **messages**, because a flip republishes the whole history and no file edit
reaches a message. An absent `.claude/sensitive-terms.txt` is a FINDING reading
`NOT CONFIGURED`, never a SKIP, and terms are masked in the output).

⚠ **The three handoff-dependent legs — `queue-items`, `handoff-symbols`, `handoff-size` —
report `SKIPPED: no handoff file` rather than clean when the handoff is absent**, which it is
on a worktree or a fresh clone, the file being gitignored. `handoff-size` was built as a plain
result while its two siblings already skipped, so it read **green** there: the parent's #699
defect mirrored, and in the worse direction, since upstream returns a finding. One
`_handoff_leg` now carries the decision for all three — "no finding" and "no handoff" are
different states and only one of them is green.

`--text <file>` (`make post-branch-text FILE=<path>`) is a **fourth** surface for that gate
and runs alone, without any git surface: a PR title or body is neither the tree nor a commit,
so the three legs above report `clean` on one naming every term — correctly, and uselessly.
Repeatable, `FILE=-` reads stdin, exit 1 on a hit (make collapses that to its own 2, as with
`wait_ci`). It prints line numbers and a masked term and **never the matching line**,
because the match sits inside the very prose being screened. Hand-screening caught #245's
first draft naming all three terms, in the window the gate exists to make safe; the second
hand-check (#249) used a throwaway six-line loop, which is what this wires in. Belongs in
phase 5 — a posted body is public on landing and a later edit does not unpublish it.

⚠ **`negative-claims` read 15% of its own corpus for eight months, and nothing could see it.**
From the #218 extraction until 2026-08-26 the corpus query was `git grep -nI -e x`, written as
if the pattern meant "every line". It does not — it matches *every line containing the letter
`x`*, which is **1,892 of 12,277 non-blank corpus lines (15%)**. **46 of the 69 claim-shaped
lines then in the corpus carry no `x` at all** and had never been reachable, including `Makefile`'s "The 505-member
research universe has NO scheduled refresher" — a claim #265 falsified while this leg reported
nothing about it. Three things made it durable: every test injects a **fake runner**, so the
real argv was never exercised; the leg's quietness read as a well-tuned scope rather than as
blindness; and **every triage figure ever quoted for it** ("33 → 21 lines", "8 of 8", "0–5 to
1–11 per run") was measured on the truncated slice, so the numbers corroborated the defect.
⚠ **The error direction was flattering, which is why it survived** — a filter nobody declared
reads exactly like a corpus nobody wrote a claim into. `TestCorpusQueryReachesEveryLine` now
pins the argv and the behaviour separately, with an `x`-free line as the positive control;
only the argv leg goes red when the defect is reintroduced, because the behaviour leg's fake
runner cannot see it. On the fixed corpus the leg reports **13.2 findings + 18.2 soft per
run** against 8.5 while blind — **4.2× quieter per corpus line**. A finding now needs a
backticked or punctuated token; a hit on bare English ("there is no **state**") is demoted to
a named re-read note rather than dropped, and the subject is read to the LEFT of `has no` as
well as the right, since that is where the discriminating noun sits.

`negative-claims` narrows through `NEGATIVE_CLAIM_EXEMPT`, `(path, token)` → reason. A hit
is dropped only when **every** matched token is exempt, so one unexempt token still reports
the line and an entry narrows rather than deletes; the count is printed, never swallowed,
and `tests/test_post_branch_checks.py` fails a dead entry against the real file so the
allowlist keeps an external referent. ⚠ **The obvious alternative is the wrong one.** These
claim lines are 3–6 KB paragraphs carrying 49 and 113 tokens, so scoping on a window around
the regex match looks like the real fix; measured against the pre-#248 tree it would have
suppressed the leg's only true positive, where the regex matched
`gate_audit.py … not ported` and the sentence the branch falsified sat ~1,400 characters
earlier on the same line. The finding's value was a human re-reading the paragraph, which is
why the line stays the unit and `attribution` stays off the allowlist — on two of those
lines it is the claim's own subject.

⚠ **The phrasing allowlist was the leg's real hole, and it was invisible for the reason
allowlists always are.** Measured 2026-08-26 against the signal-timer branch (#261): of the
absence claims that branch falsified, it reported **0 of 8** — every one written in the plain
*"there is no X"* / *"X has no Y"* form, which is simply how absence gets written, and which
had no entry because no past incident had happened to use it. Two of the eight sat in
`deploy/`, outside `NEGATIVE_CLAIM_PATHS` entirely and so unreachable at any regex; the path
and the widening therefore ship together, since `deploy/` alone surfaces **zero** hits against
the unwidened regex. The tree's own emphasis convention hid one more — `has **no daemon at
all**` puts a bold marker mid-phrase — so the pattern carries an emphasis slot.

Three things bounded the cost, and each was measured rather than reasoned:

- **`has no` is anchored to a subject that is not a third party** — `wifey`, `this
  repo|fork|tree|skill`, `the fork|repo`, or any definite noun phrase that is not `the parent`
  or `the endpoint` — plus an intervening-adverb slot and a line-initial arm for a claim whose
  subject sits on the previous line. Bare, it matches mostly claims about what something ELSE
  lacks (*"yfinance OHLCV has no taker data"*, *"the endpoint has no children field"*), which
  no wifey branch can falsify; unanchored it measures **57.5 findings per run**. ⚠ The
  line-count figures once quoted here were measured through the `-e x` corpus filter and are
  gone rather than restated — see the corpus note above.
- **`claim_subject_tokens` is a scoping fallback, not a wider token list.** Widening the regex
  took claim lines carrying no backticked token — unscopable, therefore reported on *every*
  branch forever — from **0 to 11**, which would have made the leg permanently unclean. The
  fallback scopes such a line on its own subject noun, so it can only ever REMOVE a report.
  ⚠ **It is the opposite knob from #250's**: that one scopes lines IN wholesale. Fail-open
  survives where it is still earned — a subject that is all stopwords, or that runs off the
  end of its line, still reports.
- ⚠ **Price the TRIAGE LOAD, not just the catch.** On the fixed corpus the leg reports
  **13.2 findings + 18.2 soft per run** over the six branches to `7519f0b`, against 8.5 while
  it was reading 15% of the tree — **4.2× quieter per corpus line**. That is paid on every
  branch and is the standing argument against going wider: **a check that is never clean
  trains dismissal.**
  `TestTheLegIsCleanOnAnUNRELATEDBranch` pins the property that made it shippable — zero claim
  lines report unconditionally — against the real tree, so a future doc edit fails there. The
  fix is then to scope or exempt that one sentence, never to grow `_SUBJECT_STOP` until the
  number goes away.

Two are new and fix defects the prose form structurally could not:

- **`queue-items`** — nothing swept the handoff's own task list for work the branch just
  finished, so a completed item survived under a heading reading *"Settled — do not
  re-litigate"*, i.e. as an instruction to redo it. Confirmed three times. The earlier
  mitigation keyed on added Python **symbols**, so a docs-only branch defeated it entirely;
  this keys the handoff's own distinctive tokens against the diff **content**, which every
  branch has. It fired correctly on its own introducing branch.
- **`new-files`** — the prose probed `basename`, and every skill's basename is the shared
  constant `SKILL.md`, which matches CLAUDE.md's generic *"Skills live in
  `.claude/skills/<name>/SKILL.md`"*. A fabricated skill therefore reported COVERED — the
  exact false positive that check's own `-w` rule exists to prevent. `probe_names` probes
  the **parent directory** when the basename names a role rather than a file
  (`SKILL.md`, `README.md`, `__init__.py`, `INDEX.md`).

- **`negative-claims`** — shipped **unscoped** and was the third defect of this shape.
  `check_negative_claims` took no diff argument at all: it grepped the tree for absence
  language and reported every hit on every branch, so it returned the same 7 findings
  forever while the skill's table described it as asking about *"something this branch just
  added"*. Code and sentence disagreed and only the sentence was read — the same
  reachability-vs-scope gap as the fixture class, and its own test could not have caught it
  because **it had no test at all**, the only check without one. It now intersects the claim
  line's distinctive tokens against the diff's **added** lines (a removal makes an absence
  claim *more* true, so additions only), and the remainder becomes a `note:` rather than a
  finding. ⚠ **A claim line with no extractable token is REPORTED, not suppressed** — this
  leg fails open on purpose, because a miss ships a doc denying something now present.
  ⚠ **A check that is never clean trains dismissal**, precisely as a check that is never
  green stops being read; that is the cost the unscoped form was paying.

⚠ **Untracked files count as added.** `git diff` cannot see them in any form, so the
presence checks used to report zero on precisely the branch they existed for; the skill
answered that with "remember to `git add -A` first", one more hand-step to forget. Reading
`git status --porcelain` makes them correct either way.

⚠ **`new-modules` asks TWO questions, because a presence probe cannot answer the second.**
Where the context docs merely *mention* a package it asks whether the module is named at
all — the original word-boundary probe. Where they keep an **inventory** of one, it compares
the documented member set against `ls` and reports the **set difference**. The quorum is two
backticked siblings: below that the mentions are incidental prose, and the leg falls back to
the probe so every merely-mentioned package does not start firing.

**Why the second question had to exist.** The probe form reported COVERED on a real omission
(2026-08-21, wifey #255): `analytics/research_guards/sharpe.py` landed while
`.claude/context/analytics.md` enumerated **ten of the package's eleven** members, and the
word "sharpe" appears throughout that file as ordinary prose. The omission was found by
diffing the documented list against `ls` by hand. ⚠ **Tightening the regex cannot fix this
class** — the hit was a real token in real prose, so no boundary rule separates them; the
check has to change *what it asks*, not how precisely it asks it. Backticks are the
discriminator the bare probe lacked, because docs write a **file** as `sharpe.py` and a
**concept** as plain "sharpe", and only the former is a claim about the package's contents.

This is the `skill-claims` split in code: the mechanical half asks *does the artifact
exist*, the semantic half asks *does it still mean what the claim says*, and only the second
needs an **external referent** — here `ls`, something outside the check that the check can be
wrong about. Same shape as `/stats-dashboard`'s card **count** passing over a wrong inventory
and `missed_ports.py`'s undeclarable `PORTED` set. **A count, a presence probe and an
allowlist all pass on any error that conserves their own shape.**

Advisory by design — a finding is a candidate to dismiss in seconds, never an automatic
edit. The asymmetry is the point: a false positive costs a glance, a silent miss ships a doc
that enumerates every sibling but one and reads as complete. The Makefile is deliberately
**not** an enumerating doc; a build rule is not documentation.

**The full sweep closes by naming the `/post-branch` phases it does NOT reach**
(`UNCOVERED_STEPS` / `uncovered_notice()`), so passing the mechanical half cannot feel like
passing the walk. Upstream shipped this after finding **both** parallel sessions of one wave
substituting the sweep for the skill, *neither being careless* — its always-loaded tier carried a
sibling sentence licensing the substitution. **The fix belongs on reachability, not on another
rule**: a rule that competes with a nearby rule loses to whichever is read last.

⚠ **wifey cites `Phase N` where upstream cites `Step N`, and that divergence is deliberate.**
Upstream's phases are table rows declaring no headings, so a phase citation there is a dead anchor
its own `stale_anchors` correctly flags — hence its mutation test pinning the ABSENCE of the word
*phase*. Here the skill has real `## Phase N` headings **and** `tools/stale_anchors.py` resolves
`phase N` against them (`_HEAD_TYPED`), so the citation is a CHECKED anchor and the better one;
porting the upstream rule verbatim would have swapped a live reference for a vague one.
`TestUncoveredSteps::test_every_cited_phase_resolves_in_the_skill` is what makes that falsifiable.
**Port the rule, re-derive the reason.** Ported from parent #697.

The notice is suppressed for `--text` (which screens one composed PR body seconds before a
visibility flip, and wants no step list at the moment the operator is triaging under time
pressure) and for any `--check` run, which is a deliberate partial invocation.

**Run:** `make post-branch-checks` (passes `--exit-zero`), or
`PYTHONPATH=. poetry run python tools/post_branch_checks.py [--check NAME ...] [--exit-zero]`
to let it exit 1 on findings. ⚠ **`--check` is repeatable, and it was NOT until 2026-09-06** —
it was declared without `action="append"` while the `--text` flag on the next line had it, so
`--check memory-cap --check handoff-size` ran **`handoff-size` alone** and printed a
complete-looking clean sweep. That is exactly the two legs `/post-branch` phase 1 tells you to
re-read after phase 6, i.e. emptiness reading as coverage on the pair most able to hide a
finding. ⚠ **An unknown name now aborts the whole run** rather than silently shortening it: a
typo among several would otherwise run the survivors and report clean.
⚠ **The `PYTHONPATH=.` is load-bearing** — direct invocation without it dies
`ModuleNotFoundError: No module named 'tools'`.

## wait_ci.py — did CI settle, and did it actually RUN?

Two gates in one tool. `--pr <n>` (`make wait-ci PR=<n>`) waits on a PR's own checks; `--branch main
--min-jobs 5` (`make wait-ci-main`) is the **flip-back gate** — main's push run must reach a
job-count floor before the repo goes private again, because flipping kills whatever is created after
it and `Regression tests` is not created until ~4 minutes in.

The branch mode existed only as hand-rolled shell, at exactly the step with a documented trap. One
such waiter **was** wrong — it reported `jobs=0` against a live `total_count=2` — and the two traps
below are recorded against the PR gate as #194 and #195. Both are encoded here, so the flip-back
gate is the same tested tool rather than fresh shell each time:

- **A job-count FLOOR, never "nothing pending".** An empty result satisfies "no check is
  unresolved", so the naive loop exits instantly and renders identically to all-green. `is_settled`
  requires the floor **and** `completed == total`; dropping either half restores a real defect, and
  `TestIsSettled` pins both plus the vacuous input.
- ⚠ **A `gh` failure RAISES; it is never turned into data.** The previous `gh()` returned `""` on a
  non-zero exit, so an unreadable `actions/runs` response left every step count `None` and the tool
  printed **"all green, all executed real steps"** — a false green asserting exactly what it had
  failed to observe. That state is now exit **4**. Transient failures are retried inside the poll
  loop; an unrecoverable one propagates.

⚠ **The branch gate counts `push`-event runs only, and this was found by RUNNING it.** A `main` SHA
also carries a GitHub-managed `dynamic` run — "Configured Graph Update: pip in /." — created several
minutes after the push runs finish. Unfiltered, the first live run reported `jobs=6` where CLAUDE.md,
this file and the tool's own constant all say 5. Reading the code would not have shown it; the
characterization test `test_unfiltered_includes_the_dependency_graph_job` keeps the reason visible.

⚠ **`steps` prints as EXECUTED/DECLARED, because declared alone reads backwards.** A job behind a
`dorny/paths-filter` declares its whole step list on every diff and skips the body on most of them,
so a bare `steps=14` on a docs-only PR reads as "the heavy leg ran" — the opposite of what happened,
and it contradicts CLAUDE.md's paths-filter claim, which is correct. `step_counts` splits the two by
counting the steps GitHub reports as `skipped`; `?/N` means only the executed half was unobservable
and `?` means neither was. ⚠ **The billing discriminator is deliberately UNTOUCHED** — an exhausted
allowance declares nothing, so `steps=0/0` still settles it, and a test pins that it did not move.
This closes the hole memory `reference_ci_steps_counts_skipped` names: *a green job with a healthy
step count can have run nothing.* Ported from parent #673.

Exit codes: `0` green and observed · `1` genuine failure · `2` timeout · `3` billing (`steps=0`) ·
`4` settled green but step counts unreadable. ⚠ **`make` collapses all of them to its own `2`**, so
call the script directly when the code matters.

**Run:** `make wait-ci PR=<n>` · `make wait-ci-main` · or
`PYTHONPATH=. poetry run python tools/wait_ci.py --branch main --min-jobs 5 [--timeout-min N]`.

## stale_anchors.py — citations of a section number that no longer exists

The engine behind `post_branch_checks`'s `stale-anchors` leg. Document A cites a numbered
section of document B — `/post-branch` "Step 10b", `/sanity-check` "§4a" — and B
later renumbers itself. Nothing noticed: this recurred **three times**, the third caused by
the branch that shipped `/sanity-check`, and no existing check could see it because
`handoff-symbols` keys on symbols and **a section number is not a symbol**.

⚠ **It is a library, not a CLI — there is no `__main__` and no argparse.** A bare
`poetry run python tools/stale_anchors.py` prints nothing and exits **0**, which is
indistinguishable from a clean sweep. Reach it through `make post-branch-checks`, which is
what passes scope. (Found by the sibling repo running the ported copy this way and reading
the silence as a pass — the same *a SKIP is not a PASS* class as a leg that degrades to
`SKIPPED` when a symbol it references no longer exists.)

Three decisions carry it:

- **Quoting is tested as an enclosing SPAN, never as the two adjacent characters.** A
  quotation marks a *mention* rather than a *use*, and the adjacent-character form carried
  one bug in each direction: it missed a quotation wrapping target-plus-anchor as one phrase
  (a false positive, which fired on this repo's own prose describing the check), and because
  `_QUOTES` is a `str`, `"" in _QUOTES` is `True` — so an anchor ending the line
  short-circuited to "quoted" and was dropped **in silence**. Only the end-of-line branch was
  ever reachable: `citations()` calls `_anchor_after` with `start = t.end()` of a target that
  must precede the anchor, so `begin == 0` cannot occur. An unterminated quotation now yields
  no span and therefore reports, because a false positive costs a glance while a suppressed
  citation is invisible.
- **Kinds must agree, unless the citation is untyped.** `§4a` names "the section numbered
  4a" without claiming a kind, so it matches any declaration; `Step 6` and `Phase 6` are
  typed and must agree. A label-only comparison would call those two a match — and
  Step → Phase is the exact rename that keeps breaking.
- **The ordered-list fallback is CONDITIONAL**, and the real corpus is what forced it. A
  document with no numbered heading (`/db-update`, `/ingest-x`) numbers itself through its
  column-0 ordered list, so those items are its steps. But `/post-branch` says *"Phases, not
  step numbers"* while carrying three column-0 **rubric** lists numbered 1..5 — harvesting
  those unconditionally would have silently validated every dead `/post-branch` "Step N"
  citation, i.e. blinded the check to its own founding defect.

⚠ **Scope is wider than the repo**, and the build measured why: of the **6** dead (or, under
the mutation below, would-be-dead) citations observed, **2 sat in the memory tree** — which no
repo-scoped check can reach. So this runs over `.claude/`, the four current-state files *and*
`memory/*.md`. It is also why the check is not in CI-gating `sanity_checks`: CI cannot see the
memory tree at all, and the named hole below would make a gate red by construction. Dated trees
are excluded (`is_dated_path`) on the fork-drift leg's reasoning — a citation in a dated record
was correct when written, and the hand sweep that preceded this found 2 such correct ones.

⚠ **Do not repeat the pre-build claim that "two of four live instances were in the memory
tree".** The handoff that filed this check said so, but its own enumeration lists CLAUDE.md,
two handoff blocks and one memory file — i.e. **one**. The figure above is this branch's own
measurement and is reproducible; the inherited one is not.

**First run found 3 real dead citations** the hand sweep had missed: `/ingest-x` "step 5"
cited twice (`.claude/context/tools.md`, `/ingest-video`) when that skill's Flow stops at
step 4, and a surviving `/post-branch` "step 10b" in
`memory/feedback_handoff_prompt_location.md`.
Mutation-checked against the live tree by renumbering post-branch's `Phase 6` heading, which
surfaced 3 further live citations — one of them in the memory tree, which is the proof that
the wider scope is load-bearing rather than decorative.

⚠ **Known hole, named rather than papered over** (`TestKnownHoles`): a document's own
sub-label sitting beside another document's name is syntactically indistinguishable from a
citation of it — `/ingest-x` wrote *handed to `/ingest-video` (1b)* where the label 1b was
`/ingest-x`'s own. Suppressing that by "the source declares this anchor too" was built,
measured, and **reverted**: documents share small integers, and it dropped a real cross-doc
finding. Only target-then-anchor is read, within a bounded window, so a citation far from
its target is invisible.

**Run:** via `make post-branch-checks`, or
`PYTHONPATH=. poetry run python tools/post_branch_checks.py --check stale-anchors`.

## claude_home.py — the one derivation of this checkout's memory tree

Five call sites derived the Claude project directory independently — `cadence_check`,
`post_branch_checks`, `sync_parent`, `deploy/backup-analytics.sh` and the Makefile — and every
one of them was wrong after the move to the Windows host. Two carried the old Linux box's
absolute home as a tracked literal, so no environment variable could rescue them; the other
three folded `/` alone, which leaves a `C:\Users\…` path untouched and yields a "slug" that is
itself drive-absolute.

⚠ **The class never raises, and it fails toward ABSENCE.** An unresolvable memory tree reads as
absent to every consumer: the backup script warns and records `files: 0`, `cadence_check` prints
a note, `post_branch_checks` measures the MEMORY.md cap against a file it never found, and
`make status` printed `?`. So it reads as *nothing to do* on a host where the tree is present
and merely unlocated — which is how it survived a migration whose brief already listed the slug
remapping as a restore step.

⚠ **`slugify_path` takes TEXT rather than a `Path`, and that is the testability decision.**
Linux CI can then assert the Windows rule and a Windows box the POSIX one. A `Path` argument
resolves against the running host, which makes exactly one of the two assertions unwritable —
and the one you cannot write is the one that breaks. Both separators and the drive colon fold,
so `C:\Users\User\repo\x` → `C--Users-User-repo-x` and `/home/kng/repo/x` → `-home-kng-repo-x`.

⚠ **Root selection tests `projects/<slug>`, never the config ROOT's existence — and the first
version got this wrong.** It probed whether `~/.claude-personal` existed and took it if so, which
shipped broken within the hour: that directory appeared on the dev box while both profiles were
in use, the probe chose a root that had never held this project, and every consumer went straight
back to reading ABSENT against a tree that was present under `~/.claude` all along. **Both roots
can exist; only one holds the tree.** This is the repo's own recurring lesson landing on the fix
for it — *a check is only true about the scope it looked at*. Root existence is a proxy; the
project directory is the thing wanted, so it is what gets tested. Order is the tie-break and only
the tie-break: with a tree under both, the more specific `.claude-personal` still wins.
`CLAUDE_CONFIG_DIR` collapses the candidate list to one, because an explicit setting must not be
second-guessed by a probe. `claude_home()` is *derived from* `project_dir()` rather than computed
beside it, so the two cannot disagree about which root won. No tree anywhere falls back rather
than raising: every consumer already degrades to a printed note, and raising would cost the
backup rather than the report.

**Run:** nothing — it is a library. `tests/test_claude_home.py` pins both platform rules, the
probe order and the fallback; `tests/test_backup_local_coverage.py` pins that the shell script
and the Python consumers resolve to the *same* place, which is the coupling that drifted.

## cadence_check.py — which recurring tasks are overdue

`make cadence-check` reads `docs/plans/task-marks/` — one file per task holding an ISO-8601 UTC
timestamp — and reports what is past its period. `make cadence-stamp TASK=<slug>` records a run.
Ported from the parent's `daily_check.py` task-mark block; the parent's other ~1,650 lines are
crypto-specific (signal-watch mtime, the xsmom executor, a majors cross-section) and were not
taken.

**Three divergences from upstream, each because the parent's REASON does not hold here.**

- **The checker is TRACKED.** Upstream's lives under gitignored `docs/plans/` and its own
  docstring admits a reclone loses it. That is the failure wifey fixed on 2026-08-20 by inverting
  `.claude/` to a denylist, so repeating it here would import a defect. The **marks** stay
  gitignored — they are per-machine state, and `docs/plans/` is already covered wholesale by
  `make backup`.
- **The mark's CONTENT is authoritative, not its mtime.** Upstream writes an ISO line and then
  reads `st_mtime`, so the content it carefully writes has no consumer — the same self-referential
  shape that cost the handoff its `Line count:` stamp. mtime also moves for reasons that are not
  runs (an editor open, a restore that does not preserve times), and that failure direction
  reports **fresher than reality**. An unparseable mark here reads as OVERDUE, never as fresh.
- **Stamping is a flag.** Upstream tells each skill to run `date -u +%FT%TZ > …/<task>`; a redirect
  typo silently writes the wrong file and a missing directory fails the write. `--stamp` creates
  the directory and **refuses a slug that is not a declared task**.

⚠ **A MISSING mark reads as OVERDUE on purpose** — the fail-safe direction, since the opposite
mistake reports "fresh" for a task that has never run once.

⚠ **ADVISORY, and it must never enter `make test` or a CI job.** The marks are gitignored, so a
fresh clone sees every one absent and would report every task permanently overdue. **A check that
can only be red in CI is worse than no check.** `--exit-nonzero` exists for a human who wants a
shell condition.

The four inclusion rules — rots silently · named consequence · one cheap field · exactly one
action clears it — and the reason each rejected candidate fails one (`make backup`, `make
go-live`, `/db-update`, `/ingest-feed`) live beside `TASKS` in the module, so the table cannot
grow into noise without someone stating which rule the new line satisfies.

**The audit-verdict → SoT ownership join rides in the same report** (parent #641's other half,
its `daily_check.py` § 6b). An audit whose verdict recommends action and that NO SoT row names is
a finding with no owner — the parent measured its only BUILD verdict in 47 audits sitting unowned
for seven weeks in a generated, test-enforced index, because a research chain of audits has an
owner at every link except the last: each link's owner is the next audit, and the terminal
recommendation is owned by nobody. It reads `docs/audits/INDEX.md`, never the audit bodies — the
index's currency is already gated by `tests/test_docs_index.py`, so a stale index reds that test,
not this line. **Ownership = a SoT row naming the audit's FILENAME**, open or closed — "is it
implemented" needs a judgement no string match can make, and green being reachable two ways (do
the work, or record where it was already done) is what keeps this from becoming an amber nobody
believes. It is NOT a `Task`: no mark, no cadence — an observed-state join, hosted here because
the audits are in the repo, the SoT is in `~/.claude-personal`, and no pytest can see both.

Three divergences from the parent's join, same discipline as the block above. The **predicates are
re-derived** against wifey's FOUND / BOUNDED / EXCLUDED / BLOCKED taxonomy — the parent keys on
BUILD / NO-EDGE, words wifey audits never say — and `INSUFFICIENT` is deliberately OFF the settled
list, because the corpus's one live SUPPRESS-CANDIDATE rides in an "INSUFFICIENT on 11 of 12
cells" verdict and a global veto would silently skip exactly the row carrying a recommendation
(pinned by a test). **Rows match by date shape, not the parent's `| 2026-` prefix**, which goes
blind at the new year with every row silently dropped — and a 0-rows parse prints as parser
drift, never as clean. **The predicates are under real pytest** (`tests/test_cadence_check.py`,
including the parent's decisive strip-the-owner mutation and an empty-SoT positive control on the
committed index): the parent's module executes on import, so its proof is a hand-run sibling that
duplicates the regexes and must be edited in lockstep — this file is importable, so drift between
code and test is structurally impossible. The blind bracket (`31/41 readable, 10 state it in a
table`) prints because a green line is a claim about the readable rows only.

## backup_check.py — is the newest snapshot actually recent?

`make backup-check` runs `tools/backup_check.py`, which reads `$WIFEY_BACKUP_ROOT` (default
`~/backups/wifey`) and reports the **age of the newest verified snapshot**. It is the observed-state probe that `cadence_check.py`'s own
exclusion note points at: `make backup` fails that tool's inclusion rule (4), because a scheduled
`wifey-backup.timer` clears it and no human action does — but its real risk was never "a human
forgot", it is **the timer stopping silently**, and a mark cannot see that.

**Why a mark could not do this job.** Alerting is failure-only (`OnFailure=` starts
`wifey-alert@`), which makes the channel unfalsifiable: a timer with nothing to report and a timer
that stopped firing are indistinguishable from the Telegram side. `deploy/README.md` states the
transferable rule — **a scheduled job can only attest to the step it performs** — so for a green
light to mean "the data is current", something has to check the *input's* age rather than the
copy's exit code. This reads the tree the off-site leg copies **from**.

⚠ **The two tiers are DIFFERENT ARTIFACTS and are graded separately.** `daily/` holds verified
snapshots each carrying `MANIFEST.json`; `weekly/` holds a format-independent **parquet export**
and carries no manifest at all, by design. The first draft graded them together and so reported
every weekly dir as a malformed snapshot — a permanent warning about a directory that was exactly
as the backup script intended. **A check that is never clean stops being read**, which is why the
daily tier alone decides the verdict and the archive is reported beside it as informational
against its own 7d bar. Pinned by `TestTiersAreDifferentArtifacts`, whose control asserts that a
manifest-less dir under `daily/` **is** still flagged, so the exemption cannot widen.

**Four load-bearing properties**, each mirroring a defect this repo has already paid for:

- **The manifest's CONTENT is authoritative, not the directory's mtime.** `captured_at_utc` is read
  from inside the file, exactly as `cadence_check` reads a mark's content — mtime moves for things
  that are not runs (a restore that does not preserve times, an editor, an `rclone` round-trip) and
  it fails in the direction that reports **fresher than reality**. The weekly tier has no manifest,
  so it falls back to the date the script stamped into the **directory name**, still a recorded
  decision rather than a filesystem side effect. `test_a_fresh_mtime_cannot_rescue_an_old_manifest`
  is the only test separating the two fields; a mutation adding an mtime fallback fails 8 tests.
- **Every unreadable state reads as STALE, never as fresh** — a missing manifest, a corrupt one, a
  missing field and an unparseable timestamp all funnel to the same verdict, matching the off-site
  script's stance that a missing `MANIFEST.json` is a fault rather than "nothing to do".
- **An ABSENT root is its own verdict** (`NO BACKUP ROOT`), not a stale one. Collapsing them prints
  the milder of the two, and they want different actions: "the timer broke" versus "this machine
  never backed up at all".
- **ADVISORY, and it must never enter `make test`, `make sanity-checks` or a CI job** — the backup
  root is machine-local single-copy state no clone has, so CI would report a missing backup
  forever, the same structural reason `cadence_check` stays out. `--exit-nonzero` opts in.

⚠ **Two holes, named rather than papered over.** It measures the **LOCAL tree only** and cannot see
whether the off-site mirror received the snapshot — that needs a network `rclone` call, and a probe
that fails when the laptop is offline reports a backup problem for a connectivity one. The off-site
leg's own success plus a fresh source here is the two-part answer, and neither half is sufficient
alone. And **it refuses nothing**: wiring a staleness refusal into `deploy/backup-offsite.sh` is
the second candidate fix in `deploy/README.md` and stays a deliberate user call, because a guard
that costs you the backup is worse than the gap it closes.

## freshness_check.py — did the scheduled work actually run?

`make freshness-check` runs `tools/freshness_check.py`. Same premise as `backup_check.py` — all
three `wifey-*` units alert through `OnFailure=wifey-alert@%N.service`, and that channel cannot
tell a quiet timer from a stopped one — applied to the two surfaces the backup probe cannot see.
Ported from the parent's `ohlcv_freshness.py` (#681/#698 — that path exists upstream, not here),
whose motivating measurement was **22 of
25 universe symbols frozen for eleven weeks, all `TRADING`, nothing broken and nothing watching**.

### The port's one hard divergence: sessions, not wall-clock

The parent computes `age_bars = (now - newest) / bar_ms`. That is correct on a 24h tape and **wrong
here in the direction that reds everything forever**: `4h` RTH is 2 bars/day, not 6, so a healthy
two-session-old series reads ~12 bars behind, and every Monday adds a phantom weekend on top. Wifey
counts NYSE sessions via `analytics/trading_calendar.py::nyse_sessions` and multiplies by
`cost_model.BARS_PER_DAY` — the single shared table, imported rather than forked, so a change there
reaches this too. `TestRthDivergenceFromParent` pins it, which is what makes a future "tidy-up"
back to the parent's formula fail in the suite rather than in production.

`sessions_elapsed` floors at zero, so a bar stamped in the future cannot read as freshness with
room to spare — that is a different defect and must not be laundered into a green.

### Why the watermark is dated but UNGRADED

The first build graded the `signal_state.json` watermarks against the timer's daily cadence and
printed **STALE at 4 sessions on a healthy system**, with run evidence on **two of the three**
intervening sessions (`fired_at_ms` rows on 08-24 and 08-25). ⚠ The third (08-21) carries no row,
which is **not** evidence of no run: a scan detecting nothing new writes nothing, so this channel
can confirm a run happened and can never confirm one did not.
The watermark advances on **dispatch**, not on every run, and dispatch is intermittent by design:
`day_filter = tue_thu` suppresses on the bar's open weekday, so a Mon run (Fri bars) and a Tue run
(Mon bars) can never alert. There is deliberately **no signal-side tolerance constant** — the
cadence is a consequence of `day_filter` rather than a schedule, so any constant would lack an
external referent, and a hand-picked one reds a healthy system.

The module's own stated principle ("staleness is meaningless without a declared cadence") is what
condemned its first draft. **Run-liveness moved to the ohlcv leg**: a `go-live` run's first act is
a watchlist sync, so fresh watchlist bars *are* the evidence a run happened, and that quantity has
a cadence. The signal leg's only finding is **no watermarks at all** — a scan has never run here,
or the state file is unreadable. Primary and `:wife` are dated separately because they mean
different things; a primary mark ahead of `:wife` is the normal resting state, not a fault.

### Two tiers, because grading everything trains dismissal

A series is **scheduled** when some declared `Cadence` covers it. Two exist: the **watchlist**
cadence (`wifey-signal-watch.timer`, `4h`/`1d`, one fire per trading day) and the **universe**
cadence (`wifey-universe-sync.timer`, `4h`/`1d`/`1wk`, one fire a week). When both cover a series
the **tightest gap wins** — a watchlist name is refreshed daily whether or not the weekly timer also
touches it, so the tight bar is both achievable and the only one that would notice the daily timer
stopping.

⚠ **Whether the universe tier exists at all is read from the BOX, not asserted here.**
`universe_timer_enabled` asks the host's own scheduler, and `main` passes the ACTIVE members to
`evaluate_ohlcv` **only** when that returns enabled. The units are opt-in and nothing in the repo
installs them, so "the universe has a cadence" is true on one machine and false on the next;
hardcoding either answer is wrong on half of them. This is the tool's one piece of non-DB observed
state, and it exists because the file previously asserted "nothing refreshes the 505-member research
universe" as a **constant**, which stopped being true the day a timer was written.

⚠ **It DISPATCHES per host, and a non-Linux box is no longer one of the failure branches.**
`host_platform.is_windows()` picks the reader: Linux shells out to `systemctl --user is-enabled`
and accepts `enabled`/`enabled-runtime`; Windows runs `Get-ScheduledTask -TaskPath '\wifey\'`
and accepts `Ready`/`Running`. `task_name_for_unit` is the one transform between the two naming
schemes (`wifey-universe-sync.timer` → `wifey-universe-sync`), shared with
`deploy/windows/install-tasks.ps1` and pinned by a test, because a probe looking in the wrong
folder returns "not enabled" rather than an error and so is indistinguishable from a box that
installed nothing.

⚠ **On Windows the field is `State`, never existence.** `install-tasks.ps1` registers
`wifey-backup-offsite` and then **disables it on purpose** — `rclone sync` mirrors deletions, and
until this host has its own remote a scheduled run could mirror an empty local tree over the
snapshots. So "the task is there" and "the task will fire" are genuinely different answers, and
only the second licenses grading a cadence.

⚠ **Why the non-Linux branch had to stop degrading to False.** It used to be correct:
`systemctl` is absent on Windows, the `OSError` branch returned False, and the leg reported the
absence — right up until the host moved and the job was registered with Task Scheduler instead.
From that moment the same False would have meant "no cadence" about a job running every Saturday,
and the leg would have stayed **silent forever on the one host it was newly wrong about**.
*Degrading to the safe answer is only safe while the safe answer is also the true one.* Genuine
failures — no `systemctl`, no PowerShell, a timeout, an unregistered task, a permission error —
still degrade to **not enabled**, the direction that cannot invent faults.

Measured 2026-08-26, both worlds on the same tree: **timer off → 26 graded, 1131 unscheduled**;
**timer on → 1115 graded, 42 unscheduled, 4 findings**. All four were true positives (`SATS`
delisted, `EA` wound down post-acquisition), which is the point — grading unconditionally on a box
with no timer would have printed ~1,100, and a leg that is never green stops being read, the same
argument that bounds `negative-claims`' triage load.

⚠ **Those four findings are no longer REACHABLE, and that is the fix rather than a regression.**
`EA`, `EQR` and `SATS` were flagged `delisted` on 2026-09-02, and `read_universe_symbols` now
excludes delisted members — because `analytics_runner` resolves `--universe` through
`active_symbols()`, so nothing refreshes a delisted name **by design**. Grading it anyway prints a
permanent STALE for a decision, which is the same never-green failure this tier's timer probe
already guards against, one level down. The counts above therefore describe the pre-flag tree; the
graded population is now the 502 active members.

⚠ **A weekly bar cannot be graded on the daily footing.** A `1wk` bar stamps on the week's Monday
open and closes Friday, so a perfectly refreshed weekly series trails a daily one by four sessions
for reasons that are not staleness. `tolerance_sessions_for` is `base + cadence gap +
max(0, sessions_per_bar - 1)`, with `sessions_per_bar` the reciprocal of `cost_model.BARS_PER_DAY`
— imported, never a new constant. Without the third term every weekly series reds forever, which is
**the parent's wall-clock failure mode arriving by a different route**: the port already fixed
wall-clock-vs-sessions and this is the same error one level down, in bar span rather than clock.
`4h` and `1d` close inside a session, so the term is 0 for both and their shipped tolerances did not
move — pinned by a regression test, because that is the half a reader would not think to check.

⚠ **The residual absence is still a hazard, and the report still says so.** The pundit ledger has no
timer at all, and a pooled cross-section that reaches unrefreshed names mixes stale series with the
fresh watchlist **inside one query** — the fresh set being precisely the mega-cap tilt `4h`'s 21%
coverage already carries, so recency skew and coverage skew compound in the same direction. Nothing
errors; `n_eff`, breadth counts and any `1wk` panel are just quietly wrong.

An **empty** member set — an absent or unreadable watchlist, or a universe timer that is not enabled
— puts those series in the unscheduled tier. That degrades toward "nothing was graded", which
reports an absence, rather than toward "everything is graded against a cadence it does not have".
`universe_symbols` **defaults to empty** for the same reason: a caller that has not checked the
timer must not get the graded tier by accident. Every reader (`read_watermarks`,
`read_scheduled_symbols`, `read_universe_symbols`, `read_series`) degrades the same way;
`read_series` returns None on a lock conflict too, because a writer holding the DB is not a finding
about the data.

⚠ **ADVISORY, never in `make test` / `make sanity-checks` / CI** — both legs read machine-local
single-copy state no clone has. The **pure grading half** is in `make test`
(`tests/test_freshness_check.py`), positive control included: `TestScheduledTierHasTeeth`
constructs a stale scheduled series at wifey's real 2026-06-18 freeze date and asserts it is
caught, plus the other half — that a fresh one is not — because a check that has never been red is
indistinguishable from one that cannot be. `--exit-nonzero` opts in for a shell condition, and the
unscheduled tier deliberately cannot make it fire.

## host_platform.py — which scheduler this box actually has

`tools/host_platform.py` is one predicate, `is_windows()`, wrapping `sys.platform`. It exists so
the host test has a single name rather than a `sys.platform` comparison re-spelled at each call
site, and so a test can monkeypatch one symbol instead of the interpreter's own attribute.

Its consumer today is `freshness_check.universe_timer_enabled`, which reads systemd on Linux and
Task Scheduler on Windows (see that section). ⚠ **The transferable rule is in why it was added,
not in what it does**: the probe used to treat "not Linux" as a failure and degrade to *not
enabled*, which was true while no non-Linux box could run the job at all, and became silently
wrong the moment `deploy/windows/install-tasks.ps1` could register one. A platform test only
belongs behind a named predicate once the platforms genuinely differ in answer rather than in
availability.

## clone_preflight.py — does the suite pass on a machine that is not this one?

`make preflight` clones HEAD into a temp dir, runs `poetry install --no-root` against the clone's
own lock, and runs `make test`'s exact argv there. Ported from parent #667.

**It REPLACES that branch's `make test` rather than adding to it** — `PYTEST_ARGS` mirrors the
`test:` recipe argument-for-argument, and `TestWiredIntoTheWorkflow` pins that against the
Makefile so the claim has an external referent instead of only the sentence asserting it.

**What it catches that no local run can.** A gitignored path that exists on this box and nowhere
else is invisible to every check that runs here — `config/stocks.json`,
`.claude/sensitive-terms.txt`, `docs/plans/` and `analytics.db` are all absent on a clean clone.
Two defects share that symptom and a prose rule only addresses the first: **(a)** a test depends
on a local file, so CI reds; **(b)** *production* code loads a local file it does not need, so
the CLI is broken on a clean clone while a hermetic test passes anyway. wifey has already paid
for this class in the other direction — `.claude/` was an allowlist until 2026-08-20, so the
hooks were untracked and silently did not survive a reclone.

⚠ **CI already IS this gate**, being a clean checkout. What this closes is **TIMING, not
detection**: on a private repo, detection after a push costs a metered Actions cycle, a red PR,
and a visibility flip to read the failure at all.

**The dirty-tree refusal is the load-bearing part.** A clone sees **committed** state only, so a
run against an uncommitted tree tests stale HEAD and reports GREEN — the same invisible pass the
gate exists to kill. It refuses **before taking any clone**, and `test_refuses_before_taking_any_clone`
asserts the clone's absence rather than only the exit code. That is also why it belongs in
`/post-branch` **phase 5**, after the doc commits, and not in phase 1's sweep.

Two cheaper tricks were refuted upstream and the reasoning is structural, so it ports: **a foreign
working directory** makes every relative path absent at once, committed assets included (27
failures, almost none the bug); **monkeypatching the `DEFAULT_*` constants is partial by
construction**, since `DEFAULT_DB_PATH` is re-exported into two modules that captured it at import
— a shape wifey shares via `analytics.store` and `analytics.data_store`.

⚠ **Two holes, stated because a gate whose reach is unknown gets over-trusted.** It cannot see an
**absolute** default (`$HOME/…`), because `$HOME` is identical in the clone — `EXTERNAL_ROOTS` in
`deploy/backup-analytics.sh` is exactly that shape. And it only reaches class (b) where a *test*
exercises the path; neither mechanism sees an untested CLI branch.

Exit codes: **0** pass · **1** the suite failed, a real finding · **2** REFUSED, dirty tree · **3**
INFRA, the clone or install died. ⚠ **`make` collapses all of them to its own 2**, so branch on
the printed banner or call the module directly.

**First run, 2026-08-20: PASSED** — **3123 passed / 4 skipped** in the clone against **3124 / 3**
locally, so wifey's suite is clean-clone-safe. ⚠ **The one-test delta is the finding.**
`test_pundit_score.py::test_live_ledger_rows_all_survive_the_new_guards` guards
`docs/plans/pundit-calls.jsonl`, which is gitignored — so it runs **only** on the operator's box,
and **its assertion has never been evaluated by CI and never can be**. The code says as much
(`# gitignored; absent on a fresh clone`), so this is by design rather than a defect; what is new
is that the asymmetry is now *observable*. Locally the test silently passes, in CI it silently
skips, and **neither surface reports that it ran nowhere meaningful** — which is precisely the
shape the gate exists to expose. Suite portion **295s** in the clone against **254s** locally
(+16%), before the clone and `poetry install`.

**Run:** `make preflight`, or `python3 tools/clone_preflight.py [--repo R] [--dest D] [--dry-run]`.
Bare `python3` on purpose — stdlib-only, so the gate still runs when the dev venv is the thing
that is broken.

## sanity_checks.py — every mechanical `/sanity-check` check, in one run

Eight checks: `fork-drift` (invocable artifacts a doc names but the code lacks — make targets,
timeframes, `--strategy`, `SYMBOL`), `parent-leakage`, `missing-paths`, `context-coverage`,
`router-wiring`, `config-strategies`, `cli-documented`, `regression-surface`.

`regression-surface` reads the globs CI's regression paths-filter fires on straight out of
`.github/workflows/lint.yaml` and asserts CLAUDE.md names each one. ⚠ **It keys on the block
mentioning `tests/test_regression.py`, never on a job name or a position** — there is a second
`filters:` block in that file (the frontend one) and a positional read silently grades the wrong
one; the first draft's regex did exactly that, matching across blocks because `\s+` spans
newlines. ⚠ **An empty filter is a FINDING, not a pass**: if the workflow moves, the leg must say
it can no longer see what it grades rather than reporting clean against nothing. The comparison is
**verbatim** for a reason — until 2026-08-26 the doc list diverged in BOTH directions (narrower on
`analytics/` and `config/`, silent on four paths, *wider* on `tests/fixtures/`), and a paraphrase
(`analytics/backtest/` for `analytics/**/*.py`) is not diffable by any tool. ⚠ **Only the
narrowing direction is harmful** — over-running the gate costs ~8s, under-running it costs a
metered Actions cycle. The first draft of this note called the list a *strict subset*; the claims
audit caught it, which is the audit working on its own branch. Ported from parent #698 (ST89). Same shape as `post_branch_checks.py` —
pure functions over text, git injected as `runner`, one `Finding` per thing a human must look at —
with two deliberate differences.

**It GATES rather than advises**, and it runs in **two** CI places. `tests/test_sanity_checks.py`
asserts the working tree is clean, which puts it inside `make test`; and CI's `markdownlint` job
runs it **unconditionally** as well. The second placement is not redundancy: the test job sits
behind a `**/*.py` paths filter, so on a docs-only PR — the exact change these checks guard — the
pytest gate never fires at all. CLAUDE.md's *a self-check outside CI is not a check* is what forced
both.

**Every leg is CI-portable, and that constraint is what found the prose form's two bugs.** The old
§4a shell block read the gitignored `config/stocks.json` directly, so it would have crashed in a
clean checkout, and its `MISSING` allowlist was calibrated on a developer machine where
`config/youtube_channels.toml` happens to exist. Now `check_missing_paths` asks **git** whether a
path is expected to be absent, the watchlist leg degrades to a printed note, and the three legs
needing project imports report `SKIPPED` where nothing is installed. ⚠ **A degraded leg is a note,
never a finding** — counting it would leave the sweep permanently red in CI, and a check that is
never green stops being read.

⚠ **`parent-leakage` is scoped to `.claude/` while its siblings are not**, and that asymmetry is
load-bearing. Widening it to CLAUDE.md / README / `docs/system-overview.md` returns four hits that
are all *correct history* — the fork-lineage paragraph, the sister-memory pointer, the README's
"forked from" line — which is the prose-marker grep that was built, measured and rejected. A skill
instructs an *action*, so a parent artifact there is invocable rather than historical. Pinned by
`test_scope_stops_at_dot_claude`.

Extraction found three defects in the inherited code, none by reading:

- The skill's documented expectations were stale in **two of three** legs — it claimed "no leakage
  hits" and "exactly these ten `MISSING` paths"; the real numbers were **3** and **9**, and all
  three leakage hits were legitimate. A check that reports known-good noise gets skimmed.
- The symbol pattern capped at `[A-Z]{2,6}`, so a 7-character `BTCUSDT` was reported as
  `symbol=BTCUSD` — a finding naming a string that appears nowhere, so triaging it means grepping
  for something that does not exist.
- `cli/main.py` built its argparse tree inside `main()`, so the CLI surface could not be read
  without being run. Extracting `build_parser` was a prerequisite, not scope creep — and the check
  immediately found `wifey param-audit` documented nowhere in README.

Every allowlist entry carries its reason inline; an entry without one is how a check decays into a
no-op.

**Run:** `make sanity-checks`, or `PYTHONPATH=. poetry run python tools/sanity_checks.py
[--check NAME] [--exit-zero]`. It runs stdlib-only too (`python3 tools/sanity_checks.py`), which is
how the CI step works.

## docs_index.py — generated audit + spec indexes

Generates `docs/audits/INDEX.md` (18 verdicts) and `docs/superpowers/specs/INDEX.md`
(14 specs) — 32 documents that nothing indexed. CLAUDE.md cites 9 of the 18 audits
inline; the other 9 and 13 of the 14 specs had no surface listing them at all. Ported
from parent #600. `tests/test_docs_index.py` regenerates both and compares byte-for-byte,
so a new audit or spec **fails CI until it is indexed** — the enforcement half, without
which the index is the silent-surface class again. Output is deterministic (no
generated-at timestamp) precisely so `--check` can compare bytes.

**Nothing is guessed.** Date and title come from the filename prefix and the H1 (100%
reliable across both corpora). A verdict line is emitted **only** where it reads as prose
under a Verdict heading or in the inline `**Verdict: …**` form — **8 of 18** here — and
every other row gets an em dash, with the index header stating that coverage out loud.
Table rows, blockquotes, list items and `**Date:**` metadata lines each have a named
negative test; the last is live in `2026-06-21-experiment-1-residual-xsmom.md`, whose H1
matches the Verdict-heading pattern and is immediately followed by `**Date:**`.

### Two divergences from parent #600

**A wrapped verdict is read as a whole paragraph in BOTH forms.** Upstream joined
paragraphs under a Verdict *heading* but left the inline form reading a single line. This
corpus hard-wraps at ~80 columns, so on the first run **6 of the 8** readable verdicts
published as mid-sentence fragments — and because the cut fell on a word boundary with no
ellipsis, a fragment was **indistinguishable from a complete, shorter verdict**. The worst
was `2026-08-12-exit-mfe-mae-diagnostic-rerun.md`, cut at *"…SUPERSEDED at the cohort
level, and"* — dropping the clause that records the finding **reversed direction**, which
is the whole point of that audit. The inline pattern also now consumes an `=` separator
(`**Verdict = FAIL.**`), which otherwise leaked into the cell as a leading `= FAIL`.
**The same defect is live upstream** (2 of its 4 inline verdicts) — raise it there.

The join needed a stop rule, and the first version did not have one: several audits open
with `**Date:** / **Verdict:** / **Audit:** / **Spec:**` on **consecutive lines with no
blank between**, which markdown calls one paragraph, so joining onward merged the *Audit*
field into the verdict and turned two clean one-line verdicts into run-on text crediting a
make target. `_continues_paragraph` therefore stops at a bold field label (`_BOLD_FIELD`)
as well as at a heading, table, blockquote or list — and **both** extraction paths share
it, since the Verdict-heading path had the identical latent flaw. Stopping early costs a
few words; not stopping fabricates a verdict out of adjacent metadata.

**The reconcile column states its own floor.** `0 of N` renders identically whether the
detector found nothing or could never fire, and those are different facts. This corpus has
**zero** spec-reconcile audits, so 0 is the only reachable value; `spec_reconcile_audits()`
is read separately from the per-spec join so the page can say *no reconcile has been
written up* rather than publish a bare `0 of 14` that reads as *14 specs went
unreconciled* — a claim about the specs the data cannot support. (Same class as the
`exits/` MFE median: check the floor is reachable before quoting the number.) A spec counts
as reconciled only when an audit naming its filename says so **in its own filename or H1** —
keying on "reconcile" anywhere in the body mislabelled a doc reconciling two *findings*
upstream, and that case is a regression test here.

**Run:** `make docs-index` (write) / `make docs-index-check` (verify, writes nothing), or
`poetry run python tools/docs_index.py [--check] [--audit-dir PATH] [--spec-dir PATH]`

## distil_power.py — price a hypothesis BEFORE it is written into the inbox

The **G3 gate of `/research-distil`**. Prints the effect size the gate demands at the
declared `n` and trial family, so a claim is *priced* rather than estimated. Ported
verbatim from parent HEAD, with only the module docstring's precedent re-flavored;
`tests/test_distil_power.py` + `tests/test_research_guards_power.py` (48 cases) passed
here with **zero** code adaptation, because `dsr.py` and `psr.py` are byte-identical
across the two repos.

⚠ **It cannot price a hypothesis that is not Sharpe-shaped, and there is no error for that.**
`--units` is `per_trade | per_alert | per_book_day`, and the whole model asks what Sharpe clears
the DSR gate — so a **calendar-conditioning** claim (a seasonal or political-cycle effect, whose
unit is a *year* and which has no book, no trades and no Sharpe) has no honest way through it.
Passing one of the three existing units to get a number out is exactly the defect the module
docstring exists to prevent: *a figure that looks portable and silently changes meaning with the
panel.* Use the two-sample MDE instead — `(z_0.975 + z_0.80) · sd · sqrt(1/n1 + 1/n2)` with `sd`
**measured from the panel** — and say so. Worked example, both halves:
`docs/audits/2026-08-20-h001-h002-midterm-cycle-power-precheck.md`. ⚠ **The `--bar` /`--sd` legs
are still usable on their own** for the `powered_null` containment question; it is the Sharpe
half that does not port.

```bash
PYTHONPATH=. poetry run python tools/distil_power.py \
  --units {per_trade|per_alert|per_book_day} \
  --n-obs N --n-trials K --sr-variance V \
  [--n-series S --n-eff E] [--sd SD] [--bar R] [--corpus-best C]
```

`PYTHONPATH=.` is required — the bare invocation dies on `ModuleNotFoundError`, the same
shape as `route_dedup.py`. Exit 2 on a declared-error argument combination.

**Why it exists at all**: trial count dominates n, and it is not close. Re-derived here
against wifey's own `required_sharpe` — holding the trial family at 20, a **21× range of
n** (100 → 2,100) moves the bar **1.17×**; holding n at the live ledger's 267, going from
**1 to 320 trials** moves it **15.9×** (0.101 → 1.611). So a skill that reads three books
and emits forty hypotheses inflates the trial family until every cell is unreachable,
including ones that would have passed alone. The tool is what makes that arithmetic
tracked rather than recalled. Reproduce:

```python
from analytics.research_guards import required_sharpe
required_sharpe(100, n_trials=20, sr_variance=0.25) / required_sharpe(2100, n_trials=20, sr_variance=0.25)
required_sharpe(267, n_trials=320, sr_variance=0.25) / required_sharpe(267, n_trials=1, sr_variance=0.25)
```

**Three flags carry the traps.** `--units` is mandatory with **no default**, because a
figure that looks portable silently changes meaning with the panel — `regime.py` carried
crypto bar counts across the fork, so its "90-day" ATR window really spanned ~270 sessions
on `4h` (RTH is 2 bars/day, not 6) and 12.02% of `4h` labels moved when it was corrected.
`--n-series`/`--n-eff` must be supplied together — one alone raises, and omitting both on
a pooled multi-symbol panel overstates `n`. ⚠ **Omitting both still prints an UNDEFLATED
`n`**, i.e. an upper bound and therefore a bar smaller than the true one; never present
such a pass as having margin it did not measure. ⚠ **The repo DOES now have a measured
`n_eff`** — `make wifey-n-eff`, ≈2.96 at `1d` on the 505-member universe (2026-08-20), so
there is no longer any excuse for an undeflated run. The sentence here said the opposite
until 2026-08-26; it was written before the measurement existed and nothing touched it
after → the absence-claim decay class.

⚠ **`--corpus-best` without `--sd` is NOT a pass, and used to render as one.** The
comparison converts the required Sharpe into effect units, which needs `--sd`; without it
the branch fell through to the same bare `VERDICT REACHABLE` a cleared bar prints. Since
`/research-distil`'s G3 gate **mandates** running this tool, *did not compare* read as
*passed*. It now names the missing input. **That is the second fail-open path found in
this one file** — the first being the undeflated `n` above — so treat a `REACHABLE` here
as a claim to check rather than a result to quote. Ported from parent #692 (ST76).

`UNREACHABLE` is a **successful output**, not a failure: more data of that shape cannot
fix it, only a smaller trial family can. The null-containment verdict is delegated to
`analytics.audit_guard.powered_null` and is never restated in the tool.

## n_eff.py — how many INDEPENDENT series a pooled panel actually carries

`make wifey-n-eff` (`ARGS="--source universe --timeframe 1d"`). Wraps
`analytics/research_guards/correlation.py::effective_independent_series`, ported 2026-08-20
from the parent's `analytics/forecast/attribution.py`. Under an equicorrelation
approximation with mean pairwise correlation `rho`, `k` series carry the noise reduction
of only `n_eff = k / (1 + (k-1)·rho)`, so a naive pooled t-stat is inflated by
`sqrt(k / n_eff)`.

**It exists because `distil_power.py` failed open.** That tool has always *accepted*
`--n-series` / `--n-eff` and deflated by them, while nothing here could *measure* the
second — and `effective_n` returns `n_obs` **undeflated** when both are omitted. So every
power calculation this repo has run was either undeflated or used a borrowed figure,
including the H-004 pricing that closed it at G3. ⚠ **H-001/H-002 did NOT go
through this tool** — `distil_power` cannot price a calendar-cycle claim, so they
used a two-sample MDE, where a pooled `sd` carries the same correlation problem.

### Measured 2026-08-20 (first run)

| Panel | k | mean rho | `n_eff` | t inflation |
| --- | --- | --- | --- | --- |
| universe `1d` | 504 | +0.3365 | **2.96** | **13.05x** |
| universe `1wk` | 503 | +0.3459 | 2.88 | 13.22x |
| universe `4h` | 105 (21%, SIZE-TILTED) | +0.1958 | 4.92 | 4.62x |
| watchlist `1d` | 13 | +0.5189 | 1.80 | 2.69x |

⚠ **The 505-member universe is worth about THREE independent bets, not 505.** And
`n_eff → 1/rho` as `k` grows (1/0.3365 = 2.97 against a measured 2.96), so **adding names
buys almost nothing once `k` is large** — breadth is capped by the correlation, not by the
roster. Going 13 → 504 names is 39x the symbols for 1.6x the `n_eff`. This prices the
parent's "505 members is not breadth 505" caveat and lands well below its own ~11 estimate.

⚠ **The parent's `n_eff` 2.92 does NOT transfer** — that is 25 crypto perps at rho 0.315,
and it moves on its own panel (14 → 1.97, three → 1.42). The near-agreement with our 2.96
is the `1/rho` asymptote, not portability.

### Two things the tool refuses to do

- **It withholds the `distil_power` flags when `measured` is False.** An unmeasurable panel
  and an uncorrelated one both carry a deflator of 1.0; emitting flags for the first would
  launder "could not tell" into "no correction needed". Same distinction as
  `audit_guard`'s `INSUFFICIENT` vs `powered_null`. Exit code 1, and the banner says so.
- **It reports coverage every run rather than assuming it.** `4h` reaches 105 of 505 and
  that subset is SIZE-TILTED, so a deflator measured there describes large caps.

### ⚠ The pivot trap — a shared index silently empties the panel

The first implementation pivoted every symbol onto one union `open_time` index and called
`pct_change` across it. **When symbols sit on different stamp grids, consecutive union rows
belong to different symbols, so almost every return goes NaN.** Measured against the live DB
at `1wk` it dropped **505 of 505** symbols that the fixed implementation keeps (503 of 505).

Two reasons it survived: it **failed in the safe direction** (a refusal, not a wrong number),
and `1d`'s grids happen to align, so the timeframe anyone would hand-check passed — the `1d`
headline of 2.96 is identical before and after the fix. **Compute a per-entity series on its
own index; alignment is the correlation step's job, not the return step's.** Pinned by
`tests/test_n_eff_tool.py::TestLoadReturns::test_misaligned_stamp_grids_do_not_null_the_panel`,
which gives two symbols zero index overlap.

⚠ **`config/stocks.json` is keyed by symbol but also carries N1's `universe_policy` block**,
so a bare `sorted(json.load(f))` returns it as a 14th ticker. Go through
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

⚠ **`filings.recent` is a WINDOW, not a history** — SEC documents it as the most recent 1,000
filings, and that bound was confirmed on **one** company (AAPL, 2026-08-29: exactly 1000 entries
reaching back only to 2015-06-10, with one shard covering 1994→2015). The shard walk is
unconditional, so nothing depends on 1,000 holding for every filer; what matters is the shape,
since a fetcher reading `recent` alone returns a truncated history that reads exactly like a
quiet insider, so
`collect_filings` also walks every `filings.files` shard whose `filingTo` lands on/after the
window start (and skips the rest, which is what keeps a full run off the 1990s).

⚠ **`primaryDocument` points at the XSL-RENDERED HTML** (`xslF345X06/form4.xml`); the
machine-readable XML is the bare filename under the same accession. `raw_document_name` is that
one-line strip, and fetching the prefixed path yields a document carrying no ownership elements.

⚠ **The window starts 3 years before the study** (`--since 2015-01-01` by default): the
classifier needs a trade in each of three preceding years, so a run covering only 2018→ leaves
every insider unclassifiable.

⚠ **Requires `EDGAR_CONTACT_EMAIL`** — see the `edgar_client` note below. The run's last line is
the **phase-1 acceptance observable**: parse coverage against its 80% floor, printed PASS/FAIL.
A footnote-only price counts as a FAILURE (not a drop), while a derivative-only filing counts as
clean-but-empty — conflating those two is how a parser reports 100% coverage by construction.

**Run:** `make wifey-insider-backfill` (or
`tools/insider_backfill.py [--since ISO] [--stride N] [--limit N] [--symbols A,B]
[--max-filings-per-symbol N] [--db PATH] [--resume]
[--max-consecutive-failures N]`)

⚠ **A LONG RUN IS ONLY INTERRUPT-SAFE UNDER `--resume`, and the failure it prevents is
SILENT.** The per-filing and per-symbol handlers here both `warn → continue`, so a dropped
connection never crashed a run — it permanently skipped those filings and still printed `✅ done`
and a coverage figure. A plausible result with holes in it and no signal. `--resume` skips only
symbols carrying a completion marker in `insider_backfill_progress`, written **after** the upsert
and only when the symbol had **zero fetch errors**, so an interrupted or partially-failed symbol
is retried in full. Without the flag a restart re-walks from symbol 1 (safe via the upsert, just
wasteful).

⚠ **The marker key carries `since`, and PARSE failures deliberately do not deny one.** A marker
attests to the window it covered, so widening `--since` correctly re-runs everything rather than
reading the old completion as coverage. And a code-M option exercise carries no price and reports
a parse failure on *every* run — counting those as errors would deny the symbol a marker forever
and re-fetch it on each pass, i.e. resume that never resumes. Only **fetch** errors block a
marker.

⚠ **A sustained outage ABORTS** after `--max-consecutive-failures` symbols (default 5) rather
than walking the universe recording empty results. Nothing is lost: re-run with `--resume`.

⚠ **`--limit` ALONE TAKES THE HEAD, and `config/universe.json` is grouped by SECTOR** — so
`--limit 15` is fifteen Information Technology mega-caps, not a sample. Measured 2026-09-01 they
carry **18,549** Form 4 documents (CRM 4,175 + ACN 2,922 ≈ 38%), making the head simultaneously
the slowest slice in the universe and the least informative: parse failures concentrate among
small and older filers it contains none of, so a head-sampled coverage figure cannot support the
≥80% phase-1 floor in either direction. Pair it with `--stride`, which spreads the pick across
the file (measured: 7 sectors vs 1).

⚠ **`--max-filings-per-symbol` spreads across each symbol's range and MUST NOT become a head
cap.** `collect_filings` returns newest-first and recent filings are the most uniform, so taking
the first N measures the easy end and biases coverage **optimistically** — worse than no cap,
since the floor exists to catch exactly the documents it would drop. Same defect as the sector
one, one level down: sectors for symbols, filing vintage for documents. Both pinned by
`tests/test_insider_backfill.py`; the sampling rationale is spec Amendment 1.

⚠ **Runtime is round-trip bound, not throttle bound: ~2.1 documents/second measured** against
`www.sec.gov/Archives` (the 0.12s `_MIN_INTERVAL` is not the binding constraint). So the full
501-stock backfill is **20–40 hours**, an overnight job — the spec's "50–150k documents" estimate
is the right order but low at the top end. A coverage pilot wants breadth, not depth:
`--stride 10 --limit 50 --max-filings-per-symbol 40` ≈ 2,000 documents across 50 companies in
every sector, ~16 min.

## insider_cohort.py — H-024 phase 2, routine/opportunistic cohort shape

Reads `insider_transactions`, applies `analytics/insider/classify.py` and prints the split.
**It computes no return and has no access to a price** — the pre-registration puts the first look
at one inside phase 3's gated report.

**Run:** `make wifey-insider-cohort`, wrapping `tools/insider_cohort.py`
(`[--db PATH] [--symbols A,B] [--since YEAR]`). Read-only; no network, no
`EDGAR_CONTACT_EMAIL`.

Prints the split in **three units that disagree by design** — rows, trade-days and insiders. A
tranched sale is one trade-day and several rows, so a split quoted without its unit compares to
nothing; the WP's ~55% is a *trade* share, which makes `routine share of classified rows` the only
comparable line. The share is over **classified** rows: folding unclassifiable insiders into
"opportunistic" would inflate that arm with insiders the rule never examined.

⚠ **The phase-1 sample cannot answer this question, and that is the tool's main finding to date.**
`--max-filings-per-symbol 40` was right for observable (b) — a proportion whose validity comes
from filer diversity — and is wrong here, because the classifier needs a per-INSIDER calendar and
thinning a company's filings thins every one of its insiders'. Measured 2026-09-03: the capped
draw classifies **3.5%** of rows with **0 routine** across 47 companies, against **46.0%** on the
6 uncapped names, which supply **97.7%** of every classified row in the table. ⚠ **The cap
biases the LABEL, not just the count** — a thinned calendar cannot exhibit a same-month streak, so
everything it does classify falls to opportunistic. **The transferable rule: a sample designed for
one observable is not a sample for another**, and nothing in the stored data announces that the
unit changed from *document* to *insider-year*.

⚠ **Do not quote the 74.0% routine share as a panel figure** — it is six technology mega-caps, and
the 10b5-1-era explanation for its gap to the WP's 55% is a hypothesis nothing here tested.

The observability-divergence block (printed unconditionally, no flag) is a **probe, not an
alternative rule**: it re-runs the frozen classifier against only filings public on 1 January and
measured **1 of 6,981 (insider, year) labels (0.0%)**, which is what licences the frozen
trade-date reading rather than merely assuming it. Spec Amendment 3.

## insider_audit.py — H-024 phase 3, the gated trial family

Runs the four frozen trials and their four routine-arm placebos over the research universe (`1d`)
and prints the cohort split, each cell's headline with DSR / PBO / boot-CI, the realized equity-β
to SPY, the paired trial-minus-placebo reversal test, and PASS/FAIL on the pre-committed `T1`.
Read-only; no writes, no network, no `EDGAR_CONTACT_EMAIL`.

**Run:** `make wifey-insider-audit`, wrapping `tools/insider_audit.py`
(`[--db PATH] [--symbols A,B]`).

⚠ **This is the row's FIRST look at a return.** Ingestion, the coverage observable, the classifier
and the cohort shape were all built and reported without one, which is what makes this output a
test rather than a search. Everything it prints is therefore reportable as-is, including a null.

**Books are priced GROSS and NET side by side**, because the two verdicts read differently: a
sleeve that is negative before costs is a *signal* failure, and one that is positive gross and
negative net is a *cost* failure. The gross column is the live cost model with every charge zeroed
rather than a second model, so nothing but the prices differs between the two runs.

⚠ **The DSR family is the four TRIALS, never the eight books.** Placebos are controls; deflating
against eight would silently raise the bar the sleeve was pre-registered to clear. Pinned by
`tests/test_insider_report.py::TestDsrFamilyIsTheFourTrials`, whose control drops a real trial and
asserts the DSR *does* move — a family-size test that only asserted invariance would pass on a
report that ignored the family entirely.

⚠ **The reversal observable prints THREE states.** A pair reads `measurable` from the BOOKS, not
from their difference: two never-funded books produce an all-zero difference and a CI of `[0, 0]`,
which satisfies "indistinguishable" while establishing nothing — `audit_guard`'s lesson that
`INSUFFICIENT` and a powered null are different verdicts and collapsing them prints the confident
one. An all-zero difference between two *funded* books is a real null and is reported as one.

⚠ **Weights are trailing dollar ADV, not market cap** — there is no market-cap series in this repo,
so spec Amendment 4 rules the substitute in. It is a **liquidity** weight, so the book tilts to
high-turnover names, and any comparison to the WP's 82 bps/mo carries that tilt. The same number
buckets the spread each name pays, so weight and cost are read off one quantity.

⚠ **Do not run it on a symbol subset to get "a result".** `--symbols` exists for reproducing a
recorded draw, not for sampling: a non-pre-registered draw is the error Amendments 1 and 3 were
written about, and it spends the first look at a return on a panel that cannot be the registered
one.

⚠ **It needs the FULL uncapped backfill** (`insider_backfill_progress` certifies 497 symbols over
779,914 rows as of 2026-09-19). It also needs real memory: the panel is ~2,190 sessions × 498
names and the run died on a box whose kernel paged pool had leaked to 42.8 GiB of a 15.4 GiB
machine. ⚠ **A gate run under memory pressure is uninterpretable** — an OOM and a real failure
look identical.

## edgar_client.py — the SEC User-Agent contract

⚠ **MEASURED 2026-08-29: a User-Agent carrying a URL is refused (HTTP 403) by both SEC hosts**,
with or without parentheses. The module used to fall back to the repo URL when
`EDGAR_CONTACT_EMAIL` was unset, documented as "SEC may throttle an address-less UA harder" —
too kind by the time it was measured: unset meant a hard 403 everywhere, so
`make wifey-pead-backfill` could not run on an unconfigured box and failed as though SEC were
down. Probe matrix (`data.sec.gov` / `www.sec.gov`): **name+email 200/200 · name only 200/403 · URL
with parens 403/403 · URL without parens 403/403**. `_user_agent()` now raises
`EdgarContactMissing` when the contact is absent or has no `@`, so an unconfigured box fails
loud at the call site instead of three frames away.

⚠ **The contact reaches that check only because the entry points load `.env` — which neither did
until 2026-09-01.** `edgar_client` reads `os.environ`, and `tools/insider_backfill.py` and
`tools/pead_backfill.py` never called `load_dotenv()`, so a box with `EDGAR_CONTACT_EMAIL` set
exactly where `.env.example`, the README and the error message all say to put it still died on
`EdgarContactMissing` — advice naming a file the tool never read, which reads as operator error
rather than as a defect. Both now load it as their first statement, pinned by
`tests/test_edgar_user_agent.py` (the loader is patched to RAISE, so the test fixes the ordering
as well as the call).

⚠ **Bounded retry, on TRANSIENT shapes only.** `_open_with_retry` is the single place `_last_call`
advances, so both `_get_json` and `_get_bytes` share one throttle clock and a retry storm cannot
breach the SEC's 10 req/s ceiling. It retries **429 and 5xx, `URLError` and `TimeoutError`** —
4 attempts, 1.5s doubling — and raises everything else on the **first** attempt. **403 and 404 are
excluded deliberately**: a 403 is the User-Agent contract above and a 404 is a document that does
not exist, so retrying either burns the budget three times over and buries a configuration error
under what looks like flakiness. Before this, one dropped packet cost a filing permanently in
every caller that swallows per-item exceptions — which both backfills do.

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
**⚠ Its "0 bps" column is not cost-free** — `fee_pct` defaults to 1bp; see `velocity_audit.py`.

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

Read-only **exit MFE/MAE diagnostic** for the `analytics/exits/` package (PR #96 / parent #433),
diagnose mode only. Prints coverage, the overall win/loss/expired cohort roll-up, the
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

⚠ **That resolution is POSITIONAL and must stay so.** It was arithmetic until 2026-08-18:
`_regime_at_entry` floored `entry_time` to a UTC 4h boundary — correct on a 24/7 tape, impossible on
an RTH equity one, where 4h bars stamp 13:30/17:30 UTC and **0 of 105,708** 4h bars in the DB are
UTC-4h aligned (a single offset, 90 minutes). Every lookup missed, `fillna("unknown")` turned each
miss into a fall-open, and the tool reported **0 suppressed of 2,849 trades** under
`HOLD — insufficient suppressed trades`, which is exactly what a genuine sample shortage prints.
`regime_threshold_sweep.py` imports the same helper and was equally blind;
`direction_filter_replay.py` does no bar alignment and was never affected; a repo-wide scan finds no
other timestamp floor. **Live was never affected** — `scanner.py` reads `_series.iloc[-2]`.

⚠ **Its own test could not have caught it**, which is why the fix is a test shape rather than a
patch. `test_lookup_hits_previous_closed_candle` built 5 bars on a UTC-aligned grid, below the
classifier's minimum history, then asserted the result was `"unknown"` — the bug's own output, so it
passed identically before and after. The replacement pivots on `test_rth_entry_does_NOT_fall_open`,
the only assertion that fails against the old implementation (mutation-checked: **0 of 6** RTH
lookups hit under the modulo). A UTC-aligned case is retained as a non-regression for the crypto
shape.

**The decision rule is PER CELL, then combined under the single-switch constraint** (rebuilt
2026-08-19). Each suppressed (strategy × regime) cell earns an `analytics/audit_guard.py`
verdict — a block-bootstrap CI on the suppressed slice's mean R that must clear ±`bar`, AND a
Holm-adjusted p-value below `alpha` across the family of tested cells. `ENABLE` = that slice
reliably loses, so dropping it helps; `DISABLE`/`CONCENTRATE` = it reliably wins, so dropping it
costs; `INSUFFICIENT` = the run cannot tell.

⚠ **The cells are combined, never pooled.** `mode` is ONE GLOBAL SWITCH, so a single
reliably-winning cell blocks the flip regardless of how many cells or how much volume point the
other way. **An n-weighted mean cannot express that**, which is how the previous rule printed
`FLIP justified` off a table that contradicted it. The pooled aggregates are still printed,
labelled `DESCRIPTIVE — NOT decision-bearing`. Pinned by
`TestFlipVerdictCombinesCellsNotPools::test_one_blocking_cell_vetoes_a_dominant_losing_aggregate`,
which asserts the fixture satisfies the OLD pooled FLIP condition *and* still comes back blocked —
without both halves it would pass against a pooling implementation and could not detect a revert.

**Live-DB verdict 2026-08-19: `DO NOT FLIP`**, and it inverts the old banner on the same data
(pooled suppressed −0.1585 ≤ 0 with kept −0.0153 above it — the old rule's exact FLIP condition).
The blocker is `ema`/`high_vol`: n=172, avg_r **+0.5497**, CI **[+0.085, +1.023]**, Holm-adj
p=0.001 → `DISABLE`. `bos`/`trend` (n=897, −0.3540, CI [−0.462, −0.246], p=0.000) is a genuine
`ENABLE` and is the cell that carried the old aggregate.

⚠ **The significance test DEMOTES one of the three cells previously cited.** `ema`/`range`
(+0.3291, n=110) comes back **INSUFFICIENT** — CI [−0.210, +0.966] straddles zero at adj p=0.117 —
so the filed "three of six cells pointed the other way" overstates it: **one** survives a
significance test, not three. Correct frame, wrong count; the flip is blocked either way.

⚠ **A `DO NOT FLIP` here is not a clean bill for the config.** `bos`/`high_vol` (−0.3852, n=655,
the worst cell in the table) is a **kept** cell, so it is never tested and the tool says nothing
about it. That is the separate, still-open refutation of the inherited crypto calibration: the
`bos` override exists because a 2026-05-13 crypto audit found `high_vol` was bos's best regime, and
on equities it is bos's worst. Blocking the flip does not fix it.

⚠ **Every figure above is IN-SAMPLE** — 13 symbols, 2025-06-06 → 2026-08-11, one pass, no
out-of-sample split. It is evidence against flipping, never evidence for a replacement mapping.
→ [[project_flag_deltas_need_significance_tests]]

**Run:** `PYTHONPATH=. poetry run python tools/regime_gate_replay.py [--db PATH]`

## regime_threshold_sweep.py — slope-threshold sensitivity for the regime classifier

Re-runs `regime_gate_replay`'s annotation across a grid of candidate
`_SLOPE_TREND_THRESHOLD` values in `analytics/regime.py`, reporting suppressed/kept `n` and
`avg_r` plus `lift = kept_avg_r − suppressed_avg_r` per threshold. Tests whether the live 0.5%
default mis-labels exhaustion as trend: if some threshold separates cleanly the §6 mapping is
salvageable, and if none does, the mapping itself is the problem.

**This is a threshold sweep in the literal frozen sense** — it selects a parameter value. Read it
as diagnosis of the mapping, not as a source of a new constant.

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

Ported from parent #492. **This is the tool that WIRED `analytics/audit_guard.py`** — the guard
sat hostless through Phase N2 because the obvious host (`tools/gate_audit.py`) needs
`low_volume`/`volume_spike` on `backtest_trades`, columns wifey never had, and skipping that
migration fails **silently** via `fillna(False)`. This audit needs neither: it re-derives its
flags from OHLCV. Closing that gap closed SoT N2.

Regenerates each historical trade's six candle-warning flags through `analytics/warning_audit.py`
(which imports the live `alert_formatter` helpers rather than reimplementing them) and emits a
pre-committed SUPPRESS-CANDIDATE / REVERSE / COSMETIC / INSUFFICIENT verdict per
(warning × direction) — bootstrap CI clearing ±`bar` **and** a Holm-adjusted p, one family per
source. `backtest_trades` is primary and deduped across saved runs on
`(symbol, tf, strategy, direction, signal_time)`; `signal_alert_outcomes` is corroboration only.
Read-only.

**`_tf_ms` delegates to `parse_timeframe_secs` — do not "simplify" it back to a literal map.**
Upstream's map spells the weekly timeframe `1w`; every equity surface here uses `1wk`, which
carries 497 signals, so a verbatim copy raises `KeyError` on the first real run.
`TestTimeframeLength` pins this and was mutation-checked in both directions.

**Result (2026-08-13, CORRECTED same day): 11 of 12 backtest cells INSUFFICIENT, one
SUPPRESS-CANDIDATE — `w5_wick_rejection`/long** (n=316 warned at −0.315R vs −0.037R clean; Holm
p=0.002; sign holds on all three timeframes and on 5 of 6 strategies). **Not shipped as a gate** —
the live substrate has n=1 for that cell.

⚠ **The first run reported those 11 cells as COSMETIC and that was an artifact** (parent #617,
ported 2026-08-13p). COSMETIC was awarded on `n >= min_n`, a sample-size floor that says a test
*ran* but never that it could have *seen* anything. Under the honest test — `powered_null`, the
CI strictly inside ±bar — **0 of 11 survive** and **0 of 12 cells** have a CI inside ±0.05R
(half-width median **4.1×** the bar, range 1.8×–6.2×; **4 of 11** point estimates *exceed* the
bar, worst `w1_marubozu`/long at **+0.289R**, CI [−0.007, +0.615]). The honest reading is **"we
cannot tell"**, never "the warnings are decoration". The W5 lead is unchanged — only negative
labels can move. Verdict + caveats: `docs/audits/2026-08-13-warning-value-audit.md`.

**Run:** `make wifey-warning-value-audit` (`ARGS="--source live|backtest|both --min-n N --out PATH"`).

## pundit_score.py — read-only scorer for the Stream-C pundit ledger

Ported from the parent. Resolves every `docs/plans/pundit-calls.jsonl` call against stored
OHLCV → hit-rate + R proxies per author × setup-family × direction, plus a machine-readable
`docs/plans/pundit-priors.json` sidecar. Level parsing, family tagging, roll-up and
output shape are byte-identical to the parent so the two ledgers stay comparable.

**Descriptive priors only — NO gate is implemented, and the docstring used to imply one**
(parent #582's A7, ported 2026-08-13). It read *"audit_guard gates come later, only if a
cell earns n>=30"*, which reads as a live threshold; the only implemented construct is
`--min-n` (default 5), which renders a `⚠` marker and changes no output. **Nothing happened
when a cell crossed 30.** The claim is wrong a second and worse way here than upstream:
wifey *does* have a wired `audit_guard` since #183 (`analytics/warning_audit.py`), but it
scores the **W1–W8 signal warnings** and no code path joins it to pundit cells — so the
sentence read as a forward reference to something that had since arrived. `AUDIT_ELIGIBLE_N
= 30` does **not** restore a gate: `audit_eligible_cells()` returns cell *keys*, and
`render_report` prints a NOTE saying in words that none fires, so the crossing stops being
silent. **Deciding what a pundit prior should gate is an open research question** — do not
wire this to anything without answering it. Preventive today: the largest author cell is
**n=13** of 19 ledger rows, so the NOTE fires on **zero** cells (`make wifey-pundit-score`).

`load_ledger` enforces three field domains at the read boundary via the pure
`analytics/pundit_{direction,horizon,authors}.py` guards (parent #560/#561/#555 — see
`context/analytics.md`): a violation becomes a per-line warning naming the line and the
value, rather than a silent wrong number downstream. **`horizon` is the one that was live
here** — an unrecognised value took *two* silent `.get` fallbacks (`SCORE_TIMEFRAME`'s wrong
bar series *and* `SESSION_WINDOWS`' wrong window), where the parent has only the latter. The
`author` guard changes the **priors JSON key shape** (`@fenggemeigu` → `fenggemeigu`); nothing
in this fork reads that sidecar yet, so a future Brief/Card port must join on the normalised
key. No scored number changed: the committed 19-row ledger produces a byte-identical report
apart from the author column.

### A hyphenated range OVERRIDES a `/`-ladder (measured 2026-08-13)

`parse_level_field` emits `zones` and `numbers` separately, and `select_level` prefers a
zone. So **any** hyphenated range in a `target` field — including a clarifying
parenthetical after a valid ladder — replaces the intended level. Reproduced against the
production functions at `ref_close=64,000`, `role="target"`: `67,000 / 70,362.23 / 82,000`
→ **67,000**, and the same string plus `(or 65k-68k)` → **65,000** long / **68,000** short.

The zone resolves to whichever edge price reaches **first**, so the error runs in *both*
directions: a long lands nearer (manufacturing an optimistic WIN) and a short lands further
(stranding the row OPEN). **This corrects the upstream note in parent #616**, which states
the error "only ever pushes the target further away" — true of its short example, not of
the mechanism. Write-side rule and the `make wifey-pundit-score` round-end check live in
`/ingest-video` step 8 and `/ingest-x` step 4.

### The level-negation guard (parent #589, ported 2026-08-12)

`parse_level_field` drops **all** candidates when a field's *head* negates the level
(`_NEGATION_HEAD_RE`). `_UNSPECIFIED_MARKERS` cannot catch this on its own — it matches the
**whole stripped string**, so bare `not specified` was caught while
`"not stated (implied ~454 resistance)"` fell through to `_NUM_RE` and returned the
parenthetical as the level.

**The sanity gate is no backstop and cannot be made into one.** Its window is
`0.2×–5.0× ref_close`, and the worst form of the phantom number *is* a level near
`ref_close` — it passes, and with exactly one sane candidate `select_level` returned it at
`low_confidence = False`, so a fabricated call was indistinguishable from a real one at the
highest confidence label the scorer has.

**Anchored at the head deliberately.** A negation that *trails* a stated level qualifies its
**provenance**, not its existence — `"~420 (current market, no explicit entry stated)"` is a
real level. Those keep their candidates and set `ParsedField.hedged`, which `select_level`
ORs into `low_confidence`, downgrading rather than deleting a genuine call. An
"anywhere in the text" match — the obvious first design — destroys that second class.

Measured on this repo's **19-row / 33 populated-level-field** ledger (script:
`docs/plans/scripts/pundit_negation_impact.py`; **do not import the parent's 603-field
counts as wifey's**): **2 fields stop fabricating a level, 1 keeps its level at reduced
confidence, 30 unchanged.** Only **one** of the two reaches a published number, and the
discriminating check is why — a parse-layer count is not a scorer-layer count:

| row | field | before | after |
| --- | --- | --- | --- |
| `luckychartape` TSLA short | stop `"not stated (implied ~454 resistance)"` | `454.00`, conf **ok**, **R +3.27** | dropped, conf **low**, R — (ATR-R 6.36) |
| `benjaminjcowen` SLV long | target `"not stated (qualitative; 1970s analog…)"` | `1970` **already rejected** by the gate — ~**38×** SLV's 52.16 reference close, against a 5.0× ceiling | no published change |

`other/short` therefore flips **+1.13 → −1.00 avg R**, and that +3.27 was the ledger's *largest*
positive-R win (the only other is `fenggemeigu` MSFT at +1.35) and the only one that rested on a
fabricated level. **`fenggemeigu` — the one author with a rankable `n` — does not move at all**
(−0.41 either way), which settles the same question it settled upstream: the negative floor on
the only rankable author is real, not a parsing artifact. **Any `pundit-priors.json` generated
before 2026-08-12 carries the fabricated record — regenerate rather than reasoning from it.**

### `avg R` ships its own denominator (parent #602, ported 2026-08-12)

**`avg_r` and `n` are different populations, and the report used to print them adjacent.**
`r` needs a stated stop (`score_call`: `if risk is not None and risk > 0`), so a call that
stopped out necessarily has one while a win scored against a target often does not —
`avg_r` describes a loss-enriched subsample while `n` / `resolved` describe the whole cell.

Measured here 2026-08-12 (19 calls, 8 resolved; script:
`docs/plans/scripts/pundit_r_coverage.py`, which calls the production scorer — **do not carry
the parent's 43%/79% across**, that is a 203-row crypto ledger sharing no rows with this one):

| cohort | resolved | with `r` | coverage |
| --- | --- | --- | --- |
| WIN | 3 | 1 | **33%** |
| LOSS | 5 | 5 | **100%** |

**The censoring is worse here than upstream and it inverts the headline.** Every loss carries
an `r`; a third of wins do. `fenggemeigu` reads `avg R` **−0.41** over `r_n=6` while the
complete `atr_r` sample over all 7 resolved calls is **+0.80** — the two disagree in **sign**,
where the parent's only disagreed in significance. `luckychartape` is the mechanism in one
row: a WIN whose stop was the fabricated level dropped by #589, so it now contributes
`ATR-R 6.36` and **nothing at all** to `avg R` (`— (0/1)`).

The fix is disclosure, not a new statistic: `CellStats.r_coverage`, a
`avg R (r_n/resolved)` cell in both report tables, and `r_n` / `r_coverage` / `atr_r_n` in the
priors JSON. **`avg ATR-R` now leads `avg R` in the column order** because it is the complete
sample. Nothing in this fork reads the sidecar yet, so this is free to re-key.

**Transferable rule: a mean and a count printed side by side assert a shared denominator.**
When they do not share one, the disclosure belongs *in the cell*, not in a footnote — a reader
comparing two authors' `avg R` is comparing two different populations and nothing on the row
says so.

### Nine documented divergences

(1)–(8) cover everything that touches the tape, because equities are a sessioned market, and
(9) is a correctness fix that is not equity-specific:

- **(1)** scoring frame follows the horizon (`1h` intraday / `1d` swing+unspecified — several
  ledger symbols have no 1h bars at all)
- **(2)** no call-candle containment — the reference is the last bar *fully closed* at/before
  the call, so after-hours and weekend calls still resolve
- **(3)** thesis entries fill at the **next open**, not the call bar's close (the parent's
  convention is a look-ahead)
- **(4)** gap-aware direction-aware level crossing — a long stops at `min(open, stop)` rather
  than needing `low <= stop <= high`
- **(5)** adverse-first resolved by the open (opened-beyond-stop → loss at open; both-intrabar
  keeps the parent's stop-wins rule)
- **(6)** windows counted in **NYSE sessions** (2 / 21 / 10) via
  `analytics/trading_calendar.py`, not wall-clock
- **(7)** month-anchored years stripped before level parsing (US index levels share the
  1,900–2,100+ band with year strings — the first run read "…starting Aug-Sep 2026" as a
  2,026 target on a 7,436 index)
- **(8)** staleness measured against the last closed session, not wall-clock now (else every
  symbol reads STALE overnight). Also folds in the null-symbol guard that previously existed
  only as `/ingest-video` skill prose.
- **(9)** `_geometry_note` delegates to `x_route.check_level_order` and covers the **target**
  leg as well as the stop (2026-08-04): a wrong-sided stop only yields a nonsense R, but a
  wrong-sided target is already in profit at the fill and books an instant `WIN` at ~0.00 R —
  a phantom statistic rather than a visible error, which is how a mis-written "unless it
  reclaims 29,200" *stop* produced a 100% hit rate with zero warnings. Such a row is now
  `UNSCORED` with the offending pair named. The parent was ported from the same code and so
  likely carries this latent defect — raise it on the next `/sync-parent` rather than assuming
  it was fixed upstream.

Read-only; no schema change, goldens untouched.

**Run:** `make wifey-pundit-score` or
`PYTHONPATH=. poetry run python tools/pundit_score.py [--as-of ISO] [--min-n N]`

### Its OHLCV is a third universe, and nothing else refreshes it

`make go-live` syncs `config/stocks.json` — 13 ETF and equity **proxies**. The ledger records the
index and futures **underlyings** a pundit actually quoted (`^GSPC`, `GC=F`, `^TNX`), so the two
sets barely intersect and syncing one never refreshed the other. Measured 2026-08-15 right after an
operator `CATCH_UP=1 make go-live`: the mega-caps reached 2026-08-14 while eight ledger symbols sat
at 2026-08-04 and `^TNX` had **no bars at all** — and go-live reported success throughout, because
`pundit_score` degrades a stale symbol to `STALE` rather than erroring. A permanently-unresolving
ledger is indistinguishable from one where nothing has triggered yet.

Fixed 2026-08-17 by `wifey analytics {sync,backfill} --pundit`, wrapped as `make wifey-pundit-sync`
/ `make wifey-pundit-backfill`. Three properties worth keeping:

- **It resolves from the ledger at run time**, never from a second hardcoded list. A frozen list
  would reproduce the original defect one level over — the ledger gains symbols as calls are routed.
- **An empty resolve exits non-zero rather than falling back to the watchlist.** A fallback would
  resync the same 13 names go-live already covers and report success, which is exactly the shape
  being fixed.
- **`--universe` and `--pundit` are mutually exclusive at argparse level**, so a conflicting pair is
  rejected outright instead of silently resolving by precedence.

`INVALID_LEDGER_SYMBOLS` (in `utils/config_validation.py`) is the single definition of "not a
symbol", imported by both the loader and this scorer — if they diverge, the sync path fetches
symbols the scorer discards, or skips ones it scores. `tests/test_analytics_runner.py` pins the
identity.

After scoring, the ledger's own state is the check that the refresh worked: 22 rows, 0 `STALE`,
0 `UNRESOLVABLE` as of 2026-08-17.

## backfill_null_tp_outcomes.py — one-shot retro migration

One-shot retro migration (ported from parent #410): reconstructs the pct-fallback SL/TP for
legacy `signal_alert_outcomes` rows written with NULL `tp_price` (before
`_resolve_outcome_sl_tp`), then resolves them via `backfill_outcomes`. Read-only by default;
`--apply` gated; idempotent.

**Run:** `PYTHONPATH=. poetry run python tools/backfill_null_tp_outcomes.py [--config config/signal_watch.toml] [--apply]`

## x_fetch.py — read-only X/Twitter post fetcher

Read-only X/Twitter post fetcher via the public syndication endpoint
(`cdn.syndication.twimg.com/tweet-result`; no auth/scraping — a non-empty `token` is required
but its value is not validated, so a fixed dummy suffices). URL → `XPost` (text + full-res
`?name=orig` chart URLs + `video_present`/`is_thread`/`is_quote` flags + best-effort
`quoted_text`/`quoted_author` from the nested quoted tweet) + `download_photos` (charts →
gitignored `.cache/x-media/<id>/`); graceful `Unavailable` on
protected/deleted/tombstone/non-200/non-dict. Batch path `fetch_x_batch` fetches N URLs once
each with a randomized cooldown *between network fetches only* (default 4–12s; skipped before
the first fetch and on cache hits) + a per-id dedup cache (`.cache/x-posts/<id>.json` → zero
network on re-runs); `sleep`/`rng`/`get` are injected for deterministic, network-free tests
while `fetch_x_post` stays pure. **Thread path** `walk_thread` (parent #591) recovers one
author's self-thread by following `in_reply_to_status_id_str` **upward** from the tail,
returning a `ThreadChain` (posts root→leaf with `thread_pos`, plus `notes`). **The direction is
a hard constraint, not a choice: the endpoint has no replies/children field, so a thread is
reachable only from its LAST post — a bookmarked parent yields nothing below it.** `XPost`
gained `in_reply_to_id` / `in_reply_to_author` / `conversation_count` / `thread_pos`, **all
defaulted** because `_load_cached` does `XPost(**raw)` and any pre-existing cache entry lacks
the keys (wifey's `.cache/x-posts/` is empty, so this is inherited belt-and-braces here — and
`_load_cached` already catches `TypeError` as a cache miss, so it fails safe either way). The
walk stops at the root, on an author change (climbing further would attribute another pundit's
words to the bookmarked author), at `max_hops` (25), or on an unavailable hop — every stop but
the root records a note, and it never raises. It **reads** the per-id cache but deliberately
does **not write** it: an entry written here with empty `photo_paths` would make a later ingest
of that post skip its chart download. ⚠ `conversation_count` counts the whole conversation's
replies (everyone's) and is **not** thread length. Backs the `/ingest-x` skill (ported from
parent #466/#467; spec `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`).

**Run:** `PYTHONPATH=. poetry run python tools/x_fetch.py <url…> [--batch] [--thread] [--json] [--force] [--min-delay/--max-delay S] [--cache-dir/--media-root DIR]`

## x_route.py — routing decision and shared level sign-check

Pure routing decision **and** the shared level sign-check for the ingest skills.
`route_target(content_type, verdict, *, retrospective=False, rejected=False) -> str | None`
is the content-type gate
setup/mechanic/claim → the research pipeline's 4-bucket verdict taxonomy on the claim path →
Stream A `thesis-inbox.md` / B `mechanics-backlog.md` / C `pundit-calls.jsonl`, or drop.

**Two keyword-only suppressors drop a `setup`** — `retrospective` and `rejected`, both
defaulting to `False` (parent #521, ported 2026-08-13). A `setup` with either returns
`None`; a `mechanic`/`claim` is unaffected on purpose, since neither has an entry to
decline and honouring the flag there would let one mis-set field delete a routable item.

**They were missing here for 13 days and the gap was invisible.** wifey ported this file
on 2026-07-31 (#123); the parent added the suppressors on 2026-08-01 (#521) — a timing
artifact, not an equity divergence. What hid it: **wifey's #130 cites `#518/#521` in its
own title** while porting only #521's `route_dedup` half, so the PR number reads as
"already applied" to any check keyed on citations. **A PR cited in a port commit is not
evidence that every half of it landed** — #521 touched `x_route.py` *and*
`tests/test_x_route.py`, and #130 touched the first by 14 lines and the second not at all.

Live consequence while it was missing: `/ingest-video`'s pass 1 *does* set `retrospective`
on a setup lifted from a channel's intro recap, and nothing read it — so the call routed
to `pundit-calls.jsonl` carrying **today's** `call_ts_utc` and was scored on an
already-resolved trade. **Zero rows were actually damaged** (all 19 ledger rows predate
the flags and none carries either field), so this is preventive.

**`unattributable` is the parent's third suppressor and is deliberately absent** — it
belongs to the relay-attribution work (#558), which this fork has refused. **Callers must
pass the flags explicitly**; defaulting to `False` means a forgetful call site fails open
and silently, which is exactly how the original gap survived.

`check_level_order(direction, *, entry, stop, target) -> str` (2026-08-04) is the sign-check: a
long must satisfy `stop < entry < target`, a short `target < entry < stop`; every pair whose
legs are both present is judged, equality counts as a violation (zero risk / zero reward), and
an unjudgeable direction warns rather than passing silently.

`check_row_levels` applies it to a ledger-shaped row (`<role>_px` overriding the free text via
`first_level`, which strips **three** classes of number that read as a level but are not one).

### Three number classes stripped by `first_level`

- Month-anchored years — `MONTH_YEAR_RE`, the single definition also imported by
  `pundit_score.py` as its divergence 7
- Percentage *ranges* via `PCT_RE`, since the `%` binds to the second number
- Chart **timeframes** via `TIMEFRAME_RE` (2026-08-05; Latin `4h`/`1d`/`15m`/`1wk` + CJK
  `小时`/`日线`/`分钟`)

The first two are the single definitions, also imported by `tools/route_dedup.py`'s
`normalize_levels`, which has no sanity gate either; `TIMEFRAME_RE` is deliberately **not**
shared, because `normalize_levels` is already immune by a different mechanism — its
`_MIN_LEVEL = 100.0` floor drops a `4h` → `4.0` as sub-$100. `TIMEFRAME_RE` was added after a
gold long whose entry read "break above the 4h descending trendline" sign-checked as
`stop 4000 on the wrong side of entry 4`; that instance warned *loudly*, but the same artifact
passes **silently** whenever the stripped number happens to out-rank a real leg — a long with
entry `4h` and a stop of 3 reads 4 > 3 = OK — which is the fake-`WIN` class the guard exists to
catch. Its `(?<![\d.])` lookbehind is load-bearing: without it `\b` matches at the decimal
point, so `4.5m` matches its own `5m` tail and leaves a bare `4.` behind).

Consumed by `/ingest-x` + `/ingest-video` step 8 through the `--check-levels` CLI (JSONL from a
file or stdin; **advisory** — prints, exits 1 iff any row warned, never rewrites or drops) and
by `tools/pundit_score.py` on the read side. It warns and never drops, because the failure mode
being fixed is *silence*. Stdlib only, no I/O beyond the CLI's read.

### Known blind spot

The pairwise rule cannot judge a row stating one level and nothing else — that shape is caught
read-side only, where the scorer substitutes the market price for a missing entry. This is the
*dominant* shape, not an edge case: in the 2026-08-05 @fenggemeigu batch **7 of 11 candidate
rows were one-legged** (a lone 防势点 "defense point" and nothing else), 1 had zero legs, and
the only full entry/stop/target triple was degenerate — the support level served as both entry
and stop, which the check correctly flagged as zero risk. Encode such a row as entry+target
with the stop left unstated rather than inventing a gap the pundit never gave.

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

**Advisory except for `already_routed`** — it surfaces candidates for the review digest and
never drops a row. CLI `check | mark | unmark | pairs | seed`; `mark` runs strictly *after* the
sink write (marking at check time is the #68 watermark-on-send defect class).

⚠ **`--sink` is gated on `KNOWN_SINKS`, and the reason is the KEY, not tidiness.** `is_routed`
keys on `(source_id, item_ts, sink)` via `_key`, so a sink outside those three full paths is
**dedup-blind** — it writes a ledger row no later round can ever match, while `find_similar`
stays lenient on an unrecognised sink because scoping genuinely cannot apply there. That
leniency is why bad rows were writable at all, so the membership check sits at the CLI boundary
and nowhere else. ⚠ **Ported from parent #706 as PREVENTION, not a repair**: wifey's ledger held
**0 bad rows of 54** when it landed, against upstream's 30 writable — do not quote that count as
this repo's.

⚠ **A bare `python3 tools/route_dedup.py` now works**, and it did not before: that invocation
puts `tools/` on `sys.path` rather than the repo root, so the `tools.x_route` import died with
`ModuleNotFoundError` and only `make` or an explicit `PYTHONPATH=.` ran. A `sys.path` bootstrap
fixes it, scoped to tools that actually import from the repo — in one that does not it is dead
code masking the breakage the moment the first import appears. ⚠ **The guarantee is
`test_bare_invocation_works`, never the comment beside that line**; the test runs the module with
`PYTHONPATH` stripped from the environment, which is the exact condition that failed, and it was
mutation-checked. The parent hits the same class on `analytics.*` — the rule ports, the failing
module name does not.

### Seven divergences from the parent

Ported from parent #518/#521 with **seven divergences**, every one found by running the ported
code against the live sinks rather than by reading it, and four of the seven (1, 3, 6, 7)
traceable to the single structural fact that wifey's Stream A sink is a **markdown table** where
the parent's is prose:

- **(1)** Stream A splits on **table rows**, not the parent's level-two headings — the parent's
  splitter returns *one blob* for the whole live file (measured), so every comparison would
  silently score against it while `semantic_scope` still reported `all-entries`
- **(2)** `normalize_levels` also strips month-anchored years + percentages, reusing `x_route`'s
  `MONTH_YEAR_RE`/`PCT_RE` single definitions (US index levels share the year band — the
  PR #128 scorer defect, third consumer)
- **(3)** URLs are stripped before **both** scoring layers, since Stream A rows embed the
  source deep-link and its `&t=124s` offset reads as a price level (measured: the parent reads
  `124.0` off the live H-001 row)
- **(4)** bare 4-digit years in `_YEAR_BAND` (1900–2099) are not levels — before this rule
  **3 of 28 live Stream A pairs flagged and all three were year artifacts**; the rule keys on
  *how the number is written* (a level in that band carries a separator or decimal, a year
  never does), at the stated cost of losing a bare `2050` level
- **(5)** the ledger key **truncates** `item_ts` to whole seconds (`_ts_key`) where the parent
  rounds to 1dp — an offset arrives both as the row's float `ts` (`566.81`) and as the deep link
  it persists (`&t=566s`, truncated), and `round()` maps those to *different* keys, so a seeded
  ledger would silently fail to block a re-route
- **(6)** term overlap is **containment over the shorter side**, not jaccard (`_overlap` +
  `_MIN_DENOM`) — a Stream A entry is a whole row incl. a `gap note` column, so jaccard scored a
  near-verbatim restatement of the live H-002 row at **1.86 and never fired** (containment reads
  0.89)
- **(7)** the pipeline's own verdict vocabulary is excluded from term matching (`_STOPWORDS`
  derived from `x_route.VERDICTS`) — `ALREADY-TESTED` is stamped on every entry and
  `already`+`tested` carried **all three** remaining live flags, the same defect
  `_PUNDIT_CONTENT_FIELDS` fixes for Stream C in the shape a table takes

### Inherited limits

Two inherited limits are documented rather than "fixed": sub-$100 names contribute no numeric
evidence (`_MIN_LEVEL`), and Stream C's `same-source` scope is blind to one author restating a
call across uploads (deliberate — two calls a week apart are two genuine observations the
scorer resolves against different bars).

### Calibration

Calibration after all seven, on the live sinks: **0/28 Stream A and 0/7 same-source Stream C**
pairs flagged (highest non-flagging score 2.5 vs a 3.0 threshold), while restatements of the
live H-002/H-003 rows score **8.9 / 8.8** against the correct row — discriminating, not inert,
which matters because the inert failure is indistinguishable from "checked and clean".

Consumed by `/ingest-x` (step 3 check, step 4 mark) and `/ingest-video` (step 7 check +
`pairs`, step 8 mark).

**Run:** `make wifey-route-dedup-seed` (`APPLY=1` to write; read-only otherwise, idempotent,
10/10 live rows seeded unmodified) to seed the ledger. CLI subcommands:
`check | mark | unmark | pairs | seed`.

## video_calltime.py — pure call-time resolution

Pure call-time resolution, the `/ingest-video` look-ahead guard (kept out of prompt-space
deliberately — LLM date arithmetic is a known failure mode and this field decides whether every
author's hit rate is honest): `resolve_call_ts` prefers a video's stated in-video time but
bounds it (`stated < publish`, `publish − stated ≤ STATED_TS_MAX_LEAD_H` (168h), a
naive/offset-less stated value is rejected not assumed-UTC **unless
`stated_date_only` is set**, date-only → conservative end-of-day clamped below publish),
else falls back to `publish_ts_utc`; `is_backlog` flags
`publish → ingested` lag > `BACKLOG_THRESHOLD_H` (24h) — computed from publish time, so it
describes ingest lag, not the pundit's.

**Run:** `PYTHONPATH=. poetry run python tools/video_calltime.py --publish <iso> [--stated <iso>] [--date-only] --stated-raw "<quote>" [--ingested <iso>]`

⚠ **The naive carve-out is keyed on the FLAG, never on the value's shape**, and it exists
because the date-only branch was **unreachable on its own documented input** until
2026-08-20: `/ingest-video` tells its pass-1 subagent to emit `YYYY-MM-DD`, `_parse_aware`
required an explicit offset, so a bare date returned `None` and the publish fallback won
every time. Two properties make the carve-out safe where the blanket rejection is not — a
date carries no zone to lose, and end-of-day normalisation can only move the result
**later**, away from the look-ahead-permitting direction. A time component is discarded
rather than trusted, since the flag asserts there is none. **It failed in the SAFE
direction**, which is why a dead branch could sit there through a full suite: the fallback
is the most conservative answer available, so nothing downstream ever looked wrong.

## yt_feed.py — YouTube channel auto-feed backing `/ingest-feed`

Ported from parent #515, taken at **parent HEAD** rather than at #515's merge commit, so
six follow-ups (#516, #529, #535, #558, #582, #585) land with it (724 → 894 lines).
**#516 is the one that matters operationally** — it added `load_dotenv()`, without which
`YOUTUBE_API_KEY`
in `.env` is invisible and every API subcommand fails; porting #515 literally would have
shipped a feed that could not authenticate.

Read-only `poll` of each configured channel's uploads playlist (Data API v3,
`YOUTUBE_API_KEY`, ~2–3 units/channel/day, **never `search.list`**) + `backfill` deep pager
(floor ignored, ledger respected). **`poll --since` NARROWS the floor only**
(`max(floor, since)`, shared `_parse_since` with `backfill`): the floor records what the
operator already declined, so honouring an earlier `--since` would resurface it — reaching
below the floor stays `backfill`'s job, and that asymmetry is the whole difference between
the two subcommands. `mark` is the **ONLY** writer, stamped post-review-gate, so the
wifey-#68 watermark-on-send defect class is structurally impossible: no fetch-time writes,
no moving watermark, static per-channel `floor_ts`. Plus `resolve` (handle → ready-to-paste
TOML block) and `hint` (pure local config read; resolves **ahead of** the API-key gate, so
it needs no `YOUTUBE_API_KEY`).

Config is the gitignored `config/youtube_channels.toml` (committed `.example`). State is
`docs/plans/yt-feed-state.json` (gitignored, atomic writes, loud-abort on malformed).
Injected HTTP `get` → network-free request-shape tests (70 of them).

**Two wifey-specific divergences, both re-derived here rather than inherited:**

- **`item_cap` is nearly inert in this repo.** It defaults to `video_marks.ITEM_CAP`,
  imported not re-literalled — so it correctly picks up wifey's **12** (the parent's is
  5, raised here in #128 alongside a `MIN_ITEM_SPECIFICITY` floor). But `/ingest-video`
  requires `item_cap + len(TAIL_OFFSETS_S) <= FRAME_CAP`, i.e. **≤ 13**, so the usable
  range is 13..13. **`yt_feed.py` does not validate this** — it imports `FRAME_CAP` only
  to estimate tokens — and a larger value silently degrades kept items to
  `vision_confidence: "low"`. The parent has 8 of headroom and never hit the ceiling.
- **`hint` EXITS 1 when `config/youtube_channels.toml` is absent** (`load_feed_config`
  raises `SystemExit`; it does not return `matched: false`). That file may legitimately
  not exist here, since `/ingest-video`'s primary mode in this repo is a hand-pasted URL
  with no follow list — so its step-3 call is guarded with `|| true` and a missing config
  is treated as `matched: false`, not as an error.

**Run:** `PYTHONPATH=. poetry run python tools/yt_feed.py poll|backfill|mark|resolve|hint`

⚠ **`mark` being the ONLY writer is also its sharpest failure mode, and it set the ranking
rule: a silent WRITE-path failure outranks a loud READ-path one.** `-mx3UwwJ5P4` is a valid
YouTube id and argparse read the leading `-` as a flag, so `nargs="*"` dropped it. Because
`mark` is the sole writer of consumption state, the swallowed id was never recorded and the
video **re-presented forever with no other symptom** — no error, no partial write, nothing
downstream that looked wrong. `mark` now extracts `--ingested`/`--skipped` from `argv`
before argparse sees them, so every call shape works
(`tests/test_yt_feed.py::TestDashLeadingVideoIds`, 5 tests).

`route_dedup --source-id` is the deliberate contrast and is left unchanged: it takes one
value, so `--source-id=<id>` works natively and the space form fails **loudly**, which its
`--help` says. A read-path tool that dies in front of you costs a retry; a write-path tool
that drops one argument costs a ledger nobody knows is wrong. **Rank the fix by whether
anything could have SEEN the failure, not by its blast radius.**

## video_fetch.py — read-only YouTube/X video fetcher

Read-only YouTube/X video fetcher: every yt-dlp call goes through
`_YT_DLP = ("yt-dlp", "--js-runtimes", "node")` — never a bare `["yt-dlp", …]` (yt-dlp
≥2026.07.04 enables only **deno** by default; without an available JS runtime every *media*
path 403s while captions still resolve, so the failure masquerades as one unlucky video.
`--js-runtimes` is additive, and the `yt-dlp-ejs` runtime dep backs it).

⚠ **At least two independent causes produce that identical symptom, and the JS runtime is
only one of them** — the count is a floor, not an enumeration. The media fetch also 403s when the *extractor client* is left to yt-dlp's
own default selection, which is why `_ensure_local_media` pins
`--extractor-args youtube:player_client=android`. Measured 2026-08-19 on 2026.07.04, the stable
this repo pinned before the nightly bump, with `node` installed and `--js-runtimes node` already in effect: the default
pick `android_vr` 403s, while `android` / `mweb` / `web_embedded` all download; `tv` fails
to load, and `web_safari` / `ios` fail *differently* — "requested format is not available"
against `bv*[height<=1080]` — so they are **not** substitutes. **A version bump is not the
fix** and `poetry.lock` is not involved; an earlier filing said otherwise and was wrong.
The 3-attempt retry cannot cover this either: it was written for an *intermittent* 403 and
this one is deterministic. Pinned by
`tests/test_video_fetch.py::test_local_media_download_pins_the_extractor_client`, which
asserts the flag/value pair adjacently because the regression shape is an **absent flag**.
⚠ **Both causes are silent in the same direction** — captions resolve either way, so the
vision pass returns chart-uncorrected items that look fine. Diagnose by running the
download, never by reading the code.

- `fetch_meta` (yt-dlp `--dump-json` → `VideoMeta` incl. publish time)
- `fetch_transcript` (existing captions in any language first, else Groq `whisper-large-v3` over
  extracted opus audio — `split_audio` chunks past the 25MB cap using `duration_s` for
  offset-correct per-chunk timestamps). Returns a `TranscriptResult` carrying the segments, the
  chosen `lang` and a `source` of `manual_captions` / `auto_captions` / `asr_whisper` /
  `captions_unknown`. ⚠ **`captions_unknown` is NOT folded into `auto`** — "we did not ask" and
  "we asked and it was ASR" are different claims, and every pre-port cache entry is the former.
  An ASR transcript is a materially weaker source than an author-written one, and every item,
  `raw_quote` and call-time derives from that text
- ⚠ **`_sub_langs` decides which caption tracks are even REQUESTED, and asking wrong costs the
  whole transcript.** yt-dlp returns `language: null` on a large slice of the follow list, and the
  pre-port expression then asked for `en` ALONE — so a Chinese upload with an author-written
  `zh-Hant` track got "no subtitles for the requested languages" and fell through to ASR, worst
  exactly where ASR is weakest. It now widens the request with the codes `--dump-json` already
  returned (same call, no extra quota), resolves a REGIONAL `lang` down to its base (`en-US` →
  `en`), and caps the list at `_MAX_SUB_LANGS = 6` so no video can request a translate matrix —
  upstream measured 157 auto codes led by `ab`/`aa`/`af`, answered with HTTP 429 partway through,
  leaving the transcript's language decided by which file survived the rate limit. Ported from
  parent #668 + #674. ⚠ **The chapters/recap half of #668 is now PORTED too** (2026-08-27), at
  its **post-#695 shape** rather than as merged
- `Chapter` / `_parse_chapters` / `recap_window_s` — a video's own leading recap chapter answers
  per VIDEO what `intro_recap_s` answers per CHANNEL, and beats it in **both** directions (a
  shorter chapter window must narrow the trim too, or the override is just a bigger constant).
  Chapters ride in on the `--dump-json` call `fetch_meta` already makes, so this costs parsing,
  not quota; `_parse_chapters` drops a malformed entry rather than failing the fetch, since the
  list is author-supplied. `recap_window_s` returns `0.0` for "no answer here", leaving the
  constant in charge — upstream found no chapters at all on about half its corpus.
  ⚠ **`_RECAP_TITLE_HINTS` deliberately EXCLUDES `intro`.** Upstream shipped it, then removed it
  in #695 after a leading chapter titled `Intro` marked the first 26% of an educational upload as
  a position recap — **on a channel configured `intro_recap_s: 0`, which is exactly wifey's
  setting for BOTH live channels**. So the unamended list would have reproduced that defect here
  on day one rather than importing it dormant. An introduction OPENS content; a recap REPLAYS
  prior calls, and only the second is what the window trims. `review` and 概述 are the same
  shape and are UNMEASURED — treat a sighting on either as this defect again, not a new one.
  ⚠ **Here the chapter window is the ONLY trim that can fire**, both channels being at 0, so a
  false positive has no constant to fall back to and costs the whole trim.
  ⚠ **`_meta_from_cache` must COERCE chapters back into `Chapter` objects** — `asdict` flattens
  them to dicts and a frozen dataclass does no coercion, so the field would claim
  `tuple[Chapter, ...]` while holding dicts and `recap_window_s` would die on `chapter.title` at
  the first cache hit. mypy cannot see it: `**` builds the lie at runtime
- `extract_frames` (one ffmpeg seek per caller-supplied `FrameMark`, never speculative; retries
  the whole download-and-seek on **total** failure only — 3 attempts spaced by
  `_FRAME_RETRY_BACKOFF_S`, since a partial result means those marks individually failed to
  seek)
- `fetch_video_batch` (per-video dedup cache at `.cache/video/<id>/asset.json`, randomized
  cooldown *between network fetches only*, one failing video never kills the batch)

`run`/`get`/`sleep`/`rng` injected so the suite is network-free. Backs `/ingest-video` (ported
from parent #513; spec `docs/superpowers/specs/2026-07-28-ingest-video-design.md`).

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

**That tie-break has a measured directional bias:** ts-asc means a tie at the cap resolves in
favour of *earlier* material, and a pundit who opens with macro and closes with single names
therefore loses the single names first. Measured 2026-08-05 — `ITEM_CAP=12` bound on all four
@fenggemeigu videos (26/15/39/36 candidates), and on `EKtmvhDOW20` it dropped the NVDA setup
**and both GOOGL items despite those two names being in the video's own title**, because they
sat past 1,090s behind a gold/DXY block. The cap is doing what it is specified to do; the note
exists so a thin-looking Stream C yield from a dense video is read as a cap artifact rather than
as a video that made no calls. The **floor** binds first and stops a *thin* video padding
low-specificity vibes up to the cap just because slots exist; the **cap** binds on a *dense* one.

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

**The report is the triage artifact, so it is written in-repo:**
`docs/plans/parent-sync/parent-sync-<date>.md` (gitignored via `docs/plans/`, directory created
on demand). It was `/tmp` until 2026-08-11 — a `/tmp` clear destroyed the 2026-07-29 report with
**57 of 67 PRs still undecided**, forcing a full re-scan. A reviewer decides PRs against this
file over days; it has to outlive a reboot.

**The parent's checked-out branch does not matter.** Every parent read is ref-based against
`origin/main` (`cat-file` / `fetch` / `log` / `show` / `rev-parse`) and nothing touches the
parent working tree, so the only real precondition is that `origin/main` resolves. A
checked-out-branch guard blocked scans outright on 2026-06-17 and 2026-08-11 and was removed.

**ALREADY-APPLIED reads SYMBOLS, and a MODIFIED symbol's name is not evidence** (fixed
2026-08-13q). A signature-only change re-emits its own `def` line, so the name sits on both
sides of the diff and a name-presence grep matches wifey's *old* copy. Parent **#521** is the
worked example — its entire payload was two kwargs on an existing `route_target`, and the
resolver returned **HIGH / ALREADY-APPLIED** while wifey had only the two-arg version, a real
missed port that stood until #186 closed it by hand. `extract_symbol_changes` now splits
`added` from `modified`; a modified symbol's evidence is the **identifiers the change
introduced** (restricted to syntactically-used tokens, since a bare `\w+` sweep harvests
docstring prose and the wifey grep is repo-wide), and modify-only with no new identifier
reports **UNKNOWN** — "cannot tell", never "applied". Parsing is hunk-scoped: `git show`
without `--format=` prepends the commit message, whose prose has no diff prefix and otherwise
reads as context, subtracting the very identifiers the change introduced.

**Two further limits to know before trusting a bucket.** The classifier defaults to EVALUATE whenever a
path resolves, so at a wide range the counts degrade (2026-08-11: 0 SKIP / 39 PORT / 107
EVALUATE over 156 PRs). And **buckets cannot see portability** — a mechanical check of whether
the touched files exist in wifey is the cheap filter, and it inverts the parent's own ranking:
on 2026-08-11 `fix(xsmom)` #572 touched **0** files present here despite wifey owning
`analytics/xsmom/`.

**Run:** `make wifey-sync-parent [FROM=<hash>] [FULL=1] [NO_FETCH=1] [BUMP_TO=<hash>]`. Never
`--bump-to` while the state file doubles as a memory — it rewrites the file to a stub and wipes
the triage body; hand-edit the frontmatter pointer instead.
