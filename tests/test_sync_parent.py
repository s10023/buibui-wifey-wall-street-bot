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


class TestPathTranslation:
    """translate_paths maps removed / renamed / direct / unmapped correctly."""

    def test_removed_module_maps_to_none_skip(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["utils/binance_client.py"])
        assert out[0].wifey_path is None
        assert out[0].kind == "removed"

    def test_renamed_module(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["analytics/indicators_lib.py"])
        assert out[0].wifey_path == "analytics/strategies/_registry.py"
        assert out[0].kind == "renamed"

    def test_skip_glob_match(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["analytics/strategies/cvd_divergence.py"])
        assert out[0].kind == "skip"
        assert out[0].wifey_path is None

    def test_surviving_same_name_is_direct(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=True)
        out = sp.translate_paths(["analytics/regime.py"])
        assert out[0].wifey_path == "analytics/regime.py"
        assert out[0].kind == "direct"

    def test_absent_everywhere_is_unmapped(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["analytics/some_new_parent_module.py"])
        assert out[0].kind == "unmapped"
        assert out[0].wifey_path is None


class TestBucketClassifier:
    """classify_pr returns SKIP / PORT / EVALUATE per the rules."""

    def _wp(self, path: str, kind: str, target: str | None) -> Any:
        return sp.WifeyPath(path, target, kind)  # type: ignore[arg-type]

    def test_all_removed_is_skip(self) -> None:
        pr = sp.PR(number=1, title="fix: binance (#1)", commits=[], files=["utils/binance_client.py"])
        wps = [self._wp("utils/binance_client.py", "removed", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.SKIP

    def test_all_skip_glob_is_skip(self) -> None:
        pr = sp.PR(number=2, title="fix: cvd (#2)", commits=[], files=["analytics/strategies/cvd_divergence.py"])
        wps = [self._wp("analytics/strategies/cvd_divergence.py", "skip", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.SKIP

    def test_surviving_module_is_port(self) -> None:
        pr = sp.PR(number=3, title="fix: regime (#3)", commits=[], files=["analytics/regime.py"])
        wps = [self._wp("analytics/regime.py", "direct", "analytics/regime.py")]
        assert sp.classify_pr(pr, wps) == sp.Bucket.PORT

    def test_unmapped_path_is_evaluate(self) -> None:
        pr = sp.PR(number=4, title="feat: new (#4)", commits=[], files=["analytics/new_mod.py"])
        wps = [self._wp("analytics/new_mod.py", "unmapped", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_toml_config_is_evaluate(self) -> None:
        pr = sp.PR(number=5, title="feat: tp_r (#5)", commits=[], files=["config/signal_watch.toml"])
        wps = [self._wp("config/signal_watch.toml", "direct", "config/signal_watch.toml")]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_new_strategy_file_is_evaluate(self) -> None:
        pr = sp.PR(number=6, title="feat: strat (#6)", commits=[], files=["analytics/strategies/new_thing.py"])
        wps = [self._wp("analytics/strategies/new_thing.py", "unmapped", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_mixed_survivors_and_skip_is_port(self) -> None:
        """A bugfix touching a survivor + an incidental removed test stays PORT."""
        pr = sp.PR(number=7, title="fix (#7)", commits=[], files=["analytics/regime.py", "utils/binance_client.py"])
        wps = [
            self._wp("analytics/regime.py", "direct", "analytics/regime.py"),
            self._wp("utils/binance_client.py", "removed", None),
        ]
        assert sp.classify_pr(pr, wps) == sp.Bucket.PORT

    def test_no_files_is_evaluate(self) -> None:
        pr = sp.PR(number=8, title="empty (#8)", commits=[], files=[])
        assert sp.classify_pr(pr, []) == sp.Bucket.EVALUATE


class TestAlreadyApplied:
    """extract_added_symbols + detect_already_applied."""

    def _diff(self) -> str:
        return (
            Path(__file__).parent / "fixtures" / "sync_parent" / "sample_diff.txt"
        ).read_text()

    def test_extract_function_and_constant(self) -> None:
        syms = sp.extract_added_symbols(self._diff())
        assert "classify_regime_v2" in syms
        assert "ADX_TREND_THRESHOLD" in syms

    def test_extract_ignores_removed_and_context(self) -> None:
        syms = sp.extract_added_symbols(self._diff())
        assert "old_line_removed" not in syms  # removed line, not added
        assert "pd" not in syms                 # context import, not added def/const

    def test_confidence_high_all_match(self) -> None:
        c = sp.detect_already_applied(["foo", "bar"], grep=lambda s: True)
        assert c == sp.Confidence.HIGH

    def test_confidence_low_none_match(self) -> None:
        c = sp.detect_already_applied(["foo", "bar"], grep=lambda s: False)
        assert c == sp.Confidence.LOW

    def test_confidence_medium_partial(self) -> None:
        c = sp.detect_already_applied(["foo", "bar"], grep=lambda s: s == "foo")
        assert c == sp.Confidence.MEDIUM

    def test_confidence_unknown_no_symbols(self) -> None:
        c = sp.detect_already_applied([], grep=lambda s: True)
        assert c == sp.Confidence.UNKNOWN

    def test_grep_exception_treated_as_no_match(self) -> None:
        def boom(_s: str) -> bool:
            raise RuntimeError("git grep blew up")

        c = sp.detect_already_applied(["foo"], grep=boom)
        assert c == sp.Confidence.LOW


class TestMemoryExtract:
    """extract_memory_entry finds the paragraph referencing a PR number."""

    def _memory(self) -> str:
        return (
            Path(__file__).parent / "fixtures" / "sync_parent" / "sample_memory.md"
        ).read_text()

    def test_finds_referenced_pr(self) -> None:
        excerpt = sp.extract_memory_entry(403, self._memory())
        assert excerpt is not None
        assert "Regime gate thresholds" in excerpt
        assert "+0.12R uplift" in excerpt

    def test_returns_none_when_absent(self) -> None:
        assert sp.extract_memory_entry(999, self._memory()) is None

    def test_does_not_bleed_into_neighbour(self) -> None:
        excerpt = sp.extract_memory_entry(402, self._memory())
        assert excerpt is not None
        assert "Cooldown watermark" in excerpt
        assert "Regime gate" not in excerpt


class TestSuggestApproach:
    """suggest_approach returns one of the three labels."""

    def _wp(self, kind: str) -> Any:
        return sp.WifeyPath("p", "p", kind)  # type: ignore[arg-type]

    def test_high_confidence_is_verify_only(self) -> None:
        out = sp.suggest_approach(sp.Bucket.PORT, sp.Confidence.HIGH, [self._wp("direct")])
        assert out == "verify-only"

    def test_all_surviving_paths_is_cherry_pick(self) -> None:
        out = sp.suggest_approach(
            sp.Bucket.PORT, sp.Confidence.LOW, [self._wp("direct"), self._wp("renamed")]
        )
        assert out == "cherry-pick-with-edits"

    def test_unmapped_path_is_reimplement(self) -> None:
        out = sp.suggest_approach(
            sp.Bucket.EVALUATE, sp.Confidence.LOW, [self._wp("direct"), self._wp("unmapped")]
        )
        assert out == "re-implement"

    def test_evaluate_bucket_is_reimplement_even_if_paths_direct(self) -> None:
        out = sp.suggest_approach(sp.Bucket.EVALUATE, sp.Confidence.LOW, [self._wp("direct")])
        assert out == "re-implement"

    def test_no_paths_is_reimplement(self) -> None:
        out = sp.suggest_approach(sp.Bucket.EVALUATE, sp.Confidence.LOW, [])
        assert out == "re-implement"
