"""Run every mechanical check `/post-branch` used to carry as copy-by-hand bash.

The skill embedded 16 shell blocks in prose. A session had to notice each one,
copy it, and run it — which is the failure CLAUDE.md names as *a hand walk is
not the walk*, and it is why the same defects kept recurring: prose cannot
enforce. Every check that can run without judgement lives here instead, so the
skill's instruction is "run this, triage the hits" rather than sixteen
invitations to remember.

Two checks are new, and both fix defects the prose form could not:

* ``queue-items`` — nothing swept the handoff's own task list for work the
  branch just did, so an item survived as a confident instruction to redo
  finished work. Confirmed three times. The prose mitigation keyed on added
  Python *symbols*, which a docs-only branch does not have; this keys the
  handoff's own distinctive tokens against the diff **content**, so it sees a
  branch that adds no code at all.
* ``new-files`` — the prose probed ``basename``, and every skill's basename is
  the shared constant ``SKILL.md``, which matches CLAUDE.md's generic sentence
  about where skills live. A fabricated skill therefore reported COVERED, the
  exact false-positive the check's own ``-w`` rule exists to prevent. When a
  basename carries no identity, :func:`probe_names` probes the parent directory
  instead.

Checks are pure functions over text wherever possible; the git surface is
injected as ``runner`` so the suite can exercise them without a repository.
"""

from __future__ import annotations

import argparse
import re
import subprocess  # noqa: S404 - git plumbing, fixed argv, no shell
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.stale_anchors import default_resolver, describe, scan

Runner = Callable[[Sequence[str]], str]

HANDOFF = Path("docs/plans/next-conversation-prompt.md")
MEMORY = Path.home() / (
    ".claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot"
    "/memory/MEMORY.md"
)
MEMORY_DIR = MEMORY.parent

#: Current-state doc surfaces swept for dead anchor citations. Deliberately the
#: same shape as `sanity_checks.SURFACE_ROOTS` — the dated trees are excluded by
#: `stale_anchors.is_dated_path`, because a citation in a dated record was
#: correct when written. Hand-sweeping this class found 2 such correct
#: citations against 4 live ones, so the exclusion is load-bearing.
ANCHOR_ROOTS = (".claude",)
ANCHOR_FILES = (
    "CLAUDE.md",
    "README.md",
    "docs/system-overview.md",
    "docs/plans/next-conversation-prompt.md",
)

#: Basenames that identify a *role* rather than a file. Probing these by name
#: matches unrelated prose, so the parent directory is the real identity.
SHARED_CONSTANT_BASENAMES = frozenset(
    {"SKILL.md", "README.md", "__init__.py", "INDEX.md", "index.ts", "main.py"}
)

#: Docs that enumerate files by name. A new operator-facing file should appear
#: in at least one of them. The Makefile is deliberately absent: a build rule is
#: not documentation, and including it would let a file mentioned in no prose
#: report COVERED.
ENUMERATING_DOCS = ("CLAUDE.md", "README.md", ".claude/context", "deploy/README.md")

CONTEXT_DOCS = (".claude/context",)

#: Absence-language. Kept narrow on purpose — generic phrases ("for now",
#: "unwired", "stop-gap") each pulled double-digit false positives for no catch.
NEGATIVE_CLAIM_RE = re.compile(
    r"(never|not) (yet )?ported"
    r"|no (reader|host|consumer)\b"
    r"|this repo has no"
    r"|until (that|the) port lands"
    r"|silent accumulator"
    r"|accumulates? unscored"
    r"|is not (yet )?(available|implemented|wired)",
    re.IGNORECASE,
)

NEGATIVE_CLAIM_PATHS = (
    "CLAUDE.md",
    "README.md",
    "Makefile",
    "docker-compose.yml",
    ".claude",
)

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_HYPOTHESIS = re.compile(r"\bH-\d{3}\b")
_MD_NUMBERED_ITEM = re.compile(r"^(\d+)\.\s+(.*)$")
_DEF_OR_CLASS = re.compile(
    r"^[+-]\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE
)
_BAD_ATX = re.compile(r"^#[0-9]")

