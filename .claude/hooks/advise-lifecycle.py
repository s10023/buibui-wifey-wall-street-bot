#!/usr/bin/env python3
"""Lifecycle advisories — PR create, PR merge, session close-out.

Advisory only: it NEVER blocks. It emits `additionalContext` and exits 0.
Wired via .claude/settings.json on three events: PreToolUse (Bash), PostToolUse
(Bash) and UserPromptSubmit.

Why Python rather than the inline `jq | grep` one-liner it replaces
-------------------------------------------------------------------
The `gh pr create` reminder used to be an inline `jq -r ... | grep -q ...`. This
Windows host has no `jq`, so the pipe failed, `|| true` swallowed it, and the
reminder never fired here — silently, the same fail-open shape
`tests/test_hook_wiring.py` pins for the interpreter. A hook that runs on the
repo's own venv has no dependency the box can lack.

Head-anchored, like `advise-foreground-run.py`: a command merely QUOTING
`gh pr create` (a grep over CLAUDE.md, this docstring) must not trip it, so the
match is at the start of the first line or after a shell separator.

The reminder stays silent once its precondition is met: a `--body-file` / `-F`
whose file carries `/post-branch`'s `## Documentation updates` heading is the
evidence the skill already ran (#381). An advisory that fires when its
precondition holds teaches sessions to skim it. An inline `--body`, stdin (`-`)
or an unreadable file is no evidence, so those still fire.

Protocol: Claude Code pipes {"hook_event_name", "tool_input" | "prompt", ...} on
stdin. Exit 0 always; advice is JSON on stdout.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# A leading `GH_TOKEN=$(gh auth token --user s10023) ` prefix is still a head
# position — it is how every gh call in this repo is written (CLAUDE.md > CI quota).
_ENV = r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\$\([^)]*\)|\S*)\s+)*"
_SEP = r"(?:^|[;&|()]|&&)\s*" + _ENV
PR_CREATE = re.compile(_SEP + r"gh\s+pr\s+create\b")
PR_MERGE = re.compile(_SEP + r"gh\s+pr\s+merge\b")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_SEGMENT_END = re.compile(r"[;&|]")
_BODY_FILE = re.compile(
    r"(?:^|\s)(?:--body-file|-F)(?:=|\s+)(?:'([^']*)'|\"([^\"]*)\"|(\S+))"
)
DOC_UPDATES = re.compile(r"^##\s+Documentation updates\b", re.MULTILINE)
CLOSE_OUT = re.compile(r"\bdelet(?:e|ing)\s+(?:the\s+|this\s+)?ses", re.IGNORECASE)

POST_BRANCH = (
    "post-branch reminder: you are ABOUT TO run gh pr create. Invoke the /post-branch"
    " skill FIRST, while the branch is still local-only, and fold its"
    " Documentation-updates section into this PR body rather than editing the body"
    " afterwards: a doc-sync commit pushed to an open PR re-runs the whole 5-check"
    " matrix. Do not skip phase 4 (MEMORY.md, 6-bullet cap; SoT reconcile; claims"
    " audit). Re-verifying PR state still runs LAST. Advisory only."
    " Rationale: CLAUDE.md > Git conventions."
)

POST_MERGE = (
    "post-merge reminder: gh pr merge just ran. (1) run make wait-ci-main in the"
    " BACKGROUND; (2) if the repo was flipped public for this PR, flip it back private"
    " once main is green and forkCount is 0, confirming with the operator; (3) close"
    " any GitHub Issues the PR resolved; (4) prune the merged local branch; (5) append"
    " the handoff. Advisory only. Rationale: CLAUDE.md > CI quota."
)

CLOSE_OUT_ADVICE = (
    "close-out: the operator is about to delete this session. Before answering, write"
    " every surface: (1) new or changed to-dos become GitHub Issues on"
    " s10023/buibui-wifey-wall-street-bot, never memory-only. Screen each body first"
    " and chain the post behind it: make post-branch-text FILE=<repo-relative path>"
    " && gh issue create ... (FILE=- for stdin; an absolute Windows path loses its"
    " backslashes, the screen exits 2, and an unchained post goes out unscreened);"
    " (2) prune and rewrite"
    " docs/plans/next-conversation-prompt.md; (3) update MEMORY.md Current State"
    " (6-bullet cap). Then ask the two close-out questions from the global CLAUDE.md."
    " Advisory only."
)


def _blank_quotes(line: str) -> str:
    """Blank quoted strings, keeping every offset where it was."""
    return _QUOTED.sub(lambda m: m[0][0] + " " * (len(m[0]) - 2) + m[0][-1], line)


def _post_branch_ran(line: str, cwd: str) -> bool:
    """True when this `gh pr create`'s body file holds the Documentation updates.

    The flag is read only inside the `gh pr create` segment, so a chained
    `git commit -F <file>` cannot vouch for the PR body.
    """
    blank = _blank_quotes(line)
    head = PR_CREATE.search(blank)
    if head is None:
        return False
    end = _SEGMENT_END.search(blank, head.end())
    segment = line[head.end() : end.start() if end else len(line)]
    flag = _BODY_FILE.search(segment)
    if flag is None:
        return False
    raw = next(g for g in flag.groups() if g is not None)
    if raw == "-":
        return False
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return DOC_UPDATES.search(body) is not None


def advise(payload: dict[str, object]) -> str | None:
    """The advice for this event, or None to stay silent."""
    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        return CLOSE_OUT_ADVICE if CLOSE_OUT.search(prompt) else None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    # A `\`-continued chain is one logical line: `make post-branch-text ... \`
    # followed by `&& gh pr create` on a later line is still a PR create.
    command = str(tool_input.get("command") or "").replace("\\\n", " ")
    lines = command.splitlines()
    # Blank quoted strings first: a `|` inside `grep -E "a|gh pr create"` is regex
    # alternation, not a pipe, and must not read as a head position.
    first = _blank_quotes(lines[0]) if lines else ""
    if event == "PreToolUse" and PR_CREATE.search(first):
        cwd = str(payload.get("cwd") or Path.cwd())
        return None if _post_branch_ran(lines[0], cwd) else POST_BRANCH
    if event == "PostToolUse" and PR_MERGE.search(first):
        return POST_MERGE
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open: never break the session on a parse error
    if not isinstance(payload, dict):
        return 0
    advice = advise(payload)
    if advice:
        out = {
            "hookSpecificOutput": {
                "hookEventName": payload.get("hook_event_name"),
                "additionalContext": advice,
            }
        }
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
