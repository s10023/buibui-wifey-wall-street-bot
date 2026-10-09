"""Print the main checkout's `.venv` when make runs from a linked git worktree.

A worktree under `.claude/worktrees/` holds tracked files only, so it has no `.venv`, and
Poetry keys its cache env on the project directory: `poetry run` there silently creates
an empty env and every gate dies on `ModuleNotFoundError` (#453). Poetry adopts an
exported `VIRTUAL_ENV` instead of its own resolution, so the Makefile exports what this
prints and every `poetry run` recipe uses the main checkout's venv unchanged.

Prints nothing, and always exits 0, whenever the fallback does not apply: not a linked
worktree (the main checkout, a CI runner, `make preflight`'s clone), the worktree has
its own `.venv` (Poetry already uses it), or the main checkout's venv has no interpreter.
Stdlib only, because it runs before any venv is chosen.
"""

from __future__ import annotations

import argparse
import subprocess  # noqa: S404 - fixed git argv, no shell
import sys
from pathlib import Path

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.venv_bootstrap import _venv_python


def main_checkout(cwd: Path) -> Path | None:
    """The main checkout's root when ``cwd`` is inside a linked worktree, else None.

    Resolved from ``--git-dir`` against ``--git-common-dir``: they differ only in a
    linked worktree, and the common dir's parent is the main checkout. Both may come
    back relative to ``cwd``, and ``--path-format=absolute`` needs git 2.31 (this host
    runs 2.28, where the flag is echoed back as output), so they are resolved here.
    """
    try:
        out = subprocess.run(  # noqa: S603, S607 - fixed argv
            ["git", "rev-parse", "--git-dir", "--git-common-dir"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return None
    if len(out) != 2:
        return None
    git_dir, common_dir = ((cwd / line).resolve() for line in out)
    if git_dir == common_dir:
        return None
    return common_dir.parent


def fallback_venv(cwd: Path) -> Path | None:
    """The venv a worktree at ``cwd`` should borrow, or None when it should not."""
    if (cwd / ".venv").exists():
        return None
    main = main_checkout(cwd)
    if main is None:
        return None
    venv = main / ".venv"
    return venv if _venv_python(venv).is_file() else None


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    venv = fallback_venv(Path.cwd())
    if venv is not None:
        print(venv.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
