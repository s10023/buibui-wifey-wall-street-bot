# CLAUDE.md

Instructions for Claude Code in this repository.

## Working agreement

**Persona.** You are a senior quant-systems engineer on a research codebase that has not yet
found an edge. Prefer de-biased, out-of-sample evidence to in-sample optimism, and know which
panel you are arguing from, because the two are not interchangeable:

- A **sleeve** is judged on net-of-cost Sharpe against `GATE_SHARPE = 0.7`, DSR, PBO and the
  bootstrap lower bound, with a realized-beta guardrail on any market-neutral construction.
- A **detector cell** is judged on pooled `avg_r` across strategy × timeframe × direction, with
  regime as a soft conditioner. US equities have one RTH session, so crypto's session axis does
  not port.

Never commit an overfit parameter. Report negative results plainly: all nine sleeves built so
far are non-positive, and each is a finding.

**Definition of done.** A Python change is done when `make lint-py`, `make typecheck` and
`make test` are green.

`make test-regression` is a separate gate. Neither `make test` nor `make preflight` runs it
(both pass `--ignore=tests/test_regression.py`). Run it when the diff touches any path in CI's
regression filter, quoted verbatim from `.github/workflows/lint.yaml`: `analytics/**/*.py`,
`pyproject.toml`, `poetry.lock`, `config/*.toml`, `tests/test_regression.py`,
`tests/fixtures/**.parquet`, `tests/fixtures/golden_*.json`,
`scripts/extract_regression_fixture.py`, `.github/workflows/lint.yaml`. Say which branch you
took. The gate takes about 8 seconds, so run it when in doubt. Keep the list verbatim: the
`regression-surface` leg of `make sanity-checks` diffs it against the workflow. A moved golden
is a decision rather than a failure; check the data-drift falsifier (Database, below) before
regenerating.

**Anti-drift.** Before a multi-step task, restate the goal and its success metric in one line,
and stop to ask if a step stops serving it. Before killing or demoting a strategy, require
evidence on the right panel rather than one pooled number, and prefer demotion to deletion.

**Keep going.** Take every step that does not need the user, and put a status note in the
same message as the next action rather than pausing to report. Stop only when blocked on a
user decision or before anything destructive or outward-facing: deleting data, force-push,
publishing (a PR, an Issue comment, a visibility flip, a Telegram send) or changes outside the
repo. The specific stops elsewhere in this file still bind: the anti-drift stop above, a
repo-visibility flip, `go-live`, `/post-branch`'s confirm-each-edit, and anything
`guard-destructive.py` blocks. End a long run with three parts in this order: what is blocked
on the user, what changed, what was found. Mark anything unconfirmed as unconfirmed.

**Effort.** Effort sets how much a run verifies, tests edge cases and decides alone; raising it
fixes missed edge cases, not a wrong approach. Rule of thumb: `low` for in-the-loop sketches
and mechanical edits, `medium` for feature work, `high` where verification or edge cases decide
the result (brownfield fixes, audits, statistical gates, research), `max` for fully autonomous
hard problems. A feature loop is spec, implement on `low`, review, verify on `high`. Skills pin
their level with an `effort:` frontmatter key, which overrides the session for that turn:
`high` on `research-distil`, `sanity-check`, `post-branch`, `sync-parent`,
`investigate-strategy`, `new-strategy`, `db-update` and the sweep skills (`backtest-findings`,
`wfo-sweep`, `param-sweep-apply`, `config-refresh`, `atr-sweep`, `volume-sweep`); `low` on
`backtest-run`, `pr-summary`, `journal-trade`, `data-backfill` and `recalibrate`; the rest
inherit. A session cannot change its own effort, so when starting an Issue, name its
`effort:<level>` label (the digest shows `effort:?` when unset) and suggest `/effort <level>`.
Issues carry one of `effort:low`, `effort:medium`, `effort:high`, `effort:max`.

**Token efficiency.** Skills stay dormant until invoked. Redirect any command output over roughly
20 lines to a file and read the part you need, and `/compact` at a logical boundary. Delegate a
heavy read to a subagent when the context it saves outweighs its startup cost.

**Delegation.** The main thread orchestrates: design, judgment, review and routing stay here.
Send bulk mechanical work (vision extraction, file sweeps, boilerplate, test triage) to
**sonnet** subagents and trivial one-shot lookups to **haiku**. Use **opus** subagents only as a
quota escape valve for long parallel research: they sit below the main thread, so they save
limits rather than buy better thinking. Make every brief self-contained, with the goal, success
metric and rubric inline and no SoT or memory re-reads. Confirm background work from outside
(`ps`, `git status`) rather than from the agent's own report. At most two subagents run at once
and a third launch is refused, so pipeline wider fan-outs in pairs.

**Guardrails.** `.claude/settings.json` registers hooks on `SessionStart` (the session digest,
below), `UserPromptSubmit`, `PreToolUse` and `PostToolUse`; every one is a tracked Python file,
because this host has no `jq`. Three `PreToolUse` hooks on `Bash` are files in `.claude/hooks/`.
`guard-destructive.py` blocks `rm -rf`, `git reset --hard`, force-push and DB wipes; if it
blocks you, say so rather than working around it. Each hook wrapper exits 0 when its script is
missing (Python's own exit 2 would read as a block), then tries `.venv/Scripts/python.exe`,
`.venv/bin/python` and `python3` in that order. Keep that order: on this host `python3` is the
Windows Store alias stub, which exits 126 and would silently disable every hook, and the stub
passes `[ -f ]` and `[ -x ]`, so it cannot be detected. `tests/test_hook_wiring.py` runs the
wrapper string from `settings.json` and asserts an end-to-end block. Write commit and PR bodies
to a file and pass `-F` or `--body-file`: a heredoc is part of the command string, so quoting a
hazard in it trips the guard.

## Fork lineage

A fork of `s10023/buibui-moon-trader-bot`, frozen at parent commit `635ed5a` and repurposed
from a Binance crypto bot into a yfinance-backed US-equities signal bot.

Resolve memory-tree paths with `tools/claude_home.py` rather than writing them literally; the
config root and the slug differ per host:

```bash
PYTHONPATH=. poetry run python -c 'import sys; from pathlib import Path; from tools.claude_home import memory_dir; print(memory_dir(Path(sys.argv[1])))' .
```

**Sister memory.** The parent's accumulated findings (strategy edges, regime classifier
history, F8/F9/T2 work, sweep findings, gate architecture) live in `MEMORY.md` in
`memory_dir(<the buibui-moon-trader-bot checkout>)`, beside this checkout. Read it when a
feature exists in both repos. Skip its Current State and its crypto-specific findings
(`smt_pairs`, `funding_reversion`, BTC/ETH/SOL cells, CME gap).

**Planning lives in GitHub Issues.** Every to-do, open question, decision and future plan is
an Issue in this repo, never a memory note, a handoff line or a markdown to-do. File it the
moment it surfaces, including work found mid-branch that this branch will not do. Labels: one
priority (`p1`–`p3`), one kind (`build`, `mechanics`, `audit`, `hypothesis`, `ops`,
`decision`, `question`), one `effort:` level (see Effort, above), plus `blocked` when it waits
on something named in its body, and `cloud-ok` when a cloud session can finish it with
tracked files alone (no `analytics.db`, no memory tree, no Windows host). A cloud session picks from `cloud-ok`. Over REST:
`gh api 'repos/s10023/buibui-wifey-wall-street-bot/issues?state=open&per_page=100'`, because
`gh issue list` goes through GraphQL, which cloud sessions are refused.

**North star.** `docs/north-star.md` holds the north star, acceptance gates G1–G4, the
data-cost policy and the frozen list; it is tracked so a cloud session can read it.
`project_todo_master.md` in this checkout's memory tree (`memory_dir(<this repo>)`) keeps only
closed verdicts and the deliberately-not-queued list, as anti-re-litigation evidence. Neither is a
queue. Gate G1 is not live, so paper sizing has nothing to size, and Phase B (order layer,
broker pick) stays gated G3→G4.

