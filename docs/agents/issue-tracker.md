# Issue tracker: GitHub

Issues and specs for this repo live as GitHub Issues on `s10023/buibui-wifey-wall-street-bot`.
Use the `gh` CLI for all operations. Read by the mattpocock skills (`/to-spec`, `/to-tickets`,
`/implement-spec`, `/triage`, `/wayfinder`, `code-review`) before they touch the tracker.

## Authenticate every call as the personal account, against this repo

The active `gh` account on this host is a work account, so a bare `gh` call writes as the wrong
user. The `gh` default repo is the parent, so a call without `--repo` lands on the wrong
tracker. Prefix every call inline and pass the repo every time — never `export`, never
`gh auth switch`, never `gh repo set-default`:

```bash
GH_TOKEN=$(gh auth token --user s10023) gh issue view 378 --repo s10023/buibui-wifey-wall-street-bot
```

Cloud sessions are refused GraphQL, which `gh issue list` uses; list over REST there:
`gh api 'repos/s10023/buibui-wifey-wall-street-bot/issues?state=open&per_page=100'`.

## Screen before you publish

Issues publish with the repo on a visibility flip, and a body is indexable on its own. Before
creating or editing an Issue, write the title and body to a file and run
`make post-branch-text FILE=<path>`; fix every finding first. Never put account figures,
balances or PnL in an Issue.

## Conventions

Every command below takes the `GH_TOKEN` prefix and `--repo s10023/buibui-wifey-wall-street-bot`.

- **Create an issue**: `gh issue create --title "..." --body-file <path>`. One line per
  paragraph or bullet, no hard wraps: GitHub renders a single newline as a line break.
- **Read an issue**: `gh issue view <number> --comments`.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with `--label` / `--state` filters, or the REST form above.
- **Make an issue a sub-issue of a parent**: `gh issue create --parent <parent> ...`, or
  `gh issue edit <parent> --add-sub-issue <child>` afterwards.
- **Comment on an issue**: `gh issue comment <number> --body-file <path>`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "<one-line verdict>"` — closing always carries a
  one-line verdict comment; a PR's `Closes #n` does it on merge.

## Labels every new Issue carries

The triage roles (`docs/agents/triage-labels.md`) say whether an agent can take it. On top of
those, every Issue this repo files carries the labels `CLAUDE.md` → Fork lineage → "Planning
lives in GitHub Issues" defines:

- **Priority**: exactly one of `p1` / `p2` / `p3`.
- **Kind**: exactly one of `build`, `mechanics`, `audit`, `hypothesis`, `ops`, `decision`,
  `question`.
- **Effort**: one of `effort:low|medium|high|max`, the session effort it needs. The
  SessionStart digest shows `effort:?` when it is missing.
- **`blocked`** when it waits on something named in its body.
- **`cloud-ok`** only when a cloud session can finish it from tracked files alone: no
  `analytics.db`, memory tree or Windows-host specifics.

`ready-for-agent` and `cloud-ok` are different claims: the first says the Issue is fully
specified, the second says where it can run.

## Specs from `/to-spec`

A spec published by `/to-spec` also carries a `## Decision Log` section naming, for each
decision, the observable that would reverse it. A **research** hypothesis or sleeve is not a
`/to-spec` Issue: it is pre-registered as a tracked doc under `docs/superpowers/specs/`
(indexed by `make docs-index`, gated by `tests/test_docs_index.py`), with an Issue pointing at
it.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature
requests; `/triage` reads this flag.)_

GitHub shares one number space across issues and PRs, so a bare `#42` may be either: resolve
with `gh pr view 42` and fall back to `gh issue view 42`, both with `--repo`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue, screened as above.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments --repo s10023/buibui-wifey-wall-street-bot`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog
  body. `gh issue create --label wayfinder:map`.
- **Child ticket**: a GitHub sub-issue of the map. Labels: `wayfinder:<type>`
  (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the
  driving dev.
- **Blocking**: GitHub's native issue dependencies. Add an edge with
  `gh api --method POST repos/s10023/buibui-wifey-wall-street-bot/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`,
  where `<blocker-db-id>` is the blocker's numeric **database id**
  (`gh api repos/s10023/buibui-wifey-wall-street-bot/issues/<n> --jq .id`, not the `#number`).
  `issue_dependencies_summary.blocked_by` counts open blockers only. A ticket is unblocked when
  every blocker is closed.
- **Frontier query**: list the map's open sub-issues, drop any with an open blocker or an
  assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me`, the session's first write.
- **Resolve**: `gh issue comment <n> --body-file <path>`, then `gh issue close <n>`, then append
  a context pointer (gist + link) to the map's Decisions-so-far.
