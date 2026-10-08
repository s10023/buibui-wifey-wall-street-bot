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


# --- Child Python processes (Issue #411) -------------------------------------
#
# `encoding="utf-8"` fixes how the PARENT decodes. A child Python on Windows still
# WRITES piped output as cp1252 unless its env carries PYTHONUTF8=1, so a text-mode
# call that launches Python must pass `env=python_child_env(...)` (directly, or via a
# local name assigned from it in the same function) or set `errors=`.

_HELPER = "python_child_env"

# Interpreter variables this repo passes as argv[0], by their source text.
_INTERPRETER_EXPRS = frozenset({"interpreter", "str(candidate)"})

# Text-mode sites whose argv is not a literal list, keyed by (path, function).
# Each must say why its child is never Python, or why its output is never decoded.
NON_LITERAL_ARGV_ALLOWLIST: dict[tuple[str, str], str] = {
    ("tools/post_branch_checks.py", "_run"): "every caller passes a git argv",
    ("tools/sanity_checks.py", "_run"): "every caller passes a git argv",
}


def _is_python_argv(call: ast.Call) -> bool | None:
    """True / False for a literal argv list, None when the argv is not a literal."""
    argv = (
        call.args[0]
        if call.args
        else next((kw.value for kw in call.keywords if kw.arg == "args"), None)
    )
    if not isinstance(argv, ast.List) or not argv.elts:
        return None
    head = argv.elts[0]
    if isinstance(head, ast.Attribute) and head.attr == "executable":
        return True  # sys.executable
    if isinstance(head, ast.Constant) and isinstance(head.value, str):
        if head.value.startswith("python") or head.value == "py":
            return True
        rest = [e.value for e in argv.elts[1:3] if isinstance(e, ast.Constant)]
        return head.value == "poetry" and rest == ["run", "python"]
    return ast.unparse(head) in _INTERPRETER_EXPRS


def _helper_names(func: ast.AST) -> set[str]:
    """Local names assigned from a `python_child_env(...)` call inside `func`."""
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and _is_helper_call(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _is_helper_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (
        f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
    ) == _HELPER


def _pins_child_utf8(call: ast.Call, helper_names: set[str]) -> bool:
    kws = {kw.arg: kw.value for kw in call.keywords}
    if "errors" in kws:
        return True
    env = kws.get("env")
    return _is_helper_call(env) or (
        isinstance(env, ast.Name) and env.id in helper_names
    )


def find_unpinned_child_python(
    source: str, filename: str = "<src>"
) -> list[tuple[int, str]]:
    """(line, enclosing function) of text-mode subprocess calls a child Python could break.

    A non-literal argv is reported with its function so the allowlist can name it.
    """
    tree = ast.parse(source, filename=filename)
    found = []
    funcs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    # The module scope sorts last (no lineno), so it claims only module-level calls.
    scopes: list[tuple[str, ast.AST]] = [(f.name, f) for f in funcs] + [
        ("<module>", tree)
    ]
    seen: set[int] = set()
    # Innermost function first, so a nested def owns its own calls.
    for name, scope in sorted(scopes, key=lambda s: -getattr(s[1], "lineno", 0)):
        helpers = _helper_names(scope)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call) or id(node) in seen:
                continue
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if fname not in _SUBPROCESS_FUNCS or not _is_text_mode_subprocess(node):
                continue
            seen.add(id(node))
            if _is_python_argv(node) is False or _pins_child_utf8(node, helpers):
                continue
            found.append((node.lineno, name))
    return sorted(found)


def test_child_python_writes_utf8() -> None:
    offenders = []
    used: set[tuple[str, str]] = set()
    for path in _scanned_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        for line, func in find_unpinned_child_python(source, rel):
            if (rel, func) in NON_LITERAL_ARGV_ALLOWLIST:
                used.add((rel, func))
                continue
            offenders.append(f"{rel}:{line} ({func})")

    assert not offenders, (
        "a text-mode subprocess call that launches Python (or whose argv is not a "
        "literal) neither pins the child to UTF-8 nor tolerates bad bytes. Pass "
        "`env=python_child_env(...)` from tools/child_env.py, set `errors=`, or, for a "
        "non-literal argv that never runs Python, add it to NON_LITERAL_ARGV_ALLOWLIST:\n  "
        + "\n  ".join(offenders)
    )
    stale = set(NON_LITERAL_ARGV_ALLOWLIST) - used
    assert not stale, f"allowlist entries no longer needed: {sorted(stale)}"


@pytest.mark.parametrize(
    "snippet",
    [
        "subprocess.run([sys.executable, 'x.py'], text=True, encoding='utf-8')",
        "subprocess.run(['python', 'x.py'], text=True, encoding='utf-8')",
        "subprocess.run(['poetry', 'run', 'python', 'x.py'], text=True, encoding='utf-8')",
        "subprocess.run([interpreter, 'x.py'], text=True, encoding='utf-8')",
        "subprocess.run(argv, text=True, encoding='utf-8')",  # non-literal: unknown child
        # an env= that is not built by the helper proves nothing
        "subprocess.run([sys.executable], text=True, encoding='utf-8', env=os.environ)",
    ],
)
def test_flags_unpinned_child_python(snippet: str) -> None:
    assert [line for line, _ in find_unpinned_child_python(snippet)] == [1]


@pytest.mark.parametrize(
    "snippet",
    [
        "subprocess.run(['git', 'log'], text=True, encoding='utf-8')",
        "subprocess.run([sys.executable], text=True, encoding='utf-8', env=python_child_env())",
        "subprocess.run([sys.executable], text=True, encoding='utf-8', errors='replace')",
        "subprocess.run([sys.executable], capture_output=True)",  # bytes mode
        "def f():\n    e = python_child_env()\n"
        "    subprocess.run([sys.executable], text=True, encoding='utf-8', env=e)",
    ],
)
def test_does_not_flag_pinned_or_non_python(snippet: str) -> None:
    assert find_unpinned_child_python(snippet) == []


def test_module_level_call_is_scanned_beside_a_def() -> None:
    """A file that defines a function must not hide its module-level calls."""
    snippet = (
        "def f():\n"
        "    pass\n"
        "subprocess.run([sys.executable, 'x.py'], text=True, encoding='utf-8')\n"
    )
    assert find_unpinned_child_python(snippet) == [(3, "<module>")]