**TA detector and sweep work is frozen**: no new boolean detectors and no `tp_r`, gate or
threshold sweeps. The freeze covers the equity signal engine's `DETECTOR_REGISTRY` book at
`4h`/`1d`/`1wk`. It is an inherited category verdict (memory
`project_parent_fresh_eyes_port.md`), not a claim about moving averages or price structure
anywhere else.

Historical design for the retired bring-up queue:
`docs/superpowers/specs/2026-04-10-tradfi-equity-fork-design.md`.

## Project overview

Buibui Wifey Wall Street Bot, a yfinance-backed US-equities signal bot. Phase A ships signals
only: a DuckDB analytics and backtest stack, a multi-strategy signal engine with Telegram
alerts, and a FastAPI plus Svelte web UI. Python 3.11+, managed with Poetry.

## Key commands

After any Python change:

```bash
make lint-py        # ruff format + lint (mutates); lint-py-check verifies without writing
make typecheck      # mypy strict
make test           # full pytest suite (make test-cov for coverage)
```

### Windows host

- **Encoding.** The Makefile exports `PYTHONUTF8=1` to every recipe. Without it Windows reads
  and writes text as cp1252, and this tree's source, configs and fixtures contain non-ASCII
  characters, so the suite fails with `'charmap' codec` errors. It covers `make` only, which is
  why `tests/test_explicit_encoding.py` statically requires `encoding=` on every `read_text`,
  `write_text`, text-mode `open` and text-mode `subprocess` call in shipped code, `tests/` and
  `.claude/hooks/`: no env var can mask a source scan. The subprocess case fails more quietly:
  the decode error happens in a reader thread, `stdout` comes back `None`, and the error
  surfaces later as `'NoneType' object has no attribute 'splitlines'`. A child Python also
  writes its piped output as cp1252, so the same test requires a call that launches Python to
  pass `env=tools/child_env.py::python_child_env()` (which pins `PYTHONUTF8=1`) or set
  `errors=`; a non-literal argv needs a named entry in its allowlist. Go through `make`,
  or prefix direct invocations:
  `PYTHONUTF8=1 PYTHONPATH=. poetry run python tools/post_branch_checks.py …`. Linux CI is
  unaffected.
- **No `os.exec*`.** Here `os.execve` with an env dict segfaults, and `os.execv` exits 0 without
  the child's output or exit code reaching the caller. On Windows use `subprocess.run` plus
  `sys.exit(rc)`, as `tools/venv_bootstrap.py` does: it branches on
  `host_platform.is_windows()` and keeps `os.execve` on POSIX. A verbatim upstream port that
  uses `os.exec*` looks correct and discards its output.
- **Heredocs.** Through the Bash tool, a heredoc past about 300 lines dies with
  `unexpected EOF`, and a quoted heredoc still consumes one backslash level (`"\\\n"` arrives as
  `"\\n"`; the tell is a `SyntaxWarning: invalid escape sequence`). Write any file containing
  backslashes with the Write tool.
- **Line endings.** `.gitattributes` pins `*.sh eol=lf`. Bash strips CR from a script's source
  but not from data the script reads, and git never sees the gitignored `.env`, so
  `deploy/windows/load-env.sh` and `python-dotenv` each strip CR from it. Keep both strips: a
  trailing CR on `TELEGRAM_BOT_TOKEN` makes Telegram reject a token that prints correctly, and
  the only symptom is that alerts stop.

### Repo checks

- `make status` prints every repo-shape number: tests, files, CLAUDE.md size, handoff lines,
  MEMORY.md size and bullet count, audits, skills, tools. Print these rather than writing them
  into a doc, where they go stale.
- `make sanity-checks` runs the eight mechanical checks in `tools/sanity_checks.py`: fork drift
  against invocable artifacts, parent-repo leakage in skills, dead repo paths, package coverage
  in `.claude/context/`, the three hand-maintained router lists, `[strategy_params.X]` keys,
  README's CLI coverage, and `regression-surface`. It gates: it exits non-zero,
  `tests/test_sanity_checks.py` runs it inside `make test`, and CI's `markdownlint` job runs it
  on every PR, because the pytest job is path-filtered and skips docs-only PRs. Legs that need
  project imports or the gitignored watchlist report `SKIPPED` rather than a finding.
- `make session-digest` prints one screen: is `wifey-signal-watch` scheduled on this box,
  watchlist OHLCV, backup and cadence reds, the survival core's state (OV-1 × VM, read from
  `^GSPC`), open GitHub Issues (the planning queue since 2026-09-30), and the handoff's first
  move. The `SessionStart` hook runs it at every session start; `wifey-daily-check` runs
  `TELEGRAM=1`, which sends to the personal channel every day, green included, so a missing
  message means the scheduler stopped. `TELEGRAM=1` runs `make core-sync` first, because no
  watchlist carries `^GSPC`. It always exits 0.
- `make cadence-check` reports overdue recurring tasks from `docs/plans/task-marks/`, one file
  per task holding an ISO-8601 UTC timestamp; stamp one with `make cadence-stamp TASK=<slug>`.
  The file's content is authoritative, not its mtime, and a missing mark reads as overdue. It is
  advisory and stays out of CI: the marks are gitignored, so a fresh clone sees every task
  overdue. Two tasks are declared (`/sanity-check` and `/sync-parent`, both 7d); the inclusion
  rules live beside `TASKS` in `tools/cadence_check.py`. It records runs and triggers nothing.
  Its second section joins audit verdicts to owners: an actionable verdict in
  `docs/audits/INDEX.md` that no Issue (open or closed) and no SoT row names is a finding. It
  reads Issues over REST and the SoT when this machine has one, and says which it read.
- `make preflight` runs the suite against a fresh clone of HEAD and replaces that branch's
  `make test` (it mirrors that recipe's argv, pinned by a test). The loop is targeted
  `pytest <files>`, commit, then `make preflight`; running `make test` as well wastes about four
  minutes. If you are reaching for `make test` and a PR is coming, run preflight instead, in
  `/post-branch` phase 5 after the doc commits and before `gh pr create`. It refuses on a dirty
  tree, because a clone sees only committed state. It catches dependencies on gitignored paths
  that exist only on this box (`config/stocks.json`, `.claude/sensitive-terms.txt`,
  `docs/plans/`, `analytics.db`); it cannot see an absolute `$HOME` default, which is identical
  inside the clone, or production breakage that no test exercises. Through `make` its exit codes
  (0 pass, 1 failure, 2 refused, 3 infra) are hidden, so read the banner: refused and infra are
  not suite failures. CI runs the same gate; preflight moves detection before the push, which
  saves a metered Actions cycle. Narrative: `.claude/context/tools.md`.
- `make wait-ci PR=<n>` waits for a PR's checks, and `make wait-ci-main`
  (`--branch main --min-jobs 5`) is the flip-back gate. Both report whether the checks actually
  ran. `tools/wait_ci.py` exits 3 on a `FAILED` check at `steps=0` (an exhausted Actions
  allowance, which looks like a real failure: flip the repo public rather than debugging it), 1
  on a genuine failure, and 4 when it settles green but could not read step counts. A `SKIPPED`
  job was never created and declares nothing — `Regression tests` skips through
  `needs: lint-typecheck-test` after any test failure — so the failing row decides the verdict
  and skips never do. A `gh` failure raises rather than becoming data. Through `make` any
  failure is make's exit 2, so read the banner, or call
  `poetry run python tools/wait_ci.py --pr <n>` / `--branch main` for the code.
- Run `make test`, `make test-regression` and every CI wait in the background
  (`run_in_background: true`) and wait for the task notification. `.claude/settings.local.json`
  allowlists these targets, and `.claude/hooks/advise-foreground-run.py` nudges when a run is in
  the foreground; it keys on the `run_in_background` parameter, since the two command strings
  are identical.
- Never poll a background job with `pgrep`: `until ! pgrep -f 'pytest tests/'` matches the
  polling shell's own argv and waits on itself, and the bracketed variant exits at once. If a
  waiter is unavoidable, assert a positive marker (`grep -q ALLDONE`), never an absence.

### Live signals

- `CATCH_UP=1 make go-live` is the one-shot dispatch, run by hand or by a scheduled job:
  `wifey-signal-watch.timer` on Linux (Mon–Fri 08:30 UTC) or the task that
  `deploy/windows/install-tasks.ps1` registers. Nothing installs either, and installing is the
  operator's call because Telegram messages go out. There is no daemon (the unit is
  `Type=oneshot`), so "restart signal watch" means start the service or run the target.
  `buibui-signal-watch.*` is the parent's unit; tell them apart by `WorkingDirectory`, not by
  name. Ratings reload every run.
