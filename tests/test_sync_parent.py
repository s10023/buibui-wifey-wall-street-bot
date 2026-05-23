from __future__ import annotations

from pathlib import Path

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
