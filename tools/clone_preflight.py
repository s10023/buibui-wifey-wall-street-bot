"""Run the test suite against a fresh clone, before `gh pr create`.

A gitignored path that exists on the dev box and nowhere else is invisible to
every local check. Two defects share that one symptom: **(a)** a test depends on
a local file, so CI reds; and **(b)** *production* code loads a local file it
does not need, so the CLI is broken on a clean clone while a hermetic test
passes anyway. A rule aimed only at (a) — "pass every path explicitly" — makes
(b) invisible, which is why this is a mechanism rather than another line of prose.

wifey has already paid for this class once, in the other direction: `.claude/`
was an allowlist until 2026-08-20, so the hooks were untracked and silently did
not survive a reclone while every local run had them. Absence on a clean clone
is the thing no local check can see.

**CI already is this gate**, being a clean checkout. What this closes is TIMING,
not detection: on a private repo, detection after a push costs a metered Actions
cycle, a red PR, and a visibility flip to read the failure at all. So this runs
locally, and at roughly the cost of `make test` it REPLACES that branch's
`make test` rather than adding to it.

Why a clone and not a cheaper trick. The first two were measured in the parent
repo (`buibui-moon-trader-bot`, 2026-08-20) and the reasoning is structural, so
it ports; the third was re-measured here:

* **A foreign working directory was REFUTED.** Every default is a relative path,
  so a foreign cwd makes them all absent at once — including every *committed*
  asset. Upstream measured 27 failures of 4192, and almost none were the bug.
* **Monkeypatching the `DEFAULT_*` constants is PARTIAL by construction.**
  ``DEFAULT_DB_PATH`` is re-exported into two modules that captured the value at
  import, so patching one attribute leaves two live and the guard reports covered.
  That re-export shape is wifey's too — `analytics.store` and
  `analytics.data_store` both surface it.
* **A clone has no false positives BY CONSTRUCTION** — every committed file is
  present and every gitignored one is absent.

⚠ **Two holes, stated because a gate whose reach is unknown gets over-trusted.**
A clone does NOT catch an *absolute* default (``$HOME/...``), because ``$HOME``
is identical in the clone — ``EXTERNAL_ROOTS`` in ``deploy/backup-analytics.sh``
is exactly that shape, and it is the entry that exists *because* a denylist
defaults to covered only within the tree it is applied to. And it only catches
class (b) where a *test* exercises the code path; neither mechanism sees an
untested CLI branch.

The refusal on a dirty tree is the load-bearing part. A clone only ever sees
**committed** state, so a pre-flight run against an uncommitted tree tests stale
code and reports GREEN — the same invisible pass the gate exists to kill. That
is also why this belongs in `/post-branch` phase 5, *after* the doc commits and
before `gh pr create`, and not in phase 1's sweep, which runs before them.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # noqa: S404 - git/poetry plumbing, fixed argv, no shell
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

#: Suite passed against the clean clone.
OK = 0
#: The suite failed — a real finding.
FAILED = 1
#: Refused to run: the working tree is dirty, so a clone would test stale HEAD.
REFUSED = 2
#: The clone or the dependency install failed — infrastructure, not a finding.
INFRA = 3

#: Mirrors `make test` exactly, so "replacement" is literal rather than roughly.
PYTEST_ARGS: tuple[str, ...] = (
    "tests/",
    "-q",
    "--durations=10",
    "--ignore=tests/test_regression.py",
)


def clone_argv(root: Path, dest: Path) -> list[str]:
    """Argv for the clone.

    ``--no-hardlinks`` is not optional: ``git clone --local`` (the default for a
    local path) fails ``Invalid cross-device link`` cloning onto ``/tmp`` here,
    because the repo and the temp dir sit on different filesystems.
    """
    return ["git", "clone", "--no-hardlinks", "--quiet", str(root), str(dest)]


def install_argv() -> list[str]:
    """Dependencies come from the clone's own lock, not the dev box's venv.

    The hermetic form is bought for almost nothing against a warm wheel cache,
    and it widens the gate to catch a dependency that is installed locally but
    undeclared in `poetry.lock`.
    """
    return ["poetry", "install", "--no-root"]


def pytest_argv() -> list[str]:
    return ["poetry", "run", "pytest", *PYTEST_ARGS]


def dirty_paths(root: Path) -> list[str]:
    """Porcelain lines for every uncommitted change, untracked files included.

    Untracked matters most: a brand-new module is precisely the change a clone
    would silently omit while the suite still went green.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _head(root: Path) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "log", "--oneline", "-1"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=".",
        help="repository to clone (default: the current directory)",
    )
    parser.add_argument(
        "--dest",
        default=None,
        help="clone destination; a temp dir that is removed afterwards by default",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="clone and report, but skip the install and the suite",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.repo).resolve()

    dirty = dirty_paths(root)
    if dirty:
        print("⛔ REFUSED: the working tree is dirty.")
        print("   A clone sees COMMITTED state only, so this run would test stale")
        print("   HEAD and report green. Commit first, then re-run.")
        for line in dirty:
            print(f"     {line}")
        return REFUSED

    owned_tmp: str | None = None
    if args.dest:
        dest = Path(args.dest)
    else:
        owned_tmp = tempfile.mkdtemp(prefix="clone-preflight-")
        dest = Path(owned_tmp) / "clone"

    try:
        # flush=True on every banner line: stdout is BLOCK-buffered when this
        # runs redirected to a file (the normal background-run shape), so
        # without it the banner lands AFTER four minutes of pytest output from
        # the subprocess — and the `HEAD` line is exactly what a caller is told
        # to check against `git rev-parse HEAD` before trusting the result.
        print(f"🧪 clean-clone pre-flight: {root}", flush=True)
        print(f"   HEAD {_head(root)}", flush=True)
        print(f"   clone → {dest}", flush=True)
        if subprocess.run(clone_argv(root, dest)).returncode != 0:  # noqa: S603
            print("⚠ clone failed — infrastructure, not a finding.", flush=True)
            return INFRA

        if args.dry_run:
            print("   dry run: skipping the install and the suite. Would run:")
            print(f"     {' '.join(install_argv())}")
            print(f"     {' '.join(pytest_argv())}")
            return OK

        if subprocess.run(install_argv(), cwd=dest).returncode != 0:  # noqa: S603
            print(
                "⚠ dependency install failed — infrastructure, not a finding.",
                flush=True,
            )
            return INFRA

        if subprocess.run(pytest_argv(), cwd=dest).returncode != 0:  # noqa: S603
            print("⛔ the suite FAILED against a clean clone.", flush=True)
            print("   This is what CI would have told you after a metered cycle.")
            return FAILED

        print(
            "✅ clean-clone pre-flight passed. This REPLACES this branch's `make test`."
        )
        print(
            "   Not covered: an absolute $HOME default, or a CLI branch no test reaches."
        )
        return OK
    finally:
        if owned_tmp is not None:
            shutil.rmtree(owned_tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