#: Tokens too common to discriminate — they appear in prose for unrelated
#: reasons and would make every run noisy.
_STOPWORDS = frozenset(
    {
        "main",
        "HEAD",
        "true",
        "false",
        "None",
        "make",
        "git",
        "gh",
        "PR",
        "CI",
        "md",
        "py",
        "1d",
        "4h",
        "1wk",
        # Stems of files nearly every branch touches: they discriminate nothing
        # and their hits drown the ones that matter.
        "CLAUDE",
        "SKILL",
        "README",
        "INDEX",
        "tools",
        "docs",
        "tests",
        "config",
    }
)


@dataclass
class Finding:
    """One thing a human must look at. `detail` is printed verbatim."""

    check: str
    detail: str


@dataclass
class CheckResult:
    name: str
    findings: list[Finding] = field(default_factory=list)
    skipped: str | None = None


def _run_rc(argv: Sequence[str]) -> int:
    """Exit code of a fixed-argv command; 0 if it cannot be launched."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, shell=False
            list(argv), capture_output=True, text=True, check=False
        ).returncode
    except OSError:
        return 0


def _run(argv: Sequence[str]) -> str:
    """Run a fixed-argv command, returning stdout and swallowing failure.

    A check that cannot run must not abort the sweep — the others still carry
    information.
    """
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            list(argv), capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return out.stdout


# ---------------------------------------------------------------- pure helpers


def probe_names(path: str) -> list[str]:
    """Names to grep when asking "is this file documented anywhere?".

    The basename alone is wrong whenever it is a shared constant: every skill is
    ``SKILL.md``, so a probe for it matches CLAUDE.md's generic sentence about
    where skills live and reports COVERED for a file no doc has heard of. In
    that case the identity lives in the directory, so probe that instead.

    Generalises to any file named for its role rather than its content.
    """
    p = Path(path)
    if p.name in SHARED_CONSTANT_BASENAMES and p.parent.name:
        return [p.parent.name]
    stem = p.stem
    return [p.name] if stem == p.name else [p.name, stem]


def extract_tokens(text: str) -> set[str]:
    """Distinctive tokens from prose: backticked spans plus hypothesis ids.

    Deliberately narrow. A token has to be specific enough that finding it in a
    diff means something; bare English words would match everything.
    """
    out: set[str] = set()
    for raw in _BACKTICKED.findall(text):
        tok = raw.strip()
        # A backticked command line is not a token; take its first word.
        first = tok.split()[0] if tok.split() else ""
        for cand in (tok, first):
            cand = cand.strip("`*_.,:;()[]")
            if len(cand) >= 3 and cand not in _STOPWORDS:
                out.add(cand)
    out.update(_HYPOTHESIS.findall(text))
    return out


def numbered_items(markdown: str) -> dict[int, str]:
    """Split a markdown numbered list into ``{index: full item text}``.

    Continuation lines (indented under the item) belong to the item, which is
    where most of a queue entry's distinctive tokens live.
    """
    items: dict[int, str] = {}
    current: int | None = None
    for line in markdown.splitlines():
        m = _MD_NUMBERED_ITEM.match(line)
        if m:
            current = int(m.group(1))
            items[current] = m.group(2)
        elif current is not None:
            if line.startswith(("   ", "\t")) or (line.strip() and line[0].isspace()):
                items[current] += "\n" + line.strip()
            elif not line.strip():
                continue
            else:
                current = None
    return items


def added_paths(diff_filter_a: str, status_porcelain: str) -> list[str]:
    """Files this branch adds, tracked **and** untracked.

    ``git diff`` in any form cannot see an untracked file, so the presence
    checks reported zero added files on a branch whose new code was not yet
    staged — the precise case they exist to catch. The skill answered this with
    "remember to ``git add -A`` first", which is one more hand-step to forget;
    reading ``git status`` instead makes the check correct either way.
    """
    out = {p for p in diff_filter_a.split() if p}
    for line in status_porcelain.splitlines():
        if line.startswith("?? "):
            path = line[3:].strip()
            if path.endswith("/"):
                continue
            out.add(path)
        elif line[:2] in {"A ", "AM", " A"}:
            out.add(line[3:].strip())
    return sorted(out)


def diff_symbols(diff: str) -> set[str]:
    """Function and class names added or removed in a diff."""
    return set(_DEF_OR_CLASS.findall(diff))


def bad_atx_lines(text: str) -> list[int]:
    """1-indexed lines where a wrapped ``#123`` became an MD018 heading."""
    return [i for i, line in enumerate(text.splitlines(), 1) if _BAD_ATX.match(line)]


