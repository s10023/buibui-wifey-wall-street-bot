"""Teeth for `tools.venv_bootstrap.reexec_into_venv`.

The interesting cases are the POSITIVE ones — delete the swap and
`test_swaps_interpreter_when_outside_the_venv` (POSIX) and
`test_windows_swaps_via_subprocess_and_exits_with_the_child_code` are the only tests
that fail. The no-op tests exist because this function is called unconditionally at
import time by a script that must keep working on a clone with no venv at all.

Both platform branches are exercised on either host, by patching
`venv_bootstrap.is_windows` rather than `os.name` — patching `os.name` repoints
`pathlib` and every `Path(...)` under it raises, taking pytest's own failure reporting
down with it (`tools/host_platform.py` carries that measurement). A seam a test can
patch is the only way to reach the branch this box does not take, which is the whole
reason that module exists.

The two reachability cases assert through `shutil.which`, never the shape of the
PATH string. Asserting that `sys.prefix` moved — or that `PATH` merely contains the
venv — is what let the upstream defect ship: the interpreter swapped correctly and a
subprocess resolved by name still died `FileNotFoundError`, because that resolution is
the shell's, not Python's. A test that cannot tell those two apart is the blind spot,
restated. Those two run on the NATIVE branch, because `shutil.which` asks the real
platform; the prepend/extend SHAPE cases cover both branches directly instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.venv_bootstrap import SENTINEL, _venv_first_path, reexec_into_venv

_NATIVE_WINDOWS = os.name == "nt"


@pytest.fixture(autouse=True)
def _clear_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SENTINEL, raising=False)


def _force_platform(monkeypatch: pytest.MonkeyPatch, *, windows: bool) -> None:
    monkeypatch.setattr("tools.venv_bootstrap.is_windows", lambda: windows)


def _fake_venv(root: Path, *, windows: bool = False) -> Path:
    python = (
        root / ".venv" / "Scripts" / "python.exe"
        if windows
        else root / ".venv" / "bin" / "python"
    )
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    return python


def _record_execve(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr("os.execve", lambda *args: calls.append(args))
    return calls


def _record_subprocess(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, returncode)

    monkeypatch.setattr("subprocess.run", _fake_run)
    return calls


def _bin_name() -> str:
    return "Scripts" if _NATIVE_WINDOWS else "bin"


def _exe(name: str) -> str:
    """`shutil.which` asks the real platform, and on Windows that means PATHEXT."""
    return f"{name}.exe" if _NATIVE_WINDOWS else name


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _which(name: str, env: dict[str, str]) -> Path | None:
    """`shutil.which` against the swapped PATH, as a `Path`.

    Compared as a path, never as a string: on Windows `which` returns the name with
    the PATHEXT case it matched (`yt-dlp.EXE`), so a string comparison against the file
    the test created fails for a reason that has nothing to do with reachability.
    `PureWindowsPath` equality is case-insensitive and POSIX stays exact, so this asks
    the question each platform actually means.
    """
    found = shutil.which(name, path=env["PATH"])
    return Path(found) if found else None


def _capture_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Fire the swap on whichever branch is native and hand back its exec environment."""
    if _NATIVE_WINDOWS:
        calls = _record_subprocess(monkeypatch)
        with pytest.raises(SystemExit):
            reexec_into_venv(tmp_path)
        windows_env: dict[str, str] = calls[0]["env"]
        return windows_env
    execve = _record_execve(monkeypatch)
    reexec_into_venv(tmp_path)
    posix_env: dict[str, str] = execve[0][2]
    return posix_env


