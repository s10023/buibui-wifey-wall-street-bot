"""Tests for `tools/clone_preflight.py` — the clean-clone pre-flight.

The gate exists because a *fresh clone* is the only mechanism that separates
"relative path to a committed asset" (fine) from "relative path to gitignored
operator data" (the defect). CI already is that clone; the gap this closes is
TIMING, not detection.

The load-bearing test here is the *dirty tree* one. A clone only ever sees
committed state, so a pre-flight run against an uncommitted tree silently
tests stale code and reports GREEN — the same shape of invisible pass the
gate exists to kill. Refusing has to happen before any clone is taken, so the
test asserts on the absence of the clone rather than only on the exit code.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tools.clone_preflight import (
    PYTEST_ARGS,
    REFUSED,
    clone_argv,
    dirty_paths,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _make_repo(tmp_path: Path) -> Path:
    """A real, minimal git repo — no mocks, the gate is all subprocess."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "committed.txt").write_text("committed\n")
    _git(repo, "add", "committed.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


class TestCloneArgv:
    def test_passes_no_hardlinks(self, tmp_path: Path) -> None:
        """`git clone --local` fails `Invalid cross-device link` onto /tmp here."""
        argv = clone_argv(tmp_path / "src", tmp_path / "dest")
        assert "--no-hardlinks" in argv

    def test_clones_the_repo_into_the_destination(self, tmp_path: Path) -> None:
        argv = clone_argv(tmp_path / "src", tmp_path / "dest")
        assert argv[:2] == ["git", "clone"]
        assert argv[-2:] == [str(tmp_path / "src"), str(tmp_path / "dest")]


class TestDirtyPaths:
    def test_clean_tree_reports_nothing(self, tmp_path: Path) -> None:
        assert dirty_paths(_make_repo(tmp_path)) == []

    def test_modified_file_is_reported(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "committed.txt").write_text("modified\n")
        assert any("committed.txt" in line for line in dirty_paths(repo))

    def test_untracked_file_is_reported(self, tmp_path: Path) -> None:
        """An untracked new module is exactly the change a clone would miss."""
        repo = _make_repo(tmp_path)
        (repo / "brand_new.py").write_text("x = 1\n")
        assert any("brand_new.py" in line for line in dirty_paths(repo))


class TestRefusesOnDirtyTree:
    def test_returns_the_refused_exit_code(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "committed.txt").write_text("uncommitted edit\n")
        clone_dir = tmp_path / "clone"
        assert (
            main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"])
            == REFUSED
        )

    def test_refuses_before_taking_any_clone(self, tmp_path: Path) -> None:
        """A clone of a dirty tree would test stale HEAD and report green."""
        repo = _make_repo(tmp_path)
        (repo / "committed.txt").write_text("uncommitted edit\n")
        clone_dir = tmp_path / "clone"
        main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"])
        assert not clone_dir.exists()


class TestCleanTreeProceeds:
    def test_dry_run_succeeds(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        assert (
            main(["--repo", str(repo), "--dest", str(tmp_path / "clone"), "--dry-run"])
            == 0
        )

    def test_dry_run_really_clones(self, tmp_path: Path) -> None:
        """Dry-run stops before install+pytest, but proves the clone works."""
        repo = _make_repo(tmp_path)
        clone_dir = tmp_path / "clone"
        main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"])
        assert (clone_dir / "committed.txt").exists()

    def test_clone_omits_gitignored_operator_data(self, tmp_path: Path) -> None:
        """The whole point: gitignored paths must be absent in the clone."""
        repo = _make_repo(tmp_path)
        (repo / ".gitignore").write_text("operator_only.json\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-q", "-m", "ignore")
        (repo / "operator_only.json").write_text("{}\n")
        clone_dir = tmp_path / "clone"
        assert main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"]) == 0
        assert not (clone_dir / "operator_only.json").exists()


class TestWiredIntoTheWorkflow:
    """Acceptance is mechanical, not prose. These pin the wiring."""

    def test_makefile_exposes_the_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text()
        assert "preflight:" in makefile
        assert "tools/clone_preflight.py" in makefile

    def test_post_branch_phase_5_names_the_gate(self) -> None:
        skill = (REPO_ROOT / ".claude/skills/post-branch/SKILL.md").read_text()
        assert "make preflight" in skill

    def test_pytest_args_still_mirror_make_test(self) -> None:
        """`PYTEST_ARGS` claims to mirror `make test`; give that an external referent.

        The docstring says the pre-flight REPLACES `make test`. Without this,
        that claim's only consumer is the sentence asserting it — the same
        self-referential shape as the handoff's retired `Line count:` stamp.
        Widen `make test` and this fails rather than the claim quietly rotting.
        """
        makefile = (REPO_ROOT / "Makefile").read_text()
        recipe = re.search(
            r"^test:\n(?:.*\n)*?\tpoetry run (pytest .*)$", makefile, re.M
        )
        assert recipe is not None, "could not locate the `test:` recipe's pytest line"
        assert recipe.group(1).split()[1:] == list(PYTEST_ARGS)
