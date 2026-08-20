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

#: Names that must never enter a tracked file — employer, clients, work repos.
#: **Gitignored on purpose (`.gitignore:25`): a tracked list of the words you are
#: hiding is the leak it exists to prevent.** It therefore dies on a reclone, like
#: the hooks did before `.claude/` was inverted to a denylist, which is why an
#: absent list is a FINDING rather than a SKIP.
SENSITIVE_TERMS = Path(".claude/sensitive-terms.txt")
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

#: Tokens that scope a claim line IN while carrying no claim of their own, keyed
#: on ``(path, token)`` with the reason inline — the
#: ``sanity_checks.MISSING_PATH_EXEMPT`` shape.
#:
#: ⚠ **Keyed on the pair, never on either half.** A path-wide mute would have
#: suppressed this leg's only true positive (2026-08-20h: adding
#: ``research_guards/cluster.py`` falsified two sentences in the same file), and a
#: token-wide mute would carry that hole into every other document.
#:
#: ⚠ **The tempting fix is the wrong one.** These lines are 3-6 KB paragraphs
#: carrying 49 and 113 tokens, so the obvious remedy is to scope on a window
#: around the regex match instead of the whole line. Measured against the
#: pre-#248 tree, that window would have suppressed the true positive: the regex
#: matched ``gate_audit.py … not ported`` while the sentence the branch actually
#: falsified sat ~1,400 characters earlier on the same line. The value came from
#: a human re-reading the paragraph, so the line stays the unit.
#:
#: ``attribution`` is deliberately ABSENT: on two of these lines it is the
#: claim's own subject, so a branch that ports it must still be told.
NEGATIVE_CLAIM_EXEMPT: dict[tuple[str, str], str] = {
    (".claude/context/analytics.md", "symbol"): (
        "a parameter name in the engine signatures quoted on this line, not the "
        "subject of any absence claim in this file"
    ),
    (".claude/context/analytics.md", "signal_watch"): (
        "names the live configs the paragraph describes; the claim beside it is "
        "about per-direction ADR overrides"
    ),
    (".claude/context/analytics.md", "sharpe"): (
        "a metric the paragraph lists as PRESENT — the claim is about the "
        "book-dependent attribution funcs, which sharpe is not"
    ),
    (".claude/context/analytics.md", "test_regression.py"): (
        "cited as a consumer of the cost model, not as anything claimed absent"
    ),
    (".claude/context/analytics.md", "evaluate"): (
        "a forecast-book function name; the claim on its line is about the "
        "parent's book-dependent attribution"
    ),
}

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
    #: Context a human may want without it counting as something to triage.
    #: A check that is never clean trains dismissal, so anything the check
    #: cannot tie to this branch belongs here rather than in ``findings``.
    note: str | None = None


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
    """Which queue items share vocabulary with this branch? Judge each one.

    Nothing swept for this, so a finished item survived under a heading telling
    the next session it was still owed. The earlier mitigation keyed on added
    Python symbols and therefore could not see a docs-only branch at all; this
    keys the handoff's own tokens against the diff content, which every branch
    has.

    ⚠ **It reports RELEVANCE, never closure, and the wording says so.** Token
    overlap cannot distinguish an item's subject from its vocabulary: across four
    runs the false positives all came from area nouns (`tools/`, `.claude/`,
    `YYYY-MM-DD`), and one item reliably matches ITSELF because its body is about
    this very check.

    ⚠ **Do NOT "fix" this by matching the item's action phrase instead of its
    nouns.** That was the standing proposal until 2026-08-20, when the check
    produced its first true positive — and that one ALSO matched on nouns
    (`H-001`, `H-002`), because for that item the nouns *were* the outcome. An
    action-phrase discriminator would have suppressed the one hit that mattered
    and kept none of the noise. A token-count threshold is likewise ruled out:
    one false positive matched three tokens.

    **Bias toward reporting.** A false positive costs a glance — which is why the
    matched tokens are printed — and a silent miss ships a queue that reads as
    current.
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
                    f"item {idx} possibly related — judge it "
                    f"(shared tokens: {', '.join(hits[:6])})\n      {head}…",
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


def check_negative_claims(
    runner: Runner, diff: str, diff_names: str
) -> tuple[list[Finding], int, int]:
    """Docs asserting the absence of something THIS branch just added.

    Returns ``(findings, suppressed, exempted)``. The absence corpus is a
    property of the tree, not of the branch, so reporting all of it every run
    made this the one leg that was never clean — and a check that is never clean
    trains dismissal exactly as a check that is never green stops being read. The
    scope is the intersection with the branch, which is what the sentence beside
    it always claimed; both remainders are counted into a note.

    Scoping is on the claim line's own distinctive tokens against the diff's
    ADDED lines. Additions only: a branch that REMOVES the named thing makes
    an absence claim more true, not less.

    ⚠ A claim line with no extractable token cannot be ruled out, so it is
    reported. This leg fails OPEN on purpose — a miss ships a doc denying
    something now present, which is the whole harm the check exists to catch.

    ⚠ ``NEGATIVE_CLAIM_EXEMPT`` suppresses a hit only when EVERY matched token
    is exempt for that path. One unexempt token reports the whole line, so an
    entry narrows a finding rather than deleting it — the exemption cannot grow
    into the wide token filter it exists instead of.
    """
    added = "\n".join(line for line in diff.splitlines() if line.startswith("+"))
    haystack = added + "\n" + diff_names
    out = runner(["git", "grep", "-nI", "-e", "x", "--", *NEGATIVE_CLAIM_PATHS])
    findings: list[Finding] = []
    suppressed = 0
    exempted = 0
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3 or "post-branch" in parts[0]:
            continue
        m = NEGATIVE_CLAIM_RE.search(parts[2])
        if not m:
            continue
        tokens = extract_tokens(parts[2])
        hits = sorted(t for t in tokens if t in haystack)
        if tokens and not hits:
            suppressed += 1
            continue
        if hits and all((parts[0], h) in NEGATIVE_CLAIM_EXEMPT for h in hits):
            exempted += 1
            continue
        why = f" (matched {', '.join(hits[:3])})" if hits else " (no token to scope on)"
        findings.append(
            Finding("negative-claims", f"{parts[0]}:{parts[1]}: {m.group(0)}{why}")
        )
    return findings, suppressed, exempted


# ------------------------------------------------------------------ execution


def _negative_claims_result(runner: Runner, diff: str, diff_names: str) -> CheckResult:
    """Wrap the check so both remainders are a note, not a finding.

    The exempt count is printed rather than swallowed: an allowlist nobody can
    see is a mute, and a mute is what this leg's own history argues against.
    """
    findings, suppressed, exempted = check_negative_claims(runner, diff, diff_names)
    parts = []
    if suppressed:
        parts.append(
            f"{suppressed} standing absence claim(s) in the tree are unrelated "
            "to this diff (scoped out, not dismissed)"
        )
    if exempted:
        parts.append(
            f"{exempted} scoped in only by token(s) on NEGATIVE_CLAIM_EXEMPT "
            "(reason inline there)"
        )
    return CheckResult("negative-claims", findings, note="; ".join(parts) or None)


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
        _negative_claims_result(runner, diff, diff_names),
        CheckResult("doc-indexes", _check_doc_indexes()),
        CheckResult("md-atx", _check_md_atx(changed_md)),
        CheckResult("memory-cap", _check_memory_cap()),
        CheckResult("handoff-size", _check_handoff_size(handoff)),
        CheckResult("stale-anchors", _check_stale_anchors()),
        sensitive_terms_result(runner),
    ]
    return results


def load_sensitive_terms(text: str) -> list[str]:
    """Non-empty, non-comment lines, lowercased."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line:
            out.append(line)
    return out


