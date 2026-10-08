"""Tests for `deploy/backup-analytics.sh`'s coverage of trees OUTSIDE the repo.

`BACKUP_DIRS` and `BACKUP_FILES` are both resolved against `$REPO`, so before
the EXTERNAL ROOTS section the memory tree — which holds `project_todo_master.md`,
the single source of truth to-do — was uncovered by construction rather than by
judgement. These tests pin the fix. Nothing else does: there is no shellcheck
here and no CI step reads `deploy/`, so this file and its off-site sibling are
the only gates on either script.

Why a real end-to-end run rather than `--dry-run`
-------------------------------------------------
`--dry-run` reports what it *would* copy and returns before copying anything, so
a dry-run assertion passes whether or not the copy loop runs at all. That is the
vacuous-guard shape this repo has been bitten by twice (`docs/audits/
2026-08-13-vacuous-causality-guards.md`), and the subject here IS the copy. So
these drive the script for real against a fixture tree, via the two env knobs
(`WIFEY_REPO_ROOT`, `WIFEY_PYTHON`) that exist for exactly this.

`test_memory_tree_is_absent_without_it` is the negative control for the whole
file: it points `WIFEY_MEMORY_DIR` at a path that does not exist and asserts the
snapshot comes back WITHOUT a memory directory. Without it, every assertion
below would pass just as well if the script copied the memory tree from some
hardcoded location, and the env knob the other tests rely on would be unproven.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from tools.claude_home import memory_dir

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "backup-analytics.sh"


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A minimal stand-in for $REPO: a real DuckDB plus one backed-up tree.

    The database must carry a non-zero `signal_alert_outcomes` count — the
    script refuses to publish a snapshot without one, on the grounds that a
    schema copy is not a backup.
    """
    repo = tmp_path / "repo"
    (repo / "docs" / "plans").mkdir(parents=True)
    (repo / "docs" / "plans" / "note.md").write_text(
        "research output\n", encoding="utf-8"
    )

    con = duckdb.connect(str(repo / "analytics.db"))
    con.execute("CREATE TABLE signal_alert_outcomes (id INTEGER)")
    con.execute("INSERT INTO signal_alert_outcomes VALUES (1)")
    con.close()
    return repo


@pytest.fixture
def fake_memory(tmp_path: Path) -> Path:
    """A stand-in for the memory tree, with the SoT's filename in it."""
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("# index\n", encoding="utf-8")
    (mem / "project_todo_master.md").write_text("# SoT\n", encoding="utf-8")
    (mem / "reference_env_gotchas.md").write_text("# topic\n", encoding="utf-8")
    return mem


def run_backup(
    fake_repo: Path, tmp_path: Path, memory_dir: Path | str
) -> tuple[int, str, Path]:
    """Run the script for real; return (exit code, stdout, backup root)."""
    backup_root = tmp_path / "backups"
    env = dict(os.environ)
    env.update(
        {
            "WIFEY_REPO_ROOT": str(fake_repo),
            "WIFEY_BACKUP_ROOT": str(backup_root),
            "WIFEY_MEMORY_DIR": str(memory_dir),
            "WIFEY_PYTHON": sys.executable,
            "WIFEY_LOCK_RETRIES": "1",
            "WIFEY_LOCK_SLEEP": "0",
        }
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120
    )
    return proc.returncode, proc.stdout, backup_root


def only_snapshot(backup_root: Path) -> Path:
    snapshots = sorted((backup_root / "daily").glob("2*"))
    assert len(snapshots) == 1, f"expected one snapshot, got {snapshots}"
    return snapshots[0]


def test_memory_tree_lands_in_the_snapshot(
    fake_repo: Path, fake_memory: Path, tmp_path: Path
) -> None:
    """The point of the whole section: the SoT is no longer single-copy."""
    rc, _, backup_root = run_backup(fake_repo, tmp_path, fake_memory)
    assert rc == 0

    snapshot = only_snapshot(backup_root)
    assert (snapshot / "memory" / "project_todo_master.md").read_text(
        encoding="utf-8"
    ) == "# SoT\n"
    assert (snapshot / "memory" / "MEMORY.md").exists()
    assert (snapshot / "memory" / "reference_env_gotchas.md").exists()


def test_memory_tree_is_absent_without_it(fake_repo: Path, tmp_path: Path) -> None:
    """Negative control: prove the copy follows WIFEY_MEMORY_DIR.

    Without this, every other assertion in the file would also pass if the
    script copied the tree from a hardcoded path, and the env knob the rest of
    the suite steers with would be unverified.
    """
    rc, out, backup_root = run_backup(fake_repo, tmp_path, tmp_path / "nonexistent")
    assert rc == 0, "an absent external root must not fail the backup"

    snapshot = only_snapshot(backup_root)
    assert not (snapshot / "memory").exists()
    assert "ABSENT" in out


