r"""Where this checkout's Claude Code project directory lives.

Five call sites derived this path independently -- ``cadence_check``,
``post_branch_checks``, ``sync_parent``, ``deploy/backup-analytics.sh`` and the
Makefile -- and every one of them was wrong on Windows. Two carried the old
Linux box's absolute path as a tracked literal, so no environment variable
could rescue them; the other three applied a separator rule that is correct
only on POSIX.

⚠ **Each one fails SILENTLY, in the direction of absence.** The backup script
records ``files: 0`` for an absent external root rather than failing, the
cadence checker degrades to a printed note, and ``post_branch_checks`` reports
the memory cap against a file it never found. So the whole class reads as
"nothing to do" on a host where the tree is present and merely unlocated --
which is what let it survive a migration whose brief already named the slug
remapping as a restore step.

Deduping rather than correcting each site is deliberate, and is the same
reasoning as ``cost_model``'s shared bars-per-day table: fixing the values in
place would have hidden a missing key as well as the defect being fixed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Path separators and the Windows drive colon, all of which Claude Code folds
#: to a single ``-`` when it names a project directory.
_SEPARATORS = re.compile(r"[/\\:]")

#: Config-root candidates, most specific first. ``.claude-personal`` is a
#: second profile the operator used on a shared work machine to keep personal
#: projects out of the work account's config; a personal box has only
#: ``.claude``. Probing rather than hardcoding is what lets one tracked literal
#: serve both, and ``CLAUDE_CONFIG_DIR`` overrides both because that is the
#: variable Claude Code itself honours.
_CONFIG_DIR_NAMES = (".claude-personal", ".claude")


def slugify_path(text: str) -> str:
    r"""Fold an absolute path into Claude Code's project-directory name.

    Pure and platform-independent on purpose: it takes the path as TEXT rather
    than a ``Path``, so the Windows rule is testable from Linux CI and the
    POSIX rule from a Windows box. A ``Path``-typed argument would resolve
    against the running platform and make exactly one of those two assertions
    unwritable -- which is the shape that let this defect ship.

        ``/home/kng/repo/x``       -> ``-home-kng-repo-x``
        ``C:\Users\User\repo\x``   -> ``C--Users-User-repo-x``
    """
    return _SEPARATORS.sub("-", text)


def project_slug(repo_root: Path) -> str:
    """``slugify_path`` over a checkout's resolved absolute path."""
    return slugify_path(str(repo_root.resolve()))


def claude_home() -> Path:
    """The config root holding ``projects/``.

    ``CLAUDE_CONFIG_DIR`` wins when set. Otherwise the first candidate that
    exists wins, and ``.claude`` is the fallback when none does -- never an
    error, because every consumer here already treats an absent tree as a
    printed note rather than a failure, and raising would convert an advisory
    check into one that blocks.
    """
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env)
    home = Path.home()
    for name in _CONFIG_DIR_NAMES:
        if (home / name).is_dir():
            return home / name
    return home / _CONFIG_DIR_NAMES[-1]


def project_dir(repo_root: Path) -> Path:
    """This checkout's Claude Code project directory."""
    return claude_home() / "projects" / project_slug(repo_root)


def memory_dir(repo_root: Path) -> Path:
    """This checkout's memory tree -- the SoT to-do's home."""
    return project_dir(repo_root) / "memory"