def mask_term(term: str) -> str:
    """Enough to identify, not enough to re-state.

    This output is read in a terminal and pasted into handoffs and PR bodies,
    which are themselves tracked or backed up. Printing the term in full would
    reproduce the leak inside the report about the leak — the same shape as the
    ``gh pr create`` hook firing on its own documentation.
    """
    return f"{term[:3]}…" if len(term) > 3 else "…"


def _resolve_terms(terms: Sequence[str] | None) -> list[str]:
    if terms is not None:
        return list(terms)
    if not SENSITIVE_TERMS.exists():
        return []
    return load_sensitive_terms(SENSITIVE_TERMS.read_text(encoding="utf-8"))


def _not_configured() -> Finding:
    return Finding(
        "sensitive-terms",
        f"NOT CONFIGURED — no {SENSITIVE_TERMS}; this gate is not "
        "running, which is NOT the same as passing",
    )


def scan_text_for_terms(label: str, text: str, terms: Sequence[str]) -> list[Finding]:
    """Sensitive terms in a composed text that has not been published yet.

    Line numbers only, never the surrounding text: the match sits inside the
    very prose being screened, so quoting context would reproduce the term the
    masking exists to withhold.
    """
    numbered = list(enumerate(text.splitlines(), 1))
    findings = []
    for term in terms:
        at = [n for n, line in numbered if term in line.lower()]
        if not at:
            continue
        shown = ", ".join(str(n) for n in at[:5]) + (" …" if len(at) > 5 else "")
        findings.append(
            Finding(
                "sensitive-terms",
                f"{mask_term(term)} in {label} at line(s) {shown} — a PR title "
                "or body is PUBLIC the moment it posts, and editing it later "
                "does not unpublish it",
            )
        )
    return findings


