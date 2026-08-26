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

    def test_parent_agents_md_names_wifey_claude_md(self, mocker: Any) -> None:
        """The parent's instruction file resolves to wifey's, not to "unmapped"."""
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["AGENTS.md"])
        assert out[0].kind == "renamed"
        assert out[0].wifey_path == "CLAUDE.md"


class TestBucketClassifier:
    """classify_pr returns SKIP / PORT / EVALUATE per the rules."""

    def _wp(self, path: str, kind: str, target: str | None) -> Any:
        return sp.WifeyPath(path, target, kind)  # type: ignore[arg-type]

    def test_all_removed_is_skip(self) -> None:
        pr = sp.PR(
            number=1,
            title="fix: binance (#1)",
            commits=[],
            files=["utils/binance_client.py"],
        )
        wps = [self._wp("utils/binance_client.py", "removed", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.SKIP

    def test_all_skip_glob_is_skip(self) -> None:
        pr = sp.PR(
            number=2,
            title="fix: cvd (#2)",
            commits=[],
            files=["analytics/strategies/cvd_divergence.py"],
        )
        wps = [self._wp("analytics/strategies/cvd_divergence.py", "skip", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.SKIP

    def test_surviving_module_is_port(self) -> None:
        pr = sp.PR(
            number=3,
            title="fix: regime (#3)",
            commits=[],
            files=["analytics/regime.py"],
        )
        wps = [self._wp("analytics/regime.py", "direct", "analytics/regime.py")]
        assert sp.classify_pr(pr, wps) == sp.Bucket.PORT

    def test_unmapped_path_is_evaluate(self) -> None:
        pr = sp.PR(
            number=4, title="feat: new (#4)", commits=[], files=["analytics/new_mod.py"]
        )
        wps = [self._wp("analytics/new_mod.py", "unmapped", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_toml_config_is_evaluate(self) -> None:
        pr = sp.PR(
            number=5,
            title="feat: tp_r (#5)",
            commits=[],
            files=["config/signal_watch.toml"],
        )
        wps = [
            self._wp("config/signal_watch.toml", "direct", "config/signal_watch.toml")
        ]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_new_strategy_file_is_evaluate(self) -> None:
        pr = sp.PR(
            number=6,
            title="feat: strat (#6)",
            commits=[],
            files=["analytics/strategies/new_thing.py"],
        )
        wps = [self._wp("analytics/strategies/new_thing.py", "unmapped", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_mixed_survivors_and_skip_is_port(self) -> None:
        """A bugfix touching a survivor + an incidental removed test stays PORT."""
        pr = sp.PR(
            number=7,
            title="fix (#7)",
            commits=[],
            files=["analytics/regime.py", "utils/binance_client.py"],
        )
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
        assert "pd" not in syms  # context import, not added def/const

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


class TestModifiedSymbolIsNotEvidence:
    """The #521 miss: a signature-only change re-emits its own ``def`` line.

    Parent #521 added two suppressor kwargs to an existing ``route_target``. The
    resolver read ``+def route_target(`` as an addition, grepped the bare name,
    matched wifey's *old* two-arg copy and returned HIGH — scoring a missing port
    as ALREADY-APPLIED. Reproduced against the real commit (148496e) before the
    fix; these tests pin the fix in both directions.
    """

    def _diff(self) -> str:
        return (
            Path(__file__).parent
            / "fixtures"
            / "sync_parent"
            / "modified_symbol_diff.txt"
        ).read_text()

    def test_modified_symbol_is_not_counted_as_added(self) -> None:
        ch = sp.extract_symbol_changes(self._diff())
        assert ch.added == []
        assert "route_target" in ch.modified

    def test_new_kwargs_are_captured_as_new_identifiers(self) -> None:
        ch = sp.extract_symbol_changes(self._diff())
        assert "retrospective" in ch.new_identifiers
        assert "rejected" in ch.new_identifiers

    def test_unchanged_symbol_name_is_not_new_evidence(self) -> None:
        # The bare name sits on BOTH sides, so it must never be admitted as the
        # thing that proves the port landed.
        ch = sp.extract_symbol_changes(self._diff())
        assert "route_target" not in ch.new_identifiers

    def test_name_present_but_payload_missing_is_not_high(self) -> None:
        """The exact #521 world: wifey HAS route_target, lacks the kwargs."""
        ch = sp.extract_symbol_changes(self._diff())
        c = sp.resolve_confidence(ch, grep=lambda s: s == "route_target")
        assert c == sp.Confidence.LOW

    def test_payload_present_is_high(self) -> None:
        """Positive control — a genuinely applied port must still read HIGH."""
        ch = sp.extract_symbol_changes(self._diff())
        c = sp.resolve_confidence(ch, grep=lambda _s: True)
        assert c == sp.Confidence.HIGH

    def test_added_symbol_still_uses_its_name_as_evidence(self) -> None:
        """A greenfield addition is unaffected by the fix."""
        added_diff = (
            Path(__file__).parent / "fixtures" / "sync_parent" / "sample_diff.txt"
        ).read_text()
        ch = sp.extract_symbol_changes(added_diff)
        assert "classify_regime_v2" in ch.added
        assert ch.modified == []
        assert sp.resolve_confidence(ch, grep=lambda _s: True) == sp.Confidence.HIGH

    def test_modify_only_with_no_new_identifier_is_unknown(self) -> None:
        """Cannot tell != applied. Reports UNKNOWN rather than guessing."""
        ch = sp.SymbolChanges(added=[], modified=["foo"], new_identifiers=[])
        assert sp.resolve_confidence(ch, grep=lambda _s: True) == sp.Confidence.UNKNOWN

    def test_commit_message_prose_is_not_read_as_context(self) -> None:
        """`git show` without `--format=` prepends the message; it must be ignored.

        Caught while validating against the real commit: the message described
        the new kwargs, so parsing its lines as context subtracted `retrospective`
        and `rejected` from the evidence — leaving the resolver to discriminate on
        docstring words instead of the payload. Right verdict, wrong reason.
        """
        body = self._diff()
        with_message = (
            "commit 148496e\n"
            "Author: someone <s@example.com>\n\n"
            "    fix(ingest): stop scoring pundits on declined calls (#521)\n\n"
            "    route_target now takes retrospective= / rejected= keyword-only.\n\n"
        ) + body
        assert sp.extract_symbol_changes(with_message) == sp.extract_symbol_changes(
            body
        )
        ch = sp.extract_symbol_changes(with_message)
        assert "retrospective" in ch.new_identifiers
        assert "rejected" in ch.new_identifiers


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
        out = sp.suggest_approach(
            sp.Bucket.PORT, sp.Confidence.HIGH, [self._wp("direct")]
        )
        assert out == "verify-only"

    def test_all_surviving_paths_is_cherry_pick(self) -> None:
        out = sp.suggest_approach(
            sp.Bucket.PORT, sp.Confidence.LOW, [self._wp("direct"), self._wp("renamed")]
        )
        assert out == "cherry-pick-with-edits"

    def test_unmapped_path_is_reimplement(self) -> None:
        out = sp.suggest_approach(
            sp.Bucket.EVALUATE,
            sp.Confidence.LOW,
            [self._wp("direct"), self._wp("unmapped")],
        )
        assert out == "re-implement"

    def test_evaluate_bucket_is_reimplement_even_if_paths_direct(self) -> None:
        out = sp.suggest_approach(
            sp.Bucket.EVALUATE, sp.Confidence.LOW, [self._wp("direct")]
        )
        assert out == "re-implement"

    def test_no_paths_is_reimplement(self) -> None:
        out = sp.suggest_approach(sp.Bucket.EVALUATE, sp.Confidence.LOW, [])
        assert out == "re-implement"


class TestReportFormat:
    """format_report emits the header, summary table, and four sections."""

    def _report(self, bucket: Any, confidence: Any, number: int) -> Any:
        pr = sp.PR(
            number=number,
            title=f"feat: thing (#{number})",
            commits=[],
            files=["analytics/regime.py"],
        )
        return sp.PRReport(
            pr=pr,
            bucket=bucket,
            confidence=confidence,
            wifey_paths=[
                sp.WifeyPath("analytics/regime.py", "analytics/regime.py", "direct")
            ],
            memory_excerpt="Some why.",
            approach="cherry-pick-with-edits",
        )

    def test_header_and_range(self) -> None:
        out = sp.format_report(
            [self._report(sp.Bucket.PORT, sp.Confidence.LOW, 1)], "635ed5a", "abcdef0"
        )
        assert "# Parent sync report" in out
        assert "635ed5a..abcdef0" in out
        assert "--bump-to abcdef0" in out

    def test_summary_counts(self) -> None:
        reports = [
            self._report(sp.Bucket.SKIP, sp.Confidence.LOW, 1),
            self._report(sp.Bucket.PORT, sp.Confidence.LOW, 2),
            self._report(sp.Bucket.EVALUATE, sp.Confidence.LOW, 3),
            self._report(
                sp.Bucket.PORT, sp.Confidence.HIGH, 4
            ),  # routed to ALREADY-APPLIED
        ]
        out = sp.format_report(reports, "a", "b")
        assert "| SKIP" in out and "| 1 |" in out
        assert "## ALREADY-APPLIED" in out

    def test_high_confidence_port_routes_to_already_applied(self) -> None:
        out = sp.format_report(
            [self._report(sp.Bucket.PORT, sp.Confidence.HIGH, 7)], "a", "b"
        )
        already = out.split("## ALREADY-APPLIED", 1)[1]
        assert "#7" in already

    def test_port_detail_block_has_memory_and_approach(self) -> None:
        out = sp.format_report(
            [self._report(sp.Bucket.PORT, sp.Confidence.LOW, 9)], "a", "b"
        )
        assert "Suggested approach" in out
        assert "cherry-pick-with-edits" in out
        assert "Some why." in out
        assert "https://github.com/s10023/buibui-moon-trader-bot/pull/9" in out


class TestCLI:
    """Argparse surface + bump-to guard."""

    def test_parses_all_flags(self) -> None:
        ns = sp.build_arg_parser().parse_args(["--from", "abc", "--no-fetch"])
        assert ns.from_hash == "abc"
        assert ns.no_fetch is True

    def test_full_and_bump_flags(self) -> None:
        ns = sp.build_arg_parser().parse_args(["--full"])
        assert ns.full is True
        ns2 = sp.build_arg_parser().parse_args(["--bump-to", "deadbee"])
        assert ns2.bump_to == "deadbee"


class TestSmokeRun:
    """run_pipeline produces a well-formed report from fixture PRs."""

    def test_pipeline_buckets_and_sections(self, mocker: Any) -> None:
        # Two PRs: one survivor bugfix (PORT), one binance fix (SKIP).
        prs = [
            sp.PR(
                number=403,
                title="fix: regime (#403)",
                commits=[],
                files=["analytics/regime.py"],
            ),
            sp.PR(
                number=402,
                title="fix: binance (#402)",
                commits=[],
                files=["utils/binance_client.py"],
            ),
        ]
        memory_text = (
            Path(__file__).parent / "fixtures" / "sync_parent" / "sample_memory.md"
        ).read_text()
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=True)
        mocker.patch(
            "tools.sync_parent.extract_added_symbols", return_value=[]
        )  # -> UNKNOWN
        reports = sp.run_pipeline(prs, memory_text)
        out = sp.format_report(reports, "635ed5a", "abcdef0")
        assert "# Parent sync report" in out
        assert "## SKIP" in out and "## PORT" in out
        assert "#403" in out and "#402" in out

    def test_main_writes_report_file(self, mocker: Any, tmp_path: Path) -> None:
        mocker.patch("tools.sync_parent.PARENT_REPO_PATH", tmp_path)  # exists
        mocker.patch("tools.sync_parent._hash_exists_in_parent", return_value=True)
        mocker.patch(
            "tools.sync_parent.fetch_parent_commits",
            return_value=[
                sp.Commit("abc", "fix: regime (#403)", "", ["analytics/regime.py"])
            ],
        )
        mocker.patch("tools.sync_parent._parent_head_hash", return_value="abcdef0")
        mocker.patch("tools.sync_parent._read_parent_memory", return_value="(empty)")
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=True)
        mocker.patch("tools.sync_parent.extract_added_symbols", return_value=[])
        report_path = tmp_path / "out.md"
        mocker.patch("tools.sync_parent._report_path", return_value=report_path)
        code = sp.main(["--from", "635ed5a", "--no-fetch"])
        assert code == 0
        assert report_path.exists()
        assert "Parent sync report" in report_path.read_text()

    def test_main_creates_report_dir_when_missing(
        self, mocker: Any, tmp_path: Path
    ) -> None:
        """A checkout with no report dir yet still gets its report written.

        ``_report_path`` is deliberately NOT patched here — the sibling test above
        patches it and so never exercises the directory, which is the whole risk of
        writing into the repo instead of a directory /tmp guarantees exists.
        """
        mocker.patch("tools.sync_parent.PARENT_REPO_PATH", tmp_path)
        mocker.patch("tools.sync_parent._hash_exists_in_parent", return_value=True)
        mocker.patch(
            "tools.sync_parent.fetch_parent_commits",
            return_value=[
                sp.Commit("abc", "fix: regime (#403)", "", ["analytics/regime.py"])
            ],
        )
        mocker.patch("tools.sync_parent._parent_head_hash", return_value="abcdef0")
        mocker.patch("tools.sync_parent._read_parent_memory", return_value="(empty)")
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=True)
        mocker.patch("tools.sync_parent.extract_added_symbols", return_value=[])
        report_dir = tmp_path / "docs" / "plans" / "parent-sync"
        mocker.patch("tools.sync_parent.REPORT_DIR", report_dir)
        assert not report_dir.exists()

        code = sp.main(["--from", "635ed5a", "--no-fetch"])

        assert code == 0
        written = list(report_dir.glob("parent-sync-*.md"))
        assert len(written) == 1
        assert "Parent sync report" in written[0].read_text()

    def test_report_dir_is_inside_the_repo(self) -> None:
        """The report must outlive a reboot: a /tmp clear lost a 67-PR triage once."""
        assert sp.REPORT_DIR.is_relative_to(sp.WIFEY_REPO_PATH)

    def test_main_bump_to_unknown_hash_exits_1(
        self, mocker: Any, tmp_path: Path
    ) -> None:
        mocker.patch("tools.sync_parent.PARENT_REPO_PATH", tmp_path)
        # origin/main resolves and ONLY the bump target is unknown, so exit 1 here
        # can only come from the bump check. Patching _hash_exists_in_parent flat to
        # False would also trip the origin/main precondition and pass either way.
        mocker.patch(
            "tools.sync_parent._hash_exists_in_parent",
            side_effect=lambda h: h == "origin/main",
        )
        code = sp.main(["--bump-to", "nope123"])
        assert code == 1

    def test_main_fails_when_origin_main_is_unreadable(
        self, mocker: Any, tmp_path: Path
    ) -> None:
        """An unfetched parent clone is the real precondition — the branch is not.

        The tests above reach a written report with `tmp_path` (not a git repo, and
        so on no branch at all) standing in for the parent, which is what proves the
        checked-out branch no longer gates a scan.
        """
        mocker.patch("tools.sync_parent.PARENT_REPO_PATH", tmp_path)
        mocker.patch("tools.sync_parent._hash_exists_in_parent", return_value=False)
        assert sp.main(["--from", "635ed5a", "--no-fetch"]) == 1


def _ws_report(title: str, number: int | None) -> Any:
    """Minimal PRReport for workstream-grouping tests (bucket/paths irrelevant)."""
    pr = sp.PR(number=number, title=title, commits=[], files=[])
    return sp.PRReport(
        pr=pr,
        bucket=sp.Bucket.EVALUATE,
        confidence=sp.Confidence.LOW,
        wifey_paths=[],
        memory_excerpt=None,
        approach="re-implement",
    )


class TestWorkstreamSummary:
    """derive_workstream clusters subjects; format_workstream_summary tabulates."""

    def test_campaign_tag_live_parity(self) -> None:
        assert (
            sp.derive_workstream(
                "feat(backtest): T6 live-parity foundation — LiveParityConfig (#387)"
            )
            == "live-parity (backtest engine port)"
        )
        # The "T6 PR-N" siblings tag inconsistently but share "into run_backtest".
        assert (
            sp.derive_workstream(
                "feat(backtest): T6 PR-2 — port live regime gate into run_backtest (#388)"
            )
            == "live-parity (backtest engine port)"
        )
        # #394 / #395 lack "run_backtest" but match conflict-resolver / cooldown.
        assert (
            sp.derive_workstream(
                "feat(backtest): T6 PR-4b — port conflict resolver via runner pooling (#394)"
            )
            == "live-parity (backtest engine port)"
        )

    def test_campaign_tag_bucket_c(self) -> None:
        assert (
            sp.derive_workstream(
                "feat(config): Bucket C TOML — encode 7 strategies (#385)"
            )
            == "Bucket C (schema + config)"
        )
        # Bucket C spans feat(backtest) too — tag wins over scope.
        assert (
            sp.derive_workstream(
                "feat(backtest): Bucket C — per-direction adr_exempt on backtest_config (#400)"
            )
            == "Bucket C (schema + config)"
        )

    def test_campaign_tag_gate_audit(self) -> None:
        assert (
            sp.derive_workstream("fix(tools): gate_audit prod-data bugs (#374)")
            == "gate_audit (tooling)"
        )

    def test_campaign_tag_phase_a(self) -> None:
        assert (
            sp.derive_workstream(
                "feat(config): T6 Phase A day-filter audit decisions (#377)"
            )
            == "Phase A (config decisions)"
        )

    def test_gate_audit_wins_over_phase_a_when_both_present(self) -> None:
        # #373 mentions both gate_audit.py and "T6 Phase A plan docs"; tool routes to gate_audit.
        assert (
            sp.derive_workstream(
                "feat(tools): add gate_audit.py + T6 Phase A plan docs (#373)"
            )
            == "gate_audit (tooling)"
        )

    def test_dependency_bumps_merge_deps_and_deps_dev(self) -> None:
        assert (
            sp.derive_workstream("build(deps): bump devalue (#369)")
            == "dependency bumps"
        )
        assert (
            sp.derive_workstream("build(deps-dev): bump svelte (#370)")
            == "dependency bumps"
        )

    def test_fallback_to_conventional_type_scope(self) -> None:
        assert (
            sp.derive_workstream("feat(store): persist volume flags (#371)")
            == "feat(store)"
        )
        assert sp.derive_workstream("chore: refresh regression goldens") == "chore"

    def test_fallback_other_when_no_prefix(self) -> None:
        assert sp.derive_workstream("random subject with no prefix") == "other"

    def test_format_groups_and_counts_in_first_appearance_order(self) -> None:
        reports = [
            _ws_report("feat(backtest): T6 live-parity foundation (#387)", 387),
            _ws_report("build(deps): bump x (#369)", 369),
            _ws_report(
                "feat(backtest): T6 PR-2 — port regime into run_backtest (#388)", 388
            ),
        ]
        text = "\n".join(sp.format_workstream_summary(reports))
        assert "## Workstreams" in text
        assert "| Workstream | PRs | Count |" in text
        assert "live-parity (backtest engine port) | #387 #388 | 2" in text
        assert "dependency bumps | #369 | 1" in text
        assert text.index("live-parity") < text.index("dependency bumps")

    def test_none_pr_renders_as_hash_none(self) -> None:
        text = "\n".join(
            sp.format_workstream_summary([_ws_report("chore: goldens", None)])
        )
        assert "#none | 1" in text

    def test_report_includes_workstreams_between_summary_and_skip(self) -> None:
        pr = sp.PR(
            number=387,
            title="feat(backtest): T6 live-parity foundation (#387)",
            commits=[],
            files=["analytics/backtest/engine.py"],
        )
        report = sp.PRReport(
            pr=pr,
            bucket=sp.Bucket.EVALUATE,
            confidence=sp.Confidence.LOW,
            wifey_paths=[
                sp.WifeyPath(
                    "analytics/backtest/engine.py",
                    "analytics/backtest/engine.py",
                    "direct",
                )
            ],
            memory_excerpt=None,
            approach="re-implement",
        )
        out = sp.format_report([report], "a", "b")
        assert "## Workstreams" in out
        assert (
            out.index("## Summary") < out.index("## Workstreams") < out.index("## SKIP")
        )


class TestInstructionFilesReachAHuman:
    """A parent instruction-file change is EVALUATE, and is never a cherry-pick.

    Mapping ``AGENTS.md`` -> ``CLAUDE.md`` moves it from "unmapped" to "renamed",
    and ``_is_evaluate_path`` grants EVALUATE to every unmapped path. Without the
    ``_INSTRUCTION_FILES`` leg the mapping would therefore have *demoted* the
    parent's highest-leverage surface to PORT / cherry-pick-with-edits — a diff
    that cannot apply, since the parent's 8 KB pointer plus 77 KB ``AGENTS.md``
    has no wifey twin. Each test below pairs with the control at the bottom, which
    fails if the leg is widened into "everything is EVALUATE".
    """

    def _wp(self, path: str, kind: str, target: str | None) -> Any:
        return sp.WifeyPath(path, target, kind)  # type: ignore[arg-type]

    def _pr(self, files: list[str]) -> Any:
        return sp.PR(
            number=99, title="docs: instructions (#99)", commits=[], files=files
        )

    def test_mapped_agents_md_is_still_evaluate(self) -> None:
        wps = [self._wp("AGENTS.md", "renamed", "CLAUDE.md")]
        assert sp.classify_pr(self._pr(["AGENTS.md"]), wps) == sp.Bucket.EVALUATE

    def test_claude_md_is_evaluate_not_port(self) -> None:
        """Pre-existing hole: a surviving same-name file was bucketed PORT."""
        wps = [self._wp("CLAUDE.md", "direct", "CLAUDE.md")]
        assert sp.classify_pr(self._pr(["CLAUDE.md"]), wps) == sp.Bucket.EVALUATE

    def test_instruction_file_is_never_cherry_picked(self) -> None:
        wps = [self._wp("AGENTS.md", "renamed", "CLAUDE.md")]
        approach = sp.suggest_approach(sp.Bucket.EVALUATE, sp.Confidence.LOW, wps)
        assert approach == "re-implement"

    def test_control_mapped_code_file_is_still_port(self) -> None:
        """Positive control: the leg is scoped to instruction files only.

        Without this, widening ``_is_evaluate_path`` to return True for every
        mapped path would satisfy all three tests above and silently route the
        entire port queue to EVALUATE.
        """
        wps = [
            self._wp(
                "analytics/backtest_lib.py", "renamed", "analytics/backtest/engine.py"
            )
        ]
        pr = sp.PR(number=100, title="fix: engine (#100)", commits=[], files=[])
        assert sp.classify_pr(pr, wps) == sp.Bucket.PORT
        assert (
            sp.suggest_approach(sp.Bucket.PORT, sp.Confidence.LOW, wps)
            == "cherry-pick-with-edits"
        )


class TestClassifierCaveatBanner:
    """The counts must not read as rulings to a session that never opened the skill.

    Round 16 (2026-08-26) printed `0 SKIP / 3 PORT / 19 EVALUATE /
    0 ALREADY-APPLIED` where the rulings were **3 / 4 / 10 / 4 / 1**. The
    ALREADY-APPLIED zero is the sharp half: all four were the parent adopting
    *wifey's* work, which no path-resolution test can detect, because the path
    resolves either way.

    ⚠ **What is pinned here is the WARNING, not better counts.** The classifier
    cannot see direction of travel, so the deliverable is a reader who distrusts
    the table. The skill body already said this; the REPORT did not, and the
    report is what gets read days later.
    """

    def _out(self) -> str:
        pr = sp.PR(
            number=1,
            title="feat: thing (#1)",
            commits=[],
            files=["analytics/regime.py"],
        )
        report = sp.PRReport(
            pr=pr,
            bucket=sp.Bucket.EVALUATE,
            confidence=sp.Confidence.LOW,
            wifey_paths=[
                sp.WifeyPath("analytics/regime.py", "analytics/regime.py", "direct")
            ],
            memory_excerpt="",
            approach="re-implement",
        )
        return sp.format_report([report], "a", "b")

    def test_the_report_carries_the_caveat(self) -> None:
        out = self._out()
        assert "not rulings" in out
        assert "direction of travel" in out

    def test_the_caveat_precedes_the_counts_it_qualifies(self) -> None:
        """A warning printed under the table is read after the number is believed."""
        out = self._out()
        assert out.index("not rulings") < out.index("## Summary")

    def test_the_caveat_names_the_ruling_set_the_table_cannot_show(self) -> None:
        """NO PORT is a ruling with no bucket, so its absence from the table is
        not visible IN the table — which is why the banner has to name it."""
        out = self._out()
        assert "NO PORT" in out

    def test_the_measured_miss_is_quoted_not_summarised(self) -> None:
        """An abstract "counts may be wrong" trains dismissal; the observed
        distribution is what makes the warning checkable."""
        out = self._out()
        assert "0 ALREADY-APPLIED" in out
