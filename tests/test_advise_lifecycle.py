"""`.claude/hooks/advise-lifecycle.py`: which events speak, and which stay silent."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.child_env import python_child_env

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "advise-lifecycle.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("advise_lifecycle", HOOK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def _bash(event: str, command: str) -> dict[str, Any]:
    return {"hook_event_name": event, "tool_input": {"command": command}}


class TestPrCreate:
    @pytest.mark.parametrize(
        "command",
        [
            "gh pr create --repo s10023/x --body-file f",
            "GH_TOKEN=$(gh auth token --user s10023) gh pr create --repo x",
            "git push && gh pr create --fill",
        ],
    )
    def test_fires_at_a_head_position(self, command: str) -> None:
        assert mod.advise(_bash("PreToolUse", command)) == mod.POST_BRANCH

    @pytest.mark.parametrize(
        "command",
        [
            "grep 'gh pr create' CLAUDE.md",
            "echo gh pr create",
            "gh pr view 5",
            'grep -nE "^#|jq|gh pr create" hooks.md',
        ],
    )
    def test_a_command_that_only_QUOTES_it_is_silent(self, command: str) -> None:
        assert mod.advise(_bash("PreToolUse", command)) is None

    def test_it_is_a_PRE_tool_advisory_only(self) -> None:
        assert mod.advise(_bash("PostToolUse", "gh pr create")) is None

    def test_a_backslash_continued_chain_is_one_logical_line(self) -> None:
        cmd = "make post-branch-text FILE=b.md \\\n  && gh pr create --body x"
        assert mod.advise(_bash("PreToolUse", cmd)) == mod.POST_BRANCH


class TestPostBranchAlreadyRan:
    """#381: the reminder is silent once the body file proves /post-branch ran."""

    BODY = "## Summary\n\n- x\n\n## Documentation updates\n\n- `CLAUDE.md`: no change\n"

    def _pre(self, command: str, cwd: Path) -> str | None:
        payload = _bash("PreToolUse", command)
        payload["cwd"] = str(cwd)
        result: str | None = mod.advise(payload)
        return result

    @pytest.mark.parametrize(
        "flag",
        ["--body-file body.md", "-F body.md", "--body-file=body.md", "-F 'body.md'"],
    )
    def test_silent_when_the_body_file_carries_the_section(
        self, tmp_path: Path, flag: str
    ) -> None:
        (tmp_path / "body.md").write_text(self.BODY, encoding="utf-8")
        cmd = f"GH_TOKEN=$(gh auth token --user s10023) gh pr create --repo x {flag}"
        assert self._pre(cmd, tmp_path) is None

    def test_silent_for_the_phase_5_chain(self, tmp_path: Path) -> None:
        (tmp_path / "body.md").write_text(self.BODY, encoding="utf-8")
        cmd = (
            "make post-branch-text FILE=body.md \\\n"
            '  && gh pr create --title "$T" --body-file body.md'
        )
        assert self._pre(cmd, tmp_path) is None

    def test_an_absolute_body_path_ignores_cwd(self, tmp_path: Path) -> None:
        body = tmp_path / "body.md"
        body.write_text(self.BODY, encoding="utf-8")
        cmd = f"gh pr create --body-file {body}"
        assert self._pre(cmd, tmp_path / "elsewhere") is None

    def test_fires_on_an_inline_body(self, tmp_path: Path) -> None:
        assert self._pre('gh pr create --body "x"', tmp_path) == mod.POST_BRANCH

    def test_fires_when_the_section_is_absent(self, tmp_path: Path) -> None:
        (tmp_path / "body.md").write_text("## Summary\n\n- x\n", encoding="utf-8")
        cmd = "gh pr create --body-file body.md"
        assert self._pre(cmd, tmp_path) == mod.POST_BRANCH

    def test_a_mention_in_prose_is_not_the_heading(self, tmp_path: Path) -> None:
        text = "Run /post-branch and fold its ## Documentation updates in.\n"
        (tmp_path / "body.md").write_text(text, encoding="utf-8")
        cmd = "gh pr create --body-file body.md"
        assert self._pre(cmd, tmp_path) == mod.POST_BRANCH

    @pytest.mark.parametrize("flag", ["--body-file missing.md", "--body-file -"])
    def test_fires_on_an_unreadable_or_stdin_body(
        self, tmp_path: Path, flag: str
    ) -> None:
        assert self._pre(f"gh pr create {flag}", tmp_path) == mod.POST_BRANCH

    def test_a_chained_commit_message_cannot_vouch_for_the_body(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "msg.md").write_text(self.BODY, encoding="utf-8")
        cmd = "git commit -F msg.md && gh pr create --body x"
        assert self._pre(cmd, tmp_path) == mod.POST_BRANCH


class TestPrMerge:
    def test_fires_after_merge_with_the_token_prefix(self) -> None:
        cmd = "GH_TOKEN=$(gh auth token --user s10023) gh pr merge 5 --squash"
        assert mod.advise(_bash("PostToolUse", cmd)) == mod.POST_MERGE

    def test_silent_before_the_merge(self) -> None:
        assert mod.advise(_bash("PreToolUse", "gh pr merge 5")) is None


class TestCloseOut:
    @pytest.mark.parametrize(
        "prompt",
        ["ok deleting sesh", "Deleting session now", "going to delete this sesh"],
    )
    def test_fires_on_the_close_out_phrase(self, prompt: str) -> None:
        payload = {"hook_event_name": "UserPromptSubmit", "prompt": prompt}
        assert mod.advise(payload) == mod.CLOSE_OUT_ADVICE

    def test_an_ordinary_prompt_is_silent(self) -> None:
        payload = {"hook_event_name": "UserPromptSubmit", "prompt": "whats next"}
        assert mod.advise(payload) is None


class TestProtocol:
    def _run(self, stdin: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=python_child_env(),
            timeout=30,
            check=False,
        )

    def test_emits_json_carrying_the_event_name(self) -> None:
        proc = self._run(json.dumps(_bash("PreToolUse", "gh pr create")))
        out = json.loads(proc.stdout)
        assert proc.returncode == 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_garbage_stdin_fails_open(self) -> None:
        proc = self._run("not json")
        assert (proc.returncode, proc.stdout) == (0, "")
