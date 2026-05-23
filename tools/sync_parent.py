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
