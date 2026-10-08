"""Every `tools/*.py` entry point survives a bare call with no `PYTHONPATH` (#436).

`python tools/<name>.py` puts `tools/` on `sys.path[0]`, not the repo root, so a tool
importing from `tools.`/`analytics.`/`utils.`/... died at import with
`ModuleNotFoundError` and exit 1. For `tools/post_branch_checks.py` and
`tools/wait_ci.py` exit 1 is also the "findings" / "CI failed" code, so the crash read
as a verdict. Each tool now carries a one-line `sys.path` bootstrap, and this file is
the guarantee, not the comment beside that line.

Two legs, because `--help` exercises module-level imports only:

- **runtime**: run every entry point's `--help` bare and require exit 0;
- **static**: any entry point importing from a repo package, at module *or* function
  level, carries a `sys.path.insert` call. `tools/session_digest.py` is the case the
  runtime leg cannot see: all its repo imports are function-level, so `--help`
  passed bare while every probe read BROKE.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tools.child_env import python_child_env

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"

#: Top-level packages a tool can import from the repo root. `tools` is one of them.
REPO_PACKAGES = frozenset({"analytics", "cli", "signals", "tools", "utils", "web"})


def _is_entry_point(tree: ast.Module) -> bool:
    """True when the module has a top-level ``if __name__ == "__main__":`` block."""
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and any(
                isinstance(c, ast.Constant) and c.value == "__main__"
                for c in test.comparators
            )
        ):
            return True
    return False


def repo_imports(tree: ast.AST) -> list[int]:
    """Line numbers of absolute imports from a repo package, at any nesting depth."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] in REPO_PACKAGES:
                lines.append(node.lineno)
        elif isinstance(node, ast.Import) and any(
            a.name.split(".")[0] in REPO_PACKAGES for a in node.names
        ):
            lines.append(node.lineno)
    return sorted(lines)


def has_path_bootstrap(tree: ast.AST) -> bool:
    """True when the module calls ``sys.path.insert(...)`` anywhere."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and ast.unparse(node.func.value) == "sys.path"
        ):
            return True
    return False


def _entry_points() -> list[Path]:
    return [
        p
        for p in sorted(TOOLS.glob("*.py"))
        if _is_entry_point(ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
    ]


ENTRY_POINTS = _entry_points()


def _run_bare(script: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run `script --help` the way a human does: no `PYTHONPATH`, no `make`."""
    return subprocess.run(  # noqa: S603 - fixed argv, shell=False
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=python_child_env(drop=("PYTHONPATH",)),
        check=False,
        timeout=120,
        encoding="utf-8",
    )


class TestDiscovery:
    def test_finds_the_known_entry_points(self) -> None:
        """Positive control: the glob is not vacuous and sees the #436 worst case."""
        names = {p.name for p in ENTRY_POINTS}
        assert len(names) >= 40, sorted(names)
        assert {"post_branch_checks.py", "wait_ci.py", "session_digest.py"} <= names
        # a library module with no __main__ is not an entry point
        assert "child_env.py" not in names

    def test_repo_imports_sees_function_level_imports(self) -> None:
        src = (
            "import os\nfrom pathlib import Path\n\ndef f():\n    from tools import x\n"
        )
        assert repo_imports(ast.parse(src)) == [5]

    def test_repo_imports_ignores_relative_and_lookalike_imports(self) -> None:
        src = "from . import x\nimport toolsy\nfrom utilsx import y\nimport pandas\n"
        assert repo_imports(ast.parse(src)) == []

    def test_has_path_bootstrap(self) -> None:
        assert has_path_bootstrap(ast.parse("import sys\nsys.path.insert(0, 'x')\n"))
        assert not has_path_bootstrap(ast.parse("xs = []\nxs.insert(0, 1)\n"))


class TestBareInvocation:
    def test_harness_detects_a_missing_bootstrap(self, tmp_path: Path) -> None:
        """Positive control for the runtime leg.

        A probe with no bootstrap, run through the same harness, must die at import.
        If it does not, `PYTHONPATH` leaked through or the repo is installed into the
        venv, and every pass below would be vacuous.
        """
        probe = tmp_path / "tools" / "probe.py"
        probe.parent.mkdir()
        probe.write_text(
            "from tools.child_env import python_child_env\n", encoding="utf-8"
        )
        proc = _run_bare(probe, cwd=REPO)
        assert proc.returncode != 0
        assert "ModuleNotFoundError" in proc.stderr

    @pytest.mark.parametrize("script", ENTRY_POINTS, ids=lambda p: p.name)
    def test_help_runs_without_pythonpath(self, script: Path) -> None:
        proc = _run_bare(script, cwd=REPO)
        assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("script", ENTRY_POINTS, ids=lambda p: p.name)
    def test_repo_imports_carry_the_bootstrap(self, script: Path) -> None:
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        lines = repo_imports(tree)
        if lines:
            assert has_path_bootstrap(tree), (
                f"{script.name} imports from the repo (lines {lines}) but never calls "
                "sys.path.insert, so a bare call dies at import (#436)"
            )
