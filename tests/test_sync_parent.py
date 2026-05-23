from __future__ import annotations

from pathlib import Path

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
