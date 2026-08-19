"""Tests for the extracted `/post-branch` mechanical checks.

Every check here ships a **positive control** — an input that makes it fire.
The prose versions of these checks had none, which is how the skill's own
non-Python file check shipped reporting COVERED for a file no doc had heard of:
it was never run against something that should fail.
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.post_branch_checks import (
    Runner,
    added_paths,
    bad_atx_lines,
    check_handoff_symbols,
    check_negative_claims,
    check_new_files,
    check_new_modules,
    check_new_targets,
    check_queue_items,
    current_state_bullets,
    extract_tokens,
    numbered_items,
    probe_names,
)

# CLAUDE.md's real sentence — the one that made every skill report COVERED.
GENERIC_SKILL_SENTENCE = (
    "Skills live in `.claude/skills/<name>/SKILL.md`, are invoked with "
    "`/skill-name`, and each one's description is already loaded."
)


class TestProbeNames:
    """The defect: a basename that names a ROLE cannot identify a FILE."""

    def test_shared_constant_probes_the_parent_directory(self) -> None:
        assert probe_names(".claude/skills/zzz-fake-skill/SKILL.md") == [
            "zzz-fake-skill"
        ]

    def test_every_shared_constant_behaves_the_same_way(self) -> None:
        for path, want in [
            ("pkg/__init__.py", "pkg"),
            ("docs/audits/INDEX.md", "audits"),
            ("web/ui/README.md", "ui"),
        ]:
            assert probe_names(path) == [want], path

    def test_ordinary_file_probes_its_own_name_and_stem(self) -> None:
        assert probe_names("deploy/backup-offsite.sh") == [
            "backup-offsite.sh",
            "backup-offsite",
        ]

    def test_bare_name_without_directory_does_not_probe_empty_string(self) -> None:
        assert "" not in probe_names("SKILL.md")


class TestCheckNewFiles:
    """The 4-for-4 the fix was tested against, now pinned."""

    def test_fabricated_skill_is_UNDOCUMENTED_despite_the_generic_sentence(
        self,
    ) -> None:
        found = check_new_files(
            [".claude/skills/zzz-fake-skill/SKILL.md"], GENERIC_SKILL_SENTENCE
        )
        assert len(found) == 1
        assert "zzz-fake-skill" in found[0].detail

    def test_a_skill_the_docs_actually_name_is_covered(self) -> None:
        blob = GENERIC_SKILL_SENTENCE + "\nThe `post-branch` skill sweeps docs."
        assert check_new_files([".claude/skills/post-branch/SKILL.md"], blob) == []

    def test_ordinary_operator_file_still_reports_when_absent(self) -> None:
        found = check_new_files(["deploy/notify-failure.sh"], "unrelated prose")
        assert len(found) == 1

    def test_ordinary_operator_file_is_covered_when_named(self) -> None:
        assert (
            check_new_files(["deploy/notify-failure.sh"], "runs notify-failure.sh")
            == []
        )

    def test_tests_and_docs_and_python_are_out_of_scope(self) -> None:
        assert check_new_files(["tests/test_x.py", "docs/a.md", "tools/x.py"], "") == []


class TestCheckNewModules:
    def test_undocumented_module_fires(self) -> None:
        found = check_new_modules(["analytics/newsleeve/book.py"], "nothing here")
        assert len(found) == 1

    def test_documented_module_is_quiet(self) -> None:
        assert (
            check_new_modules(["analytics/regime.py"], "`regime.py` labels bars") == []
        )

    def test_word_boundary_stops_a_truncated_probe_reporting_covered(self) -> None:
        """`-w` semantics: a substring hit must NOT count as documentation."""
        assert (
            len(check_new_modules(["tools/docs_index.py"], "ocs_index is great")) == 1
        )


class TestCheckNewTargets:
    def test_undocumented_target_fires(self) -> None:
        found = check_new_targets("+zzz-nope: x\n", "unrelated docs")
        assert len(found) == 1
        assert "zzz-nope" in found[0].detail

    def test_wifey_prefix_is_stripped_before_probing(self) -> None:
        """CLAUDE.md documents subcommands; the wrapper rule covers the rest."""
        assert (
            check_new_targets("+wifey-param-audit: x\n", "`param-audit` runs WFO") == []
        )

    def test_unchanged_target_lines_are_ignored(self) -> None:
        assert check_new_targets(" existing-target: x\n", "") == []

    def test_a_yaml_key_is_not_a_make_target(self) -> None:
        """Found by running the sweep on its own branch.

        The regex was applied to the WHOLE diff, so `behavior_signal_globs:` in a
        SKILL.md's YAML block reported as an undocumented Make target. A target
        exists only in the Makefile, so `gather` now passes the Makefile diff
        alone — this test pins the shape that broke it.
        """
        yaml_hunk = "+behavior_signal_globs:   # touching these needs a walk\n"
        assert check_new_targets(yaml_hunk, "") != []  # the regex still matches...
        # ...which is exactly why the CALLER must scope it to the Makefile.


class TestCheckQueueItems:
    """Defect 1: nothing swept the handoff for work the branch just finished."""

    HANDOFF = (
        "## Next tasks\n\n"
        "1. **H-004 risk-off sector rotation is the NEXT TASK.** Start from\n"
        "   `config/universe.json`, which carries GICS sector.\n\n"
        "2. **Something unrelated** about `deploy/backup-offsite.sh`.\n"
    )

    def test_a_docs_only_branch_closing_an_item_is_CAUGHT(self) -> None:
        """The case the symbol-based mitigation structurally could not see.

        No `.py`, no `def` — only prose mentioning H-004.
        """
        diff = "+# H-004 verdict: BLOCKED at G3\n"
        found = check_queue_items(self.HANDOFF, diff, "docs/audits/h004.md")
        assert len(found) == 1
        assert "item 1" in found[0].detail

    def test_an_unrelated_branch_does_not_fire(self) -> None:
        diff = "+def unrelated() -> None:\n+    pass\n"
        assert check_queue_items(self.HANDOFF, diff, "analytics/x.py") == []

    def test_it_matches_on_a_changed_FILENAME_too(self) -> None:
        found = check_queue_items(self.HANDOFF, "", "deploy/backup-offsite.sh")
        assert len(found) == 1
        assert "item 2" in found[0].detail


class TestNumberedItems:
    def test_continuation_lines_join_their_item(self) -> None:
        items = numbered_items("1. first\n   more of first\n2. second\n")
        assert items[1] == "first\nmore of first"
        assert items[2] == "second"

    def test_a_new_unindented_paragraph_ends_the_item(self) -> None:
        items = numbered_items("1. first\n\nUnindented prose.\n2. second\n")
        assert "Unindented" not in items[1]


class TestAddedPaths:
    """An untracked file is exactly the case the presence checks exist for."""

    def test_untracked_files_count_as_added(self) -> None:
        assert added_paths("", "?? tools/brand_new.py\n") == ["tools/brand_new.py"]

    def test_untracked_directories_are_skipped(self) -> None:
        assert added_paths("", "?? somedir/\n") == []

    def test_staged_and_diffed_additions_merge_without_duplicates(self) -> None:
        assert added_paths("a.py\n", "A  a.py\n") == ["a.py"]

    def test_modified_files_are_not_additions(self) -> None:
        assert added_paths("", " M existing.py\n") == []


class TestHandoffSymbolNoise:
    """Ubiquitous doc stems discriminate nothing and drown the real hits."""

    def test_ubiquitous_stems_are_suppressed(self) -> None:
        handoff = "line about CLAUDE and SKILL and tools\n"
        found = check_handoff_symbols(handoff, "", "CLAUDE.md SKILL.md tools/x.py")
        assert found == []

    def test_a_real_symbol_still_reports(self) -> None:
        handoff = "the `powered_null` helper is absent here\n"
        found = check_handoff_symbols(handoff, "+def powered_null() -> None:\n", "")
        assert len(found) == 1


class TestExtractTokens:
    def test_backticked_spans_become_tokens(self) -> None:
        assert "universe.json" in extract_tokens("see `universe.json` for sectors")

    def test_hypothesis_ids_are_tokens_even_unbackticked(self) -> None:
        assert "H-004" in extract_tokens("H-004 is the next task")

    def test_stopwords_and_short_tokens_are_dropped(self) -> None:
        toks = extract_tokens("`main` and `md` and `git`")
        assert toks == set()

    def test_a_backticked_command_yields_its_first_word(self) -> None:
        assert "docs-index" in extract_tokens("run `docs-index --check` first")


class TestCurrentStateBullets:
    MEM = (
        "## Something\n- not counted\n\n"
        "## Current State\n\n> a quote\n\n- one\n- two\n  - nested not counted\n\n"
        "## After\n- also not counted\n"
    )

    def test_counts_only_top_level_bullets_in_the_section(self) -> None:
        assert current_state_bullets(self.MEM) == 2

    def test_absent_section_counts_zero(self) -> None:
        assert current_state_bullets("# nothing here\n") == 0


class TestBadAtxLines:
    def test_a_wrapped_pr_reference_in_column_one_is_flagged(self) -> None:
        assert bad_atx_lines("fine line\n#198 wrapped here\n") == [2]

    def test_a_real_heading_is_not_flagged(self) -> None:
        assert bad_atx_lines("# Real Heading\n## Also real\n") == []


class TestCheckNegativeClaims:
    """The scope leg. This check had NO test, which is how it ran unscoped.

    It greps the tree for absence language and used to report every hit on
    every branch — the same findings forever, regardless of the diff, while
    the skill's own table described it as asking about what the branch just
    added. Code and sentence disagreed and only the sentence was read.
    """

    CLAIM = (
        "docs/x.md:12:the `pead_wiring` module is not yet wired, so nothing reads it"
    )

    @staticmethod
    def _runner(out: str) -> Runner:
        def run(argv: Sequence[str]) -> str:
            return out

        return run

    def test_a_claim_the_branch_CONTRADICTS_is_reported(self) -> None:
        """Positive control: the branch adds the very thing the doc denies."""
        findings, suppressed = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert len(findings) == 1
        assert "pead_wiring" in findings[0].detail
        assert suppressed == 0

    def test_a_claim_unrelated_to_the_diff_is_scoped_out_and_COUNTED(self) -> None:
        findings, suppressed = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def something_else() -> None:\n",
            diff_names="analytics/other.py",
        )
        assert findings == []
        assert suppressed == 1, "a scoped-out claim must stay countable, not vanish"

    def test_a_claim_with_no_token_FAILS_OPEN(self) -> None:
        """Unscopable means unruled-out; a miss is the harm this check exists for."""
        findings, suppressed = check_negative_claims(
            self._runner("docs/x.md:3:the exporter is not yet wired"),
            diff="+unrelated\n",
            diff_names="other.py",
        )
        assert len(findings) == 1
        assert "no token to scope on" in findings[0].detail
        assert suppressed == 0

    def test_a_REMOVAL_does_not_report_the_claim(self) -> None:
        """Removing the named thing makes an absence claim MORE true, not less."""
        findings, suppressed = check_negative_claims(
            self._runner(self.CLAIM),
            diff="-def pead_wiring() -> None:\n",
            diff_names="",
        )
        assert findings == []
        assert suppressed == 1

    def test_the_skill_itself_is_still_exempt(self) -> None:
        """post-branch's own file documents the language and must not self-match."""
        findings, suppressed = check_negative_claims(
            self._runner(
                ".claude/skills/post-branch/SKILL.md:9:`pead_wiring` is not yet wired"
            ),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert findings == []
        assert suppressed == 0
