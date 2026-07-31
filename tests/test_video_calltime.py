"""Tests for tools/video_calltime.py — the look-ahead guard. Pure, no I/O."""

from __future__ import annotations

import json

import pytest

from tools.video_calltime import (
    CallTime,
    is_backlog,
    main,
    resolve_call_ts,
)

PUB = "2026-07-28T14:00:00+00:00"


def test_falls_back_to_publish_when_no_stated_time() -> None:
    got = resolve_call_ts(PUB)
    assert got == CallTime(
        call_ts_utc=PUB, call_ts_source="publish", publish_ts_utc=PUB, stated_ts_raw=""
    )


def test_prefers_stated_time_when_valid() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-28T08:00:00+00:00", stated_ts_raw="it's 8am Monday"
    )
    assert got.call_ts_utc == "2026-07-28T08:00:00+00:00"
    assert got.call_ts_source == "stated"
    assert got.stated_ts_raw == "it's 8am Monday"
    assert got.publish_ts_utc == PUB


def test_rejects_stated_time_at_or_after_publish() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-28T16:00:00+00:00")
    assert got.call_ts_source == "publish"
    assert got.call_ts_utc == PUB


def test_rejects_stated_time_leading_publish_by_more_than_max() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-01T08:00:00+00:00")
    assert got.call_ts_source == "publish"


def test_date_only_resolves_to_conservative_end_of_day() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-27T00:00:00+00:00", stated_date_only=True
    )
    assert got.call_ts_utc == "2026-07-27T23:59:59+00:00"
    assert got.call_ts_source == "stated"


def test_date_only_clamps_below_publish() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-28T00:00:00+00:00", stated_date_only=True
    )
    assert got.call_ts_utc == PUB
    assert got.call_ts_source == "publish"


def test_unparseable_stated_time_falls_back() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="last Tuesday-ish")
    assert got.call_ts_source == "publish"


def test_unparseable_publish_time_raises() -> None:
    with pytest.raises(ValueError, match="publish_ts_utc"):
        resolve_call_ts("not a timestamp")


def test_is_backlog_true_beyond_threshold() -> None:
    assert is_backlog(PUB, "2026-07-30T14:00:00+00:00") is True


def test_is_backlog_false_within_threshold() -> None:
    assert is_backlog(PUB, "2026-07-28T20:00:00+00:00") is False


def test_cli_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--publish", PUB, "--stated", "2026-07-28T08:00:00+00:00"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["call_ts_source"] == "stated"
    assert payload["call_ts_utc"] == "2026-07-28T08:00:00+00:00"


def test_naive_stated_time_falls_back_to_publish() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-28T08:00:00")
    assert got.call_ts_source == "publish"
    assert got.call_ts_utc == PUB


def test_bare_date_stated_time_falls_back_to_publish() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-27", stated_date_only=True)
    assert got.call_ts_source == "publish"


def test_naive_publish_time_raises() -> None:
    with pytest.raises(ValueError, match="publish_ts_utc"):
        resolve_call_ts("2026-07-28T14:00:00")


def test_stated_time_exactly_equal_to_publish_is_rejected() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc=PUB)
    assert got.call_ts_source == "publish"


def test_is_backlog_raises_on_unparseable_publish() -> None:
    with pytest.raises(ValueError, match="publish_ts_utc"):
        is_backlog("not a timestamp", "2026-07-30T14:00:00+00:00")


def test_is_backlog_raises_on_naive_ingested() -> None:
    with pytest.raises(ValueError, match="ingested_ts_utc"):
        is_backlog(PUB, "2026-07-30T14:00:00")
