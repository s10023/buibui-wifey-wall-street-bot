"""Every Python script a Makefile recipe invokes must exist and hold real code.

This is the static half of the silent-surface enforcement; the data-driven half is
`tools/dead_surface_check.py` (+ `tests/test_dead_surface_check.py`), which reports
declared (strategy × timeframe) cells whose detector never fires.

`make wifey-open-trades` ran `trade/open_trades.py` — a **0-byte file** — printed
"🚀 Opening multiple trades…" and exited **0** until 2026-08-06 (#138). Python
exits 0 on an empty file, so a target can invoke nothing, report success, and be
indistinguishable from a target that worked. `make` cannot catch this: the
command genuinely succeeded.

Scope note: a path merely *named* in an `@echo` string is not an invocation.
`wifey-open-trades` still mentions `trade/open_trades.py` in its failure message,
which is correct — it explains why it refuses to run. Only paths passed to a
Python interpreter are checked.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"

# `python foo.py`, `poetry run python foo.py`, `python3 -m x foo.py`, with or
# without a leading `@` / `-` recipe prefix. Captures the first .py argument.
_INVOCATION_RE = re.compile(r"python[0-9.]*\s+(?:-\w+\s+)*([\w./-]+\.py)\b")

# A file that parses but does nothing is the exact #138 failure. Nothing in this
# repo's real entry points is under this size; the 0-byte placeholder is 0.
_MIN_SCRIPT_BYTES = 50


def _recipe_lines(makefile_text: str) -> list[str]:
    """Recipe lines only (tab-indented), with line continuations joined."""
    joined = makefile_text.replace("\\\n", " ")
    return [ln for ln in joined.splitlines() if ln.startswith("\t")]


def invoked_scripts(makefile_text: str) -> list[str]:
    """Python scripts passed to an interpreter in any recipe, deduped, in order."""
    found: list[str] = []
    for line in _recipe_lines(makefile_text):
        # Strip echo'd text so a path merely named in a message isn't counted.
        if re.match(r"\s*[@-]*echo\b", line):
            continue
        for match in _INVOCATION_RE.finditer(line):
            path = match.group(1)
            if path not in found:
                found.append(path)
    return found


class TestMakefileInvocations:
    def test_finds_the_real_entry_points(self) -> None:
        scripts = invoked_scripts(MAKEFILE.read_text())
        # Sanity-check the parser itself: if this regressed to matching nothing,
        # every assertion below would vacuously pass.
        assert "wifey.py" in scripts
        assert len(scripts) >= 5

    def test_every_invoked_script_exists(self) -> None:
        missing = [
            s
            for s in invoked_scripts(MAKEFILE.read_text())
            if not (REPO_ROOT / s).is_file()
        ]
        assert missing == [], f"Makefile invokes non-existent script(s): {missing}"

    def test_no_invoked_script_is_an_empty_placeholder(self) -> None:
        """The #138 case: python exits 0 on an empty file, so this looks like success."""
        empty = [
            f"{s} ({(REPO_ROOT / s).stat().st_size}B)"
            for s in invoked_scripts(MAKEFILE.read_text())
            if (REPO_ROOT / s).is_file()
            and (REPO_ROOT / s).stat().st_size < _MIN_SCRIPT_BYTES
        ]
        assert empty == [], (
            "Makefile invokes empty/near-empty script(s), which exit 0 and report "
            f"success without doing anything: {empty}"
        )


class TestInvokedScriptsParser:
    """The parser is the load-bearing part — a silent parse failure hides everything."""

    def test_ignores_paths_named_only_in_echo(self) -> None:
        text = '\t@echo "trade/open_trades.py is a 0-byte placeholder"\n'
        assert invoked_scripts(text) == []

    def test_matches_poetry_run_python(self) -> None:
        assert invoked_scripts("\t@poetry run python wifey.py backtest\n") == [
            "wifey.py"
        ]

    def test_matches_across_a_line_continuation(self) -> None:
        text = "\t@poetry run python \\\n\t\ttools/pundit_score.py --json\n"
        assert invoked_scripts(text) == ["tools/pundit_score.py"]

    def test_dedupes_repeated_invocations(self) -> None:
        text = "\t@python wifey.py a\n\t@python wifey.py b\n"
        assert invoked_scripts(text) == ["wifey.py"]

    def test_ignores_non_recipe_lines(self) -> None:
        # A variable assignment at column 0 is not a recipe.
        assert invoked_scripts("SCRIPT = python not_a_recipe.py\n") == []
