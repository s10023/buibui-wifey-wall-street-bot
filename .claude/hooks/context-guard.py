#!/usr/bin/env python3
"""PreToolUse hook — delivers the footgun that governs the file about to change.

Self-authored (no third-party dependency) so every rule is reviewable here.
Wired via .claude/settings.json -> hooks.PreToolUse (matcher "Edit|Write|...").
Card text lives in context-map.json beside this file; this module is logic only.

WHY THIS EXISTS (SoT ST23)
    CLAUDE.md, MEMORY.md and the handoff each carry the same facts because a
    guard rail behind a pointer is not a guard rail -- nothing forces a read of
    .claude/context/analytics.md before someone edits analytics/store/_common.py.
    So the footguns were pinned into the always-loaded tier, where three copies
    of a number cannot be kept in sync. Measured 2026-08-13: H14/H15 appear 17x
    across those three files. Duplication is the mechanism behind this repo's
    #1 recurring defect ("the filed number was wrong").

    This hook is the alternative: the footgun arrives at the moment of the edit
    instead of being paid on every boot. It is what lets a rule LEAVE CLAUDE.md
    without becoming invisible.

WHY IT DOES NOT INJECT THE .claude/context/*.md FILE ITSELF
    ST23's phrasing was "inject the matching context file". Taken literally that
    is 56KB for analytics.md and 62KB for tools.md -- an order of magnitude worse
    than the ~13.2k CLAUDE.md boot cost it is meant to relieve. What the caller
    needs is the two or three sentences that stop the wrong edit, plus a pointer
    to the deep reference. So each card is short by construction and the total
    injection is capped (MAX_INJECTED_CHARS).

TWO TRIGGERS, AND THEY ARE GENUINELY DIFFERENT
    path  -- the file being written matches a curated glob. Catches "you are
             about to edit the thing with the trap in it".
    claim -- the CONTENT being written asserts a negative result ("NO-EDGE",
             "powered null", "CONFIRMED-BAD", ...). ST28's lesson is that the
             six powered-null sites each spelled the ARITHMETIC differently
             (sample-size floor x4, failure-to-clear x1, noise-derived MDE x1),
             so no grep for the pattern finds the seventh. The CLAIM is the only
             durable trigger, which is why it is watched separately from paths.

WHY IT DELIBERATELY DOES *NOT* SKIP GITIGNORED PATHS
    This inverts guard-branch.py, on purpose. The sixth powered-null site lived
    in docs/plans/scratch/, which is unreachable by every hygiene pass this repo
    has -- so ST26 and ST27 structurally could not see it, and it was found only
    by promoting the file into tools/. Gitignored code is precisely where this
    class of defect survives. Skipping it would blind the hook to the one place
    that has already burned us.

WHY IT IS ADVISORY, NOT BLOCKING
    Every card here is "easy to get wrong", not "unrecoverable". Blocking is
    reserved for the latter (see guard-destructive.py).

WHY IT STAYS QUIET
    At most one utterance per card per session, and the claim trigger only fires
    on code and verdict docs -- never on the handoff or memory prose that merely
    RECALLS a past NO-EDGE. A hook that speaks on every edit is one you learn to
    scroll past, the way the `stale SoT rows` line went 0-for-8.

Protocol: Claude Code pipes {"tool_name","tool_input":{...},"session_id":...} on
stdin. Exit 0 always; advisory text is emitted as JSON on stdout.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

WATCHED_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
MAP_PATH = Path(__file__).with_name("context-map.json")

# A hard ceiling on what one tool call can inject. Several cards can match a
# single edit (signals/alert_formatter.py hits two); without a cap, extending
# the map would silently make edits expensive -- the exact failure this hook
# exists to avoid.
MAX_INJECTED_CHARS = 2600

# The claim trigger fires only where a negative result is being AUTHORED.
# Prose that merely recalls one -- the handoff, memory topic files, CLAUDE.md --
# is recollection, not a new claim, and firing there is pure noise.
CLAIM_PATH_SUFFIXES = (".py",)
CLAIM_PATH_PREFIXES = (
    "docs/audits/",
    "docs/plans/scratch/",
    "docs/superpowers/specs/",
)


def _load_map() -> dict[str, Any]:
    """Read the card map, returning an empty map on any failure (fail open)."""
    try:
        with MAP_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _project_dir(target: Path) -> Path | None:
    """Repo root, from the harness env var if set, else by walking up to .git."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    for parent in [target, *target.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _relative_path(raw_path: str) -> str:
    """Repo-relative posix path, or "" when the target sits outside the repo.

    Returning "" (rather than the absolute path) keeps a stray edit in /tmp from
    matching a glob by coincidence.
    """
    target = Path(raw_path)
    root = _project_dir(target)
    if root is None:
        return ""
    try:
        return PurePosixPath(target.resolve().relative_to(root.resolve())).as_posix()
    except (OSError, ValueError):
        return ""


