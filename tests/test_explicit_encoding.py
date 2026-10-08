"""Every text-mode file read or write in this repo's code must name its encoding.

Ported from the parent's `tests/test_explicit_encoding.py` (parent #792; Issue #403).

Windows defaults text I/O to the ANSI codepage (cp1252 on the operator's host), so a
bare `read_text()` over a file holding CJK or `⚠` raises `UnicodeDecodeError`.

⚠ **`make test` cannot see this class, by design.** `Makefile` exports
`PYTHONUTF8=1`, and UTF-8 mode also flips the default FILE encoding -- so every bare
call passes under `make` and fails under a bare `pytest`, `python tools/<x>.py` or a
hook, none of which carry the export. Hence a STATIC gate: it reads the source, so
no environment variable can mask it.

⚠ **Name-based on purpose.** ruff's `PLW1514` infers the receiver's type and so
misses any `Path` reached through an attribute. This scan flags every `read_text` /
`write_text` / text-mode `open` call by NAME, and a false positive costs one
`encoding=` argument.

Subprocess calls are in scope too (Issue #408): `subprocess.run(..., text=True)` (or
`universal_newlines=True`) without `encoding=` decodes the child's output as cp1252
in a reader thread, `stdout` comes back `None`, and the failure surfaces later as
`'NoneType' object has no attribute 'splitlines'`. Matched by function name
(`run`, `check_output`, `Popen`, `call`, `check_call`) and a text-mode keyword that
is `True` or not a literal.

`tests/` is IN scope. A tmp-file round trip looks symmetric and is not: cp1252
cannot ENCODE CJK, so `write_text(VTT)` dies at the write.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# `.claude/hooks` is in because hooks run under a bare venv interpreter with no
# PYTHONUTF8 -- the unmasked path this gate exists for.
SCANNED_DIRS = (
    "tests",
    "analytics",
    "cli",
    "migrations",
    "scripts",
    "signals",
    "tools",
    "trade",
    "utils",
    "web",
    ".claude/hooks",
)
SCANNED_FILES = ("wifey.py",)

# Methods that take `encoding=` and default it from the locale.
_TEXT_METHODS = frozenset({"read_text", "write_text"})

# `<receiver>.open(...)` receivers that are not file opens in text mode.
_NON_FILE_OPEN_RECEIVERS = frozenset(
    {"webbrowser", "Image", "tarfile", "zipfile", "os"}
)

# Subprocess entry points that take `text=` / `encoding=`.
_SUBPROCESS_FUNCS = frozenset({"run", "check_output", "Popen", "call", "check_call"})
_TEXT_MODE_KWARGS = ("text", "universal_newlines")

# A site that carried CJK-capable text with a bare read, kept so the scope test below
# stays anchored to a real file.
KNOWN_SITE = Path("tools/video_fetch.py")


def _mode_literal(call: ast.Call, positional_index: int) -> str | None:
    """The mode string when it is a literal, else None."""
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    if len(call.args) > positional_index:
        arg = call.args[positional_index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _is_text_mode_subprocess(call: ast.Call) -> bool:
    """True when a text-mode keyword is literally True or a non-literal expression."""
    return any(
        kw.arg in _TEXT_MODE_KWARGS
        and (not isinstance(kw.value, ast.Constant) or kw.value.value is True)
        for kw in call.keywords
    )


def _needs_encoding(call: ast.Call) -> bool:
    """True when `call` is a text-mode file open that leaves encoding to the locale."""
    keywords = {kw.arg for kw in call.keywords}
    if "encoding" in keywords or None in keywords:  # None = **kwargs, cannot tell
        return False

    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _TEXT_METHODS:
        return True

    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in _SUBPROCESS_FUNCS:
        return _is_text_mode_subprocess(call)

    if isinstance(func, ast.Name) and func.id == "open":
        mode_index = 1  # open(file, mode, ...)
    elif isinstance(func, ast.Attribute) and func.attr == "open":
        receiver = func.value
        if isinstance(receiver, ast.Name) and receiver.id in _NON_FILE_OPEN_RECEIVERS:
            return False
        mode_index = 0  # Path.open(mode, ...)
    else:
        return False

    mode = _mode_literal(call, mode_index)
    return mode is None or "b" not in mode


def find_bare_calls(source: str, filename: str = "<src>") -> list[int]:
    """Line numbers of every text-mode file call in `source` lacking `encoding=`."""
    tree = ast.parse(source, filename=filename)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _needs_encoding(node)
    )


def _scanned_files() -> list[Path]:
    files = [REPO_ROOT / name for name in SCANNED_FILES]
    for directory in SCANNED_DIRS:
        files.extend(sorted((REPO_ROOT / directory).rglob("*.py")))
    return files


def test_shipped_code_names_every_text_encoding() -> None:
    offenders = []
    for path in _scanned_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for line in find_bare_calls(path.read_text(encoding="utf-8"), rel):
            offenders.append(f"{rel}:{line}")

    assert not offenders, (
        "text-mode file I/O without `encoding=` -- Windows decodes it as cp1252, and "
        '`make test` hides that because PYTHONUTF8=1. Add `encoding="utf-8"`:\n  '
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "cfg.ledger_path.read_text()",  # receiver type is unknowable statically
        "p.write_text(data)",
        "open(p)",
        "open(p, 'w')",
        "p.open()",
        "p.open('a')",
        "subprocess.run(['git'], capture_output=True, text=True)",
        "subprocess.check_output(cmd, universal_newlines=True)",
        "subprocess.Popen(cmd, text=flag)",  # non-literal: may be text mode
        "run(cmd, text=True)",
    ],
)
def test_flags_bare_calls(snippet: str) -> None:
    """Teeth: each of these defaults to the locale encoding."""
    assert find_bare_calls(snippet) == [1]


@pytest.mark.parametrize(
    "snippet",
    [
        "p.read_text(encoding='utf-8')",
        "p.write_text(data, encoding='utf-8')",
        "open(p, encoding='utf-8')",
        "open(p, 'rb')",
        "open(p, mode='wb')",
        "p.open('rb')",
        "p.read_bytes()",
        "webbrowser.open(url)",
        "p.read_text(**kw)",
        "subprocess.run(cmd, text=True, encoding='utf-8')",
        "subprocess.run(cmd, capture_output=True)",  # bytes mode
        "subprocess.run(cmd, text=False)",
        "subprocess.run(cmd, **kw)",
    ],
)
def test_does_not_flag_explicit_or_binary(snippet: str) -> None:
    """Specificity: a gate that flags everything cannot tell clean from blind."""
    assert find_bare_calls(snippet) == []


def test_known_site_is_in_scope() -> None:
    """The anchor file must stay inside the scanned set, or the gate is blind to it."""
    assert REPO_ROOT / KNOWN_SITE in _scanned_files()
