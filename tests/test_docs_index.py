"""Tests for `tools/docs_index.py` — the generated audit + spec indexes.

The load-bearing tests here are the *negative* ones. Table rows, blockquotes,
list items and `**Date:**` metadata lines all sit directly under Verdict
headings in corpora of this shape — the last of those is live in this repo, in
`2026-06-21-experiment-1-residual-xsmom.md` — and every one of them would render
as a plausible-looking but wrong verdict, so `verdict_from_markdown` returning
None is the behaviour worth guarding. Likewise the reconcile fixture carries
three distinguishable specs — reconciled, referenced-but-not-reconciled, and
unreferenced — so a rule that simply returned True (or the reference list) would
fail rather than pass.

`TestInlineVerdictIsReadAsAParagraph` guards the fix this port made to the
upstream tool: the inline `**Verdict: …**` form read one line of an
80-column-wrapped paragraph, which truncated 6 of this repo's 8 readable
verdicts mid-sentence with nothing marking the cut.
"""

from __future__ import annotations

from pathlib import Path

from tools.docs_index import (
    INDEX_NAME,
    build_indexes,
    collect_audits,
    collect_specs,
    date_from_filename,
    main,
    render_audit_index,
    render_spec_index,
    spec_reconcile_audits,
    title_from_markdown,
    verdict_from_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestDateFromFilename:
    def test_reads_the_iso_prefix(self) -> None:
        assert date_from_filename("2026-05-17-adr-exempt.md") == "2026-05-17"

    def test_returns_none_without_a_prefix(self) -> None:
        assert date_from_filename("INDEX.md") is None
        assert date_from_filename("notes.md") is None


class TestTitleFromMarkdown:
    def test_reads_the_first_h1(self) -> None:
        assert (
            title_from_markdown("# H14 — Coinbase premium\n\nbody\n")
            == "H14 — Coinbase premium"
        )

    def test_strips_a_trailing_date_parenthetical(self) -> None:
        text = "# P3 Carry sleeve — funding-carry G-gate verdict (2026-06-19)\n"
        assert title_from_markdown(text) == "P3 Carry sleeve — funding-carry G-gate"

    def test_ignores_a_parenthetical_that_is_not_a_date(self) -> None:
        assert title_from_markdown("# Exit-policy A/B v1 (read-only replay)\n") == (
            "Exit-policy A/B v1 (read-only replay)"
        )

    def test_returns_empty_when_there_is_no_h1(self) -> None:
        assert title_from_markdown("## only a subheading\n") == ""


class TestVerdictFromMarkdown:
    def test_reads_the_inline_bold_form(self) -> None:
        text = "# Carry\n\n**Verdict: FAIL the de-biased gate. No standalone edge in funding carry.**\n"
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        assert verdict.startswith("FAIL the de-biased gate")

    def test_reads_prose_under_a_verdict_heading(self) -> None:
        text = (
            "# Reference-level proximity\n\n"
            "## Verdict\n\n"
            "Live near-level cohort does not clear the de-biased gate — do not build.\n"
        )
        assert verdict_from_markdown(text) == (
            "Live near-level cohort does not clear the de-biased gate — do not build."
        )

    def test_rejects_a_table_row(self) -> None:
        text = "# ST9\n\n## Verdict\n\n| Strategy | TF | Decision | n | baseline avg_r | lift |\n"
        assert verdict_from_markdown(text) is None

    def test_rejects_a_blockquote(self) -> None:
        text = "# P2\n\n## Verdict\n\n> Read-only audit. Engine: `analytics/forecast/`. Driver follows.\n"
        assert verdict_from_markdown(text) is None

    def test_rejects_a_metadata_line(self) -> None:
        text = "# P3 xsmom\n\n## Verdict\n\n**Date:** 2026-06-16 · **Status:** published result\n"
        assert verdict_from_markdown(text) is None

    def test_rejects_a_numbered_list_item(self) -> None:
        text = "# MFE/MAE\n\n## Verdict\n\n1. **Expired often reaches 1R** → CONFIRMED, dominant\n"
        assert verdict_from_markdown(text) is None

    def test_rejects_a_fragment_below_the_length_floor(self) -> None:
        assert verdict_from_markdown("# Doc\n\n## Verdict\n\nNo.\n") is None

    def test_returns_none_when_there_is_no_verdict_section(self) -> None:
        text = "# T6 Phase A — adr_exempt Audit Findings\n\n## Method\n\nSome prose about method.\n"
        assert verdict_from_markdown(text) is None

    def test_stops_at_the_next_heading(self) -> None:
        text = (
            "# Doc\n\n## Verdict\n\n| a | b |\n\n"
            "## Method\n\nThis prose sits under Method and must not be read as the verdict.\n"
        )
        assert verdict_from_markdown(text) is None


class TestInlineVerdictIsReadAsAParagraph:
    """Regression: the inline form read one line of a hard-wrapped paragraph.

    Upstream joined paragraphs under a Verdict *heading* and left the inline
    form line-at-a-time, so a verdict wrapped at ~80 columns published as a
    fragment — and, because the cut fell on a word boundary with no ellipsis, a
    fragment indistinguishable from a complete, shorter verdict.
    """

    def test_joins_the_wrapped_remainder(self) -> None:
        text = (
            "# Exit re-run\n\n"
            "**Verdict: the 2026-06-20 INCONCLUSIVE call is SUPERSEDED at the cohort level, and\n"
            "it resolved in the opposite direction to the thin read that preceded it.**\n"
        )
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        # The clause the one-line read dropped — that the finding REVERSED.
        assert "opposite direction" in verdict

    def test_marks_the_cut_when_the_paragraph_overruns_the_cell(self) -> None:
        text = "# Doc\n\n**Verdict: " + "word " * 60 + "end.**\n"
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        assert verdict.endswith("…")

    def test_accepts_an_equals_separator(self) -> None:
        """`**Verdict = FAIL.**` — the `=` was left in the body as a stray token."""
        text = (
            "# PEAD-lite\n\n"
            "**Verdict = FAIL.** The pre-committed dollar-neutral cell misses the gate\n"
            "on every axis except PBO.\n"
        )
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        assert verdict.startswith("FAIL.")

    def test_stops_at_a_following_table(self) -> None:
        text = (
            "# Doc\n\n"
            "**Verdict: the sleeve fails its pre-registered gate on every axis measured.**\n"
            "| Cell | Sharpe |\n"
        )
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        assert "Sharpe" not in verdict

    def test_stops_at_the_next_bold_metadata_field(self) -> None:
        """Regression on the paragraph join itself, caught on its first run.

        These docs carry `**Date:** / **Verdict:** / **Audit:** / **Spec:**` as
        consecutive lines with no blank between, so markdown calls them one
        paragraph. Joining onward merged the *Audit* field into the verdict —
        turning two clean one-line verdicts into run-on text that attributed a
        make target to the verdict statement.
        """
        text = (
            "# Edge-Hunt #3\n\n"
            "**Date:** 2026-06-23\n"
            "**Verdict:** **FAIL** (pre-registered gate, committed `broad` cell)\n"
            "**Audit:** `make wifey-xasset-audit` (read-only) over a frozen basket.\n"
        )
        assert verdict_from_markdown(text) == (
            "FAIL (pre-registered gate, committed `broad` cell)"
        )

    def test_stops_at_a_following_heading(self) -> None:
        text = (
            "# Doc\n\n"
            "**Verdict: the sleeve fails its pre-registered gate on every axis measured.**\n"
            "## Method\n"
        )
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        assert "Method" not in verdict


class TestCollectAudits:
    def test_skips_the_index_and_undated_files_and_sorts_newest_first(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "2026-01-02-old.md").write_text(
            "# Old audit doc\n", encoding="utf-8"
        )
        (tmp_path / "2026-03-04-new.md").write_text(
            "# New audit doc\n", encoding="utf-8"
        )
        (tmp_path / INDEX_NAME).write_text("# Audit index\n", encoding="utf-8")
        (tmp_path / "scratch.md").write_text("# Undated\n", encoding="utf-8")

        rows = collect_audits(tmp_path)

        assert [r.filename for r in rows] == ["2026-03-04-new.md", "2026-01-02-old.md"]
        assert rows[0].title == "New audit doc"


class TestCollectSpecs:
    """Three distinguishable specs — a rule that returned True for all would fail."""

    def _corpus(self, tmp_path: Path) -> tuple[Path, Path]:
        specs = tmp_path / "specs"
        audits = tmp_path / "audits"
        specs.mkdir()
        audits.mkdir()

        for name in (
            "2026-06-15-reconciled-design.md",
            "2026-06-16-cited-design.md",
            "2026-06-17-orphan-design.md",
            "2026-06-18-f8-gate-design.md",
        ):
            (specs / name).write_text(f"# {name}\n", encoding="utf-8")

        (audits / "2026-08-06-spec-reconcile-run.md").write_text(
            "# Spec-vs-code reconcile\n\nWalked 2026-06-15-reconciled-design.md leg by leg.\n",
            encoding="utf-8",
        )
        (audits / "2026-07-01-plain-audit.md").write_text(
            "# A plain audit\n\nBackground reading: 2026-06-16-cited-design.md.\n",
            encoding="utf-8",
        )
        # The real false positive that the first rule produced: an audit that
        # reconciles two FINDINGS, under a heading in its body, while citing a spec.
        (audits / "2026-06-03-direction-axis-hard-flip.md").write_text(
            "# Direction-axis hard-flip decision doc\n\n"
            "Gate spec: 2026-06-18-f8-gate-design.md.\n\n"
            "## Reconciliation — the asymmetry is regime-contingent, not permanent\n\n"
            "Reconciling the two results rather than the spec against its code.\n",
            encoding="utf-8",
        )
        return specs, audits

    def test_reconciled_spec_is_detected(self, tmp_path: Path) -> None:
        specs, audits = self._corpus(tmp_path)
        rows = {r.filename: r for r in collect_specs(specs, audits)}
        assert rows["2026-06-15-reconciled-design.md"].reconciled_by == (
            "2026-08-06-spec-reconcile-run.md",
        )

    def test_a_plain_reference_is_not_a_reconcile(self, tmp_path: Path) -> None:
        specs, audits = self._corpus(tmp_path)
        rows = {r.filename: r for r in collect_specs(specs, audits)}
        cited = rows["2026-06-16-cited-design.md"]
        assert cited.referenced_by == ("2026-07-01-plain-audit.md",)
        assert cited.reconciled_by == ()

    def test_an_unreferenced_spec_is_neither(self, tmp_path: Path) -> None:
        specs, audits = self._corpus(tmp_path)
        rows = {r.filename: r for r in collect_specs(specs, audits)}
        orphan = rows["2026-06-17-orphan-design.md"]
        assert orphan.referenced_by == ()
        assert orphan.reconciled_by == ()

    def test_a_findings_reconciliation_section_is_not_a_spec_reconcile(
        self, tmp_path: Path
    ) -> None:
        """Reconcile status must key on the audit's own title, not on 'reconcil'
        anywhere in the body.

        Keying on the body mislabels `2026-06-03-direction-axis-hard-flip.md`, whose body has a
        `## Reconciliation` section about two findings, as reconciling the F8 gate
        spec.
        """
        specs, audits = self._corpus(tmp_path)
        rows = {r.filename: r for r in collect_specs(specs, audits)}
        gate = rows["2026-06-18-f8-gate-design.md"]
        assert gate.referenced_by == ("2026-06-03-direction-axis-hard-flip.md",)
        assert gate.reconciled_by == ()


class TestRender:
    def test_escapes_a_pipe_in_a_title(self, tmp_path: Path) -> None:
        (tmp_path / "2026-01-01-piped.md").write_text(
            "# A | B split\n", encoding="utf-8"
        )
        rendered = render_audit_index(collect_audits(tmp_path))
        assert "A \\| B split" in rendered

    def test_states_verdict_coverage_rather_than_implying_completeness(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "2026-01-01-a.md").write_text(
            "# With verdict\n\n## Verdict\n\nThis one states its verdict as a readable sentence.\n",
            encoding="utf-8",
        )
        (tmp_path / "2026-01-02-b.md").write_text(
            "# Without verdict\n", encoding="utf-8"
        )
        rendered = render_audit_index(collect_audits(tmp_path))
        assert "**1 of 2**" in rendered


class TestReconcileColumnStatesItsOwnFloor:
    """`0 of N` renders identically whether or not the detector could ever fire.

    This repo has no spec-reconcile audit, so the column's only reachable value
    is 0 — publishing that bare would read as "N specs went unreconciled", a
    claim about the specs that the data cannot support.
    """

    def test_repo_corpus_has_no_spec_reconcile_audit(self) -> None:
        assert spec_reconcile_audits(REPO_ROOT / "docs/audits") == ()

    def test_zero_state_says_the_value_is_forced(self, tmp_path: Path) -> None:
        (tmp_path / "2026-01-01-a-design.md").write_text("# A spec\n", encoding="utf-8")
        rendered = render_spec_index(collect_specs(tmp_path, tmp_path), ())
        assert "only value this" in rendered
        assert "went unreconciled" in rendered

    def test_non_zero_state_drops_the_caveat(self, tmp_path: Path) -> None:
        specs, audits = tmp_path / "specs", tmp_path / "audits"
        specs.mkdir()
        audits.mkdir()
        (specs / "2026-01-01-a-design.md").write_text("# A spec\n", encoding="utf-8")
        (audits / "2026-02-02-spec-reconcile-run.md").write_text(
            "# Spec-vs-code reconcile\n\nWalked 2026-01-01-a-design.md leg by leg.\n",
            encoding="utf-8",
        )
        rendered = render_spec_index(
            collect_specs(specs, audits), spec_reconcile_audits(audits)
        )
        assert "**Reconciled (derived): 1 of 1**" in rendered
        assert "only value this" not in rendered


class TestMainCheckMode:
    def test_check_fails_when_missing_then_passes_after_a_write(
        self, tmp_path: Path
    ) -> None:
        specs = tmp_path / "specs"
        audits = tmp_path / "audits"
        specs.mkdir()
        audits.mkdir()
        (audits / "2026-01-01-a.md").write_text("# An audit\n", encoding="utf-8")
        (specs / "2026-01-01-s-design.md").write_text("# A spec\n", encoding="utf-8")
        argv = ["--audit-dir", str(audits), "--spec-dir", str(specs)]

        assert main([*argv, "--check"]) == 1
        assert main(argv) == 0
        assert main([*argv, "--check"]) == 0

    def test_check_fails_once_a_new_doc_lands(self, tmp_path: Path) -> None:
        specs = tmp_path / "specs"
        audits = tmp_path / "audits"
        specs.mkdir()
        audits.mkdir()
        (audits / "2026-01-01-a.md").write_text("# An audit\n", encoding="utf-8")
        argv = ["--audit-dir", str(audits), "--spec-dir", str(specs)]
        assert main(argv) == 0

        (audits / "2026-02-02-b.md").write_text("# A later audit\n", encoding="utf-8")
        assert main([*argv, "--check"]) == 1


class TestCommittedIndexesAreCurrent:
    """The guard that makes drift loud — a new audit or spec fails CI until indexed.

    Without it the index is the silent-surface class this repo keeps hitting
    (CLAUDE.md → Testing): declared, executed by nothing, asserted about by
    nothing, and so indistinguishable from a working one while it rots.
    """

    def test_indexes_match_the_corpus(self) -> None:
        expected = build_indexes(
            REPO_ROOT / "docs/audits", REPO_ROOT / "docs/superpowers/specs"
        )
        for path, content in expected.items():
            assert path.exists(), f"{path} is missing — run: make docs-index"
            assert path.read_text(encoding="utf-8") == content, (
                f"{path} is stale — run: make docs-index"
            )


class TestWrappedProse:
    """Regression: this corpus hard-wraps at ~80 columns.

    Reading a single line out of a Verdict section produced mid-sentence
    fragments in the first generated index — "(`DSR >= 0.95 …`). This lands on
    §8's third row:" — which reads as a truncated verdict rather than an opening.
    """

    def test_joins_a_wrapped_paragraph(self) -> None:
        text = (
            "# Doc\n\n## Verdict\n\n"
            "The sleeve fails the de-biased gate on every one of the ten\n"
            "pre-registered trials, and the failure is missing signal.\n"
        )
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        assert "ten pre-registered trials" in verdict

    def test_a_wrapped_table_is_still_rejected(self) -> None:
        text = "# Doc\n\n## Verdict\n\n| Strategy | TF |\n| --- | --- |\n| bos | 1d |\n"
        assert verdict_from_markdown(text) is None


class TestTitleTrailingLabel:
    def test_strips_the_colon_left_by_a_verdict_label(self) -> None:
        assert title_from_markdown(
            "# D1 — Spot-perp CVD divergence sleeve: VERDICT\n"
        ) == ("D1 — Spot-perp CVD divergence sleeve")


# Frozen 2026-08-21: audits written before the parseable-verdict rule existed;
# each states its verdict in a table, a blockquote or the body, where
# `verdict_from_markdown` cannot read it. **This set may only shrink** — the
# ratchet below fails if an entry is fixed or deleted without being removed
# here, so it cannot quietly re-admit blindness.
#
# They are grandfathered rather than retrofitted on purpose: SEVEN of the ten are
# cited by name in CLAUDE.md's footguns (verified by grepping each filename), so
# their consequences have already shipped and rewriting their verdict sections
# would be churn against settled decisions with a real chance of misstating one.
# The other three are the residual-xsmom experiment, the crypto-era `bos`
# direction flag and the orphaned-ratings audit — all equally settled, none
# reachable by that grep.
VERDICT_RATCHET_GRANDFATHERED = frozenset(
    {
        "2026-06-21-experiment-1-residual-xsmom.md",
        "2026-06-24-honest-exit-free-data-edge-arc.md",
        "2026-08-06-adr-gate-timeframe-degeneracy.md",
        "2026-08-06-bos-timeframe-and-crypto-era-direction-flag.md",
        "2026-08-06-live-ev-gate-window.md",
        "2026-08-06-orphaned-and-contaminated-confidence-ratings.md",
        "2026-08-07-backtest-runs-writer-collision.md",
        "2026-08-07-ev-gate-directional-sample-guard.md",
        "2026-08-07-ev-gate-significance-test.md",
        "2026-08-07-live-parity-ratings-sweep.md",
    }
)


class TestEveryNewAuditExposesItsVerdict:
    """A new audit must state its verdict where a machine can read it.

    This repo already holds the rule as prose — every audit closes with FOUND /
    BOUNDED / EXCLUDED / BLOCKED — and prose does not enforce. The consumer that
    makes it mechanical is the generated `docs/audits/INDEX.md`: it renders each
    audit's verdict in a column, so an unparseable verdict shows up as an empty
    cell that reads exactly like an audit which reached no conclusion.

    Ported from the parent's #641 without its second half. Upstream pairs this
    with a gitignored `daily_check.py` line joining the index against the SoT to
    ask whether an actionable verdict has an owner. This gate buys legibility
    only, and is not an ownership check: `cadence_check.py` holds the
    verdict-to-owner join (`tests/test_cadence_check.py::TestVerdictJoin`), and
    the human running `/post-branch`'s SoT reconcile owns the rest.

    It deliberately asserts nothing about verdict CONTENT. The only property
    ownable here is that the verdict is legible at all.
    """

    def test_a_new_audit_states_a_parseable_verdict(self) -> None:
        rows = collect_audits(REPO_ROOT / "docs/audits")
        unreadable = sorted(
            r.filename
            for r in rows
            if r.verdict is None and r.filename not in VERDICT_RATCHET_GRANDFATHERED
        )
        assert not unreadable, (
            "these audits state no verdict a machine can read, so the generated "
            f"INDEX.md renders them as having reached none: {unreadable}. Add a "
            "'## Headline verdict: ...' section stating the verdict as PROSE (a "
            "table, blockquote or **Date:** line under the heading is "
            "deliberately rejected — see TestVerdictFromMarkdown)."
        )

    def test_the_grandfather_set_can_only_shrink(self) -> None:
        rows = {r.filename: r for r in collect_audits(REPO_ROOT / "docs/audits")}
        deleted = sorted(VERDICT_RATCHET_GRANDFATHERED - rows.keys())
        assert not deleted, (
            f"grandfathered audits no longer exist: {deleted}. "
            "Remove them from VERDICT_RATCHET_GRANDFATHERED."
        )
        fixed = sorted(
            f
            for f in VERDICT_RATCHET_GRANDFATHERED
            if rows[f].verdict is not None  # now readable — the exemption is spent
        )
        assert not fixed, (
            f"these audits now state a parseable verdict: {fixed}. "
            "Remove them from VERDICT_RATCHET_GRANDFATHERED — a stale exemption "
            "silently re-admits the blind spot it was granted for."
        )
