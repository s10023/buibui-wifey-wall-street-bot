"""Find citations of a document's numbered anchors that no longer exist.

The defect class: document A cites a numbered section of document B — ``
`/post-branch` Step 10b ``, ``  `/sanity-check` §4a `` — and B later renumbers
or renames its own sections. Nothing notices. It has now recurred three times
here, the third caused by the branch that shipped `/sanity-check` itself, and
**no existing check can see it**: `handoff-symbols` keys on symbols, and a
section number is not a symbol.

Two design decisions carry the check.

**Kinds must agree, except when a citation is untyped.** ``§4a`` names "the
section numbered 4a" without claiming it is a step or a phase, so it matches a
declaration of any kind; ``Step 6`` and ``Phase 6`` are *typed* and must agree.
That asymmetry is the whole point — the recurring rename is Step → Phase, which
a label-only comparison would call a match while every citer points at a kind
that no longer exists.

**Adjacency is the discriminator, and it is scoped by how ambiguous the anchor
is.** "Phase 3" appears in ordinary prose about `run_scan_cycle`, so a typed
anchor is only read as a citation when it sits within
:data:`TYPED_WINDOW` characters of a target reference with no sentence boundary
between them. ``§`` is never ordinary prose, so it gets a wider window
(:data:`UNTYPED_WINDOW`) and may cross a sentence break. ⚠ **That bound is a
real cap and it is stated rather than hidden**: a citation separated from its
target by more than the window is invisible to this check.

Dated trees are excluded (:func:`is_dated_path`), the same exclusion
``sanity_checks``'s fork-drift leg already makes — a past-tense record is
correct by construction, and hand-sweeping this class found 2 such correct
citations against 4 live ones.

⚠ **Scope is wider than the repo.** Two of those 4 live citations were in the
memory tree, which no repo-scoped check can reach — so ``post_branch_checks``
scans both and ``sanity_checks`` gates the repo half that CI can actually see.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: The untyped kind. A bare ``§N`` matches a declaration of any kind.
UNTYPED = "section"

#: How far after a target reference an anchor may sit and still be read as a
#: citation of it. Typed anchors ("Step 3") are ordinary English, so they must
#: be close and must not cross a sentence boundary.
TYPED_WINDOW = 40
UNTYPED_WINDOW = 90

_LABEL = r"\d{1,3}[a-z]?"

_HEADING = re.compile(r"^#{1,6}[ \t]+(.*)$", re.M)
#: Column-0 ordered-list items, harvested ONLY as a fallback — see
#: :func:`declared_anchors`. Nested items are never harvested: they renumber
#: inside a section and would mask a genuinely dead top-level step.
_TOP_LEVEL_ORDERED = re.compile(rf"^({_LABEL})\.[ \t]+\S", re.M)

#: A sub-step declared as a bold lead-in rather than a heading —
#: ``**7b — The board view.**``. `/ingest-video` numbers 7a/7b/7c this way,
#: and the SoT cites them, so a heading-only parser calls a live anchor dead.
_BOLD_LEAD = re.compile(r"^\*\*(.+?)\*\*", re.M)

_HEAD_TYPED = re.compile(rf"^(step|phase)[ \t]+({_LABEL})\b", re.I)
#: Tight on purpose: the label must lead and be closed by a separator, so
#: ``**Fix: stamp last**`` declares nothing. Over-generating here would be a
#: false NEGATIVE — a dead citation matching a coincidental bold line.
#: ⚠ The `(?!\d)` is load-bearing. Without it a DECIMAL declares a section:
#: `**4.9 min**` declared `section 4` and `### 12.5 GB` declared `section 12`.
#: That is not merely a spurious entry — `declared_anchors` disables the
#: ordered-list fallback the moment ANY declaration exists, so one decimal
#: anywhere in a file blinded the check to every genuine ordered-list anchor in
#: it. Both directions were live here: one memory file lost 3 real `step`
#: anchors (false positives on every citation to them) and another gained a
#: phantom `section 0` that made a dead citation read as valid (a false
#: negative). `_TOP_LEVEL_ORDERED` already requires whitespace after the dot, so
#: it needs no equivalent guard.
_HEAD_UNTYPED = re.compile(rf"^§?({_LABEL})[ \t]*[.)—–-](?!\d)")

#: Target references this check can resolve to a file on disk.
_TARGET = re.compile(
    r"\[\[(?P<wiki>[a-z0-9_]+)\]\]"
    r"|/(?P<skill>[a-z][a-z0-9-]{2,})\b"
    r"|(?P<doc>[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.md)\b"
)

_ANCHOR = re.compile(
    rf"§[ \t]*(?P<slabel>{_LABEL})"
    rf"|\b(?P<kind>step|phase)s?[ \t]+(?P<klabel>{_LABEL})\b",
    re.I,
)

_SENTENCE_BREAK = re.compile(r"[.!?][ \t\n]")

#: ``CLAUDE.md still cited "§4a"`` is a *report* of a dead anchor, not a live
#: citation of one. Quoting is the one reliable tell. MEMORY.md's own bullet
#: about this very defect is why the rule exists, and `.claude/context/tools.md`
#: needed it a second time to document this check without tripping it.
#: Read by `_quoted_spans`, which pairs an opener with its own closer. Kept a
#: `str` only as data; nothing does `ch in _QUOTES` any more, because that test
#: is `True` for the empty string and silently suppressed a whole class.
_QUOTES = '"\u201c\u201d'

#: Directories whose contents are dated records: a citation there was correct
#: when written and must not be "fixed".
DATED_DIRS = (
    "docs/audits/",
    "docs/redesign/",
    "docs/superpowers/",
    "docs/plans/parent-sync/",
)
_DATED_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}|archive_|project_session_log_|pr-)")


def is_dated_path(path: str | Path) -> bool:
    """True when a citation in `path` records what was true at a past date."""
    text = str(path).replace("\\", "/")
    if any(d in text for d in DATED_DIRS):
        return True
    return bool(_DATED_NAME.match(Path(text).name))


@dataclass(frozen=True)
class Citation:
    """One cross-document anchor reference, as written."""

    source: str
    line: int
    target: str
    kind: str
    label: str
    context: str


def declared_anchors(markdown: str) -> set[tuple[str, str]]:
    """Every ``(kind, label)`` anchor a document declares about itself."""
    found: set[tuple[str, str]] = set()
    for heading in _HEADING.findall(markdown):
        title = heading.strip().lstrip("*_ ").strip()
        typed = _HEAD_TYPED.match(title)
        if typed:
            found.add((typed.group(1).lower(), typed.group(2).lower()))
            continue
        untyped = _HEAD_UNTYPED.match(title)
        if untyped:
            found.add((UNTYPED, untyped.group(1).lower()))
    for lead in _BOLD_LEAD.findall(markdown):
        title = lead.strip()
        typed = _HEAD_TYPED.match(title)
        if typed:
            found.add((typed.group(1).lower(), typed.group(2).lower()))
            continue
        untyped = _HEAD_UNTYPED.match(title)
        if untyped:
            found.add((UNTYPED, untyped.group(1).lower()))
    if found:
        return found
    # Fallback, and it is deliberately conditional. A document that numbers its
    # own headings has an explicit sectioning scheme, and harvesting its ordered
    # lists on top of that is how the check would blind itself: `/post-branch`
    # says "Phases, not step numbers" yet carries three column-0 rubric lists
    # numbered 1..5, which would have silently validated every dead
    # `/post-branch Step N` citation — the precise defect this exists to catch.
    # Only a document with no numbered heading at all (`/db-update`, `/ingest-x`)
    # is read as numbering itself through its ordered list.
    return {("step", label.lower()) for label in _TOP_LEVEL_ORDERED.findall(markdown)}


def anchor_matches(kind: str, label: str, declared: Iterable[tuple[str, str]]) -> bool:
    """Does a cited anchor resolve against a document's declared set?"""
    for dkind, dlabel in declared:
        if dlabel != label:
            continue
        if UNTYPED in (kind, dkind) or dkind == kind:
            return True
    return False