- Run it before the open, not after the close: `1d` bars stamp 04:00/05:00 UTC and close the
  next day, so at the bell that session's daily bar is still forming.
- Telegram alerts go out on Wed, Thu and Fri only. This follows from `day_filter = tue_thu`
  rather than from a setting: a pre-open run sees the previous session's bars, and the filter
  suppresses on the bar's open weekday, so a Mon run (Fri bars) or a Tue run (Mon bars) cannot
  alert. Those runs still sync, update the ledger and backfill outcomes. Skipping a run day
  destroys that day's alerts, because the next run's catch-up consumes the primary watermark
  without dispatching. Friday carries the largest share of the alert surface.
- `max_alert_age_hours` (shared base, 24.0) is what lets the session's first `4h` bar dispatch
  under one run a day, and it acts only with `CATCH_UP=1`, since only catch-up emits events for
  a non-latest candle. `0.0` restores latest-candle-only dispatch; negative values are refused.
  Raising it cannot re-send a consumed watermark or recover a skipped day.
- `fired_at_ms` is not a dispatch record: `upsert_signal_outcome` overwrites it on every
  re-detection. The `:wife` watermark is the dispatch oracle — backfill marks the primary
  watermark alone and a real send marks both. Audit:
  `docs/audits/2026-08-25-dispatch-recency-window.md`.
- Re-run `make universe-stamp-listed` after any membership or backfill change: a new
  constituent arrives unstamped, and unstamped is the permissive value. The weekly
  `wifey-universe-sync` only extends existing series, so it never moves the `listed` seam.

### Docs and audits

- Markdown: `make lint-md`, which covers `.claude/` skills and context;
  `make lint-md-fix` applies the auto-fixable subset. Keep `.claude/` in scope: do not add a
  `!.claude` exclusion to `.markdownlint-cli2.jsonc`.
- After adding or renaming a file in `docs/audits/` or `docs/superpowers/specs/`, run
  `make docs-index`. `tools/docs_index.py` generates both `INDEX.md` files, and
  `tests/test_docs_index.py` compares them byte for byte; `make docs-index-check` verifies
  without writing.
- A new audit states its verdict as prose under a Verdict heading (FOUND / BOUNDED / EXCLUDED /
  BLOCKED), or `TestEveryNewAuditExposesItsVerdict` fails. A table, blockquote or `**Date:**`
  line there is rejected, because the generated `docs/audits/INDEX.md` renders each verdict in
  a column and an unparseable one reads like an audit that reached no conclusion. Ten
  pre-2026-08-21 audits are grandfathered in a frozen set that can only shrink; remove an
  entry when you fix or delete its audit. The ownership half is `make cadence-check`'s
  verdict→owner join: an actionable verdict (FOUND / CANDIDATE / EXIT-FIXABLE / UNBLOCKED,
  unless settled) that no Issue or SoT row names is a finding. Clear it by filing an Issue that
  names the audit filename, or by recording in a closed Issue where the work was already done.
- UI or API changes: `make web-build` for a production bundle, `make web-dev` for the Vite dev
  server, `make web-check` for `svelte-check` types without a build.

### Database

- `make db-update` is the routine refresh after backtest or strategy changes
  (`db-update-backtest` → `db-update-recalibrate` → `regression-update` →
  `check-dead-surfaces`). The last step reports `(strategy × timeframe)` cells where
  declaration and output disagree: declared but dead, or rated but undeclared (a
  `confidence_ratings` row outliving its config). It never blocks the refresh, but the
  completion banner depends on it; run `make check-dead-surfaces` alone for a non-zero exit.
- A golden diff from that run is usually data drift, because `regression-update` re-derives the
  fixture parquets from a DB that has moved on. Run the falsifier the banner prints
  (`git checkout -- tests/fixtures/ && make test-regression`); if it passes, revert the goldens
  rather than shipping them. Attribute a `confidence_ratings` star move by re-running the sweep
  twice over one fixed window (`/db-update` step 3).
- Two maintenance targets sit outside every routine flow. `make db-prune-backtests` runs
  `scripts/db_prune_backtests.py` (hard cutoff 30d; soft cutoff 7d, keeping the top 10 per
  strategy × symbol × timeframe × day_filter × adr_threshold). `make clean-db` deletes
  `analytics.db` and its WAL outright; `guard-destructive.py` blocks it, so take a
  `make backup` first.

### Backups and freshness

- `make backup` writes a verified copy of `analytics.db`, all of `docs/plans/` and the memory
  tree to `~/backups/wifey` (`make backup-dry-run` previews). All three are single-copy and
  outside git: `git clean -xdf` deletes the whole research pipeline's output without a prompt,
  and `analytics.db.bak` is an undated, unverified byte copy, not a backup. Coverage is a
  denylist over a wholesale copy, so a new file is covered by default. `BACKUP_FILES` adds
  single-copy files outside `docs/plans/`, such as `signal_state.json`, whose loss is silent:
  every watermark key comes back cold, the cold-start guard keeps only the latest candle, and
  `CATCH_UP=1` replays nothing. `EXTERNAL_ROOTS` covers trees outside the repo (the memory tree)
  and lands them inside each snapshot; an absent root warns and records `files: 0` rather than
  failing the run. `MANIFEST.json` is serialised with `json.dumps`, never `printf`, because
  Windows paths contain backslashes. `tests/test_backup_local_coverage.py` drives a real run
  through `WIFEY_REPO_ROOT` and `WIFEY_PYTHON`. Rationale, restore procedure and scheduling:
  `deploy/README.md`.
- `make backup-offsite` runs `rclone sync` from that root to `$WIFEY_BACKUP_REMOTE`
  (`gdrive-wifey:snapshots`). wifey's remote is pinned to its own Drive folder, and that
  separation, not the script's guards, keeps it off the parent's backups on the same account.
  `sync` mirrors deletions, so run `make backup-offsite-dry-run` after touching the remote. The
  intruder guard compares top-level entries only; `tests/test_backup_offsite_guards.py` pins
  that hole and is the script's only gate, since no CI step reads `deploy/`. Never paste
  `rclone config` output anywhere: it carries a live refresh token.
