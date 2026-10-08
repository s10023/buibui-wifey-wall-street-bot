"""Environment for a child Python whose output the parent decodes as UTF-8.

`encoding="utf-8"` on a `subprocess` call (enforced by
`tests/test_explicit_encoding.py`) fixes how the PARENT decodes. A child Python
on Windows still WRITES its piped stdout/stderr as cp1252 unless its env carries
`PYTHONUTF8=1`, so any non-ASCII byte it prints fails the parent's strict decode
(Issue #411). Pass `env=python_child_env()` at every site that launches Python
and decodes its output; the same test enforces that.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping


def python_child_env(
    base: Mapping[str, str] | None = None, *, drop: Iterable[str] = ()
) -> dict[str, str]:
    """A copy of `base` (default `os.environ`) without `drop`, with `PYTHONUTF8=1`."""
    source = os.environ if base is None else base
    dropped = set(drop)
    env = {k: v for k, v in source.items() if k not in dropped}
    env["PYTHONUTF8"] = "1"
    return env
