r"""The shared Claude-project-path derivation.

Five call sites had their own copy of this rule and all five were wrong on
Windows; two of them carried the old Linux box's absolute home as a tracked
literal. The class is worth a dedicated test file because of HOW it failed:
never by raising. Every consumer treats an unresolvable memory tree as "absent,
skip with a warning", so a wrong derivation reads as *nothing to back up* and
*nothing to check*, and a green suite says so too.

⚠ **The platform rules are pinned as TEXT, both of them, on every host.**
`slugify_path` takes a `str` rather than a `Path` precisely so that Linux CI
asserts the Windows rule and a Windows box asserts the POSIX one. Had it taken
a `Path`, exactly one of the two assertions would be unwritable on any given
host — and the one you cannot write is the one that breaks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.claude_home import (
    claude_home,
    memory_dir,
    project_dir,
    project_slug,
    slugify_path,
)


class TestSlugifyPath:
    """The rule itself, as text, independent of the running host."""

    def test_posix_absolute_path(self) -> None:
        assert (
            slugify_path("/home/kng/repo/buibui-wifey-wall-street-bot")
            == "-home-kng-repo-buibui-wifey-wall-street-bot"
        )

    def test_windows_absolute_path(self) -> None:
        """The drive colon folds too, which is why `C:` becomes `C-`.

        A rule folding separators alone leaves `C:\\Users\\...` untouched and
        yields a "slug" that is still a drive-absolute path — which then joins
        onto `projects/` as a second root and silently discards it. That was
        the live defect, and it is the reason this asserts the doubled `-`.
        """
        assert (
            slugify_path(r"C:\Users\User\repo\buibui-wifey-wall-street-bot")
            == "C--Users-User-repo-buibui-wifey-wall-street-bot"
        )

    def test_the_doubled_separator_is_the_drive_colon(self) -> None:
        """A negative control for the assertion above.

        `C--Users` could be read as an accident of some other rule, so pin that
        it is exactly the colon and the following separator, and that a path
        with no drive letter does not acquire one.
        """
        assert slugify_path("C:") == "C-"
        assert slugify_path(r"\Users\User") == "-Users-User"

    def test_mixed_separators_fold_the_same_way(self) -> None:
        """Windows accepts both, and `Path` normalises inconsistently enough
        that a real `resolve()` can hand back either."""
        assert slugify_path("C:/Users/User") == slugify_path(r"C:\Users\User")

    def test_a_relative_path_is_not_special_cased(self) -> None:
        """Documents the boundary: the rule is total over text. Callers that
        need an absolute path resolve first — that is `project_slug`'s job, and
        keeping it out of here is what makes this function testable."""
        assert slugify_path("repo/x") == "repo-x"


class TestClaudeHome:
    """Config-root discovery: explicit override, then existence, then default."""

    def test_env_override_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert claude_home() == tmp_path / "elsewhere"

    def test_env_override_wins_even_when_a_candidate_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ordering control: existence must not outrank an explicit setting."""
        (tmp_path / ".claude").mkdir()
        _set_home(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert claude_home() == tmp_path / "elsewhere"

    def test_personal_profile_wins_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`.claude-personal` is the work-machine profile and is more specific,
        so a box carrying both resolves to it."""
        (tmp_path / ".claude-personal").mkdir()
        (tmp_path / ".claude").mkdir()
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert claude_home() == tmp_path / ".claude-personal"

    def test_plain_claude_when_it_is_the_only_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".claude").mkdir()
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert claude_home() == tmp_path / ".claude"

    def test_absent_roots_fall_back_rather_than_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deliberate: every consumer degrades to a printed note on an absent
        tree, so raising here would convert an advisory check into a blocking
        one — and would break the backup rather than the report."""
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert claude_home() == tmp_path / ".claude"

    def test_a_candidate_that_is_a_FILE_does_not_win(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`~/.claude.json` sits beside `~/.claude` on a real box, so the probe
        has to test for a directory rather than for existence."""
        (tmp_path / ".claude-personal").write_text("not a dir\n", encoding="utf-8")
        (tmp_path / ".claude").mkdir()
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert claude_home() == tmp_path / ".claude"


class TestDerivedPaths:
    """`project_dir` and `memory_dir` compose the two rules above."""

    def test_memory_dir_sits_under_the_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_home(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        repo = tmp_path / "repo" / "demo"
        repo.mkdir(parents=True)
        assert memory_dir(repo) == project_dir(repo) / "memory"
        assert project_dir(repo).parent == tmp_path / ".claude" / "projects"
        assert project_dir(repo).name == project_slug(repo)

    def test_project_slug_resolves_before_folding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A relative path must not produce a relative slug — the whole point
        of the derivation is that two checkouts at different paths get
        different project directories."""
        repo = tmp_path / "repo" / "demo"
        repo.mkdir(parents=True)
        monkeypatch.chdir(repo)
        assert project_slug(Path(".")) == project_slug(repo)
        assert project_slug(Path(".")) == slugify_path(str(repo.resolve()))


def _set_home(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `Path.home()` at `path` on either host.

    POSIX reads HOME; Windows reads USERPROFILE and ignores HOME entirely. A
    helper exists so no test here can set one and quietly leak the real home
    into an assertion on the other platform.
    """
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
