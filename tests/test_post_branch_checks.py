"""Tests for the extracted `/post-branch` mechanical checks.

Every check here ships a **positive control** — an input that makes it fire.
The prose versions of these checks had none, which is how the skill's own
non-Python file check shipped reporting COVERED for a file no doc had heard of:
it was never run against something that should fail.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from tools.post_branch_checks import (
    HANDOFF_MAX_LINES,
    NEGATIVE_CLAIM_EXEMPT,
    NEGATIVE_CLAIM_RE,
    Runner,
    _check_handoff_size,
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
    load_sensitive_terms,
    main,
    mask_term,
    numbered_items,
    probe_names,
    scan_text_for_terms,
    sensitive_terms_result,
    sensitive_text_result,
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
        findings, suppressed, exempted = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert len(findings) == 1
        assert "pead_wiring" in findings[0].detail
        assert suppressed == 0

    def test_a_claim_unrelated_to_the_diff_is_scoped_out_and_COUNTED(self) -> None:
        findings, suppressed, exempted = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def something_else() -> None:\n",
            diff_names="analytics/other.py",
        )
        assert findings == []
        assert suppressed == 1, "a scoped-out claim must stay countable, not vanish"

    def test_a_claim_with_no_token_FAILS_OPEN(self) -> None:
        """Unscopable means unruled-out; a miss is the harm this check exists for."""
        findings, suppressed, exempted = check_negative_claims(
            self._runner("docs/x.md:3:the exporter is not yet wired"),
            diff="+unrelated\n",
            diff_names="other.py",
        )
        assert len(findings) == 1
        assert "no token to scope on" in findings[0].detail
        assert suppressed == 0

    def test_a_REMOVAL_does_not_report_the_claim(self) -> None:
        """Removing the named thing makes an absence claim MORE true, not less."""
        findings, suppressed, exempted = check_negative_claims(
            self._runner(self.CLAIM),
            diff="-def pead_wiring() -> None:\n",
            diff_names="",
        )
        assert findings == []
        assert suppressed == 1

    def test_the_skill_itself_is_still_exempt(self) -> None:
        """post-branch's own file documents the language and must not self-match."""
        findings, suppressed, exempted = check_negative_claims(
            self._runner(
                ".claude/skills/post-branch/SKILL.md:9:`pead_wiring` is not yet wired"
            ),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert findings == []
        assert suppressed == 0


class TestNegativeClaimExempt:
    """The allowlist that keeps the leg readable without making it a mute.

    Three consecutive runs dismissed the same two lines of
    `.claude/context/analytics.md`, which is a check training its own reader to
    skim. But the leg's ONE true positive (2026-08-20h) came from exactly that
    file, so every test here asks whether the narrowing preserved it.
    """

    CLAIM = "docs/x.md:12:the `pead_wiring` module is not yet wired, `symbol` too"

    @staticmethod
    def _runner(out: str) -> Runner:
        def run(argv: Sequence[str]) -> str:
            return out

        return run

    def test_an_exempt_token_alone_is_counted_not_reported(self) -> None:
        exempt = dict(NEGATIVE_CLAIM_EXEMPT)
        exempt[("docs/x.md", "symbol")] = "test fixture"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.post_branch_checks.NEGATIVE_CLAIM_EXEMPT", exempt)
            findings, suppressed, exempted = check_negative_claims(
                self._runner(self.CLAIM),
                diff="+def uses(symbol: str) -> None:\n",
                diff_names="analytics/other.py",
            )
        assert findings == []
        assert exempted == 1, "an exemption must stay countable, never vanish"
        assert suppressed == 0

    def test_ONE_unexempt_token_still_reports_the_whole_line(self) -> None:
        """The property that stops the allowlist growing into a token filter."""
        exempt = dict(NEGATIVE_CLAIM_EXEMPT)
        exempt[("docs/x.md", "symbol")] = "test fixture"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.post_branch_checks.NEGATIVE_CLAIM_EXEMPT", exempt)
            findings, _suppressed, exempted = check_negative_claims(
                self._runner(self.CLAIM),
                diff="+def pead_wiring(symbol: str) -> None:\n",
                diff_names="analytics/signal/pead_wiring.py",
            )
        assert len(findings) == 1, "pead_wiring is not exempt, so the line reports"
        assert exempted == 0

    def test_the_exemption_is_keyed_on_the_PAIR_not_the_token(self) -> None:
        """The same token in another file is untouched — no cross-file hole."""
        findings, _suppressed, exempted = check_negative_claims(
            self._runner("docs/other.md:3:`symbol` handling is not yet wired"),
            diff="+def uses(symbol: str) -> None:\n",
            diff_names="analytics/other.py",
        )
        assert len(findings) == 1
        assert exempted == 0

    def test_every_entry_still_matches_a_REAL_claim_line(self) -> None:
        """External referent: a dead entry fails here rather than sitting silent.

        An allowlist nobody re-derives is how a mute survives the doc it was
        written for. This asserts each `(path, token)` still names a token on a
        line that actually trips the regex.
        """
        for (path, token), reason in NEGATIVE_CLAIM_EXEMPT.items():
            text = Path(path).read_text(encoding="utf-8")
            on_claim_lines = {
                tok
                for line in text.splitlines()
                if NEGATIVE_CLAIM_RE.search(line)
                for tok in extract_tokens(line)
            }
            assert token in on_claim_lines, (
                f"{path}:{token} exempts a token no claim line carries any more — "
                "delete the entry rather than leaving a mute behind"
            )
            assert reason.strip(), "every entry states why, inline"

    def test_the_TRUE_POSITIVE_tokens_are_NOT_exempt(self) -> None:
        """Regression control for the narrowing itself.

        2026-08-20h: adding `analytics/research_guards/cluster.py` falsified two
        sentences in `.claude/context/analytics.md`. No symbol-keyed check could
        have caught it — an absence claim shares no symbol with the thing that
        falsifies it — so this leg is the only thing that would, and the
        allowlist must not have closed that path.
        """
        analytics = ".claude/context/analytics.md"
        for token in ("research_guards", "cluster.py", "attribution"):
            assert (analytics, token) not in NEGATIVE_CLAIM_EXEMPT


class TestSensitiveTextScan:
    """The fourth exposure surface: a PR title/body, before it is posted.

    The three git legs report `clean` on a body naming every term — correctly,
    since a body is neither the tree nor a commit. It happened live on #245,
    whose first draft named all three in the very window the gate exists to make
    safe, and was caught by hand both times since.
    """

    TERM = "acmecorp"

    def test_a_term_in_a_composed_body_FIRES(self) -> None:
        """Positive control for the surface the git legs cannot reach."""
        result = sensitive_text_result(
            [("pr-body.md", f"## Summary\n\nPorted from {self.TERM}'s tooling.\n")],
            terms=[self.TERM],
        )
        assert len(result.findings) == 1
        assert "line(s) 3" in result.findings[0].detail
        assert "does not unpublish it" in result.findings[0].detail

    def test_the_term_is_NEVER_printed_unmasked(self) -> None:
        """The report is itself pasted into a handoff, so it must not restate."""
        result = sensitive_text_result(
            [("pr-body.md", f"{self.TERM}\n")], terms=[self.TERM]
        )
        assert self.TERM not in result.findings[0].detail
        assert "acm…" in result.findings[0].detail

    def test_the_matching_LINE_is_never_echoed(self) -> None:
        """Line numbers only: quoting context would leak what masking withheld."""
        secret_line = f"we vendored {self.TERM} internals here"
        result = sensitive_text_result([("pr-body.md", secret_line)], terms=[self.TERM])
        assert "vendored" not in result.findings[0].detail

    def test_a_clean_body_is_clean_and_says_what_it_checked(self) -> None:
        result = sensitive_text_result(
            [("pr-body.md", "## Summary\n\nNothing to see.\n")], terms=[self.TERM]
        )
        assert result.findings == []
        assert result.note is not None
        assert "1 term(s)" in result.note

    def test_an_absent_list_is_a_FINDING_here_too(self) -> None:
        """`--text` must not become the one mode where NOT CONFIGURED is a pass."""
        result = sensitive_text_result([("pr-body.md", "anything")], terms=[])
        assert len(result.findings) == 1
        assert "NOT CONFIGURED" in result.findings[0].detail

    def test_case_is_ignored_and_every_hit_line_is_listed(self) -> None:
        text = f"{self.TERM.upper()}\nfiller\nand {self.TERM.title()} again\n"
        found = scan_text_for_terms("body", text, [self.TERM])
        assert len(found) == 1
        assert "line(s) 1, 3" in found[0].detail

    def test_cli_text_mode_exits_1_on_a_hit_and_0_when_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """End-to-end: this is the mode a session runs before `gh pr create`."""
        terms_file = tmp_path / "terms.txt"
        terms_file.write_text(f"{self.TERM}\n", encoding="utf-8")
        dirty = tmp_path / "dirty.md"
        dirty.write_text(f"ported from {self.TERM}\n", encoding="utf-8")
        clean = tmp_path / "clean.md"
        clean.write_text("nothing here\n", encoding="utf-8")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.post_branch_checks.SENSITIVE_TERMS", terms_file)
            assert main(["--text", str(dirty)]) == 1
            assert main(["--text", str(clean)]) == 0
        out = capsys.readouterr().out
        assert self.TERM not in out

    def test_cli_REFUSES_text_combined_with_check(self, tmp_path: Path) -> None:
        """Honouring one flag and dropping the other reports a pass unasked for."""
        body = tmp_path / "b.md"
        body.write_text("clean\n", encoding="utf-8")
        assert main(["--text", str(body), "--check", "md-atx"]) == 2

    def test_cli_text_mode_REFUSES_an_unreadable_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A read error must not render as a clean run of a one-check sweep."""
        terms_file = tmp_path / "terms.txt"
        terms_file.write_text(f"{self.TERM}\n", encoding="utf-8")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.post_branch_checks.SENSITIVE_TERMS", terms_file)
            assert main(["--text", str(tmp_path / "nope.md")]) == 2
        assert "clean" not in capsys.readouterr().out


class TestHandoffSize:
    """The leg measures the file against an EXTERNAL cap, not against itself.

    It used to compare the handoff to a `Line count:` stamp the handoff carried
    about itself — a number whose only purpose was to be checked, costing a
    read / `wc -l` / edit / re-read cycle every run. And the stamp regex missing
    returned `[]`, so deleting the stamp would have made the leg **vacuously
    green forever** rather than red. A check that cannot fire is dismissal.
    """

    def test_oversized_handoff_fires(self) -> None:
        handoff = "\n".join(f"line {i}" for i in range(HANDOFF_MAX_LINES + 5))
        found = _check_handoff_size(handoff)
        assert len(found) == 1
        assert "prune before adding" in found[0].detail

    def test_handoff_at_the_cap_is_clean(self) -> None:
        handoff = "\n".join(f"line {i}" for i in range(HANDOFF_MAX_LINES))
        assert _check_handoff_size(handoff) == []

    def test_absent_handoff_is_silent(self) -> None:
        assert _check_handoff_size("") == []

    def test_no_stamp_is_required_to_make_it_fire(self) -> None:
        """The regression control for the vacuous-green defect.

        The old leg keyed off a `Line count:` stamp; text carrying none was
        unconditionally clean however large it grew. This asserts the opposite.
        """
        handoff = "\n".join(
            "no stamp anywhere here" for _ in range(HANDOFF_MAX_LINES + 1)
        )
        assert "Line count:" not in handoff
        assert _check_handoff_size(handoff) != []


class TestSensitiveTerms:
    """The pre-flip gate (parent #658).

    Ported because the flip publishes the whole HISTORY, not `HEAD`: a
    working-tree `git grep` agrees with every other review surface while deleted
    blobs stay reachable. wifey's own 2026-08-19 measurement found identifiers on
    three of four surfaces, commit MESSAGES among them — the surface no file edit
    reaches — so the three legs are tested separately, as they fail separately.
    """

    TERM = "acmecorp"

    @staticmethod
    def _runner(
        *, tracked: str = "", messages: str = "", introduced: str = ""
    ) -> Runner:
        """Dispatch on argv: the three legs ask git three different questions."""

        def run(argv: Sequence[str]) -> str:
            if argv[1] == "grep":
                return tracked
            if "-S" in argv:
                return introduced
            return messages

        return run

    def test_an_absent_list_is_a_FINDING_not_a_skip(self) -> None:
        """ "Did not run" and "passed" must not look alike before a flip."""
        result = sensitive_terms_result(self._runner(), terms=[])
        assert len(result.findings) == 1
        assert "NOT CONFIGURED" in result.findings[0].detail
        assert "NOT the same as passing" in result.findings[0].detail

    def test_a_clean_branch_reports_no_findings_and_notes_the_baseline(self) -> None:
        result = sensitive_terms_result(self._runner(), terms=[self.TERM])
        assert result.findings == []
        assert result.note is not None
        assert "1 term(s)" in result.note
        assert "baseline is not re-reported" in result.note

    def test_a_tracked_file_hit_fires(self) -> None:
        """Positive control for the only leg a plain `git grep` covers."""
        result = sensitive_terms_result(
            self._runner(tracked="docs/a.md\ndocs/b.md\n"), terms=[self.TERM]
        )
        assert len(result.findings) == 1
        assert "2 tracked file(s)" in result.findings[0].detail

    def test_a_commit_MESSAGE_hit_fires_with_no_file_hit(self) -> None:
        """The leg no file edit reaches — and the one a tree-only gate misses."""
        result = sensitive_terms_result(
            self._runner(messages=f"chore: scrub {self.TERM} from the docs\n"),
            terms=[self.TERM],
        )
        assert len(result.findings) == 1
        assert "commit MESSAGE" in result.findings[0].detail
        assert "only a history rewrite does" in result.findings[0].detail

    def test_a_term_INTRODUCED_on_this_branch_fires(self) -> None:
        result = sensitive_terms_result(
            self._runner(introduced="abc1234 feat: add a thing\n"), terms=[self.TERM]
        )
        assert len(result.findings) == 1
        assert "introduced by 1 commit(s)" in result.findings[0].detail
        assert "will NOT unexpose it" in result.findings[0].detail

    def test_the_term_is_NEVER_printed_unmasked(self) -> None:
        """Control: the report about the leak must not reproduce the leak.

        This output is pasted into handoffs and PR bodies, which are themselves
        tracked or backed up — the same self-referential shape as the
        `gh pr create` hook firing on its own documentation.
        """
        result = sensitive_terms_result(
            self._runner(
                tracked="docs/a.md\n",
                messages=f"chore: scrub {self.TERM}\n",
                introduced="abc1234 feat: add a thing\n",
            ),
            terms=[self.TERM],
        )
        assert len(result.findings) == 3, "all three legs should fire"
        for finding in result.findings:
            assert self.TERM not in finding.detail
            assert "acm…" in finding.detail

    def test_a_short_term_masks_to_nothing_identifying(self) -> None:
        assert mask_term("ab") == "…"
        assert mask_term("abcdef") == "abc…"

    def test_load_strips_comments_blanks_and_case(self) -> None:
        terms = load_sensitive_terms(
            "# a comment\n\nAcmeCorp\n  Other Co  # trailing note\n"
        )
        assert terms == ["acmecorp", "other co"]
