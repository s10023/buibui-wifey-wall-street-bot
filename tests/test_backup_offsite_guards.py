"""Tests for the destination guards in `deploy/backup-offsite.sh`.

`rclone sync` mirrors deletions into its destination, so every one of these
guards stands between a mistyped env var and the permanent loss of data this
repo does not own. Nothing else pins them: there is no shellcheck in this
repo and no CI step reads this script, so these tests are the only gate.

Two design choices keep the suite from going vacuous — the failure mode where
a guard is "verified" by a fixture that could never have reached the guarded
call in the first place:

1. Every rejection test asserts `sync` was never invoked, not merely that the
   exit code was 1. A script that died for an unrelated reason also exits 1.
2. `test_destination_matching_local_root_is_allowed` is the positive control
   for the intruder check. Without it, that guard would pass just as well if
   it rejected *any* non-empty destination, which would break every sync after
   the first one.

One case here asserts a hole rather than a guard. Guard 3 compares only
top-level entries, so it cannot tell a same-shaped sibling tree from our own —
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
    (snapshot / "MANIFEST.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
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
        '  lsf) if [ "$2" = "-R" ]; then\n'
        '         printf %s "${FAKE_LSF_RECURSIVE:-}"\n'
        '         exit "${FAKE_LSF_RECURSIVE_RC:-${FAKE_LSF_RC:-0}}"\n'
        "       fi\n"
        '       printf %s "${FAKE_LSF_OUTPUT:-}"\n'
        '       [ -n "${FAKE_LSF_STDERR:-}" ] && echo "$FAKE_LSF_STDERR" >&2\n'
        '       exit "${FAKE_LSF_RC:-0}" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return bindir


def run_script(
    backup_root: Path,
    fake_rclone: Path,
    tmp_path: Path,
    remote: str | None,
    lsf_output: str = "",
    lsf_rc: int = 0,
    lsf_stderr: str = "",
    lsf_recursive: str = "",
    lsf_recursive_rc: int | None = None,
    extra_env: dict[str, str] | None = None,
    args: tuple[str, ...] = (),
) -> tuple[int, str, list[str]]:
    """Run the script; return (exit code, stderr, rclone subcommands invoked)."""
    log = tmp_path / "rclone.log"
    log.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.pop("WIFEY_OFFSITE_MAX_SNAPSHOT_DELETES", None)
    env.update(
        {
            "PATH": f"{fake_rclone}{os.pathsep}{env['PATH']}",
            "WIFEY_BACKUP_ROOT": str(backup_root),
            "RCLONE_LOG": str(log),
            "FAKE_LSF_OUTPUT": lsf_output,
            "FAKE_LSF_RC": str(lsf_rc),
            "FAKE_LSF_STDERR": lsf_stderr,
            "FAKE_LSF_RECURSIVE": lsf_recursive,
        }
    )
    if lsf_recursive_rc is not None:
        env["FAKE_LSF_RECURSIVE_RC"] = str(lsf_recursive_rc)
    env.update(extra_env or {})
    if remote is None:
        env.pop("WIFEY_BACKUP_REMOTE", None)
    else:
        env["WIFEY_BACKUP_REMOTE"] = remote

    proc = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=20,
    )
    calls = [
        line.split()[0]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return proc.returncode, proc.stderr, calls


def sync_argv(tmp_path: Path) -> list[str]:
    """The argv of the one `rclone sync` call the last run made."""
    lines = (tmp_path / "rclone.log").read_text(encoding="utf-8").splitlines()
    (line,) = [ln for ln in lines if ln.startswith("sync ")]
    return line.split()


def max_delete(tmp_path: Path) -> int:
    argv = sync_argv(tmp_path)
    return int(argv[argv.index("--max-delete") + 1])


def remote_snapshots(*snapshots: str, files: int = 3) -> str:
    """A recursive lsf listing holding `files` files in each named snapshot."""
    return "".join(f"{s}/f{i}.bin\n" for s in snapshots for i in range(files))


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
    """Characterization test: it asserts the hole, not the protection.

    Guard 3 compares top-level entries only, so a destination whose top level
    matches ours passes even when everything below it belongs to someone else.
    `gdrive:snapshots` is the crypto parent's tree and is `daily/` + `weekly/`
    exactly like this one; the parent measured a `--dry-run` aimed at it on
    2026-08-15 passing every guard and reporting deletions of its own files.

    Guard 3 is not the control for that. wifey's remote is pinned to a
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


def test_unlistable_destination_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """A listing that failed is not an empty destination (parent #785).

    If lsf's stderr and exit code are discarded, a broken config lists
    nothing and the intruder guard passes without having looked. Measured on
    this host: a missing remote exits 1.
    """
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive-wifey:snapshots",
        lsf_rc=1,
        lsf_stderr='CRITICAL: didn\'t find section in config file ("gdrive-wifey")',
    )
    assert rc == 1
    assert "sync" not in calls
    assert "could not list" in err
    assert "didn't find section" in err  # rclone's own reason is surfaced


