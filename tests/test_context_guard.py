"""Guards for `.claude/hooks/context-guard.py` (ported, parent #659, wifey #320).

The hook is a byte-for-byte copy of the parent's; `context-map.json` beside it is
wifey's own, re-derived from CLAUDE.md's Footguns, because the parent's cards name
crypto trees (`trade/`, `card/`, `portfolio/`) that do not exist here.

The parent keeps its harness beside the hook as a hand-run script. Here it lives in
`tests/` because `testpaths = ["tests"]` would collect nothing under `.claude/hooks/`
(see `tests/test_guard_shell_hygiene.py`).

The hook dedups per (session_id, card) through a marker in the temp dir that
outlives the process, so every session id here carries a per-run nonce; fixed ids
pass once and then fail on every re-run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest

from tools.child_env import python_child_env

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "context-guard.py"
MAP = REPO_ROOT / ".claude" / "hooks" / "context-map.json"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
RUN = uuid.uuid4().hex[:8]


def _env() -> dict[str, str]:
    return python_child_env({**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)})


def _run(payload: dict[str, Any], *, hook: Path = HOOK) -> str:
    if "session_id" in payload:
        payload = {**payload, "session_id": f"{RUN}-{payload['session_id']}"}
    out = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(),
        check=False,
    )
    assert out.returncode == 0, f"hook exited {out.returncode}: {out.stderr}"
    if not out.stdout.strip():
        return ""
    return str(json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"])


def _edit(path: str, new_string: str = "x = 1", session: str = "s1") -> dict[str, Any]:
    return {
        "tool_name": "Edit",
        "session_id": session,
        "tool_input": {"file_path": str(REPO_ROOT / path), "new_string": new_string},
    }


def _sid() -> str:
    return uuid.uuid4().hex[:8]


def _cards() -> list[dict[str, Any]]:
    return list(json.loads(MAP.read_text(encoding="utf-8"))["cards"])


class TestPathTrigger:
    @pytest.mark.parametrize(
        ("path", "needle"),
        [
            ("analytics/store/_common.py", "conn.register"),
            ("analytics/db_retry.py", "is_lock_conflict"),
            ("analytics/store/backtest_runs.py", "live_parity"),
            ("analytics/signal/outcome_backfill.py", "COALESCE"),
            ("analytics/backtest/fills.py", "BOTH sides"),
            ("analytics/data_sync.py", "auto_adjust=False"),
            ("config/signal_watch.toml", "regression trigger set"),
            ("config/strategy_params.toml", "shared base"),
            ("analytics/strategies/_registry.py", "FROZEN"),
            ("analytics/audit_guard.py", "CI CONTAINMENT"),
            ("tools/x_route.py", "MONTH_YEAR_RE"),
            ("web/ui/src/App.svelte", "/frontend-design"),
            ("deploy/backup-offsite.sh", "rclone sync"),
            ("migrations/008_realign_off_monday_weekly_bars.py", "KEY"),
            (".claude/settings.json", "Windows Store stub"),
        ],
    )
    def test_a_guarded_path_delivers_its_card(self, path: str, needle: str) -> None:
        assert needle.lower() in _run(_edit(path, session=_sid())).lower()

    @pytest.mark.parametrize("path", ["README.md", "analytics/xsmom/report.py"])
    def test_an_unguarded_path_is_silent(self, path: str) -> None:
        assert _run(_edit(path, session=_sid())) == ""

    def test_a_path_outside_the_repo_never_matches_by_coincidence(
        self, tmp_path: Path
    ) -> None:
        payload = {
            "tool_name": "Write",
            "session_id": _sid(),
            "tool_input": {
                "file_path": str(tmp_path / "analytics" / "store" / "_common.py"),
                "content": "x",
            },
        }
        assert _run(payload) == ""

    def test_an_unwatched_tool_is_silent(self) -> None:
        payload = {
            "tool_name": "Read",
            "session_id": _sid(),
            "tool_input": {"file_path": str(REPO_ROOT / "analytics/store/_common.py")},
        }
        assert _run(payload) == ""


class TestClaimTrigger:
    @pytest.mark.parametrize(
        ("path", "text"),
        [
            ("docs/plans/scripts/probe.py", "if abs(delta) < mde:  # powered null"),
            ("docs/audits/2026-10-09-x.md", "All cells NO-EDGE."),
            ("docs/audits/2026-10-09-y.md", "cell is no_edge here"),
            ("docs/audits/2026-10-09-z.md", "the arms are indistinguishable"),
        ],
    )
    def test_authoring_a_negative_claim_fires(self, path: str, text: str) -> None:
        assert "negative claim detected" in _run(_edit(path, text, _sid())).lower()

    def test_write_content_is_read(self) -> None:
        payload = {
            "tool_name": "Write",
            "session_id": _sid(),
            "tool_input": {
                "file_path": str(REPO_ROOT / "docs/audits/z.md"),
                "content": "CONFIRMED-BAD across the board",
            },
        }
        assert "negative claim detected" in _run(payload).lower()

    def test_multiedit_edits_are_read(self) -> None:
        payload = {
            "tool_name": "MultiEdit",
            "session_id": _sid(),
            "tool_input": {
                "file_path": str(REPO_ROOT / "analytics/xsmom/report.py"),
                "edits": [{"new_string": "# nothing"}, {"new_string": "# ruled out"}],
            },
        }
        assert "negative claim detected" in _run(payload).lower()

    @pytest.mark.parametrize(
        ("path", "text"),
        [
            # Recollection is not a new claim: the handoff and docs prose stay quiet.
            (
                "docs/plans/next-conversation-prompt.md",
                "The NO-EDGE verdicts are closed.",
            ),
            ("CLAUDE.md", "a powered null is CI containment"),
            # No token at all.
            ("docs/plans/scripts/quiet.py", "x = compute_mde(sample)"),
            # A token inside a longer word.
            ("docs/audits/w.md", "the canoedgear was fine"),
        ],
    )
    def test_recollection_and_near_misses_stay_silent(
        self, path: str, text: str
    ) -> None:
        assert _run(_edit(path, text, _sid())) == ""


class TestDedup:
    def test_one_utterance_per_card_per_session(self) -> None:
        sid = _sid()
        first = _run(_edit("analytics/store/_common.py", session=sid))
        second = _run(_edit("analytics/store/_common.py", session=sid))
        other_card = _run(_edit("analytics/backtest/fills.py", session=sid))
        new_session = _run(_edit("analytics/store/_common.py", session=_sid()))
        assert "conn.register" in first
        assert second == ""
        assert "BOTH sides" in other_card
        assert "conn.register" in new_session


class TestFailOpen:
    @pytest.mark.parametrize("stdin", ["not json at all", "", '"just a string"'])
    def test_garbage_stdin_exits_zero_and_silent(self, stdin: str) -> None:
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_env(),
            check=False,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""


class TestMutation:
    """Breaking the mechanism must silence the card, so a pass proves the glob or
    token is what fired it rather than something incidental."""

    def _mutant(self, tmp_path: Path, data: dict[str, Any] | str) -> Path:
        shutil.copy(HOOK, tmp_path / "context-guard.py")
        text = data if isinstance(data, str) else json.dumps(data)
        (tmp_path / "context-map.json").write_text(text, encoding="utf-8")
        return tmp_path / "context-guard.py"

    def test_removing_the_glob_silences_the_card(self, tmp_path: Path) -> None:
        data = json.loads(MAP.read_text(encoding="utf-8"))
        for card in data["cards"]:
            if card["id"] == "store-upsert":
                card["globs"] = ["analytics/store/NOTHING_HERE.py"]
        hook = self._mutant(tmp_path, data)
        assert (
            _run(_edit("analytics/store/_common.py", session=_sid()), hook=hook) == ""
        )

    def test_removing_the_tokens_silences_the_claim(self, tmp_path: Path) -> None:
        data = json.loads(MAP.read_text(encoding="utf-8"))
        data["claim"]["tokens"] = ["zzz never appears zzz"]
        hook = self._mutant(tmp_path, data)
        payload = _edit("docs/audits/m.md", "every cell is NO-EDGE", _sid())
        assert _run(payload, hook=hook) == ""

    def test_a_corrupt_map_fails_open(self, tmp_path: Path) -> None:
        hook = self._mutant(tmp_path, "{ this is not json")
        assert (
            _run(_edit("analytics/store/_common.py", session=_sid()), hook=hook) == ""
        )


class TestTheMapStaysHonest:
    def test_every_glob_matches_a_tracked_file(self) -> None:
        """A glob that matches nothing is a card that can never fire, and it reads
        exactly like coverage. Catches a renamed or deleted guarded file."""
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.splitlines()
        dead = [
            f"{card['id']}: {glob}"
            for card in _cards()
            for glob in card["globs"]
            if not any(fnmatch(path, glob) for path in tracked)
        ]
        assert dead == []

    def test_card_ids_are_unique(self) -> None:
        ids = [card["id"] for card in _cards()]
        assert len(ids) == len(set(ids))

    def test_no_card_carries_a_measured_figure(self) -> None:
        """CLAUDE.md owns the figures; a second copy in a card drifts silently."""
        figure = re.compile(r"\d+\.\d+|\d+(\.\d+)?\s*%|\bn\s*=\s*\d+")
        texts = [c["text"] for c in _cards()]
        texts.append(json.loads(MAP.read_text(encoding="utf-8"))["claim"]["text"])
        assert [t[:60] for t in texts if figure.search(t)] == []

    def test_the_widest_single_edit_stays_under_the_cap(self) -> None:
        hook = HOOK.read_text(encoding="utf-8")
        cap = int(re.search(r"MAX_INJECTED_CHARS = (\d+)", hook).group(1))  # type: ignore[union-attr]
        for card in _cards():
            assert len(card["text"]) < cap, card["id"]


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
class TestTheConfiguredWrapperRunsIt:
    """Drive the wrapper string read from settings.json, not the module, because
    the wrapper is the part that has failed on this host before."""

    def test_the_edit_matcher_wrapper_delivers_a_card(self) -> None:
        cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
        wrappers = [
            h["command"]
            for entry in cfg["hooks"]["PreToolUse"]
            if entry.get("matcher") == "Edit|Write|NotebookEdit|MultiEdit"
            for h in entry.get("hooks", [])
            if "context-guard.py" in h["command"]
        ]
        assert len(wrappers) == 1
        payload = _edit("analytics/store/_common.py", session=f"{RUN}-{_sid()}")
        proc = subprocess.run(
            ["sh", "-c", wrappers[0]],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_env(),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert "conn.register" in proc.stdout
