"""Tests for the destination guards in `deploy/backup-offsite.sh`.

`rclone sync` mirrors deletions into its destination, so every one of these
guards stands between a mistyped env var and the permanent loss of data this
repo does not own. Nothing else pins them: there is no shellcheck in this
repo and no CI step reads this script, so these tests are the only gate.

Two design choices keep the suite from going vacuous — the failure mode where
a guard is "verified" by a fixture that could never have reached the guarded
call in the first place:

1. Every rejection test asserts `sync` was NEVER invoked, not merely that the
   exit code was 1. A script that died for an unrelated reason also exits 1.
2. `test_destination_matching_local_root_is_allowed` is the positive control
   for the intruder check. Without it, that guard would pass just as well if
   it rejected *any* non-empty destination, which would break every sync after
   the first one.

⚠ One case here asserts a HOLE rather than a guard. Guard 3 compares only
TOP-LEVEL entries, so it cannot tell a same-shaped sibling tree from our own —
the crypto parent's remote is `daily/` + `weekly/` exactly like this one, and
the parent measured a dry-run against it passing every check and reporting
deletions of its own files. `test_same_shaped_sibling_is_NOT_caught` pins that
limitation so it stops being folklore.

What actually separates the two repos is structural and lives outside this
file: wifey has its OWN rclone remote pinned to its OWN folder, so the
parent's tree is not addressable from here at all. Do not read guard 3 as the
control for that — it is not, and the test says so.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "backup-offsite.sh"


@pytest.fixture
def backup_root(tmp_path: Path) -> Path:
    """A backup root that passes the pre-existing source-side guards."""
    snapshot = tmp_path / "root" / "daily" / "2026-01-01"
    snapshot.mkdir(parents=True)
    (snapshot / "MANIFEST.json").write_text(json.dumps({"ok": True}))
    (tmp_path / "root" / "weekly").mkdir()
    return tmp_path / "root"


@pytest.fixture
def fake_rclone(tmp_path: Path) -> Path:
    """A shim that records its argv and replays scripted `lsf` output.

    Returning success for every subcommand is deliberate: it means a guard that
    fails to fire produces a *passing* sync, so the rejection tests below can
    only pass because the guard actually blocked.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "rclone"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$@" >> "$RCLONE_LOG"\n'
        'case "$1" in\n'
        '  lsf) printf %s "${FAKE_LSF_OUTPUT:-}" ;;\n'
        "esac\n"
        "exit 0\n"
    )
    shim.chmod(0o755)
    return bindir


def run_script(
    backup_root: Path,
    fake_rclone: Path,
    tmp_path: Path,
    remote: str | None,
    lsf_output: str = "",
) -> tuple[int, str, list[str]]:
    """Run the script; return (exit code, stderr, rclone subcommands invoked)."""
    log = tmp_path / "rclone.log"
    log.write_text("")
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_rclone}{os.pathsep}{env['PATH']}",
            "WIFEY_BACKUP_ROOT": str(backup_root),
            "RCLONE_LOG": str(log),
            "FAKE_LSF_OUTPUT": lsf_output,
        }
    )
    if remote is None:
        env.pop("WIFEY_BACKUP_REMOTE", None)
    else:
        env["WIFEY_BACKUP_REMOTE"] = remote

    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=20
    )
    calls = [line.split()[0] for line in log.read_text().splitlines() if line.strip()]
    return proc.returncode, proc.stderr, calls


def test_wellformed_remote_syncs(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """Positive control: without it, every rejection below could be a no-op."""
    rc, _, calls = run_script(
        backup_root, fake_rclone, tmp_path, "gdrive-wifey:snapshots"
    )
    assert rc == 0
    assert "sync" in calls


def test_bare_remote_without_path_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """`gdrive:` is the ENTIRE drive, and sync mirrors deletions into it."""
    rc, err, calls = run_script(backup_root, fake_rclone, tmp_path, "gdrive:")
    assert rc == 1
    assert "sync" not in calls
    assert "no path component" in err


def test_remote_without_colon_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """No colon means rclone writes LOCALLY — green job, no off-machine copy."""
    rc, err, calls = run_script(backup_root, fake_rclone, tmp_path, "gdrive")
    assert rc == 1
    assert "sync" not in calls
    assert "is not a remote" in err


def test_unset_remote_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    rc, err, calls = run_script(backup_root, fake_rclone, tmp_path, None)
    assert rc == 1
    assert "sync" not in calls
    assert "WIFEY_BACKUP_REMOTE is unset" in err


def test_foreign_destination_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """A well-formed remote aimed at somebody else's folder must not sync."""
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive-wifey:snapshots",
        lsf_output="daily/\nPhotos/\nresume.pdf\n",
    )
    assert rc == 1
    assert "sync" not in calls
    assert "Photos" in err
    assert "resume.pdf" in err
    assert "daily" not in err  # the entry we DO own must not be flagged


def test_same_shaped_sibling_is_NOT_caught(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """⚠ Characterization test: it asserts the HOLE, not the protection.

    Guard 3 compares TOP-LEVEL entries only, so a destination whose top level
    matches ours passes even when everything below it belongs to someone else.
    `gdrive:snapshots` is the crypto parent's tree and is `daily/` + `weekly/`
    exactly like this one; the parent measured a `--dry-run` aimed at it on
    2026-08-15 passing every guard and reporting deletions of its own files.

    Guard 3 is NOT the control for that. wifey's remote is pinned to a
    wifey-only folder, so the parent's tree is unreachable from here — the
    separation is structural, one layer below this script. This test exists so
    the distinction stops being folklore: if someone later widens the guard to
    compare recursively or to check an owner marker, this goes red and names
    what changed.

    By construction it drives the same code path as the positive control below
    — the guard cannot tell the two apart, which is the whole finding.
    """
    rc, _, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive:snapshots",
        lsf_output="daily/\nweekly/\n",
    )
    assert rc == 0
    assert "sync" in calls


def test_destination_matching_local_root_is_allowed(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """The control for the two guards above: a populated OWN destination must pass.

    Every sync after the first one hits this path. A guard that rejected any
    non-empty destination would pass both intruder tests and break production.
    """
    rc, _, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive-wifey:snapshots",
        lsf_output="daily/\nweekly/\n",
    )
    assert rc == 0
    assert "sync" in calls


def test_missing_manifest_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """Pre-existing guard, pinned here because sync would mirror the empty tree."""
    for manifest in backup_root.rglob("MANIFEST.json"):
        manifest.unlink()
    rc, err, calls = run_script(
        backup_root, fake_rclone, tmp_path, "gdrive-wifey:snapshots"
    )
    assert rc == 1
    assert "sync" not in calls
    assert "no MANIFEST.json" in err


def test_missing_backup_root_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    rc, err, calls = run_script(
        backup_root.parent / "absent", fake_rclone, tmp_path, "gdrive-wifey:snapshots"
    )
    assert rc == 1
    assert "sync" not in calls
    assert "does not exist" in err
