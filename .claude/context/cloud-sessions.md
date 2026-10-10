# Cloud Sessions — what a claude.ai/code container is and is not

A cloud session runs in an ephemeral Linux container that clones this repo fresh. It is **not the
laptop**: everything that makes the laptop the operator's machine is gitignored, account-level or
host-bound, so none of it arrives. Ported from parent #1020, with this repo's own rows.

The laptop column was measured on 2026-10-10 with `ls` and `git check-ignore`. The cloud column
follows from each path being gitignored or outside the repo; no wifey container has measured it
yet, so re-measure in the first cloud session rather than trusting it. Rows marked
*parent-measured* come from the parent's container on the same date and the same shared
environment.

`tools/session_digest.py` prints a banner pointing here whenever `CLAUDE_CODE_REMOTE=true`, which
is set by the cloud harness and nothing else. In a container the digest's probe lines (scheduler,
freshness, backup, off-site, cadence) grade the container, so their REDs say nothing about the
laptop.

**A claude.ai Routine fires a cloud session, so this table binds every Routine too.** A cadence
that reads `analytics.db`, `docs/plans/`, the keys or Task Scheduler stays on the laptop.

## What is and is not there

| | Laptop | Cloud session |
| --- | --- | --- |
| `analytics.db` (and `analytics.db.bak`) | present | **absent**: gitignored, single-copy |
| `docs/plans/`: the handoff, `task-marks/`, `parent-sync/` reports, `scripts/`, `issue-drafts/`, `video-notes/`, `mechanics-backlog.md`, PR bodies | present | **absent**: gitignored |
| `signal_state.json` (dispatch watermarks) and `logs/` (job logs, read by the off-site probe) | present | **absent**: gitignored |
| Memory tree (`MEMORY.md` and topic files) | present | **absent**: it lives outside the repo, in no git remote |
| `.env`: Telegram, YouTube Data API, Groq, EDGAR contact, the web API token, rclone remote | present | **absent**: no alerts, no `/ingest-feed`, no off-site backup |
| `config/stocks.json` (13-symbol watchlist), `config/youtube_channels.toml` | present | **absent**: `config/universe.json` and the `.example` files are tracked |
| `.claude/sensitive-terms.txt` | present | **absent**, so `make post-branch-text` and the `sensitive-terms` leg report NOT CONFIGURED: screen by reading |
| `.claude/settings.local.json`, `.claude/skill-usage.log` | present | **absent**: machine-local |
| `~/backups/wifey` snapshots, the rclone config | present | **absent** |
| Task Scheduler `\wifey\` jobs (signal-watch, backups, universe-sync, daily-check) | the laptop runs them | none, and no Telegram push |
| Account `CLAUDE.md`, skills and plugins (`mattpocock-skills`, the `carver-futures` distillation) | present | **absent** unless the shared environment's setup script installs them (parent #1016) |
| The parent checkout `../buibui-moon-trader-bot` that `/sync-parent` reads | present | **absent**; reachable read-only only through `add_repo` |
| Repo skills, hooks, agents and context (`.claude/`) | yes | yes: tracked |
| `tests/fixtures/*.parquet` (3 files) | yes | yes: tracked, so `make test-regression` runs |
| Git history | full | **shallow** (*parent-measured*, 50 commits): `post_branch_checks.py` and `sync_parent.py` read history |
| GitHub | `gh` with the personal token | MCP tools scoped to this repo, REST only; `gh issue list` (GraphQL) is refused (*parent-measured*) |
| `.venv` / Python | Poetry venv | none until `poetry install --no-root` |
| Lifetime | persistent | reclaimed after inactivity: commit and push anything worth keeping |

So a cloud session can do work that lives in **tracked files plus the public web**: code and its
tests, docs, audits over committed fixtures, research surveys and Issue filing. It cannot run
anything against `analytics.db`, read or write the handoff or memory, send a Telegram alert, take
a backup, or use an account-level skill. That boundary is what the `cloud-ok` label asserts
(`docs/agents/issue-tracker.md`).

## Filing Issues from a cloud session

1. **File the Issue; never leave the work as a chat prompt alone.** The tracker is the planning
   surface, and a prompt in chat dies with the container.
2. **Label it `needs-triage`, never `ready-for-agent` or `ready-for-human`.** Granting the role is
   `/triage`'s job, and `/triage` is a `mattpocock-skills` skill the container may not have.
3. **Add `cloud-ok` only when the work fits the table above**, checked against it, not assumed.
4. **Work that needs the laptop carries a `## Local session prompt` block** in the body: a quoted
   prompt the operator can paste into a local session as-is, naming what to run and when to close
   the Issue.
5. **Screen the body by reading it** for account figures and employer or client names, since the
   term list is absent, and say in the reply that the list was not available.

A prompt with no Issue is right only for a one-off command the operator runs immediately, where an
Issue would outlive the need.

## What a cloud session must not stand in for

Do not fake a missing input. A study needing `analytics.db` is not "approximately" run on the
fixtures; an unreadable handoff is not reconstructed from memory of a previous conversation; an
account skill that is not installed is not imitated by hand and reported as if it ran. Say what
was missing and file the remainder.
