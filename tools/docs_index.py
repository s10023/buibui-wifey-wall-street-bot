"""Generate browsable indexes for the audit and spec corpora.

`docs/audits/` (18 verdicts) and `docs/superpowers/specs/` (14 specs) are 32
documents that **nothing indexed**. CLAUDE.md cites 9 of the 18 audits inline, on
the footgun and sleeve-verdict entries they support; the other 9, and 13 of the
14 specs, had no surface listing them at all.

Ported from parent PR #600. Two rules shape the extraction, and both exist to
avoid making the index a new source of confident-sounding wrong claims:

- **Nothing is guessed.** Date and title come from the filename and the H1, which
  are 100% reliable across both corpora. A verdict line is emitted ONLY when it
  can be read out of a Verdict heading as prose; every other row gets an em dash,
  and the index header states the coverage out loud. A keyword-guessed verdict
  column would be strictly worse than none in a repo whose standing lesson is
  that a filed artifact is a hypothesis.
- **Reconcile status is DERIVED, not remembered.** A spec counts as reconciled
  when a *spec-reconcile audit* — one that says so in its filename or its H1 —
  references the spec's filename, so the count is recomputed from disk on every
  run rather than carried in prose. When this disagrees with a number written in
  CLAUDE.md, the disagreement is the finding — do not "fix" it by editing the
  generator. **The looser "reconcile appears anywhere in the audit body" rule was
  tried first and produced a false positive on its first run**: an audit whose
  body carries a `## Reconciliation` section about reconciling two *findings*, not
  a spec against its code. Matching the audit's own title is what separates them.
  This corpus currently holds **no** spec-reconcile audit at all, so the derived
  count can only be zero, and `render_spec_index` says so on the page rather than
  publishing a bare `0 of 14` that reads as "14 specs went unreconciled".

**A wrapped verdict is read as a whole paragraph in BOTH forms.** Upstream joined
paragraphs under a Verdict *heading* but left the inline `**Verdict: …**` form
reading a single line. This corpus hard-wraps at ~80 columns, so that published
mid-sentence fragments for **6 of the 8** verdicts it can read — including one cut
at "…at the cohort level, and", which drops the very clause recording that the
finding reversed direction. The same defect is live upstream (2 of its 4 inline
verdicts); raise it there.

Output is **deterministic** — no generated-at timestamp — because
`--check` compares the committed file byte-for-byte and a clock would make it
fail on every run.

Run via:

    poetry run python tools/docs_index.py           # write both indexes
    poetry run python tools/docs_index.py --check    # exit 1 if stale (CI/test)
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

AUDIT_DIR = Path("docs/audits")
SPEC_DIR = Path("docs/superpowers/specs")
INDEX_NAME = "INDEX.md"

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
_H1 = re.compile(r"^#\s+(.*\S)\s*$")
_HEADING = re.compile(r"^#{1,6}\s")
_VERDICT_HEADING = re.compile(r"^#{1,6}\s+.*\bverdicts?\b", re.IGNORECASE)
_VERDICT_INLINE = re.compile(
    r"^\*\*verdicts?\b[:=\s]*\*{0,2}\s*(?P<body>.*)$", re.IGNORECASE
)
_TRAILING_YEAR_PAREN = re.compile(r"\s*\((?:19|20)\d{2}-\d{2}-\d{2}[^)]*\)\s*$")
_TRAILING_VERDICT = re.compile(r"\s*(?:[—\-–]\s*)?\(?verdict\)?\s*$", re.IGNORECASE)
_METADATA_PREFIX = re.compile(
    r"^\*\*(?:date|status|question|headline|parent|engine)\b", re.IGNORECASE
)
# `**Audit:**`, `**Spec:**` — a new bold field, not a wrapped continuation.
_BOLD_FIELD = re.compile(r"^\*\*[^*]+\*\*")

# A verdict snippet shorter than this is a fragment (a stray bold label, a table
# cell) rather than a readable statement, so it is dropped in favour of an em dash.
MIN_VERDICT_CHARS = 40
MAX_VERDICT_CHARS = 130


@dataclass(frozen=True)
class AuditRow:
    """One `docs/audits/*.md` verdict document."""

    date: str
    title: str
    verdict: str | None
    filename: str


@dataclass(frozen=True)
class SpecRow:
    """One `docs/superpowers/specs/*.md` design document.

    `reconciled_by` holds audits that both reference this spec's filename and
    talk about reconciling; `referenced_by` holds every audit that names it.
    """

    date: str
    title: str
    reconciled_by: tuple[str, ...]
    referenced_by: tuple[str, ...]
    filename: str


def date_from_filename(name: str) -> str | None:
    """Return the `YYYY-MM-DD` prefix of a doc filename, or None if absent."""
    match = _DATE_PREFIX.match(name)
    return match.group(1) if match else None


def title_from_markdown(text: str) -> str:
    """First H1, with a trailing date parenthetical and a trailing "verdict" removed."""
    for line in text.splitlines():
        match = _H1.match(line)
        if match:
            title = _TRAILING_YEAR_PAREN.sub("", match.group(1))
            title = _TRAILING_VERDICT.sub("", title)
            # "…sleeve: VERDICT" leaves a dangling colon once the label is gone.
            title = title.rstrip(" :—–-")
            return title.strip() or match.group(1)
    return ""


def _clean_snippet(line: str) -> str:
    """Strip emphasis markers and collapse whitespace into one readable sentence."""
    snippet = line.strip().replace("**", "").replace("__", "")
    snippet = re.sub(r"\s+", " ", snippet).strip()
    if len(snippet) > MAX_VERDICT_CHARS:
        cut = snippet[:MAX_VERDICT_CHARS].rsplit(" ", 1)[0]
        snippet = f"{cut}…"
    return snippet


def _starts_prose(line: str) -> bool:
    """True when a line opens a statement rather than structure or metadata.

    Table rows, blockquotes, list items, headings and `**Date:**`-style metadata
    lines all sit directly under Verdict headings somewhere in this corpus, and
    every one of them would render as a plausible-looking but wrong verdict.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped[0] in "|>-*+#":
        return False
    if re.match(r"^\d+[.)]\s", stripped):
        return False
    return not _METADATA_PREFIX.match(stripped)


