"""Detect-and-recommend pipeline for porting parent-repo PRs into the wifey fork.

Read-only on both repos except the wifey memory state file (written only on
``--bump-to``). Enumerates parent PRs merged since the last sync point, classifies
each (SKIP / PORT / EVALUATE, with an ALREADY-APPLIED routing overlay), and writes a
context-rich report to ``docs/plans/parent-sync/parent-sync-<date>.md``.

The parent (``buibui-moon-trader-bot``) squash-merges every PR into a single commit
whose subject ends in ``(#N)`` — there are no merge commits — so PR grouping keys off
that suffix, not ``git log --merges``.

Usage::

    PYTHONPATH=. poetry run python tools/sync_parent.py                  # incremental
    PYTHONPATH=. poetry run python tools/sync_parent.py --from <hash>    # override start
    PYTHONPATH=. poetry run python tools/sync_parent.py --full           # fork -> HEAD
    PYTHONPATH=. poetry run python tools/sync_parent.py --bump-to <hash> # update state only
    PYTHONPATH=. poetry run python tools/sync_parent.py --no-fetch       # local refs only
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

from tools.claude_home import memory_dir

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

WIFEY_REPO_PATH = Path(__file__).resolve().parent.parent

#: The parent checkout, as a SIBLING of this one rather than an absolute
#: literal. True on both boxes (`~/repo/` on Linux, `C:\Users\User\repo\` on
#: Windows) and it survives the next move; the literal it replaced pinned the
#: old work machine's home directory into a tracked file.
PARENT_REPO_PATH = WIFEY_REPO_PATH.parent / "buibui-moon-trader-bot"
FORK_COMMIT = "635ed5a"

WIFEY_MEMORY_DIR = memory_dir(WIFEY_REPO_PATH)
PARENT_MEMORY_PATH = memory_dir(PARENT_REPO_PATH) / "MEMORY.md"
STATE_FILE_PATH = WIFEY_MEMORY_DIR / "project_parent_sync_state.md"

# Parent path -> wifey path. None means "removed in fork (SKIP)".
# Surviving same-name files are intentionally absent: translate_paths() resolves
# them to a "direct" map via a wifey working-tree existence check.
PARENT_TO_WIFEY_PATHS: dict[str, str | None] = {
    # The parent moved its always-loaded instructions into AGENTS.md and left
    # CLAUDE.md an @-importing pointer; wifey still keeps everything in
    # CLAUDE.md. Without this entry the parent's highest-leverage surface falls
    # through to "unmapped" and renders as the same "investigate" noise as a
    # genuinely foreign path. See _INSTRUCTION_FILES for why it stays EVALUATE.
    "AGENTS.md": "CLAUDE.md",
    "analytics/indicators_lib.py": "analytics/strategies/_registry.py",
    "analytics/cme_gap_lib.py": None,
    "utils/binance_client.py": None,
    "monitor/live_price.py": None,
    "monitor/live_position.py": None,
    "trade/open_trades.py": None,
    "analytics/signal_lib.py": "analytics/signal/scanner.py",
    "analytics/backtest_lib.py": "analytics/backtest/engine.py",
    "analytics/stats_lib.py": "analytics/stats/bundle.py",
    "analytics/data_store.py": "analytics/store/data_store.py",
}

# Glob patterns whose match means "removed crypto-only surface (SKIP)".
SKIP_GLOBS = [
    "**/funding_rates*",
    "**/open_interest*",
    "**/cvd_divergence*",
    "**/smt_*",
    "**/funding_extreme*",
    "**/cme_gap*",
]

# Substrings that, if present in a touched path, mark removed crypto surface.
SKIP_SUBSTRINGS = ["taker_buy_volume", "binance", "live_price", "live_position"]

# Ordered (regex, label) rules that cluster PRs into multi-PR "workstreams" for
# the report's Workstreams summary. Checked in order, first match wins (so put
# the more specific campaign before the broader one — e.g. gate_audit before
# Phase A, since #373 mentions both). Anything unmatched falls back to the
# conventional-commit type(scope). Hand-tuned like SKIP_GLOBS: when the parent
# kicks off a new multi-PR campaign, add a rule so its PRs cluster in the report.
WORKSTREAM_RULES: list[tuple[str, str]] = [
    (
        r"live[ -]?parity|into run_backtest|conflict resolver|live cooldown|_CooldownState",
        "live-parity (backtest engine port)",
    ),
    (r"\bBucket C\b", "Bucket C (schema + config)"),
    (r"\bgate_audit\b", "gate_audit (tooling)"),
    (r"Phase A", "Phase A (config decisions)"),
    (r"^build\(deps", "dependency bumps"),
]

# In-repo (and gitignored via ``docs/plans/``), deliberately NOT ``/tmp``: the report
# IS the triage artifact — a reviewer decides PRs against it over days, and the
# sync-state memory only records what was already decided. On 2026-07-29 a ``/tmp``
# clear destroyed a 67-PR report with 57 still undecided, and the range had to be
# re-scanned from scratch. It must outlive a reboot.
REPORT_DIR = WIFEY_REPO_PATH / "docs" / "plans" / "parent-sync"

PathKind = Literal["direct", "renamed", "removed", "skip", "unmapped"]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class SyncStateError(Exception):
    """Raised when the memory state file is present but unparseable."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


