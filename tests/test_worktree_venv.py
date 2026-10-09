"""Teeth for `tools.worktree_venv`, against real `git worktree add` layouts.

The positive case (`test_worktree_borrows_the_main_checkouts_venv`) is the one that
fails if the fallback is deleted; every other case pins a None that keeps the Makefile's
export from firing where Poetry already resolves the right env.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import worktree_venv
from tools.venv_bootstrap import _venv_python
from tools.worktree_venv import fallback_venv, main_checkout


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout and one linked worktree under its `.claude/worktrees/`."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    (main / "README.md").write_text("x\n", encoding="utf-8")
    _git(main, "add", "README.md")
    _git(main, "commit", "-q", "-m", "init")
    wt = main / ".claude" / "worktrees" / "wt"
    _git(main, "worktree", "add", "-q", "-b", "wt", str(wt))
    return main.resolve(), wt.resolve()


def _make_venv(root: Path) -> Path:
    venv = root / ".venv"
    py = _venv_python(venv)
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    return venv


def test_worktree_borrows_the_main_checkouts_venv(repo: tuple[Path, Path]) -> None:
    main, wt = repo
    venv = _make_venv(main)
    assert main_checkout(wt) == main
    assert fallback_venv(wt) == venv


def test_main_checkout_is_not_a_worktree(repo: tuple[Path, Path]) -> None:
    main, _ = repo
    _make_venv(main)
    assert main_checkout(main) is None
    assert fallback_venv(main) is None


def test_worktree_with_its_own_venv_keeps_it(repo: tuple[Path, Path]) -> None:
    main, wt = repo
    _make_venv(main)
    (wt / ".venv").mkdir()
    assert fallback_venv(wt) is None


def test_main_venv_without_an_interpreter_is_not_borrowed(
    repo: tuple[Path, Path],
) -> None:
    main, wt = repo
    (main / ".venv").mkdir()
    assert fallback_venv(wt) is None


def test_outside_any_repo_is_none(tmp_path: Path) -> None:
    assert main_checkout(tmp_path) is None
    assert fallback_venv(tmp_path) is None


def test_cli_prints_a_posix_path_or_nothing(
    repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main, wt = repo
    venv = _make_venv(main)
    monkeypatch.chdir(main)
    assert worktree_venv.main([]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.chdir(wt)
    assert worktree_venv.main([]) == 0
    assert capsys.readouterr().out == venv.as_posix() + "\n"