def _paragraph_snippet(paragraph: list[str]) -> str | None:
    """Join a wrapped paragraph into one snippet, or None if it is not prose.

    Whole paragraphs, never single lines: this corpus hard-wraps at ~80 columns,
    so taking one line yields a mid-sentence fragment that reads as a truncated
    verdict rather than an opening.
    """
    if not paragraph or not _starts_prose(paragraph[0]):
        return None
    body = [paragraph[0]]
    for line in paragraph[1:]:
        if not _continues_paragraph(line):
            break
        body.append(line)
    snippet = _clean_snippet(" ".join(line.strip() for line in body))
    return snippet if len(snippet) >= MIN_VERDICT_CHARS else None


def _continues_paragraph(line: str) -> bool:
    """True when a line is more of the paragraph above rather than a new block.

    A line opening a **bold field label** ends the paragraph even though markdown
    would treat it as the same one: these docs carry `**Date:** … **Verdict:** …
    **Audit:** …` as consecutive unseparated lines, so joining onward from the
    verdict merges a DIFFERENT field into it. Erring toward stopping early costs a
    few words; erring the other way fabricates a verdict out of adjacent metadata.
    """
    stripped = line.strip()
    if not stripped or _HEADING.match(line):
        return False
    if stripped[0] in "|>":
        return False
    if _BOLD_FIELD.match(stripped):
        return False
    return not re.match(r"^(?:[-*+]\s|\d+[.)]\s)", stripped)


