# /sync-parent Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only `/sync-parent` skill that enumerates parent-repo (`buibui-moon-trader-bot`) PRs merged since the last sync point, classifies each into SKIP / PORT / EVALUATE / ALREADY-APPLIED, and emits a context-rich markdown report so a human can decide which upstream changes to port into the wifey fork.

**Architecture:** Two artefacts. `tools/sync_parent.py` is a single-file, pure-stdlib + `subprocess` helper, read-only on both repos except for the memory state file on `--bump-to`. `.claude/skills/sync-parent/SKILL.md` is the Claude-facing workflow wrapper invoked as `/sync-parent`, wired through a `make wifey-sync-parent` target. A pure functional core (parsing, classification, formatting) is testable with all git calls mocked; `main()` is the only side-effecting orchestrator.

**Tech Stack:** Python 3.11+ (stdlib only — `argparse`, `subprocess`, `dataclasses`, `enum`, `pathlib`, `re`, `datetime`, `os`, `fnmatch`), pytest + pytest-mock, ruff, mypy strict.

---

## Spec deviations (resolved against real parent data — read before starting)

Two spec assumptions break against the live parent repo. Both resolutions are baked into the tasks below; this section explains *why* so an engineer reading a single task out of order isn't surprised.

1. **Parent is squash-merge only.** `git -C <parent> log --merges 635ed5a..origin/main` returns **zero** commits — the parent never creates merge commits. Every PR is a single squash commit whose subject ends in `(#N)`, e.g. `feat(backtest): Bucket C ... (#403)`. The spec's `fetch_parent_commits` uses `git log --merges`, which would find nothing. **Resolution:** `fetch_parent_commits` runs `git log <range> --reverse` with **no `--merges` filter**, and `group_into_prs` extracts the PR number from the `(#N)` suffix. The legacy `Merge pull request #N` format is still parsed (defensive), and commits with no `#N` become a synthetic `#none` direct-commit entry.

2. **The path map covers only renamed/removed files (~15 entries).** Surviving same-name files (e.g. `analytics/regime.py`) are *not* in `PARENT_TO_WIFEY_PATHS`, yet the spec's PORT example treats `analytics/regime.py → analytics/regime.py (direct)` as a portable change. Reading the spec's "path not in map → EVALUATE fallback" literally would route almost every PR to EVALUATE. **Resolution:** `translate_paths` checks the wifey working tree: a path absent from the map but **present in wifey** is a `direct` map (PORT-eligible); a path absent from both the map and the wifey tree is `unmapped` → EVALUATE. Existence is checked through a module-level `_wifey_path_exists` indirection so unit tests can patch it.

3. **ALREADY-APPLIED is a routing decision, not a `classify_pr` return value.** `classify_pr` stays pure and returns only `SKIP` / `PORT` / `EVALUATE`. A PR is *routed* to the ALREADY-APPLIED report section when its `detect_already_applied` confidence is `HIGH` and its bucket is not `SKIP`. This keeps classification independent of the (slower, working-tree-dependent) content grep.

---

## File structure

| File | Responsibility | Create / Modify |
| --- | --- | --- |
| `tools/sync_parent.py` | Entire helper: constants, data model, git helpers, classification, enrichment, formatter, CLI `main()` | Create |
| `tests/test_sync_parent.py` | 8 test classes covering every pure function + a mocked smoke run | Create |
| `tests/fixtures/sync_parent/sample_git_log.txt` | Captured `git log` output: mixed squash + (synthetic) merge + direct-to-main | Create |
| `tests/fixtures/sync_parent/sample_memory.md` | Minimal parent MEMORY.md with 2–3 PR-referenced entries | Create |
| `tests/fixtures/sync_parent/sample_diff.txt` | Captured unified diff for one PR (for symbol extraction) | Create |
| `Makefile` | Add `wifey-sync-parent` target + `.PHONY` entry | Modify |
| `.claude/skills/sync-parent/SKILL.md` | Claude-facing workflow wrapper | Create |
| `CLAUDE.md` | Add `/sync-parent` row to the Agent Skills table | Modify |

`tools/sync_parent.py` is a single file by spec mandate. It is internally decomposed into clearly-sectioned function groups (constants → data model → git helpers → classification → enrichment → formatter → CLI) so each is independently testable.

---

## Data model (defined once, referenced by every task)

These types live at the top of `tools/sync_parent.py` and are the contract every later task depends on. Signatures here are authoritative — if a later task's code disagrees, the later task is wrong.

```python
# NOTE: use `StrEnum` (from enum import StrEnum), not `(str, Enum)` — repo ruff
# config has UP enabled and flags UP042. Members stay str instances, so
# `Bucket.SKIP == "SKIP"` and `.value` still work.
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
    number: int | None          # None == direct-to-main (rendered as "#none")
    title: str
    commits: list[Commit]
    files: list[str]            # de-duplicated union of every commit's files

@dataclass
class WifeyPath:
    parent_path: str
    wifey_path: str | None      # None when the parent path has no wifey target
    kind: PathKind              # see Literal below

PathKind = Literal["direct", "renamed", "removed", "skip", "unmapped"]
```

`Bucket` and `Confidence` subclass `str` so report formatting can use `.value` directly and `==` comparisons read naturally.

---

## Task 1: Module scaffolding — constants, data model, custom exception

**Files:**

- Create: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`

- [ ] **Step 1: Create the branch**

```bash
cd /home/kng/repo/buibui-wifey-wall-street-bot
git fetch origin main
git switch -c feat/sync-parent-skill origin/main
git config --local user.email   # must print ngkhaijian@gmail.com before any commit
```

Expected: `ngkhaijian@gmail.com`. If not, run `git config --local user.email ngkhaijian@gmail.com`.

- [ ] **Step 2: Write the failing test for constants + data model**

Create `tests/test_sync_parent.py`:

```python
from __future__ import annotations

