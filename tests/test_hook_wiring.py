"""End-to-end guards that the PreToolUse hooks actually RUN as configured.

⚠ **This exists because they did not.** Measured 2026-09-21 on the Windows host:
every hook in `.claude/settings.json` launched with `exec python3 "$f"`, and on
this box `python3` resolves to the Windows Store App Execution Alias, a reparse
point that returns **Permission denied / exit 126** from Git Bash. Only exit 2
blocks, so `guard-destructive.py` -- the guard for `rm -rf`, `git reset --hard`,
force-push and DB wipes -- **failed open on every command**. CLAUDE.md anticipated
fail-open for a MISSING hook file; a broken interpreter fails open identically.

⚠ **The stub is UNDETECTABLE by test operator**: `[ -f ]` and `[ -x ]` both report
true for it, and even `wc -c` on it is denied. So the fix is candidate ORDERING --
prefer this repo's own venv, the interpreter everything else already runs on --
rather than trying to detect brokenness.

These tests drive the wrapper string **read from settings.json**, not a copy, so
they fail if the wiring regresses. Testing the hook module directly would pass
either way -- the module was never the broken part.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None, reason="needs a POSIX shell to run the wrapper"
)


def _bash_hook_commands() -> list[str]:
    cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return [
        h["command"]
        for entry in cfg["hooks"]["PreToolUse"]
        if entry.get("matcher") == "Bash"
        for h in entry.get("hooks", [])
    ]


def _drive(wrapper: str, command: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "test-hook-wiring",
        }
    )
    return subprocess.run(
        ["sh", "-c", wrapper],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        env={"CLAUDE_PROJECT_DIR": str(REPO_ROOT), "PATH": _path(), "PYTHONUTF8": "1"},
        timeout=30,
    )


def _path() -> str:
    import os

    return os.environ.get("PATH", "")


def _wrapper_for(script: str) -> str:
    matches = [c for c in _bash_hook_commands() if script in c]
    assert len(matches) == 1, (
        f"expected exactly one wrapper for {script}, got {matches}"
    )
    return matches[0]


class TestEveryConfiguredHookCanActuallyRun:
    """126/127 mean the interpreter never started -- the failure this pins."""

    @pytest.mark.parametrize(
        "script",
        [
            "guard-destructive.py",
            "advise-foreground-run.py",
            "guard-shell-hygiene.py",
            "advise-lifecycle.py",
        ],
    )
    def test_the_interpreter_starts(self, script: str) -> None:
        proc = _drive(_wrapper_for(script), "echo hello")
        assert proc.returncode not in (126, 127), (
            f"{script} wrapper could not launch an interpreter "
            f"(exit {proc.returncode}): {proc.stderr.strip()}"
        )

    @pytest.mark.parametrize(
        "script",
        [
            "guard-destructive.py",
            "advise-foreground-run.py",
            "guard-shell-hygiene.py",
            "advise-lifecycle.py",
        ],
    )
    def test_the_hook_file_exists(self, script: str) -> None:
        assert (REPO_ROOT / ".claude" / "hooks" / script).is_file()


class TestTheDestructiveGuardActuallyGuards:
    """The decisive control: end to end, through the configured wrapper.

    Pre-fix this returned 126 for every input, so the guard blocked nothing.
    """

    def test_rm_rf_is_blocked_with_exit_2(self) -> None:
        proc = _drive(_wrapper_for("guard-destructive.py"), "rm -rf /")
        assert proc.returncode == 2, (
            "guard-destructive must BLOCK with exit 2 through its real wrapper; "
            f"got {proc.returncode}. Only exit 2 blocks -- anything else fails OPEN."
        )
        assert "BLOCKED" in (proc.stdout + proc.stderr)

    def test_a_benign_command_is_not_blocked(self) -> None:
        """Positive control on the other side: the guard must not block everything.

        Without this, a wrapper that exits 2 unconditionally would pass the test
        above while breaking every command in the session.
        """
        proc = _drive(_wrapper_for("guard-destructive.py"), "ls -la")
        assert proc.returncode == 0, (
            f"a benign command must pass (exit 0), got {proc.returncode}"
        )


class TestTheInterpreterOrderIsLoadBearing:
    """⚠ Do not 'simplify' these wrappers back to a bare `python3`."""

    @pytest.mark.parametrize(
        "script",
        [
            "guard-destructive.py",
            "advise-foreground-run.py",
            "guard-shell-hygiene.py",
            "advise-lifecycle.py",
        ],
    )
    def test_the_venv_is_tried_before_python3(self, script: str) -> None:
        w = _wrapper_for(script)
        venv = w.find(".venv")
        bare = w.find("python3")
        assert venv != -1, f"{script} wrapper must try this repo's venv first"
        assert venv < bare, (
            f"{script} wrapper reaches python3 before the venv. On Windows that "
            "resolves to an App Execution Alias stub which exits 126, and the "
            "hook fails OPEN -- the state this test exists to prevent."
        )

    @pytest.mark.parametrize(
        "script",
        [
            "guard-destructive.py",
            "advise-foreground-run.py",
            "guard-shell-hygiene.py",
            "advise-lifecycle.py",
        ],
    )
    def test_a_missing_hook_file_still_fails_open(self, script: str) -> None:
        """A missing file must exit 0, never 2 -- CLAUDE.md's standing rule."""
        w = _wrapper_for(script)
        proc = subprocess.run(
            ["sh", "-c", w],
            input="{}",
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=REPO_ROOT,
            env={"CLAUDE_PROJECT_DIR": "/nonexistent-dir", "PATH": _path()},
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"{script}: a missing hook file must fail OPEN (exit 0), "
            f"got {proc.returncode}"
        )


def _event_hook_commands(event: str) -> list[str]:
    cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return [h["command"] for e in cfg["hooks"][event] for h in e.get("hooks", [])]


class TestNoHookDependsOnJq:
    """This Windows host has no `jq`. The inline `jq | grep` post-branch reminder
    failed there, `|| true` swallowed it, and it never fired: the same fail-open
    shape as the interpreter stub above. Every hook goes through a Python file."""

    def test_no_configured_command_calls_jq(self) -> None:
        assert "jq " not in SETTINGS.read_text(encoding="utf-8")


class TestSessionStartDigestWiring:
    """The digest's wrapper must never fail a session start: exit 0 always."""

    def test_the_wrapper_names_the_digest_and_tries_the_venv_first(self) -> None:
        (cmd,) = _event_hook_commands("SessionStart")
        assert "tools/session_digest.py" in cmd
        assert cmd.find(".venv") < cmd.find("python3")

    def test_a_missing_digest_fails_open(self) -> None:
        (cmd,) = _event_hook_commands("SessionStart")
        proc = subprocess.run(
            ["sh", "-c", cmd],
            input="{}",
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=REPO_ROOT,
            env={"CLAUDE_PROJECT_DIR": "/nonexistent-dir", "PATH": _path()},
            timeout=30,
        )
        assert proc.returncode == 0

    @pytest.mark.parametrize("event", ["UserPromptSubmit", "PostToolUse"])
    def test_lifecycle_events_route_through_the_python_hook(self, event: str) -> None:
        assert all("advise-lifecycle.py" in c for c in _event_hook_commands(event))
