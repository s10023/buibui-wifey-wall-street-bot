"""Pure routing decision for ingested X posts (no I/O).

content_type gate, then the parent pipeline's 4-bucket verdict taxonomy on the
claim path. See docs/superpowers/specs/2026-06-30-x-post-ingest-design.md.
"""

from __future__ import annotations

_DROP_VERDICTS = {"ALREADY-TESTED", "FROZEN-CATEGORY", "NOT-FALSIFIABLE"}


def route_target(content_type: str, verdict: str) -> str | None:
    if content_type == "setup":
        return "docs/plans/pundit-calls.jsonl"
    if content_type == "mechanic":
        return "docs/plans/mechanics-backlog.md"
    if content_type == "claim":
        if verdict == "NOVEL":
            return "docs/plans/thesis-inbox.md"
        if verdict in _DROP_VERDICTS:
            return None
    raise ValueError(f"unroutable: content_type={content_type!r} verdict={verdict!r}")