def _quoted_spans(line: str) -> list[tuple[int, int]]:
    """Half-open ranges covered by a CLOSED quotation on this line.

    An unterminated quotation yields no span, so its contents read as *used*
    rather than *mentioned*. That direction is deliberate: reporting a citation
    that turned out to be a mention costs a glance, while suppressing a real one
    is invisible, and this leg's whole value is catching what nothing else can.
    """
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    closer = ""
    for i, ch in enumerate(line):
        if open_at is None:
            if ch == "“":
                open_at, closer = i, "”"
            elif ch == '"':
                open_at, closer = i, '"'
        elif ch == closer:
            spans.append((open_at, i))
            open_at, closer = None, ""
    return spans


def _is_quoted(line: str, begin: int, end: int) -> bool:
    """Is this anchor inside a quotation, i.e. mentioned rather than used?

    ⚠ **Tests the enclosing SPAN, never the two adjacent characters.** The
    adjacent-character form had one bug in each direction and they were only
    visible from opposite ends. It missed a quotation wrapping *target plus
    anchor* as one phrase (`"wifey's /post-branch Step 5c"`), because the
    character before the anchor is a space -- a false positive, found by
    triaging findings. And `_QUOTES` is a `str`, so `in` is a substring test and
    ``"" in _QUOTES`` is `True`: an anchor ending the line short-circuited to
    "quoted" and was dropped in silence -- a false negative, findable only by
    reading the code, since by construction it produced nothing to triage. The
    end-of-line case is not exotic; it is what a quoted phrase looks like when
    markdown wraps it, i.e. the same construction that produced the false
    positive. Spans fix both, and drop the character indexing that caused them.
    """
    return any(start < begin and end <= stop for start, stop in _quoted_spans(line))