class Bucket(StrEnum):
    SKIP = "SKIP"
    PORT = "PORT"
    EVALUATE = "EVALUATE"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


@dataclass
class Commit:
    sha: str
    subject: str
    body: str
    files: list[str]


@dataclass
class PR:
    number: int | None
    title: str
    commits: list[Commit]
    files: list[str]


@dataclass
class WifeyPath:
    parent_path: str
    wifey_path: str | None
    kind: PathKind


# --------------------------------------------------------------------------- #
# Sync-state file
# --------------------------------------------------------------------------- #


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a minimal ``key: value`` frontmatter block between ``---`` fences."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SyncStateError("state file is malformed: missing opening '---' fence")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    raise SyncStateError("state file is malformed: missing closing '---' fence")


def load_sync_state(state_path: Path = STATE_FILE_PATH) -> str:
    """Return the last-synced parent hash, or ``FORK_COMMIT`` on bootstrap."""
    if not state_path.exists():
        return FORK_COMMIT
    fields = _parse_frontmatter(state_path.read_text())
    if "last_synced_hash" not in fields:
        raise SyncStateError("state file is missing 'last_synced_hash' in frontmatter")
    return fields["last_synced_hash"]


def write_sync_state(
    new_hash: str, state_path: Path = STATE_FILE_PATH, note: str = ""
) -> None:
    """Atomically rewrite the state file with the new pointer + a run-history line."""
    today = date.today().isoformat()
    history_line = f"- {today}: bumped to {new_hash}"
    if note:
        history_line += f" ({note})"
    body = (
        f"---\nlast_synced_hash: {new_hash}\nupdated: {today}\n---\n\n"
        "# Parent sync state\n\n"
        "Tracks the last parent commit reviewed by `tools/sync_parent.py`.\n\n"
        "## Run history\n\n"
        f"{history_line}\n"
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(body)
    os.replace(tmp, state_path)


# --------------------------------------------------------------------------- #
# Git helpers + commit fetch
# --------------------------------------------------------------------------- #

_LOG_FORMAT = "%H\x1f%s\x1f%b"
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
_PR_SQUASH_RE = re.compile(r"\(#(\d+)\)\s*$")
_PR_MERGE_RE = re.compile(r"^Merge pull request #(\d+)\b")


def _git(repo: Path, *args: str) -> str:
    """Run a git command in ``repo`` and return stripped stdout. Raises on failure."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def fetch_parent_commits(from_hash: str, no_fetch: bool = False) -> list[Commit]:
    """Return commits in ``from_hash..origin/main`` (oldest first), each with files.

    The parent squash-merges every PR, so this does NOT use ``--merges`` — it walks
    every commit and lets :func:`group_into_prs` key off the ``(#N)`` subject suffix.
    """
    # Validate the start hash is reachable (fail-fast handled by caller catching CalledProcessError).
    _git(PARENT_REPO_PATH, "cat-file", "-e", f"{from_hash}^{{commit}}")
    if not no_fetch:
        _git(PARENT_REPO_PATH, "fetch", "origin", "main")

    raw = _git(
        PARENT_REPO_PATH,
        "log",
        "--reverse",
        f"--format={_LOG_FORMAT}{_RECORD_SEP}",
        f"{from_hash}..origin/main",
    )
    commits: list[Commit] = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, body = (record.split(_FIELD_SEP) + ["", ""])[:3]
        files = _git(PARENT_REPO_PATH, "show", "--name-only", "--format=", sha)
        commits.append(
            Commit(
                sha=sha,
                subject=subject,
                body=body,
                files=[f for f in files.splitlines() if f.strip()],
            )
        )
    return commits


def _pr_number(subject: str) -> int | None:
    """Extract a PR number from a squash ``(#N)`` suffix or legacy merge subject."""
    merge = _PR_MERGE_RE.match(subject)
    if merge:
        return int(merge.group(1))
    squash = _PR_SQUASH_RE.search(subject)
    if squash:
        return int(squash.group(1))
    return None


def group_into_prs(commits: list[Commit]) -> list[PR]:
    """Group commits into PRs by ``(#N)``; commits with no PR number become ``#none``."""
    by_number: dict[int, PR] = {}
    ordered: list[PR] = []
    for commit in commits:
        number = _pr_number(commit.subject)
        if number is None:
            ordered.append(
                PR(
                    number=None,
                    title=commit.subject,
                    commits=[commit],
                    files=list(commit.files),
                )
            )
            continue
        if number not in by_number:
            pr = PR(number=number, title=commit.subject, commits=[], files=[])
            by_number[number] = pr
            ordered.append(pr)
        pr = by_number[number]
        pr.commits.append(commit)
        for f in commit.files:
            if f not in pr.files:
                pr.files.append(f)
    return ordered


# --------------------------------------------------------------------------- #
# Path translation
# --------------------------------------------------------------------------- #


def _wifey_path_exists(rel_path: str) -> bool:
    """True when ``rel_path`` exists in the wifey working tree (patched in tests)."""
    return (WIFEY_REPO_PATH / rel_path).exists()


def _matches_skip_glob(path: str) -> bool:
    if any(fnmatch.fnmatch(path, pat) for pat in SKIP_GLOBS):
        return True
    return any(token in path for token in SKIP_SUBSTRINGS)


def translate_paths(parent_files: list[str]) -> list[WifeyPath]:
    """Map each parent path to a wifey target (or mark removed / skip / unmapped)."""
    out: list[WifeyPath] = []
    for f in parent_files:
        if f in PARENT_TO_WIFEY_PATHS:
            target = PARENT_TO_WIFEY_PATHS[f]
            if target is None:
                out.append(WifeyPath(f, None, "removed"))
            elif target == f:
                out.append(WifeyPath(f, target, "direct"))
            else:
                out.append(WifeyPath(f, target, "renamed"))
        elif _matches_skip_glob(f):
            out.append(WifeyPath(f, None, "skip"))
        elif _wifey_path_exists(f):
            out.append(WifeyPath(f, f, "direct"))
        else:
            out.append(WifeyPath(f, None, "unmapped"))
    return out


# --------------------------------------------------------------------------- #
# Bucket classifier
# --------------------------------------------------------------------------- #

_EVALUATE_PATH_RE = re.compile(
    r"(^config/.*\.toml$|strategy_params|analytics/strategies/)"
)


# Instruction files are never a cherry-pick: the parent's split (an 8 KB
# CLAUDE.md pointer @-importing a 77 KB AGENTS.md) has no wifey twin, so a diff
# against wifey's single CLAUDE.md cannot apply as written. They are also the
# surface a parent PR changes least visibly and most widely, so they must reach
# a human. Mapping AGENTS.md above would otherwise have demoted it from the
# EVALUATE that "unmapped" was granting it, to PORT/cherry-pick-with-edits.
_INSTRUCTION_FILES = frozenset({"AGENTS.md", "CLAUDE.md"})


def _is_evaluate_path(wp: WifeyPath) -> bool:
    """Instruction file, cohort-sensitive path, or unmapped -> needs judgment."""
    if wp.kind == "unmapped":
        return True
    if wp.parent_path in _INSTRUCTION_FILES:
        return True
    return bool(_EVALUATE_PATH_RE.search(wp.parent_path))


def classify_pr(pr: PR, wifey_paths: list[WifeyPath]) -> Bucket:
    """Return SKIP / PORT / EVALUATE. ALREADY-APPLIED is a separate routing overlay."""
    if not wifey_paths:
        return Bucket.EVALUATE
    if all(wp.kind in ("removed", "skip") for wp in wifey_paths):
        return Bucket.SKIP
    # Consider only the non-skipped paths for the PORT/EVALUATE decision.
    relevant = [wp for wp in wifey_paths if wp.kind not in ("removed", "skip")]
    if any(_is_evaluate_path(wp) for wp in relevant):
        return Bucket.EVALUATE
    return Bucket.PORT


# --------------------------------------------------------------------------- #
# Already-applied detection
# --------------------------------------------------------------------------- #

_ADDED_DEF_RE = re.compile(r"^\+\s*(?:async\s+)?def\s+([A-Za-z_]\w+)")
_ADDED_CLASS_RE = re.compile(r"^\+\s*class\s+([A-Za-z_]\w+)")
_ADDED_CONST_RE = re.compile(r"^\+([A-Z_][A-Z0-9_]{3,})\s*[:=]")
_REMOVED_DEF_RE = re.compile(r"^-\s*(?:async\s+)?def\s+([A-Za-z_]\w+)")
_REMOVED_CLASS_RE = re.compile(r"^-\s*class\s+([A-Za-z_]\w+)")
_REMOVED_CONST_RE = re.compile(r"^-([A-Z_][A-Z0-9_]{3,})\s*[:=]")

# Only tokens used SYNTACTICALLY — a parameter, kwarg, assignment target, call or
# subscript. A plain `\w+` sweep also harvests docstring prose ("already",
# "behaviour"), and since the wifey grep is repo-wide those English words match
# *something* almost always, which inflates the ladder back to HIGH — the very
# failure this resolver exists to prevent.
_IDENTIFIER_RE = re.compile(r"([A-Za-z_]\w{2,})\s*(?=[=(,:)\[\]])")

# Tokens that carry no porting signal: language keywords, common builtins and
# typing names. Without this the identifier set fills with `str`/`None`/`return`
# and the confidence ladder is pinned at MEDIUM whatever the truth is.
_NOISE_TOKENS = frozenset(
    [
        "and",
        "any",
        "anyway",
        "are",
        "async",
        "await",
        "bool",
        "break",
        "callable",
        "class",
        "continue",
        "def",
        "del",
        "dict",
        "elif",
        "else",
        "except",
        "False",
        "finally",
        "float",
        "for",
        "from",
        "global",
        "has",
        "if",
        "import",
        "in",
        "int",
        "is",
        "lambda",
        "list",
        "len",
        "None",
        "nonlocal",
        "not",
        "or",
        "pass",
        "raise",
        "return",
        "self",
        "set",
        "str",
        "the",
        "True",
        "try",
        "tuple",
        "type",
        "while",
        "with",
        "yield",
        "Any",
        "Callable",
        "Dict",
        "Iterable",
        "List",
        "Optional",
        "Sequence",
        "Union",
        "cls",
        "args",
        "kwargs",
    ]
)


@dataclass(frozen=True)
class SymbolChanges:
    """What a diff did to top-level symbols — added vs merely modified.

    The distinction is the whole point. A NEW symbol's name is real evidence: if
    wifey has it, the port landed. A MODIFIED symbol's name is **no evidence at
    all** — a signature change re-emits the ``def`` line, so the name sits on
    both sides and a name-presence grep matches the *old* version.
    ``new_identifiers`` carries what the modification actually introduced.
    """

    added: list[str]
    modified: list[str]
    new_identifiers: list[str]


def _match_first(patterns: tuple[re.Pattern[str], ...], line: str) -> str | None:
    for pattern in patterns:
        m = pattern.match(line)
        if m:
            return m.group(1)
    return None


def extract_symbol_changes(diff_text: str) -> SymbolChanges:
    """Split a unified diff's top-level symbols into added vs modified.

    A symbol touched on BOTH sides (``-def foo`` and ``+def foo``) was modified,
    not added. Verified against parent #521, whose whole payload was two new
    kwargs on an existing ``route_target``: the old resolver read
    ``+def route_target(`` as an addition, grepped the bare name, found wifey's
    two-arg version and returned HIGH — a false ALREADY-APPLIED on a port that
    was in fact missing. The removed-side hunk was in the diff the whole time.
    """
    added_pats = (_ADDED_DEF_RE, _ADDED_CLASS_RE, _ADDED_CONST_RE)
    removed_pats = (_REMOVED_DEF_RE, _REMOVED_CLASS_RE, _REMOVED_CONST_RE)
    plus_syms: list[str] = []
    minus_syms: set[str] = set()
    plus_tokens: set[str] = set()
    seen_elsewhere: set[str] = set()

    in_hunk = False
    for line in diff_text.splitlines():
        # Only parse inside hunks. `git show` without `--format=` prepends the
        # commit message, whose prose lines carry no diff prefix and would
        # otherwise read as context — silently subtracting the very identifiers
        # the change introduced whenever the message describes them.
        if line.startswith("diff --git"):
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            sym = _match_first(added_pats, line)
            if sym and sym not in plus_syms:
                plus_syms.append(sym)
            if not line[1:].lstrip().startswith("#"):
                plus_tokens.update(_IDENTIFIER_RE.findall(line))
        elif line.startswith("-"):
            sym = _match_first(removed_pats, line)
            if sym:
                minus_syms.add(sym)
            seen_elsewhere.update(_IDENTIFIER_RE.findall(line))
        else:
            # Context line: unchanged code, so every token on it already existed.
            seen_elsewhere.update(_IDENTIFIER_RE.findall(line))

    added = [s for s in plus_syms if s not in minus_syms]
    modified = [s for s in plus_syms if s in minus_syms]
    # Identifiers the change INTRODUCED: on the added side, absent from both the
    # removed and the context lines, not a symbol name, and not pure noise.
    new_identifiers = sorted(
        t
        for t in plus_tokens - seen_elsewhere - set(plus_syms)
        if t not in _NOISE_TOKENS
    )
    return SymbolChanges(
        added=added, modified=modified, new_identifiers=new_identifiers
    )


def extract_added_symbols(diff_text: str) -> list[str]:
    """Pull **genuinely new** function / class / module-constant names from a diff.

    Modified symbols are deliberately excluded — see ``extract_symbol_changes``.
    """
    return extract_symbol_changes(diff_text).added


def _wifey_grep(symbol: str) -> bool:
    """True if ``symbol`` appears anywhere in the wifey working tree (via git grep)."""
    try:
        subprocess.run(
            ["git", "-C", str(WIFEY_REPO_PATH), "grep", "-q", "-w", symbol],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False  # exit 1 == no match; any other failure also treated as no match


def detect_already_applied(
    symbols: list[str],
    grep: Callable[[str], bool] = _wifey_grep,
) -> Confidence:
    """HIGH if all symbols found, LOW if none, MEDIUM if partial, UNKNOWN if no symbols."""
    if not symbols:
        return Confidence.UNKNOWN
    matched = 0
    for sym in symbols:
        try:
            if grep(sym):
                matched += 1
        except Exception:  # noqa: BLE001 — never let a grep failure crash the run
            continue
    if matched == 0:
        return Confidence.LOW
    if matched == len(symbols):
        return Confidence.HIGH
    return Confidence.MEDIUM


def resolve_confidence(
    changes: SymbolChanges,
    grep: Callable[[str], bool] = _wifey_grep,
) -> Confidence:
    """Decide WHICH symbols are admissible evidence, then run the ladder.

    A new symbol's name is evidence. A modified symbol's name is not, so the
    evidence becomes the identifiers the modification introduced. A PR that only
    modifies existing symbols and introduces no new identifier is **UNKNOWN** —
    this reports "cannot tell" rather than guessing, because guessing here is
    what produced the #521 miss.
    """
    evidence = list(changes.added)
    if changes.modified:
        evidence.extend(changes.new_identifiers)
    if not evidence:
        return Confidence.UNKNOWN
    return detect_already_applied(evidence, grep=grep)


# --------------------------------------------------------------------------- #
# Parent MEMORY excerpt
# --------------------------------------------------------------------------- #


def extract_memory_entry(pr_number: int, memory_text: str) -> str | None:
    """Return the bullet/paragraph in parent MEMORY.md that references ``#<pr_number>``.

    A bullet runs from a line starting with ``- `` (or ``* ``) until the next bullet
    or a blank line, so neighbouring entries don't bleed in.
    """
    needle = re.compile(rf"#\s*{pr_number}\b")
    lines = memory_text.splitlines()
    for i, line in enumerate(lines):
        if not needle.search(line):
            continue
        # Walk back to the bullet start.
        start = i
        while start > 0 and not re.match(r"^\s*[-*]\s", lines[start]):
            if lines[start].strip() == "":
                start += 1
                break
            start -= 1
        # Walk forward to the bullet end (next bullet or blank line).
        end = i + 1
        while (
            end < len(lines)
            and lines[end].strip() != ""
            and not re.match(r"^\s*[-*]\s", lines[end])
        ):
            end += 1
        block = "\n".join(lines[start:end]).strip()
        return block or None
    return None


# --------------------------------------------------------------------------- #
# Suggested approach
# --------------------------------------------------------------------------- #


def suggest_approach(
    bucket: Bucket, confidence: Confidence, wifey_paths: list[WifeyPath]
) -> str:
    """One of: verify-only / cherry-pick-with-edits / re-implement."""
    if confidence == Confidence.HIGH:
        return "verify-only"
    relevant = [wp for wp in wifey_paths if wp.kind not in ("removed", "skip")]
    if (
        bucket == Bucket.PORT
        and relevant
        and all(wp.kind in ("direct", "renamed") for wp in relevant)
    ):
        return "cherry-pick-with-edits"
    return "re-implement"


# --------------------------------------------------------------------------- #
# Report formatter
# --------------------------------------------------------------------------- #

PARENT_PR_URL = "https://github.com/s10023/buibui-moon-trader-bot/pull/{n}"


@dataclass
class PRReport:
    pr: PR
    bucket: Bucket
    confidence: Confidence
    wifey_paths: list[WifeyPath]
    memory_excerpt: str | None
    approach: str


def _section_of(r: PRReport) -> str:
    """Final report section: SKIP stays SKIP; HIGH-confidence non-skip -> ALREADY-APPLIED."""
    if r.bucket == Bucket.SKIP:
        return "SKIP"
    if r.confidence == Confidence.HIGH:
        return "ALREADY-APPLIED"
    return r.bucket.value


def _pr_label(pr: PR) -> str:
    return f"#{pr.number}" if pr.number is not None else "#none"


_CONVENTIONAL_RE = re.compile(r"^(\w+)(?:\(([^)]+)\))?:")


def derive_workstream(subject: str) -> str:
    """Cluster a PR subject into a workstream theme.

    Checks :data:`WORKSTREAM_RULES` in order (first match wins, case-insensitive);
    otherwise falls back to the conventional-commit ``type(scope)`` prefix, or
    ``"other"`` when the subject has no recognisable prefix.
    """
    for pattern, label in WORKSTREAM_RULES:
        if re.search(pattern, subject, re.IGNORECASE):
            return label
    m = _CONVENTIONAL_RE.match(subject)
    if m:
        ctype, scope = m.group(1), m.group(2)
        return f"{ctype}({scope})" if scope else ctype
    return "other"


def format_workstream_summary(reports: list[PRReport]) -> list[str]:
    """Markdown lines for the Workstreams table: theme → PRs → count.

    Groups by :func:`derive_workstream` of each PR title, preserving the order in
    which each theme first appears (dict insertion order).
    """
    groups: dict[str, list[PR]] = {}
    for r in reports:
        groups.setdefault(derive_workstream(r.pr.title), []).append(r.pr)
    lines = ["## Workstreams", "", "| Workstream | PRs | Count |", "|---|---|---|"]
    for theme, prs in groups.items():
        pr_list = " ".join(_pr_label(p) for p in prs)
        lines.append(f"| {theme} | {pr_list} | {len(prs)} |")
    lines.append("")
    return lines


def _path_line(wp: WifeyPath) -> str:
    if wp.kind == "removed" or wp.kind == "skip":
        return f"  - `{wp.parent_path}` → (removed in fork — SKIP)"
    if wp.kind == "unmapped":
        return f"  - `{wp.parent_path}` → (unmapped — investigate)"
    return f"  - `{wp.parent_path}` → wifey: `{wp.wifey_path}` ({wp.kind})"


def _detail_block(r: PRReport) -> str:
    lines = [
        f'## PR {_pr_label(r.pr)} — "{r.pr.title}"',
        "",
        f"- **Bucket**: {r.bucket.value}",
        f"- **Confidence already-applied**: {r.confidence.value}",
        "- **Files touched in parent**:",
    ]
    lines.extend(
        _path_line(wp) for wp in r.wifey_paths
    ) if r.wifey_paths else lines.append("  - (none)")
    lines.append(f"- **Suggested approach**: {r.approach}")
    if r.memory_excerpt:
        quoted = "\n".join(f"  > {ln}" for ln in r.memory_excerpt.splitlines())
        lines.append("- **Parent MEMORY excerpt**:")
        lines.append(quoted)
    else:
        lines.append("- **Parent MEMORY excerpt**: (none found)")
    if r.pr.number is not None:
        lines.append(f"- **Link**: {PARENT_PR_URL.format(n=r.pr.number)}")
    lines.append("")
    return "\n".join(lines)


#: Emitted into every report header. The skill body already warns that a bucket
#: label is not a ruling; the REPORT did not, and the report is the artifact read
#: days later, by a session that never opened the skill.
#:
#: ⚠ **Do NOT answer a wrong count by trying to fix the classifier.** It resolves
#: paths, and a path resolves identically whether the parent wrote the code or
#: adopted wifey's — so the ALREADY-APPLIED zero is not a tuning failure, it is
#: outside what a path test can observe. The deliverable is a reader who
#: distrusts the counts, not counts that deserve trust.
CLASSIFIER_CAVEAT = [
    "> ⚠ **These are the classifier's buckets, not rulings.** It resolves paths,",
    "> so it defaults to EVALUATE whenever one resolves, and it cannot see",
    "> **direction of travel**: where the PARENT adopted wifey's work the path",
    "> resolves either way, and the PR lands anywhere but ALREADY-APPLIED.",
    ">",
    "> Round 16 (2026-08-26) printed `0 SKIP / 3 PORT / 19 EVALUATE /",
    "> 0 ALREADY-APPLIED`. The rulings were **3 PORT / 4 EVALUATE / 10 NO PORT /",
    "> 4 ALREADY-APPLIED / 1 never** — and all four of that bucket the table",
    "> reported as EMPTY were the parent adopting wifey's work.",
    ">",
    "> **Check the wifey-side artifact's DATE before ruling.** The ruling buckets",
    "> are PORT · ALREADY-APPLIED · EVALUATE · NO PORT — not this table's four.",
    "",
]


def format_report(reports: list[PRReport], from_hash: str, to_hash: str) -> str:
    sections: dict[str, list[PRReport]] = {
        "SKIP": [],
        "PORT": [],
        "EVALUATE": [],
        "ALREADY-APPLIED": [],
    }
    for r in reports:
        sections[_section_of(r)].append(r)

    out: list[str] = [
        f"# Parent sync report — {date.today().isoformat()}",
        "",
        f"**Range**: {from_hash}..{to_hash}",
        f"**PRs found**: {len(reports)}",
        f"**Pointer-bump command**: `poetry run python tools/sync_parent.py --bump-to {to_hash}`",
        "",
        *CLASSIFIER_CAVEAT,
        "## Summary",
        "",
        "| Bucket | Count |",
        "|---|---|",
        f"| SKIP            | {len(sections['SKIP'])} |",
        f"| PORT            | {len(sections['PORT'])} |",
        f"| EVALUATE        | {len(sections['EVALUATE'])} |",
        f"| ALREADY-APPLIED | {len(sections['ALREADY-APPLIED'])} |",
        "",
    ]

    # Workstream clustering (multi-PR campaigns) above the four buckets.
    out += format_workstream_summary(reports)

    # Compact tables for SKIP + ALREADY-APPLIED.
    out += ["## SKIP", "", "| PR | Title | Reason |", "|---|---|---|"]
    for r in sections["SKIP"]:
        out.append(
            f"| {_pr_label(r.pr)} | {r.pr.title} | all touched paths removed in fork |"
        )
    out.append("")

    out += ["## PORT", ""]
    out += [_detail_block(r) for r in sections["PORT"]] or ["(none)", ""]

    out += ["## EVALUATE", ""]
    out += [_detail_block(r) for r in sections["EVALUATE"]] or ["(none)", ""]

    out += [
        "## ALREADY-APPLIED",
        "",
        "| PR | Title | Confidence | Verify against |",
        "|---|---|---|---|",
    ]
    for r in sections["ALREADY-APPLIED"]:
        targets = ", ".join(
            wp.wifey_path or "?" for wp in r.wifey_paths if wp.wifey_path
        )
        out.append(
            f"| {_pr_label(r.pr)} | {r.pr.title} | {r.confidence.value} | {targets} |"
        )
    out.append("")

    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Orchestration + CLI
# --------------------------------------------------------------------------- #


def _parent_head_hash() -> str:
    return _git(PARENT_REPO_PATH, "rev-parse", "--short", "origin/main").strip()


def _hash_exists_in_parent(commit_hash: str) -> bool:
    try:
        _git(PARENT_REPO_PATH, "cat-file", "-e", f"{commit_hash}^{{commit}}")
        return True
    except subprocess.CalledProcessError:
        return False


def _read_parent_memory() -> str:
    return PARENT_MEMORY_PATH.read_text() if PARENT_MEMORY_PATH.exists() else ""


def _commit_diff(sha: str) -> str:
    try:
        return _git(PARENT_REPO_PATH, "show", "--format=", sha)
    except subprocess.CalledProcessError:
        return ""


def _report_path() -> Path:
    return REPORT_DIR / f"parent-sync-{date.today().isoformat()}.md"


def run_pipeline(prs: list[PR], memory_text: str) -> list[PRReport]:
    """Run the per-PR enrichment pipeline. Pure given mocked helpers."""
    reports: list[PRReport] = []
    for pr in prs:
        wifey_paths = translate_paths(pr.files)
        bucket = classify_pr(pr, wifey_paths)
        excerpt = extract_memory_entry(pr.number, memory_text) if pr.number else None
        added: list[str] = []
        modified: list[str] = []
        new_identifiers: list[str] = []
        for commit in pr.commits:
            ch = extract_symbol_changes(_commit_diff(commit.sha))
            added.extend(s for s in ch.added if s not in added)
            modified.extend(s for s in ch.modified if s not in modified)
            new_identifiers.extend(
                s for s in ch.new_identifiers if s not in new_identifiers
            )
        # A symbol added by one commit and modified by a later one in the same PR
        # is still an addition from the fork's point of view.
        modified = [s for s in modified if s not in added]
        confidence = resolve_confidence(
            SymbolChanges(
                added=added, modified=modified, new_identifiers=new_identifiers
            )
        )
        approach = suggest_approach(bucket, confidence, wifey_paths)
        reports.append(PRReport(pr, bucket, confidence, wifey_paths, excerpt, approach))
    return reports


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--from", dest="from_hash", default=None, help="override start hash")
    p.add_argument("--full", action="store_true", help="scan fork commit -> HEAD")
    p.add_argument(
        "--bump-to",
        dest="bump_to",
        default=None,
        help="update state pointer only, no scan",
    )
    p.add_argument(
        "--no-fetch", dest="no_fetch", action="store_true", help="use local refs only"
    )
    return p


def _fail(message: str) -> int:
    print(f"ERROR: {message}")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not PARENT_REPO_PATH.exists():
        return _fail(
            f"Parent repo not found at {PARENT_REPO_PATH}. Clone it or update PARENT_REPO_PATH."
        )
    # Deliberately NOT a checked-out-branch guard. Every parent read here is
    # ref-based against origin/main (cat-file / fetch / log / show / rev-parse) and
    # nothing touches the parent working tree, so which branch happens to be checked
    # out cannot change a scan's result. Guarding on it blocked a scan outright on
    # 2026-06-17 (parent parked on feat/xsmom-sleeve) and again on 2026-08-11 — pure
    # friction, and a blocked scan is how a triage backlog grows.
    if not _hash_exists_in_parent("origin/main"):
        return _fail(
            f"No readable origin/main in {PARENT_REPO_PATH}. Fetch it first: "
            f"git -C {PARENT_REPO_PATH} fetch origin main"
        )

    if args.bump_to:
        if not _hash_exists_in_parent(args.bump_to):
            return _fail("Pointer-bump target not in parent history; refusing to bump.")
        write_sync_state(args.bump_to, note="manual bump")
        print(f"Sync pointer bumped to {args.bump_to}.")
        return 0

    if args.full:
        from_hash = FORK_COMMIT
    elif args.from_hash:
        from_hash = args.from_hash
    else:
        try:
            from_hash = load_sync_state()
        except SyncStateError as exc:
            return _fail(
                f"Sync state file malformed: {exc}. Inspect or delete to re-bootstrap."
            )

    if from_hash == FORK_COMMIT and not STATE_FILE_PATH.exists():
        print("BOOTSTRAP: scanning full fork → HEAD")

    try:
        commits = fetch_parent_commits(from_hash, no_fetch=args.no_fetch)
    except subprocess.CalledProcessError:
        return _fail(
            f"Could not read parent history from {from_hash}. "
            "Pass --from <known-hash> or re-run with --no-fetch."
        )

    to_hash = _parent_head_hash()
    if not commits:
        print(f"No new parent commits since {from_hash}. Nothing to review.")
        return 0

    prs = group_into_prs(commits)
    reports = run_pipeline(prs, _read_parent_memory())
    report = format_report(reports, from_hash, to_hash)

    path = _report_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report)
    except OSError as exc:
        return _fail(f"Cannot write report to {path}: {exc}")

    print(f"Wrote {len(reports)} PR(s) to {path}")
    print(
        f"When done reviewing: poetry run python tools/sync_parent.py --bump-to {to_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