def _written_text(tool_input: dict[str, Any]) -> str:
    """Concatenate whatever text this tool call is about to put on disk.

    Covers all four watched tools: Edit/MultiEdit write `new_string`, Write
    writes `content`, NotebookEdit writes `new_source`.
    """
    parts: list[str] = []
    for key in ("new_string", "content", "new_source"):
        value = tool_input.get(key)
        if isinstance(value, str):
            parts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
                parts.append(edit["new_string"])
    return "\n".join(parts)


def _claim_path_eligible(rel: str) -> bool:
    return rel.endswith(CLAIM_PATH_SUFFIXES) or rel.startswith(CLAIM_PATH_PREFIXES)


def _matches_claim(text: str, tokens: list[str]) -> bool:
    """True when the text asserts a negative result.

    Matched case-insensitively and with flexible separators, because the same
    claim is written NO-EDGE, "no edge" and no_edge across this corpus.
    """
    for token in tokens:
        pattern = r"[\s\-_]+".join(re.escape(word) for word in token.split())
        if re.search(rf"(?<![\w-]){pattern}(?![\w-])", text, re.IGNORECASE):
            return True
    return False


def _already_spoken(session_id: str, card_id: str) -> bool:
    """One utterance per card per session; True if we have spoken already.

    Keyed per card so that touching a second guarded file still speaks, while a
    run of edits to the same file stays silent.
    """
    tag = hashlib.sha256(f"{session_id}:{card_id}".encode()).hexdigest()[:16]
    marker = Path(tempfile.gettempdir()) / f"claude-context-guard-{tag}"
    if marker.exists():
        return True
    # Fail open: an un-deduped card is better than a silent one.
    with contextlib.suppress(OSError):
        marker.touch()
    return False


def _collect(payload: dict[str, Any], cards: dict[str, Any]) -> list[str]:
    """Return the card texts that apply to this tool call, before dedup."""
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return []

    rel = _relative_path(str(tool_input.get("file_path", "")))
    if not rel:
        return []

    session_id = str(payload.get("session_id", "nosession"))
    hits: list[str] = []

    for card in cards.get("cards", []):
        if not isinstance(card, dict):
            continue
        globs = card.get("globs", [])
        if not any(fnmatch(rel, str(g)) for g in globs):
            continue
        if _already_spoken(session_id, str(card.get("id", ""))):
            continue
        hits.append(str(card.get("text", "")).strip())

    claim = cards.get("claim")
    if isinstance(claim, dict) and _claim_path_eligible(rel):
        tokens = [str(t) for t in claim.get("tokens", [])]
        if _matches_claim(_written_text(tool_input), tokens) and not _already_spoken(
            session_id, str(claim.get("id", "claim"))
        ):
            hits.append(str(claim.get("text", "")).strip())

    return [h for h in hits if h]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open -- never break the session on a parse error
    if not isinstance(payload, dict):
        return 0

    if payload.get("tool_name") not in WATCHED_TOOLS:
        return 0

    cards = _load_map()
    if not cards:
        return 0

    hits = _collect(payload, cards)
    if not hits:
        return 0

    message = "context-guard: " + " || ".join(hits)
    if len(message) > MAX_INJECTED_CHARS:
        message = message[:MAX_INJECTED_CHARS].rstrip() + " [...truncated]"
    message += (
        " -- Advisory only, never blocking. These rules are owned by "
        ".claude/hooks/context-map.json; deep reference in .claude/context/."
    )

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": message,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