- Scheduling is per host and opt-in: `wifey-backup.timer` and `wifey-backup-offsite.timer` on
  Linux, tasks under `\wifey\` on Windows via `deploy/windows/install-tasks.ps1`. A scheduled job
  that silently stops looks identical to one that works, so judge coverage by snapshot age with
  `make backup-check`, never by a timer's or task's state.
- `make backup-check` reports the age of the newest verified snapshot from `captured_at_utc`
  inside `MANIFEST.json`, never an mtime (a restore or an `rclone` round-trip moves mtimes toward
  "fresher"). Only `daily/` carries manifests; `weekly/` is a parquet export and is not graded.
  Every unreadable state reads STALE. It checks the local tree only, runs only when called, and
  stays out of `make test`, `make sanity-checks` and CI, because the backup root is
  machine-local. Narrative: `.claude/context/tools.md`.
- `make freshness-check` grades what `backup-check` cannot: OHLCV staleness and the dispatch
  watermark. It measures age in NYSE sessions, never wall-clock, because on an RTH tape a
  wall-clock grade reds every healthy series; sessions come from `analytics/trading_calendar.py`
  and bars per day from `cost_model.BARS_PER_DAY`. A `1wk` bar stamps Monday and closes Friday,
  so the weekly tolerance adds `max(0, sessions_per_bar - 1)`. The watermark advances only on
  dispatch, which `tue_thu` makes intermittent, so it is dated but ungraded; its only finding is
  "no watermarks at all". Run liveness comes from the watchlist OHLCV leg instead, since every
  `go-live` run starts with a watchlist sync. The 505-member universe is graded only where the
  `wifey-universe-sync` job is enabled on this host. That job extends existing series and skips a
  new constituent, which needs a hand-run backfill, and yfinance's intraday window leaves most
  of the universe without `4h` bars, so no schedule closes that coverage gap. Check freshness
  before any pooled cross-section, because a stale universe beside the fresh, mega-cap-tilted
  watchlist compounds the `4h` tilt. The pundit ledger has no timer: run
  `make wifey-pundit-sync` before a ledger glance. Advisory for the same reason as
  `backup-check`; the pure grading logic is tested in `make test`
  (`tests/test_freshness_check.py`). Narrative: `.claude/context/tools.md`.

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

Equity price and position monitoring lives in the web UI under `web/`.

## Project structure

The deep reference lives in `.claude/context/`, and the pointers below are a session's only path
to it; follow them before working in an area. Verdicts and footguns stay in this file because
they guard against re-litigating settled research.

| Package | What it is | Deep reference |
| --- | --- | --- |
| `wifey.py` · `cli/` | Entry shim delegating to `cli.main:main`; argparse subcommand package with `_common.py` helpers | — |
| `analytics/` | DuckDB analytics layer: `store/`, `strategies/` (16 registered for dispatch), `backtest/`, `signal/`, `stats/`, `research_guards/`, `sweep_guard.py`, `audit_guard.py`, `db_retry.py`, plus data ingest, quality and calendar | `context/analytics.md` |
| `analytics/{forecast,xsmom,lowvol,xasset,pead,gapfill,velocity,exits}/` | Research sleeves and the exit diagnostic (verdicts below) | `context/analytics.md` |
| `analytics/insider/` | H-024, the first non-price sleeve, so the TA freeze does not bind it: `form4`, `classify`, `book`/`replay`/`report`, run by `make wifey-insider-audit`. Measured; verdict below. The pre-registration is frozen at four trials with routine-arm placebos as controls | `context/analytics.md` |
| `analytics/overlay/` | OV-1 (#418) and VM (#421): risk overlays on the market premium, judged on the overlay yardstick (`docs/north-star.md` § Two yardsticks), never `GATE_SHARPE`. The 200-session MA filter is **FOUND as an overlay** on the French total-return frame, 1929–2026: ulcer ratio 0.382, ΔSR +0.250 (CI +0.072 to +0.447). The advantage is front-loaded: on SPY from 1993 it is a drawdown cut (ratio 0.590) at about equal Sharpe (+0.039), significant on neither leg. Not an edge, and it does not make G1 live. Audit: `docs/audits/2026-10-08-ov1-ma-overlay-total-return.md`. VM (#421), `min(1, σ_target / σ̂_20d)`, is **FOUND as an increment over OV-1**: OV-1 × VM against OV-1 has ΔUI CI clear of zero in every run (max DD −19.7% vs −44.6%), but the ulcer ratio is a thin margin (0.722 vs the 0.75 floor; 0.762 BOUNDED under a one-session lag) and the Sharpe gain is not significant. Adopted as the core (#429, `docs/north-star.md`); `analytics/overlay/live.py` puts its daily state in the digest (#423). Audit: `docs/audits/2026-10-08-vm-overlay-increment.md` | `context/analytics.md` |
| `signals/` · `utils/` | Alerting and dedup daemon (detection lives in `analytics/`); shared Telegram, yfinance and EDGAR clients; the two config-universe loaders | `context/signals.md` |
| `web/` | FastAPI backend plus Svelte 5 / Vite UI | `context/web.md` |
| `tools/` | One-shot analysis, audit and research-ingest scripts, outside the daemon and CLI surface | `context/tools.md` |
| `scripts/` | Three maintenance one-shots that act on the DB or the test fixtures: `db_prune_backtests.py` (`make db-prune-backtests`), `extract_regression_fixture.py` (called by `make regression-update`), `profile_suite.py` (no target; see memory `project_suite_runtime_profile.md`) | — |
| `trade/` | Empty placeholder (both files are 0 bytes); `make wifey-open-trades` fails loudly. An order layer would land here in Phase B | — |
| `tests/` | pytest suite; tests import from lib modules and pass mock dependencies directly | — |
| `migrations/` | Seven one-shot scripts, run by hand, each refusing to start without a `.bak`. 001/002 rewrite `run_id` and cascade to `backtest_trades`; 003–005 update `signal_alert_outcomes` in place (its key carries no measured value); 006/007 delete from `ohlcv`, and 007's rows cannot be recovered by refetch. Check what the target table's key is made of before choosing a shape. Routine schema changes go through `analytics/store/schema.py`'s migration list | `context/migrations.md` |
| `.claude/hooks/` | Destructive-command guard, foreground-run and shell-hygiene advisories, the branch guard on edits, and `advise-lifecycle.py` (PR create/merge, session close-out). `SessionStart` runs `tools/session_digest.py`. Covered by ruff and mypy | `context/hooks.md` |
| `config/` | `stocks.json` (gitignored 13-symbol live watchlist), `universe.json` (committed 505-member research universe), `strategy_params.toml` (shared base inherited via `extends`), `youtube_channels.toml` (gitignored; `.example` committed) | `context/config.md` |
| `deploy/` | `backup-analytics.sh` (local leg), `backup-offsite.sh` (rclone leg), `notify-failure.sh`, and opt-in `wifey-*` systemd user units (backup ×2, templated alert, signal-watch, universe-sync, daily-check). `deploy/windows/` is the Windows half of the same jobs: `job.sh`, `load-env.sh` (strips CR) and `install-tasks.ps1`. Do not re-sync `job.sh` from the parent: that one shims `deploy/run-job.sh`, which wifey does not have. Every job is one-shot | `deploy/README.md` |

### Sleeve verdicts

Nine sleeves have been built and measured on equities, eight on price and `insider/` on
filings, and every one is non-positive. Do not rebuild a shelved sleeve. The free-data edge-hunt
arc is concluded (`docs/audits/2026-06-24-honest-exit-free-data-edge-arc.md`); a new free-data
hunt needs an explicit user go, and any re-opening is per candidate.

A guardrail firing means the construction failed its own neutrality precondition, so that cell
is not evidence about the underlying premise.

| Sleeve | Verdict |
| --- | --- |
| `forecast/` EWMAC trend | **G2 FAIL** — portfolio Sharpe −0.05, negative before costs, so a signal failure rather than a cost failure |
| `xsmom/` cross-sectional momentum | **G3 FAIL** — combined Sharpe −0.156 @2bps, negative at 0bps; `corr_to_trend` +0.62, so no diversification win either |
| `xsmom/residual.py` residualised XS | **FAIL** — committed `broad_residual_skip` +0.15 @2bps, DSR 0.44, boot_lo<0. The long-only leg's +0.88 is survivorship- and beta-confounded and is not the gated cell |
| `lowvol/` low-beta / BAB | **FAIL** — committed cell −0.069 @2bps, DSR ~0.03; β guardrail fired (β +3.9) |
| `xasset/` cross-asset TSMOM | **FAIL (clean)** — `broad_ls` +0.41 cost-free, +0.36 @2bps, never ≥0.7; PBO ~0.79. The β guardrail held (−0.083): the construction diversified as designed, and the premium is too weak in free-ETF proxies |
| `pead/` PEAD-lite | **FAIL** — `broad_ls` +0.10 @2bps, DSR 0.20; β guardrail fired (β ≈ +113, governor saturation on sparse daily cohorts). The controlled mega arm (β −0.40) drifted negative (−0.53) |
| `gapfill/` gap-fill magnet | **EXCLUDED, direction refuted.** Cost-free the magnet returns −0.460, so gaps continue rather than revert; the post-hoc inverse (+0.392) is under 0.7 before costs, and at ~211× daily gross turnover a 1bp fee costs ~0.9 Sharpe. Quote "90.3% of gaps fill within 60 sessions" only with its null: a matched placebo level fills 88.9%, so the gap-specific lift is +1.5pp (peak +5.7pp at 5 sessions). Audit: `docs/audits/2026-08-14-edge-hunt-5-gapfill-magnet.md` |
| `velocity/` velocity alternation | **EXCLUDED as a null.** The β guardrail fired (−1.646), and beta-hedged it is −0.169 at alpha t −0.49. `velocity = depth / duration` correlates +0.499 / +0.546 with its components and performs like depth alone. `long_only` +0.649 gross hedges to +0.004, i.e. pure market beta, so `deploy_grade`'s long-only leg reads market exposure; changing that gate is a user call. The time-series form the pundit described is untested and not queued. Audit: `docs/audits/2026-08-14-edge-hunt-6-velocity-alternation.md` |
| `insider/` H-024 routine vs opportunistic | **EXCLUDED as a deployable sleeve; the premise is not refuted, because the test is underpowered.** Keep those two apart. T1 (opportunistic L/S, 1mo, ADV-weighted) is Sharpe −0.468 net and −0.402 gross, DSR 0.001, boot_lo −1.054; β −0.167 is near-neutral, so it is evidence about the premise. All four reversal pairs are `measurable`, but `indistinguishable` means only that the CI contains zero: paired CIs of about ±150 bps/mo against the paper's 82 bps/mo can neither separate the arms nor exclude the effect. T4 is the cell likely to be misread: net +0.756 and boot_lo +0.147, but β +1.043, hedged +0.337, alpha t +0.99, DSR 0.65, and its routine placebo returns +0.546. Weights are trailing dollar ADV, a liquidity weight, since no market-cap series exists. Panel routine share is 68.1%. Re-opening needs an explicit user go and a stated lever, and the lever is more history, not more names. Audit: `docs/audits/2026-09-20-h024-insider-phase3.md` |
| `exits/` MFE-MAE diagnostic | **EXIT-FIXABLE at the cohort level** (n=264): of the 157 losses that could show excursion, 43.9% reached ≥1R before stopping (CI 36.4–51.8%). Still blocked per edge (0 of 30 loss cells reach n=30), and the ledger predates the outcome fix. Audit: `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md` |
| `exits/` policy replay A/B | **BOUNDED** — the lever's ceiling is +0.368R of paired uplift, and it buys no measurably profitable book. Every arm beats `fixed` on a paired CI clear of zero, and the effect is the time lever alone (`time_only` +0.317R vs `composite` +0.297R). A paired CI certifies "A beats B", never "A makes money": with the 31 session days as the unit no arm's mean R clears zero, and the swept maximum (t +2.52) misses the Bonferroni bar of 2.81. Baseline avg_r −0.176 (t −1.84); both parameters in-sample. The "peaks at bar 3" table is the arm's avg_r at `time_stop=k`, not open-position R. Audit: `docs/audits/2026-08-14-exit-policy-ab-v1.md` |

### Footguns

Read these before touching the mechanisms they name. Narratives for entries without their own
audit are in `.claude/context/footguns.md`.

#### Storage and writers

- `analytics/store/_common.py::_upsert` uses explicit `conn.register` / `conn.unregister` in
  try/finally; the implicit replacement scan corrupts the malloc heap.
- Open every write site in `analytics/` and `web/` through
  `analytics/db_retry.py::connect_with_retry`. The one exception is `web/api/routers/stats.py`'s
  cache write, where a ~52s retry would block the response the cache exists to speed up. Narrow
  every `except duckdb.IOException` with `is_lock_conflict`, because DuckDB raises that class
  for every I/O failure, and an unnarrowed handler reports a missing or corrupt database as
  "busy". `read_only=True` does not admit a second process: a writer refuses a read-only opener
  with the same `Conflicting lock` message a second writer gets.
- Import `DEFAULT_DB_PATH` from `analytics.store` or `analytics.data_store` (it lives in
  `analytics/store/_common.py`); never redefine it in a runner.
- `upsert_backtest_run` requires three provenance kwargs, because `backtest_runs` has four
  writers and a key hashed from the parameter tuple alone lets one replace another in place:
  `origin` names the writer (`"sweep"` stays unsuffixed so historical `run_id`s resolve);
  `live_parity` is `cfg.live_parity.identity()`, the gate set that executed, never a declared
  block (`identity()` returns None when no gate is on, so historical `run_id`s are unchanged);
  and the ADR value is `effective_adr_threshold(declared, timeframe, adr_exempt=…)`, what
  executed rather than what was declared. mypy cannot see a required kwarg through a `**dict`
  splat, so run the suite, and grep `tests/` for `INSERT INTO backtest_runs VALUES` before
  adding a column, since `test_schema_insert_arity.py` does not scan `tests/`. A column in the
  identity hash cannot be corrected in place without a migration that respects code eras.
  Audits: `docs/audits/2026-08-07-backtest-runs-writer-collision.md`,
  `docs/audits/2026-08-26-run-id-live-parity-axis.md`; narrative:
  `migrations/002_adr_threshold_executed.py`.
- A writer that owns only some of a table's columns must not replace the whole row.
  `upsert_signal_outcome` uses `ON CONFLICT … DO UPDATE` with `COALESCE(excluded.x, x)` on the
  three outcome columns, so a re-detection cannot erase a resolved label. Test both
  directions: "never update outcome" passes the obvious test and breaks every caller that
  legitimately sets one.

#### Gates and configuration

- Causality is enforced by `tests/test_lookahead.py`, a truncated-series property test
  asserting no detector or backtest fill depends on bars after its `open_time`.
  `_KNOWN_LOOKAHEAD_DETECTORS` is empty and all 16 detectors pass. Audit:
  `docs/redesign/phase0-lookahead-audit.md`.
- A per-strategy flag whose correctness depends on a second flag belongs in the shared base.
  `adr_suppress_threshold` and `volume_suppress` select on quantities correlated at +0.65, so
  declaring both without `adr_exempt = true` discards ~99% of a strategy's signals;
  `load_signal_config::voided_volume_gates` refuses the pairing. A test that a config value
  parsed cannot detect that the value produces nothing. Audit:
  `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.
