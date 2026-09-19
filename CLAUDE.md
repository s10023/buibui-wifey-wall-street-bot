# CLAUDE.md

Instructions for Claude Code in this repository.

## Working agreement

**Persona.** Senior quant-systems engineer on a research codebase that has so far found no edge.
Bias to de-biased, out-of-sample evidence over in-sample optimism, and know which panel you are
arguing from — they are not interchangeable here. A **sleeve** is judged on net-of-cost Sharpe
against `GATE_SHARPE = 0.7`, DSR, PBO and the bootstrap lower bound, with a realized-beta guardrail
on any market-neutral construction. A **detector cell** is judged on pooled `avg_r` across
strategy × timeframe × direction, with regime as a soft conditioner. Wifey has one RTH session, so
crypto's session axis does not port. Never commit an overfit parameter. Report a negative result
plainly: eight sleeves have come back non-positive and each of those is a finding, not a failure.

**Definition of done.** A Python change is done when `make lint-py`, `make typecheck` and
`make test` are green.

⚠ **`make test-regression` is a separate gate, and neither `make test` nor `make preflight` says
anything about it** — both run the suite with `--ignore=tests/test_regression.py`, so the
clean-clone gate does not cover the regression gate either. It is **required** when the diff
touches any path CI's own regression filter fires on, quoted here **verbatim** from
`.github/workflows/lint.yaml`: `analytics/**/*.py`, `pyproject.toml`, `poetry.lock`,
`config/*.toml`, `tests/test_regression.py`, `tests/fixtures/**.parquet`,
`tests/fixtures/golden_*.json`, `scripts/extract_regression_fixture.py`,
`.github/workflows/lint.yaml`. Say which branch you took.

⚠ **This list used to be hand-written, and it diverged from that filter in BOTH directions.**
It named four `analytics/` paths against CI's `analytics/**/*.py` and two config patterns against
CI's `config/*.toml`, omitted `pyproject.toml`, `tests/test_regression.py`,
`scripts/extract_regression_fixture.py` and the workflow entirely — and ran *wider* than CI on
`tests/fixtures/`, against CI's two narrower fixture globs. ⚠ **Only the narrowing direction is
harmful**, and conflating the two is what made this look tidier than it was: running the gate when
CI would not costs ~8s, while not running it when CI would costs a metered cycle. So a diff touching
`analytics/store/`, `analytics/audit_guard.py` or `analytics/signal/` read as *gate not required*
here while CI ran the golden suite on it — you learn the golden moved **after** the push, in a
metered Actions cycle, which is the cost `make preflight` exists to avoid. It is now pinned by
`make sanity-checks`' `regression-surface` leg, because prose does not enforce. Ported from
parent #698 (ST89).

The path list marks a **coverage gap, not a cost**: the gate runs in ~8s wall clock, so when in
doubt just run it. (Do not port the parent's rationale, where the same gate costs ~95s and the list
exists to avoid paying for a chain the diff cannot reach.) When it applies and a golden moves, that
is a *decision* rather than a failure, which is why it stays local instead of being left to CI.
Check the data-drift falsifier in Key Commands before regenerating.

**Anti-drift.** Before a multi-step task, restate the goal and its success metric in one line. If a
step stops serving that metric, stop and ask. Before killing or demoting a strategy, require
evidence on the panel above rather than a single pooled number, and prefer demotion to deletion.

**Token efficiency.** Skills are dormant until invoked. Reach for the context-mode `ctx_*` tools on
any command or output over roughly 20 lines, and `/compact` at a logical boundary rather than
waiting for autocompaction. Delegate a heavy read to a subagent when the main-context clutter it
saves outweighs the startup cost.

**Delegation.** The main thread is the orchestrator: design, judgment, review and routing stay here.
Push bulk mechanical work down a tier. Use **sonnet** subagents for high-volume execution (vision
extraction, file sweeps, boilerplate, test triage), **haiku** for trivial one-shot lookups, and
**opus** subagents only as a quota escape valve for long parallel research — Opus sits below the
main thread in capability, so it conserves limits rather than buying better thinking. Every
subagent brief must be self-contained: goal, success metric and rubric inline, with no SoT or
memory re-reads. Confirm background work from the outside (`ps`, `journalctl`, `git status`) rather
than from an agent's own report.

⚠ **Only 2 subagents run at once.** A third launch is blocked outright, so any fan-out wider than
two has to be pipelined by hand in pairs and budgeted for wall-clock.

**Guardrail.** A `PreToolUse` hook (`.claude/hooks/guard-destructive.py`) blocks catastrophic Bash:
`rm -rf`, `git reset --hard`, force-push, DB wipes. If it blocks you, surface it rather than working
around it silently. It is **tracked and survives a reclone** (`.claude/` is a denylist). The hook
wrapper still fails **open** on a missing file, because `python3` exits 2 when it
cannot open a script and 2 is the block code. Write commit and PR bodies to a file and pass `-F` or
`--body-file`: a heredoc is the command payload, so quoting a hazard in a commit message trips the
guard, while a file is invisible to it.

## Fork lineage

A fork of `s10023/buibui-moon-trader-bot`, frozen at parent commit `635ed5a` and repurposed from a
Binance crypto bot into a yfinance-backed US-equities signal bot.

⚠ **Memory-tree paths here are DERIVED, never literal.** They used to be spelled out as
`~/.claude-personal/projects/-home-kng-repo-…`, i.e. one machine's home directory in an
always-loaded file, and after the 2026-09-18 host move all three pointed at nothing. Resolve them
with `tools/claude_home.py`, which selects the config root by testing `projects/<slug>` rather
than the root's existence:

```bash
PYTHONPATH=. poetry run python -c 'import sys; from pathlib import Path; from tools.claude_home import memory_dir; print(memory_dir(Path(sys.argv[1])))' .
```

**Sister memory** holds the parent's accumulated wisdom (strategy edges, regime classifier history,
F8/F9/T2 work, sweep findings, gate architecture) in **`MEMORY.md` inside the parent checkout's
memory tree** — `memory_dir(<the buibui-moon-trader-bot checkout>)`, which sits beside this one.
Read it when a feature exists in both repos. Skip the parent's Current State and its crypto-specific findings
(`smt_pairs`, `funding_reversion`, BTC/ETH/SOL cells, CME gap).

**Active work is driven by the master to-do**, `project_todo_master.md` in **this** checkout's
memory tree (`memory_dir(<this repo>)`; `make cadence-check` prints the resolved path),
which carries the north star and acceptance gates G1–G4 and is the single source of truth. Current
scope is correctness plus universe groundwork; XS-momentum forecasts and paper sizing wait for the
parent to pass G1, and Phase B (order layer, broker pick) is gated G3→G4.

⚠ **TA detector and sweep work is frozen** — no new boolean detectors, no tp_r / gate / threshold
sweeps. Scope: the equity signal engine's `DETECTOR_REGISTRY` book at `4h`/`1d`/`1wk`, and it is a
policy rather than a claim about moving averages or price structure anywhere else. This is an
inherited category verdict; see memory `project_parent_fresh_eyes_port.md`.

Historical design for the retired bring-up queue:
`docs/superpowers/specs/2026-04-10-tradfi-equity-fork-design.md`.

## Project overview

Buibui Wifey Wall Street Bot, a yfinance-backed US-equities signal bot. Phase A ships signals only:
a DuckDB analytics and backtest stack, a multi-strategy signal engine with Telegram alerts, and a
FastAPI plus Svelte web UI. Python 3.11+, managed with Poetry.

## Key commands

After any Python change:

```bash
make lint-py        # ruff format + lint (mutates); lint-py-check to verify without writing
make typecheck      # mypy strict
make test           # full pytest suite (make test-cov for coverage)
```

⚠ **The Makefile exports `PYTHONUTF8=1` to EVERY recipe, and on Windows that is load-bearing.**
Windows defaults a redirected stdout and every implicit text read to the ANSI codepage (cp1252),
and this tree's source, configs and fixtures carry em-dashes and ⚠ throughout. Measured
2026-09-18: the suite is **0 failed** with it and **41 failed** without — 40 `UnicodeDecodeError`,
38 `UnicodeEncodeError`, all `'charmap' codec`, concentrated in `test_video_fetch` (17) and
`test_systemd_units` (10). Nothing set it before, so `make test` was red for reasons unrelated to
any diff, and a session exporting it by hand got a green **its own shell was producing**.
⚠ **It covers `make`, and `make` only** — a bare `poetry run pytest` or a directly-run tool still
starts in cp1252, because **56** `read_text()` sites carry no explicit `encoding=`
(`grep -rn "read_text()" --include=*.py tools/ tests/ analytics/ cli/ signals/ utils/ web/
scripts/ | wc -l`). Linux CI is unaffected either way.

⚠ **The second half of that class reads as a NULL, not as an encoding error, and it costs an hour
cold.** `subprocess.run(…, text=True)` with no `encoding=` decodes via cp1252 and raises inside the
reader **thread**, so `stdout` comes back `None` and the failure surfaces far away as
`'NoneType' object has no attribute 'splitlines'`. **20 sites across 7 `tools/` files** are still
like this, so the documented direct invocation
(`PYTHONPATH=. poetry run python tools/post_branch_checks.py …`) fails this way on Windows unless
`PYTHONUTF8=1` is in the environment — prefix it, or go through `make`. Pre-existing, not a
regression; the parent fixed its own copy of this in #769.

⚠ **`.gitattributes` pins `*.sh eol=lf`, and the hazard it closes is LATENT rather than loud.**
Measured on this host *before* the file existed: all three `deploy/*.sh` were already CRLF in the
working tree and nothing had broken, because nothing had yet read **data** through them. Bash
strips CR from a script's **source** but not from data a script **reads**, so the scripts keep
running while the values they parse grow a trailing CR. ⚠ **It cannot reach `.env`**, which is
gitignored — git never sees the file — which is why `deploy/windows/load-env.sh` strips CR itself
and `python-dotenv` strips it again independently. **Do not remove either strip**: without one,
`TELEGRAM_BOT_TOKEN=123:abc` loads as 8 characters rather than 7, the token prints correctly,
Telegram rejects it, and the symptom is "the bot stopped alerting" over a config that looks
perfect. wifey has two bot tokens, so that failure has two chances to fire and the second is the
channel with a human audience.

`make status` prints every repo-shape number: tests, files, CLAUDE.md size, handoff lines,
MEMORY.md size and bullet count, audits, skills, tools. **Print these rather than writing any of
them into a doc** — each has a history of being quoted stale.

`make sanity-checks` runs the eight mechanical `/sanity-check` checks from
`tools/sanity_checks.py`: fork drift against invocable artifacts, parent-repo leakage in skills,
dead repo paths, package coverage in `.claude/context/`, the three hand-maintained router lists,
`[strategy_params.X]` keys, README's CLI coverage, and `regression-surface` — this file's
`make test-regression` trigger list against CI's own paths filter, compared **verbatim** because a
paraphrase cannot be diffed by anything. ⚠ **It gates rather than advises** — it exits
non-zero, `tests/test_sanity_checks.py` runs the same sweep inside `make test`, and CI's
`markdownlint` job runs it **unconditionally**. That last placement is load-bearing: the test job
sits behind a `**/*.py` paths filter, so on a docs-only PR the pytest gate never fires — on exactly
the change the check exists to catch. Legs needing project imports or the gitignored watchlist
degrade to `SKIPPED`/a note rather than a finding, because a check that is never green stops being
read.

