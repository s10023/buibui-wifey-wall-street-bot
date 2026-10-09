# Sweep and gate notes

Supports Phase 1 and Phase 2 of `SKILL.md`.

## Why the sweep runs before the PR

**It runs before the PR exists**, on a branch that is committed but not opened.
Private repo, free tier, hard Actions budget: sweeping after the PR is open means
a second push, and every push re-runs the full five-check matrix. Sweeping first
is one CI run instead of two, with identical review signal.

## Inherited phases

**If phase 1 and the doc edits already ran on this HEAD, do not re-walk them.** A build
session often runs the sweep and the doc walk as it goes, and re-confirming phases 1–3
from scratch then costs a full pass for no new signal. Re-run only the legs a later
edit touches, by name (`PYTHONPATH=. poetry run python tools/post_branch_checks.py
--check <leg>`, repeatable), and say in the output block which phases were inherited and
from which commit. Phases 4–6 still run in full, and so does the post-phase-6
`memory-cap` / `handoff-size` re-run below.

## The thirteen checks

**The sweep's output names the phases below that it does not reach**, so a clean run
doesn't read as a complete walk. This note is suppressed for `--text` and for any
`--check` (repeatable).

| Check | Asks |
| --- | --- |
| `queue-items` | Does this branch **close** a task the handoff still lists as to-do? |
| `handoff-symbols` | Does the handoff make a claim about a symbol or file this branch touched? |
| `new-files` | Does an added non-Python operator file reach a doc that enumerates by name? |
| `new-modules` | Does an added module reach `.claude/context/`? |
| `new-targets` | Does an added Make target reach a doc? |
| `amended-targets` | Did an existing target's recipe change, leaving a doc's enumeration of it short by one? Names the target and the docs to re-read |
| `negative-claims` | Does a doc assert the absence of something this branch just added? |
| `doc-indexes` | Is a generated `INDEX.md` stale? (a red suite, not a lint nit) |
| `md-atx` | Did a wrapped `#123` land in column 1 and become an MD018 heading? |
| `memory-cap` | Is MEMORY.md over 6 Current State bullets or ~17KB? **Phase 6 reading** |
| `handoff-size` | Is the handoff past `HANDOFF_MAX_LINES` (240), or within `HANDOFF_WARN_MARGIN` (20) of it? **SKIPPED when the handoff is absent — never clean.** **Phase 6 reading** |
| `stale-anchors` | Does a doc cite a numbered section (`Step 3`, `§4a`) its target no longer has? |
| `sensitive-terms` | Would a visibility flip publish a work identifier? Tracked tree · this branch's commit **content** · this branch's commit **messages** · every Issue and PR title, body and comment already on GitHub (an unreadable read is `UNREADABLE`, never clean). A PR title/body not yet posted is the surface none of these reach — `--text`, below |

## Vacuous legs and NOT CONFIGURED

**`memory-cap` and `handoff-size` are vacuous on the phase-1 run — ignore them here.**
They measure files that phases 4 and 6 write, so a first run scores the *previous*
session's state and reports clean regardless of what this branch will do. Re-run
`make post-branch-checks` after phase 6 and read them then; that run is the gate.

**`sensitive-terms` reading `NOT CONFIGURED` is a finding, not a skip.** The term list
(`.claude/sensitive-terms.txt`) is gitignored by policy — a tracked list of the words
you are hiding is the leak it exists to prevent — so it dies on a reclone, and before a
flip "did not run" and "passed" must not look alike. Only the operator can enumerate
the terms. The commit-**message** leg is the one no file edit reaches: the flip
republishes the whole history, so scrubbing a term in a later commit does not unexpose
it. Terms are masked in the output because this report gets pasted into handoffs and PR
bodies that are themselves tracked or backed up, and main's accepted historical
baseline is deliberately not re-reported.

## Repo-relative FILE and chaining

**Pass a repo-relative `FILE=`, or pipe with `FILE=-`; never an absolute Windows
path.** The recipe hands `$(FILE)` to the shell unquoted, so `C:\Users\…\iss.md` arrives
with its backslashes stripped, the screen exits 2 on a file that does not exist, and
whatever runs next posts unscreened. **Chain the post behind the screen with `&&`**
(`make post-branch-text FILE=… && gh …`): make's exit 2 then stops the post, whereas
`;` or a separate tool call posts regardless of the screen's verdict.

## Screening a PR title and body

`FILE=-` reads stdin, and the flag is repeatable on a direct invocation. Unlike the
sweep it gates — exit 1 on a hit, though through `make` you see make's own 2, so read
the banner. It needs no git surface. Output is line numbers plus a masked term and
never the matching line — quoting context would reproduce what the masking withholds.
It reads the same gitignored list, so `NOT CONFIGURED` is a finding here too, and an
unreadable `FILE` exits 2 rather than rendering as a clean one-check run. **This
belongs in phase 5, beside `make preflight`, not after `gh pr create`** — a posted
body is public the moment it lands and editing it later does not unpublish it.

## How the checks behave

**`queue-items` reports relevance, not closure**, and prints the tokens it matched so
you can dismiss in a glance. Expect false positives from area vocabulary, and expect
one item to match itself. Do not "fix" it by matching action phrases instead of nouns —
a real true positive can match on nouns alone; the reasoning is pinned in
`check_queue_items`'s docstring.

**Every finding is a candidate to dismiss in seconds, never an automatic edit.**
The asymmetry is deliberate: a false positive costs a glance, a silent miss ships
a doc that enumerates every sibling but one and reads as complete.

