"""Shared CLI helpers used by multiple subcommand modules."""

from __future__ import annotations

import datetime


def parse_since_to_ms(since: str) -> int:
    """Parse ISO date string 'YYYY-MM-DD' to Unix milliseconds."""
    d = datetime.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    return int(d.timestamp() * 1000)