`make cadence-check` reports which recurring tasks are **overdue**, read from
`docs/plans/task-marks/` — one file per task holding an ISO-8601 UTC timestamp, stamped with
`make cadence-stamp TASK=<slug>`. Ported from the parent's `daily_check.py` task-mark block, with
three divergences, each because the parent's *reason* does not hold here: **the checker is
TRACKED** (upstream's lives in gitignored `docs/plans/` and dies on a reclone — the same failure
the `.claude/` denylist inversion fixed), **the mark's CONTENT is authoritative rather than its
mtime** (upstream writes a timestamp and reads `st_mtime`, so the content it writes has no
consumer, and mtime moves for reasons that are not runs), and **stamping is a flag** rather than a
`date -u … > path` redirect a typo can misdirect.

⚠ **A MISSING mark reads as OVERDUE on purpose** — a lost mark must shout, where the opposite
mistake reports "fresh" for a task that has never run once. ⚠ **It is ADVISORY and must never gate
CI**: the marks are gitignored, so a fresh clone sees every one absent and would report every task
permanently overdue. A check that can only be red in CI is worse than no check. Two tasks are
declared (`/sanity-check`, `/sync-parent`, both 7d); the four inclusion rules and the reason each
rejected candidate fails one live beside `TASKS` in `tools/cadence_check.py`. **Nothing auto-runs
anything** — these only record that a run happened. The report's second section is the
**audit-verdict → SoT ownership join** (parent #641's other half): an actionable verdict in
`docs/audits/INDEX.md` that no SoT row names is a finding with no owner — the class where a research
chain of audits owns every link except the last, measured upstream as a BUILD verdict sitting
unowned for seven weeks in a test-enforced index.

`make preflight` runs the suite against a **fresh clone of HEAD** and **REPLACES** that branch's
`make test` rather than adding to it — it mirrors that recipe's argv, pinned by a test so the
claim has an external referent. ⚠ **The correct loop is therefore `targeted pytest <files>` →
commit → `make preflight`, and running BOTH is ~4 min wasted.** The trap is structural rather
than forgetful: preflight **REFUSES on a dirty tree**, so it can never be the inner loop, which
makes reaching for `make test` feel obligatory. Sessions have paid this three times (latest #289),
because neither the handoff note that named it nor this line prevented it. ⚠ **The tell is the
trigger: reaching for `make test` at all is the moment to ask whether a PR is coming.** Run it in
`/post-branch` **phase 5**, after the doc commits and
before `gh pr create`. It catches what no local run can: a gitignored path that exists on this box
and nowhere else (`config/stocks.json`, `.claude/sensitive-terms.txt`, `docs/plans/`,
`analytics.db`). ⚠ **CI already IS this gate** — it closes **timing, not detection**, and the
timing is the whole cost here, since detection after a push means a metered Actions cycle, a red
PR and a visibility flip to read the failure at all.

⚠ **It REFUSES on a dirty tree, and that refusal is the load-bearing part** — a clone sees
committed state only, so an earlier run tests stale HEAD and reports green. ⚠ **Two blind spots**:
an **absolute** `$HOME` default is identical inside the clone (`EXTERNAL_ROOTS`' shape), and it
only reaches production-side breakage where a *test* exercises the path. ⚠ **Through `make` the
exit-code taxonomy is invisible** (0 pass · 1 real failure · 2 REFUSED · 3 INFRA), so read the
banner: REFUSED and INFRA are **not** suite failures. Narrative: `context/tools.md`.

`make wait-ci PR=<n>` waits for a PR's checks and `make wait-ci-main` is the **flip-back gate**
(`--branch main --min-jobs 5`); both report whether the checks actually *ran*. `tools/wait_ci.py`
exits **3** on `steps=0`, the Actions-allowance failure that renders exactly like a real one (flip
the repo public, never debug it), **1** on a genuine failure, and **4** when it settles green but
could not read the step counts — that last state used to print "all green, all executed real
steps", asserting the one thing it had failed to observe. A `gh` failure now **raises**; it is
never turned into data, which is how a hand-rolled waiter once reported `jobs=0` against a live
`total_count=2`. ⚠ **Through `make` you see none of these codes** — GNU make collapses any recipe
failure to its own exit **2**, so branch on the printed banner, or call
`poetry run python tools/wait_ci.py --pr <n>` / `--branch main` directly when you need the code.

⚠ **Run `make test`, `make test-regression` and any CI wait in the BACKGROUND** — Bash's
`run_in_background: true`, then wait for the task notification. A foreground run burns the turn on a
job that reports nothing until it ends, and `.claude/settings.local.json` allowlists these targets,
so it draws no permission prompt either. A machine-local `PreToolUse` advisory
(`.claude/hooks/advise-foreground-run.py`) nudges it; like the other hooks it is now tracked.
It keys on the `run_in_background` **tool parameter** rather than the
command string — the two runs are byte-identical, so no string match can separate them — and is
head-anchored so it does not fire on its own documentation. `--selftest` pins both discriminators.

⚠ **Never write a `pgrep` waiter for a background job — wait for the task notification.**
`until ! pgrep -f 'pytest tests/'` matches the polling shell's own argv and waits on itself; the
bracketed fix exits instantly instead, so an empty log reads as "done". If a waiter is unavoidable,
assert a positive marker (`grep -q ALLDONE`), never an absence. Both variants were already in memory
`reference_env_gotchas.md` and were not read, which cost three mutually-deadlocked waiters and an
hour. That is why the rule is here rather than there.

`CATCH_UP=1 make go-live` is the **one-shot** dispatch, run by hand or by the opt-in
`wifey-signal-watch.timer` (Mon–Fri 08:30 UTC — **nothing installs it**, and installing is
operator-only because Telegram goes out). There is still no wifey *daemon*: the unit is
`Type=oneshot`, so "restart signal watch" stays a non-instruction — start the service or run the
target. ⚠ **`buibui-signal-watch.*` in systemd is the PARENT's**, and a same-shaped
`wifey-signal-watch.*` now sits beside it, so read `WorkingDirectory` rather than the name. Ratings
reload every run. **Run it pre-open, not after the close**:
`1d` bars stamp 04:00/05:00 UTC and close the next day, so at the bell that session's daily bar is
still forming.

⚠ **The alerting schedule is Wed/Thu/Fri, and it is a consequence of `day_filter`, not a setting.**
A pre-open run sees the PREVIOUS session's bars, and `tue_thu` suppresses on the bar's **open
weekday**, so a Mon run (Fri bars) and a Tue run (Mon bars) can never alert — measured identical
dispatch for a Mon–Fri and a Wed–Fri cadence. They still earn their keep on sync, ledger and
outcome backfill, so this is true of the **Telegram leg only**. ⚠ **Skipping a run day DESTROYS
that day's alerts rather than deferring them** — the next run's catch-up consumes the primary
watermark without dispatching, by design. Friday is worth **66% vs 44%** of the alert surface.

⚠ **`max_alert_age_hours` (shared base, 24.0) is what makes the session's FIRST 4h bar
deliverable at all — and it is INERT without `CATCH_UP=1`**, since only catch-up emits an event
for a non-latest candle in the first place. Only the newest closed candle used to dispatch, and
under one run a day the 13:30 UTC bar can never *be* it — 120 of 351 ledger candles, 34%, silently ledger-only. `0.0`
restores that old rule and is the documented escape hatch; negative is refused. ⚠ **Raising it
cannot re-send history** (a consumed watermark stays consumed) and ⚠ **it does not recover a
skipped day** (~87h stale) — that stays the cadence habit above.
⚠ **`fired_at_ms` is NOT a dispatch record** — `upsert_signal_outcome` overwrites it on every
re-detection, so the ledger holds no dispatch history; the `:wife` watermark is the only oracle,
since backfill marks primary alone and a real send marks both.
Audit: `docs/audits/2026-08-25-dispatch-recency-window.md`.

Re-run `make universe-stamp-listed` after any membership or backfill change: a new constituent
arrives unstamped, and unstamped is the permissive value. The weekly `wifey-universe-sync` does not
move the `listed` seam — it never adds a member, only extends existing series.

Markdown changes: `make lint-md`, which covers `.claude/` skills and context, so a skill edit lints
like any other file (`make lint-md-fix` applies the auto-fixable subset). **Do not re-add a
`!.claude` exclusion to `.markdownlint-cli2.jsonc`** — the tree accumulated 245 issues while it was
there, and an excluded-tree failure is silent.

After adding or renaming a file in `docs/audits/` or `docs/superpowers/specs/`: `make docs-index`.
Both `INDEX.md` files are generated by `tools/docs_index.py`, and `tests/test_docs_index.py`
regenerates and compares them byte-for-byte, so an unindexed audit or spec fails the suite.
`make docs-index-check` verifies without writing.

**A new audit must state its verdict as PROSE under a Verdict heading**, or the suite fails
(`TestEveryNewAuditExposesItsVerdict`). A table, blockquote or `**Date:**` line under that heading
is deliberately rejected — each renders as a plausible-but-wrong verdict. This repo already holds
the rule as prose (every audit closes FOUND / BOUNDED / EXCLUDED / BLOCKED); the gate exists because
prose does not enforce. The consumer that makes it mechanical is the generated `docs/audits/INDEX.md`,
which renders each verdict in a column, so an unparseable one shows up as an empty cell that reads
exactly like an audit that reached no conclusion. **10 pre-2026-08-21 audits are grandfathered in a
frozen set that can only SHRINK** — fixing or deleting one without removing it from the set fails the
test, so a spent exemption cannot quietly re-admit the blind spot. ⚠ **Legibility and ownership are
separate legs.** The ownership half is `make cadence-check`'s **verdict→owner join**: an actionable
verdict (FOUND / CANDIDATE / EXIT-FIXABLE / UNBLOCKED, unless settled) that no SoT row names is a
finding, and green is reachable two ways — do the work, or record in the SoT where it was already
done. Advisory like the rest of that tool, since the SoT is machine-local. Both halves ported from
parent #641.

UI or API changes: `make web-build` for a production bundle, `make web-dev` for the Vite dev
server, `make web-check` for `svelte-check` types without a build.

Two DB maintenance targets sit outside `db-update` and neither is part of any routine flow.
`make db-prune-backtests` runs `scripts/db_prune_backtests.py` (hard cutoff 30d; soft cutoff 7d
keeping the top 10 per strategy × symbol × timeframe × day_filter × adr_threshold). ⚠ **`make
clean-db` deletes `analytics.db` and its WAL outright** — it is a wipe, not a refresh, it is
gitignored single-copy state, and `guard-destructive.py` blocks it. Take a `make backup` first.

`make db-update` is the routine refresh after backtest or strategy changes
(`db-update-backtest` → `db-update-recalibrate` → `regression-update` → `check-dead-surfaces`).
The last step reports `(strategy × timeframe)` cells where declaration and output disagree in
either direction: declared-but-dead, so a dead surface cannot hide behind rows that merely exist,
and rated-but-undeclared, a `confidence_ratings` row outliving the config that produced it. It
never blocks the refresh, but the completion banner is conditional on it. Run
`make check-dead-surfaces` alone for a non-zero exit.

⚠ **A golden diff from that run is usually data drift, not your change.** `regression-update`
re-derives the fixture parquets from a DB that has moved on. The success banner prints the
falsifier (`git checkout -- tests/fixtures/ && make test-regression`); if it passes, revert the
goldens rather than shipping them. This does not cover a `confidence_ratings` star move — attribute
those by re-running the sweep twice over one fixed window (`/db-update` step 3).

`make backup` writes a verified copy of `analytics.db`, all of `docs/plans/` and the **memory tree**
to `~/backups/wifey` (`make backup-dry-run` to preview). All three are single-copy and unreachable
by git: `git ls-files docs/plans/ | wc -l` returns 0, so `git clean -xdf` deletes the entire research
pipeline's output with no prompt, and `analytics.db.bak` is an undated unverified byte copy rather
than a backup. Coverage is a denylist over a wholesale copy, deliberately not the parent's
allowlist, because an allowlist over a single-copy tree defaults to uncovered. Rationale, restore
procedure and the opt-in timer live in `deploy/README.md`.

⚠ **`signal_state.json` is covered as of 2026-09-18, and it was the one watermark that was not.**
It sits at the repo **root**, outside the `docs/plans/` tree the denylist walks, so no glob
reached it. Losing it is silent in **both** directions — no error, and no burst of stale alerts
either: every key comes back cold, the cold-start guard then keeps only the latest closed candle,
and `CATCH_UP=1` replays nothing. The parent lost **three days of fires** that way; bars and
outcome resolutions both recovered, only the fires depend on it. ⚠ **It lands in `BACKUP_FILES`,
not in a `LEDGERS` array** — wifey has no such array, so the parent's instruction (#771) does not
port verbatim.

⚠ **`MANIFEST.json` is SERIALISED, never `printf`'d.** It was a block of format strings, which is
correct only for values containing nothing JSON must escape — and `source` is an absolute path,
so on Windows `C:\Users\…` made `\U` an illegal escape and **every manifest this host wrote was
unparseable**. `backup_check.py` catches `JSONDecodeError` and degrades, so the symptom was
`make backup-check` reading STALE forever: a check that can only be red, which this file
elsewhere names as worse than no check. `external_roots` was already safe because it alone went
through `json.dumps`; routing every field that way is what stops the next added field from
reintroducing it.

⚠ **A denylist defaults to covered only within the tree it is applied to.** `BACKUP_DIRS` and
`BACKUP_FILES` both resolve against `$REPO`, so the memory tree — which holds the SoT to-do — sat
uncovered by construction rather than by judgement until `EXTERNAL_ROOTS` was added. Anything
outside `$REPO` needs an entry there, landing **inside** each snapshot so it inherits the atomic
publish and retention and the off-site leg needs no change. An absent root warns and records
`files: 0` in `MANIFEST.json` rather than failing the run, because a guard that costs you the
backup is worse than the gap it closes. Pinned by `tests/test_backup_local_coverage.py`; the two
env knobs it steers with (`WIFEY_REPO_ROOT`, `WIFEY_PYTHON`) exist so the test can drive a real run
instead of `--dry-run`, which would pass whether or not the copy happened.

`make backup-offsite` is the leg that survives disk death: an `rclone sync` of that same root to
`$WIFEY_BACKUP_REMOTE`. ⚠ **`sync` mirrors deletions in BOTH directions**, so always
`make backup-offsite-dry-run` after touching the remote. wifey has its **own** rclone remote
(`gdrive-wifey:snapshots`) pinned to its own Drive folder — that structural separation, not the
script's guards, is what keeps it off the crypto parent's backups on the same account. The
in-script intruder guard compares **top-level entries only**, so it cannot tell a same-shaped
sibling tree from wifey's own; `tests/test_backup_offsite_guards.py` pins that hole deliberately
and is the script's only gate, since no CI step reads `deploy/`. Never paste `rclone config`
output anywhere — it carries a live refresh token.

⚠ **A green off-site timer does not mean the backup is current** — but the reason CHANGED on
2026-08-19 and the old one is no longer why. **Both legs are now scheduled**: `wifey-backup.timer`
(local, installed 2026-08-19 16:06) and `wifey-backup-offsite.timer`, both `enabled`. Until then
only the off-site leg ran, mirroring a `~/backups/wifey` that only a manual `make backup`
populated — **that sentence stood here after it stopped being true**, which is the drift this
paragraph now exists to correct. The residual risk is narrower and real: **a timer that silently
stops looks identical to one that is working**, since alerting is failure-only, so verify a
snapshot's age rather than a unit's `enabled` state. The general rule survives both versions: a
scheduled job attests only to the step it performs, so a green light means "the data is current"
only if something checks the *input's* age. Rationale and the two candidate fixes are in
`deploy/README.md`. ⚠ **This is deliberately NOT a `cadence-check` line** — no human action
clears it, so it fails that tool's inclusion rule (4); it wanted an observed-state probe, and
`make backup-check` is now it.

`make backup-check` reports **the age of the newest verified snapshot**, from `captured_at_utc`
inside `MANIFEST.json` — never an mtime, which moves for a restore or an `rclone` round-trip and
fails in the direction that reports *fresher than reality*. ⚠ **`daily/` and `weekly/` are
DIFFERENT ARTIFACTS**: only `daily/` carries a manifest, `weekly/` is a parquet export, and
grading them together prints a permanent warning about a directory that is exactly as intended.
Every unreadable state reads STALE, an absent root is its own verdict, and it is **ADVISORY — it
must never enter `make test`, `make sanity-checks` or CI**, since the backup root is machine-local
state no clone has. ⚠ **Two holes, both deliberate**: it measures the **local tree only** (the
off-site mirror needs a network call, and a probe that fails when offline reports a backup problem
for a connectivity one), and it is a **pull, not a push** — nothing runs it on a schedule, so it
shrinks the invisible-timer gap rather than closing it. Wiring a staleness refusal into
`backup-offsite.sh` stays a user call: a guard that costs you the backup is worse than the gap it
closes. Narrative: `context/tools.md`.

`make freshness-check` is the same probe for the two surfaces `backup-check` cannot see, and it
answers **two different questions that must not be graded the same way**. Ported from the
parent's `ohlcv_freshness.py` (#681/#698).

⚠ **Age is in NYSE SESSIONS, never wall-clock — this is the port's one hard divergence and a
verbatim copy is WRONG here.** The parent computes `(now - newest) / bar_ms` against a 24h tape;
on an RTH tape `4h` is 2 bars/day rather than 6, so a healthy two-session-old series reads ~12 bars
behind and every Monday adds a phantom weekend. It reds everything forever, which is the failure
mode the parent's own docstring warns about. Sessions come from `analytics/trading_calendar.py` and
bars-per-day from `cost_model.BARS_PER_DAY`, imported rather than forked.

⚠ **A weekly series cannot be graded on the daily footing.** A `1wk` bar stamps on the week's
Monday open and closes Friday, so a perfectly refreshed weekly series trails a daily one by four
sessions for reasons that are not staleness. The tolerance is `base + cadence gap +
max(0, sessions_per_bar - 1)`, with `sessions_per_bar` the reciprocal of `cost_model.BARS_PER_DAY`
rather than a new constant. Without that third term every weekly series reds forever — the parent's
wall-clock failure mode in a new place. `4h` and `1d` close inside a session, so the term is 0 for
both and their shipped tolerances did not move.

⚠ **The watermark is a DISPATCH oracle, not a RUN oracle, so it is dated but UNGRADED.** It
advances only when a candle is consumed by an alert, and `day_filter = tue_thu` makes dispatch
intermittent by design — a Mon run (Fri bars) and a Tue run (Mon bars) can never alert. The first
build graded it and printed STALE at 4 sessions on a healthy system, with run evidence on two of
the three intervening sessions (the third carries no row, which is not evidence of no run — a scan
detecting nothing new writes nothing). **Run-liveness is the ohlcv leg instead**: a `go-live` run's first act is a
watchlist sync, so fresh watchlist bars *are* the evidence a run happened, and that quantity does
have a declared cadence. The only finding this leg makes is **no watermarks at all**.

⚠ **The 505-member research universe now HAS a refresher, and whether it is graded depends on the
BOX.** `make wifey-universe-sync` is the incremental target (4h/1d/1wk) and
`wifey-universe-sync.timer` runs it **Sat 10:00 UTC** — opt-in like every unit here, so **nothing
installs it**. The probe reads whichever scheduler the host has — `systemctl --user is-enabled` on
Linux, `Get-ScheduledTask` under `\wifey\` on Windows, where the field is `State` rather than
existence because the offsite task is registered **and deliberately disabled** — and grades the 505
members on a weekly tolerance only where the job is enabled; everywhere else it reports the absence
and names the timer. Grading unconditionally would print ~1,100 findings on a box that installed nothing, and a
leg that is never green stops being read. ⚠ **`sync` is not `backfill`** — the backfill re-fetches
from 2018 and stays hand-run for a NEW constituent, which arrives with no bars and which `sync`
skips by design; that is also why **400 of 505 `4h` series stay absent** (yfinance's intraday
window), so this timer **cannot close the 21% `4h` coverage gap**.

⚠ **The staleness it fixes was measured, and it is a live hazard for any pooled cross-section.**
On 2026-08-26 the 13-symbol watchlist ran to the previous session while **493 of 526** `1d` series
had a last bar on or before **2026-06-18** — a breadth query would mix ~10-week-stale names with
the fresh watchlist inside one query, and the fresh set is exactly the mega-cap tilt `4h`'s 21%
coverage already carries, so the two skews compound. **The pundit ledger still has no timer**: run
`make wifey-pundit-sync` by hand before a ledger glance.

⚠ **ADVISORY — it must never enter `make test`, `make sanity-checks` or CI**, for `backup-check`'s
reason: both legs read machine-local single-copy state (`analytics.db`, the gitignored
`signal_state.json` and `config/stocks.json`) that no clone has. Every unreadable state degrades to
a reported absence rather than to freshness, and an absent watchlist grades **nothing** rather than
everything. The pure grading half *is* in `make test` (`tests/test_freshness_check.py`), positive
control included. Narrative: `context/tools.md`.

## CLI

`wifey.py` is the single entry point. Each Makefile `wifey-*` target wraps the equivalent
invocation.

| Subcommand | Purpose |
| --- | --- |
| `signal watch \| test` | Live signal daemon, historical replay |
| `analytics backfill \| sync` | OHLCV ingestion |
| `backtest` | Run and save backtests (sweep, combo, cross-TF) |
| `digest` | Pre-canned analytics queries |
| `param-audit \| param-sweep` | WFO parameter tools |
| `recalibrate` | Refresh star ratings |
| `web` | Start the FastAPI backend |

Equity price and position monitoring lives in the web UI under `web/`; the legacy
`wifey monitor` subcommand and the `monitor/` package are gone.

## Project structure

The deep reference lives in `.claude/context/`, and the pointers below are a session's only path to
it. Follow them before working in an area. Verdicts and footguns stay in this file on purpose: they
guard against re-litigating settled research, and a guard rail behind a pointer is not a guard rail.

A footgun entry holds what a session needs before it acts — the rule, the enforcing mechanism, any
operational constant still in force, and the transferable lesson. The discovery narrative and full
measured impact belong in the linked committed audit, or in `.claude/context/footguns.md` for the
entries that have no audit of their own.

| Package | What it is | Deep reference |
| --- | --- | --- |
| `wifey.py` · `cli/` | Entry shim delegating to `cli.main:main`; argparse subcommand package with `_common.py` helpers | — |
| `analytics/` | DuckDB analytics layer: `store/`, `strategies/` (**16** of them registered for dispatch), `backtest/`, `signal/`, `stats/`, `research_guards/`, `sweep_guard.py`, `audit_guard.py`, `db_retry.py`, plus data ingest, quality and calendar | `context/analytics.md` |
| `analytics/{forecast,xsmom,lowvol,xasset,pead,gapfill,velocity,exits}/` | P2/P3 research sleeves and the exit diagnostic (verdicts below) | `context/analytics.md` |
| `analytics/insider/` | H-024, the first NON-PRICE sleeve, so the TA freeze does not bind it. Phases 1–3 are built (`form4` · `classify` · `book`/`replay`/`report`, `make wifey-insider-audit`) and **no return has been read yet**, so it carries no verdict below. The pre-registration is frozen at four trials with routine-arm placebos as controls; Amendment 4 records what phase 3 had to operationalise | `context/analytics.md` |
| `signals/` · `utils/` | Alerting and dedup daemon (detection lives in `analytics/`); shared Telegram, yfinance and EDGAR clients; the two config-universe loaders | `context/signals.md` |
| `web/` | FastAPI backend plus Svelte 5 / Vite UI | `context/web.md` |
| `tools/` | One-shot analysis, audit and research-ingest scripts; outside the daemon and CLI surface | `context/tools.md` |
| `scripts/` | Three maintenance one-shots, distinct from `tools/`: they act on the DB or the test fixtures rather than producing research output. `db_prune_backtests.py` (`make db-prune-backtests`), `extract_regression_fixture.py` (called by `make regression-update`, not run directly), `profile_suite.py` (no target; suite profiling, see memory `project_suite_runtime_profile.md`) | — |
| `trade/` | Empty placeholder — both files are 0 bytes. The parent's Binance Futures opener was dropped at fork time and nothing replaced it; `make wifey-open-trades` now fails loudly. An order layer would land in Phase B | — |
| `tests/` | pytest suite; tests import from lib modules and pass mock dependencies directly | — |
| `migrations/` | **Seven** one-shot migration scripts, run by hand. All seven refuse to start without a `.bak`; only **001/002** rewrite `run_id` and cascade to `backtest_trades` — 003/004/005 target `signal_alert_outcomes`, whose key carries no measured value, so an in-place `UPDATE` is correct there, and **006/007 target `ohlcv` and DELETE rather than rewrite**. ⚠ **007 is the first whose rows cannot be recovered by refetch** — 006's deleted bars would simply be re-quarantined, while 007 removes a wrong instrument the provider still serves under the right ticker. Check what the target table's key is made of rather than following the precedent. Routine schema changes go through `analytics/store/schema.py`'s migration list | `context/migrations.md` |
| `.claude/hooks/` | Three `PreToolUse` hooks on `Bash`: a destructive-command guard, a foreground-run advisory, and an inline `gh pr create` reminder. **Two are files here, the third is inline in `.claude/settings.json`**, which registers all three. Tracked since the 2026-08-20 denylist inversion, so they survive a reclone and ruff + mypy cover them | `context/hooks.md` |
| `config/` | `stocks.json` (gitignored 13-symbol live watchlist), `universe.json` (committed 505-member research universe), `strategy_params.toml` (shared base inherited via `extends`), `youtube_channels.toml` (gitignored; `.example` committed) | `context/config.md` |
| `deploy/` | `backup-analytics.sh` (local leg), `backup-offsite.sh` (rclone leg), `notify-failure.sh`, and opt-in `wifey-*` systemd user units — backup ×2, the templated alert, **signal-watch** and **universe-sync**. `deploy/windows/` is the same four jobs' Windows half: `job.sh` (the unit file's imperative side), `load-env.sh` (`EnvironmentFile=`, hand-rolled, strips CR) and `install-tasks.ps1`. ⚠ **Do NOT re-sync `job.sh` from the parent's** — that one shims `deploy/run-job.sh`, which wifey does not have. Nothing installs either half; every one is `Type=oneshot` or a one-shot task, so there is still no wifey daemon | `deploy/README.md` |

### Sleeve verdicts

Eight sleeves have been built and measured on equities, and every one is non-positive. ⚠ **Do not
rebuild a shelved sleeve.** The free-data edge-hunt arc was concluded on 2026-06-24
(`docs/audits/2026-06-24-honest-exit-free-data-edge-arc.md`); starting a new free-data hunt needs an
explicit user go, and a reopening is per-candidate rather than a re-opening of the arc.

A guardrail firing means the *construction* failed its own neutrality precondition, so that cell is
not evidence about the underlying premise.

| Sleeve | Verdict |
| --- | --- |
| `forecast/` EWMAC trend | **G2 FAIL** — portfolio Sharpe −0.05, negative even pre-cost, so a signal failure rather than a cost failure |
| `xsmom/` cross-sectional momentum | **G3 FAIL** — combined Sharpe −0.156 @2bps, negative at 0bps; `corr_to_trend` +0.62, so not a diversification win either |
| `xsmom/residual.py` residualised XS | **FAIL** — committed `broad_residual_skip` Sharpe +0.15 @2bps, DSR 0.44, boot_lo<0. The long-only leg's +0.88 is survivorship- and beta-confounded and is not the gated cell |
| `lowvol/` low-beta / BAB | **FAIL** — committed cell Sharpe −0.069 @2bps, DSR ~0.03; realized-beta guardrail fired (β +3.9) |
| `xasset/` cross-asset TSMOM | **FAIL (clean)** — `broad_ls` +0.41 cost-free, +0.36 @2bps, never ≥0.7; PBO ~0.79. The equity-β guardrail held (β −0.083), so the construction diversified as designed and the premium is simply too weak in free-ETF proxies |
| `pead/` PEAD-lite | **FAIL** — `broad_ls` +0.10 @2bps, DSR 0.20; β guardrail fired (β ≈ +113, governor saturation on sparse daily cohorts). The controlled mega arm (β −0.40) showed negative drift (−0.53) |
| `gapfill/` gap-fill magnet | **EXCLUDED, direction refuted.** Cost-free the magnet returns −0.460, so gaps continue rather than revert; the post-hoc inverse is +0.392, below the 0.7 bar before a single bp. At ~211× daily gross turnover the 1bp fee alone costs ~0.9 Sharpe, so neither direction is tradeable. ⚠ **Never quote "90.3% of gaps fill within 60 sessions" without its null** — a matched placebo level fills 88.9%, so the gap-specific lift is +1.5pp, peaking +5.7pp at 5 sessions and gone by 60. The descriptive claim is true, almost entirely diffusion, and inert. Audit: `docs/audits/2026-08-14-edge-hunt-5-gapfill-magnet.md` |
| `velocity/` velocity alternation | **EXCLUDED as a null**, in contrast to `gapfill`. The β guardrail fired (−1.646) so the raw −0.265 is contaminated; beta-hedged −0.169 at alpha t −0.49 means no effect in either direction. The decomposition is the finding: `velocity = depth / duration` sits at corr +0.499 / +0.546 to its own component controls and performs indistinguishably from depth alone. Cost is not the constraint (24.5× daily gross; gross is already non-positive). ⚠ **`long_only` +0.649 gross hedges to +0.004** (alpha t +0.01), i.e. 100% market beta, so `deploy_grade`'s long-only leg reads market exposure — latent, never bound, and changing that gate is a user call. The time-series form the pundit described is untested and not queued. Audit: `docs/audits/2026-08-14-edge-hunt-6-velocity-alternation.md` |
| `exits/` MFE-MAE diagnostic | **EXIT-FIXABLE at the cohort level** (n=264, 264/264 scored). Of the 157 losses that could show excursion, 43.9% reached ≥1R before stopping (CI 36.4–51.8%). Still blocked per-edge (0 of 30 loss cells reach n=30) and the whole ledger predates the outcome fix. Audit: `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md` |
| `exits/` policy replay A/B | **BOUNDED** — the lever's ceiling is +0.368R of paired uplift and it buys no measurably profitable book. All three arms beat `fixed` on a paired bootstrap CI clear of zero, and the effect is entirely the time lever: `time_only` +0.317R exceeds the full `composite` +0.297R, so bolting breakeven and partial onto a time-stop makes it worse. ⚠ **A paired CI certifies "A beats B", never "A makes money"** — no arm's own mean R clears zero once the 31 ET session days rather than the 267 alerts are the unit, and the swept maximum over the 10×2 time-stop grid is arm-level t +2.52 against a Bonferroni bar of 2.81. Baseline avg_r −0.176 at t −1.84; both parameters in-sample. "Mean R of an open position peaks at bar 3" is a mislabel: that table is the arm's own avg_r at `time_stop=k`, and positions genuinely still open at bar k improve monotonically (+0.283 → +0.951) because a stop removes losers first. Audit: `docs/audits/2026-08-14-exit-policy-ab-v1.md` |

### Footguns

Read these before touching the mechanisms they name.

⚠ **`analytics/store/_common.py::_upsert` must use explicit `conn.register` / `conn.unregister` in
try/finally.** The implicit replacement scan causes malloc heap corruption.

**Open every write site in `analytics/` and `web/` through
`analytics/db_retry.py::connect_with_retry`.** The one deliberate exception is
`web/api/routers/stats.py`'s cache write, which sits on the request path where a ~52s retry would
block the response the cache exists to speed up. Narrow every `except duckdb.IOException` with
`is_lock_conflict`, because DuckDB raises that one class for all I/O failures and an unnarrowed
handler reports a missing or corrupt database as "busy, try again in a few seconds". `read_only=True`
does not admit a second process: a writer refuses a read-only opener with the same `Conflicting
lock` message a second writer gets. Narrative: `context/footguns.md`.

**Import `DEFAULT_DB_PATH` from `analytics.store` or `analytics.data_store`** (it lives in
`analytics/store/_common.py`) rather than redefining it in a runner.

**Causality is enforced by a test, not by review.** `tests/test_lookahead.py` is a truncated-series
property test asserting that no detector or backtest fill depends on bars after its `open_time`.
`_KNOWN_LOOKAHEAD_DETECTORS` is empty and all 16 detectors pass. Audit:
`docs/redesign/phase0-lookahead-audit.md`.

**A per-strategy flag whose correctness depends on a second flag belongs in the shared base**, not
in one day-filter config. `adr_suppress_threshold` and `volume_suppress` select on quantities
correlated at +0.65, so declaring both without `adr_exempt = true` discards ~99% of a strategy's
signals. Enforced by `load_signal_config::voided_volume_gates`, which refuses the pairing. A test
asserting that a config value parsed can never detect that the parsed value produces nothing.
Audit: `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

**The ADR gate is intraday-only**, enforced by `adr_gate_applies(timeframe)` with `timeframe` as a
required arg so mypy forces every call site to state it, and unknown timeframes falling closed. The
gate needs more than one bar per calendar day; on `1d` and `1wk` the ratio stops measuring
exhaustion and its direction guard makes `chasing` true by construction. It stays dispersed
(p25 0.71 / p75 1.21), which is why a degenerate gate reads as functional. Re-derive any
crypto-inherited constant against equity bar counts — `4h` RTH is 2 bars/day, not 6. Note that
`check-dead-surfaces` cannot see this class: it finds exact zeros, and a 77–82% haircut leaves the
cell non-empty. Audit: `docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md`.

**One `bt_days` feeds both the OHLCV cache and `run_scan_cycle`.** Cross-check a recorded parameter
against a recorded observable (`data_end_ms - data_start_ms` found this in one query), and remember
that when a value reaches its consumer through a cache, fixing the consumer's argument fixes
nothing — find who populates the cache first. `passes_ev_gate` returns `True` below `min_trades`,
so this gate fails open and has no loud failure mode.
Audit: `docs/audits/2026-08-06-live-ev-gate-window.md`.

**`upsert_backtest_run` requires an `origin` kwarg naming the writer.** `backtest_runs` has four
writers, and a key hashed from the parameter tuple alone let the daemon's single-strategy row
replace the competed sweep row in place. `"sweep"` is deliberately unsuffixed in the hash so
historical `run_id`s still resolve. When a table has more than one writer, the writer belongs
in the key: a provenance *column* cannot help if the loser is deleted before any query runs. Check
a producer's output count against what the consumer stored.
Audit: `docs/audits/2026-08-07-backtest-runs-writer-collision.md`.

⚠ **`conflict_resolver` stays off inside the sweep.** It reads `confidence_ratings`, so enabling it
in the pipeline that produces them is a fixed-point iteration rather than a gate — three consecutive
passes went 108 → 66 rows differing, damping but not converged. Five gates are on in
`config/strategy_params.toml`, the shared base and deliberately not a Makefile flag; with five, two
consecutive passes differ on 0 of 160 rows. Enforced by `TestSharedBaseGateState`. Check data-flow
direction before enabling a gate in the producer of its own input, and note that "it converges"
needs three points rather than two. The live path is unaffected, since it reads ratings already
written. Audit: `docs/audits/2026-08-07-live-parity-ratings-sweep.md`.

**The live EV gate counts `long_closed_trades` / `short_closed_trades`**, matching the direction
whose `avg_r` it tests; `README.md` had documented those semantics all along. A gate that fails open
inverts the meaning of "stricter" — at `min_trades = 20` the `1wk` gate reaches 100% bypass. A test
that re-implements the code under test can never falsify it, so extraction is a prerequisite for the
fix rather than scope creep. Suppression upstream of the recorder destroys evidence, not just
output: a blocked leg never reaches `signal_alert_outcomes`.
Audit: `docs/audits/2026-08-07-ev-gate-directional-sample-guard.md`.

**A shortfall must clear `min_avg_r_z`** (default 1.64, one-sided 95%) standard errors below
`min_avg_r`, because a threshold applied to a point estimate is a coin flip with extra steps. This
changed the decision rule rather than the line, which is why it is correctness rather than the
frozen threshold-selection work. Multiplicity correction belongs where selection happens: BH suits a
sweep and is wrong for a per-leg operational gate, where Bonferroni z ≈ 3.5 would silently disable a
fail-open gate. `BacktestSnapshot` is the hot path, so a new statistic has to be added to both the
cached and the computed type. Audit: `docs/audits/2026-08-07-ev-gate-significance-test.md`.

**`upsert_backtest_run` requires `live_parity` naming the gate set that EXECUTED** — pass
`cfg.live_parity.identity()`, never a declared block. The shared base runs five live-parity gates
and `cli/backtest.py` overrides any of them per run, so a parameter tuple does not identify a
measurement: an ad-hoc override hashed to the routine sweep's `run_id` and `INSERT OR REPLACE`
replaced it, in the table `confidence_ratings` is built from. `identity()` returns None when no
gate is on, so every historical `run_id` is unchanged and **no migration is owed** —
`recalibrate_lib` keeps only the latest row per (strategy, timeframe, symbol), and a migration
could only guess, since the column recording the gate set is the one that closed this. ⚠ **Two
enforcement gaps bit here**: mypy cannot see a required kwarg through a `**dict` splat, and
`test_schema_insert_arity.py`'s `SCANNED_DIRS` excludes `tests/`, so six test-local positional
INSERTs surfaced only under the suite — grep `tests/` for `INSERT INTO backtest_runs VALUES`
before adding a column. Audit: `docs/audits/2026-08-26-run-id-live-parity-axis.md`.

**`upsert_backtest_run` requires `effective_adr_threshold(declared, timeframe, adr_exempt=…)`** — a
column recording what was *declared* is not provenance. Three things this cost: mypy cannot enforce
a required kwarg through a `**dict` splat, so run the suite; a column in the identity hash cannot be
corrected in place without a migration, since flipping the value changes `run_id` and leaves the old
row behind as a fake gated-vs-ungated pair; and a migration must respect code eras. Check whether
the consumer already assumed the correct semantics — `recalibrate_lib` did, which is what made the
migration provably rating-neutral. Narrative:
`migrations/002_adr_threshold_executed.py` docstring.

**A writer that owns only some of a table's columns must not use a whole-row replace.**
`upsert_signal_outcome` uses `ON CONFLICT … DO UPDATE` with `COALESCE(excluded.x, x)` on the three
outcome columns, so a re-detection cannot erase a resolved label. When you fix a preserve-on-NULL
bug, test the other direction too: "never update outcome" passes the obvious test and silently
breaks every caller that legitimately sets one. A defect masked by ordering is still a defect.
Narrative: `context/footguns.md`.

**`passes_sleeve_gate`'s bar is `GATE_SHARPE = 0.7`, declared and effective.** `min_trl` and
`n_obs` are no longer parameters, so re-adding a MinTRL leg has to touch every call site. A gate is
identified by its full leg set rather than by the constant that carries the name, and when a
threshold is a function of the data the bar is not a number that can be read off the source — solve
for it. Narrative: `context/footguns.md`.

**Credit the target you walked.** `signal_alert_outcomes.rr_ratio` is the declared target and
`tp_price` is the effective one, so one shared `implied_tp_r` in
`analytics/signal/outcome_backfill.py` serves all four readers: the resolver, the scanner at fire time,
`analytics/exits/audit.py` and `analytics/exits/mfe_mae.py`. A column recording what was configured is not a record of what happened.
**Pooled live avg_r is −0.2553R** (n=292, 2026-08-20, net and symmetric-gap-filled); −0.2192R
predates migration 005, −0.1752R was gross at n=267 and −0.1247R predates the `implied_tp_r` fix. ⚠ **Two things moved it, so do not attribute the whole
gap to either** — the ledger grew (267→292 resolved) *and* the basis changed (see the next entry).
Audit: `docs/audits/2026-08-14-exit-policy-ab-v1.md`.

**The live ledger is NET of costs and the backtest always was.** `outcome_r` is
`gross - outcome_cost_r`, so gross stays recoverable as the sum; `outcome_cost_r IS NULL` means
UNPRICED, never "cost nothing". One `live_cost_r` in `analytics/signal/outcome_backfill.py` serves
the resolver and migration 004, and it **mirrors `engine.Trade.pnl_r` exactly** — a `CostModel`
replaces `fee_pct` rather than adding to it. ⚠ **Do not port the parent's flat-fee `net_R`
resolver**: wifey's engine ignores `fee_pct` whenever a `CostModel` is set, and the shared base
sets one, so a verbatim port prices live on a basis the backtest does not use — a third basis does
not fix a comparability gap, it adds one. Any resolver change must pass `cost_model`/`fee_pct`
through, or the ledger silently reverts to gross. Audit: `docs/audits/2026-08-19-live-ledger-net-of-cost.md`.

**A bar that OPENS beyond a level fills there, on BOTH sides — `analytics/backtest/fills.py` is the
one rule and both books import it.** `gap_fill_price` returns the bar open when the bar opened
through the level, else the level; `level_is_on_the_expected_side` gates it so a malformed row
cannot be priced as a gap (it is also the single definition `implied_tp_r` now routes through).
⚠ **A one-sided fix is worse than no fix, and this one nearly shipped that way.** The 2026-08-19
audit measured only gapped LOSSES; the mirror is bigger — **26.1% of wins gap through their target
against 21.1% of losses through their stop** — so pricing the adverse tail alone would have moved
pooled `avg_r` −0.2192 → −0.3218 where the symmetric answer is **−0.2553**, a **~65% overstatement**
that makes every sleeve look worse against a stop-free benchmark, i.e. the exact opposite of the
reason for doing it. **Measure both tails before pricing either.** The absence was **SHARED** before
the fix (live −0.1374R per loss vs matched-backtest −0.0976R, 95% CI **[−0.1180, +0.0266]** contains
zero), so no sleeve verdict moved and none is reachable from it. Ledger restated by migration 005
(264 eligible rows, replica check **264/264**); the regression goldens moved by design — same trades
and same outcomes, only R values.
Audits: `docs/audits/2026-08-19-gap-through-stop-measurement.md` (the adverse tail),
`docs/audits/2026-08-20-symmetric-gap-fill.md` (the mirror, and the fix).

**`sync` appends the tail, so a SPLIT used to leave a permanent fake return in the stored
series.** The provider restates every historical bar onto the post-split basis while the bars
already stored keep the old one, and no later sync repairs it — the seam is *created* by the
refresh, not by a missing one. `analytics/data_sync.py::sync` now compares the re-fetched
**overlap bar** (it re-fetches `latest`, not `latest + 1`) against its stored close and re-syncs
the whole series when it moves by more than `ADJUSTMENT_BASIS_TOL` (1%). ⚠ **That tolerance is
only safe because `utils/yfinance_client.py` fetches with `auto_adjust=False`** — Yahoo applies
splits to raw OHLC retroactively but leaves dividends out, so a stored close is stable across
syncs; under `auto_adjust=True` every ex-dividend date would trip it. ⚠ **The guard prevents a
NEW seam and cannot repair a stored one**, because the overlap bar has long since settled onto
the new basis — repairing means a full re-backfill of that series. Measured 2026-09-04 over the
whole DB: four contaminated names carrying **−74.9%** (CRWD, 4:1), **+198.5%** (DD, 1:3 reverse)
and **±50/+95% seven times** (MNST, 2:1, its split landing inside the active sync window), all
repaired by re-backfill. ⚠ **A big move is not evidence of this defect** — MRNA's **+177.0%**
survived a full re-backfill on **199M shares against a normal 5M** and is a real event; check
whether the provider still serves the jump before treating one as a seam. ⚠ **And a wrong
INSTRUMENT is a different failure that a re-backfill cannot fix**: `AVB` was served a $63–71
tape under a ticker trading at $184, and `history(start=2018)` returns **27 rows — the bogus
window only** — so `migrations/007_purge_avb_wrong_instrument_tail.py` deletes them and the series reads
honestly STALE instead.
Audit: `docs/audits/2026-09-04-split-adjustment-seams.md`.

**A bar count is not a calendar span on an RTH tape.** Check any expression converting bars to time
or time to bars against `4h` RTH = 2 bars/day. Fetch forward windows to `get_latest_open_time`
instead of deriving a horizon from bar count, pinned by `TestForwardWindowSpansRthGaps`. `regime.py`
imports `cost_model`'s single bars-per-day table (`TestBarsPerDayIsShared`), and `min_periods`
clamps to the window via `atr_window_bars`, because a floor calibrated on intraday counts is
undefined on a coarse timeframe. Deduping beats correcting here: swapping the values alone would
have hidden both a missing `1wk` key and the floor problem. ⚠ **Nor is a wall-clock hour a stable
key**: `4h` sits on a fixed **UTC** grid (13:30 / 17:30 UTC), which renders **09:30 / 13:30 ET in
summer and 08:30 / 12:30 ET in winter**. Bar count and session alignment are unaffected, so anything
keyed on an ET hour mislabels every winter bar silently — and the live ledger cannot expose it,
being EDT-dated throughout. Narrative: `context/footguns.md`; measured in
`docs/audits/2026-08-19-gap-through-stop-measurement.md`.

**Check whether a metric's floor is reachable by every row in a cohort before quoting its median.**
`exits/`'s MFE for a loss comes from `fav[:-1]`, so a loss resolved on its first held bar has
`mfe_r == 0.0` by construction rather than by observation. When a denominator-like quantity moves
between runs, the statistic may be measuring it. A subgroup chosen to remove one bias usually
introduces its own, so the honest claim is the bracket rather than either endpoint.
Audit: `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md`.

**A verdict meaning "ruled out" tests CI containment, never n.** `audit_guard.CellVerdict.powered_null`
requires the CI strictly inside ±`bar`, is computed once so the rule cannot drift, and defaults to
`False` so an untested cell establishes nothing. A sample-size floor says a test *ran*; it never says
the test could have seen anything. `INSUFFICIENT` and a powered null are different states, and
collapsing them prints the confident one. Only negative labels can move under this correction, so it
cannot promote a cell. Check a tool's legend against its own predicate — the printed legend was wrong
in the same direction as the code, so the output corroborated the defect.
Audit: `docs/audits/2026-08-13-warning-value-audit.md`.

**An `audit_guard` verdict is priced per SESSION DAY, and the cluster key is REQUIRED.**
`AuditCell.cluster_key` takes one entry per `supp_r` row and sits **before** the defaulted
`kept_r`, so mypy refuses a call site that omits it. Both legs use it: `cluster_bootstrap_ci`
resamples whole days, and the Holm leg forms `t = sr·√n_eff` with `n_eff = n / DEFF`. ⚠ **A
mismatched key length FAILS CLOSED to `INSUFFICIENT`** and leaves the family — an unmeasurable
panel and an uncorrelated one must not both read as a deflator of 1.0. ⚠ **The deflator can only
shrink** (`icc` clamped to `[0,1]`, `DEFF ≥ 1`), because a negative sample ICC would otherwise
invent more independence than there are trades.

Why a block bootstrap could not do this: it absorbs **serial** dependence, between observations
adjacent *in the array it is handed*, and same-day cross-symbol trades are scattered through that
array. **The error size is a property of the CELL CUT, not of the tool** — pooling across
strategies decorrelates the day, which is why `warning_audit` barely moved (one cell's CI ×1.11,
Holm p 0.002 → 0.012, **verdict unchanged**) while single-strategy cells moved ~1.9×. **A re-cut
per strategy re-opens it.**

⚠ **`[bias.regime]`'s `EXCLUDED` is now UNBLOCKED, not overturned.** Its sole blocking cell
(`ema/high_vol`, n=172 on **25 days**, DEFF 5.90) went `[+0.085, +1.023]` → `[−0.330, +1.450]` at
adj-p 0.276, so the replay reports **FLIP justified** — but the same run reports **MAPPING
UNTESTED**, and **widening a CI removes evidence rather than supplying the opposite conclusion.**
`[bias.regime].mode` is unchanged and is the operator's call. Both `bos` cells keep `ENABLE` on the
day unit, which is the check that the fix is not merely a width knob.

⚠ **`session_day_keys` floors to the UTC day, which IS the session day for US RTH** (13:30–20:00
UTC on EDT, 14:30–21:00 on EST — both inside one date), so it needs no ET conversion and **must not
be ported to a 24h tape.** Sector and cross-day symbol correlation remain unabsorbed.
Audits: `docs/audits/2026-08-20-audit-guard-cross-sectional-clustering.md` (the measurement),
`docs/audits/2026-08-20-audit-guard-cluster-key-fix.md` (the fix).

**Deflate a pooled cross-section before quoting any t-stat from it — `make wifey-n-eff`.**
The 505-member universe carries **`n_eff` ≈ 2.96** independent series at `1d` (mean pairwise
`rho` +0.3365), so a naive pooled t-stat is inflated **13×**; `1wk` is 2.88, and `4h` is 4.92 on a
21% SIZE-TILTED subset. ⚠ **`tools/distil_power.py` fails open on exactly this**: it accepts
`--n-series` / `--n-eff` and returns `n_obs` **undeflated** when both are omitted, so every power
run before 2026-08-20 was undeflated or used a borrowed figure, including the H-004 pricing that
closed it at G3. (H-001/H-002 went through a two-sample MDE instead — `distil_power` cannot price a
calendar-cycle claim — but a pooled `sd` there carries the same correlation problem.) `n_eff → 1/rho` as `k` grows, so **adding names does not buy breadth**: 13 → 504
symbols is 39× the roster for 1.6× the `n_eff`, and the parent's 2.92 is that same asymptote rather
than a portable constant. The estimator refuses to emit flags when it could not measure, because an
unmeasurable panel and an uncorrelated one both read as a deflator of 1.0. Narrative and the
pivot-index trap that silently emptied the `1wk` panel: `context/tools.md`.

**Pool a mean over its denominator.** `digest_lib::_pooled`
(`SUM(avg_r * closed_trades) / SUM(closed_trades)`) is the one definition for all six queries.
When two aggregates sit side by side, check they pool the same way before trusting either — the tell
is inside the row, since `win_rate` was already `SUM(win_count)/SUM(trades)` and so disagreed with
itself about its denominator. Mask numerator and denominator together, and re-derive a producer-side
fix's consumers rather than re-running everything. Narrative: `context/footguns.md`.

**Ingest level-parsing fails silently**, and every instance so far was found by running the code
rather than by reading it. Full narratives in `context/tools.md`; the standing rules:

- A number that reads like a price level often is not one. `tools/x_route.py::first_level` strips
  month-anchored years (`MONTH_YEAR_RE`), percentage ranges (`PCT_RE`) and chart timeframes
  (`TIMEFRAME_RE`, e.g. a "4h descending trendline"). Two of those are single definitions shared
  with `tools/route_dedup.py` and `tools/pundit_score.py`; do not fork them.
- `check_level_order` only judges a row whose legs are both present. A one-legged row is the
  dominant shape in practice, and it is caught read-side only. Encode such a row as entry plus
  target with the stop left unstated rather than inventing a level the pundit never gave.
- `tools/route_dedup.py` is advisory except for `already_routed`: it never drops a row, and `mark`
  runs strictly after the sink write.
- **Swapping a symbol to a "tradeable proxy" without converting its levels silently disables the
  level parser.** Two 2026-07-31 rows normalised spot `XAUUSD`/`XAGUSD` to `GLD`/`SLV` and kept the
  quoted levels, so a $3,800 gold level met the ref-relative sanity gate against a ~$375 ETF, failed
  it, and fell back to call-time price — the row then scored a level the author never gave, at
  confidence `fallback`, with the gate behaving exactly as designed. **Record the underlying the
  author actually quoted** (`GC=F`, `SI=F`); a proxy is a second instrument, not a rename. Refiled
  2026-08-17, which restored the real level at confidence `low`.

**A units error near 1× is more dangerous than one near 10×.** The gold row above was ~10.7× off and
the sanity gate caught it; the silver row was ~6% off at call time and no gate can see it, so it sat
`OPEN` awaiting a trigger that could not come. ⚠ **Anchor a plausibility check to the price at
`call_ts_utc`, never to the symbol's recent range** — a handoff scored that same silver row "~2× off"
by comparing against SLV's January 2026 peak of 105.60 rather than its 53.08 close on the call date,
which mis-stated both the magnitude and the severity.

**A cap that silently truncates looks identical to an absence.** `tools/video_marks.py::keep_items`
ranks by specificity descending then timestamp ascending, so a tie at `ITEM_CAP` resolves in favour
of earlier material. Read a thin Stream C yield from a dense video as a cap artifact before
concluding the video made no calls.

## Code style

- **Linter and formatter**: ruff, covering linting, import sorting and formatting.
- **Type checker**: mypy strict (`disallow_untyped_defs = true`). All functions need type
  annotations including return types, `-> None` for test methods. Use `from typing import Any` for
  mock parameters in tests.
- **Markdown**: markdownlint-cli2.

## Testing

pytest plus `unittest.mock`. Tests must not make real network calls: lib functions accept a `client`
parameter and tests pass a `MagicMock` directly. Analytics tests use `duckdb.connect(":memory:")`
for full DB isolation and never touch the real `analytics.db`.

⚠ **Mocking `duckdb` and `init_schema` does NOT isolate the DB.** Production opens through
`connect_with_retry`, so `run_signal_watch` reached the real `analytics.db` and WROTE to it
(`init_schema` + `prune_backtest_cache`) for as long as the sentence above stood — the fixture
patched the names the runner used to call, not the one it calls now, and its docstring claimed
"without touching DuckDB" throughout. **Mock the open site, not its neighbours**, and note that
the failure is invisible on a quiet box: it surfaces only when something else holds the write
lock, as a timeout or a lock error in a test that never mentions a database.

`make test-regression` compares backtest pipeline output to golden JSON in `tests/fixtures/`, and
skips if the fixture parquets are absent. Regenerate with `make regression-update` after an
intentional change.

**A "did not change" assertion is satisfied by two worlds** — the invariant holding, and the
perturbation never arriving. Every one needs a positive control, and the control must observe the
channel the guard protects. Three traps to watch. A cap silently turns a perturbation test into a
no-op: xsmom's fixture perturbed `STRONG`, a ramp pinned at the +20 EWMAC cap, so the guarded input
moved by exactly 0.0. A control on a downstream value can fire through a different path, which is
why wifey's control asserts the demeaned forecast at `k` moved rather than reusing upstream's
`leverage[k+1]` control. And vacuity is per-shift and has to be measured, so a missing control block
is a smell rather than the finding. `make check-orphan-tests` cannot see this class, since these
tests all call their subject.
Audit: `docs/audits/2026-08-13-vacuous-causality-guards.md`.

**A mutation test proves a guard is REACHABLE by its own test, never that its SCOPE matches the
sentence written beside it.** The loop is closed over what the guard does, so code and test can be
internally consistent and jointly wrong about coverage — upstream's destination guard shipped that
way through a full suite, mutation testing, lint and typecheck. After mutation-testing, ask
separately: *what does the doc sentence claim, and can I construct an input satisfying the claim but
not the guard?* Then build that input; if it passes, the sentence is wrong, not the test. Three
distinct shapes now, and they need different fixes — a fixture that can never **reach** the guard
(vacuous, above), one that reaches it and tests the wrong **scope** (this), and one that reaches it
at the right scope but asserts against **inputs that do not exist**. The third is the quietest — the
off-site guard's first draft asserted an intruder rejection using top-level entries the real remote
has never had, and it would have passed forever. Prefer a characterization test naming a known hole
over a test asserting a protection you have not constructed.

**A green suite does not mean a test exercises its subject.** Three mechanical guards exist because
prose did not enforce these constraints:

- `tests/test_schema_insert_arity.py` (in `make test`) ties every positional INSERT to its table's
  real column list. `INSERT … SELECT` is checked by name order, so a transposition of two
  same-typed columns fails. Adding a column to a positionally-written table requires updating that
  statement in the same PR.
- `make check-orphan-tests` (advisory, heuristic, not in `make test`) reports `Test*` classes that
  name a unit but never call it. Its `not-importable` verdict means the unit is a closure and no
  test can reach it, so extraction becomes a prerequisite for a fix.
- `make post-branch-checks` (advisory, not in `make test`) runs the twelve mechanical
  `/post-branch` checks — queue items the branch closed, handoff claims, undocumented new
  files/modules/targets, negative claims, stale doc indexes, MD018 headings, the MEMORY.md cap,
  the handoff's size against `HANDOFF_MAX_LINES`, **dead cross-document section anchors**
  (`stale-anchors`), and the **pre-flip `sensitive-terms` gate**.
  ⚠ **`sensitive-terms` asks three questions because they fail differently**: the tracked tree
  (the only one a plain `git grep` covers), this branch's commit **content**, and this branch's
  commit **messages** — the surface **no file edit reaches**, since the flip republishes the whole
  history and scrubbing a term later does not unexpose it. wifey's own 2026-08-19 measurement found
  identifiers on three of four surfaces, messages among them, so a tree-only gate would have read
  clean. Four properties are load-bearing: the term list is **gitignored by policy**
  (`.claude/sensitive-terms.txt` — a tracked list of the words you are hiding is the leak it
  prevents), so an **absent list is a FINDING reading `NOT CONFIGURED`, never a SKIP**; output
  **masks** the term, because this report gets pasted into handoffs and PR bodies that are
  themselves tracked or backed up; and it **excludes main's accepted baseline**, since a check that
  is never clean trains dismissal. The list is single-copy, so `deploy/backup-analytics.sh` covers
  it in `BACKUP_FILES`. Ported from parent #658.
  ⚠ **Those three legs cannot see a PR TITLE or BODY, which is the fourth measured surface and the
  indexable one** — a body is neither the tree nor a commit, so the gate reports `clean` on one
  naming every term. Screen the composed text before it posts: **`make post-branch-text
  FILE=<path>`** (`FILE=-` reads stdin, so a title pipes straight in). Unlike the advisory sweep it
  **gates**, exit 1 on a hit — though ⚠ **through `make` you see make's own 2**, so read the banner.
  It runs that check alone, needs no git surface, and prints **line numbers plus a masked term and
  never the matching line**, since quoting context would reproduce what the masking withholds. It
  belongs in `/post-branch` phase 5 beside `make preflight`: **a posted body is public the moment
  it lands, and editing it later does not unpublish it.** Hand-screening caught #245's first draft,
  which named all three terms; a recipe that has to be remembered is the failure mode this file
  keeps naming, so the loop is now the feature.
  ⚠ **`negative-claims` narrows through `NEGATIVE_CLAIM_EXEMPT`, keyed on `(path, token)` with the
  reason inline, and drops a hit only when EVERY matched token is exempt** — one unexempt token
  still reports the line, so an entry narrows a finding rather than deleting it. Neither half of
  the key is safe alone, and `attribution` is off the list because on two claim lines it is the
  claim's own subject. ⚠ **A claim line naming no subject the tool can reach keys on the matched
  MARKER instead, and there too EVERY marker must be exempt** — keying on the first one hid a
  second claim sitting on the same line. ⚠ **The corpus covers `deploy/` and the regex covers the
  plain "there is no X" / "X has no Y" form as of 2026-08-26**; the harvested-phrasing allowlist
  missed **8 of 8** claims the signal-timer branch falsified, 2 of them in `deploy/` and so out of
  reach at any regex, which is why both halves shipped together. `has no` is **anchored to a
  subject that is not a third party** — bare, it matches claims about what something ELSE lacks
  ("yfinance has no taker data"); a definite noun phrase ("The 505-member research universe")
  counts as repo-self, which the old allowlist could not reach.
  ⚠ **Until 2026-08-26 the corpus query filtered on the letter `x`** (`git grep -e x`, written as
  if it meant "every line"), so the leg read **15% of its declared corpus** and **46 of the 69 claim-shaped lines
  then in the corpus (67%) were unreachable** — every figure ever quoted for it was measured on that slice,
  and because the tests mock the runner **no test ever saw the real argv**.
  ⚠ **Price the TRIAGE LOAD of any further widening, not just the catch**: on the fixed corpus it
  reports **13.2 findings + 18.2 soft per run** against 8.5 while blind, and a leg that
  is never clean trains dismissal. A finding needs a backticked or punctuated token; a hit on bare
  English ("no **state**") is demoted to a named re-read note, never dropped. ⚠ **Do not re-scope this leg to a window around the regex match**: on 3–6 KB
  paragraph lines that is the obvious remedy, and measured against the pre-#248 tree it would have
  **suppressed** the leg's only true positive, which a human found by re-reading the paragraph
  rather than the matched clause. Narrative: `context/tools.md`.
  They were **16 shell blocks inside `post-branch/SKILL.md`** until 2026-08-18, i.e. a check that
  only ran when a session remembered to copy it. **A skill that answers each new defect with more
  prose accumulates defects**: two of these had shipped broken, and the fix in both cases was to
  make them code with a positive control (`tests/test_post_branch_checks.py`).
  ⚠ **`handoff-size` used to compare the file to a `Line count:` stamp the file carried about
  itself** — a figure whose only consumer was the check that verified it, maintained by a
  read / `wc -l` / edit / re-read cycle every run, and the leg returned no finding when the stamp
  was absent, so dropping the stamp alone would have left it **vacuously green forever**. Stamp and
  self-reference are both gone and neither should return; the cap is an external referent. **A
  measurement can only be wrong if it has something outside itself to be wrong about.**
  ⚠ **`stale-anchors` sweeps the memory tree as well as the repo** — a section number is not a
  symbol, so no symbol-keyed check can see this class, and **2 of the 6 dead citations observed
  when it was built sat in the memory tree**, which no repo-scoped check can reach. Engine and
  its named hole: `.claude/context/tools.md`.
- `tests/test_outcome_backfill.py::TestMaxHoldCalibrationCoverage` (in `make test`) walks every
  `config/signal_watch*.toml` and fails if a declared timeframe has no `DEFAULT_MAX_HOLD_BARS`
  entry. The outcome resolver refuses an unlisted timeframe (`counts["no_hold_cap"]`) rather than
  falling back to `max(...)`, which had silently handed `1wk` the `15m` value of 96 bars, i.e. 96
  weeks. The transferable rule is about *when* a guard can exist: this one was unwritable until
  `1wk` had a value, because the assertion would have been red with no correct way to green it. A
  guard whose only fix is a calibration decision has to ship with that decision.

## Dependencies

Poetry: `poetry install --no-root`. Runtime is `duckdb`, `pandas`, `pyarrow`, and
`exchange-calendars` (NYSE trading calendar, isolated to `analytics/trading_calendar.py`). Dev deps
are ruff, mypy, pytest, pytest-mock, pre-commit, type stubs and pandas-stubs. Change `poetry.lock`
through `poetry add` / `poetry remove` rather than by hand.

⚠ **`yt-dlp` is pinned to a NIGHTLY on purpose, and the floor is a `.dev0`.** The newest *stable*
(`2026.7.4`) is the exact build measured as **403 on all YouTube media** while captions still
resolve — so the vision pass loses every frame and the run looks merely unlucky. yt-dlp ships that
fix on nightlies only, so `pyproject.toml` carries `[tool.poetry.dependencies] yt-dlp =
{ allow-prereleases = true }`; without it the `.dev0` floor cannot resolve at all. **Do not
"tidy" the pin back to a stable release** — the newest stable is behind the floor, not ahead of it.
Ported from parent #653; the symptom is memory `reference_ytdlp_js_runtime_403`, whose note that the
user's global config masks this locally still applies, so a clean box fails where this one does not.

## Documentation

Update `README.md` when changes affect project structure, CLI commands, features or behaviour.

`docs/system-overview.md` is the best single onboarding read. §9 is safe to send externally, and
§4's figures each ship the query that produced them — re-run those rather than editing a number in
place.

### Where knowledge goes

**If a session would not know to go look something up, it must be always-loaded; if it would, it
belongs on demand.** Always-loaded content is paid on every conversation whether relevant or not, so
that cost has to be earned by content whose absence causes silent damage.

| Surface | Loaded | Committed? | Holds |
| --- | --- | --- | --- |
| `CLAUDE.md` | always | yes | Rules binding on any session here regardless of task: commands, conventions, footguns, sleeve verdicts. No personal preferences |
| `MEMORY.md` index | always | no | A routing table — one line per memory, plus Current State. Enough to decide whether to open a file, never the content |
| `memory/*.md` topics | on demand | no | The detail behind an index line: user preferences, feedback and its why, project history, references |
| `.claude/context/*.md` | on demand | yes | Long-form module references and footgun narratives, reached via the Project Structure pointers |
| `docs/plans/next-conversation-prompt.md` | session start | no (gitignored, in-repo) | Live state: what is in flight, queued, or just decided. Pruned every run |

The index is a router rather than a store, so content that grows without bound belongs in a topic
file with a one-line pointer.

### Session memory protocol

At the end of every session where anything changed, update the **Current State** section in
`MEMORY.md` inside this checkout's memory tree (see Fork lineage for how to resolve it) without
being asked. Keep a one-line summary of what changed, and the open questions or "none".

The index is read into context every session, so its size is a per-conversation tax. Capping it
makes each update O(1) — add one line, roll one out:

- Current State holds at most 6 bullets. Adding a 7th means first rolling the oldest, verbatim,
  into `memory/project_session_log_<month>.md`. After rolling one, grep the log to confirm it landed.
- The whole index stays under ~17KB. ⚠ **No hook enforces this.** Column-scanned
  2026-08-19 across all five settings files (`find ~/.claude ~/.claude-personal .claude
  -name 'settings*.json'`, then every `hooks` entry): **5 hooks total** —
  `guard-destructive`, the `gh pr create` advisory, the foreground-run advisory, a
  context-mode cache heal and a budget line — and **none reads a file size**. So "a hook fires at 19.7KB" was false and the cap
  had **no** enforcement at all. `make post-branch-checks` is the enforcement now
  (`memory-cap`). Re-run that scan before trusting any claim about which hooks exist —
  this one was first filed after checking only two of the five files.
  It compounded with a second defect: `make status` measured with `du -k`, i.e. disk
  blocks, so it overstated the file by up to 4KB — in the direction that causes needless
  rolling, and worst on the smallest file, which is the one with the cap. Fixed to
  `wc -c`; MEMORY.md now reads 16.5 KB where it printed 20.
- "Last session" is at most 2 lines; every other bullet is exactly 1 line.
- Session logs have no size limit. Prune by moving, never by deleting.
- Open questions live in `memory/project_open_questions.md`. They are live state, so they cannot be
  rolled into a dated session log, but left in the index they grow without bound. The index carries
  a one-line pointer plus the count.

## Agent skills

Skills live in `.claude/skills/<name>/SKILL.md`, are invoked with `/skill-name`, and each one's
description and trigger conditions are already loaded every session. Use them proactively. Only the
facts that are not derivable from those descriptions live here:

⚠ **`.claude/` is a DENYLIST as of 2026-08-20 — a new artifact class is TRACKED by default.**
`.gitignore` names only `settings.local.json` (absolute machine paths), `RESUME.md` (session
scratch) and `sensitive-terms.txt` (never tracked, by policy); skills, agents, context, hooks and
`settings.json` all ship. It was an allowlist, where each class defaulted to ignored until someone
named it, so hooks silently did not survive a reclone and three entries here said "re-add it".
**An allowlist over a tree that grows new classes fails silently, in the direction of absence** —
the same shape as `make backup`'s coverage. Still check `git check-ignore` before assuming.

- **Always load `/frontend-design` before any Svelte, CSS or UI change.**
- **Invoke `/post-branch` before `gh pr create`**, while the branch is still local-only.
- `/wfo-sweep` is the trusted production path for `tp_r`; `/config-refresh` covers the other config
  dimensions.
- `/sanity-check` runs weekly or after a large refactor, and its mechanical half is
  `make sanity-checks` — which **gates**, unlike `/post-branch`'s advisory sweep. The fork-drift
  leg deliberately excludes the dated trees, where a past-tense claim is correct by construction.
- The sweep skills (`atr-sweep`, `volume-sweep`, `wfo-sweep`, `param-sweep-apply`,
  `backtest-findings`) are dormant while the TA book is frozen.

## Git conventions

Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`) and branch prefixes
(`feat/`, `fix/`, `docs/`, `chore/`).

**Branch off the latest `main` before the first edit:**

```bash
git fetch origin && git switch main && git merge --ff-only origin/main
git switch -c <type>/<slug>
```

If work has already started on `main`, `git switch -c` carries the uncommitted changes over. Verify
with `git status -b` rather than from memory of which branch you were on. A branch cut from a stale
`main` replays work already merged, and one cut from another feature branch inherits its whole diff
into the PR.

Keep `.env`, `config/stocks.json` and IDE files out of commits.

⚠ **Per-repo git identity is mandatory before any commit**: this account uses
`s10023 <ngkhaijian@gmail.com>`, since the global config inherits a work identity and will
mis-attribute commits. Check `git config --local user.email` first. The SSH alias
`git@github.com-personal:...` is also required for s10023 remotes; see memory
`reference_ssh_host_aliases.md`.

**Every `gh` command here needs `--repo s10023/buibui-wifey-wall-street-bot`.** The `gh` default
repo is intentionally the parent, so a bare `gh pr view N` resolves against the wrong repo. This is
a preference rather than a bug to fix, so leave `gh repo set-default` alone.

**Invoke `/post-branch` before `gh pr create`, while the branch is still local-only**, and fold its
"Documentation updates" section into the initial `--body`. These are private repos on the free tier
and Actions minutes are a hard budget, so a doc-sync commit pushed to an already-open PR re-runs the
whole 5-check matrix for what is usually a two-file edit. Phase 6's zero-commit tail still runs
last: re-verify PR state, then stamp the handoff, in that order. A local `PreToolUse` hook on
`Bash` greps for `gh pr create` and emits an advisory reminder. It has to be `PreToolUse`: a
`PostToolUse` hook cannot fire before the PR exists, so it could not enforce this ordering at all.
The hook is tracked and survives a reclone.

### CI quota

**The flip is about the ACCOUNT'S ALLOWANCE, never about your diff's paths.** While the monthly
allowance holds — it resets on the 1st — a **PRIVATE** repo runs the whole matrix for free:
PRs #281 and #285 each merged green private on 48/53 real steps. **Flip only when the allowance is
exhausted**, and back to private once it merges: public repos get unlimited free standard-runner
Actions minutes, which is the only way to get real CI once the allowance is gone. ⚠ **The inverted
reading — that a `**/*.py` diff needs the flip — cost an exposure window on 2026-09-06.** The paths
filter fires **for** Python, so a Python diff **runs** those jobs; a docs-only diff is what skips
them.

**Pushing a branch costs no CI**, since `push:` triggers only on `main` and `pull_request:` fires
only on a PR. Commit and push freely; the meter starts at `gh pr create`.

⚠ **Confirm the flip with the user each time.** Standing authorisation covers the mechanics, not the
timing, and the flip publishes the parent's pre-fork commits for its duration.

```bash
GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-wifey-wall-street-bot \
  --visibility public --accept-visibility-change-consequences
# ...open PR, let CI run, merge...
GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-wifey-wall-street-bot \
  --visibility private --accept-visibility-change-consequences
```

**Wait for main's own push run to finish before flipping back.** Merging starts a fresh run on
`main` (`lint.yaml` and `security-scan.yaml` also trigger on `push: branches:[main]`), and flipping
to private kills whichever jobs are created after the flip. `Regression tests` needs
`lint-typecheck-test` and is not created until ~4 minutes in, so an early flip leaves it at
`steps=0` and main looks red for billing reasons rather than code ones. **Run `make wait-ci-main`
rather than hand-rolling a waiter** — it encodes the rest of this paragraph, and the hand-rolled
version has been wrong twice. It gates on a job-count floor, not on "nothing pending", because the
status check is vacuously true while a chained job does not yet exist; the count arrives as 3, then
5. ⚠ **It counts `push`-event runs only.** A `main` SHA also carries a GitHub-managed `dynamic`
run ("Configured Graph Update: pip in /.") created minutes *after* the push runs, so an unfiltered
count reads 6 where this paragraph says 5 and the gate waits on dependency-graph submission it has
no interest in. After a corrective push, confirm `headRefOid` changed before trusting a check
rollup, which can otherwise report the previous run.

**A merge-run failure at ~3s with `steps=0` and `visibility=PRIVATE` is billing.** Verify duration,
visibility and step count, then merge. Never debug it.

**Paths tell you what a private PR LOSES once the allowance is gone — never whether to flip.**
Three of the five checks sit behind `dorny/paths-filter` on `**/*.py` and `web/ui/**`, so a
`.md`-only diff executes zero steps in `lint-typecheck-test`, `Regression tests` and
`frontend-check`; `make lint-md` reproduces
CI's `markdownlint` exactly, so the only check forgone is Trivy's secret scan. ⚠ **That reasoning
is about your DIFF and says nothing about the ACCOUNT.** `markdownlint` and `Trivy` have **no** path
filter, so an exhausted allowance zeroes them too — #238 opened docs-only with **all five** checks
at `steps=0`. **The tell: a path-filtered skip reports `SKIPPED`, an exhausted allowance reports
`FAILURE`, both at `steps=0`**, so the totals cannot separate them. Skip the flip only if the last
run on this repo executed real steps. ⚠ **That check is LAGGING and is NOT sufficient** — measured
2026-08-20 on #251: `main`'s last push run had executed **8 real steps and SUCCEEDED ~1h earlier**,
and the docs-only PR opened on that basis still came back **4 of 5 `FAILURE` at `steps=0`**. The
allowance drains between the check and the PR, so read a green last-run as **permission to try,
never a guarantee** — and budget for the recovery, which is cheap and known: flip public, then
**re-run the existing runs IN PLACE** (they exist; they just executed nothing). ⚠ **While the repo
IS public, further pushes are FREE**, so a correction discovered mid-window should land **during**
the window — after the flip back the same edit costs a whole metered cycle. Read any externally
pasted content in a doc before committing it. Narrative: memory
`reference_ci_steps_counts_skipped.md`.

**CI's markdownlint glob is not wider than local's**, despite the workflow appearing to say so: the
job passes `globs: **/*.md !venv`, but markdownlint-cli2 still applies the negations in
`.markdownlint-cli2.jsonc`, so **local lints a superset of CI** and `make lint-md` green means CI
green. Two reasons local sees more: it picks up untracked files, and **markdownlint does not read
`.gitignore`**. The only negated tracked file is `.github/pull_request_template.md`, which carries a
live MD041 error — that is why it is negated. Do not "fix" an apparent divergence; re-derive it:

```bash
git ls-files '*.md' | wc -l                 # tracked
make lint-md 2>&1 | grep '^Linting:'        # what local actually lints
```

⚠ **The counts are deliberately not written down here.** They moved twice and were quoted stale
both times, and nothing reads them but the sentence that carried them — the same self-referential
defect as the handoff's old `Line count:` stamp. Repo-shape numbers come from `make status`.

⚠ **Know what the public window costs, because flipping back does not undo it.** wifey is not a
GitHub fork — its history was copied — so 389 of 541 commits are the still-private parent's pre-fork
research, published for the duration. Anything cloned or indexed in that window stays out, and any
fork created while public is split into its own network and survives the flip back. This is an IP
and history exposure rather than a secrets one: all 4,519 blobs scanned clean. Flip back promptly
after the merge, and check `forkCount` is still 0 before you do.
