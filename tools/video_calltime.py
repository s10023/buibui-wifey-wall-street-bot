"""Resolve when a video call was actually made — the /ingest-video look-ahead guard.

tools/pundit_score.py resolves every pundit call forward from call_ts_utc. A speaker
often opens with the date/time, which is closer to when the call was made than the
publish timestamp — but that is pundit-supplied and unverifiable, and it moves in the
look-ahead-permitting direction. Publish time is a trustworthy upper bound.

So: prefer stated, but bound it. Kept as deterministic tested code rather than prompt
logic because LLM timezone arithmetic is a known failure mode and this field decides
whether every author's hit rate is honest.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

STATED_TS_MAX_LEAD_H = 168
BACKLOG_THRESHOLD_H = 24


@dataclass(frozen=True)
class CallTime:
    call_ts_utc: str
    call_ts_source: str
    publish_ts_utc: str
    stated_ts_raw: str


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_aware(value: str) -> datetime | None:
    """ISO-8601 with an explicit UTC offset, else None.

    A naive value is rejected rather than assumed UTC: the caller's contract is that
    a stated time carries a resolved zone, and silently assuming one could shift a
    call by up to 12h in the look-ahead-permitting direction. The one carve-out is
    `_parse_stated_date`, which is reached only when the caller sets `stated_date_only`.
    """
    parsed = _parse(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def _parse_stated_date(value: str) -> datetime | None:
    """A date-only stated value, normalised to the last instant of that date.

    A naive value is accepted here and ONLY here, keyed on the caller's
    `stated_date_only` flag rather than on the value's shape. Two things make the
    carve-out safe where `_parse_aware`'s blanket rejection is not: a date carries no
    time, so reading it as UTC loses no zone information the caller ever had, and
    end-of-day normalisation can only move the result LATER than the input — never
    into the look-ahead-permitting direction. Any time component is discarded for the
    same reason: the caller has asserted there is none.

    Reading a bare date as UTC still credits the call up to a zone-offset earlier than
    the speaker's own end-of-day. That residue is bounded by the caller's `stated >=
    publish` and `max_lead_h` checks, and is the price of the flag doing anything at
    all — a bare `YYYY-MM-DD` used to fall through to publish, so the branch was
    unreachable on its own documented input.
    """
    parsed = _parse(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    midnight = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(hours=23, minutes=59, seconds=59)


def resolve_call_ts(
    publish_ts_utc: str,
    *,
    stated_ts_utc: str | None = None,
    stated_date_only: bool = False,
    stated_ts_raw: str = "",
    max_lead_h: int = STATED_TS_MAX_LEAD_H,
) -> CallTime:
    """Stated time if it survives every bound, else publish time."""
    publish = _parse(publish_ts_utc)
    if publish is None or publish.tzinfo is None:
        raise ValueError(f"publish_ts_utc is not ISO-8601: {publish_ts_utc!r}")

    fallback = CallTime(
        call_ts_utc=publish_ts_utc,
        call_ts_source="publish",
        publish_ts_utc=publish_ts_utc,
        stated_ts_raw=stated_ts_raw,
    )
    if stated_ts_utc is None:
        return fallback
    stated = (
        _parse_stated_date(stated_ts_utc)
        if stated_date_only
        else _parse_aware(stated_ts_utc)
    )
    if stated is None:
        return fallback
    if stated >= publish:
        return fallback
    if publish - stated > timedelta(hours=max_lead_h):
        return fallback
    return CallTime(
        call_ts_utc=stated.isoformat(),
        call_ts_source="stated",
        publish_ts_utc=publish_ts_utc,
        stated_ts_raw=stated_ts_raw,
    )


def is_backlog(
    publish_ts_utc: str,
    ingested_ts_utc: str,
    *,
    threshold_h: int = BACKLOG_THRESHOLD_H,
) -> bool:
    """True when our ingest lags publication — describes our lag, not the pundit's."""
    publish = _parse(publish_ts_utc)
    if publish is None or publish.tzinfo is None:
        raise ValueError(f"publish_ts_utc is not ISO-8601: {publish_ts_utc!r}")
    ingested = _parse(ingested_ts_utc)
    if ingested is None or ingested.tzinfo is None:
        raise ValueError(f"ingested_ts_utc is not ISO-8601: {ingested_ts_utc!r}")
    return ingested - publish > timedelta(hours=threshold_h)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve a video call timestamp (stated time preferred, bounded)."
    )
    parser.add_argument("--publish", required=True, help="ISO-8601 publish timestamp")
    parser.add_argument("--stated", default=None, help="ISO-8601 stated timestamp")
    parser.add_argument(
        "--date-only",
        action="store_true",
        help="stated value is a date with no time; a bare YYYY-MM-DD is accepted",
    )
    parser.add_argument("--stated-raw", default="", help="verbatim quote, for audit")
    parser.add_argument("--ingested", default=None, help="ISO-8601 ingest timestamp")
    args = parser.parse_args(argv)

    call = resolve_call_ts(
        args.publish,
        stated_ts_utc=args.stated,
        stated_date_only=args.date_only,
        stated_ts_raw=args.stated_raw,
    )
    payload: dict[str, object] = dict(asdict(call))
    if args.ingested:
        payload["backlog"] = is_backlog(args.publish, args.ingested)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