from pathlib import Path

import tools.sync_parent as sp


class TestModuleContract:
    """The constants + data model other tasks depend on exist and are well-formed."""

    def test_fork_commit_constant(self) -> None:
        assert sp.FORK_COMMIT == "635ed5a"

    def test_parent_repo_path_is_path(self) -> None:
        assert isinstance(sp.PARENT_REPO_PATH, Path)
        assert sp.PARENT_REPO_PATH.name == "buibui-moon-trader-bot"

    def test_path_map_marks_removed_modules_as_none(self) -> None:
        assert sp.PARENT_TO_WIFEY_PATHS["utils/binance_client.py"] is None
        assert sp.PARENT_TO_WIFEY_PATHS["analytics/cme_gap_lib.py"] is None

    def test_path_map_renames_indicators_lib(self) -> None:
        assert (
            sp.PARENT_TO_WIFEY_PATHS["analytics/indicators_lib.py"]
            == "analytics/strategies/_registry.py"
        )

    def test_buckets_and_confidence_are_str_enums(self) -> None:
        assert sp.Bucket.SKIP.value == "SKIP"
        assert sp.Confidence.HIGH.value == "HIGH"
        assert sp.Bucket.SKIP == "SKIP"  # str subclass

    def test_pr_dataclass_fields(self) -> None:
        pr = sp.PR(number=12, title="t", commits=[], files=["a.py"])
        assert pr.number == 12
        assert pr.files == ["a.py"]

    def test_sync_state_error_is_exception(self) -> None:
        assert issubclass(sp.SyncStateError, Exception)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/test_sync_parent.py::TestModuleContract -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.sync_parent'` (or attribute errors).

- [ ] **Step 4: Write the module scaffolding**

Create `tools/sync_parent.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/test_sync_parent.py::TestModuleContract -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py
git commit -m "feat(tools): sync_parent scaffolding — constants, data model, errors"
```

---

## Task 2: Sync-state file — load + atomic write

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`

The state file is a memory topic file with minimal frontmatter. To stay pure-stdlib (no PyYAML), the frontmatter is a hand-parsed `key: value` block between `---` fences. Only `last_synced_hash` is load-bearing.

State file format:

```markdown
---
last_synced_hash: 635ed5a
updated: 2026-05-23
---

# Parent sync state

Tracks the last parent commit reviewed by `tools/sync_parent.py`.

## Run history

- 2026-05-23: bumped to 635ed5a (bootstrap)
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
class TestStateFile:
    """load_sync_state + write_sync_state."""

    def test_bootstrap_when_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.md"
        assert sp.load_sync_state(missing) == sp.FORK_COMMIT

    def test_reads_frontmatter_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        f.write_text("---\nlast_synced_hash: abc1234\nupdated: 2026-05-23\n---\nbody\n")
        assert sp.load_sync_state(f) == "abc1234"

    def test_malformed_frontmatter_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        f.write_text("no frontmatter here\n")
        with pytest.raises(sp.SyncStateError, match="malformed"):
            sp.load_sync_state(f)

    def test_missing_hash_key_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        f.write_text("---\nupdated: 2026-05-23\n---\nbody\n")
        with pytest.raises(sp.SyncStateError, match="last_synced_hash"):
            sp.load_sync_state(f)

    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        sp.write_sync_state("deadbee", f, "smoke note")
        assert sp.load_sync_state(f) == "deadbee"
        assert "smoke note" in f.read_text()

    def test_write_is_atomic_no_temp_left(self, tmp_path: Path) -> None:
        f = tmp_path / "state.md"
        sp.write_sync_state("deadbee", f, "note")
        assert list(tmp_path.glob("*.tmp")) == []
```

Add `import pytest` to the top of the test file if not already present (it is added in Task 1's edits — verify and add once).

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestStateFile -v`
Expected: FAIL — `AttributeError: module 'tools.sync_parent' has no attribute 'load_sync_state'`.

- [ ] **Step 3: Implement load + write**

Append to `tools/sync_parent.py` (after the data model):

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestStateFile -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py
git commit -m "feat(tools): sync_parent state file load + atomic write"
```

---

## Task 3: Git helpers, commit fetch, and PR grouping

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`
- Create: `tests/fixtures/sync_parent/sample_git_log.txt`

