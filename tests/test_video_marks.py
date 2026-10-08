"""Tests for tools/video_marks.py — pure, no I/O."""

from __future__ import annotations

from typing import Any

from tools.video_marks import (
    DEDUP_WINDOW_S,
    FRAME_CAP,
    ITEM_CAP,
    MIN_ITEM_SPECIFICITY,
    TAIL_OFFSETS_S,
    FrameMark,
    TranscriptSegment,
    dedupe,
    deixis_marks,
    keep_items,
    level_marks,
    sample_marks,
    select,
    tail_marks,
)


def seg(ts: float, text: str, lang: str = "en") -> TranscriptSegment:
    return TranscriptSegment(ts_s=ts, text=text, lang=lang)


def test_deixis_marks_english() -> None:
    segments = [
        seg(10.0, "the market is quiet today"),
        seg(20.0, "I am taking a long here"),
        seg(30.0, "look at this structure"),
    ]
    marks = deixis_marks(segments)
    assert [m.ts_s for m in marks] == [20.0, 30.0]
    assert all(m.reason == "deixis" for m in marks)


def test_deixis_marks_chinese() -> None:
    segments = [seg(5.0, "大盘很安静", "zh"), seg(15.0, "我在这里做多", "zh")]
    marks = deixis_marks(segments)
    assert [m.ts_s for m in marks] == [15.0]


def test_level_marks_needs_symbol_context() -> None:
    segments = [
        seg(10.0, "I have been doing this for 15 years"),
        seg(20.0, "BTC is at 62400 right now"),
    ]
    marks = level_marks(segments)
    assert [m.ts_s for m in marks] == [20.0]
    assert marks[0].reason == "level"


def test_sample_marks_every_interval() -> None:
    marks = sample_marks(duration_s=650.0, sample_s=300.0)
    assert [m.ts_s for m in marks] == [0.0, 300.0, 600.0]
    assert all(m.reason == "sample" for m in marks)


def test_dedupe_keeps_highest_weight_in_window() -> None:
    marks = [
        FrameMark(ts_s=10.0, reason="sample", weight=1),
        FrameMark(ts_s=20.0, reason="item", weight=3),
        FrameMark(ts_s=30.0, reason="deixis", weight=2),
        FrameMark(ts_s=200.0, reason="deixis", weight=2),
    ]
    kept = dedupe(marks, window_s=DEDUP_WINDOW_S)
    assert [(m.ts_s, m.reason) for m in kept] == [(20.0, "item"), (200.0, "deixis")]


def test_select_respects_cap_and_prefers_items() -> None:
    segments = [seg(float(i * 10), f"look here number {i}") for i in range(40)]
    marks = select(segments, item_ts=[5.0], duration_s=400.0, cap=3)
    assert len(marks) == 3
    assert marks[0].reason == "item"
    assert marks == sorted(marks, key=lambda m: m.ts_s)


def test_select_is_deterministic() -> None:
    segments = [seg(10.0, "long here"), seg(90.0, "BTC at 62400")]
    a = select(segments, item_ts=[], duration_s=120.0)
    b = select(segments, item_ts=[], duration_s=120.0)
    assert a == b


def test_select_never_exceeds_frame_cap_by_default() -> None:
    segments = [seg(float(i * 5), "taking a long here") for i in range(200)]
    marks = select(segments, item_ts=[], duration_s=1000.0)
    assert len(marks) <= FRAME_CAP


def test_level_marks_ignores_link_in_the_description() -> None:
    segments = [seg(10.0, "link in the description, I've been trading 15 years")]
    assert level_marks(segments) == []


def test_level_marks_ignores_small_counts() -> None:
    segments = [seg(10.0, "here are my top 3 setups for BTC")]
    assert level_marks(segments) == []


def test_level_marks_ignores_bare_years() -> None:
    segments = [seg(10.0, "ETH did this back in 2021")]
    assert level_marks(segments) == []


def test_level_marks_accepts_price_shapes() -> None:
    cases = [
        "BTC at 62400",
        "ETH 2,980",
        "SOL 138.5",
        "BTC 62.4k",
        "shorting BTC at 63.8",
    ]
    for text in cases:
        assert level_marks([seg(10.0, text)]) != [], text


def test_level_marks_still_needs_a_symbol() -> None:
    assert level_marks([seg(10.0, "it printed 62400 on the hourly")]) == []


def test_tail_marks_anchor_the_final_seconds() -> None:
    marks = tail_marks(duration_s=900.0)
    assert [m.ts_s for m in marks] == [840.0, 898.0]
    assert all(m.reason == "tail" for m in marks)


def test_tail_offsets_are_further_apart_than_the_dedup_window() -> None:
    """Otherwise dedupe collapses the pair and the very last frame is lost.

    The dedup pass keeps the EARLIER mark on a weight tie, so a too-close pair
    would silently drop the `duration - 2` anchor — the one that actually reaches
    a closing slide.
    """
    spread = max(TAIL_OFFSETS_S) - min(TAIL_OFFSETS_S)
    assert spread > DEDUP_WINDOW_S


