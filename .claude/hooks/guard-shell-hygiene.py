#!/usr/bin/env python3
"""PreToolUse advisory hook — shell habits that silently produce a wrong result.

Self-authored and stdlib-only, so it runs wherever the session does. Wired via
`.claude/settings.json` -> `hooks.PreToolUse` on the `Bash` matcher, beside
`guard-destructive.py` and `advise-foreground-run.py`. Advisory, never blocking:
every pattern here has honest uses, so the risk is a wrong belief rather than a
wrong command.

Protocol: Claude Code pipes `{"tool_name","tool_input":{...},"session_id":...}` on
stdin. Exit 0 always; the note rides `hookSpecificOutput.additionalContext`. Each
rule speaks once per session (`_already_spoken`), so a repeated habit doesn't
retrain its reader to skip the note.

The gate list (RULE 2, `_GATE`) is re-derived against this repo's own Makefile and
tools/, not copied from upstream — the target names differ. It is phrased without
an "X has no Y" construction on purpose: with nothing backticked to scope on, that
form makes `negative-claims` report the line on every branch forever
(`test_no_claim_line_reports_unconditionally` catches it).

Two of the parent's rules stay out of this port, and bringing them over needs more
than copying the pattern back:

- A duplicate-waiter rule and an edit-during-live-suite rule both need to
  enumerate live processes and match command lines. The parent does that with
  `pgrep`, which does not exist on this Windows host, so a verbatim port would
  ship two rules that silently never fire. They need a Windows-capable process
  probe (`Get-CimInstance Win32_Process`) with its own tests, plus a decision on
  per-edit latency, since the live-suite rule would fire on every Edit/Write.
- A rule for two concurrent uses of a `/card` skill has no subject here: wifey has
  no `/card` skill and no `card/` package, so it would ship a rule that can never
  fire.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

# A gate's exit code is the answer; piping into a truncating reader discards it.
# Scoped to the gates below rather than every `make` invocation, since a note has
# to earn attention the first time to survive to the second.
_GATE = (
    r"(?:make\s+(?:preflight|test|test-regression|test-cov|typecheck|lint-py"
    r"|lint-py-check|lint-md|sanity-checks|post-branch-checks|post-branch-text"
    r"|check-orphan-tests|check-dead-surfaces|docs-index-check|cadence-check"
    r"|backup-check|freshness-check|wait-ci|wait-ci-main|db-update|web-check)"
    r"|(?:poetry\s+run\s+)?(?:python3?\s+\S*)?pytest"
    r"|python3?\s+\S*(?:wait_ci|sanity_checks|post_branch_checks|cadence_check"
    r"|clone_preflight|orphan_test_audit|dead_surface_check|backup_check"
    r"|freshness_check)\.py)"
)
_TRUNCATOR = r"(?:tail|head)\b"
# Commands that succeed on essentially any input. Put last in a `;`-sequence, one
# makes the shell's status its own -- the same swallow the pipe form performs:
# `; echo "exit=$?"` prints the code for a human and still hands the caller a 0.
# Kept tight on purpose: `grep` and `diff` answer the caller's own question and can
# legitimately fail, so they are not swallows (parent #779).
_ALWAYS_OK = r"(?::|true|echo|printf|tail|head|cat|ls|wc)\b"

# Anchored at a real command start, not just a substring match: unanchored, this
# could fire inside a quoted string such as a commit message that mentions the
# command (guard-destructive.py anchors the same way, for the same reason).
# Leading env assignments (`PYTHONUTF8=1 make ...`) are allowed since they're
# part of the command and load-bearing on this host.
_CMD_START = r"(?:^|[\n;&|(])\s*(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]*\s+)*"

RULES: list[tuple[str, str, str]] = [
    (
        "waiter",
        # `until`/`while` in the same command as a process probe. `sleep` is not
        # required: `until ! pgrep ...; do :; done` is the same defect unslept.
        r"\b(?:until|while)\b[^\n]*\b(?:pgrep|pidof)\b",
        "hand-rolled waiter. A background job started with run_in_background "
        "already re-invokes you when it exits -- polling it is wasted, and a "
        "`pgrep` guard names a CLASS of process, so the next run of the same kind "
        "re-arms a waiter that already fired and it reports a STALE file as the "
        "fresh result. Worse here: `pgrep -f 'pytest tests/'` matches the polling "
        "shell's OWN argv, so it waits on itself. Use the completion "
        "notification. If a hand-rolled wait is truly unavoidable, assert a "
        "POSITIVE marker this run writes (grep -q ALLDONE), never an absence.",
    ),
    (
        "piped-gate",
        # Pipe half: the gate's OWN segment piped into a truncator. It stops at
        # `;`, since a later `grep f | head` after `rc=$?` loses nothing (the
        # upstream form spanned segments and flagged `...; exit $rc` commands).
        # `;` half: the LAST segment decides the status, so it fires when that
        # segment is an always-ok command or a pipeline into tail/head, and an
        # always-ok command mid-chain is fine when the status is exited with.
        rf"{_GATE}(?:[^\n|;]*\|\s*{_TRUNCATOR}"
        rf"|[\s\S]*[;\n]\s*(?:{_ALWAYS_OK}[^\n;]*|[^\n;|]*\|\s*{_TRUNCATOR}[^\n;]*)$)",
        "a gate whose exit status is SWALLOWED. A pipeline exits with its LAST "
        "command's status and a `;`-sequence with its last segment's, so `| tail`, "
        '`; echo "exit=$?"` and `; tail -8 f` all turn a red gate green -- the '
        "same class CLAUDE.md documents for `make preflight` and `wait_ci.py`, "
        "where make collapses every failure to its own exit 2 and you must read "
        "the banner. Capture the status first and make it the LAST word: "
        "`make <gate> > <log> 2>&1; rc=$?; tail -8 <log>; exit $rc`.",
    ),
    (
        "gh-auth-switch",
        rf"{_CMD_START}gh\s+auth\s+switch\b",
        "`gh auth switch`. It mutates gh's GLOBAL active account, which this "
        "machine shares with the operator's own terminal and every other session "
        "on it, and nothing switches it back when the turn ends. Scope it to the "
        "one command instead -- the form CLAUDE.md's visibility-flip block "
        "already uses: `GH_TOKEN=$(gh auth token --user s10023) gh <cmd>`.",
    ),
]

_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$",
    re.DOTALL | re.MULTILINE,
)


def _strip_heredocs(command: str) -> str:
    """Drop heredoc bodies before matching: they are data, not commands.

    A heredoc can contain the literal text of a rule (e.g. a commit message that
    quotes "until ... pgrep") and would otherwise trip that rule on its own
    content. Stripping the whole body rather than keeping only the first line
    also catches a waiter written a few lines into a multi-line script, not just
    the first line of it.
    """
    return _HEREDOC.sub("<<STRIPPED", command)


def _already_spoken(session_id: str, rule_id: str) -> bool:
    """One utterance per rule per session; True if we have spoken already.

    Keyed per RULE rather than per command, so the second distinct habit still
    speaks while a run of the same one stays quiet.
    """
    tag = hashlib.sha256(f"{session_id}:{rule_id}".encode()).hexdigest()[:16]
    marker = Path(tempfile.gettempdir()) / f"claude-shell-hygiene-{tag}"
    if marker.exists():
        return True
    # Fail open: an un-deduped note is better than a silent one.
    with contextlib.suppress(OSError):
        marker.touch()
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open -- never break the session on a parse error

    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    command = str(tool_input.get("command", ""))
    if not command:
        return 0
    session_id = str(payload.get("session_id", "nosession"))
    command = _strip_heredocs(command)

    hits = [
        note
        for rule_id, pattern, note in RULES
        if re.search(pattern, command) and not _already_spoken(session_id, rule_id)
    ]
    if not hits:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        "shell-hygiene: "
                        + " || ".join(hits)
                        + " -- Advisory only, never blocking; said once per rule "
                        "per session. Owned by .claude/hooks/guard-shell-hygiene.py."
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