- The ADR gate is intraday-only: `adr_gate_applies(timeframe)` takes `timeframe` as a required
  argument so mypy makes every call site state it, and unknown timeframes fall closed. On `1d`
  and `1wk` the ratio stops measuring exhaustion and `chasing` is true by construction, yet it
  stays dispersed, so a degenerate gate looks functional. Re-derive any crypto-inherited
  constant against equity bar counts (`4h` RTH is 2 bars/day, not 6). `check-dead-surfaces`
  finds exact zeros only, so it cannot see a partial haircut. Audit:
  `docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md`.
- One `bt_days` feeds both the OHLCV cache and `run_scan_cycle`. Cross-check a recorded
  parameter against a recorded observable (`data_end_ms - data_start_ms`), and when a value
  reaches its consumer through a cache, fix whoever populates the cache.
  `passes_ev_gate` returns `True` below `min_trades`, so the gate fails open silently. Audit:
  `docs/audits/2026-08-06-live-ev-gate-window.md`.
- `conflict_resolver` stays off inside the sweep: it reads `confidence_ratings`, so enabling it
  in the pipeline that produces them is a fixed-point iteration that did not converge. Five
  gates are on in `config/strategy_params.toml`, the shared base (not a Makefile flag), and
  `TestSharedBaseGateState` enforces it. Check data-flow direction before enabling a gate in the
  producer of its own input, and remember that "it converges" needs three points. The live path
  reads ratings already written and is unaffected. Audit:
  `docs/audits/2026-08-07-live-parity-ratings-sweep.md`.
- The live EV gate counts `long_closed_trades` / `short_closed_trades`, matching the direction
  whose `avg_r` it tests. A gate that fails open inverts the meaning of "stricter": at
  `min_trades = 20` the `1wk` gate is bypassed every time. A test that re-implements the code
  under test cannot falsify it, so extract the unit first. Suppression upstream of the recorder
  destroys evidence: a blocked leg never reaches `signal_alert_outcomes`. Audit:
  `docs/audits/2026-08-07-ev-gate-directional-sample-guard.md`.
- A shortfall must clear `min_avg_r_z` (default 1.64, one-sided 95%) standard errors below
  `min_avg_r`; a threshold on a point estimate is a coin flip. Correct for multiplicity where
  selection happens: BH suits a sweep, while Bonferroni on a per-leg operational gate would
  silently disable a fail-open gate. `BacktestSnapshot` is the hot path, so add a new statistic
  to both the cached and the computed type. Audit:
  `docs/audits/2026-08-07-ev-gate-significance-test.md`.