def current_state_bullets(memory: str) -> int:
    """Count top-level bullets under MEMORY.md's ``## Current State``."""
    seen = False
    n = 0
    for line in memory.splitlines():
        if line.startswith("## Current State"):
            seen = True
            continue
        if seen:
            if line.startswith("## "):
                break
            if line.startswith("- "):
                n += 1
    return n


# --------------------------------------------------------------------- checks


def check_queue_items(handoff: str, diff: str, diff_names: str) -> list[Finding]:
    """Does this branch CLOSE a task the handoff still lists as to-do?

    Nothing swept for this, so a finished item survived under a heading telling
    the next session it was still owed. The earlier mitigation keyed on added
    Python symbols and therefore could not see a docs-only branch at all; this
    keys the handoff's own tokens against the diff content, which every branch
    has.
    """
    findings: list[Finding] = []
    haystack = diff + "\n" + diff_names
    for idx, body in numbered_items(handoff).items():
        hits = sorted(t for t in extract_tokens(body) if t in haystack)
        if hits:
            head = " ".join(body.split())[:90]
            findings.append(
                Finding(
                    "queue-items",
                    f"item {idx} may be CLOSED by this branch "
                    f"(matched {', '.join(hits[:4])})\n      {head}…",
                )
            )
    return findings


def check_handoff_symbols(handoff: str, diff: str, diff_names: str) -> list[Finding]:
    """Handoff claims naming a symbol or file this branch touched."""
    names = {Path(n).stem for n in diff_names.split() if n} | diff_symbols(diff)
    findings: list[Finding] = []
    for i, line in enumerate(handoff.splitlines(), 1):
        for sym in sorted(names):
            if len(sym) < 4 or sym in _STOPWORDS:
                continue
            if re.search(rf"\b{re.escape(sym)}\b", line):
                findings.append(
                    Finding("handoff-symbols", f"{HANDOFF}:{i} mentions `{sym}`")
                )
                break
    return findings


def check_new_files(added: Sequence[str], doc_blob: str) -> list[Finding]:
    """Every added non-Python operator file should reach an enumerating doc."""
    findings = []
    for path in added:
        if path.startswith(("tests/", "docs/")) or path.endswith(".py"):
            continue
        if not any(
            re.search(rf"\b{re.escape(n)}\b", doc_blob) for n in probe_names(path)
        ):
            findings.append(Finding("new-files", f"UNDOCUMENTED FILE: {path}"))
    return findings


def check_new_modules(added: Sequence[str], context_blob: str) -> list[Finding]:
    """Every added module should reach `.claude/context/`."""
    findings = []
    for path in added:
        if not path.endswith(".py") or path.startswith(("tests/", "docs/")):
            continue
        if not any(
            re.search(rf"\b{re.escape(n)}\b", context_blob) for n in probe_names(path)
        ):
            findings.append(Finding("new-modules", f"UNDOCUMENTED: {path}"))
    return findings


