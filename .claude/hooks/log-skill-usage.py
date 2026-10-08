#!/usr/bin/env python3
"""Skill-usage log — records every skill invocation, never blocks (#395).

Nothing recorded which skills ran, so there was no evidence for which of the repo's
skills earn the description that loads into every session and which are dead weight.
This appends one tab-separated line per invocation to the gitignored
``.claude/skill-usage.log``: UTC timestamp, source, skill name, args (truncated).

Two events, because a skill reaches a session two ways:

* ``PreToolUse`` on ``Skill``: the model invoked it (source ``tool``).
* ``UserPromptSubmit`` starting with ``/name``: the user typed it (source ``prompt``).
  A typed slash command loads the skill without a ``Skill`` tool call, so a log fed by
  ``PreToolUse`` alone would score the skills the user reaches for most as unused.
  This side logs every ``/name``, built-ins such as ``/compact`` included; the
  summary judges only the repo's own ``.claude/skills/`` against the log.

It must never block: every path returns 0, every failure is swallowed, and its wrapper
in ``settings.json`` ends ``exit 0`` instead of ``exec``-ing the interpreter. A usage
log that can refuse a skill would cost more than the evidence it collects.

``--summary`` prints per-name counts and the repo skills with none; ``--status-line``
prints the one line ``make status`` shows. ``WIFEY_SKILL_LOG`` overrides the path.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
ARGS_MAX = 80

#: A typed slash command: `/name` or `/plugin:name` at the start of the prompt.
SLASH_RE = re.compile(r"^\s*/([A-Za-z0-9][A-Za-z0-9_:.-]*)(?:\s+(.*))?\Z", re.DOTALL)


def log_path() -> Path:
    override = os.environ.get("WIFEY_SKILL_LOG")
    return Path(override) if override else REPO_ROOT / ".claude" / "skill-usage.log"


def _clean(text: str) -> str:
    """One field of one line: no tabs or newlines, at most ARGS_MAX characters."""
    flat = " ".join(text.split())
    return flat if len(flat) <= ARGS_MAX else flat[: ARGS_MAX - 1] + "…"


def entry(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    """``(source, name, args)`` for an invocation, or None when this is not one."""
    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":
        m = SLASH_RE.match(str(payload.get("prompt", "")))
        if m is None:
            return None
        return "prompt", m.group(1), _clean(m.group(2) or "")
    if payload.get("tool_name") == "Skill":
        tool_input = payload.get("tool_input") or {}
        name = str(tool_input.get("skill", "")).strip().lstrip("/")
        if not name:
            return None
        return "tool", _clean(name), _clean(str(tool_input.get("args", "") or ""))
    return None


def record(payload: dict[str, Any], now: datetime | None = None) -> bool:
    """Append the invocation's line; True when one was written."""
    found = entry(payload)
    if found is None:
        return False
    ts = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\t".join((ts, *found)) + "\n")
    return True


def repo_skills() -> list[str]:
    return sorted(p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md"))


def read_log() -> tuple[Counter[str], str | None]:
    """Per-name counts, plus the first timestamp (None for an absent or empty log)."""
    counts: Counter[str] = Counter()
    first: str | None = None
    path = log_path()
    if not path.is_file():
        return counts, None
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        first = first or parts[0]
        counts[parts[2]] += 1
    return counts, first


def status_line() -> str:
    counts, first = read_log()
    skills = repo_skills()
    if first is None:
        return f"no log yet ({len(skills)} repo skills)"
    unused = sum(1 for s in skills if counts[s] == 0)
    return (
        f"{sum(counts.values())} logged since {first[:10]}, "
        f"{unused}/{len(skills)} repo skills unused"
    )


def summary() -> str:
    counts, first = read_log()
    skills = repo_skills()
    if first is None:
        return f"no skill-usage log at {log_path()}"
    lines = [f"skill usage since {first} ({log_path()})"]
    lines += [f"  {n:5d}  {name}" for name, n in counts.most_common()]
    unused = [s for s in skills if counts[s] == 0]
    lines.append(f"repo skills never invoked ({len(unused)}/{len(skills)}):")
    lines += [f"  {s}" for s in unused]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    try:
        if "--summary" in argv:
            print(summary())
        elif "--status-line" in argv:
            print(status_line())
        else:
            payload = json.load(sys.stdin)
            if isinstance(payload, dict):
                record(payload)
    except Exception:  # noqa: BLE001, S110 - a usage log must never block or crash
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
