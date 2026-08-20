#!/usr/bin/env python3
"""PreToolUse guard hook — blocks catastrophic / unrecoverable Bash commands.

Self-authored (no third-party dependency) so every rule is reviewable here.
Wired via .claude/settings.json -> hooks.PreToolUse (matcher "Bash").

Protocol: Claude Code pipes {"tool_name","tool_input":{"command":...}} on stdin.
Exit 0 = allow. Exit 2 = block (stderr reason is shown to Claude and the user).

Tune by editing HARD_DENY below. Disable by removing the hook from settings.json.
The OPTIONAL block (live signal daemon) is off by default — uncomment to enable.
"""

from __future__ import annotations

import json
import re
import sys

# (regex, human reason) — matched case-insensitively against the full command.
HARD_DENY: list[tuple[str, str]] = [
    (
        r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r",
        "rm -rf (recursive force delete)",
    ),
    (
        r"\brm\b.*\.(duckdb|db)\b",
        "deleting a DuckDB/.db file (analytics.db is gitignored + not trivially recoverable)",
    ),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard (discards uncommitted work)"),
    (r"\bgit\s+clean\s+-[a-z]*[dx]", "git clean -fd/-fdx (deletes untracked files)"),
    (r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "git force-push"),
    (
        r"\b(drop\s+table|drop\s+database|truncate\s+table)\b",
        "destructive SQL (DROP/TRUNCATE)",
    ),
    (r"\bclean-db\b", "clean-db (wipes the analytics DB)"),
]

# OPTIONAL — guard against the live signal daemon firing real Telegram + DB writes.
# Per MEMORY.md: `signal watch --once` fired real Telegram and wrote real analytics.db.
# Uncomment to require a conscious override.
# HARD_DENY.append(
#     (r"\bsignal\s+watch\b", "live signal daemon (sends real Telegram + writes real DB) — smoke-test deliberately")
# )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open — never break the session on a parse error

    if payload.get("tool_name") != "Bash":
        return 0

    command = str(payload.get("tool_input", {}).get("command", ""))
    if not command:
        return 0

    for pattern, reason in HARD_DENY:
        if re.search(pattern, command, re.IGNORECASE):
            sys.stderr.write(
                f"BLOCKED by guard-destructive hook: {reason}.\n"
                f"Command: {command}\n"
                "If this is genuinely intended, run it yourself in a terminal, "
                "rephrase it, or temporarily disable the hook in .claude/settings.json.\n"
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
