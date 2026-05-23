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
from dataclasses import dataclass
from datetime import date
from enum import Enum
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


class Bucket(str, Enum):
    SKIP = "SKIP"
    PORT = "PORT"
    EVALUATE = "EVALUATE"


class Confidence(str, Enum):
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
