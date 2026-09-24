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
import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.claude_home import memory_dir
from tools.stale_anchors import default_resolver, describe, scan

Runner = Callable[[Sequence[str]], str]

HANDOFF = Path("docs/plans/next-conversation-prompt.md")

#: Names that must never enter a tracked file — employer, clients, work repos.
#: **Gitignored on purpose (`.gitignore:25`): a tracked list of the words you are
#: hiding is the leak it exists to prevent.** It therefore dies on a reclone, like
#: the hooks did before `.claude/` was inverted to a denylist, which is why an
#: absent list is a FINDING rather than a SKIP.
SENSITIVE_TERMS = Path(".claude/sensitive-terms.txt")
#: Derived, never a literal. This was the old Linux box's absolute path spelled
#: out in a tracked file, so it resolved to nothing after the Windows migration
#: and `memory-cap` reported against a file it had never found -- silently, in
#: the direction of "nothing to do".
MEMORY_DIR = memory_dir(Path(__file__).resolve().parent.parent)
MEMORY = MEMORY_DIR / "MEMORY.md"

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

#: Markdown emphasis, which this tree writes mid-phrase ("has **no daemon at
#: all**"). Without a slot for it, every bolded absence claim reads as absent.
_EMPH = r"[*_`]*"

#: Absence-language. The first six alternations are phrasings harvested from past
#: incidents, and an allowlist of phrasings can only catch shapes already seen:
#: measured against the signal-timer branch (#261) it missed **8 of 8** claim
#: lines that branch falsified, every one written in the plain "there is no X" /
#: "X has no Y" form, which is how absence is normally written. The last three
#: alternations are that form, and they restore 8 of 8 across 6 of 6 files.
#: Generic phrases ("for now", "unwired", "stop-gap") stay OUT — each pulled
#: double-digit false positives for no catch.
#:
#: ⚠ **``has no`` is ANCHORED to a subject that is not a third party**, because
#: most of this tree's bare ``has no`` lines are claims about something ELSE
#: lacking something — "yfinance OHLCV has no taker data", "the endpoint has no
#: children field" — which no wifey branch can falsify. The line-initial arm
#: exists for one real shape: a claim whose subject sits on the PREVIOUS line
#: ("unlike the parent, this repo" / "has NO signal-watch timer").
#:
#: ⚠ **EVERY FIGURE ONCE QUOTED IN THIS BLOCK WAS MEASURED ON 15% OF THE
#: CORPUS** and has been removed rather than restated. Until 2026-08-26 the
#: corpus query filtered on the letter ``x`` (see :func:`check_negative_claims`),
#: so "16 corpus lines", "33 -> 21" and "0-5 to 6-16 findings per run" each
#: described a tree this leg was never reading. ⚠ **A number is only re-usable
#: if the thing it measured still exists** — re-derive against the current query
#: before quoting any of it, and note that the direction of the error was
#: flattering: the leg looked quiet because it was mostly blind.
#:
#: ⚠ **Widening this regex is a DIFFERENT knob from widening the token list**,
#: which is #250's trap: tokens SCOPE a claim line against the diff, so widening
#: those scopes lines in wholesale. This widens what counts as a claim.
#:
#: ⚠ **Re-measure any further widening, and price the TRIAGE LOAD, not just the
#: catch.** Measured over the six branches to 7519f0b on the FIXED corpus, this
#: leg reports **13.2 findings and 18.2 soft per run**, against 8.5 findings when
#: it could see 15% of the tree — i.e. **4.2x quieter per corpus line** while no
#: longer blind to 46 of 69 claim-shaped lines. That cost is
#: paid on EVERY branch and it is the real argument against going wider: a leg
#: that is never clean trains dismissal exactly as a check that is never green
#: stops being read. An alternation can only ADD matches — it was the 2026-08-20h
#: SCOPING change that could REMOVE them, which is why that warning sits on
#: ``NEGATIVE_CLAIM_EXEMPT`` and not here.
#: ⚠ **One adverb defeated the whole anchor.** ``wifey has no daemon`` matched
#: while ``wifey still has no daemon`` did not, so two live repo-self claims sat
#: outside the corpus for a word that carries no meaning here.
_CLAIM_ADVERB = r"(?:\s+(?:still|now|yet|already|currently|itself))*"