`fetch_parent_commits` shells out (mocked in tests). `group_into_prs` is pure and is where the squash-merge resolution lives. The git-log format uses ASCII unit separators so subjects/bodies with newlines parse unambiguously: `%H\x1f%s\x1f%b\x1e`.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/sync_parent/sample_git_log.txt` — three records separated by `\x1e` (record sep) with `\x1f` (field sep) inside. Because raw control bytes are awkward in a checked-in file, the fixture stores a *human-readable* form and the test reconstructs the separators. Write this exact content:

```text
abc1234|feat(backtest): tighten regime gate thresholds (#403)|Validated on 4-symbol cohort, +0.12R uplift.
def5678|fix(signals): correct cooldown watermark off-by-one (#402)|
0011223|chore: bump ruff to 0.6.0|direct-to-main maintenance, no PR
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
def _load_commits_from_fixture() -> list[sp.Commit]:
    """Reconstruct Commit objects from the pipe-delimited fixture."""
    text = (
        Path(__file__).parent / "fixtures" / "sync_parent" / "sample_git_log.txt"
    ).read_text()
    commits: list[sp.Commit] = []
    for line in text.strip().splitlines():
        sha, subject, body = line.split("|", 2)
        commits.append(sp.Commit(sha=sha, subject=subject, body=body, files=[]))
    return commits


class TestPRGrouping:
    """group_into_prs handles squash, legacy merge, and direct-to-main shapes."""

    def test_squash_suffix_extracts_pr_number(self) -> None:
        commits = _load_commits_from_fixture()
        prs = sp.group_into_prs(commits)
        numbers = {pr.number for pr in prs}
        assert 403 in numbers
        assert 402 in numbers

    def test_direct_to_main_becomes_none(self) -> None:
        commits = _load_commits_from_fixture()
        prs = sp.group_into_prs(commits)
        direct = [pr for pr in prs if pr.number is None]
        assert len(direct) == 1
        assert "ruff" in direct[0].title

    def test_legacy_merge_format_parsed(self) -> None:
        commits = [
            sp.Commit("aaa", "Merge pull request #99 from foo/bar", "body", []),
        ]
        prs = sp.group_into_prs(commits)
        assert prs[0].number == 99

    def test_files_union_deduplicated(self) -> None:
        commits = [
            sp.Commit("a", "feat: x (#5)", "", ["a.py", "b.py"]),
            sp.Commit("b", "feat: y (#5)", "", ["b.py", "c.py"]),
        ]
        prs = sp.group_into_prs(commits)
        pr5 = next(pr for pr in prs if pr.number == 5)
        assert sorted(pr5.files) == ["a.py", "b.py", "c.py"]

    def test_fetch_validates_hash_then_logs(self, mocker: Any) -> None:
        run = mocker.patch("tools.sync_parent._git")
        # cat-file -e (validation) -> ""; fetch -> ""; log -> one record; show -> file
        run.side_effect = [
            "",  # cat-file -e <from>
            "",  # fetch
            "abc1234\x1ffeat: x (#5)\x1fbody\x1e",  # log
            "analytics/regime.py\n",  # show --name-only
        ]
        commits = sp.fetch_parent_commits("635ed5a", no_fetch=False)
        assert commits[0].sha == "abc1234"
        assert commits[0].files == ["analytics/regime.py"]

    def test_fetch_skips_fetch_when_no_fetch(self, mocker: Any) -> None:
        run = mocker.patch("tools.sync_parent._git")
        run.side_effect = [
            "",  # cat-file -e
            "abc1234\x1ffeat: x (#5)\x1fbody\x1e",  # log (no fetch call)
            "analytics/regime.py\n",  # show
        ]
        sp.fetch_parent_commits("635ed5a", no_fetch=True)
        # 3 calls, none of them a fetch
        assert all("fetch" not in call.args for call in run.call_args_list)
```

Add `from typing import Any` to the test imports.

- [ ] **Step 3: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestPRGrouping -v`
Expected: FAIL — `AttributeError: ... has no attribute 'group_into_prs'`.

- [ ] **Step 4: Implement git helpers + grouping**

Append to `tools/sync_parent.py`:

```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestPRGrouping -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py tests/fixtures/sync_parent/sample_git_log.txt
git commit -m "feat(tools): sync_parent commit fetch + squash-aware PR grouping"
```

---

## Task 4: Path translation

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`

`translate_paths` is the divergence resolver (deviation #2). Existence against the wifey tree is checked through `_wifey_path_exists` so tests can patch it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
class TestPathTranslation:
    """translate_paths maps removed / renamed / direct / unmapped correctly."""

    def test_removed_module_maps_to_none_skip(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["utils/binance_client.py"])
        assert out[0].wifey_path is None
        assert out[0].kind == "removed"

    def test_renamed_module(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["analytics/indicators_lib.py"])
        assert out[0].wifey_path == "analytics/strategies/_registry.py"
        assert out[0].kind == "renamed"

    def test_skip_glob_match(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["analytics/strategies/cvd_divergence.py"])
        assert out[0].kind == "skip"
        assert out[0].wifey_path is None

    def test_surviving_same_name_is_direct(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=True)
        out = sp.translate_paths(["analytics/regime.py"])
        assert out[0].wifey_path == "analytics/regime.py"
        assert out[0].kind == "direct"

    def test_absent_everywhere_is_unmapped(self, mocker: Any) -> None:
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=False)
        out = sp.translate_paths(["analytics/some_new_parent_module.py"])
        assert out[0].kind == "unmapped"
        assert out[0].wifey_path is None
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestPathTranslation -v`
Expected: FAIL — no attribute `translate_paths`.

- [ ] **Step 3: Implement**

Append to `tools/sync_parent.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestPathTranslation -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py
git commit -m "feat(tools): sync_parent path translation with wifey-tree existence check"
```

---

## Task 5: Bucket classifier

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`

`classify_pr` consumes the translated paths (already computed in the pipeline) to avoid re-walking. It returns only SKIP / PORT / EVALUATE (deviation #3).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
class TestBucketClassifier:
    """classify_pr returns SKIP / PORT / EVALUATE per the rules."""

    def _wp(self, path: str, kind: str, target: str | None) -> Any:
        return sp.WifeyPath(path, target, kind)  # type: ignore[arg-type]

    def test_all_removed_is_skip(self) -> None:
        pr = sp.PR(number=1, title="fix: binance (#1)", commits=[], files=["utils/binance_client.py"])
        wps = [self._wp("utils/binance_client.py", "removed", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.SKIP

    def test_all_skip_glob_is_skip(self) -> None:
        pr = sp.PR(number=2, title="fix: cvd (#2)", commits=[], files=["analytics/strategies/cvd_divergence.py"])
        wps = [self._wp("analytics/strategies/cvd_divergence.py", "skip", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.SKIP

    def test_surviving_module_is_port(self) -> None:
        pr = sp.PR(number=3, title="fix: regime (#3)", commits=[], files=["analytics/regime.py"])
        wps = [self._wp("analytics/regime.py", "direct", "analytics/regime.py")]
        assert sp.classify_pr(pr, wps) == sp.Bucket.PORT

    def test_unmapped_path_is_evaluate(self) -> None:
        pr = sp.PR(number=4, title="feat: new (#4)", commits=[], files=["analytics/new_mod.py"])
        wps = [self._wp("analytics/new_mod.py", "unmapped", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_toml_config_is_evaluate(self) -> None:
        pr = sp.PR(number=5, title="feat: tp_r (#5)", commits=[], files=["config/signal_watch.toml"])
        wps = [self._wp("config/signal_watch.toml", "direct", "config/signal_watch.toml")]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_new_strategy_file_is_evaluate(self) -> None:
        pr = sp.PR(number=6, title="feat: strat (#6)", commits=[], files=["analytics/strategies/new_thing.py"])
        wps = [self._wp("analytics/strategies/new_thing.py", "unmapped", None)]
        assert sp.classify_pr(pr, wps) == sp.Bucket.EVALUATE

    def test_mixed_survivors_and_skip_is_port(self) -> None:
        """A bugfix touching a survivor + an incidental removed test stays PORT."""
        pr = sp.PR(number=7, title="fix (#7)", commits=[], files=["analytics/regime.py", "utils/binance_client.py"])
        wps = [
            self._wp("analytics/regime.py", "direct", "analytics/regime.py"),
            self._wp("utils/binance_client.py", "removed", None),
        ]
        assert sp.classify_pr(pr, wps) == sp.Bucket.PORT

    def test_no_files_is_evaluate(self) -> None:
        pr = sp.PR(number=8, title="empty (#8)", commits=[], files=[])
        assert sp.classify_pr(pr, []) == sp.Bucket.EVALUATE
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestBucketClassifier -v`
Expected: FAIL — no attribute `classify_pr`.

- [ ] **Step 3: Implement**

Append to `tools/sync_parent.py`:

```python
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
```

Note: `analytics/strategies/_registry.py` is a renamed survivor (kind `renamed`) and its parent path `analytics/indicators_lib.py` does **not** match `analytics/strategies/`, so registry edits classify PORT, not EVALUATE — only genuinely new files under `analytics/strategies/` (kind `unmapped`) hit EVALUATE.

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestBucketClassifier -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py
git commit -m "feat(tools): sync_parent bucket classifier (SKIP/PORT/EVALUATE)"
```

---

## Task 6: Already-applied detection — symbol extraction + grep confidence

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`
- Create: `tests/fixtures/sync_parent/sample_diff.txt`

Split into a pure `extract_added_symbols(diff_text)` and a `detect_already_applied(symbols, grep)` taking an injectable grep predicate so neither needs real git.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/sync_parent/sample_diff.txt`:

```text
diff --git a/analytics/regime.py b/analytics/regime.py
index 1111111..2222222 100644
--- a/analytics/regime.py
+++ b/analytics/regime.py
@@ -10,6 +10,11 @@ import pandas as pd
+ADX_TREND_THRESHOLD = 22
+
+def classify_regime_v2(df: pd.DataFrame) -> str:
+    """New regime classifier."""
+    return "trend"
-old_line_removed = True
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
class TestAlreadyApplied:
    """extract_added_symbols + detect_already_applied."""

    def _diff(self) -> str:
        return (
            Path(__file__).parent / "fixtures" / "sync_parent" / "sample_diff.txt"
        ).read_text()

    def test_extract_function_and_constant(self) -> None:
        syms = sp.extract_added_symbols(self._diff())
        assert "classify_regime_v2" in syms
        assert "ADX_TREND_THRESHOLD" in syms

    def test_extract_ignores_removed_and_context(self) -> None:
        syms = sp.extract_added_symbols(self._diff())
        assert "old_line_removed" not in syms  # removed line, not added
        assert "pd" not in syms                 # context import, not added def/const

    def test_confidence_high_all_match(self) -> None:
        c = sp.detect_already_applied(["foo", "bar"], grep=lambda s: True)
        assert c == sp.Confidence.HIGH

    def test_confidence_low_none_match(self) -> None:
        c = sp.detect_already_applied(["foo", "bar"], grep=lambda s: False)
        assert c == sp.Confidence.LOW

    def test_confidence_medium_partial(self) -> None:
        c = sp.detect_already_applied(["foo", "bar"], grep=lambda s: s == "foo")
        assert c == sp.Confidence.MEDIUM

    def test_confidence_unknown_no_symbols(self) -> None:
        c = sp.detect_already_applied([], grep=lambda s: True)
        assert c == sp.Confidence.UNKNOWN

    def test_grep_exception_treated_as_no_match(self) -> None:
        def boom(_s: str) -> bool:
            raise RuntimeError("git grep blew up")

        c = sp.detect_already_applied(["foo"], grep=boom)
        assert c == sp.Confidence.LOW
```

- [ ] **Step 3: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestAlreadyApplied -v`
Expected: FAIL — no attribute `extract_added_symbols`.

- [ ] **Step 4: Implement**

Append to `tools/sync_parent.py`:

```python
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
```

Add `from collections.abc import Callable` to the imports at the top of the module (the repo standardizes on `collections.abc`, not `typing`, for `Callable` — ruff UP035).

- [ ] **Step 5: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestAlreadyApplied -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py tests/fixtures/sync_parent/sample_diff.txt
git commit -m "feat(tools): sync_parent already-applied symbol extraction + grep confidence"
```

---

## Task 7: Parent MEMORY.md excerpt extraction

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`
- Create: `tests/fixtures/sync_parent/sample_memory.md`

`extract_memory_entry` takes the MEMORY text as a parameter (read from `PARENT_MEMORY_PATH` in `main`) so it's testable without the real file.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/sync_parent/sample_memory.md`:

```markdown
# Memory — Parent

## Current State

- **Session (PR #403)**: Regime gate thresholds were too tight on 1h — false-positive
  trend classification killed mean-reversion pairs. New thresholds ADX 25 to 22.
  Validated on 4-symbol cohort, +0.12R uplift.
- **Session (PR #402)**: Cooldown watermark off-by-one fixed.

## Older

- Some entry with no PR reference at all.
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
class TestMemoryExtract:
    """extract_memory_entry finds the paragraph referencing a PR number."""

    def _memory(self) -> str:
        return (
            Path(__file__).parent / "fixtures" / "sync_parent" / "sample_memory.md"
        ).read_text()

    def test_finds_referenced_pr(self) -> None:
        excerpt = sp.extract_memory_entry(403, self._memory())
        assert excerpt is not None
        assert "Regime gate thresholds" in excerpt
        assert "+0.12R uplift" in excerpt

    def test_returns_none_when_absent(self) -> None:
        assert sp.extract_memory_entry(999, self._memory()) is None

    def test_does_not_bleed_into_neighbour(self) -> None:
        excerpt = sp.extract_memory_entry(402, self._memory())
        assert excerpt is not None
        assert "Cooldown watermark" in excerpt
        assert "Regime gate" not in excerpt
```

- [ ] **Step 3: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestMemoryExtract -v`
Expected: FAIL — no attribute `extract_memory_entry`.

- [ ] **Step 4: Implement**

Append to `tools/sync_parent.py`:

```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestMemoryExtract -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py tests/fixtures/sync_parent/sample_memory.md
git commit -m "feat(tools): sync_parent parent MEMORY excerpt extraction"
```

---

## Task 8: Suggested-approach heuristic

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
class TestSuggestApproach:
    """suggest_approach returns one of the three labels."""

    def _wp(self, kind: str) -> Any:
        return sp.WifeyPath("p", "p", kind)  # type: ignore[arg-type]

    def test_high_confidence_is_verify_only(self) -> None:
        out = sp.suggest_approach(sp.Bucket.PORT, sp.Confidence.HIGH, [self._wp("direct")])
        assert out == "verify-only"

    def test_all_surviving_paths_is_cherry_pick(self) -> None:
        out = sp.suggest_approach(
            sp.Bucket.PORT, sp.Confidence.LOW, [self._wp("direct"), self._wp("renamed")]
        )
        assert out == "cherry-pick-with-edits"

    def test_unmapped_path_is_reimplement(self) -> None:
        out = sp.suggest_approach(
            sp.Bucket.EVALUATE, sp.Confidence.LOW, [self._wp("direct"), self._wp("unmapped")]
        )
        assert out == "re-implement"

    def test_evaluate_bucket_is_reimplement_even_if_paths_direct(self) -> None:
        out = sp.suggest_approach(sp.Bucket.EVALUATE, sp.Confidence.LOW, [self._wp("direct")])
        assert out == "re-implement"

    def test_no_paths_is_reimplement(self) -> None:
        out = sp.suggest_approach(sp.Bucket.EVALUATE, sp.Confidence.LOW, [])
        assert out == "re-implement"
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestSuggestApproach -v`
Expected: FAIL — no attribute `suggest_approach`.

- [ ] **Step 3: Implement**

Append to `tools/sync_parent.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestSuggestApproach -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py
git commit -m "feat(tools): sync_parent suggested-approach heuristic"
```

---

## Task 9: Report formatter

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`

The formatter takes a list of already-enriched per-PR records. Define a small `PRReport` dataclass to carry the per-PR pipeline output into the formatter, keeping `format_report` pure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
class TestReportFormat:
    """format_report emits the header, summary table, and four sections."""

    def _report(self, bucket: Any, confidence: Any, number: int) -> Any:
        pr = sp.PR(number=number, title=f"feat: thing (#{number})", commits=[], files=["analytics/regime.py"])
        return sp.PRReport(
            pr=pr,
            bucket=bucket,
            confidence=confidence,
            wifey_paths=[sp.WifeyPath("analytics/regime.py", "analytics/regime.py", "direct")],  # type: ignore[arg-type]
            memory_excerpt="Some why.",
            approach="cherry-pick-with-edits",
        )

    def test_header_and_range(self) -> None:
        out = sp.format_report([self._report(sp.Bucket.PORT, sp.Confidence.LOW, 1)], "635ed5a", "abcdef0")
        assert "# Parent sync report" in out
        assert "635ed5a..abcdef0" in out
        assert "--bump-to abcdef0" in out

    def test_summary_counts(self) -> None:
        reports = [
            self._report(sp.Bucket.SKIP, sp.Confidence.LOW, 1),
            self._report(sp.Bucket.PORT, sp.Confidence.LOW, 2),
            self._report(sp.Bucket.EVALUATE, sp.Confidence.LOW, 3),
            self._report(sp.Bucket.PORT, sp.Confidence.HIGH, 4),  # routed to ALREADY-APPLIED
        ]
        out = sp.format_report(reports, "a", "b")
        assert "| SKIP" in out and "| 1 |" in out
        assert "## ALREADY-APPLIED" in out

    def test_high_confidence_port_routes_to_already_applied(self) -> None:
        out = sp.format_report([self._report(sp.Bucket.PORT, sp.Confidence.HIGH, 7)], "a", "b")
        already = out.split("## ALREADY-APPLIED", 1)[1]
        assert "#7" in already

    def test_port_detail_block_has_memory_and_approach(self) -> None:
        out = sp.format_report([self._report(sp.Bucket.PORT, sp.Confidence.LOW, 9)], "a", "b")
        assert "Suggested approach" in out
        assert "cherry-pick-with-edits" in out
        assert "Some why." in out
        assert "https://github.com/s10023/buibui-moon-trader-bot/pull/9" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestReportFormat -v`
Expected: FAIL — no attribute `PRReport` / `format_report`.

- [ ] **Step 3: Implement**

Append to `tools/sync_parent.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestReportFormat -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run ruff to confirm the one-liner `lines.extend(...) if ... else ...` is acceptable**

Run: `poetry run ruff check tools/sync_parent.py`
Expected: no errors. If ruff flags the conditional-expression-as-statement (`B018`/style), replace that single line with an explicit `if r.wifey_paths:` block before re-running.

- [ ] **Step 6: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py
git commit -m "feat(tools): sync_parent markdown report formatter"
```

---

## Task 10: CLI orchestrator + integration smoke test

**Files:**

- Modify: `tools/sync_parent.py`
- Test: `tests/test_sync_parent.py`

`main()` wires the pipeline, handles CLI flags, maps fail-fast conditions to exit-1 messages, and writes the report. `run_pipeline` (pure-ish, takes pre-fetched PRs + memory text) is factored out so the smoke test can drive it without mocking `subprocess` for every helper.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_parent.py`:

```python
class TestCLI:
    """Argparse surface + bump-to guard."""

    def test_parses_all_flags(self) -> None:
        ns = sp.build_arg_parser().parse_args(["--from", "abc", "--no-fetch"])
        assert ns.from_hash == "abc"
        assert ns.no_fetch is True

    def test_full_and_bump_flags(self) -> None:
        ns = sp.build_arg_parser().parse_args(["--full"])
        assert ns.full is True
        ns2 = sp.build_arg_parser().parse_args(["--bump-to", "deadbee"])
        assert ns2.bump_to == "deadbee"


class TestSmokeRun:
    """run_pipeline produces a well-formed report from fixture PRs."""

    def test_pipeline_buckets_and_sections(self, mocker: Any) -> None:
        # Two PRs: one survivor bugfix (PORT), one binance fix (SKIP).
        prs = [
            sp.PR(number=403, title="fix: regime (#403)", commits=[], files=["analytics/regime.py"]),
            sp.PR(number=402, title="fix: binance (#402)", commits=[], files=["utils/binance_client.py"]),
        ]
        memory_text = (
            Path(__file__).parent / "fixtures" / "sync_parent" / "sample_memory.md"
        ).read_text()
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=True)
        mocker.patch("tools.sync_parent.extract_added_symbols", return_value=[])  # -> UNKNOWN
        reports = sp.run_pipeline(prs, memory_text)
        out = sp.format_report(reports, "635ed5a", "abcdef0")
        assert "# Parent sync report" in out
        assert "## SKIP" in out and "## PORT" in out
        assert "#403" in out and "#402" in out

    def test_main_writes_report_file(self, mocker: Any, tmp_path: Path) -> None:
        mocker.patch("tools.sync_parent.PARENT_REPO_PATH", tmp_path)  # exists
        mocker.patch("tools.sync_parent._parent_current_branch", return_value="main")
        mocker.patch(
            "tools.sync_parent.fetch_parent_commits",
            return_value=[sp.Commit("abc", "fix: regime (#403)", "", ["analytics/regime.py"])],
        )
        mocker.patch("tools.sync_parent._parent_head_hash", return_value="abcdef0")
        mocker.patch("tools.sync_parent._read_parent_memory", return_value="(empty)")
        mocker.patch("tools.sync_parent._wifey_path_exists", return_value=True)
        mocker.patch("tools.sync_parent.extract_added_symbols", return_value=[])
        report_path = tmp_path / "out.md"
        mocker.patch("tools.sync_parent._report_path", return_value=report_path)
        code = sp.main(["--from", "635ed5a", "--no-fetch"])
        assert code == 0
        assert report_path.exists()
        assert "Parent sync report" in report_path.read_text()

    def test_main_bump_to_unknown_hash_exits_1(self, mocker: Any, tmp_path: Path) -> None:
        mocker.patch("tools.sync_parent.PARENT_REPO_PATH", tmp_path)
        mocker.patch("tools.sync_parent._parent_current_branch", return_value="main")
        mocker.patch(
            "tools.sync_parent._hash_exists_in_parent", return_value=False
        )
        code = sp.main(["--bump-to", "nope123"])
        assert code == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_sync_parent.py::TestCLI tests/test_sync_parent.py::TestSmokeRun -v`
Expected: FAIL — no attribute `build_arg_parser` / `run_pipeline` / `main`.

- [ ] **Step 3: Implement orchestrator**

Append to `tools/sync_parent.py`:

```python
# --------------------------------------------------------------------------- #
# Orchestration + CLI
# --------------------------------------------------------------------------- #


def _parent_current_branch() -> str:
    return _git(PARENT_REPO_PATH, "branch", "--show-current").strip()


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
        symbols: list[str] = []
        for commit in pr.commits:
            symbols.extend(extract_added_symbols(_commit_diff(commit.sha)))
        confidence = detect_already_applied(symbols) if symbols else Confidence.UNKNOWN
        approach = suggest_approach(bucket, confidence, wifey_paths)
        reports.append(
            PRReport(pr, bucket, confidence, wifey_paths, excerpt, approach)
        )
    return reports


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="from_hash", default=None, help="override start hash")
    p.add_argument("--full", action="store_true", help="scan fork commit -> HEAD")
    p.add_argument("--bump-to", dest="bump_to", default=None, help="update state pointer only, no scan")
    p.add_argument("--no-fetch", dest="no_fetch", action="store_true", help="use local refs only")
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
    branch = _parent_current_branch()
    if branch != "main":
        return _fail(f"Parent must be on 'main'. Currently on '{branch}'.")

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
            return _fail(f"Sync state file malformed: {exc}. Inspect or delete to re-bootstrap.")

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

    try:
        path = _report_path()
        path.write_text(report)
    except OSError:
        return _fail("Cannot write report. Check /tmp permissions.")

    print(f"Wrote {len(reports)} PR(s) to {path}")
    print(f"When done reviewing: poetry run python tools/sync_parent.py --bump-to {to_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_sync_parent.py::TestCLI tests/test_sync_parent.py::TestSmokeRun -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full test module + lint + typecheck**

```bash
poetry run pytest tests/test_sync_parent.py -v
poetry run ruff check tools/sync_parent.py tests/test_sync_parent.py
poetry run ruff format --check tools/sync_parent.py tests/test_sync_parent.py
poetry run mypy tools/sync_parent.py
```

Expected: all tests PASS (~44 across 9 classes); ruff clean; mypy clean.

- [ ] **Step 6: Commit**

```bash
git add tools/sync_parent.py tests/test_sync_parent.py
git commit -m "feat(tools): sync_parent CLI orchestrator + integration smoke test"
```

---

## Task 11: Makefile target

**Files:**

- Modify: `Makefile`

- [ ] **Step 1: Add the target**

Add to the `.PHONY` line (Makefile line 14), appending `wifey-sync-parent` to the existing list, and add this target after the `wifey-recalibrate` block (around line 202):

```makefile
wifey-sync-parent:
 @echo "🔀 Scanning parent repo for portable changes..."
 @PYTHONPATH=. poetry run python tools/sync_parent.py \
  $(if $(FROM),--from $(FROM),) \
  $(if $(FULL),--full,) \
  $(if $(BUMP_TO),--bump-to $(BUMP_TO),) \
  $(if $(NO_FETCH),--no-fetch,)
```

- [ ] **Step 2: Verify the target parses**

Run: `make -n wifey-sync-parent`
Expected: prints the `poetry run python tools/sync_parent.py` line with no make syntax error.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build(make): add wifey-sync-parent target"
```

---

## Task 12: SKILL.md + CLAUDE.md skills-table entry

**Files:**

- Create: `.claude/skills/sync-parent/SKILL.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write SKILL.md**

Create `.claude/skills/sync-parent/SKILL.md`:

````markdown
---
name: sync-parent
description: >
  Detect-and-recommend pipeline for porting upstream parent-repo
  (buibui-moon-trader-bot) changes into the wifey fork. Scans parent PRs merged
  since the last sync point, classifies each SKIP / PORT / EVALUATE /
  ALREADY-APPLIED, and writes a context-rich report to /tmp/parent-sync-<date>.md.
  Invoke when the user says "/sync-parent", asks to "check the parent repo",
  "port upstream changes", "what changed in the parent", or after a known parent
  refactor.
allowed-tools: Bash, Read
---

# Sync from parent repo

Read-only catch-up tool. Surfaces parent-repo PRs that may be worth porting into
wifey, classifies them, and enriches each with the parent MEMORY excerpt + a
suggested approach. **It never edits wifey code** — a human ports.

## When to use

- Periodic catch-up with the parent repo.
- Before or after a known upstream refactor.
- Bootstrap (first run) — scans the full fork → HEAD range.

## Prerequisites

- Parent clone present at `/home/kng/repo/buibui-moon-trader-bot`, checked out on `main`.
- State file `project_parent_sync_state.md` exists in wifey memory, or the skill
  bootstraps from the fork commit `635ed5a` on first run.

## Invocation

```bash
make wifey-sync-parent                 # incremental from the state pointer
make wifey-sync-parent FULL=1          # full fork -> HEAD audit
make wifey-sync-parent NO_FETCH=1      # local refs only (offline)
make wifey-sync-parent FROM=<hash>     # override start point
make wifey-sync-parent BUMP_TO=<hash>  # advance the pointer, no scan
```

Direct: `PYTHONPATH=. poetry run python tools/sync_parent.py [flags]`.

## Output

- `/tmp/parent-sync-<date>.md` — summary table + four sections (SKIP / PORT /
  EVALUATE / ALREADY-APPLIED). PORT and EVALUATE entries are full detail blocks
  with parent paths → wifey targets, the parent MEMORY excerpt, and a suggested
  approach (`verify-only` / `cherry-pick-with-edits` / `re-implement`).
- A pointer-bump hint on stdout.

## Workflow

1. Run `make wifey-sync-parent`. If it reports a fail-fast error (parent missing,
   not on `main`, malformed state), **surface it to the user and stop** — do not
   auto-clone, auto-checkout, or auto-bump.
2. Read `/tmp/parent-sync-<date>.md`. Summarise the bucket counts for the user.
3. For each **PORT** / **EVALUATE** candidate the user wants: open a fresh Claude
   session, paste the PR number + the parent MEMORY excerpt from the report, and
   do the actual port work there (this skill does not edit code).
4. Once the user confirms every PR in the range has been decided, advance the
   pointer: `make wifey-sync-parent BUMP_TO=<to_hash>` (the exact command is
   printed at the end of the report).

## Notes

- The parent squash-merges every PR (one commit, `(#N)` suffix) — there are no
  merge commits, which is why the tool groups by subject, not `git log --merges`.
- ALREADY-APPLIED is a confidence flag, never an auto-removal. Always verify.
- Sweep findings (`tp_r`, ATR multipliers) land in EVALUATE: methodology may
  transfer, values won't (equity cohort ≠ crypto cohort).

````

- [ ] **Step 2: Add the CLAUDE.md skills-table row**

In `CLAUDE.md`, in the Agent Skills table (the `| Skill | Invoke | When to use | Cadence |` table), add this row after the `frontend-svelte` row:

```markdown
| `sync-parent` | `/sync-parent` | Detect-and-recommend parent-repo PRs to port into wifey; writes a classified report to `/tmp/parent-sync-<date>.md` | Periodic catch-up with the parent, or after a known upstream refactor |
```

- [ ] **Step 3: Lint markdown**

Run: `make lint-md`
Expected: no markdownlint errors on the new SKILL.md or CLAUDE.md. Fix any flagged (common: surround tables/fenced blocks with blank lines, specify fence languages).

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/sync-parent/SKILL.md CLAUDE.md
git commit -m "docs(skills): add /sync-parent skill + CLAUDE.md table row"
```

---

## Task 13: Final gate — full suite, manual smoke test, PR

**Files:** none (verification + PR)

- [ ] **Step 1: Full project gate**

```bash
make lint-py
make typecheck
make test
```

Expected: ruff clean; mypy strict clean on **192** source files (191 + `tools/sync_parent.py`); pytest baseline **~1092** (1048 + ~44 new test methods). Record the exact final count from the run output.

- [ ] **Step 2: Manual smoke test — empty range**

```bash
make wifey-sync-parent FROM=$(git -C /home/kng/repo/buibui-moon-trader-bot rev-parse --short origin/main) NO_FETCH=1
```

Expected: prints `No new parent commits since <hash>. Nothing to review.` and writes no `/tmp` file.

- [ ] **Step 3: Manual smoke test — real incremental run**

```bash
make wifey-sync-parent
```

Expected: writes `/tmp/parent-sync-<today>.md`; open it and confirm it has the header, summary table with four bucket rows, and at least the SKIP + PORT + EVALUATE + ALREADY-APPLIED section headers. Spot-check that a known crypto PR (e.g. one touching `funding_*`) landed in SKIP and a survivor-module bugfix landed in PORT. Do **not** run `--bump-to` (leave the pointer for the user to advance after review).

- [ ] **Step 4: Update wifey MEMORY.md Current State**

Per CLAUDE.md Session Memory Protocol, add a one-line Current State entry noting the `/sync-parent` skill shipped and pointing at this plan + the spec. Keep it under ~200 chars (MEMORY.md is over its size limit).

- [ ] **Step 5: Run /pr-summary, then push and open the PR**

```bash
git push -u origin feat/sync-parent-skill
gh pr create --repo s10023/buibui-wifey-wall-street-bot \
  --title "feat(tools): /sync-parent detect-and-recommend skill" \
  --body-file /tmp/pr-feat-sync-parent-skill.md
```

(The `/pr-summary` skill writes the body file; invoke it first.)

- [ ] **Step 6: Run /post-branch**

Immediately after `gh pr create` succeeds, invoke `/post-branch` to diff behaviour changes against the doc surfaces and propose any further doc edits, before reporting the PR URL.

---

## Self-review (run after the plan is written, before execution)

**1. Spec coverage** — every spec component maps to a task:

| Spec item | Task |
| --- | --- |
| `load_sync_state` / bootstrap / malformed-raise | Task 2 |
| `fetch_parent_commits` (squash-aware) | Task 3 |
| `group_into_prs` (merge / squash / direct) | Task 3 |
| `classify_pr` (SKIP/PORT/EVALUATE) | Task 5 |
| `extract_memory_entry` | Task 7 |
| `detect_already_applied` (HIGH/MED/LOW/UNKNOWN) | Task 6 |
| `translate_paths` | Task 4 |
| `suggest_approach` (3 labels) | Task 8 |
| `format_report` (header + summary + 4 sections + detail block) | Task 9 |
| `main()` + CLI flags (`--from`/`--full`/`--bump-to`/`--no-fetch`) | Task 10 |
| Fail-fast error table (parent missing / not main / fetch fail / malformed state / unknown bump hash / `/tmp` unwritable) | Task 10 |
| Graceful degradation (bootstrap / direct-commit `#none` / no memory entry / unmapped path / no symbols / grep failure) | Tasks 3, 4, 6, 7, 9, 10 |
| 8 test classes + smoke run | Tasks 1–10 (TestModuleContract is an extra) |
| 3 fixtures | Tasks 3, 6, 7 |
| Makefile target | Task 11 |
| SKILL.md | Task 12 |
| Lint/type/test gate (192 files, ~1070+ tests) | Task 13 |

No spec component is unaddressed. `--bump-to`-not-in-parent and `/tmp`-unwritable fail-fasts are covered by `main()` in Task 10.

**2. Placeholder scan** — no TBD/TODO; every code step shows complete code; every test step shows real assertions.

**3. Type consistency** — `classify_pr(pr, wifey_paths)` and `suggest_approach(bucket, confidence, wifey_paths)` carry a `wifey_paths` arg (a documented refinement over the spec's single-arg signatures, justified to avoid re-walking paths). `Bucket`/`Confidence` enums, `PR`/`Commit`/`WifeyPath`/`PRReport` dataclasses, and `PathKind` literals are referenced identically everywhere. `detect_already_applied(symbols, grep)` and `extract_memory_entry(pr_number, memory_text)` take the testable parameter forms used in their tests.