- `passes_sleeve_gate`'s bar is `GATE_SHARPE = 0.7`, declared and effective. `min_trl` and
  `n_obs` are not parameters, so re-adding a MinTRL leg touches every call site. When a
  threshold is a function of the data, solve for the bar rather than reading it off the source.

#### Outcomes, fills and data

- Credit the target you walked: `signal_alert_outcomes.rr_ratio` is the declared target and
  `tp_price` the effective one, and one `implied_tp_r` in
  `analytics/signal/outcome_backfill.py` serves the resolver, the scanner at fire time,
  `analytics/exits/audit.py` and `analytics/exits/mfe_mae.py`. Pooled live avg_r is −0.2553R
  (n=292, 2026-08-20, net and symmetric-gap-filled). Earlier figures differ because the ledger
  grew and the basis changed, so do not attribute the gap to either alone. Audit:
  `docs/audits/2026-08-14-exit-policy-ab-v1.md`.
- The live ledger is net of costs, like the backtest: `outcome_r` is
  `gross - outcome_cost_r`, and `outcome_cost_r IS NULL` means unpriced, never free. One
  `live_cost_r` in `analytics/signal/outcome_backfill.py` serves the resolver and migration 004
  and mirrors `engine.Trade.pnl_r` exactly: a `CostModel` replaces `fee_pct` rather than adding
  to it. Do not port the parent's flat-fee `net_R` resolver, which prices on a basis the
  backtest does not use. A resolver change must pass `cost_model`/`fee_pct` through, or the
  ledger silently reverts to gross. Audit: `docs/audits/2026-08-19-live-ledger-net-of-cost.md`.
- A bar that opens beyond a level fills at the open, on both sides. `analytics/backtest/fills.py`
  holds the one rule and both books import it: `gap_fill_price` returns the bar open when the
  bar opened through the level, and `level_is_on_the_expected_side` keeps a malformed row from
  being priced as a gap. Measure both tails before pricing either: wins gap through their target
  more often than losses gap through their stop, so pricing only the adverse tail overstates
  losses by about 65%. Migration 005 restated the ledger. Audits:
  `docs/audits/2026-08-19-gap-through-stop-measurement.md`,
  `docs/audits/2026-08-20-symmetric-gap-fill.md`.
- A split restates every historical bar at the provider, while the bars already stored keep the
  old basis. `analytics/data_sync.py::sync` therefore re-fetches the overlap bar (`latest`, not
  `latest + 1`), compares it with the stored close, and re-syncs the whole series when it moves
  by more than `ADJUSTMENT_BASIS_TOL` (1%). That tolerance is safe only because
  `utils/yfinance_client.py` fetches with `auto_adjust=False`; under `auto_adjust=True` every
  ex-dividend date would trip it. The guard prevents a new seam but cannot repair a stored one,
  which needs a full re-backfill of that series. A big move is not evidence of a seam (MRNA's
  +177% on 40× volume is real), so check whether the provider still serves the jump. A wrong
  instrument is a different failure that re-backfill cannot fix (AVB; see
  `migrations/007_purge_avb_wrong_instrument_tail.py`). Audit:
  `docs/audits/2026-09-04-split-adjustment-seams.md`.
- A bar count is not a calendar span on an RTH tape: `4h` RTH is 2 bars/day. Fetch forward
  windows to `get_latest_open_time` rather than deriving a horizon from a bar count
  (`TestForwardWindowSpansRthGaps`); `regime.py` imports `cost_model`'s bars-per-day table
  (`TestBarsPerDayIsShared`), and `min_periods` clamps to the window via `atr_window_bars`. A
  wall-clock hour is not a stable key either: `4h` sits on a fixed UTC grid (13:30 / 17:30 UTC),
  which is 09:30 / 13:30 ET in summer and 08:30 / 12:30 ET in winter, so anything keyed on an ET
  hour mislabels every winter bar. Narrative: `.claude/context/footguns.md`.

#### Statistics

- Check that a metric's floor is reachable by every row before quoting its median. `exits/`
  takes a loss's MFE from `fav[:-1]`, so a loss resolved on its first held bar has
  `mfe_r == 0.0` by construction. A subgroup chosen to remove one bias usually adds its own, so
  quote the bracket rather than either endpoint. Audit:
  `docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md`.
- A verdict meaning "ruled out" tests CI containment, never n.
  `audit_guard.CellVerdict.powered_null` requires the CI strictly inside ±`bar`, is computed
  once, and defaults to `False`. `INSUFFICIENT` and a powered null are different states. Audit:
  `docs/audits/2026-08-13-warning-value-audit.md`.
- An `audit_guard` verdict is priced per session day. `AuditCell.cluster_key` takes one entry
  per `supp_r` row and sits before the defaulted `kept_r`, so mypy refuses a call site that omits
  it. `cluster_bootstrap_ci` resamples whole days, and the Holm leg uses `n_eff = n / DEFF`. A
  mismatched key length fails closed to `INSUFFICIENT`, and the deflator can only shrink (`icc`
  clamped to `[0,1]`). A block bootstrap cannot do this: it absorbs only serial dependence,
  and same-day cross-symbol trades are scattered through the array. The error size depends on
  the cell cut, so a per-strategy re-cut re-opens it. `[bias.regime]`'s EXCLUDED is unblocked,
  not overturned: the replay reports FLIP justified and MAPPING UNTESTED, and a wider CI removes
  evidence rather than supplying the opposite conclusion; `[bias.regime].mode` is the
  operator's call. `session_day_keys` floors to the UTC day, which is the session day for US RTH,
  so it must not be ported to a 24h tape. Audits:
  `docs/audits/2026-08-20-audit-guard-cross-sectional-clustering.md`,
  `docs/audits/2026-08-20-audit-guard-cluster-key-fix.md`.
- Deflate a pooled cross-section before quoting any t-stat from it (`make wifey-n-eff`). The
  505-member universe carries `n_eff` ≈ 2.96 independent series at `1d` (mean pairwise `rho`
  +0.3365), so a naive pooled t-stat is inflated about 13×; `1wk` is 2.88, and `4h` is 4.92 on a
  size-tilted 21% subset. `tools/distil_power.py` returns `n_obs` undeflated when both
  `--n-series` and `--n-eff` are omitted, so always pass one. `n_eff` tends to `1/rho` as names
  are added, so more names do not buy breadth. The estimator refuses to emit flags when it could
  not measure. Narrative: `.claude/context/tools.md`.
- Pool a mean over its denominator: `digest_lib::_pooled`
  (`SUM(avg_r * closed_trades) / SUM(closed_trades)`) is the one definition for all six queries.
  When two aggregates sit side by side, check they pool the same way, and mask numerator and
  denominator together.

#### Research ingest

- Level parsing fails silently, and defects have been found by running the code, not by reading
  it (narratives in `.claude/context/tools.md`):
  - `tools/x_route.py::first_level` strips month-anchored years (`MONTH_YEAR_RE`), percentage
    ranges (`PCT_RE`) and chart timeframes (`TIMEFRAME_RE`). The first two are shared with
    `tools/route_dedup.py` and `tools/pundit_score.py`; do not fork them.
  - `check_level_order` judges only rows with both legs present. Encode a one-legged row as
    entry plus target with the stop left unstated, rather than inventing a level.
  - `tools/route_dedup.py` is advisory except for `already_routed`: it never drops a row, and
    `mark` runs strictly after the sink write.
  - Record the underlying the author quoted (`GC=F`, `SI=F`), not a "tradeable proxy" like
    `GLD`/`SLV`: a proxy keeps levels in the wrong units, the sanity gate rejects them, and the
    row silently scores against the call-time price instead.
- A units error near 1× is more dangerous than one near 10×, because no gate can see it. Anchor
  a plausibility check to the price at `call_ts_utc`, never to the symbol's recent range.
- A cap that silently truncates looks like an absence. `tools/video_marks.py::keep_items` ranks
  by specificity descending, then timestamp ascending, so a tie at `ITEM_CAP` favours earlier
  material; read a thin Stream C yield from a dense video as a cap artifact first.

## Code style