#: Subjects that are unambiguously this repo.
_CLAIM_SELF = r"(?:wifey|this (?:repo|fork|tree|skill)|the (?:fork|repo))"
_CLAIM_SELF_RE = re.compile(_CLAIM_SELF, re.IGNORECASE)

#: Double-quoted prose spans — the bare-English equivalent of a backticked
#: token, and the only subject some absence claims name at all.
_QUOTED = re.compile(r'"([^"\n]{3,60})"')

#: ⚠ **A definite noun phrase is repo-self unless it names a third party.** The
#: allowlist above could not reach "**The 505-member research universe** has NO
#: scheduled refresher" — a claim #265 falsified and that was still standing in
#: ``Makefile`` when this was written. Only two third-party subjects in this tree
#: take "the", so the exclusion is a two-word lookahead rather than a list to
#: maintain; every other third party ("yfinance", "equities", "markdownlint")
#: takes no determiner and so never reaches this arm at all. Measured cost over
#: the six branches to 7519f0b: **+0.2 findings per run**, measured against the
#: shipped scoping rather than against an earlier draft of it.
_CLAIM_DEFINITE = (
    r"(?:the (?!parent\b|endpoint\b|other\b|same\b)[\w-]+(?: [\w-]+){0,2})"
)

NEGATIVE_CLAIM_RE = re.compile(
    r"(never|not) (yet )?ported"
    r"|no (reader|host|consumer)\b"
    r"|until (that|the) port lands"
    r"|silent accumulator"
    r"|accumulates? unscored"
    r"|is not (yet )?(available|implemented|wired)"
    rf"|there (is|are) {_EMPH}(\w+ )?{_EMPH}no\b"
    rf"|(?:{_CLAIM_SELF}|{_CLAIM_DEFINITE}){_CLAIM_ADVERB}\s+(has|have) {_EMPH}no\b"
    rf"|^#?\s*(has|have) {_EMPH}no\b"
    r"|\bnothing (installs?|reads?|runs?|writes?|consumes?|enforces?|owns?|tracks?)\b",
    re.IGNORECASE,
)

#: Determiners, pronouns and degree words: real words that scope nothing. A
#: claim's subject has to be specific enough that finding it in a diff means
#: something, which is ``extract_tokens``' own rule applied to bare prose.
_SUBJECT_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "it",
        "its",
        "them",
        "they",
        "this",
        "that",
        "these",
        "those",
        "such",
        "any",
        "one",
        "more",
        "longer",
        "further",
        "own",
        "here",
        "there",
        "still",
        "yet",
        "real",
        "single",
        "other",
        "second",
        # Degree and manner adverbs: content-shaped, but they scope nothing.
        # "no allowlist FOR orphaned ratings" scoped in on "for", a word in
        # every diff ever written.
        "for",
        "exactly",
        "like",
        "simply",
        "merely",
        "only",
        "just",
        "almost",
        "nearly",
        "quite",
        "very",
        "rather",
        "less",
        "else",
        "otherwise",
        "far",
        "all",
        "and",
        "but",
        "with",
        "from",
        "than",
        "then",
    }
)

#: The subject of a plain-form absence claim: the words the "no" attaches to.
_CLAIM_SUBJECT_RE = re.compile(
    rf"(?:there (?:is|are) {_EMPH}(?:\w+ )?{_EMPH}no|(?:has|have) {_EMPH}no"
    rf"|nothing {_EMPH}\w+)"
    r"((?: [\w-]+){0,4})",
    re.IGNORECASE,
)

#: ⚠ **In the "X has no Y" form the discriminating noun phrase sits BEFORE the
#: marker**, and reading only forwards is why "The 505-member research universe
#: has NO scheduled refresher" scoped on ``{scheduled, refresher}`` — words
#: absent from the very branch that falsified it, while ``universe`` sat one
#: word to the left and was all over that diff. Words are taken RIGHT-to-left:
#: the head noun abuts the verb, so "the 505-member research **universe**" is
#: the half worth keeping.
_CLAIM_HEAD_RE = re.compile(
    rf"((?:[\w-]+ ){{0,4}})(?:has|have) {_EMPH}no\b",
    re.IGNORECASE,
)