class TestPosixBranch:
    def test_swaps_interpreter_when_outside_the_venv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_platform(monkeypatch, windows=False)
        python = _fake_venv(tmp_path)
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "argv", ["tools/sanity_checks.py", "--json"])
        calls = _record_execve(monkeypatch)

        reexec_into_venv(tmp_path)

        assert len(calls) == 1, "the interpreter swap did not fire"
        path, argv, env = calls[0]
        assert path == str(python)
        # argv[0] is the interpreter; the script's own argv rides behind it unchanged,
        # so flags survive the swap — one that dropped --json would print the wrong form.
        assert argv == [str(python), "tools/sanity_checks.py", "--json"]
        assert env[SENTINEL] == "1", (
            "without the sentinel a broken venv re-execs forever"
        )

    def test_no_swap_when_already_inside_the_venv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_platform(monkeypatch, windows=False)
        _fake_venv(tmp_path)
        monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
        calls = _record_execve(monkeypatch)

        reexec_into_venv(tmp_path)

        assert calls == []

    def test_no_swap_when_the_venv_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh clone, a CI runner and `make preflight`'s clone all look like this."""
        _force_platform(monkeypatch, windows=False)
        monkeypatch.setattr(sys, "prefix", "/usr")
        calls = _record_execve(monkeypatch)

        reexec_into_venv(tmp_path)

        assert calls == []

    def test_no_swap_once_the_sentinel_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_platform(monkeypatch, windows=False)
        _fake_venv(tmp_path)
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setenv(SENTINEL, "1")
        calls = _record_execve(monkeypatch)

        reexec_into_venv(tmp_path)

        assert calls == []

    @pytest.mark.skipif(
        _NATIVE_WINDOWS, reason="a Windows venv COPIES python.exe; it never symlinks it"
    )
    def test_symlinked_venv_python_still_swaps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect the `sys.prefix` test exists for.

        `.venv/bin/python` is a symlink to the system interpreter, so a check comparing
        RESOLVED executables sees one path and never fires. Point the fake venv's python
        at the real `sys.executable` and the swap must still happen.
        """
        _force_platform(monkeypatch, windows=False)
        python = tmp_path / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        monkeypatch.setattr(sys, "prefix", "/usr")
        calls = _record_execve(monkeypatch)

        reexec_into_venv(tmp_path)

        assert len(calls) == 1


class TestWindowsBranch:
    def test_probes_scripts_python_not_bin_python(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Divergence 1, and the reason a verbatim port would be a silent no-op.

        Upstream probes `.venv/bin/python` and returns when it is absent. That path does
        not exist in a Windows venv, so the verbatim function would decline to swap
        forever — reading exactly like a healthy no-op, which is the failure class this
        repo removed four instances of on 2026-09-21.
        """
        _force_platform(monkeypatch, windows=True)
        _fake_venv(tmp_path, windows=False)  # only the POSIX layout exists
        monkeypatch.setattr(sys, "prefix", r"C:\Python313")
        calls = _record_subprocess(monkeypatch)

        reexec_into_venv(tmp_path)

        assert calls == [], "bin/python must not satisfy the Windows probe"

        _fake_venv(tmp_path, windows=True)
        with pytest.raises(SystemExit):
            reexec_into_venv(tmp_path)

        assert len(calls) == 1, "Scripts/python.exe is the layout that must fire"

    def test_swaps_via_subprocess_and_exits_with_the_child_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Divergence 2, measured 2026-09-22 rather than assumed.

        `os.execve` with an env dict segfaults on this host (exit 139), and `os.execv`
        with an absolute path exits **0** having run nothing the caller can see: the
        child is orphaned, its stdout never reaches the console and its exit code is
        lost. Same from `cmd.exe`, so it is not an MSYS artifact. A swap that returns 0
        while discarding the report is strictly worse than no swap, so Windows runs the
        child and carries its exit code out.
        """
        _force_platform(monkeypatch, windows=True)
        python = _fake_venv(tmp_path, windows=True)
        monkeypatch.setattr(sys, "prefix", r"C:\Python313")
        monkeypatch.setattr(sys, "argv", ["tools/sanity_checks.py", "--json"])
        execve = _record_execve(monkeypatch)
        calls = _record_subprocess(monkeypatch, returncode=3)

        with pytest.raises(SystemExit) as excinfo:
            reexec_into_venv(tmp_path)

        assert excinfo.value.code == 3, "the child's exit code must survive the swap"
        assert calls[0]["argv"] == [str(python), "tools/sanity_checks.py", "--json"]
        assert calls[0]["env"][SENTINEL] == "1"
        assert execve == [], "os.exec* orphans the child here; it must not be reached"

    def test_returns_quietly_when_the_venv_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_platform(monkeypatch, windows=True)
        monkeypatch.setattr(sys, "prefix", r"C:\Python313")
        calls = _record_subprocess(monkeypatch)

        reexec_into_venv(tmp_path)

        assert calls == []


class TestPathCarriedThroughTheSwap:
    def test_a_path_resolved_binary_is_reachable_after_the_swap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The swap must carry PATH, not just the interpreter.

        A command built as `("yt-dlp", ...)` is resolved by the SHELL through PATH, so a
        re-exec moving only `sys.executable` leaves a venv-installed binary unreachable.
        `which` is the assertion because it is the same question the shell asks — and it
        asks it of the REAL platform, which is why this runs on the native branch.
        """
        _force_platform(monkeypatch, windows=_NATIVE_WINDOWS)
        _fake_venv(tmp_path, windows=_NATIVE_WINDOWS)
        installed = _executable(tmp_path / ".venv" / _bin_name() / _exe("yt-dlp"))
        monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "elsewhere"))

        env = _capture_env(tmp_path, monkeypatch)

        assert _which("yt-dlp", env) == installed

    def test_the_venv_wins_over_a_system_copy_of_the_same_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PREPENDED, not appended — the pin is the point.

        `yt-dlp` is held at a dated nightly because stable 403s on every media URL, so a
        stale system copy that shadowed the venv's would have a canary probe a version
        the pipeline never runs. Appending resolves the FileNotFoundError and still gets
        the wrong answer, which no reachability-only assertion can see.
        """
        _force_platform(monkeypatch, windows=_NATIVE_WINDOWS)
        _fake_venv(tmp_path, windows=_NATIVE_WINDOWS)
        installed = _executable(tmp_path / ".venv" / _bin_name() / _exe("yt-dlp"))
        decoy_dir = tmp_path / "system-bin"
        _executable(decoy_dir / _exe("yt-dlp"))
        monkeypatch.setenv("PATH", str(decoy_dir))
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "elsewhere"))

        env = _capture_env(tmp_path, monkeypatch)

        assert _which("yt-dlp", env) == installed

    @pytest.mark.parametrize("windows", [False, True])
    def test_the_inherited_path_survives_the_prepend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool
    ) -> None:
        """A venv holds console scripts, not `git`, `ffmpeg` or `node` — replacing PATH
        rather than extending it would trade one FileNotFoundError for several."""
        _force_platform(monkeypatch, windows=windows)
        monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/bin")
        leaf = "Scripts" if windows else "bin"

        assert _venv_first_path(tmp_path / ".venv").split(os.pathsep) == [
            str(tmp_path / ".venv" / leaf),
            "/usr/bin",
            "/bin",
        ]

    @pytest.mark.parametrize("windows", [False, True])
    def test_an_empty_path_yields_the_venv_bin_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool
    ) -> None:
        """An empty entry in PATH means the CWD to most shells, so a bare join would make
        the re-exec'd process resolve binaries out of whatever directory it started in."""
        _force_platform(monkeypatch, windows=windows)
        monkeypatch.delenv("PATH", raising=False)
        leaf = "Scripts" if windows else "bin"

        assert _venv_first_path(tmp_path / ".venv") == str(tmp_path / ".venv" / leaf)
