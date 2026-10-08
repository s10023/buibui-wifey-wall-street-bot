"""Candle watermark dedup store for signal alerts.

Tracks the last alerted candle open_time per (symbol, timeframe, strategy).
Prevents re-alerting on the same candle across multiple scan cycles.

State is persisted to a JSON file so dedup survives daemon restarts.
"""

import json
from contextlib import suppress
from pathlib import Path


def _key(symbol: str, timeframe: str, strategy: str, channel: str = "primary") -> str:
    # Primary channel keeps the legacy `{sym}:{tf}:{strategy}` shape so existing
    # signal_state.json files remain readable without migration. Wife (and any
    # future channel) gets a suffixed key so its watermark moves independently.
    base = f"{symbol}:{timeframe}:{strategy}"
    return base if channel == "primary" else f"{base}:{channel}"


class CooldownStore:
    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)
        self._watermarks: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        # Corrupted or unreadable state file: start empty rather than crash the daemon.
        with suppress(json.JSONDecodeError, OSError):
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._watermarks = data.get("watermarks", {})

    def _save(self) -> None:
        self._path.write_text(
            json.dumps({"watermarks": self._watermarks}, indent=2), encoding="utf-8"
        )

    def is_new_candle(
        self,
        symbol: str,
        timeframe: str,
        strategy: str,
        open_time: int,
        channel: str = "primary",
    ) -> bool:
        """Return True if open_time is newer than the last alerted candle."""
        return (
            self._watermarks.get(_key(symbol, timeframe, strategy, channel), -1)
            < open_time
        )

    def last_marked(
        self,
        symbol: str,
        timeframe: str,
        strategy: str,
        channel: str = "primary",
    ) -> int | None:
        """Return the last alerted candle open_time, or None if never marked.

        Distinct from ``is_new_candle``'s ``-1`` sentinel: catch-up needs to tell
        "no prior watermark" (cold start → fire only the latest candle, never the
        whole window) apart from "marked at candle 0".
        """
        return self._watermarks.get(_key(symbol, timeframe, strategy, channel))

    def mark_candle(
        self,
        symbol: str,
        timeframe: str,
        strategy: str,
        open_time: int,
        channel: str = "primary",
    ) -> None:
        """Record open_time as the last alerted candle and persist."""
        self._watermarks[_key(symbol, timeframe, strategy, channel)] = open_time
        self._save()
