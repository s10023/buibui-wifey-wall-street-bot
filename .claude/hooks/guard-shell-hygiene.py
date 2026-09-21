#!/usr/bin/env python3
"""PreToolUse advisory hook — shell habits that silently cost you a result.

Ported from parent #743/#744/#753 at parent HEAD (2026-09-21). Self-authored (no
third-party dependency) so every rule is reviewable here, and stdlib-only so it
runs wherever the session does. Wired via `.claude/settings.json` ->
`hooks.PreToolUse` on the `Bash` matcher, beside `guard-destructive.py` and
`advise-foreground-run.py`. **Advisory, never blocking** — every pattern here has
honest uses, and the failure this guards is a wrong BELIEF rather than a wrong
command.

Protocol: Claude Code pipes `{"tool_name","tool_input":{...},"session_id":...}` on
stdin. Exit 0 always; the note rides `hookSpecificOutput.additionalContext`.

Each rule below is here because CLAUDE.md already states it in prose and the prose
did not prevent it. That is the whole argument for a marker check over another
paragraph — the same argument `advise-foreground-run.py` was built on.

RULE 1 — a hand-rolled waiter (`until ... pgrep`).
    `run_in_background: true` already re-invokes the session when the job exits,
    so a second shell watching the first is redundancy that decays into a false
    report: `pgrep -f <name>` matches a CLASS of process, so the next run of the
    same kind re-arms a waiter that already fired while its payload still points
    at the OLD output file. ⚠ **Wifey's own instance is worse than the parent's**:
    `until ! pgrep -f 'pytest tests/'` matches the polling shell's OWN argv, so it
    waits on itself; the bracketed fix exits instantly instead, and an empty log
    then reads as "done". Measured cost here: three mutually-deadlocked waiters
    and an hour. CLAUDE.md states it, and memory `reference_env_gotchas` stated it
    before that — it was not read either time.

RULE 2 — a gate piped into a truncating reader (`make preflight | tail -8`).
    A pipeline exits with the status of its LAST command, so `tail` returns 0 and
    the gate's failure disappears. CLAUDE.md documents the neighbouring half — that
    `make` collapses every recipe failure to its own exit 2, so you must "read the
    printed banner" for `preflight` and `wait_ci.py` — but states it about `make`
    swallowing the code rather than about the pipeline the session itself writes.
    ⚠ The gate list is RE-DERIVED for this repo, not copied: wifey has no
    `daily_check.py`, and it has `cadence-check`, `backup-check`, `freshness-check`,
    `check-orphan-tests` and `check-dead-surfaces`, which the parent does not.

RULE 3 — `gh auth switch`.
    It mutates gh's GLOBAL active account. This machine's gh state is shared with
    the operator's own terminal and every other session on it, nothing switches it
    back when a turn ends, and a session cannot see whose account it just changed.
    The scoped form is one env prefix and is what CLAUDE.md's own visibility-flip
    block uses: `GH_TOKEN=$(gh auth token --user s10023) gh <cmd>`. Memory
    `feedback_gh_account_stay_s10023` is the standing rule.
    ⚠ `gh auth token` is the SANCTIONED reader and must not match this rule.

Every rule speaks ONCE PER SESSION. A hook that fires on every occurrence trains
its reader to skip it — the cost this repo names as "a leg that is never clean
trains dismissal".

⚠ **THREE of the parent's six rules are deliberately NOT ported. Do not "restore"
them without reading this.**

  - Parent RULE 4 (two `/card` in one exec) has **no subject here**: wifey has no
    `/card` skill and no `card/` package. Porting it would ship a rule that can
    never fire — the same dead-check class that `missed_ports.py` and
    `orphan_test_audit.py`'s allowlist both shipped as (PRs #301, #302).
  - Parent RULE 3 (a DUPLICATE waiter on a target something already waits on) and
    RULE 6 (editing the Python tree while a suite is LIVE) both need to enumerate
    live processes and match their command lines. The parent does that with
    `pgrep -f`. ⚠ **`pgrep` does not exist on this Windows host** — verified
    2026-09-21 against every PATH entry — so a verbatim port would have shipped
    two rules that silently never fire, which is precisely the failure both of
    this week's other PRs existed to fix. They need a Windows-capable process
    probe (`Get-CimInstance Win32_Process`) with its own tests, plus a decision
    about per-edit latency, since RULE 6 fires on every Edit/Write. Filed
    separately rather than half-ported.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

# A gate is a command whose EXIT CODE is the answer. Piping one into a truncating
# reader throws that answer away. Scoped to the gates rather than to every `make`,
# because the note has to be worth reading the first time to survive to the second.
# ⚠ RE-DERIVED against this repo's Makefile and tools/, never copied from upstream.
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

# Anchored where a command can actually START -- the fix guard-destructive.py
# already carries, after an unanchored rule there blocked its own commit message.
# Allows leading env assignments (`PYTHONUTF8=1 make ...`), which are part of the
# command and are load-bearing on this host.
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
        rf"{_GATE}[^\n|]*\|\s*{_TRUNCATOR}",
        "a gate piped into tail/head. The pipeline exits with TAIL's status, so "
        "the gate's failure is masked and a red run reads as green -- the same "
        "class CLAUDE.md documents for `make preflight` and `wait_ci.py`, where "
        "make collapses every failure to its own exit 2 and you must read the "
        "banner. Redirect instead, then read the file: "
        '`make <gate> > <log> 2>&1; echo "exit=$?"; tail -8 <log>`.',
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
    """Drop heredoc BODIES before matching -- they are data, not commands.

    Found upstream the moment the hook shipped: the commit message documenting
    these very rules contained "until ... pgrep", so `git commit -F - <<'MSG' ...`
    tripped rule 1 on its own changelog. This repo already knows the class --
    settings.json's `gh pr create` reminder anchors on `head -1` precisely so a
    grep or heredoc merely containing the string no longer self-triggers, and
    CLAUDE.md tells you to pass commit bodies with `-F` for the same reason.

    Stripping the body rather than keeping only the first line is the stronger
    form: `head -1` would also blind the hook to a waiter on line 3 of a genuine
    multi-line script, which is exactly where one tends to be written.
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