def test_absent_root_is_recorded_in_the_manifest(
    fake_repo: Path, tmp_path: Path
) -> None:
    """A warning scrolls; a manifest field is read by every later audit.

    `files: 0` is the artifact-level signal that coverage regressed, which is
    the failure this section exists to make visible rather than silent.
    """
    _, _, backup_root = run_backup(fake_repo, tmp_path, tmp_path / "nonexistent")
    manifest = json.loads(
        (only_snapshot(backup_root) / "MANIFEST.json").read_text(encoding="utf-8")
    )

    assert manifest["external_roots"]["memory"]["files"] == 0
    assert manifest["external_roots"]["memory"]["path"].endswith("nonexistent")


def test_present_root_is_counted_in_the_manifest(
    fake_repo: Path, fake_memory: Path, tmp_path: Path
) -> None:
    _, _, backup_root = run_backup(fake_repo, tmp_path, fake_memory)
    manifest = json.loads(
        (only_snapshot(backup_root) / "MANIFEST.json").read_text(encoding="utf-8")
    )

    assert manifest["external_roots"]["memory"]["files"] == 3
    assert manifest["external_roots"]["memory"]["path"] == str(fake_memory)


def test_research_files_still_counts_repo_trees_only(
    fake_repo: Path, fake_memory: Path, tmp_path: Path
) -> None:
    """`research_files` must not silently absorb the external roots.

    Earlier snapshots already recorded that field, and the script's own comment
    says a coverage regression is meant to show up as a count that DROPPED. If
    adding the memory tree inflated it, every historical diff would show an
    unexplained jump and the field would stop being readable as coverage.
    """
    _, _, backup_root = run_backup(fake_repo, tmp_path, fake_memory)
    manifest = json.loads(
        (only_snapshot(backup_root) / "MANIFEST.json").read_text(encoding="utf-8")
    )

    # docs/plans/note.md is the only repo-derived file the fixture builds; every
    # BACKUP_FILES entry is absent from it, so the count is 1 however many are
    # declared — unchanged by the 3-file memory tree beside it. Deliberately not
    # restating that entry count: a number whose only consumer is the sentence
    # carrying it goes stale unnoticed, which is why `handoff-size` lost its stamp.
    assert manifest["research_files"] == 1
    assert manifest["external_roots"]["memory"]["files"] == 3


def test_default_memory_path_is_derived_from_the_repo(
    fake_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the DEFAULT, which every other test here steers past.

    The rest of the file sets `WIFEY_MEMORY_DIR`, so it proves the copy
    mechanism works while saying nothing about the path a real run resolves.
    That gap matters more than it looks: a wrong derivation does not raise, it
    warns and skips — the silent-absence failure this whole section exists to
    remove — and the suite would have stayed green through it.

    So this builds the tree at the derived location and asserts it is copied,
    rather than merely asserting the script reported a plausible-looking path.
    """
    home = tmp_path / "home"
    # ⚠ `Path.home()` reads USERPROFILE on Windows and HOME on POSIX, so an
    # isolation setting only one of them leaks the REAL home on the other host
    # — and it leaks silently, because the derived tree merely fails to exist
    # and the run warns rather than failing. Set both.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    # Built where the SHARED derivation says, not where a second copy of the
    # rule says. That coupling is the thing under test: the shell script and
    # `tools/` each had their own derivation and drifted apart on Windows, so
    # asserting they agree is the assertion that would have caught it. The rule
    # itself is pinned separately, platform-independently, in
    # `test_claude_home.py`.
    derived = memory_dir(fake_repo)
    derived.mkdir(parents=True)
    (derived / "project_todo_master.md").write_text("# SoT\n", encoding="utf-8")

    backup_root = tmp_path / "backups"
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "WIFEY_REPO_ROOT": str(fake_repo),
            "WIFEY_BACKUP_ROOT": str(backup_root),
            "WIFEY_PYTHON": sys.executable,
            "WIFEY_LOCK_RETRIES": "1",
            "WIFEY_LOCK_SLEEP": "0",
        }
    )
    env.pop("WIFEY_MEMORY_DIR", None)

    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr

    snapshot = only_snapshot(backup_root)
    assert (snapshot / "memory" / "project_todo_master.md").read_text(
        encoding="utf-8"
    ) == "# SoT\n"


def test_memory_lands_inside_the_snapshot_not_beside_it(
    fake_repo: Path, fake_memory: Path, tmp_path: Path
) -> None:
    """Placement is the design decision, so it gets the assertion.

    Inside the snapshot, the memory tree inherits the two properties the
    snapshot already has: it is published by the same atomic rename, so it is
    never half-copied under a name a freshness check trusts, and it ages out
    under the same retention. A sibling tier at $BACKUP_ROOT level would have
    had neither, and would have been mirrored off-site as an untracked tier
    with no MANIFEST attesting it.
    """
    _, _, backup_root = run_backup(fake_repo, tmp_path, fake_memory)

    assert "memory" not in [p.name for p in backup_root.iterdir()]
    assert (only_snapshot(backup_root) / "memory").is_dir()