def sensitive_text_result(
    texts: Sequence[tuple[str, str]], terms: Sequence[str] | None = None
) -> CheckResult:
    """Screen a composed PR title/body BEFORE `gh pr create` posts it.

    ⚠ **The fourth exposure surface, and the only indexable one.** The three
    legs of :func:`sensitive_terms_result` ask about the tracked tree and this
    branch's commits; a PR title and body are neither, so that gate reports
    ``clean`` on a body naming every term — correctly, and uselessly. It
    happened live while shipping #245: the first draft of that PR's body named
    all three, in the very window the gate exists to make safe.

    Screened by hand twice before this existed, both times by a throwaway loop
    over the term list. A recipe that has to be remembered is the failure this
    repo keeps re-learning, so the loop is the feature.
    """
    resolved = _resolve_terms(terms)
    if not resolved:
        return CheckResult("sensitive-terms", [_not_configured()])
    findings = [
        f for label, text in texts for f in scan_text_for_terms(label, text, resolved)
    ]
    note = None
    if not findings:
        note = (
            f"{len(resolved)} term(s) checked against "
            f"{len(texts)} composed text(s); nothing to mask"
        )
    return CheckResult("sensitive-terms", findings, note=note)


def sensitive_terms_result(
    runner: Runner, terms: Sequence[str] | None = None
) -> CheckResult:
    """Pre-flip gate: would making this repo public expose a work identifier?

    Three questions, because they fail differently. The tracked tree answers
    "is it visible now"; the branch's commit CONTENT answers "am I adding one";
    and the commit MESSAGES answer the one that no file edit can ever undo. The
    flip republishes the entire history, so deleting the file later does not
    unexpose the blob — and a message cannot be deleted at all short of a
    rewrite.

    **The message leg is not a refinement here either.** Re-measured 2026-08-20
    against the configured list, the tracked tree holds **0** hits for all three
    terms while the history holds them on BOTH other surfaces — **6** commits by
    message and **10** by content::

        git log --all -i --grep=<term> --format=%H | wc -l   # message
        git log --all -S <term> --format=%H | wc -l          # content

    So a gate asking only "is it in the tree" reads clean against a history that
    carries every term. ⚠ **The per-term message split is 1 / 3 / 2, not the
    3 / 2 / 1 filed in memory** — the multiset is right and the attribution was
    not, which is why the totals agreed and nobody noticed. Authors measure clean.

    main's pre-existing occurrences are deliberately NOT re-reported: the
    operator ruled ACCEPT AND DOCUMENT on that baseline on 2026-08-19, and a
    check that is never clean trains dismissal.

    ⚠ **The fourth surface is NOT here.** A PR title and body are neither the
    tree nor a commit, so screening them is :func:`sensitive_text_result`
    (``--text``), run before ``gh pr create``.
    """
    terms = _resolve_terms(terms)
    if not terms:
        return CheckResult("sensitive-terms", [_not_configured()])

    findings: list[Finding] = []
    for term in terms:
        tracked = runner(["git", "grep", "-il", term, "--", "."]).split()
        if tracked:
            shown = ", ".join(tracked[:3]) + (" …" if len(tracked) > 3 else "")
            findings.append(
                Finding(
                    "sensitive-terms",
                    f"{mask_term(term)} in {len(tracked)} tracked file(s): {shown}",
                )
            )
        messages = runner(["git", "log", "main..HEAD", "--format=%B%n%s"])
        if term in messages.lower():
            findings.append(
                Finding(
                    "sensitive-terms",
                    f"{mask_term(term)} in a commit MESSAGE on this branch — no "
                    "file deletion reaches a message; only a history rewrite does",
                )
            )
        introduced = runner(["git", "log", "--oneline", "main..HEAD", "-S", term])
        if introduced.strip():
            n = len(introduced.strip().splitlines())
            findings.append(
                Finding(
                    "sensitive-terms",
                    f"{mask_term(term)} introduced by {n} commit(s) on this branch "
                    "— a flip republishes the whole history, so scrubbing it in a "
                    "later commit will NOT unexpose it",
                )
            )

    note = None
    if not findings:
        note = (
            f"{len(terms)} term(s) checked against the tracked tree and this "
            "branch's commits; main's accepted historical baseline is not re-reported"
        )
    return CheckResult("sensitive-terms", findings, note=note)


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


