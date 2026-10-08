#!/usr/bin/env python3
"""PreToolUse hook — advises branching when the first tracked edit lands on main.

Self-authored (no third-party dependency) so every rule is reviewable here.
Wired via .claude/settings.json -> hooks.PreToolUse (matcher "Edit|Write|NotebookEdit").

WHY THIS EXISTS ON Edit/Write RATHER THAN Bash
    The existing guard-destructive hook is PreToolUse on *Bash*, so it cannot
    see a task that begins with an Edit — which is how nearly every task begins.
    On 2026-08-11 a Brief refactor got four files deep on main before anyone
    noticed, and no hook could have caught it.

WHY IT IS NOT A "TASK START" HOOK
    There is no hook event for "about to start a task". The same gap is already
    documented in AGENTS.md for `gh pr create`. The first mutation of the
    working tree is the closest observable proxy, so that is what this watches.

WHY IT IS ADVISORY, NOT BLOCKING
    Editing main is sometimes right: a hotfix, a gitignored note, an explicit
    operator instruction. A blocking guard would have to be overridden often
    enough that it would get disabled. Blocking is reserved for the
    unrecoverable (see guard-destructive.py); this is merely easy to get wrong.

WHY IT STAYS QUIET
    It fires at most once per session per branch, and never for gitignored
    paths. A hook that speaks on every edit is one you learn to scroll past --
    the same way the `stale SoT rows` line went 0-for-8 and stopped being read.

Protocol: Claude Code pipes {"tool_name","tool_input":{...},"session_id":...} on
stdin. Exit 0 always; advisory text is emitted as JSON on stdout.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

WATCHED_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
PROTECTED_BRANCHES = {"main", "master"}
_GIT_TIMEOUT_S = 3


def _git(*args: str, cwd: Path) -> str | None:
    """Run a git command, returning stripped stdout or None on any failure."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
            encoding="utf-8",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _is_ignored(path: Path, cwd: Path) -> bool:
    """True when git would not track this path.

    Editing a gitignored file on main is normal and correct here -- the handoff,
    the scratchpad and docs/plans/ all live outside version control on purpose,
    and warning about them would be pure noise.
    """
    try:
        out = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=cwd,
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def _already_warned(session_id: str, branch: str) -> bool:
    """One warning per session per branch; returns True if we've spoken already.

    Keyed on both so that moving back onto main later in a session warns again,
    while a run of edits on the same branch stays silent.
    """
    tag = hashlib.sha256(f"{session_id}:{branch}".encode()).hexdigest()[:16]
    marker = Path(tempfile.gettempdir()) / f"claude-branch-guard-{tag}"
    if marker.exists():
        return True
    # Fail open: a warning we cannot dedup is better than no warning at all.
    with contextlib.suppress(OSError):
        marker.touch()
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open — never break the session on a parse error

    if payload.get("tool_name") not in WATCHED_TOOLS:
        return 0

    raw_path = str(payload.get("tool_input", {}).get("file_path", ""))
    if not raw_path:
        return 0

    target = Path(raw_path)
    cwd = target.parent if target.parent.exists() else Path.cwd()

    # Cheapest and most selective check first: almost every invocation is on a
    # feature branch and exits here having run one git command.
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    if branch is None or branch not in PROTECTED_BRANCHES:
        return 0

    if _is_ignored(target, cwd=cwd):
        return 0

    session_id = str(payload.get("session_id", "nosession"))
    if _already_warned(session_id, branch):
        return 0

    # Report divergence so the advice can say "off latest main" accurately
    # rather than assuming the local branch is current.
    behind = ""
    counts = _git(
        "rev-list", "--left-right", "--count", f"{branch}...origin/{branch}", cwd=cwd
    )
    if counts and re.fullmatch(r"\d+\s+\d+", counts):
        _, remote_ahead = counts.split()
        if remote_ahead != "0":
            behind = (
                f" NOTE: local {branch} is {remote_ahead} commit(s) behind "
                f"origin/{branch} — pull first so the branch starts from latest."
            )

    message = (
        f"branch-guard: you are about to edit a TRACKED file while on '{branch}' "
        f"({target.name}). Project convention is to start every task on a new "
        f"branch off latest main.{behind} Create one now — uncommitted changes "
        f"follow a checkout, so nothing is lost: "
        f"git checkout -b <feat|fix|docs|chore>/<short-name>. "
        f"If editing {branch} directly is intended (hotfix, or the operator asked), "
        f"say so and continue. Advisory only — never blocking. "
        f"Rationale: CLAUDE.md > Git conventions."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": message,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