def _is_distinctive(token: str) -> bool:
    """Is a diff hit on this token evidence, or a coincidence?

    Structural rather than a word list, because a word list is the knob this
    leg's own comments warn against widening until the number goes away.
    ``signal-watch``, ``Type=oneshot`` and ``db_retry`` carry punctuation, a
    digit or a capital and name one thing; ``state``, ``write`` and ``path`` are
    plain lowercase English and appear in almost every diff for unrelated
    reasons. ⚠ The proxy is imperfect in one known direction — a distinctive but
    plain-lowercase name like ``codecov`` reads as generic and is demoted to the
    note rather than reported. That is the safe direction: the note still names
    it, so the cost is a re-read rather than a miss.
    """
    return any(c.isupper() or c.isdigit() or c in "-_./=" for c in token)


def claim_subject_tokens(text: str) -> set[str]:
    """Scoping fallback for a plain-form claim carrying no backticked token.

    ``extract_tokens`` keys on backticked spans, which prose absence claims often
    have none of — "this fork has **no daemon at all**" names its subject in bare
    English. Before this, such a line was unscopable and therefore reported on
    EVERY branch forever, and widening ``NEGATIVE_CLAIM_RE`` to the plain form
    took that population from **0 lines to 11**. A leg that is never clean trains
    dismissal exactly as a check that is never green stops being read, so the
    widening and this fallback are one change, not two.

    ⚠ **This is NOT the token list #250 widened.** That knob scopes claim lines
    IN wholesale, adding findings; this one gives a previously-unscopable line a
    way to be scoped OUT, and it can only ever REMOVE a report.

    ⚠ **Fail-open survives where it is still earned.** A claim whose subject is
    all stopwords — or that runs off the end of its line, as "…and there is still
    no" does with "wifey daemon" on the next — yields nothing here and is
    reported, because it genuinely cannot be ruled out.
    """

    def _pick(raw: str, from_right: bool) -> list[str]:
        words = [w.strip('*_`.,:;()[]|"').lower() for w in raw.split()]
        keep = [w for w in words if len(w) >= 3 and w not in _SUBJECT_STOP]
        return list(reversed(keep))[:2] if from_right else keep[:2]

    out: set[str] = set()
    # ⚠ EVERY marker, not just the first. README's "Nothing consumes it — there
    # is no codecov/coveralls step" put an anaphoric "it" under the first marker
    # and the real subject under the second, so keying on `.search` read the
    # whole line as unscopable and it failed open on every branch forever.
    for m in _CLAIM_SUBJECT_RE.finditer(text):
        out.update(_pick(m.group(1), from_right=False))
    for head in _CLAIM_HEAD_RE.finditer(text):
        # ⚠ Skip a repo-self head: "this fork has no daemon" would otherwise
        # scope on `fork`, a word that means nothing in a diff of this repo. The
        # subject is worth extracting only when it names WHICH part is denied.
        if not _CLAIM_SELF_RE.search(head.group(1)):
            out.update(_pick(head.group(1), from_right=True))
    # A quoted phrase names its subject as surely as a backticked one; this tree
    # writes 'There is no "Agent Skills table"' where code would write a span.
    for quoted in _QUOTED.findall(text):
        out.update(_pick(quoted, from_right=False))
    return out