def _anchor_after(line: str, start: int) -> tuple[str, str, int] | None:
    """First anchor in `line` after `start` that is close enough to be a citation."""
    window = line[start : start + UNTYPED_WINDOW]
    for m in _ANCHOR.finditer(window):
        begin, end = start + m.start(), start + m.end()
        if _is_quoted(line, begin, end):
            continue
        if m.group("slabel"):
            return UNTYPED, m.group("slabel").lower(), begin
        if m.start() > TYPED_WINDOW or _SENTENCE_BREAK.search(window[: m.start()]):
            continue
        return m.group("kind").lower(), m.group("klabel").lower(), begin
    return None


def citations(text: str, source: str) -> list[Citation]:
    """Every target-plus-anchor pair in `text`, in document order."""
    out: list[Citation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        targets = list(_TARGET.finditer(line))
        for i, t in enumerate(targets):
            raw = t.group("wiki") or t.group("skill") or t.group("doc")
            limit = targets[i + 1].start() if i + 1 < len(targets) else len(line)
            hit = _anchor_after(line, t.end())
            if hit is None or hit[2] >= limit:
                continue
            kind, label, _ = hit
            out.append(
                Citation(
                    source=source,
                    line=lineno,
                    target=raw,
                    kind=kind,
                    label=label,
                    context=line.strip(),
                )
            )
    return out


def default_resolver(
    repo_root: Path, memory_dir: Path | None = None
) -> Callable[[str], Path | None]:
    """Map a raw target reference onto the file it names, or None."""

    def resolve(raw: str) -> Path | None:
        if raw.endswith(".md"):
            candidate = repo_root / raw
            return candidate if candidate.is_file() else None
        skill = repo_root / ".claude" / "skills" / raw / "SKILL.md"
        if skill.is_file():
            return skill
        if memory_dir is not None:
            topic = memory_dir / f"{raw}.md"
            if topic.is_file():
                return topic
        return None

    return resolve


def scan(
    sources: Sequence[Path],
    resolve: Callable[[str], Path | None],
    *,
    root: Path | None = None,
) -> list[tuple[Citation, Path]]:
    """Every citation whose target no longer declares the anchor it names."""
    declared: dict[Path, set[tuple[str, str]]] = {}
    dead: list[tuple[Citation, Path]] = []
    for src in sources:
        label = str(src.relative_to(root)) if root else str(src)
        if is_dated_path(label):
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for cite in citations(text, label):
            target = resolve(cite.target)
            if target is None:
                continue
            if target not in declared:
                declared[target] = declared_anchors(
                    target.read_text(encoding="utf-8", errors="replace")
                )
            if not anchor_matches(cite.kind, cite.label, declared[target]):
                dead.append((cite, target))
    return dead


def describe(cite: Citation, target: Path, root: Path | None = None) -> str:
    """One printable line naming the citation, its target and the dead anchor."""
    # Forward slashes regardless of host: this line is pasted into handoffs and
    # PR bodies, which are read on both, and a backslash there reads as an
    # escape rather than a separator.
    shown = (
        target.relative_to(root).as_posix()
        if root and target.is_relative_to(root)
        else target.as_posix()
    )
    anchor = f"§{cite.label}" if cite.kind == UNTYPED else f"{cite.kind} {cite.label}"
    return f"{cite.source}:{cite.line}: cites `{cite.target}` {anchor} — {shown} declares no such anchor"
