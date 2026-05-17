"""Shared constants and helpers for the stats package.

Day/week boundaries: grouped by UTC date so each "day" matches exactly one
daily candle (00:00 UTC – 23:59 UTC = 08:00 MYT – 07:59 MYT).
Hour display in the kill-zone chart uses MYT (+8h) to show local time-of-day.
Session labels (Pre-Market / RTH / Power Hour / After Hours) are defined in
America/New_York wall-clock, mirroring the T15 alert formatter.
"""

from datetime import UTC, datetime, timedelta

MYT_OFFSET_HOURS = 8  # UTC+8

_DOW_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
_DOW_SHORT = {
    "Monday": "Mon",
    "Tuesday": "Tue",
    "Wednesday": "Wed",
    "Thursday": "Thu",
    "Friday": "Fri",
    "Saturday": "Sat",
    "Sunday": "Sun",
}

_ISODOW_TO_SHORT = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _start_ms(days: int) -> int:
    """Return Unix ms timestamp for `days` ago from now."""
    return int((datetime.now(tz=UTC) - timedelta(days=days)).timestamp() * 1000)