#: Comfortably above the handoff's observed steady-state band (181-201 lines over
#: five stamps) so a normal run is silent, and low enough that sustained growth is
#: reported while it is still cheap to prune.
HANDOFF_MAX_LINES = 240


def _check_handoff_size(handoff: str) -> list[Finding]:
    """Is the handoff past the size where it stops being read?

    ⚠ **This used to compare the file against a `Line count:` stamp the file
    carried about itself** -- a number whose only purpose was to be checked, and
    which cost a read / `wc -l` / edit / re-read cycle on every run. Worse, the
    stamp regex failing to match returned `[]`, so removing the stamp would not
    have turned the leg red; it would have gone **vacuously green forever**. A
    check that can never fire is dismissal with extra steps. The measurement now
    has an external referent, which is the only kind that can be wrong.
    """
    if not handoff:
        return []
    lines = len(handoff.splitlines())
    if lines > HANDOFF_MAX_LINES:
        return [
            Finding(
                "handoff-size",
                f"handoff is {lines} lines (cap {HANDOFF_MAX_LINES}) — "
                "prune before adding",
            )
        ]
    return []


def _read_text_arg(path: str) -> str:
    """A composed text to screen. Never swallows a read error.

    ``_run``'s swallow-and-continue is right for one leg of a twelve-leg sweep
    and wrong here: an unreadable body would render as a clean single-check run,
    which is the report this mode exists to make impossible.
    """
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def render(results: Sequence[CheckResult]) -> tuple[list[str], int]:
    out = ["post_branch_checks — mechanical sweep", ""]
    total = 0
    for r in results:
        if r.skipped:
            out.append(f"  {r.name:<16} SKIPPED  ({r.skipped})")
            continue
        if not r.findings:
            out.append(f"  {r.name:<16} clean")
            if r.note:
                out.append(f"      note: {r.note}")
            continue
        total += len(r.findings)
        out.append(f"  {r.name:<16} {len(r.findings)} to triage")
        for f in r.findings:
            out.append(f"      {f.detail}")
        if r.note:
            out.append(f"      note: {r.note}")
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
        "--text",
        action="append",
        metavar="PATH",
        help="screen a composed PR title/body for sensitive terms before it is "
        "posted; `-` reads stdin, repeatable. Runs ONLY that check, and needs "
        "no git surface",
    )
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="always exit 0 (findings are advisory, not a gate)",
    )
    args = parser.parse_args(argv)

    if args.text:
        if args.check:
            # Silently honouring one and dropping the other is how a session
            # reads a pass it never asked for.
            print(
                "post_branch_checks: --text runs alone; drop --check",
                file=sys.stderr,
            )
            return 2
        try:
            texts = [(p, _read_text_arg(p)) for p in args.text]
        except OSError as exc:
            print(f"post_branch_checks: {exc}", file=sys.stderr)
            return 2
        lines, total = render([sensitive_text_result(texts)])
        for line in lines:
            print(line)
        return 0 if (args.exit_zero or total == 0) else 1

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
