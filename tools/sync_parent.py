"""Detect-and-recommend pipeline for porting parent-repo PRs into the wifey fork.

Read-only on both repos except the wifey memory state file (written only on
``--bump-to``). Enumerates parent PRs merged since the last sync point, classifies
each (SKIP / PORT / EVALUATE, with an ALREADY-APPLIED routing overlay), and writes a
context-rich report to ``/tmp/parent-sync-<date>.md``.

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

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

PARENT_REPO_PATH = Path("/home/kng/repo/buibui-moon-trader-bot")
WIFEY_REPO_PATH = Path(__file__).resolve().parent.parent
FORK_COMMIT = "635ed5a"

WIFEY_MEMORY_DIR = (
    Path.home()
    / ".claude-personal"
    / "projects"
    / "-home-kng-repo-buibui-wifey-wall-street-bot"
    / "memory"
)
PARENT_MEMORY_PATH = (
    Path.home()
    / ".claude-personal"
    / "projects"
    / "-home-kng-repo-buibui-moon-trader-bot"
    / "memory"
    / "MEMORY.md"
)
STATE_FILE_PATH = WIFEY_MEMORY_DIR / "project_parent_sync_state.md"

# Parent path -> wifey path. None means "removed in fork (SKIP)".
# Surviving same-name files are intentionally absent: translate_paths() resolves
# them to a "direct" map via a wifey working-tree existence check.
PARENT_TO_WIFEY_PATHS: dict[str, str | None] = {
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

REPORT_DIR = Path("/tmp")

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


def write_sync_state(new_hash: str, state_path: Path = STATE_FILE_PATH, note: str = "") -> None:
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
                PR(number=None, title=commit.subject, commits=[commit], files=list(commit.files))
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

_EVALUATE_PATH_RE = re.compile(r"(^config/.*\.toml$|strategy_params|analytics/strategies/)")


def _is_evaluate_path(wp: WifeyPath) -> bool:
    """Cohort-sensitive (config / sweep / new-strategy) or unmapped -> needs judgment."""
    if wp.kind == "unmapped":
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


def extract_added_symbols(diff_text: str) -> list[str]:
    """Pull added function / class / module-constant names from a unified diff."""
    symbols: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or not line.startswith("+"):
            continue
        for pattern in (_ADDED_DEF_RE, _ADDED_CLASS_RE, _ADDED_CONST_RE):
            m = pattern.match(line)
            if m and m.group(1) not in symbols:
                symbols.append(m.group(1))
    return symbols


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
        while end < len(lines) and lines[end].strip() != "" and not re.match(r"^\s*[-*]\s", lines[end]):
            end += 1
        block = "\n".join(lines[start:end]).strip()
        return block or None
    return None


# --------------------------------------------------------------------------- #
# Suggested approach
# --------------------------------------------------------------------------- #


def suggest_approach(bucket: Bucket, confidence: Confidence, wifey_paths: list[WifeyPath]) -> str:
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


def _path_line(wp: WifeyPath) -> str:
    if wp.kind == "removed" or wp.kind == "skip":
        return f"  - `{wp.parent_path}` → (removed in fork — SKIP)"
    if wp.kind == "unmapped":
        return f"  - `{wp.parent_path}` → (unmapped — investigate)"
    return f"  - `{wp.parent_path}` → wifey: `{wp.wifey_path}` ({wp.kind})"


def _detail_block(r: PRReport) -> str:
    lines = [
        f"## PR {_pr_label(r.pr)} — \"{r.pr.title}\"",
        "",
        f"- **Bucket**: {r.bucket.value}",
        f"- **Confidence already-applied**: {r.confidence.value}",
        "- **Files touched in parent**:",
    ]
    lines.extend(_path_line(wp) for wp in r.wifey_paths) if r.wifey_paths else lines.append("  - (none)")
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


def format_report(reports: list[PRReport], from_hash: str, to_hash: str) -> str:
    sections: dict[str, list[PRReport]] = {"SKIP": [], "PORT": [], "EVALUATE": [], "ALREADY-APPLIED": []}
    for r in reports:
        sections[_section_of(r)].append(r)

    out: list[str] = [
        f"# Parent sync report — {date.today().isoformat()}",
        "",
        f"**Range**: {from_hash}..{to_hash}",
        f"**PRs found**: {len(reports)}",
        f"**Pointer-bump command**: `poetry run python tools/sync_parent.py --bump-to {to_hash}`",
        "",
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

    # Compact tables for SKIP + ALREADY-APPLIED.
    out += ["## SKIP", "", "| PR | Title | Reason |", "|---|---|---|"]
    for r in sections["SKIP"]:
        out.append(f"| {_pr_label(r.pr)} | {r.pr.title} | all touched paths removed in fork |")
    out.append("")

    out += ["## PORT", ""]
    out += [_detail_block(r) for r in sections["PORT"]] or ["(none)", ""]

    out += ["## EVALUATE", ""]
    out += [_detail_block(r) for r in sections["EVALUATE"]] or ["(none)", ""]

    out += ["## ALREADY-APPLIED", "", "| PR | Title | Confidence | Verify against |", "|---|---|---|---|"]
    for r in sections["ALREADY-APPLIED"]:
        targets = ", ".join(wp.wifey_path or "?" for wp in r.wifey_paths if wp.wifey_path)
        out.append(f"| {_pr_label(r.pr)} | {r.pr.title} | {r.confidence.value} | {targets} |")
    out.append("")

    return "\n".join(out)
