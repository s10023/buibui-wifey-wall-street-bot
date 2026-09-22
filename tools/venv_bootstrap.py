"""Re-exec a hand-run script into the project venv instead of degrading in place.

The trap this closes, measured on this host 2026-09-22: `python tools/sanity_checks.py`
RUNS. It exits **0** and prints a report ending `0 finding(s)`, with three of its eight
legs reading `SKIPPED  (project dependencies are not installed)`. Its outcome handling is
already correct — a `SKIPPED` is not a `clean` — so nothing is mis-stated. What
correctness does not buy back is the ROUND TRIP: the operator learns which interpreter
they typed only after paying for a full run, and `SKIPPED` reads as "not applicable on
this box", which is what the same words mean on the legs that genuinely degrade.

⚠ **Scope is narrow on purpose, and the discriminator is whether the failure is LOUD.**
Measured the same day: `tools/freshness_check.py` dies on an immediate
`ModuleNotFoundError: No module named 'pandas'` traceback — you learn what you typed in
one line and there is no partial result to misread, so it does NOT earn this.
`tools/cadence_check.py`, `tools/backup_check.py`, `tools/post_branch_checks.py` and
`tools/orphan_test_audit.py` import no third-party module at all, so they cannot degrade
and their bare output is byte-identical to their `make` output. Only a script that KEEPS
GOING and renders something that looks like an answer earns this. Do not blanket-apply.

Ported from parent #742 + #760, with **two divergences, both measured rather than
assumed** — a verbatim copy is worse than no port here, in the specific direction this
repo has spent a session eradicating (a mechanism that reads clean while doing nothing):

1. **The interpreter lives in `Scripts/python.exe`, not `bin/python`.** The parent probes
   `bin/python` and returns when it is absent; on this box `.venv/bin/python` does not
   exist, so the verbatim function would be a permanent silent no-op.
2. **`os.exec*` DOES NOT WORK on this host.** Measured three ways: `os.execve` with an
   env dict **segfaults** (exit 139); `os.execv` with an absolute path exits **0** having
   run nothing the caller can see — the child is orphaned, its stdout never reaches the
   console and its exit code is lost — and it behaves the same from `cmd.exe`, so it is
   not an MSYS artifact. Windows therefore swaps via `subprocess.run` and exits with the
   child's code, which preserves both ordering and the exit status. POSIX keeps the
   parent's `os.execve` verbatim: it is correct there and costs no extra process.
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 - fixed argv, no shell
import sys
from pathlib import Path

from tools.host_platform import is_windows

SENTINEL = "WIFEY_VENV_REEXEC"


def _venv_python(venv: Path) -> Path:
    """The interpreter inside ``venv``, at the layout this platform actually uses.

    ⚠ Asked through `host_platform.is_windows()` rather than `os.name`, because a test
    that patches `os.name` also repoints `pathlib` and any `Path(...)` under that patch
    raises `NotImplementedError` — see that module's docstring for the measurement.
    """
    return venv / "Scripts" / "python.exe" if is_windows() else venv / "bin" / "python"


def _venv_bin(venv: Path) -> Path:
    """Where ``venv`` keeps its console scripts."""
    return venv / "Scripts" if is_windows() else venv / "bin"


def reexec_into_venv(root: Path, *, sentinel: str = SENTINEL) -> None:
    """Replace this process with the same argv run under ``root/.venv``.

    Returns normally — never raises, never exits — in every case where the swap cannot
    or should not happen: the sentinel is already set (a prior re-exec, so a broken venv
    cannot loop), the venv is absent (a fresh clone, a CI runner, `make preflight`'s
    clone before `poetry install`), or we are already inside it. The caller then behaves
    exactly as it did before this function existed, which is what keeps it safe to call
    unconditionally at import time.

    ⚠ When the swap DOES fire it does not return: POSIX replaces the process, and
    Windows runs the child and raises `SystemExit` carrying its exit code.

    ⚠ The "already inside it" test is ``sys.prefix``, NOT ``sys.executable``. On POSIX
    ``.venv/bin/python`` is a symlink to the system interpreter, so comparing resolved
    executables reports the venv and a bare `python3` as the SAME path and the swap
    never fires. ``sys.prefix`` is the venv root inside a venv and the base install
    outside one, which is the distinction actually being asked about.

    ⚠ The swap carries ``PATH`` as well as the interpreter — see `_venv_first_path`.
    """
    if os.environ.get(sentinel) == "1":
        return
    venv = root / ".venv"
    python = _venv_python(venv)
    if not python.exists():
        return
    if Path(sys.prefix).resolve() == venv.resolve():
        return
    # stderr, not stdout: a caller may capture stdout as the report itself, so a note
    # about the interpreter must not spend a report line.
    print(f"note: re-exec into {python} (was {sys.executable})", file=sys.stderr)
    argv = [str(python), *sys.argv]
    env = {**os.environ, sentinel: "1", "PATH": _venv_first_path(venv)}
    if is_windows():
        raise SystemExit(subprocess.run(argv, env=env).returncode)  # noqa: S603
    os.execve(str(python), argv, env)


def _venv_first_path(venv: Path) -> str:
    """``PATH`` with the venv's script directory PREPENDED, so its console scripts resolve.

    Swapping the interpreter is not enough. A subprocess resolved BY NAME is resolved by
    the SHELL, through ``PATH``, which the swap inherits unchanged — so on the parent a
    correctly re-exec'd script still reported ``FileNotFoundError: 'yt-dlp'`` while
    `.venv/bin/yt-dlp` sat right there (#760). Each fix covers the resolver it names and
    nothing below it: ``sys.path`` for repo imports, the interpreter for third-party
    imports, ``PATH`` for subprocesses.

    ⚠ **Carried, not measured here.** wifey's one bootstrapped script shells out only to
    ``git``, a system binary, so this half has no current consumer in this repo. It ships
    anyway because the two halves are one resolver chain, and landing the interpreter
    swap alone would re-open a documented defect one level down the moment a bootstrapped
    script grows its first name-resolved call — `yt-dlp` being the obvious candidate,
    pinned to a nightly here exactly as upstream.

    PREPENDED rather than appended, because a pin is the point: `yt-dlp` is held at a
    dated NIGHTLY (stable 403s on every media URL), so a stale system copy earlier on
    ``PATH`` would shadow it and a canary would probe a version the pipeline never runs —
    a false verdict, which is worse than the missing one this replaces.
    """
    bin_dir = str(_venv_bin(venv))
    current = os.environ.get("PATH", "")
    return f"{bin_dir}{os.pathsep}{current}" if current else bin_dir