Three properties the checks rely on, worth knowing before you change them:

- **Word boundaries are load-bearing.** A bare substring match reports a module
  "documented" on a hit that has nothing to do with it, and *a false-positive
  presence check is worse than none, because it reports covered.*
- **A basename that names a role cannot identify a file.** Every skill is
  `SKILL.md`, so probing that matches CLAUDE.md's generic sentence about where
  skills live; `probe_names` probes the parent directory for those. Same for
  `__init__.py`, `INDEX.md`, `README.md`.
- **Untracked files count as added.** `git diff` cannot see them, so a presence check
  scoped to `git diff` alone can report zero on exactly the branch they exist for.

The Makefile is deliberately **not** in the enumerating-doc list. A build rule is
not documentation, and including it would let a file that appears in no prose report
covered. Check what a proposed addition would newly mark covered before adding it.

## Triaging the presence checks

**Triaging the presence checks:**

- A hit is a prompt to judge, not an automatic edit — a private helper module may
  legitimately not warrant a context entry.
- `migrations/` **is** in scope (documented at `.claude/context/migrations.md`),
  so a new migration script gets checked like any other module.
- `trade/` is knowingly absent and is **not** a finding — it is an empty
  placeholder, both files 0 bytes.
- **Renames are not covered.** The checks key on additions; swap in
  `git diff main --diff-filter=R --name-only` and check the new path by hand.
- **`negative-claims` prints a `note:` line with up to four remainders, and one of them
  is work.** Claims *scoped out* are absence sentences elsewhere in the tree this diff does not
  touch — not dismissed, just not yours. Claims *exempt* carry a reason inline in
  `NEGATIVE_CLAIM_EXEMPT`. The note also names claims whose only diff hit was a bare
  English word — **those say "re-read, do not assume"**, and they are listed by
  `path:line` precisely so you open the paragraph rather than trust the matched clause
  alone. A claim it cannot scope at all is still **reported**, so
  `(no token to scope on)` means "could not rule this out", not "certainly stale".
  In a doc this branch rewrote (added lines at least half its length), a claim whose
  text is already on `main` is named as *unchanged from main* rather than reported:
  the rewrite re-added it, and it scoped in on its own text.
- **`docker-compose.yml` is not covered either.** Check by hand that a new
  daemon got `restart: unless-stopped` and a new one-shot tool got
  `profiles: [tools]`, plus its `docker-up` / `docker-down` lines.

## Behaviour signal globs

Path heuristics, not absolute rules — always read the diff before deciding:

```yaml
behavior_signal_globs:   # touching these almost always needs a walk
  - wifey.py · cli/**/*.py · Makefile · docker-compose.yml · pyproject.toml
  - config/strategy_params.toml · config/*signal_watch*.toml
  - deploy/**                     # scripts AND systemd units are operator-facing
  - .github/workflows/**/*.yaml   # .yaml, not .yml — every workflow here is .yaml
behavior_skip_globs:     # internal-only refactor space
  - analytics/**/_*.py · analytics/**/*.py (per-PR judgement) · tests/**
  - poetry.lock · *.parquet · tests/fixtures/**
```

A move that adds a new **public symbol** is user-facing even under `analytics/**`.

## Lint-only means formatting

**"Lint-only" means the formatting, not the linter's configuration.** Editing
`.markdownlint*`, ruff/mypy blocks, or a CI job's globs changes what the build
*enforces*, which is operator-facing however mechanical the diff looks — a single
deleted glob line inside an otherwise cosmetic `chore(lint)` PR can silently exclude
the tree the job was meant to cover. Judge the gate on what the change enforces, not
on what the diff looks like.

## Strong refactor signals

**Strong refactor signals** — a module in CLAUDE.md's Project Structure was
renamed, moved or reduced to a re-export shim; the CLI surface changed; a new
`make wifey-*` target landed. When in doubt, ask.

## DB migrations are operator-facing

A schema change applies **silently at the next `init_schema`** — no command to
run, no output to read. The handoff must state, explicitly:

1. **When it applies** — the next process that opens the DB. Name it if scheduled.
2. **Whether existing rows stay readable**, and if not, what breaks.
3. **Whether a manual step is needed** — backfill, `make db-update`, `clean-db`, none.
4. **Whether it is self-healing, and why.** A nullable column on a table whose
   rows age out needs no backfill; on a table that accumulates, it does.

Confirm `tests/test_schema_insert_arity.py` still passes: adding a column to a
positionally-written table requires updating that statement in the same PR.

## Notification verdicts

Telegram is an operator-facing output and belongs in this gate, but it is on no
doc surface, so the decision gets made by whoever happens to think of it. Three
paths: the **personal** channel (long+short), the **wife** channel (BUY-only, a
human audience), and `deploy/notify-failure.sh`.

Needs an explicit decision if the PR adds or changes: a scheduled job; an
irreversible or outward-facing action; a latching state transition (the
*transition* is the event, not the state); a failure path visible only in an
unread log; or a periodic summary a human must act on.

Record one of four verdicts: `always` (a human must act every time, or the
channel needs a heartbeat) · `on-change` (only transitions matter) ·
`on-failure-only` (correct for jobs nobody reads when healthy) · `never` (**state
why**, in one line).

## Failure-only channels

**A channel whose only signal is failure is unfalsifiable** — you cannot tell healthy
from broken without a heartbeat, and the delivery path gets exercised for the first
time on the day you need it. If a path is `on-failure-only`, confirm something else
proves it alive. Volume is the counterweight: multiply by the schedule before choosing
`always`, and remember the wife channel is a person.
