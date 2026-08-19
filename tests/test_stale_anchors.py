"""Tests for the `stale-anchors` check.

Every leg ships a **positive control** — an input that makes it fire. Two of
them are there because the real corpus falsified an assumption during the
build, and the test is what pins the corrected behaviour:

* the ordered-list fallback is **conditional**, because `/post-branch` says
  "Phases, not step numbers" yet carries three column-0 rubric lists numbered
  1..5. Harvesting those unconditionally validated every dead
  `/post-branch Step N` citation — the exact defect the check exists to catch.
* typed kinds must **disagree**. A label-only comparison calls `Step 6` and
  `Phase 6` a match, which is precisely the rename that keeps breaking.

:class:`TestKnownHoles` names what the check cannot see. Per CLAUDE.md, a
characterization test naming a known hole beats a test asserting a protection
that was never constructed.
"""

from __future__ import annotations

from pathlib import Path

from tools.stale_anchors import (
    UNTYPED,
    Citation,
    _quoted_spans,
    anchor_matches,
    citations,
    declared_anchors,
    default_resolver,
    describe,
    is_dated_path,
    scan,
)


class TestDeclaredAnchors:
    def test_typed_headings(self) -> None:
        md = "## Phase 6 — Pre-merge\n## Step 2 — Gate\n"
        assert declared_anchors(md) == {("phase", "6"), ("step", "2")}

    def test_untyped_numbered_headings(self) -> None:
        md = "## 4. Current measured edge\n### 4a. Live alert ledger\n"
        assert declared_anchors(md) == {(UNTYPED, "4"), (UNTYPED, "4a")}

    def test_bold_lead_in_substep(self) -> None:
        """`/ingest-video` numbers 7a/7b/7c this way and the SoT cites them."""
        md = "## 7. Digest\n**7b — The board view.** Cross-video synthesis.\n"
        assert (UNTYPED, "7b") in declared_anchors(md)

    def test_ordered_list_fallback_when_no_numbered_heading(self) -> None:
        """`/db-update` and `/ingest-x` number themselves through their list."""
        md = "## After the chain\n\n1. **Read the banner.**\n3. **Prove it.**\n"
        assert declared_anchors(md) == {("step", "1"), ("step", "3")}

    def test_ordered_list_fallback_is_suppressed_by_numbered_headings(self) -> None:
        """The post-branch class: rubric lists must not manufacture steps."""
        md = "## Phase 1 — Sweep\n\n1. **A rubric item.**\n2. **Another.**\n"
        assert declared_anchors(md) == {("phase", "1")}

    def test_prose_bold_declares_nothing(self) -> None:
        assert declared_anchors("**Fix: stamp last** and re-read.\n") == set()

    def test_filename_heading_is_not_an_anchor(self) -> None:
        assert declared_anchors("## 001_day_filter_text.py — BOOLEAN\n") == set()


class TestAnchorMatches:
    def test_typed_kinds_must_agree(self) -> None:
        """The recurring rename. A label-only comparison would pass this."""
        assert not anchor_matches("step", "6", {("phase", "6")})

    def test_untyped_citation_matches_any_kind(self) -> None:
        assert anchor_matches(UNTYPED, "6", {("phase", "6")})

    def test_untyped_declaration_matches_a_typed_citation(self) -> None:
        assert anchor_matches("step", "2", {(UNTYPED, "2")})

    def test_label_must_match(self) -> None:
        assert not anchor_matches(UNTYPED, "4a", {("phase", "4")})


class TestCitations:
    def test_adjacent_typed_anchor(self) -> None:
        found = citations("see `/post-branch` phase 6 for the rule\n", "a.md")
        assert [(c.target, c.kind, c.label) for c in found] == [
            ("post-branch", "phase", "6")
        ]

    def test_wikilink_target(self) -> None:
        found = citations("[[project_open_questions]] §0 holds it\n", "a.md")
        assert [(c.target, c.kind, c.label) for c in found] == [
            ("project_open_questions", UNTYPED, "0")
        ]

    def test_typed_anchor_does_not_cross_a_sentence_boundary(self) -> None:
        """ "Phase 3" is ordinary prose about `run_scan_cycle`, not a citation."""
        line = "`/ingest-x` is the sibling. Phase 3 of the scanner does the rest\n"
        assert citations(line, "a.md") == []

    def test_untyped_anchor_may_cross_a_sentence_boundary(self) -> None:
        """`§` is never ordinary prose, so it gets the wider window."""
        line = "`docs/system-overview.md` is the best read. §9 is safe to send\n"
        assert [c.label for c in citations(line, "a.md")] == ["9"]

    def test_quoted_anchor_is_a_mention_not_a_citation(self) -> None:
        """MEMORY.md reporting `CLAUDE.md still cited "§4a"` is not a citation."""
        assert citations('CLAUDE.md still cited "§4a" after the rename\n', "a.md") == []

    def test_anchor_beyond_the_window_is_not_attributed(self) -> None:
        line = "`/post-branch` " + ("filler words " * 12) + "step 3\n"
        assert citations(line, "a.md") == []