- **Linter and formatter**: ruff, covering linting, import sorting and formatting.
- **Type checker**: mypy strict (`disallow_untyped_defs = true`). Every function needs type
  annotations including the return type, `-> None` for test methods. Use `from typing import
  Any` for mock parameters in tests.
- **Markdown**: markdownlint-cli2.

## Testing

pytest plus `unittest.mock`. Tests make no real network calls: lib functions accept a `client`
parameter and tests pass a `MagicMock` directly. Analytics tests use
`duckdb.connect(":memory:")` and never touch the real `analytics.db`.

Mock the open site, not its neighbours. Production opens the DB through `connect_with_retry`,
so mocking `duckdb` and `init_schema` does not isolate a test from `analytics.db`. The failure is
invisible on a quiet box and surfaces only when something else holds the write lock, as a
timeout or lock error in a test that never mentions a database.

`make test-regression` compares backtest pipeline output to golden JSON in `tests/fixtures/`,
and skips if the fixture parquets are absent. Regenerate with `make regression-update` after an
intentional change.

A "did not change" assertion passes both when the invariant holds and when the perturbation
never arrived, so every one needs a positive control that observes the channel the guard
protects. Three traps: a cap can turn a perturbation into a no-op (xsmom's fixture perturbed a
ramp already pinned at the +20 EWMAC cap); a control on a downstream value can fire through a
different path; and vacuity is per shift, so measure it. `make check-orphan-tests` cannot see
this class. Audit: `docs/audits/2026-08-13-vacuous-causality-guards.md`.

A mutation test proves a guard is reachable by its own test, never that its scope matches the
sentence written beside it. After mutation-testing, ask what the doc sentence claims, build an
input that satisfies the claim but not the guard, and run it; if it passes, the sentence is
wrong. There are three distinct fixture failures: one that never reaches the guard (vacuous),
one that reaches it at the wrong scope, and one that asserts against inputs that do not exist
(an intruder test built from entries the real remote never has). Prefer a characterization test
naming a known hole over a test asserting a protection you have not constructed.

Mechanical guards, because prose does not enforce:

- `tests/test_schema_insert_arity.py` (in `make test`) ties every positional INSERT to its
  table's real column list, and checks `INSERT … SELECT` by name order. Adding a column to a
  positionally written table means updating that statement in the same PR.
- `make check-orphan-tests` (advisory, heuristic) reports `Test*` classes that name a unit but
  never call it. Its `not-importable` verdict means the unit is a closure no test can reach, so
  extract it before fixing it.
- `make post-branch-checks` (advisory) runs the thirteen mechanical `/post-branch` checks:
  closed queue items, handoff claims, undocumented new files/modules/targets, amended Make
  targets (`amended-targets`: a doc names the target but its list of the recipe's overrides is
  now short), negative claims, stale doc indexes, MD018 headings, the MEMORY.md cap, the
  handoff's size against `HANDOFF_MAX_LINES`, dead cross-document section anchors
  (`stale-anchors`, which also sweeps the memory tree), and the pre-flip `sensitive-terms` gate.
  Details for every leg: `.claude/context/tools.md`.
  - `sensitive-terms` screens three surfaces: the tracked tree, this branch's commit content and
    its commit messages, because a flip republishes the whole history. The term list,
    `.claude/sensitive-terms.txt`, is gitignored by policy (a tracked list of the words you are
    hiding is the leak), so an absent list is a `NOT CONFIGURED` finding, never a skip. Output
    masks the term and excludes main's accepted baseline. The list is single-copy and covered by
    `BACKUP_FILES`.
  - PR titles and bodies are a fourth surface that leg cannot see. Screen the composed text with
    `make post-branch-text FILE=<path>` (`FILE=-` reads stdin) in `/post-branch` phase 5, beside
    `make preflight`, before it posts; a posted body is public the moment it lands. It gates
    (exit 1 on a hit, which `make` shows as 2) and prints line numbers with a masked term, never
    the matching line.
  - `negative-claims` narrows through `NEGATIVE_CLAIM_EXEMPT`, keyed on `(path, token)`, and
    drops a hit only when every matched token (or, on a line with no reachable subject, every
    matched marker) is exempt. Keep the line as the unit: scoping to a window around the regex
    match would have suppressed this leg's only true positive.
- `tests/test_outcome_backfill.py::TestMaxHoldCalibrationCoverage` (in `make test`) fails if a
  timeframe declared in any `config/signal_watch*.toml` has no `DEFAULT_MAX_HOLD_BARS` entry.
  The outcome resolver refuses an unlisted timeframe (`counts["no_hold_cap"]`) rather than
  falling back to `max(...)`. A guard whose only fix is a calibration decision has to ship with
  that decision.

## Dependencies

Poetry: `poetry install --no-root`. Runtime is `duckdb`, `pandas`, `pyarrow` and
`exchange-calendars` (NYSE trading calendar, isolated to `analytics/trading_calendar.py`). Dev
dependencies are ruff, mypy, pytest, pytest-mock, pre-commit, type stubs and pandas-stubs.
Change `poetry.lock` through `poetry add` / `poetry remove`, never by hand.

`yt-dlp` is pinned to a nightly on purpose, with a `.dev0` floor: the newest stable release
returns 403 on all YouTube media while captions still resolve, so the vision pass loses every
frame and the run merely looks unlucky. `pyproject.toml` therefore sets
`[tool.poetry.dependencies] yt-dlp = { allow-prereleases = true }`, without which the floor
cannot resolve. Keep the nightly pin; the newest stable is behind the floor. The user's global
yt-dlp config masks this locally, so a clean box fails where this one does not (memory
`reference_ytdlp_js_runtime_403`).

## Documentation

Update `README.md` when changes affect project structure, CLI commands, features or behaviour.

`docs/system-overview.md` is the best single onboarding read. §9 is safe to send externally,
and each figure in §4 ships the query that produced it — re-run those rather than editing a
number in place.

### Where knowledge goes

If a session would not know to look something up, it belongs in an always-loaded file; if it
would, it belongs on demand. Always-loaded content is paid on every conversation, so it has to
be content whose absence causes silent damage.

| Surface | Loaded | Committed? | Holds |
| --- | --- | --- | --- |
| `CLAUDE.md` | always | yes | Rules binding on any session here regardless of task: commands, conventions, footguns, sleeve verdicts. No personal preferences |
| GitHub Issues | session start (digest) | n/a | All planning: to-dos, open questions, decisions, future plans. The `SessionStart` digest lists the open ones |
| `MEMORY.md` index | always | no | A routing table — one line per memory, plus Current State (last session and pointers). Enough to decide whether to open a file, never the content |
| `memory/*.md` topics | on demand | no | The detail behind an index line: user preferences, feedback and its reason, project history, references |
| `.claude/context/*.md` | on demand | yes | Long-form module references and footgun narratives, reached via the Project structure pointers |
| `docs/plans/next-conversation-prompt.md` | session start | no (gitignored, in-repo) | Sequencing only: an ordered list of Issue numbers, host state and standing hazards. Never a to-do of its own. Pruned every run |

The index is a router, not a store: content that grows without bound belongs in a topic file
with a one-line pointer.

### Session memory protocol

At the end of every session where anything changed, update the Current State section of
`MEMORY.md` in this checkout's memory tree (see Fork lineage for the path) without being asked:
a one-line summary of what changed, plus pointers (Issue numbers, PRs). Open work goes to an
Issue, never into Current State. Keep each update O(1):

- Current State holds at most 6 bullets. Before adding a 7th, roll the oldest, verbatim, into
  `memory/project_session_log_<month>.md`, then grep the log to confirm it landed.
- The whole index stays under ~17KB. `make post-branch-checks`' `memory-cap` leg enforces it;
  no hook does. `make status` measures the file with `wc -c`.
- "Last session" is at most 2 lines; every other bullet is exactly 1 line.
- Session logs have no size limit. Prune by moving, never by deleting.
- Open questions are Issues labelled `question` or `decision`. `project_open_questions.md` was
  migrated on 2026-09-30 and takes no new entries.

## Agent skills

Skills live in `.claude/skills/<name>/SKILL.md` and are invoked with `/skill-name`; each one's
description and trigger conditions load every session, so use them proactively. What their
descriptions do not say:

