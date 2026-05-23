from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import tools.sync_parent as sp


class TestModuleContract:
    """The constants + data model other tasks depend on exist and are well-formed."""

    def test_fork_commit_constant(self) -> None:
        assert sp.FORK_COMMIT == "635ed5a"

    def test_parent_repo_path_is_path(self) -> None:
        assert isinstance(sp.PARENT_REPO_PATH, Path)
        assert sp.PARENT_REPO_PATH.name == "buibui-moon-trader-bot"

    def test_path_map_marks_removed_modules_as_none(self) -> None:
        assert sp.PARENT_TO_WIFEY_PATHS["utils/binance_client.py"] is None
        assert sp.PARENT_TO_WIFEY_PATHS["analytics/cme_gap_lib.py"] is None

    def test_path_map_renames_indicators_lib(self) -> None:
        assert (
            sp.PARENT_TO_WIFEY_PATHS["analytics/indicators_lib.py"]
            == "analytics/strategies/_registry.py"
        )

    def test_buckets_and_confidence_are_str_enums(self) -> None:
        assert sp.Bucket.SKIP.value == "SKIP"
        assert sp.Confidence.HIGH.value == "HIGH"
        assert sp.Bucket.SKIP == "SKIP"  # str subclass

    def test_pr_dataclass_fields(self) -> None:
        pr = sp.PR(number=12, title="t", commits=[], files=["a.py"])
        assert pr.number == 12
        assert pr.files == ["a.py"]

    def test_sync_state_error_is_exception(self) -> None:
        assert issubclass(sp.SyncStateError, Exception)


class TestStateFile:
    """load_sync_state + write_sync_state."""

    def test_bootstrap_when_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.md"
        assert sp.load_sync_state(missing) == sp.FORK_COMMIT

    def test_reads_frontmatter_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        f.write_text("---\nlast_synced_hash: abc1234\nupdated: 2026-05-23\n---\nbody\n")
        assert sp.load_sync_state(f) == "abc1234"

    def test_malformed_frontmatter_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        f.write_text("no frontmatter here\n")
        with pytest.raises(sp.SyncStateError, match="malformed"):
            sp.load_sync_state(f)

    def test_missing_hash_key_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        f.write_text("---\nupdated: 2026-05-23\n---\nbody\n")
        with pytest.raises(sp.SyncStateError, match="last_synced_hash"):
            sp.load_sync_state(f)

    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        sp.write_sync_state("deadbee", f, "smoke note")
        assert sp.load_sync_state(f) == "deadbee"
        assert "smoke note" in f.read_text()

    def test_write_is_atomic_no_temp_left(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        sp.write_sync_state("deadbee", f, "note")
        assert list(tmp_path.glob("*.tmp")) == []


def _load_commits_from_fixture() -> list[sp.Commit]:
    """Reconstruct Commit objects from the pipe-delimited fixture."""
    text = (
        Path(__file__).parent / "fixtures" / "sync_parent" / "sample_git_log.txt"
    ).read_text()
    commits: list[sp.Commit] = []
    for line in text.strip().splitlines():
        sha, subject, body = line.split("|", 2)
        commits.append(sp.Commit(sha=sha, subject=subject, body=body, files=[]))
    return commits


class TestPRGrouping:
    """group_into_prs handles squash, legacy merge, and direct-to-main shapes."""

    def test_squash_suffix_extracts_pr_number(self) -> None:
        commits = _load_commits_from_fixture()
        prs = sp.group_into_prs(commits)
        numbers = {pr.number for pr in prs}
        assert 403 in numbers
        assert 402 in numbers

    def test_direct_to_main_becomes_none(self) -> None:
        commits = _load_commits_from_fixture()
        prs = sp.group_into_prs(commits)
        direct = [pr for pr in prs if pr.number is None]
        assert len(direct) == 1
        assert "ruff" in direct[0].title

    def test_legacy_merge_format_parsed(self) -> None:
        commits = [
            sp.Commit("aaa", "Merge pull request #99 from foo/bar", "body", []),
        ]
        prs = sp.group_into_prs(commits)
        assert prs[0].number == 99

    def test_files_union_deduplicated(self) -> None:
        commits = [
            sp.Commit("a", "feat: x (#5)", "", ["a.py", "b.py"]),
            sp.Commit("b", "feat: y (#5)", "", ["b.py", "c.py"]),
        ]
        prs = sp.group_into_prs(commits)
        pr5 = next(pr for pr in prs if pr.number == 5)
        assert sorted(pr5.files) == ["a.py", "b.py", "c.py"]

    def test_fetch_validates_hash_then_logs(self, mocker: Any) -> None:
        run = mocker.patch("tools.sync_parent._git")
        # cat-file -e (validation) -> ""; fetch -> ""; log -> one record; show -> file
        run.side_effect = [
            "",  # cat-file -e <from>
            "",  # fetch
            "abc1234\x1ffeat: x (#5)\x1fbody\x1e",  # log
            "analytics/regime.py\n",  # show --name-only
        ]
        commits = sp.fetch_parent_commits("635ed5a", no_fetch=False)
        assert commits[0].sha == "abc1234"
        assert commits[0].files == ["analytics/regime.py"]

    def test_fetch_skips_fetch_when_no_fetch(self, mocker: Any) -> None:
        run = mocker.patch("tools.sync_parent._git")
        run.side_effect = [
            "",  # cat-file -e
            "abc1234\x1ffeat: x (#5)\x1fbody\x1e",  # log (no fetch call)
            "analytics/regime.py\n",  # show
        ]
        sp.fetch_parent_commits("635ed5a", no_fetch=True)
        # 3 calls, none of them a fetch
        assert all("fetch" not in call.args for call in run.call_args_list)
