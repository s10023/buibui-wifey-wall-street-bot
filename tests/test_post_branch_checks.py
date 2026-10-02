"""Tests for the extracted `/post-branch` mechanical checks.

Every check here ships a **positive control** — an input that makes it fire.
The prose versions of these checks had none, which is how the skill's own
non-Python file check shipped reporting COVERED for a file no doc had heard of:
it was never run against something that should fail.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from tools.post_branch_checks import (
    HANDOFF_MAX_LINES,
    HANDOFF_WARN_MARGIN,
    NEGATIVE_CLAIM_EXEMPT,
    NEGATIVE_CLAIM_PATHS,
    NEGATIVE_CLAIM_RE,
    UNCOVERED_STEPS,
    Finding,
    Runner,
    _check_handoff_size,
    _handoff_leg,
    _negative_claims_result,
    _read_each,
    _run,
    added_paths,
    bad_atx_lines,
    changed_line_numbers,
    check_amended_targets,
    check_handoff_symbols,
    check_negative_claims,
    check_new_files,
    check_new_modules,
    check_new_targets,
    check_queue_items,
    claim_subject_tokens,
    current_state_bullets,
    demote_unchanged_in_rewrites,
    enumerated_members,
    extract_tokens,
    load_sensitive_terms,
    main,
    mask_term,
    numbered_items,
    probe_names,
    render,
    rewritten_files,
    scan_text_for_terms,
    sensitive_terms_result,
    sensitive_text_result,
    targets_by_line,
    uncovered_notice,
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

    def test_leading_dot_name_also_probes_its_dotless_form(self) -> None:
        assert probe_names(".gitattributes") == [".gitattributes", "gitattributes"]

    def test_the_dotless_probe_is_what_MAKES_a_dotfile_findable(self) -> None:
        """The positive control: the dotted probe cannot match, the dotless one can.

        `check_new_files` anchors every probe with a regex word boundary, and a
        boundary needs a word character on one side. Both characters either side of
        the dot in ``a `.gitattributes` file`` are non-word, so the dotted probe
        asserts a boundary that is not there. Without the dotless form the finding
        is UNCLEARABLE rather than merely wrong — no amount of documentation can
        satisfy it. Asserting only `probe_names`'s return value would pass whether
        or not that were true, so this observes the channel the fix protects.
        """
        boundary = "\\b"
        doc = "The repo root carries a `.gitattributes` pinning `*.sh eol=lf`."
        dotted, dotless = probe_names(".gitattributes")
        assert not re.search(f"{boundary}{re.escape(dotted)}{boundary}", doc)
        assert re.search(f"{boundary}{re.escape(dotless)}{boundary}", doc)
        assert check_new_files([".gitattributes"], doc) == []

    def test_a_dotfile_no_doc_mentions_is_still_reported(self) -> None:
        """The other direction: the fix must not make every dotfile read covered."""
        found = check_new_files([".zzzfakerc"], "no doc says anything about this")
        assert [f.detail for f in found] == ["UNDOCUMENTED FILE: .zzzfakerc"]


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


class TestCheckNewModulesInventory:
    """The #255 shape: prose mentions the token, the INVENTORY omits the member.

    Each test builds a real package on disk, because the whole point of the leg
    is that it compares the doc against an external referent (`ls`) rather than
    against itself.
    """

    @staticmethod
    def _pkg(tmp_path: Path, *members: str) -> Path:
        pkg = tmp_path / "analytics" / "research_guards"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        for m in members:
            (pkg / m).write_text("")
        return pkg

    def test_prose_hit_no_longer_reports_covered(self, tmp_path: Path) -> None:
        """THE REGRESSION. `sharpe` in prose must not cover `sharpe.py`."""
        self._pkg(tmp_path, "power.py", "pbo.py", "sharpe.py")
        blob = (
            "The package holds `power.py` and `pbo.py`. Every sleeve reports a "
            "sharpe, and the sharpe bar is 0.7, so sharpe is everywhere."
        )
        found = check_new_modules(
            ["analytics/research_guards/sharpe.py"], blob, root=tmp_path
        )
        assert len(found) == 1
        assert "NOT IN INVENTORY" in found[0].detail
        assert "sharpe.py" in found[0].detail

    def test_a_member_inside_the_inventory_is_quiet(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL — the leg must be able to go green on the same shape."""
        self._pkg(tmp_path, "power.py", "pbo.py", "sharpe.py")
        blob = "The package holds `power.py`, `pbo.py` and `sharpe.py`."
        assert (
            check_new_modules(
                ["analytics/research_guards/sharpe.py"], blob, root=tmp_path
            )
            == []
        )

    def test_below_quorum_falls_back_to_the_probe(self, tmp_path: Path) -> None:
        """One backticked sibling is a passing reference, not an inventory.

        Falling back matters: the strict form would otherwise fire on every
        package the docs merely mention, which is how a check gets ignored.
        """
        self._pkg(tmp_path, "power.py", "sharpe.py")
        blob = "See `power.py`. The new `sharpe.py` primitive is described here."
        assert (
            check_new_modules(
                ["analytics/research_guards/sharpe.py"], blob, root=tmp_path
            )
            == []
        )

    def test_backticks_are_the_discriminator(self, tmp_path: Path) -> None:
        pkg = self._pkg(tmp_path, "power.py", "pbo.py")
        assert enumerated_members(pkg, "`power.py` and `pbo.py`") == {
            "power.py",
            "pbo.py",
        }
        assert enumerated_members(pkg, "power.py and pbo.py, unquoted") == set()

    def test_absent_package_is_not_an_inventory(self, tmp_path: Path) -> None:
        assert enumerated_members(tmp_path / "nope", "`a.py` `b.py`") == set()


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
        findings, suppressed, exempted, _soft = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert len(findings) == 1
        assert "pead_wiring" in findings[0].detail
        assert suppressed == 0

    def test_a_claim_unrelated_to_the_diff_is_scoped_out_and_COUNTED(self) -> None:
        findings, suppressed, exempted, _soft = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def something_else() -> None:\n",
            diff_names="analytics/other.py",
        )
        assert findings == []
        assert suppressed == 1, "a scoped-out claim must stay countable, not vanish"

    def test_a_claim_with_no_token_FAILS_OPEN(self) -> None:
        """Unscopable means unruled-out; a miss is the harm this check exists for."""
        findings, suppressed, exempted, _soft = check_negative_claims(
            self._runner("docs/x.md:3:the exporter is not yet wired"),
            diff="+unrelated\n",
            diff_names="other.py",
        )
        assert len(findings) == 1
        assert "no token to scope on" in findings[0].detail
        assert suppressed == 0

    def test_a_REMOVAL_does_not_report_the_claim(self) -> None:
        """Removing the named thing makes an absence claim MORE true, not less."""
        findings, suppressed, exempted, _soft = check_negative_claims(
            self._runner(self.CLAIM),
            diff="-def pead_wiring() -> None:\n",
            diff_names="",
        )
        assert findings == []
        assert suppressed == 1

    def test_the_skill_itself_is_still_exempt(self) -> None:
        """post-branch's own file documents the language and must not self-match."""
        findings, suppressed, exempted, _soft = check_negative_claims(
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
            findings, suppressed, exempted, _soft = check_negative_claims(
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
            findings, _suppressed, exempted, _soft = check_negative_claims(
                self._runner(self.CLAIM),
                diff="+def pead_wiring(symbol: str) -> None:\n",
                diff_names="analytics/signal/pead_wiring.py",
            )
        assert len(findings) == 1, "pead_wiring is not exempt, so the line reports"
        assert exempted == 0

    def test_the_exemption_is_keyed_on_the_PAIR_not_the_token(self) -> None:
        """The same token in another file is untouched — no cross-file hole."""
        findings, _suppressed, exempted, _soft = check_negative_claims(
            self._runner("docs/other.md:3:`symbol` handling is not yet wired"),
            diff="+def uses(symbol: str) -> None:\n",
            diff_names="analytics/other.py",
        )
        assert len(findings) == 1
        assert exempted == 0

    def test_every_entry_still_matches_a_REAL_claim_line(self) -> None:
        """External referent: a dead entry fails here rather than sitting silent.

        An allowlist nobody re-derives is how a mute survives the doc it was
        written for. This asserts each key still names something on a line that
        actually trips the regex.

        ⚠ **Two key KINDS, and the guard has to cover both.** Most entries key on
        a backticked token. A line naming no subject this tool can reach has no
        token to key on, so those key on the matched MARKER instead — and a guard
        that only understood tokens would have to be loosened to admit them,
        which is how the second kind ends up unguarded.
        """
        for (path, key), reason in NEGATIVE_CLAIM_EXEMPT.items():
            text = Path(path).read_text(encoding="utf-8")
            claim_lines = [
                line for line in text.splitlines() if NEGATIVE_CLAIM_RE.search(line)
            ]
            tokens = {tok for line in claim_lines for tok in extract_tokens(line)}
            markers = {
                mm.group(0).lower()
                for line in claim_lines
                for mm in NEGATIVE_CLAIM_RE.finditer(line)
            }
            assert key in tokens or key in markers, (
                f"{path}:{key} exempts a token/marker no claim line carries any "
                "more — delete the entry rather than leaving a mute behind"
            )
            assert reason.strip(), "every entry states why, inline"

    def test_a_marker_entry_only_fires_where_there_is_NO_token(self) -> None:
        """The marker key is the LAST resort, never a path-wide mute.

        Its whole justification is that the line names no reachable subject. If
        such a line ever gains a backticked token, token scoping takes over and
        the marker entry must stop applying — otherwise one entry silently grows
        from 'this one unscopable sentence' into 'this phrasing in this file'.
        """
        line = ".claude/skills/sanity-check/SKILL.md:9:`cluster.py` reads nothing reads"
        findings, _, exempted, _soft = check_negative_claims(
            self._runner(line),
            diff="+import cluster.py\n",
            diff_names="analytics/cluster.py",
        )
        assert exempted == 0
        assert len(findings) == 1

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

    def test_handoff_at_the_cap_warns_without_reading_as_over_it(self) -> None:
        """#346: sitting AT the cap used to be silent, which invited shuffling."""
        handoff = "\n".join(f"line {i}" for i in range(HANDOFF_MAX_LINES))
        found = _check_handoff_size(handoff)
        assert len(found) == 1
        assert "within 0 of the cap" in found[0].detail
        assert "prune before adding" not in found[0].detail

    def test_the_first_line_into_the_band_warns(self) -> None:
        floor = HANDOFF_MAX_LINES - HANDOFF_WARN_MARGIN
        handoff = "\n".join(f"line {i}" for i in range(floor + 1))
        found = _check_handoff_size(handoff)
        assert len(found) == 1
        assert f"within {HANDOFF_WARN_MARGIN - 1} of the cap" in found[0].detail

    def test_below_the_band_is_clean(self) -> None:
        """The control for the band: without it, a leg that always warned passes."""
        floor = HANDOFF_MAX_LINES - HANDOFF_WARN_MARGIN
        handoff = "\n".join(f"line {i}" for i in range(floor))
        assert _check_handoff_size(handoff) == []

    def test_absent_handoff_yields_no_finding_here(self) -> None:
        """The size unit has nothing to say; the SKIP is `_handoff_leg`'s call.

        Asserting `[]` here would be the whole defect if this were the last
        word — see :class:`TestHandoffLeg`, which pins that the sweep reports
        SKIPPED rather than green.
        """
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


class TestHandoffLeg:
    """An absent handoff must read SKIPPED, never clean.

    `handoff-size` used to be built as a plain `CheckResult(...)` while its two
    siblings already skipped on the same input, so on a worktree — where the
    handoff is gitignored and therefore absent — the sweep reported the leg
    green. That is the parent's #699 defect mirrored: upstream fails hard-red
    there, wifey failed silent-green, which is the worse direction.
    """

    def test_absent_handoff_skips(self) -> None:
        result = _handoff_leg("handoff-size", "", list)
        assert result.skipped == "no handoff file"
        assert result.findings == []

    def test_absent_handoff_never_runs_the_check(self) -> None:
        """The positive control: the skip precedes measurement.

        A leg that ran its check and happened to find nothing would satisfy the
        test above while still measuring an empty string.
        """
        calls: list[int] = []

        def _never() -> list[Finding]:
            calls.append(1)
            return []

        _handoff_leg("handoff-size", "", _never)
        assert calls == []

    def test_present_handoff_passes_findings_through(self) -> None:
        finding = Finding("handoff-size", "too long")
        result = _handoff_leg("handoff-size", "some text", lambda: [finding])
        assert result.skipped is None
        assert result.findings == [finding]


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


class TestUncoveredSteps:
    """The sweep must not be mistakable for the walk (parent #697).

    Upstream found BOTH sessions of one wave substituting the mechanical half,
    neither being careless, so the fix is reachability rather than another rule.
    """

    def test_notice_names_every_declared_step(self) -> None:
        text = "\n".join(uncovered_notice())
        for name, _ in UNCOVERED_STEPS:
            assert name in text
        assert "MECHANICAL half only" in text
        assert "not passing /post-branch" in text

    def test_render_omits_the_notice_unless_asked(self) -> None:
        lines, _ = render([])
        assert "MECHANICAL half only" not in "\n".join(lines)

    def test_render_appends_the_notice_when_asked(self) -> None:
        lines, _ = render([], show_uncovered=True)
        assert "MECHANICAL half only" in "\n".join(lines)

    def test_every_cited_phase_resolves_in_the_skill(self) -> None:
        """wifey cites `Phase N` where upstream cites `Step N`, and this is why.

        Upstream's phases are table rows declaring no headings, so a phase
        citation there is a dead anchor and its mutation test pins the ABSENCE of
        the word. Here the skill has real `## Phase N` headings and
        `tools/stale_anchors.py` resolves `phase N` against them, so the citation
        is CHECKED — porting upstream's rule verbatim would have swapped a live
        reference for a vague one. This test is what makes that claim falsifiable.
        """
        skill = Path(".claude/skills/post-branch/SKILL.md").read_text(encoding="utf-8")
        headings = {
            line.split("—")[0].strip().lstrip("#").strip().lower()
            for line in skill.splitlines()
            if line.startswith("## ")
        }
        for name, _ in UNCOVERED_STEPS:
            assert name.lower() in headings, f"{name} is not a heading in the skill"

    def test_long_entries_wrap_rather_than_running_wide(self) -> None:
        """One over-wide line drags the whole block sideways in a terminal."""
        assert max(len(line) for line in uncovered_notice()) <= 82

    def test_a_single_check_run_does_not_print_the_notice(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`--text` screens a composed body seconds before a flip; it runs alone.

        The step list is noise at the one moment the operator is triaging under
        time pressure, and `--check` is a deliberate single-leg run.

        ⚠ **`--exit-zero` is load-bearing, and `make preflight` is why.** The
        `--text` leg reads the gitignored `.claude/sensitive-terms.txt`, which
        exists on a developer box and on NO clean clone, where the leg correctly
        reports `NOT CONFIGURED` and `main` returns 1. Asserting `== 0` therefore
        passed locally and failed against a fresh clone — the exact class
        preflight exists to catch. The subject here is the NOTICE, not the exit
        code, so pin the notice and take the code out of the assertion.
        """
        body = tmp_path / "body.md"
        body.write_text("a perfectly ordinary PR body\n", encoding="utf-8")
        assert main(["--text", str(body), "--exit-zero"]) == 0
        assert "MECHANICAL half only" not in capsys.readouterr().out


class TestPlainAbsenceForms:
    """``NEGATIVE_CLAIM_RE`` is an allowlist of phrasings, so it can only catch
    shapes already seen — and the plain "there is no X" / "X has no Y" form, which
    is how absence is normally written, had no entry at all. Measured against the
    signal-timer branch (#261): **8 of 8** claim lines that branch falsified went
    unreported, after 3 misses the run before.

    The fixtures below are those eight lines VERBATIM. A synthetic sentence would
    test the regex against the phrasing its author had in mind while widening it,
    which is the loop that produced the hole — the tree's own emphasis convention
    (``has **no daemon at all**``) is exactly what a hand-written fixture omits.
    """

    FALSIFIED_BY_261 = [
        # .claude/context/analytics.md — emphasis opens mid-phrase
        "catching up simultaneously after a resume from suspend; this fork has "
        "**no daemon at all**,",
        # .claude/context/footguns.md
        "wifey has no daemon. Its budget deliberately does not outlast a "
        "`make db-update` sweep, because a",
        # CLAUDE.md, twice
        "`CATCH_UP=1 make go-live` is the **manual one-shot** dispatch. There is "
        "no wifey signal daemon or",
        "opt-in `wifey-*` systemd user units. Nothing installs them; there is "
        "still no wifey daemon |",
        # README.md
        "Both legs ship **opt-in** systemd user timers — nothing installs them, "
        "and there is still no",
        # deploy/README.md — an adverb sits between "is" and "no"
        "irreplaceable state alive**. There is deliberately no signal-watch "
        "daemon, timer,",
        # deploy/systemd/user/wifey-backup.timer, twice
        "# There is deliberately no quarter-hour dodge here: unlike the parent, "
        "this repo",
        "# has NO signal-watch timer to collide with -- dispatch is the manual "
        "one-shot",
    ]

    @pytest.mark.parametrize("line", FALSIFIED_BY_261)
    def test_a_plain_absence_sentence_counts_as_a_claim(self, line: str) -> None:
        assert NEGATIVE_CLAIM_RE.search(line), line

    def test_emphasis_mid_phrase_does_not_hide_a_claim(self) -> None:
        """The bold marker is the whole reason one of the eight was invisible."""
        assert NEGATIVE_CLAIM_RE.search("wifey has **no daemon**")
        assert NEGATIVE_CLAIM_RE.search("there is **no** wifey daemon")
        assert NEGATIVE_CLAIM_RE.search("there is `no` wifey daemon")

    def test_the_harvested_phrasings_still_match(self) -> None:
        """Positive control: an alternation can only ADD matches.

        The standing warning to re-measure a widening against the pre-#248 tree
        is about the 2026-08-20h SCOPING change, which could REMOVE a match. This
        pins that the widening is not that class of change — the `not ported`
        alternation carried this leg's only true positive and still fires.
        """
        assert NEGATIVE_CLAIM_RE.search("`gate_audit.py` was never ported")
        assert NEGATIVE_CLAIM_RE.search("the exporter is not yet wired")

    def test_ordinary_prose_is_not_swept_in(self) -> None:
        """Negative control. Generic absence words stay OUT of the regex — they
        each pulled double-digit false positives for no catch."""
        for line in (
            "for now the sweep runs weekly",
            "this is a stop-gap until the next refactor",
            "the gate is unwired from the daemon",
        ):
            assert not NEGATIVE_CLAIM_RE.search(line), line


class TestDeployIsInTheAbsenceCorpus:
    """⚠ **The path and the regex widening ship TOGETHER or neither ships.**

    Against the unwidened regex ``deploy/`` surfaced ZERO hits — this tree's
    ``deploy/`` absence claims are all written in the plain form — so the path
    alone was free and worthless. Two of the eight claims #261 falsified sat
    there, out of reach at any regex, which is the half that made it worth doing.
    """

    def test_deploy_is_grepped(self) -> None:
        """Pinned on the argv, not just the constant: the corpus is whatever the
        grep is handed, and a constant nothing reads is not a corpus."""
        seen: list[Sequence[str]] = []

        def run(argv: Sequence[str]) -> str:
            seen.append(argv)
            return ""

        check_negative_claims(run, diff="", diff_names="")
        assert "deploy" in NEGATIVE_CLAIM_PATHS
        assert "deploy" in seen[0]

    def test_a_deploy_claim_the_branch_contradicts_is_reported(self) -> None:
        line = (
            "deploy/README.md:12:There is deliberately no signal-watch daemon or "
            "`wifey-signal-watch.timer` here"
        )

        def run(argv: Sequence[str]) -> str:
            return line

        findings, suppressed, exempted, _soft = check_negative_claims(
            run,
            diff="+Description=wifey signal dispatch\n",
            diff_names="deploy/systemd/user/wifey-signal-watch.timer",
        )
        assert len(findings) == 1
        assert "deploy/README.md" in findings[0].detail
        assert suppressed == 0


class TestPhaseSixHandlesTheNoPrBranch:
    """Phase 4 writes MEMORY.md's bullet with `#NNN` omitted because phase 5 is
    *assumed* to open a PR; phase 6 fills it from a `gh` query. Where the operator
    declines the PR there is no query to fill from, and until now no branch said
    so — the placeholder waited for a fill that could not come. Hit live
    2026-08-25b, where a fake PR reference stood on two surfaces until a human
    caught it.

    Prose tests are the weakest kind, so this one is scoped to a PAIR that must
    co-occur: the step that says FILL must also say what to do with nothing to
    fill from. The `#NNN` assertion is the positive control — without it the
    guard passes vacuously once the fill instruction moves elsewhere.
    """

    SECTION = "### Re-verify PR state"

    def _section(self) -> str:
        skill = Path(".claude/skills/post-branch/SKILL.md").read_text(encoding="utf-8")
        assert skill.count(self.SECTION) == 1
        return skill.split(self.SECTION, 1)[1].split("\n## ", 1)[0]

    def test_the_step_that_fills_NNN_also_handles_having_nothing_to_fill(
        self,
    ) -> None:
        section = self._section()
        assert "#NNN" in section, "positive control: the fill instruction moved"
        assert "No PR opened" in section
        assert "local-only" in section


class TestClaimSubjectScoping:
    """``extract_tokens`` keys on backticked spans, and prose absence claims often
    have none — "this fork has **no daemon at all**" names its subject in bare
    English. Such a line was unscopable and therefore reported on EVERY branch,
    and widening the regex to the plain form took that population from **0 lines
    to 11**, which would have made the leg permanently unclean.

    ⚠ **This is not the token list #250 widened.** That knob scopes claim lines
    IN wholesale; this one gives a previously-unscopable line a way to be scoped
    OUT, so it can only ever REMOVE a report.
    """

    @staticmethod
    def _runner(out: str) -> Runner:
        def run(argv: Sequence[str]) -> str:
            return out

        return run

    CLAIM = "deploy/README.md:9:There is deliberately no signal-watch daemon here"

    def test_the_subject_scopes_the_claim_IN(self) -> None:
        findings, suppressed, _, _soft = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+ExecStart=/usr/bin/make go-live\n",
            diff_names="deploy/systemd/user/wifey-signal-watch.timer",
        )
        assert len(findings) == 1
        assert "signal-watch" in findings[0].detail

    def test_the_subject_scopes_an_unrelated_claim_OUT(self) -> None:
        """The point of the fallback: countable, not reported, not dismissed."""
        findings, suppressed, _, _soft = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def unrelated() -> None:\n",
            diff_names="analytics/other.py",
        )
        assert findings == []
        assert suppressed == 1

    def test_a_stopword_only_subject_still_FAILS_OPEN(self) -> None:
        """Fail-open survives where it is still earned. "nothing installs them"
        names a pronoun, so nothing can rule it out and it must be reported."""
        findings, _, _, _soft = check_negative_claims(
            self._runner("docs/x.md:3:the legs ship — nothing installs them"),
            diff="+anything\n",
            diff_names="other.py",
        )
        assert len(findings) == 1
        assert "no token to scope on" in findings[0].detail

    def test_generic_subject_words_are_not_scope_keys(self) -> None:
        """ "no allowlist FOR orphaned ratings" scoped in on "for" — a word in
        every diff ever written. A subject word must mean something to find."""
        assert claim_subject_tokens("There is no allowlist for orphan ratings") == {
            "allowlist",
            "orphan",
        }
        assert claim_subject_tokens("this fork has **no daemon at all**,") == {"daemon"}

    def test_ALL_markers_on_a_line_must_be_exempt(self) -> None:
        """One line can carry two claims and only one of them be settled.

        #261's README line reads "nothing installs them, and there is still no"
        — exempting the first marker hid the second. Same rule as the token
        exemption: one unexempt hit reports the whole line.
        """
        line = (
            "README.md:901:systemd user timers — nothing installs them, and "
            "there is still no"
        )
        findings, _, exempted, _soft = check_negative_claims(
            self._runner(line), diff="+anything\n", diff_names="x.py"
        )
        assert exempted == 0, "the second, unexempt claim must survive the first"
        assert len(findings) == 1


class TestTheLegIsCleanOnAnUNRELATEDBranch:
    """The property that made the widening shippable, pinned against the real tree.

    A leg that is never clean trains dismissal exactly as a check that is never
    green stops being read — the reason this check was scoped to the branch in
    the first place. So the corpus must contain **zero** claim lines that report
    no matter what the branch did: every one is either scopable, or exempt with a
    reason inline.

    ⚠ This reads the working tree on purpose. The count is a property of the
    DOCS, not of the code, so a future doc edit is exactly what should fail here
    — and the fix is then to scope or exempt that one sentence, never to widen
    ``_SUBJECT_STOP`` until the number goes away.
    """

    def test_no_claim_line_reports_unconditionally(self) -> None:
        findings, _, _, _soft = check_negative_claims(_run, diff="", diff_names="")
        assert findings == [], (
            "these claim lines report on every branch forever: "
            + "; ".join(f.detail for f in findings)
        )


class TestCorpusQueryReachesEveryLine:
    """⚠ The corpus query is the one part of this leg no test could see.

    It read ``git grep -nI -e "x"`` from the #218 extraction until 2026-08-26.
    That is not "every line" — it is *every line containing the letter x*, and
    it silently cut the declared corpus to **195 of CLAUDE.md's 879 non-blank
    lines (22%), and 1,892 of 12,277 tree-wide (15%)**. **46 of the 69
    claim-shaped lines then in the corpus carry no ``x`` at all**,
    ``Makefile``'s "The 505-member research universe has NO scheduled refresher"
    among them — a claim #265 falsified while this leg reported nothing.

    Every other test in this file injects a fake runner, which is exactly why
    the defect survived: the leg was measured, tuned and documented against 15%
    of what its own docstring claimed to read, and the suite stayed green
    throughout. ⚠ **A mocked boundary is not an exercised boundary.**

    The two halves fail differently and are asserted separately: the ARGV, where
    a regression is a one-character edit, and the BEHAVIOUR, where the control
    is a claim line chosen because it contains no ``x``.
    """

    def test_the_corpus_pattern_is_empty_not_a_letter(self) -> None:
        seen: list[Sequence[str]] = []

        def run(argv: Sequence[str]) -> str:
            seen.append(argv)
            return ""

        check_negative_claims(run, diff="", diff_names="")
        assert seen, "the check must query the git surface at all"
        argv = list(seen[0])
        assert "-e" in argv, f"no pattern flag in {argv}"
        assert argv[argv.index("-e") + 1] == "", (
            "a non-empty pattern silently filters the corpus to lines "
            f"containing it, which is the #218 defect: {argv}"
        )

    def test_a_claim_line_carrying_no_letter_x_is_reachable(self) -> None:
        """Positive control: the exact line the old pattern could not see.

        ⚠ The control is void if the fixture ever gains an ``x``, so that is
        asserted first rather than assumed.
        """
        text = "## The 505-member research universe has NO scheduled refresher"
        assert "x" not in text.lower(), "control is vacuous once the line has an x"
        assert NEGATIVE_CLAIM_RE.search(text), (
            "the definite-subject arm must match: an allowlist of repo-self "
            "subjects could not reach 'The 505-member research universe'"
        )

        def run(argv: Sequence[str]) -> str:
            return f"Makefile:588:{text}"

        findings, _suppressed, _exempted, soft = check_negative_claims(
            run,
            diff="+wifey-universe-sync.timer\n",
            diff_names="deploy/systemd/user/wifey-universe-sync.timer",
        )
        # Scoped on `universe`, taken from the subject to the LEFT of "has no"
        # and matched against the timer this branch added. It lands in the
        # re-read note rather than the findings list because the line carries no
        # backticked token, so the hit is inferred from prose rather than stated.
        assert soft == ["Makefile:588"], (findings, soft)

    def test_an_unrelated_branch_does_not_scope_that_line_in(self) -> None:
        """The other half of the control: scoping still has to do its job."""

        def run(argv: Sequence[str]) -> str:
            return (
                "Makefile:588:## The 505-member research universe has NO "
                "scheduled refresher"
            )

        findings, suppressed, _exempted, soft = check_negative_claims(
            run, diff="+def unrelated() -> None:\n", diff_names="analytics/other.py"
        )
        assert findings == [] and soft == []
        assert suppressed == 1


class TestCheckIsRepeatable:
    """`--check` runs EVERY name it is given, and refuses one it does not know.

    It was declared without ``action="append"`` until 2026-09-06 while the
    ``--text`` flag on the next line had it, so ``--check memory-cap --check
    handoff-size`` ran ``handoff-size`` alone and printed a complete-looking
    clean sweep. That is emptiness reading as coverage on exactly the two legs
    `/post-branch` phase 1 tells you to re-read after phase 6.

    A positive control is what makes this falsifiable rather than a re-statement
    of the implementation: the single-name run must NOT print the second leg, or
    the two-name assertion would pass against a tool that ignores the flag
    entirely and runs the whole sweep.
    """

    def test_two_names_run_both_legs(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["--check", "md-atx", "--check", "doc-indexes", "--exit-zero"])
        out = capsys.readouterr().out
        assert "md-atx" in out
        assert "doc-indexes" in out

    def test_one_name_runs_only_that_leg(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The positive control: the filter really is doing the narrowing."""
        main(["--check", "md-atx", "--exit-zero"])
        out = capsys.readouterr().out
        assert "md-atx" in out
        assert "doc-indexes" not in out

    def test_an_unknown_name_aborts_rather_than_shortening_the_run(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A typo among several must not run the survivors and report clean."""
        assert main(["--check", "md-atx", "--check", "no-such-leg"]) == 2
        assert "no such check: no-such-leg" in capsys.readouterr().err

    def test_text_still_refuses_a_repeated_check(self, tmp_path: Path) -> None:
        body = tmp_path / "body.md"
        body.write_text("an ordinary PR body\n", encoding="utf-8")
        assert (
            main(["--text", str(body), "--check", "md-atx", "--check", "doc-indexes"])
            == 2
        )


#: A Makefile shaped like the real one around a target that takes overrides. Line 1 is
#: `.PHONY`, line 2 declares the target, lines 3-5 are its recipe, 7-8 a sibling.
_MAKEFILE = (
    ".PHONY: wifey-backtest\n"
    "wifey-backtest:\n"
    "\t@poetry run python wifey.py backtest\n"
    "\t\t$(if $(SINCE),--since $(SINCE),)\n"
    "\t\t$(if $(SAVE),--save,)\n"
    "\n"
    "other-target:\n"
    "\t@echo hi\n"
)

#: The amendment shape: the recipe gains one override line. No target is ADDED.
_AMEND_DIFF = (
    "@@ -2,3 +2,4 @@\n"
    " wifey-backtest:\n"
    " \t@poetry run python wifey.py backtest\n"
    "-\t\t$(if $(SINCE),--since $(SINCE),)\n"
    "+\t\t$(if $(SINCE),--since $(SINCE),)\n"
    "+\t\t$(if $(SAVE),--save,)\n"
)

_DOCS = {
    "CLAUDE.md": "Wrapped by `make wifey-backtest` (`SINCE=` / `DAY_FILTER=`).",
    "README.md": "make wifey-backtest SAVE=1\n",
    "unrelated.md": "nothing to see",
}


class TestChangedLineNumbers:
    def test_added_lines_are_reported_in_new_file_coordinates(self) -> None:
        assert changed_line_numbers("@@ -1,1 +1,2 @@\n a\n+b\n") == {2}

    def test_a_deletion_blames_the_position_it_vacated(self) -> None:
        """A pure deletion has NO new-file line number of its own.

        Skipping it would make a recipe line REMOVED from a target invisible, which is
        the same amendment this leg exists to catch, arriving as a subtraction instead
        of an addition.
        """
        assert changed_line_numbers("@@ -1,2 +1,1 @@\n a\n-b\n") == {2}

    def test_file_headers_are_not_mistaken_for_added_lines(self) -> None:
        diff = "--- a/Makefile\n+++ b/Makefile\n@@ -1,1 +1,2 @@\n a\n+b\n"
        assert changed_line_numbers(diff) == {2}


class TestTargetsByLine:
    def test_recipe_lines_map_to_their_target(self) -> None:
        mapping = targets_by_line(_MAKEFILE)
        assert mapping[2] == "wifey-backtest"
        assert mapping[5] == "wifey-backtest"
        assert mapping[7] == "other-target"

    def test_phony_is_not_a_target_and_ends_attribution(self) -> None:
        """`.PHONY:` names targets; it is not one, and it is not a recipe."""
        assert targets_by_line(_MAKEFILE).get(1) is None


class TestCheckAmendedTargets:
    def test_an_amended_recipe_fires_and_names_every_doc_to_re_read(self) -> None:
        r"""The regression this leg exists for.

        An override added to an EXISTING target leaves `new-targets` silent — it only
        matches an added `^\+target:` line — while every doc enumerating that target's
        overrides goes one short. A presence check cannot see it: the artifact is there.
        """
        found = check_amended_targets(_AMEND_DIFF, _MAKEFILE, _DOCS)
        assert len(found) == 1
        assert "wifey-backtest" in found[0].detail
        assert "CLAUDE.md" in found[0].detail
        assert "README.md" in found[0].detail
        assert "unrelated.md" not in found[0].detail

    def test_an_added_target_is_left_to_new_targets(self) -> None:
        """No double-reporting: `new-targets` already owns the added case."""
        diff = "@@ -6,0 +7,2 @@\n+other-target:\n+\t@echo hi\n"
        assert check_amended_targets(diff, _MAKEFILE, _DOCS) == []

    def test_a_target_no_doc_names_is_quiet(self) -> None:
        """Nothing can be stale about a target no doc enumerates."""
        diff = "@@ -7,2 +7,2 @@\n other-target:\n-\t@echo hi\n+\t@echo bye\n"
        assert check_amended_targets(diff, _MAKEFILE, _DOCS) == []

    def test_an_empty_diff_is_quiet(self) -> None:
        assert check_amended_targets("", _MAKEFILE, _DOCS) == []

    def test_a_non_recipe_line_is_not_attributed_to_a_target(self) -> None:
        """Editing `.PHONY` is not amending the target's behaviour."""
        diff = "@@ -1,1 +1,1 @@\n-.PHONY: wifey-backtest\n+.PHONY: x\n"
        assert check_amended_targets(diff, _MAKEFILE, _DOCS) == []

    def test_word_boundary_stops_a_substring_doc_hit(self) -> None:
        r"""MUTATION: a longer target name must not credit a shorter one's docs.

        `-` is a non-word character, so a plain `\b` anchor matches INSIDE
        `wifey-backtest-extra`. Upstream's equivalent case failed on first run.
        """
        docs = {"CLAUDE.md": "see `make wifey-backtest-extra` instead"}
        assert check_amended_targets(_AMEND_DIFF, _MAKEFILE, docs) == []


class TestReadEachKeysArePosix:
    def test_a_directory_expands_to_forward_slash_keys(self, tmp_path: Path) -> None:
        """DIVERGENCE from upstream, and the reason is #302's failure class.

        `rglob` yields backslashes on Windows, so keying on `str()` printed
        `.claude\\context\tools.md` beside `negative-claims`' POSIX paths in the same
        report — which is pasted into handoffs and PR bodies. #302 was the sharper
        version: an allowlist keyed on POSIX paths matched nothing once the separators
        diverged, and reported clean while scanning nothing.
        """
        nested = tmp_path / "context"
        nested.mkdir()
        (nested / "tools.md").write_text("make wifey-backtest", encoding="utf-8")

        keys = list(_read_each([str(tmp_path)]))

        assert keys, "the directory expanded to nothing"
        assert all("\\" not in k for k in keys), keys
        assert keys[0].endswith("context/tools.md")


class TestRewrittenDocClaims:
    """#344: a rewrite re-adds every unchanged claim, and each one scoped in on
    its own re-added text (39 findings on #307, none of them new)."""

    MAIN = (
        "# Doc\n\nThe `pead_wiring` module is not yet wired,\n"
        "so nothing reads it today.\n"
    )
    # The same sentence rewrapped, plus rewritten neighbours.
    REWRITE = (
        "# Doc, rewritten\n\nThe `pead_wiring` module is not yet wired, so nothing\n"
        "reads it today.\nA new intro line.\n"
    )

    @staticmethod
    def _diff(path: str, text: str, added: int | None = None) -> str:
        lines = text.splitlines()
        n = len(lines) if added is None else added
        body = "".join(f"+{ln}\n" for ln in lines[:n])
        return f"diff --git a/{path} b/{path}\n+++ b/{path}\n{body}"

    @staticmethod
    def _runner(main_text: str, grep_out: str) -> Runner:
        def run(argv: Sequence[str]) -> str:
            return main_text if list(argv[:2]) == ["git", "show"] else grep_out

        return run

    def _write(self, tmp_path: Path, text: str) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "x.md").write_text(text, encoding="utf-8")

    def test_an_unchanged_claim_in_a_rewrite_is_demoted_and_NAMED(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        self._write(tmp_path, self.REWRITE)
        claim = "docs/x.md:3:The `pead_wiring` module is not yet wired, so nothing"
        diff = self._diff("docs/x.md", self.REWRITE)
        result = _negative_claims_result(
            self._runner(self.MAIN, claim), diff, "docs/x.md"
        )
        assert result.findings == []
        assert result.note is not None
        assert "unchanged from main" in result.note
        assert "docs/x.md:3" in result.note

    def test_a_NEW_claim_in_the_same_rewrite_still_reports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive control: the demotion keys on main's text, not on the file."""
        monkeypatch.chdir(tmp_path)
        text = self.REWRITE.replace("is not yet wired", "is not implemented")
        self._write(tmp_path, text)
        claim = "docs/x.md:3:The `pead_wiring` module is not implemented, so nothing"
        diff = self._diff("docs/x.md", text)
        result = _negative_claims_result(self._runner(self.MAIN, claim), diff, "")
        assert len(result.findings) == 1
        assert "docs/x.md:3" in result.findings[0].detail

    def test_a_lightly_edited_doc_keeps_its_unchanged_claims(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The threshold control: outside a rewrite, an unchanged claim that a
        branch contradicts elsewhere is exactly what the leg exists to report."""
        monkeypatch.chdir(tmp_path)
        text = self.MAIN + "".join(f"filler {i}\n" for i in range(20))
        self._write(tmp_path, text)
        findings = [Finding("negative-claims", "docs/x.md:3:not yet (matched x)")]
        diff = self._diff("docs/x.md", "+def pead_wiring\n", added=1)
        kept, demoted = demote_unchanged_in_rewrites(
            findings, diff, self._runner(self.MAIN, "")
        )
        assert (kept, demoted) == (findings, [])

    def test_a_new_file_has_nothing_on_main_to_be_unchanged_from(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        self._write(tmp_path, self.REWRITE)
        findings = [Finding("negative-claims", "docs/x.md:3:not yet (matched x)")]
        diff = self._diff("docs/x.md", self.REWRITE)
        kept, demoted = demote_unchanged_in_rewrites(
            findings, diff, self._runner("", "")
        )
        assert (kept, demoted) == (findings, [])

    def test_rewritten_files_applies_the_fraction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        self._write(tmp_path, "".join(f"l{i}\n" for i in range(10)))
        heavy = self._diff("docs/x.md", "".join(f"l{i}\n" for i in range(5)))
        light = self._diff("docs/x.md", "".join(f"l{i}\n" for i in range(4)))
        assert rewritten_files(heavy) == {"docs/x.md"}
        assert rewritten_files(light) == set()
