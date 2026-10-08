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

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools import clone_preflight
from tools.clone_preflight import (
    FAILED,
    INFRA,
    OK,
    PYTEST_ARGS,
    REFUSED,
    clone_argv,
    dirty_paths,
    main,
    seed_venv_argv,
    subprocess_env,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _make_repo(tmp_path: Path) -> Path:
    """A real, minimal git repo — no mocks, the gate is all subprocess."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "committed.txt").write_text("committed\n", encoding="utf-8")
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
        (repo / "committed.txt").write_text("modified\n", encoding="utf-8")
        assert any("committed.txt" in line for line in dirty_paths(repo))

    def test_untracked_file_is_reported(self, tmp_path: Path) -> None:
        """An untracked new module is exactly the change a clone would miss."""
        repo = _make_repo(tmp_path)
        (repo / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
        assert any("brand_new.py" in line for line in dirty_paths(repo))


class TestRefusesOnDirtyTree:
    def test_returns_the_refused_exit_code(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "committed.txt").write_text("uncommitted edit\n", encoding="utf-8")
        clone_dir = tmp_path / "clone"
        assert (
            main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"])
            == REFUSED
        )

    def test_refuses_before_taking_any_clone(self, tmp_path: Path) -> None:
        """A clone of a dirty tree would test stale HEAD and report green."""
        repo = _make_repo(tmp_path)
        (repo / "committed.txt").write_text("uncommitted edit\n", encoding="utf-8")
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
        (repo / ".gitignore").write_text("operator_only.json\n", encoding="utf-8")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-q", "-m", "ignore")
        (repo / "operator_only.json").write_text("{}\n", encoding="utf-8")
        clone_dir = tmp_path / "clone"
        assert main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"]) == 0
        assert not (clone_dir / "operator_only.json").exists()


class TestSubprocessEnv:
    """The clone's venv must land inside the clone.

    On Windows this decides whether the gate runs at all: under the Microsoft
    Store Python, Poetry's shared cache path plus numpy's deepest test fixture
    exceeds MAX_PATH, the install dies, and preflight returns INFRA without
    ever reaching the suite. Measured on this repo 2026-09-18.
    """

    def test_in_project_virtualenv_is_forced(self) -> None:
        assert subprocess_env()["POETRY_VIRTUALENVS_IN_PROJECT"] == "1"

    def test_the_ambient_environment_is_preserved(self) -> None:
        """It must ADD to os.environ, never replace it.

        A bare dict would drop PATH, and the failure would look like "poetry is
        not installed" rather than like a preflight bug.
        """
        assert set(os.environ) <= set(subprocess_env())

    def test_both_subprocesses_receive_it(self) -> None:
        """Negative control on the half that is easy to miss.

        Forcing the layout for the INSTALL and not for the run leaves
        `poetry run` resolving a different venv than the one just populated —
        which fails as a missing dependency, i.e. as a suite FAILURE rather
        than as INFRA, and so reads as a real finding.
        """
        src = (REPO_ROOT / "tools/clone_preflight.py").read_text(encoding="utf-8")
        runs = re.findall(r"subprocess\.run\((\w+_argv)\(\), cwd=dest([^)]*)\)", src)
        assert {name for name, _ in runs} == {
            "install_argv",
            "probe_argv",
            "pytest_argv",
        }
        for name, rest in runs:
            assert "env=env" in rest, name


def _run_with_fake_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    probe_rc: int,
    pytest_rc: int,
) -> tuple[int, list[str]]:
    """Run `main` on a real clone with install/probe/pytest faked by argv."""
    repo = _make_repo(tmp_path)
    real_run = subprocess.run
    calls: list[str] = []

    def fake_run(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        if argv == clone_preflight.seed_venv_argv():
            # Not recorded: whether a seed runs depends on the interpreter the
            # suite itself runs under. TestSeedVenv covers it directly.
            return subprocess.CompletedProcess(argv, 0)
        if argv == clone_preflight.install_argv():
            calls.append("install")
            return subprocess.CompletedProcess(argv, 0)
        if argv == clone_preflight.probe_argv():
            calls.append("probe")
            return subprocess.CompletedProcess(argv, probe_rc)
        if argv == clone_preflight.pytest_argv():
            calls.append("pytest")
            return subprocess.CompletedProcess(argv, pytest_rc)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(clone_preflight.subprocess, "run", fake_run)
    rc = main(["--repo", str(repo), "--dest", str(tmp_path / "clone")])
    return rc, calls


class TestInterpreterProbe:
    """A `poetry run` that cannot start Python is INFRA, never a red suite."""

    def test_failing_probe_is_infra_and_skips_the_suite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc, calls = _run_with_fake_steps(tmp_path, monkeypatch, probe_rc=1, pytest_rc=1)
        assert rc == INFRA
        assert calls == ["install", "probe"]

    def test_passing_probe_then_failing_suite_is_still_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive control: the probe must not swallow a genuine test failure."""
        rc, calls = _run_with_fake_steps(tmp_path, monkeypatch, probe_rc=0, pytest_rc=1)
        assert rc == FAILED
        assert calls == ["install", "probe", "pytest"]

    def test_passing_probe_and_suite_is_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc, _ = _run_with_fake_steps(tmp_path, monkeypatch, probe_rc=0, pytest_rc=0)
        assert rc == OK


class TestSeedVenv:
    """Parent #880: the clone's venv is built on preflight's own interpreter.

    Poetry otherwise builds it on the python POETRY runs under, which on the
    cloud host is 3.11 against a project pinned to 3.13 (#397). The probe above
    reports that state as INFRA; the seed prevents it.
    """

    def test_inside_a_venv_it_seeds_from_this_interpreter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "prefix", "/proj/.venv")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert seed_venv_argv() == [sys.executable, "-m", "venv", ".venv"]

    def test_a_bare_system_python_seeds_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No guarantee it is the right version, so leave poetry to choose."""
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert seed_venv_argv() is None

    def test_a_failed_seed_is_infra_and_installs_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_repo(tmp_path)
        real_run = subprocess.run
        seed = ["python-for-test", "-m", "venv", ".venv"]
        calls: list[str] = []

        def fake_run(argv: list[str], *args: Any, **kwargs: Any) -> Any:
            if argv == seed:
                calls.append("seed")
                return subprocess.CompletedProcess(argv, 1)
            if argv == clone_preflight.install_argv():
                calls.append("install")
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(clone_preflight, "seed_venv_argv", lambda: seed)
        monkeypatch.setattr(clone_preflight.subprocess, "run", fake_run)
        rc = main(["--repo", str(repo), "--dest", str(tmp_path / "clone")])
        assert rc == INFRA
        assert calls == ["seed"]


class TestWiredIntoTheWorkflow:
    """Acceptance is mechanical, not prose. These pin the wiring."""

    def test_makefile_exposes_the_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        assert "preflight:" in makefile
        assert "tools/clone_preflight.py" in makefile

    def test_post_branch_phase_5_names_the_gate(self) -> None:
        skill = (REPO_ROOT / ".claude/skills/post-branch/SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "make preflight" in skill

    def test_pytest_args_still_mirror_make_test(self) -> None:
        """`PYTEST_ARGS` claims to mirror `make test`; give that an external referent.

        The docstring says the pre-flight REPLACES `make test`. Without this,
        that claim's only consumer is the sentence asserting it — the same
        self-referential shape as the handoff's retired `Line count:` stamp.
        Widen `make test` and this fails rather than the claim quietly rotting.
        """
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = re.search(
            r"^test:\n(?:.*\n)*?\tpoetry run (pytest .*)$", makefile, re.M
        )
        assert recipe is not None, "could not locate the `test:` recipe's pytest line"
        assert recipe.group(1).split()[1:] == list(PYTEST_ARGS)
