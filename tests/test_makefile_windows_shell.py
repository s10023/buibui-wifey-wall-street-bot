"""The Makefile's Windows SHELL resolver picks Git's bash, never cmd.exe or WSL's.

From PowerShell no sh.exe is on PATH, so make with no SHELL fell back to cmd.exe
and every bash recipe died on its first `set -a`. The `ifeq ($(OS),Windows_NT)`
block at the top of the Makefile pins Git's bin/bash.exe instead, and outside Git
Bash puts Git's mingw64/bin and usr/bin first on PATH, because a line make execs
directly (preflight's bare python3) never sees the PATH bin/bash.exe builds.

These run the real Makefile with `OS=Windows_NT` against fake install dirs, so
they exercise the resolver on Linux CI too. `-n` means the fake bash.exe is
never executed; `VIRTUAL_ENV` is set so the parse-time
`$(shell)` for the worktree venv is skipped.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
MAKE = shutil.which("make")

pytestmark = pytest.mark.skipif(MAKE is None, reason="make not installed")

# `--eval` runs before the Makefile is read, so print SHELL and PATH from a
# recipe, which `-n` expands (and prints, never runs) after the resolver.
_PROBE = "probe: ; @echo RESOLVED=[$(SHELL)]|PATH=[$(PATH)]"


def _fake_bash(root: Path) -> Path:
    bash = root / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_text("", encoding="utf-8")
    for sub in ("mingw64/bin", "usr/bin"):
        (root / sub).mkdir(parents=True)
    return bash


def _resolve(
    tmp_path: Path,
    path_dirs: list[Path],
    *,
    program_files: Path | None = None,
    windows: bool = True,
    msystem: bool = False,
) -> subprocess.CompletedProcess[str]:
    nowhere = str(tmp_path / "nowhere")
    env = {
        # Windows-form PATH: the resolver splits on `;` whatever the host.
        "PATH": ";".join(str(d) for d in path_dirs),
        "ProgramW6432": str(program_files) if program_files else nowhere,
        "ProgramFiles": nowhere,
        "LOCALAPPDATA": nowhere,
        "VIRTUAL_ENV": nowhere,
    }
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    if msystem:
        env["MSYSTEM"] = "MINGW64"
    argv = [str(MAKE), "-n", "-f", str(MAKEFILE), "--eval", _PROBE]
    if windows:
        argv.append("OS=Windows_NT")
    argv.append("probe")
    return subprocess.run(
        argv,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _probe(proc: subprocess.CompletedProcess[str]) -> tuple[str, list[str]]:
    """The resolved SHELL and the PATH entries a recipe would get."""
    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if "RESOLVED=[" in ln)
    shell, _, path = line.split("RESOLVED=[", 1)[1].partition("]|PATH=[")
    return shell, path.removesuffix("]").split(";")


def _posix(entries: list[str]) -> list[str]:
    """The Makefile writes PATH entries with backslashes; compare slash-blind."""
    return [e.replace("\\", "/") for e in entries]


def _resolved_shell(proc: subprocess.CompletedProcess[str]) -> str:
    return _probe(proc)[0]


class TestWindowsShellResolver:
    def test_git_cmd_on_PATH_resolves_to_its_sibling_bash_with_the_space_intact(
        self, tmp_path: Path
    ) -> None:
        git = tmp_path / "Program Files" / "Git"
        bash = _fake_bash(git)
        (git / "cmd").mkdir()
        shell = _resolved_shell(_resolve(tmp_path, [git / "cmd"]))
        assert Path(shell) == bash

    def test_a_bare_bash_exe_on_PATH_is_never_taken(self, tmp_path: Path) -> None:
        """System32's bash.exe is WSL's; only a Git install dir counts."""
        system32 = tmp_path / "System32"
        system32.mkdir()
        (system32 / "bash.exe").write_text("", encoding="utf-8")
        program_files = tmp_path / "Program Files"
        bash = _fake_bash(program_files / "Git")
        shell = _resolved_shell(
            _resolve(tmp_path, [system32], program_files=program_files)
        )
        assert Path(shell) == bash

    def test_no_git_bash_outside_Git_Bash_fails_loudly(self, tmp_path: Path) -> None:
        proc = _resolve(tmp_path, [])
        assert proc.returncode != 0
        assert "cmd.exe" in proc.stderr

    def test_no_git_bash_inside_Git_Bash_keeps_makes_default(
        self, tmp_path: Path
    ) -> None:
        shell = _resolved_shell(_resolve(tmp_path, [], msystem=True))
        assert not shell.endswith("bash.exe")

    def test_the_block_is_inert_off_Windows(self, tmp_path: Path) -> None:
        """Positive control for the cases above: same fake Git, no OS=Windows_NT."""
        git = tmp_path / "Program Files" / "Git"
        _fake_bash(git)
        (git / "cmd").mkdir()
        shell = _resolved_shell(_resolve(tmp_path, [git / "cmd"], windows=False))
        assert not shell.endswith("bash.exe")

    def test_outside_Git_Bash_Gits_dirs_lead_PATH(self, tmp_path: Path) -> None:
        git = tmp_path / "Program Files" / "Git"
        _fake_bash(git)
        (git / "cmd").mkdir()
        _, path = _probe(_resolve(tmp_path, [git / "cmd"]))
        assert _posix(path[:3]) == [
            (git / "mingw64" / "bin").as_posix(),
            (git / "usr" / "bin").as_posix(),
            (git / "cmd").as_posix(),
        ]

    def test_inside_Git_Bash_PATH_is_left_alone(self, tmp_path: Path) -> None:
        """Git Bash already leads with those dirs; prepending would reorder its PATH."""
        git = tmp_path / "Program Files" / "Git"
        _fake_bash(git)
        (git / "cmd").mkdir()
        _, path = _probe(_resolve(tmp_path, [git / "cmd"], msystem=True))
        assert _posix(path) == [(git / "cmd").as_posix()]