def test_absent_destination_still_syncs(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """The control for the test above: rc 3 IS an empty destination.

    An absent Drive folder makes lsf exit 3 (measured on this host), and the
    first-ever sync depends on that passing. A guard that failed on ANY non-zero
    lsf would pass the test above and make the off-site leg impossible to
    bootstrap.
    """
    rc, _, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive-wifey:snapshots",
        lsf_rc=3,
        lsf_stderr="NOTICE: Failed to lsf: directory not found",
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


# --- guard 4: the deletion cap ------------------------------------------------
#
# Every refusal below has a control that syncs, and the controls read the
# `--max-delete` value off the sync argv: that proves the plan was counted, not
# merely that the guard stayed quiet because it never saw a listing.

OURS = "gdrive-wifey:snapshots"
TOP = "daily/\nweekly/\n"


def test_remote_matching_local_plans_zero_deletes(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """Positive control for the diff: files we hold locally are never counted.

    If the local listing's path format drifted from rclone's (a `./` prefix, a
    CR, a backslash), every remote file would read as doomed and this goes red.
    """
    rc, _, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        lsf_recursive="daily/2026-01-01/MANIFEST.json\r\n",
    )
    assert rc == 0
    assert "sync" in calls
    assert max_delete(tmp_path) == 0


def test_routine_rotation_syncs_and_binds_max_delete(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """One pruned daily is a routine run: it syncs, capped at the vetted count."""
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        lsf_recursive="daily/2026-01-01/MANIFEST.json\n"
        + remote_snapshots("daily/2025-12-18"),
    )
    assert rc == 0, err
    assert "sync" in calls
    assert max_delete(tmp_path) == 3


def test_deletions_at_the_cap_still_sync(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """The boundary: 4 snapshots is the default cap and must pass."""
    doomed = [f"daily/2025-12-{d:02d}" for d in (15, 16, 17, 18)]
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        lsf_recursive=remote_snapshots(*doomed),
    )
    assert rc == 0, err
    assert max_delete(tmp_path) == 12


def test_deletions_above_the_cap_are_refused(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """The post-migration case: a new root would mirror away the old history."""
    doomed = [f"daily/2025-12-{d:02d}" for d in range(1, 20)]
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        lsf_recursive=remote_snapshots(*doomed),
    )
    assert rc == 1
    assert "sync" not in calls
    assert "57 remote file(s)" in err
    assert "19 snapshot(s)" in err
    assert "daily/2025-12-01" in err


def test_dry_run_above_the_cap_is_refused(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """The dry run is where a human reads the plan, so it must refuse too."""
    doomed = [f"weekly/2025-1{m}-01" for m in range(5)]
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        lsf_recursive=remote_snapshots(*doomed),
        args=("--dry-run",),
    )
    assert rc == 1
    assert "sync" not in calls
    assert "5 snapshot(s)" in err


def test_deletions_inside_kept_snapshots_count(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """A local bug that strips content from kept snapshots touches each one.

    The local root still holds every snapshot directory, so a cap on whole
    snapshots gone would read zero here.
    """
    days = [f"daily/2026-01-0{d}" for d in range(1, 6)]
    for day in days[1:]:
        (backup_root / day).mkdir(parents=True)
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        lsf_recursive=remote_snapshots(*days, files=1),
    )
    assert rc == 1
    assert "sync" not in calls
    assert "5 snapshot(s)" in err


def test_override_lets_a_deliberate_prune_through(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    doomed = [f"daily/2025-12-{d:02d}" for d in range(1, 20)]
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        lsf_recursive=remote_snapshots(*doomed),
        extra_env={"WIFEY_OFFSITE_MAX_SNAPSHOT_DELETES": "19"},
    )
    assert rc == 0, err
    assert max_delete(tmp_path) == 57


@pytest.mark.parametrize("bad", ["-1", "four", "4.5"])
def test_malformed_cap_is_refused(
    backup_root: Path, fake_rclone: Path, tmp_path: Path, bad: str
) -> None:
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        extra_env={"WIFEY_OFFSITE_MAX_SNAPSHOT_DELETES": bad},
    )
    assert rc == 1
    assert "sync" not in calls
    assert "not a" in err


def test_unlistable_plan_is_refused(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """A recursive listing that FAILED cannot be counted, so it cannot be capped."""
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        OURS,
        lsf_output=TOP,
        lsf_recursive_rc=1,
    )
    assert rc == 1
    assert "sync" not in calls
    assert "cannot count the plan" in err