class TestIsDatedPath:
    def test_dated_directory_is_excluded(self) -> None:
        assert is_dated_path("docs/audits/2026-08-14-exit-policy-ab-v1.md")

    def test_dated_memory_basename_is_excluded(self) -> None:
        assert is_dated_path("memory/project_session_log_2026-08.md")

    def test_current_state_surface_is_not_excluded(self) -> None:
        """Vacuity control: the exclusion must not swallow the live tree."""
        assert not is_dated_path("CLAUDE.md")
        assert not is_dated_path(".claude/skills/post-branch/SKILL.md")


def _tree(root: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


class TestScan:
    def test_dead_citation_is_reported(self, tmp_path: Path) -> None:
        _tree(
            tmp_path,
            {
                ".claude/skills/post-branch/SKILL.md": "## Phase 6 — Pre-merge\n",
                "CLAUDE.md": "Invoke `/post-branch` Step 10b before the PR.\n",
            },
        )
        dead = scan(
            [tmp_path / "CLAUDE.md"],
            default_resolver(tmp_path),
            root=tmp_path,
        )
        assert [(c.target, c.kind, c.label) for c, _ in dead] == [
            ("post-branch", "step", "10b")
        ]

    def test_live_citation_is_silent(self, tmp_path: Path) -> None:
        """Negative control: the check must be silent when the anchor exists."""
        _tree(
            tmp_path,
            {
                ".claude/skills/post-branch/SKILL.md": "## Phase 6 — Pre-merge\n",
                "CLAUDE.md": "Invoke `/post-branch` phase 6 before the PR.\n",
            },
        )
        assert scan([tmp_path / "CLAUDE.md"], default_resolver(tmp_path)) == []

    def test_dated_source_is_skipped(self, tmp_path: Path) -> None:
        _tree(
            tmp_path,
            {
                ".claude/skills/post-branch/SKILL.md": "## Phase 6 — Pre-merge\n",
                "docs/audits/2026-08-01-x.md": "`/post-branch` Step 10b was run.\n",
            },
        )
        src = tmp_path / "docs/audits/2026-08-01-x.md"
        assert scan([src], default_resolver(tmp_path), root=tmp_path) == []

    def test_unresolvable_target_is_not_guessed(self, tmp_path: Path) -> None:
        _tree(tmp_path, {"CLAUDE.md": "`/no-such-skill` Step 3 does the thing.\n"})
        assert scan([tmp_path / "CLAUDE.md"], default_resolver(tmp_path)) == []

    def test_memory_topic_target_resolves(self, tmp_path: Path) -> None:
        """Two of the four live instances were in the memory tree."""
        mem = tmp_path / "memory"
        _tree(tmp_path, {"memory/notes.md": "## Phase 2 — later\n"})
        _tree(tmp_path, {"CLAUDE.md": "See [[notes]] phase 9 for detail.\n"})
        dead = scan(
            [tmp_path / "CLAUDE.md"], default_resolver(tmp_path, mem), root=tmp_path
        )
        assert [c.label for c, _ in dead] == ["9"]


class TestDescribe:
    def test_names_source_anchor_and_target(self) -> None:
        cite = Citation("CLAUDE.md", 7, "post-branch", "step", "10b", "ctx")
        line = describe(cite, Path(".claude/skills/post-branch/SKILL.md"))
        assert "CLAUDE.md:7" in line
        assert "step 10b" in line
        assert "post-branch/SKILL.md" in line


class TestKnownHoles:
    """What the check cannot see. Named rather than asserted as protection."""

    def test_self_labelling_next_to_a_target_is_indistinguishable(self) -> None:
        """`/ingest-x` writes "handed to `/ingest-video` (step 1b)".

        1b is `/ingest-x`'s OWN sub-step, but nothing syntactic separates that
        from a citation of `/ingest-video`'s step 1b. Suppressing it by
        "the source declares this anchor too" was tried and **dropped a real
        cross-doc finding**, because documents share small integers. So it is
        reported, and the glance is the cost.
        """
        line = "handed to `/ingest-video` (step 1b), not skipped\n"
        assert [c.target for c in citations(line, "a.md")] == ["ingest-video"]

    def test_anchor_before_its_target_is_invisible(self) -> None:
        """Only target-then-anchor is read. "phase 6 of `/post-branch`" is not."""
        assert citations("phase 6 of `/post-branch` covers it\n", "a.md") == []


class TestQuotedSpans:
    """`_is_quoted` tests the enclosing SPAN, not the two adjacent characters.

    The adjacent-character form carried one bug in each direction, and they were
    only reachable from opposite ends — which is why neither was found by
    triaging findings alone.
    """

    def test_phrase_level_quotation_suppresses(self) -> None:
        """A quotation wrapping TARGET plus ANCHOR is a mention, not a use.

        The old form read only the character before the anchor — a space here —
        so this reported as a live citation. It is the false positive that fired
        on this repo's own prose describing the defect.
        """
        line = 'so "wifey\'s `/post-branch` Step 5c" resolves against the wrong tree'
        assert citations(line, "doc.md") == []

    def test_unterminated_quotation_does_NOT_suppress(self) -> None:
        """The false negative: an anchor ending the line was silently dropped.

        `_QUOTES` is a `str`, so `ch in _QUOTES` was a substring test and
        ``"" in _QUOTES`` is `True`. An opening quote plus end-of-line — what a
        quoted phrase looks like when markdown wraps it — short-circuited to
        "quoted". Reporting is the correct direction: a false positive costs a
        glance, a suppressed citation is invisible.
        """
        cites = citations('`spec.md` said "§4', "doc.md")
        assert [(c.target, c.label) for c in cites] == [("spec.md", "4")]

    def test_genuine_quotation_still_suppresses(self) -> None:
        """Positive control for the suppression path itself.

        The anchor is wrapped directly, which the adjacent-character form also
        caught — so this pins the behaviour that must SURVIVE the rewrite.
        Without it, every test above is equally satisfied by a `_is_quoted` that
        never returns True at all.
        """
        assert citations('`spec.md` still says "§4a" today', "d.md") == []

    def test_unquoted_citation_is_reported(self) -> None:
        cites = citations("see `spec.md` §4 for the rule", "doc.md")
        assert [(c.target, c.label) for c in cites] == [("spec.md", "4")]

    def test_curly_quotes_pair_with_their_own_closer(self) -> None:
        assert citations("the doc said “`spec.md` §4” once", "d.md") == []

    def test_spans_are_closed_only(self) -> None:
        assert _quoted_spans('a "b" c') == [(2, 4)]
        assert _quoted_spans('a "b c') == []


class TestDecimalIsNotASection:
    """A decimal number is not a section declaration.

    `**4.9 min**` declared `section 4`. The damage is not the spurious entry:
    `declared_anchors` disables the ordered-list fallback the moment ANY
    declaration exists, so one decimal blinded the check to every genuine
    ordered-list anchor in that file.
    """

    def test_bold_decimal_declares_nothing(self) -> None:
        assert declared_anchors("**4.9 min** of runtime\n") == set()

    def test_decimal_heading_declares_nothing(self) -> None:
        assert declared_anchors("### 12.5 GB of fixtures\n") == set()

    def test_real_numbered_heading_still_declares(self) -> None:
        """Positive control: the lookahead must not blind the leg."""
        assert declared_anchors("## 4. Digest rubric\n") == {(UNTYPED, "4")}

    def test_bold_lead_in_substep_survives(self) -> None:
        """The `7b` form `/ingest-video` uses, which the SoT cites."""
        assert (UNTYPED, "7b") in declared_anchors("**7b — The board view.** x\n")

    def test_a_decimal_no_longer_blinds_the_ordered_list_fallback(self) -> None:
        """The whole point: one decimal used to suppress every real anchor."""
        md = "**4.9 min** to run\n\n1. **Read the banner.**\n2. **Prove it.**\n"
        assert declared_anchors(md) == {("step", "1"), ("step", "2")}
