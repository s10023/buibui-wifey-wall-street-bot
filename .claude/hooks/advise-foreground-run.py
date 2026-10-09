#!/usr/bin/env python3
"""PreToolUse advisory hook — nudge long-running Bash calls into the background.

Advisory only: it NEVER blocks. It emits `additionalContext` and exits 0.
Wired via .claude/settings.json -> hooks.PreToolUse (matcher "Bash").

Why this exists at all
----------------------
"Run slow tests in the background" is a standing operator rule given three times
across both repos, and this repo's always-loaded tier carries its CONSEQUENCES
without the rule itself (CLAUDE.md's background-waiter and CI-wait paragraphs both
presuppose the run is already backgrounded). `.claude/settings.local.json` also
allowlists `Bash(make test *)` and `Bash(make test-regression *)`, so a foreground
run draws no permission prompt either. A rule repeated three times is a tier
problem, not a discipline problem — so it moves into the harness.

The discriminator is NOT the command string
-------------------------------------------
`run_in_background` is a Bash *tool parameter*. A foreground and a backgrounded run
have byte-identical `.tool_input.command`, so any hook keying off the command text
alone cannot tell them apart. This one reads the parameter, and `--selftest` pins
that with a byte-identical command pair.

Head-anchored on purpose
------------------------
Every pattern is anchored at the START of the command (modulo a leading env-var
prefix). The sibling `gh pr create` advisory in settings.json uses a whole-string
`grep -q`, so it fires on `grep 'gh pr create' CLAUDE.md` and on any command that
merely quotes it — i.e. on its own documentation. Do not inherit that bug: matching
anywhere in the string means this file's own docstring would trip it.

Protocol: Claude Code pipes {"tool_name","tool_input":{...}} on stdin.
Exit 0 = allow (stdout JSON is surfaced to Claude). Exit 2 would block — never used here.

Self-test:  python3 .claude/hooks/advise-foreground-run.py --selftest
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

# A leading `FOO=bar ` / `PYTHONPATH=. ` prefix is still a head position.
_ENV = r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
# `-C <dir>` must be tried before the generic short-flag branch, or it eats the
# flag and then fails on the directory.
_MAKE = r"make\s+(?:-C\s+\S+\s+|-[A-Za-z]\S*\s+)*"
_PY = r"(?:poetry\s+run\s+)?(?:python3?\s+)?"

# (regex, what it caught) — head-anchored, matched case-sensitively.
SLOW_FOREGROUND: list[tuple[str, str]] = [
    (rf"^\s*{_ENV}{_MAKE}test\b", "make test / test-cov / test-regression"),
    (rf"^\s*{_ENV}{_MAKE}wait-ci\b", "make wait-ci / wait-ci-main"),
    (rf"^\s*{_ENV}{_PY}\S*tools/wait_ci\.py\b", "tools/wait_ci.py"),
    (rf"^\s*{_ENV}(?:poetry\s+run\s+)?(?:python3?\s+-m\s+)?pytest\b", "pytest"),
]

ADVICE = (
    "Background-run reminder: `{what}` is about to run in the FOREGROUND. "
    "Re-issue it with the Bash tool's `run_in_background: true` parameter, then wait for the "
    "task notification. Rationale (standing operator rule, given 3x across both repos; "
    "CLAUDE.md > Token efficiency, and memory feedback_run_slow_tests_in_background): "
    "`make test` and `make test-regression` are always backgrounded, and CI waits never poll in "
    "the foreground. Two traps this protects: a foreground wait burns the whole turn on a job "
    "that reports nothing until it ends, and hand-rolled `pgrep` waiters match the polling "
    "shell's own argv and deadlock on themselves (CLAUDE.md's Key Commands warning). "
    "Note `.claude/settings.local.json` allowlists these targets, so a foreground run draws no "
    "permission prompt — this advisory is the only signal. "
    "Advisory only, never blocking: if you genuinely need it in the foreground, proceed."
)


def is_background(tool_input: dict) -> bool:
    """True when the caller asked for a backgrounded run.

    Absent means foreground. A string "true" is tolerated in case a client sends
    the flag unparsed — the failure direction that matters is advising on an
    already-backgrounded run, which is pure noise.
    """
    raw = tool_input.get("run_in_background")
    return raw is True or (isinstance(raw, str) and raw.strip().lower() == "true")


def match(command: str) -> str | None:
    """Return the label of the first head-anchored slow-command match, else None."""
    for pattern, what in SLOW_FOREGROUND:
        if re.search(pattern, command):
            return what
    return None


def advise(payload: dict) -> str | None:
    """Return the advisory text, or None when nothing should be surfaced."""
    if payload.get("tool_name") != "Bash":
        return None

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None

    if is_background(tool_input):
        return None  # already doing the right thing

    what = match(str(tool_input.get("command", "")))
    return ADVICE.format(what=what) if what else None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open — never break the session on a parse error

    if not isinstance(payload, dict):
        return 0

    text = advise(payload)
    if text:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": text,
                }
            },
            sys.stdout,
        )
    return 0


# --------------------------------------------------------------------------- #
# Self-test. This file is under .claude/, which .gitignore excludes, so a tracked
# test in tests/ would be red on a fresh clone. The check lives here instead, and
# it is executable rather than prose: every guard carries a POSITIVE control, and
# the two discriminators each carry a control that isolates them.
# --------------------------------------------------------------------------- #

# The byte-identical pair. This is the whole point: these two differ ONLY in a
# tool parameter, so any command-string-keyed hook must fail one of them.
_IDENTICAL = "make test 2>&1 | tee /tmp/test.log"

SHOULD_ADVISE: list[str] = [
    "make test",
    "make test-cov",
    "make test-regression",
    _IDENTICAL,
    "make wait-ci PR=231",
    "make wait-ci-main",
    "make -C /home/kng/repo/buibui-wifey-wall-street-bot test",
    "poetry run python tools/wait_ci.py --pr 231",
    "PYTHONPATH=. poetry run python tools/wait_ci.py --branch main",
    "poetry run pytest tests/test_lookahead.py",
    "PYTHONPATH=. poetry run python -m pytest tests/",
    "  make test",  # leading whitespace is still head position
]

SHOULD_STAY_QUIET: list[str] = [
    # --- documentation and search: the whole-string grep bug this must not inherit
    "grep -n 'make test' CLAUDE.md",
    "cat docs/plans/next-conversation-prompt.md",
    "echo 'always background make test and make wait-ci'",
    "sed -n '1,40p' .claude/hooks/advise-foreground-run.py",
    "git log --oneline --grep='make test'",
    # --- fast targets that must never be nagged
    "make lint-py",
    "make sanity-checks",
    "make status",
    "make lint-md",
    "git status -sb",
    "make test-plan-that-does-not-exist".replace("test-plan", "lint-plan"),
]


def _selftest() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}\n     got={got!r}\n    want={want!r}")

    for cmd in SHOULD_ADVISE:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        check(f"[positive] should advise: {cmd}", advise(payload) is not None, True)

    for cmd in SHOULD_STAY_QUIET:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        check(
            f"[negative] should stay quiet: {cmd}", advise(payload) is not None, False
        )

    # Discriminator 1 — the tool parameter, on a byte-identical command.
    fg: dict[str, Any] = {"tool_name": "Bash", "tool_input": {"command": _IDENTICAL}}
    bg: dict[str, Any] = {
        "tool_name": "Bash",
        "tool_input": {"command": _IDENTICAL, "run_in_background": True},
    }
    check(
        "[discriminator] identical cmd, foreground -> advises",
        advise(fg) is not None,
        True,
    )
    check(
        "[discriminator] identical cmd, background -> quiet",
        advise(bg) is not None,
        False,
    )
    check(
        "[discriminator] the pair really is byte-identical",
        fg["tool_input"]["command"] == bg["tool_input"]["command"],
        True,
    )

    # Discriminator 2 — head anchoring. Same slow command, moved off the head.
    head = {"tool_name": "Bash", "tool_input": {"command": "make test"}}
    tail = {"tool_name": "Bash", "tool_input": {"command": "echo hi && make test"}}
    check("[anchor] at head -> advises", advise(head) is not None, True)
    check("[anchor] mid-string -> quiet", advise(tail) is not None, False)

    # This file must not trip its own hook when read or linted.
    for cmd in (
        "python3 .claude/hooks/advise-foreground-run.py --selftest",
        "ruff check .claude/hooks/advise-foreground-run.py",
    ):
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        check(f"[self] must not fire on: {cmd}", advise(payload) is not None, False)

    # Non-Bash tools are out of scope.
    check(
        "[scope] non-Bash tool -> quiet",
        advise({"tool_name": "Read", "tool_input": {"command": "make test"}})
        is not None,
        False,
    )

    # Malformed payloads fail open (quiet), never crash.
    for bad in ({}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": None}):
        check(
            f"[robust] malformed payload -> quiet: {bad}",
            advise(bad) is not None,
            False,
        )

    total = (
        len(SHOULD_ADVISE) + len(SHOULD_STAY_QUIET) + 11
    )  # + discriminators, self, scope, robust
    if failures:
        print(f"SELFTEST FAILED — {len(failures)} of {total} checks:\n")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"SELFTEST OK — {total} checks passed")
    print(
        f"  {len(SHOULD_ADVISE)} positive, {len(SHOULD_STAY_QUIET)} negative, "
        "2 discriminator pairs (tool-param + head-anchor)"
    )
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