def check_new_targets(diff: str, doc_blob: str) -> list[Finding]:
    """Every added Make target should be documented.

    ``wifey-`` is stripped because CLAUDE.md documents the *subcommands* and
    states that each ``wifey-*`` target wraps one; without the strip every
    wrapper reports undocumented, which is noise, and noise gets a check
    ignored.
    """
    findings = []
    for line in diff.splitlines():
        m = re.match(r"^\+([a-z][a-z0-9_-]*):", line)
        if not m:
            continue
        target = m.group(1)
        probes = {target, target.removeprefix("wifey-")}
        if not any(re.search(rf"\b{re.escape(p)}\b", doc_blob) for p in probes):
            findings.append(Finding("new-targets", f"UNDOCUMENTED TARGET: {target}"))
    return findings


def check_negative_claims(runner: Runner) -> list[Finding]:
    """Docs asserting the absence of something this branch may have added."""
    out = runner(["git", "grep", "-nI", "-e", "x", "--", *NEGATIVE_CLAIM_PATHS])
    findings = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3 or "post-branch" in parts[0]:
            continue
        m = NEGATIVE_CLAIM_RE.search(parts[2])
        if m:
            findings.append(
                Finding("negative-claims", f"{parts[0]}:{parts[1]}: {m.group(0)}")
            )
    return findings


# ------------------------------------------------------------------ execution


def gather(runner: Runner = _run) -> list[CheckResult]:
    """Run every check against the working tree. Order matches the skill."""
    diff = runner(["git", "diff", "main", "--"])
    diff_names = runner(["git", "diff", "main", "--name-only"])
    makefile_diff = runner(["git", "diff", "main", "--", "Makefile"])
    added = added_paths(
        runner(["git", "diff", "main", "--diff-filter=A", "--name-only"]),
        runner(["git", "status", "--porcelain"]),
    )
    changed_md = sorted(
        {p for p in diff_names.split() if p.endswith(".md")}
        | {p for p in added if p.endswith(".md")}
    )

    handoff = HANDOFF.read_text(encoding="utf-8") if HANDOFF.exists() else ""
    doc_blob = _read_all(ENUMERATING_DOCS)
    context_blob = _read_all(CONTEXT_DOCS)

    results = [
        CheckResult("queue-items", check_queue_items(handoff, diff, diff_names))
        if handoff
        else CheckResult("queue-items", skipped="no handoff file"),
        CheckResult("handoff-symbols", check_handoff_symbols(handoff, diff, diff_names))
        if handoff
        else CheckResult("handoff-symbols", skipped="no handoff file"),
        CheckResult("new-files", check_new_files(added, doc_blob)),
        CheckResult("new-modules", check_new_modules(added, context_blob)),
        CheckResult("new-targets", check_new_targets(makefile_diff, doc_blob)),
        CheckResult("negative-claims", check_negative_claims(runner)),
        CheckResult("doc-indexes", _check_doc_indexes()),
        CheckResult("md-atx", _check_md_atx(changed_md)),
        CheckResult("memory-cap", _check_memory_cap()),
        CheckResult("handoff-size", _check_handoff_size(handoff)),
        CheckResult("stale-anchors", _check_stale_anchors()),
    ]
    return results


def _read_all(paths: Sequence[str]) -> str:
    chunks: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            chunks.extend(
                f.read_text(encoding="utf-8", errors="replace")
                for f in sorted(p.rglob("*.md"))
            )
        elif p.exists():
            chunks.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _check_doc_indexes() -> list[Finding]:
    """`docs/*/INDEX.md` are GENERATED and a test compares them byte-for-byte.

    A branch that adds, renames or deletes an audit or spec therefore leaves a
    **red suite**, not a lint nit, until the indexes are refreshed. Checked here
    rather than at pre-merge because this is local, offline and sub-second,
    while a failure caught after `gh pr create` costs a second full matrix.
    """
    if not Path("tools/docs_index.py").exists():
        return []
    if _run_rc(["poetry", "run", "python", "tools/docs_index.py", "--check"]) != 0:
        return [
            Finding(
                "doc-indexes",
                "an INDEX.md is stale — run `make docs-index`, never hand-edit it",
            )
        ]
    return []


