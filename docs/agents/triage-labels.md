# Triage Labels

The mattpocock skills speak in terms of five canonical triage roles. This file maps those roles
to the label strings in this repo's tracker. The repo's own labels (priority, kind, effort,
`blocked`, `cloud-ok`) sit beside these; see `docs/agents/issue-tracker.md`.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding
label string from this table.

`needs-info` is not `question` or `decision`: `needs-info` waits on a reporter to supply facts,
while `question` and `decision` mark a call only the operator can make.
