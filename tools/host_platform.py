"""The one place this repo asks which platform it is running on.

The answer is trivial; the rule below is not, and a rule restated at each call
site is how it comes to disagree with itself. So the question lives here and the rule
is written once.

**Call `is_windows()`; never inline `os.name == "nt"` at a call site.** The reason is
testability, and it is sharper than a style preference: patching `os.name` to the
foreign value to exercise the other branch also repoints `pathlib`, which dispatches on
it. Any `Path(...)` under that patch then raises ``NotImplementedError: cannot
instantiate 'PosixPath' on your system``. Measured on the parent 2026-09-18: that did
not merely fail the case under test, it took pytest's own failure reporting down with it
(`INTERNALERROR` out of `_repr_failure_py`), so the run reported nothing at all rather
than a red. A function is patchable without touching the interpreter's own path
machinery; `os.name` is not.

**Two consumers** — `tools/freshness_check.py`, which asks the scheduler whether the
weekly universe job is enabled, and `tools/venv_bootstrap.py`, which picks between
`.venv/Scripts/python.exe` and `.venv/bin/python` and between two swap mechanisms. This
module is not here to deduplicate: it is here because the rule above needs a
home and because a seam a test can patch is the only way to exercise the branch this
host does not take. Do not delete it as over-engineering on a consumer count; the count
is not the reason.
"""

from __future__ import annotations

import os


def is_windows() -> bool:
    """True on a Windows host, read at CALL time so a test can force either branch."""
    return os.name == "nt"