def _check_md_atx(changed_md: Sequence[str]) -> list[Finding]:
    findings = []
    for raw in changed_md:
        p = Path(raw)
        if not p.exists():
            continue
        for line_no in bad_atx_lines(p.read_text(encoding="utf-8", errors="replace")):
            findings.append(
                Finding("md-atx", f"{raw}:{line_no}: `#123` in column 1 becomes MD018")
            )
    return findings


def _check_memory_cap() -> list[Finding]:
    if not MEMORY.exists():
        return []
    text = MEMORY.read_text(encoding="utf-8")
    n = current_state_bullets(text)
    size = len(text.encode("utf-8"))
    findings = []
    if n > 6:
        findings.append(
            Finding("memory-cap", f"Current State has {n} bullets (cap 6) — roll one")
        )
    if size > 17_408:
        findings.append(
            Finding("memory-cap", f"MEMORY.md is {size:,} bytes (~17KB soft cap)")
        )
    return findings


def _check_stale_anchors() -> list[Finding]:
    """Citations of a numbered section that the cited document no longer has.

    ⚠ **Scope is wider than the repo, and that is the point.** Of the four live
    dead `§4a` citations found by hand when this class recurred a third time,
    **two were in the memory tree** — which no repo-scoped check can reach, and
    which is why this leg lives here rather than in the CI-gating
    ``sanity_checks``. The repo half alone would have reported clean.
    """
    repo_root = Path.cwd()
    sources = [p for root in ANCHOR_ROOTS for p in sorted(Path(root).rglob("*.md"))]
    sources += [Path(f) for f in ANCHOR_FILES if Path(f).is_file()]
    resolve = default_resolver(repo_root, MEMORY_DIR if MEMORY_DIR.is_dir() else None)

    findings = [
        Finding("stale-anchors", describe(cite, target, repo_root))
        for cite, target in scan(sources, resolve)
    ]
    if MEMORY_DIR.is_dir():
        findings += [
            Finding("stale-anchors", describe(cite, target, repo_root))
            for cite, target in scan(
                sorted(MEMORY_DIR.glob("*.md")), resolve, root=MEMORY_DIR.parent
            )
        ]
    return findings


def _check_handoff_size(handoff: str) -> list[Finding]:
    if not handoff:
        return []
    lines = len(handoff.splitlines())
    m = re.search(r"^Line count: \*\*(\d+)\*\*", handoff, re.MULTILINE)
    if m and int(m.group(1)) != lines:
        return [
            Finding(
                "handoff-size",
                f"stamp claims {m.group(1)} lines, file has {lines} — "
                "rewrite the stamp LAST",
            )
        ]
    return []


def render(results: Sequence[CheckResult]) -> tuple[list[str], int]:
    out = ["post_branch_checks — mechanical sweep", ""]
    total = 0
    for r in results:
        if r.skipped:
            out.append(f"  {r.name:<16} SKIPPED  ({r.skipped})")
            continue
        if not r.findings:
            out.append(f"  {r.name:<16} clean")
            continue
        total += len(r.findings)
        out.append(f"  {r.name:<16} {len(r.findings)} to triage")
        for f in r.findings:
            out.append(f"      {f.detail}")
    out += [
        "",
        f"  {total} finding(s). Each is a candidate to DISMISS in seconds, never an",
        "  automatic edit — a false positive costs a glance, a silent miss ships a",
        "  doc that reads as complete.",
    ]
    return out, total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="post_branch_checks",
        description="Every mechanical /post-branch check, in one run.",
    )
    parser.add_argument("--check", help="run only this named check")
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="always exit 0 (findings are advisory, not a gate)",
    )
    args = parser.parse_args(argv)

    results = gather()
    if args.check:
        results = [r for r in results if r.name == args.check]
        if not results:
            print(f"post_branch_checks: no such check: {args.check}", file=sys.stderr)
            return 2
    lines, total = render(results)
    for line in lines:
        print(line)
    return 0 if (args.exit_zero or total == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