def test_tail_marks_clamp_on_a_short_video() -> None:
    assert [m.ts_s for m in tail_marks(duration_s=30.0)] == [28.0]
    assert tail_marks(duration_s=1.0) == []


def test_tail_marks_empty_on_nonpositive_duration() -> None:
    assert tail_marks(duration_s=0.0) == []
    assert tail_marks(duration_s=-5.0) == []


def test_tail_outranks_a_sample_at_the_same_timestamp() -> None:
    """A tail anchor colliding with the safety grid must win, not be absorbed."""
    kept = dedupe(
        [FrameMark(ts_s=600.0, reason="sample", weight=1), *tail_marks(660.0)],
        window_s=DEDUP_WINDOW_S,
    )
    assert [(m.ts_s, m.reason) for m in kept] == [(600.0, "tail"), (658.0, "tail")]


def test_select_reaches_the_tail_of_a_long_video() -> None:
    """The regression: sample_marks stops at floor(duration/300)*300.

    Measured across one /ingest-feed batch of five videos, the last mark of any kind landed
    133.8 / 61.7 / 0.0 / 79.0 / 152.1s before the end — four of five videos blind
    for one to two and a half minutes. A closing summary slide with no narration
    over it produced no trigger of any kind, so pass 2 never saw it.
    """
    marks = select(segments=[], item_ts=[], duration_s=1450.0)
    assert marks[-1].ts_s == 1448.0
    assert marks[-1].reason == "tail"


def test_select_keeps_the_tail_under_cap_pressure() -> None:
    """Weight matters: at sample tier the tail would be trimmed first, on exactly
    the long dense videos where the blind tail is worst."""
    segments = [seg(float(i * 5), "taking a long here") for i in range(400)]
    marks = select(
        segments, item_ts=[float(i) for i in range(ITEM_CAP)], duration_s=2000.0
    )
    assert len(marks) <= FRAME_CAP
    assert [m.ts_s for m in marks if m.reason == "tail"] == [1940.0, 1998.0]


def test_select_tail_does_not_crowd_out_items() -> None:
    """ITEM_CAP items plus the tail anchors must both fit under FRAME_CAP."""
    segments = [seg(float(i * 5), "taking a long here") for i in range(400)]
    item_ts = [float(300 + i * 100) for i in range(ITEM_CAP)]
    marks = select(segments, item_ts=item_ts, duration_s=2000.0)
    kept_items = {m.ts_s for m in marks if m.reason == "item"}
    assert kept_items == set(item_ts)


def test_item_cap_leaves_room_for_tail_anchors() -> None:
    """The hard ceiling on ITEM_CAP. Past it, kept items silently lose their frame and
    drop to vision_confidence 'low' — a failure that looks like 'no chart', not a bug."""
    assert ITEM_CAP + len(TAIL_OFFSETS_S) <= FRAME_CAP


def cand(ts: float, specificity: int, gist: str = "g") -> dict[str, Any]:
    return {"ts": ts, "content_type": "setup", "specificity": specificity, "gist": gist}


def test_keep_items_floor_binds_before_cap_on_a_thin_video() -> None:
    """A sparse video must NOT pad up to the cap just because the slots exist — that is
    what routes vibes into Stream C as if they were calls."""
    kept, dropped = keep_items(
        [cand(10.0, 5), cand(20.0, 4), cand(30.0, 2), cand(40.0, 1)]
    )
    assert [c["ts"] for c in kept] == [10.0, 20.0]
    assert [c["ts"] for c in dropped] == [30.0, 40.0]
    assert all("below floor" in c["drop_reason"] for c in dropped)


def test_keep_items_cap_binds_on_a_dense_video() -> None:
    """Above the floor, the cap truncates and says so."""
    candidates = [cand(float(i), 4) for i in range(ITEM_CAP + 3)]
    kept, dropped = keep_items(candidates)
    assert len(kept) == ITEM_CAP
    assert len(dropped) == 3
    assert all("cutoff" in c["drop_reason"] for c in dropped)


def test_keep_items_ranks_by_specificity_then_timestamp() -> None:
    """Deterministic: two runs over the same pass-1 output keep the same items."""
    candidates = [cand(90.0, 3), cand(10.0, 5), cand(50.0, 5), cand(20.0, 4)]
    kept, _ = keep_items(candidates, cap=3)
    assert [c["ts"] for c in kept] == [10.0, 50.0, 20.0]


def test_keep_items_is_total_and_lossless() -> None:
    """Every candidate lands in exactly one bucket — nothing vanishes silently."""
    candidates = [cand(float(i), i % 6) for i in range(20)]
    kept, dropped = keep_items(candidates)
    assert len(kept) + len(dropped) == len(candidates)
    assert {c["ts"] for c in kept} & {c["ts"] for c in dropped} == set()
    assert all(c["specificity"] >= MIN_ITEM_SPECIFICITY for c in kept)


def test_keep_items_empty() -> None:
    assert keep_items([]) == ([], [])