def verdict_from_markdown(text: str) -> str | None:
    """Read a verdict statement out of a doc, or None when none is unambiguous.

    Prefers the inline `**Verdict: ...**` form, then the first prose paragraph
    under a Verdict heading. Returns None rather than guessing.
    """
    lines = text.splitlines()

    for index, line in enumerate(lines):
        match = _VERDICT_INLINE.match(line.strip())
        if not match:
            continue
        # Read to the end of the wrapped paragraph, never one line: this corpus
        # hard-wraps at ~80 columns, so a single line stops mid-sentence and the
        # truncation is invisible — it reads as a complete, shorter verdict.
        parts = [match.group("body")]
        for follow in lines[index + 1 :]:
            if not _continues_paragraph(follow):
                break
            parts.append(follow.strip())
        body = _clean_snippet(" ".join(parts))
        if len(body) >= MIN_VERDICT_CHARS:
            return body

    in_section = False
    paragraph: list[str] = []
    for line in lines:
        if _VERDICT_HEADING.match(line):
            in_section, paragraph = True, []
            continue
        if not in_section:
            continue
        if _HEADING.match(line):
            found = _paragraph_snippet(paragraph)
            if found:
                return found
            in_section, paragraph = False, []
            continue
        if line.strip():
            paragraph.append(line)
            continue
        found = _paragraph_snippet(paragraph)
        if found:
            return found
        paragraph = []
    return _paragraph_snippet(paragraph)


def collect_audits(audit_dir: Path) -> list[AuditRow]:
    """Every dated audit doc in `audit_dir`, newest first."""
    rows: list[AuditRow] = []
    for path in sorted(audit_dir.glob("*.md")):
        if path.name == INDEX_NAME:
            continue
        date = date_from_filename(path.name)
        if date is None:
            continue
        text = path.read_text(encoding="utf-8")
        rows.append(
            AuditRow(
                date=date,
                title=title_from_markdown(text) or path.stem,
                verdict=verdict_from_markdown(text),
                filename=path.name,
            )
        )
    return sorted(rows, key=lambda r: (r.date, r.filename), reverse=True)


def is_spec_reconcile_audit(filename: str, text: str) -> bool:
    """True when an audit is a spec-vs-code reconcile, judged by its own title.

    Deliberately NOT "the word reconcile appears in the body": several audits
    reconcile two *findings* under a `## Reconciliation` heading, which is a
    different act entirely, and that looser rule mislabelled one on its first run.
    """
    if "spec-reconcile" in filename.lower():
        return True
    return "reconcil" in title_from_markdown(text).lower()


def collect_specs(spec_dir: Path, audit_dir: Path) -> list[SpecRow]:
    """Every dated spec doc, cross-linked to the audits that reference it."""
    audits: list[tuple[str, str, bool]] = []
    for path in sorted(audit_dir.glob("*.md")):
        if path.name == INDEX_NAME:
            continue
        body = path.read_text(encoding="utf-8")
        audits.append((path.name, body, is_spec_reconcile_audit(path.name, body)))

    rows: list[SpecRow] = []
    for path in sorted(spec_dir.glob("*.md")):
        if path.name == INDEX_NAME:
            continue
        date = date_from_filename(path.name)
        if date is None:
            continue
        referenced = tuple(name for name, body, _ in audits if path.name in body)
        reconciled = tuple(
            name
            for name, body, is_reconcile in audits
            if path.name in body and is_reconcile
        )
        rows.append(
            SpecRow(
                date=date,
                title=title_from_markdown(path.read_text(encoding="utf-8"))
                or path.stem,
                reconciled_by=reconciled,
                referenced_by=referenced,
                filename=path.name,
            )
        )
    return sorted(rows, key=lambda r: (r.date, r.filename), reverse=True)


def spec_reconcile_audits(audit_dir: Path) -> tuple[str, ...]:
    """Audits that identify themselves as spec-vs-code reconciles.

    Read separately from the per-spec join because an empty result and a
    non-empty one that matched no spec are different facts that render the same:
    `0 of N`. Only the first means the column had no reachable value.
    """
    return tuple(
        path.name
        for path in sorted(audit_dir.glob("*.md"))
        if path.name != INDEX_NAME
        and is_spec_reconcile_audit(path.name, path.read_text(encoding="utf-8"))
    )


def _cell(value: str) -> str:
    """Escape a value for a markdown table cell."""
    return value.replace("|", "\\|").strip() or "—"