#: ⚠ **``deploy/`` ships WITH the widened regex above, never alone.** Against the
#: unwidened one it surfaces ZERO hits — this tree's ``deploy/`` absence claims
#: are all written in the plain form — so adding the path by itself is free and
#: worthless, while 2 of the 8 claims #261 falsified sat here, out of reach at
#: any regex. Both halves ship together or neither does.
NEGATIVE_CLAIM_PATHS = (
    "CLAUDE.md",
    "README.md",
    "Makefile",
    "docker-compose.yml",
    ".claude",
    "deploy",
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
    # ⚠ The two entries below are keyed on a matched MARKER, not a token: their
    # lines name no subject this tool can reach, so without them the widened
    # regex leaves the leg permanently unclean, which is what it was extracted
    # from prose to stop being.
    (".claude/skills/sanity-check/SKILL.md", "nothing reads"): (
        "not an absence claim at all — the sentence is 'matching nothing reads "
        "exactly like \"no problems found\"', where 'nothing' is the thing "
        "MATCHED, not a missing artifact. The regex cannot tell a quantifier "
        "from a subject, and this line is the one place in the tree where that "
        "distinction bites"
    ),
    (".claude/skills/sync-parent/SKILL.md", "there are no"): (
        "the sentence is 'there are no merge commits' and its subject sits on "
        "the NEXT line, out of reach of a line-unit check. It is also a claim "
        "about the PARENT's squash-merge habit, so no wifey branch can falsify "
        "it — re-read this if the parent ever stops squashing"
    ),
    ("README.md", "nothing installs"): (
        "'nothing installs them' — the subject is a pronoun, and the antecedent "
        "('systemd user timers') sits earlier on a 3 KB line. The claim is a "
        "DELIBERATE permanent property: nothing in the repo installs the units, "
        "and the 2026-08-26 timer install was operator-side, which did not "
        "falsify it. ⚠ Adding an installer to the repo does, so strike this "
        "entry then rather than re-scoping the line"
    ),
    # The two below are also keyed on a matched marker. Each is a sentence no
    # branch can settle, so without an entry the leg would never report clean.
    (".claude/skills/pr-summary/SKILL.md", "there is no"): (
        "'there is no collaborator permission to lack' is a claim about "
        "GitHub's permission model on a single-owner fork, not about anything "
        "this repo could grow. Its subject also wraps to the next line, so no "
        "line-unit scoping can reach it"
    ),
    ("deploy/README.md", "nothing installs"): (
        "the one claim here that is TRUE and meant to stay true: nothing in the "
        "repo installs the systemd units, because installing one dispatches "
        "Telegram and is an operator decision. CLAUDE.md states the same "
        "property. ⚠ Known hole, taken deliberately: this mutes the marker for "
        "BOTH sentences carrying it in this file, so if the repo ever does "
        "install a unit, this leg will not be what tells you — the policy line "
        "in CLAUDE.md is"
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

    ⚠ **A LEADING-DOT name also probes its dotless form, because a word boundary
    cannot match in front of the dot.** The caller anchors each probe with one, and
    a boundary needs a word character on one side: in ``a `.gitattributes` file`` the
    characters either side of the dot are both non-word, so the assertion fails and
    the file reads UNDOCUMENTED no matter how well documented it is. Measured on the
    Windows-scheduling branch: `.gitattributes` appeared in CLAUDE.md and the probe
    returned **0 hits**. That makes the finding unclearable rather than merely wrong,
    and a leg that can never be clean trains dismissal of the legs that can.
    """
    p = Path(path)
    if p.name in SHARED_CONSTANT_BASENAMES and p.parent.name:
        return [p.parent.name]
    if p.name.startswith(".") and len(p.name) > 1:
        return [p.name, p.name[1:]]
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
            # ⚠ Strip emphasis and punctuation but keep the UNDERSCORE form as
            # well. Stripping ``_`` unconditionally turned ``__main__`` into
            # ``main``, which is a stopword — so the one genuinely distinctive
            # token on "there is no `__main__` and no argparse" was destroyed on
            # its way to the filter, and the line read as unscopable.
            base = cand.strip("`*.,:;()[]")
            for form in (base, base.strip("_")):
                if len(form) >= 3 and form not in _STOPWORDS:
                    out.add(form)
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


#: How many of a package's members the context docs must name before we treat
#: those mentions as an INVENTORY rather than incidental prose. Two is the
#: smallest number that cannot be one passing reference.
ENUMERATION_QUORUM = 2


def enumerated_members(pkg: Path, context_blob: str) -> set[str]:
    """Members of ``pkg`` that `.claude/context/` names inside backticks.

    Backticks are the discriminator that a bare word-boundary probe lacks: docs
    write a *file* as ``sharpe.py`` and a *concept* as plain "sharpe", and only
    the former is a claim about the package's contents.
    """
    if not pkg.is_dir():
        return set()
    return {
        member.name
        for member in pkg.glob("*.py")
        if member.name != "__init__.py"
        and re.search(rf"`[^`\n]*{re.escape(member.name)}[^`\n]*`", context_blob)
    }


def check_new_modules(
    added: Sequence[str], context_blob: str, *, root: Path = Path(".")
) -> list[Finding]:
    """Every added module should reach `.claude/context/`.

    Two questions, because a substring probe cannot answer the second. Where the
    docs merely *mention* a package, ask whether the module is named at all.
    Where they keep an **inventory** of it, ask whether the module is IN that
    inventory — and report the set difference, which is an external referent.

    ⚠ This exists because the probe form reported COVERED on a real omission:
    `analytics/research_guards/sharpe.py` landed while `.claude/context/analytics.md`
    enumerated ten of the package's eleven members, and the word "sharpe" appears
    throughout that file as ordinary prose. **Tightening the regex cannot fix
    that** — the hit was a real token in real prose — so the check has to change
    what it asks, not how precisely it asks it.
    """
    findings = []
    for path in added:
        if not path.endswith(".py") or path.startswith(("tests/", "docs/")):
            continue
        name = Path(path).name
        pkg = root / Path(path).parent
        documented = enumerated_members(pkg, context_blob)
        if len(documented - {name}) >= ENUMERATION_QUORUM:
            if name not in documented:
                present = {m.name for m in pkg.glob("*.py") if m.name != "__init__.py"}
                missing = ", ".join(sorted(present - documented))
                findings.append(
                    Finding(
                        "new-modules",
                        f"NOT IN INVENTORY: {path} — .claude/context/ enumerates "
                        f"{len(documented)} member(s) of {pkg.as_posix()} and this "
                        f"is not among them (undocumented: {missing})",
                    )
                )
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


#: A target declaration. Deliberately the same shape `check_new_targets` matches,
#: minus the leading `+`, so the two legs cannot disagree about what a target is.
#: ⚠ Note the char class has no `.`, matching wifey's own `new-targets` regex rather
#: than the parent's — `.PHONY` must fail this, and it does so here on the leading
#: `[a-z]` instead.
TARGET_DECL_RE = re.compile(r"^([a-z][a-z0-9_-]*):")


def names_token(text: str, token: str) -> bool:
    r"""True when ``text`` names ``token`` as a whole word, hyphens included.

    ⚠ **`\b` is NOT enough for a hyphenated name.** `-` is a non-word character, so
    `\bwifey-universe-sync\b` matches happily *inside* `wifey-universe-sync-extra` —
    the `-w` trap this tool already documents for `_`, arriving from the other side,
    because `_` is word-constituent and `-` is not. Nearly every Make target here is
    hyphenated, so the plain form would credit a doc that names a DIFFERENT target.

    ⚠ **`check_new_targets` above has the same `\b` and is deliberately unchanged**,
    as upstream left it: there a substring hit reads as DOCUMENTED, so it fails in the
    quieter direction, and widening it could newly fire on real branches. Filed to the
    skill-fix queue rather than changed without its own verification.
    """
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text) is not None


def changed_line_numbers(diff: str) -> set[int]:
    """New-file line numbers a unified diff touches.

    A **deletion** carries no new-file line number of its own, so it is blamed on the
    position it vacated. Skipping it would make a recipe line *removed* from a target
    invisible to `check_amended_targets` — the same amendment, arriving as a
    subtraction.
    """
    touched: set[int] = set()
    new_ln = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_ln = int(m.group(1)) if m else 0
            continue
        if not new_ln or line.startswith(("+++", "---", "diff ", "index ")):
            continue
        if line.startswith("+"):
            touched.add(new_ln)
            new_ln += 1
        elif line.startswith("-"):
            touched.add(new_ln)
        else:
            new_ln += 1
    return touched


def targets_by_line(makefile_text: str) -> dict[int, str]:
    """Map each 1-based Makefile line to the target whose recipe it sits in.

    A declaration claims its own line and every line after it until something
    un-indented ends the recipe. `.PHONY:` NAMES targets without being one, so it fails
    `TARGET_DECL_RE` and, being un-indented, also clears the current target — otherwise
    every branch that touches `.PHONY` would report its targets as amended.
    """
    out: dict[int, str] = {}
    current: str | None = None
    for i, line in enumerate(makefile_text.splitlines(), 1):
        m = TARGET_DECL_RE.match(line)
        if m:
            current = m.group(1)
        elif line and not line[0].isspace():
            current = None
        if current:
            out[i] = current
    return out


def check_amended_targets(
    makefile_diff: str, makefile_text: str, docs: Mapping[str, str]
) -> list[Finding]:
    """Docs enumerating a target whose recipe this branch AMENDED.

    `check_new_targets` asks whether an **added** target is documented. It is
    structurally blind to an existing one that gains an override, changes a default or
    renames a variable — the doc still names the target, so every presence check passes
    while its enumeration goes one short. That is the omission blind spot arriving from
    a direction the other four legs cannot see: they all catch a doc that has NEVER
    HEARD OF an artifact, where here the doc names it correctly and is short by one.

    Deliberately does NOT parse what changed. Scoping to `$(if $(VAR),...)` would scope
    to the symptom that happened to be noticed, which CLAUDE.md names as the recurring
    defect in this repo's guard design — a changed default has the same shape and the
    same invisible failure. It reports the target and the docs to re-read, and leaves
    the judgement to a human, which is the contract every other advisory leg keeps.
    """
    if not makefile_diff.strip():
        return []
    line_map = targets_by_line(makefile_text)
    added = {
        m.group(1)
        for line in makefile_diff.splitlines()
        if line.startswith("+") and (m := TARGET_DECL_RE.match(line[1:]))
    }
    touched = {
        target
        for ln in changed_line_numbers(makefile_diff)
        if (target := line_map.get(ln))
    }
    findings = []
    for target in sorted(touched - added):
        naming = sorted(
            path for path, text in docs.items() if names_token(text, target)
        )
        if naming:
            findings.append(
                Finding(
                    "amended-targets",
                    f"AMENDED: {target} — re-read {', '.join(naming)}",
                )
            )
    return findings


def check_negative_claims(
    runner: Runner, diff: str, diff_names: str
) -> tuple[list[Finding], int, int, list[str]]:
    """Docs asserting the absence of something THIS branch just added.

    Returns ``(findings, suppressed, exempted, soft)``. The absence corpus is a
    property of the tree, not of the branch, so reporting all of it every run
    made this the one leg that was never clean — and a check that is never clean
    trains dismissal exactly as a check that is never green stops being read. The
    scope is the intersection with the branch, which is what the sentence beside
    it always claimed; the remainders are counted into a note.

    Scoping is on the claim line's own distinctive tokens against the diff's
    ADDED lines. Additions only: a branch that REMOVES the named thing makes
    an absence claim more true, not less.

    ⚠ **The corpus query is ``-e ""`` and the empty pattern is LOAD-BEARING.**
    It read ``-e "x"`` from the #218 extraction until 2026-08-26, which is not
    "every line" — it is *every line containing the letter x*, and it silently
    cut the declared corpus to **1,892 of 12,277 non-blank lines tree-wide (15%)**,
    and to **195 of CLAUDE.md's own 879 (22%)**.
    **46 of the 69 claim-shaped lines then in the corpus (67%) contain no ``x`` at
    all** and had never
    been reachable, ``Makefile``'s "The 505-member research universe has NO
    scheduled refresher" among them — a claim #265 falsified and this leg could
    not see. Every triage figure once quoted for this leg was measured on that
    truncated corpus. ⚠ **The tests inject a fake runner, so no test exercised
    the real argv**; ``TestCorpusQueryReachesEveryLine`` now pins it, with a
    line carrying no ``x`` as the positive control. A filter nobody declared
    reads exactly like a corpus nobody wrote a claim into.

    ⚠ **A finding requires a STRONG token — backticked or a hypothesis id.** A
    claim scoped only by bare English ("there is no **state**") matches almost
    any diff, so promoting those to findings put the leg at **31.7 per run**
    against a status quo of 8.5. They are not dropped: they land in ``soft``,
    gated to files this branch actually touched, and the caller counts them into
    the note. Measured over the six branches to 7519f0b, that is **13.2 findings
    and 18.2 soft per run** while reading 6.5x the corpus.

    ⚠ ``NEGATIVE_CLAIM_EXEMPT`` suppresses a hit only when EVERY matched token
    is exempt for that path. One unexempt token reports the whole line, so an
    entry narrows a finding rather than deleting it — the exemption cannot grow
    into the wide token filter it exists instead of.
    """
    added = "\n".join(line for line in diff.splitlines() if line.startswith("+"))
    haystack = added + "\n" + diff_names
    out = runner(["git", "grep", "-nI", "-e", "", "--", *NEGATIVE_CLAIM_PATHS])
    findings: list[Finding] = []
    soft: list[str] = []
    suppressed = 0
    exempted = 0
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3 or "post-branch" in parts[0]:
            continue
        m = NEGATIVE_CLAIM_RE.search(parts[2])
        if not m:
            continue
        # ⚠ EVERY marker on the line must be exempt, mirroring the token rule
        # below. Keying on the first match alone suppressed a real one: #261's
        # README line carries "nothing installs" AND "there is still no", and
        # exempting the former hid the latter — one line, two claims, and only
        # one of them settled.
        markers = {mm.group(0).lower() for mm in NEGATIVE_CLAIM_RE.finditer(parts[2])}
        # A backticked span is the author SAYING this is an identifier, so it
        # always reports; the distinctiveness gate below applies only to tokens
        # this tool inferred from bare prose.
        strong = extract_tokens(parts[2])
        tokens = strong or claim_subject_tokens(parts[2])
        hits = sorted(t for t in tokens if t in haystack)
        if tokens and not hits:
            suppressed += 1
            continue
        if hits and all((parts[0], h) in NEGATIVE_CLAIM_EXEMPT for h in hits):
            exempted += 1
            continue
        if not tokens and all((parts[0], k) in NEGATIVE_CLAIM_EXEMPT for k in markers):
            exempted += 1
            continue
        # ⚠ A hit on a bare English word is not evidence. "there is no **state**"
        # scopes in on any diff that writes the word "state", which is most of
        # them, and promoting those put the leg at 31.7 per run against a status
        # quo of 8.5. They are DEMOTED, never dropped: the note names each one so
        # a human can re-read the paragraph, which is how this leg's only
        # confirmed true positive was ever found.
        if hits and not strong and not any(_is_distinctive(h) for h in hits):
            soft.append(f"{parts[0]}:{parts[1]}")
            continue
        why = f" (matched {', '.join(hits[:3])})" if hits else " (no token to scope on)"
        findings.append(
            Finding("negative-claims", f"{parts[0]}:{parts[1]}: {m.group(0)}{why}")
        )
    return findings, suppressed, exempted, soft


# ------------------------------------------------------------------ execution


def _negative_claims_result(runner: Runner, diff: str, diff_names: str) -> CheckResult:
    """Wrap the check so both remainders are a note, not a finding.

    The exempt count is printed rather than swallowed: an allowlist nobody can
    see is a mute, and a mute is what this leg's own history argues against.
    """
    findings, suppressed, exempted, soft = check_negative_claims(
        runner, diff, diff_names
    )
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
    if soft:
        # Named, never summarised to a bare count: the whole point is that a
        # human can re-read these paragraphs, which is how this leg's only
        # confirmed true positive was ever found.
        parts.append(
            f"{len(soft)} claim(s) carry no token distinctive enough to scope "
            "on, in files this branch touched — RE-READ, do not assume: "
            + ", ".join(soft[:8])
            + (f" (+{len(soft) - 8} more)" if len(soft) > 8 else "")
        )
    return CheckResult("negative-claims", findings, note="; ".join(parts) or None)


def _handoff_leg(
    name: str, handoff: str, findings: Callable[[], list[Finding]]
) -> CheckResult:
    """A leg that can only speak when the handoff exists.

    ⚠ **An absent handoff is a SKIP, never a clean green.** The file is
    gitignored, so a worktree or a fresh clone has none, and a leg reporting
    green there asserts the one thing it never measured — the same
    vacuously-green shape ``handoff-size``'s old `Line count:` stamp had.
    ``handoff-size`` reported exactly that until this helper collected all
    three handoff-dependent legs behind one decision.
    """
    if not handoff:
        return CheckResult(name, skipped="no handoff file")
    return CheckResult(name, findings())


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
    makefile_text = Path("Makefile").read_text(encoding="utf-8", errors="replace")
    enumerating_by_path = _read_each(ENUMERATING_DOCS)

    results = [
        _handoff_leg(
            "queue-items", handoff, lambda: check_queue_items(handoff, diff, diff_names)
        ),
        _handoff_leg(
            "handoff-symbols",
            handoff,
            lambda: check_handoff_symbols(handoff, diff, diff_names),
        ),
        CheckResult("new-files", check_new_files(added, doc_blob)),
        CheckResult("new-modules", check_new_modules(added, context_blob)),
        CheckResult("new-targets", check_new_targets(makefile_diff, doc_blob)),
        CheckResult(
            "amended-targets",
            check_amended_targets(makefile_diff, makefile_text, enumerating_by_path),
        ),
        _negative_claims_result(runner, diff, diff_names),
        CheckResult("doc-indexes", _check_doc_indexes()),
        CheckResult("md-atx", _check_md_atx(changed_md)),
        CheckResult("memory-cap", _check_memory_cap()),
        _handoff_leg("handoff-size", handoff, lambda: _check_handoff_size(handoff)),
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


def _read_each(paths: Sequence[str]) -> dict[str, str]:
    """`_read_all`, but keyed by path.

    `amended-targets` reports WHICH doc to re-read, which a joined blob cannot say.
    Directories expand the same way, so the two helpers cannot disagree about what the
    enumerating corpus is.

    ⚠ **Keys are `as_posix()`, not `str()`.** On Windows `rglob` yields backslashes, so
    the leg printed `.claude\\context\\tools.md` beside `negative-claims`' POSIX paths
    in the same report — and this output gets pasted into handoffs and PR bodies. #302
    was the sharper version of the same class: an allowlist keyed on POSIX paths that
    silently matched nothing once the separators diverged.
    """
    out: dict[str, str] = {}
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*.md")):
                out[f.as_posix()] = f.read_text(encoding="utf-8", errors="replace")
        elif p.exists():
            out[p.as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return out


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

    An **absent** handoff is not this function's call: :func:`_handoff_leg`
    turns it into a SKIP before reaching here, because "no finding" and "no
    handoff" are different states and only one of them is green.
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


UNCOVERED_STEPS: tuple[tuple[str, str], ...] = (
    (
        "Phase 2",
        "the behaviour gate — whether this PR is user-facing at all. No check "
        "can read intent from a diff.",
    ),
    (
        "Phase 3",
        "the doc-surface walk. The sweep finds UNDOCUMENTED new files, targets "
        "and modules; it cannot tell you an existing paragraph went false.",
    ),
    (
        "Phase 4",
        "the SoT reconcile and the BLOCKED re-read — naming each blocked item's "
        "unblocking condition. An item whose blocker you cannot restate is "
        "unexamined, not blocked.",
    ),
    (
        "Phase 5",
        "`make preflight` (it REPLACES this branch's `make test`) and "
        "`make post-branch-text FILE=<path>` on the composed PR title and body. "
        "Neither is any leg above: a posted body is public the moment it lands.",
    ),
    (
        "Phase 6",
        "the zero-commit tail — re-verify PR state, then stamp the handoff, in "
        "that order.",
    ),
)


def uncovered_notice(steps: Sequence[tuple[str, str]] = UNCOVERED_STEPS) -> list[str]:
    """Name the `/post-branch` phases this sweep does NOT reach.

    Ported from parent #697. Upstream found BOTH parallel sessions of one wave
    substituting the mechanical half for the walk, neither being careless — its
    always-loaded tier carried a sibling sentence licensing the substitution. The
    fix belongs on REACHABILITY rather than on another rule: passing the
    mechanical half should not be able to FEEL like passing the walk.

    ⚠ **Two upstream specifics deliberately NOT ported, because their reasons are
    the parent's.** Its notice cites ``Step N`` and a mutation test pins the
    absence of the word *phase*, because its skill's phases are table rows that
    declare no headings, so a phase citation there is a dead anchor. wifey's
    skill has real ``## Phase N`` headings AND ``tools/stale_anchors.py``
    resolves ``phase N`` citations against them (``_HEAD_TYPED``), so here a
    phase number is a CHECKED anchor and the better citation. Porting the
    upstream rule verbatim would have traded a live reference for a vague one.
    """
    out = ["", "  This sweep is the MECHANICAL half only. It does NOT cover:"]
    for name, detail in steps:
        wrapped = textwrap.wrap(detail, width=68)
        out.append(f"    {name:<9} {wrapped[0] if wrapped else ''}")
        out.extend(f"    {'':<9} {line}" for line in wrapped[1:])
    out.append("")
    out.append("  Passing every leg above is not passing /post-branch.")
    return out


def render(
    results: Sequence[CheckResult], *, show_uncovered: bool = False
) -> tuple[list[str], int]:
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
    if show_uncovered:
        out += uncovered_notice()
    return out, total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="post_branch_checks",
        description="Every mechanical /post-branch check, in one run.",
    )
    parser.add_argument(
        "--check",
        action="append",
        metavar="NAME",
        help="run only this named check; repeatable. An unknown name is an "
        "error rather than a quietly shorter run",
    )
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
        # Validate every name before filtering. A typo among several would
        # otherwise run the survivors and print a clean-looking sweep -- the
        # emptiness-reads-as-coverage class this tool exists to catch.
        known = {r.name for r in results}
        unknown = [name for name in args.check if name not in known]
        if unknown:
            print(
                f"post_branch_checks: no such check: {', '.join(unknown)}",
                file=sys.stderr,
            )
            return 2
        wanted = set(args.check)
        results = [r for r in results if r.name in wanted]
    # `--check` runs one leg on purpose; the notice belongs on the full sweep,
    # which is the run a session can mistake for the walk.
    lines, total = render(results, show_uncovered=not args.check)
    for line in lines:
        print(line)
    return 0 if (args.exit_zero or total == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