- `.claude/` is a denylist: `.gitignore` names only `settings.local.json` (absolute machine
  paths), `RESUME.md` (session scratch) and `sensitive-terms.txt` (never tracked, by policy).
  Skills, agents, context, hooks and `settings.json` all ship, and a new artifact class is
  tracked by default. Check `git check-ignore` before assuming.
- Load `/frontend-design` before any Svelte, CSS or UI change.
- Invoke `/post-branch` before `gh pr create`, while the branch is still local-only.
- When a skill upgrade fixes a defect, ask whether the defect changed coverage or only
  presentation. A defect that changed coverage (what got dropped, capped or never fetched)
  requires re-ingesting the old corpus, because the missing rows cannot be recovered from the
  per-item notes; `video_marks.py::keep_items`' `ITEM_CAP` is that kind. A defect that changed
  only presentation, attribution or routing of captured material does not, because the evidence
  is still on disk; `route_target`'s `retrospective`/`rejected` drop was that kind.
- `/wfo-sweep` is the trusted production path for `tp_r`; `/config-refresh` covers the other
  config dimensions.
- `/sanity-check` runs weekly or after a large refactor. Its mechanical half is
  `make sanity-checks`, which gates. The fork-drift leg excludes the dated trees, where a
  past-tense claim is correct by construction.
- The sweep skills (`atr-sweep`, `volume-sweep`, `wfo-sweep`, `param-sweep-apply`,
  `backtest-findings`) are dormant while the TA book is frozen.

### Issue tracker

GitHub Issues on `s10023/buibui-wifey-wall-street-bot`, every `gh` call prefixed with the
personal token and passed `--repo`, every body screened before it publishes. See
`docs/agents/issue-tracker.md`.

### Triage labels

The five default roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`), beside the repo's priority, kind and effort labels. See
`docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/`, both created lazily, with filed research verdicts
treated as ADRs. See `docs/agents/domain.md`. The review standards are in `CODING_STANDARDS.md`.

### mattpocock-skills — this repo's adaptations

The plugin is installed account-wide; the flow and its cross-repo rules are in the account
`CLAUDE.md`. Here, five bindings:

- **`/implement`'s "full suite once at the end" is `make preflight`** at `/post-branch` phase 5,
  never `make test` and then `make preflight`.
- **`/implement-spec` runs implementers in worktrees, which hold tracked files only** — no
  `docs/plans/`, `analytics.db`, `config/stocks.json` or `.claude/sensitive-terms.txt`. Give a
  ticket that needs one to an implementer working in the main checkout, and push from a
  worktree with an explicit `HEAD:refs/heads/<branch>` refspec, because a worktree branch can
  track `origin/main`.
- **The PR body is Matt's `pr` shape, written through `/pr-summary`**, which adds this repo's
  title and test-plan rules and the file output.
- **`/handoff` is for forking a side task; it never replaces the standing handoff**
  (`docs/plans/next-conversation-prompt.md`, written by `/post-branch` phase 6).
- **`diagnosing-bugs` on a signal that did or did not fire builds its Phase 1 loop with
  `/investigate-strategy`**, which replays the detector at the candle.

## Git conventions

Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`) and branch
prefixes (`feat/`, `fix/`, `docs/`, `chore/`).

Branch off the latest `main` before the first edit:

```bash
git fetch origin && git switch main && git merge --ff-only origin/main
git switch -c <type>/<slug>
```

If work has already started on `main`, `git switch -c` carries the uncommitted changes over.
Verify with `git status -b` rather than from memory. A branch cut from a stale `main` replays
merged work, and one cut from another feature branch inherits its whole diff.

Keep `.env`, `config/stocks.json` and IDE files out of commits.

Set the per-repo git identity before any commit: this account uses
`s10023 <ngkhaijian@gmail.com>`, and the global config carries a work identity that would
mis-attribute commits. Check `git config --local user.email` first. s10023 remotes use the SSH
alias `git@github.com-personal:...` (memory `reference_ssh_host_aliases.md`).

Pass `--repo s10023/buibui-wifey-wall-street-bot` to every `gh` command. The `gh` default repo
is intentionally the parent, so a bare `gh pr view N` resolves against the wrong repo; leave
`gh repo set-default` alone.

Invoke `/post-branch` before `gh pr create`, while the branch is still local-only, and fold its
"Documentation updates" section into the initial `--body`. Actions minutes are a hard budget on
these private free-tier repos, and a doc-sync commit pushed to an open PR re-runs the whole
5-check matrix. Phase 6's zero-commit tail still runs last: re-verify PR state, then stamp the
handoff. The `PreToolUse` hook on `gh pr create` (`advise-lifecycle.py`) prints a reminder; it has to be
`PreToolUse`, because a `PostToolUse` hook fires after the PR already exists.

### CI quota

Decide the flip from the account's Actions allowance, never from the diff's paths. While the
monthly allowance holds (it resets on the 1st), a private PR runs the whole matrix. Once it is
exhausted, real CI needs the repo public; flip back to private once the PR merges. A Python diff
runs the path-filtered jobs rather than needing the flip.

Pushing a branch costs nothing: `push:` triggers only on `main` and `pull_request:` only on a
PR, so the meter starts at `gh pr create`.

Confirm each flip with the user. Standing authorisation covers the mechanics, not the timing,
and a flip publishes the parent's pre-fork commits for its duration.

```bash
GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-wifey-wall-street-bot \
  --visibility public --accept-visibility-change-consequences
# ...open PR, let CI run, merge...
GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-wifey-wall-street-bot \
  --visibility private --accept-visibility-change-consequences
```

Before flipping back, wait for main's own push run with `make wait-ci-main`. Merging starts a
fresh run on `main` (`lint.yaml` and `security-scan.yaml` trigger on `push: branches:[main]`),
and going private kills jobs created after the flip; `Regression tests` needs
`lint-typecheck-test` and is not created until about 4 minutes in. The target gates on a
job-count floor, because "nothing pending" is vacuously true before a chained job exists, and it
counts `push`-event runs only, ignoring the GitHub-managed `dynamic` dependency-graph run. After
a corrective push, confirm `headRefOid` changed before trusting a check rollup.

A check that fails at ~3s with `steps=0` while the repo is private is billing: verify duration,
visibility and step count, then merge without debugging. A path-filtered skip reports
`SKIPPED` and an exhausted allowance reports `FAILURE`, both at `steps=0`, so the totals alone
cannot separate them. A green last run is permission to try, not a guarantee, because the
allowance can drain between that run and your PR; the recovery is to flip public and re-run the
existing runs in place. While the repo is public, further pushes are free, so land any
correction during the window rather than after the flip back. Read any externally pasted
content in a doc before committing it. Narrative: memory `reference_ci_steps_counts_skipped.md`.

Paths tell you what a private PR loses once the allowance is gone. Three of the five checks sit
behind `dorny/paths-filter` on `**/*.py` and `web/ui/**`, so a `.md`-only diff executes nothing
in `lint-typecheck-test`, `Regression tests` and `frontend-check`. `make lint-md` reproduces
CI's `markdownlint`, so the only check a docs-only PR forgoes is Trivy's secret scan.
`markdownlint` and `Trivy` have no path filter, so an exhausted allowance zeroes them too.

Local markdownlint lints a superset of CI: the CI job passes `globs: **/*.md !venv`, but
markdownlint-cli2 still applies the negations in `.markdownlint-cli2.jsonc`, and locally it also
picks up untracked files, since it does not read `.gitignore`. `make lint-md` green therefore
means CI green. The one negated tracked file is `.github/pull_request_template.md`, which carries
a live MD041 error. Re-derive an apparent divergence rather than fixing it:

```bash
git ls-files '*.md' | wc -l                 # tracked
make lint-md 2>&1 | grep '^Linting:'        # what local actually lints
```

The public window's cost persists after the flip back. wifey's history was copied rather than
forked, so the still-private parent's pre-fork research commits are published while it is
public; anything cloned or indexed then stays out, and a fork created then keeps its own
network. It is an IP and history exposure rather than a secrets one (every blob scanned clean).
Flip back promptly after the merge, and check `forkCount` is still 0 before you do.