def render_audit_index(rows: list[AuditRow]) -> str:
    """Render `docs/audits/INDEX.md`."""
    with_verdict = sum(1 for r in rows if r.verdict)
    out = [
        "# Audit index",
        "",
        "**Generated — do not edit by hand.** Regenerate with `make docs-index`;",
        "`make docs-index-check` (and `tests/test_docs_index.py`) fails when this file",
        "drifts from the corpus.",
        "",
        f"**{len(rows)} audits.** A verdict line is shown only where one could be read",
        f"out of a Verdict heading as prose — that is **{with_verdict} of {len(rows)}**.",
        "An em dash means the doc states its verdict in a table, a blockquote or the body,",
        "**not** that it lacks one; open the file. Nothing here is keyword-guessed.",
        "",
        "| Date | Audit | Verdict (as written) | File |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        link = f"[{row.filename}]({row.filename})"
        out.append(
            f"| {row.date} | {_cell(row.title)} | {_cell(row.verdict or '')} | {link} |"
        )
    out.append("")
    return "\n".join(out)


def render_spec_index(rows: list[SpecRow], reconcile_audits: tuple[str, ...]) -> str:
    """Render `docs/superpowers/specs/INDEX.md`."""
    reconciled = [r for r in rows if r.reconciled_by]
    unreferenced = [r for r in rows if not r.referenced_by]
    out = [
        "# Spec index",
        "",
        "**Generated — do not edit by hand.** Regenerate with `make docs-index`;",
        "`make docs-index-check` (and `tests/test_docs_index.py`) fails when this file",
        "drifts from the corpus.",
        "",
        f"**{len(rows)} specs on disk.** Reconcile status is **derived** — a spec counts as",
        "reconciled when an audit that both names its filename and identifies itself as a",
        "spec-vs-code reconcile says so, recomputed from disk on every run rather than",
        "carried in prose where nothing would catch it going stale.",
        "",
    ]
    if reconcile_audits:
        out += [
            f"- **Reconciled (derived): {len(reconciled)} of {len(rows)}**, from"
            f" {len(reconcile_audits)} spec-reconcile audit(s).",
            f"- Referenced by no audit at all: {len(unreferenced)}.",
            "",
            "**Two caveats before quoting these numbers.** A *partial* reconcile is",
            "indistinguishable from a whole-spec one here, and this table cannot see one",
            "that was done but never written down in an audit. Treat it as the list of",
            "what exists, and the reconcile column as a floor.",
            "",
        ]
    else:
        out += [
            f"- **Reconciled (derived): 0 of {len(rows)} — and 0 is the only value this",
            "  column can currently take**, because no audit in `docs/audits/` identifies",
            "  itself as a spec-vs-code reconcile. Read it as *no reconcile has been"
            " written up*,",
            f"  never as *{len(rows)} specs went unreconciled*: the two are indistinguishable",
            "  from here, and the second is a claim this table cannot support.",
            f"- Referenced by no audit at all: {len(unreferenced)}.",
            "",
            "The column starts working the moment one audit says so in its own filename or",
            "H1 — that is the whole detector, and it is deliberately not a body keyword.",
            "",
        ]
    out += [
        "| Date | Spec | Reconciled by | Also referenced by | File |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        others = tuple(n for n in row.referenced_by if n not in row.reconciled_by)
        link = f"[{row.filename}]({row.filename})"
        out.append(
            f"| {row.date} | {_cell(row.title)} | {_cell(', '.join(row.reconciled_by))} "
            f"| {_cell(', '.join(others))} | {link} |"
        )
    out.append("")
    return "\n".join(out)


def build_indexes(audit_dir: Path, spec_dir: Path) -> dict[Path, str]:
    """Map each index path to the content it should hold."""
    return {
        audit_dir / INDEX_NAME: render_audit_index(collect_audits(audit_dir)),
        spec_dir / INDEX_NAME: render_spec_index(
            collect_specs(spec_dir, audit_dir), spec_reconcile_audits(audit_dir)
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """Write both indexes, or with `--check` report whether they are current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=AUDIT_DIR)
    parser.add_argument("--spec-dir", type=Path, default=SPEC_DIR)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if an index is missing or stale; write nothing",
    )
    args = parser.parse_args(argv)

    stale: list[Path] = []
    for path, content in build_indexes(args.audit_dir, args.spec_dir).items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            print(f"  = {path} (current)")
            continue
        if args.check:
            stale.append(path)
            print(f"  ! {path} is stale")
            continue
        path.write_text(content, encoding="utf-8")
        print(f"  + {path} written")

    if stale:
        print(f"\n{len(stale)} index file(s) stale — run: make docs-index")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
