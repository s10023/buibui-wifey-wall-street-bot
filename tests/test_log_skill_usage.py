"""`.claude/hooks/log-skill-usage.py`: what counts as an invocation, and the summary.

The end-to-end wiring (both wrappers, exit 0 on every path) is pinned in
`tests/test_hook_wiring.py::TestSkillUsageLogWiring`.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "log-skill-usage.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("log_skill_usage", HOOK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


@pytest.fixture
def log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "skill-usage.log"
    monkeypatch.setenv("WIFEY_SKILL_LOG", str(path))
    return path


def _tool(skill: str, args: str = "") -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Skill",
        "tool_input": {"skill": skill, "args": args},
    }


def _prompt(text: str) -> dict[str, Any]:
    return {"hook_event_name": "UserPromptSubmit", "prompt": text}


class TestEntry:
    def test_a_skill_tool_call_is_an_invocation(self) -> None:
        assert mod.entry(_tool("post-branch", "x")) == ("tool", "post-branch", "x")

    def test_a_typed_slash_command_is_an_invocation(self) -> None:
        assert mod.entry(_prompt("/sanity-check please")) == (
            "prompt",
            "sanity-check",
            "please",
        )

    def test_a_plugin_skill_keeps_its_namespace(self) -> None:
        assert mod.entry(_prompt("/anthropic-skills:pdf")) == (
            "prompt",
            "anthropic-skills:pdf",
            "",
        )

    @pytest.mark.parametrize(
        "payload",
        [
            _prompt("do 425 and 395?"),
            _prompt("see docs/plans/x.md"),
            _prompt("path /tmp/x is mid-prompt, not a command"),
            _tool(""),
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}},
            {},
        ],
    )
    def test_anything_else_is_not(self, payload: dict[str, Any]) -> None:
        assert mod.entry(payload) is None

    def test_args_are_flattened_and_truncated(self) -> None:
        got = mod.entry(_tool("x", "a\tb\n" + "c" * 200))
        assert got is not None
        args = got[2]
        assert "\t" not in args and "\n" not in args
        assert len(args) == mod.ARGS_MAX


class TestRecord:
    def test_appends_one_tab_separated_line(self, log: Path) -> None:
        now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
        assert mod.record(_tool("post-branch"), now=now)
        assert mod.record(_prompt("/db-update"), now=now)
        assert log.read_text(encoding="utf-8").splitlines() == [
            "2026-10-08T12:00:00Z\ttool\tpost-branch\t",
            "2026-10-08T12:00:00Z\tprompt\tdb-update\t",
        ]

    def test_a_non_invocation_writes_nothing(self, log: Path) -> None:
        assert not mod.record(_prompt("hello"))
        assert not log.exists()

    def test_main_swallows_a_failure_and_returns_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "WIFEY_SKILL_LOG", str(tmp_path)
        )  # a directory: append raises
        monkeypatch.setattr(
            "sys.stdin",
            __import__("io").StringIO(
                '{"prompt": "/x", "hook_event_name": "UserPromptSubmit"}'
            ),
        )
        assert mod.main([]) == 0


class TestSummary:
    def test_counts_and_unused_repo_skills(self, log: Path) -> None:
        skills = mod.repo_skills()
        assert "post-branch" in skills  # positive control: the glob found the tree
        for _ in range(3):
            mod.record(_tool("post-branch"))
        mod.record(_prompt("/compact"))
        out = mod.summary()
        assert "    3  post-branch" in out
        assert "    1  compact" in out
        unused = out.split("never invoked", 1)[1]
        assert "  post-branch" not in unused
        assert "  db-update" in unused
        line = mod.status_line()
        assert line.startswith("4 logged since ")
        assert f"{len(skills) - 1}/{len(skills)} repo skills unused" in line

    def test_an_absent_log_says_so(self, log: Path) -> None:
        assert mod.status_line().startswith("no log yet")
        assert "no skill-usage log" in mod.summary()
