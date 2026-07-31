"""Pure frame-selection for /ingest-video — which moments of a video are worth seeing.

Deliberately NOT scene-change based (that is what claude-video does, and it returns
~100 near-identical talking-head frames while missing the annotated chart). Frames are
selected where the *transcript* indicates the speaker is pointing at something.

Stdlib only: tools/video_fetch.py imports TranscriptSegment from here, so this module
must never pull a network dependency into the pure import chain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ITEM_CAP = 5
FRAME_CAP = 15
DEDUP_WINDOW_S = 45.0
SAFETY_SAMPLE_S = 300.0

_WEIGHTS = {"item": 3, "deixis": 2, "level": 2, "sample": 1}

_DEIXIS_PATTERNS: tuple[str, ...] = (
    r"\bright here\b",
    r"\bover here\b",
    r"\blook (?:at )?(?:here|this)\b",
    r"\b(?:long|short|buy|sell)(?:ing)? (?:it )?here\b",
    r"\bthis (?:level|structure|zone|area|candle|wick|move|setup)\b",
    r"\bthese (?:levels|zones|highs|lows)\b",
    r"这里",
    r"这个(?:位置|区域|结构|水平)",
    r"看(?:这里|这个)",
)
_DEIXIS_RE = re.compile("|".join(_DEIXIS_PATTERNS), re.IGNORECASE)

_SYMBOL_RE = re.compile(
    r"\b(?:btc|eth|sol|xrp|doge|bnb|ada|avax|chainlink|bitcoin|ether(?:eum)?)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"  # 2,980 · 62,400
    r"|\b\d{3,}(?:\.\d+)?\b"  # 62400 · 138
    r"|\b\d+\.\d+\b"  # 62.4 · 138.5
    r"|\b\d+(?:\.\d+)?\s*[km]\b",  # 62.4k · 1.2m
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


@dataclass(frozen=True)
class TranscriptSegment:
    ts_s: float
    text: str
    lang: str


@dataclass(frozen=True)
class FrameMark:
    ts_s: float
    reason: str
    weight: int


def _mark(ts_s: float, reason: str) -> FrameMark:
    return FrameMark(ts_s=ts_s, reason=reason, weight=_WEIGHTS[reason])


def _has_price(text: str) -> bool:
    """A price-shaped number that is not a bare calendar year."""
    return any(
        not _YEAR_RE.match(match.group(0).strip())
        for match in _NUMBER_RE.finditer(text)
    )


def deixis_marks(segments: list[TranscriptSegment]) -> list[FrameMark]:
    """Segments where the speaker points at something only the frame shows."""
    return [_mark(s.ts_s, "deixis") for s in segments if _DEIXIS_RE.search(s.text)]


def level_marks(segments: list[TranscriptSegment]) -> list[FrameMark]:
    """Segments naming a price near a symbol — the chart is ground truth for the number."""
    return [
        _mark(s.ts_s, "level")
        for s in segments
        if _SYMBOL_RE.search(s.text) and _has_price(s.text)
    ]


def sample_marks(
    duration_s: float, *, sample_s: float = SAFETY_SAMPLE_S
) -> list[FrameMark]:
    """Low-rate safety sample so the vision pass is not blind between pointing moments."""
    if sample_s <= 0 or duration_s <= 0:
        return []
    count = int(duration_s // sample_s) + 1
    return [_mark(i * sample_s, "sample") for i in range(count)]


def dedupe(
    marks: list[FrameMark], *, window_s: float = DEDUP_WINDOW_S
) -> list[FrameMark]:
    """Collapse marks inside `window_s` to the highest-weight one.

    Stops a twenty-second riff about "here" yielding eight near-identical frames.
    """
    kept: list[FrameMark] = []
    for mark in sorted(marks, key=lambda m: (m.ts_s, -m.weight)):
        if kept and mark.ts_s - kept[-1].ts_s < window_s:
            if mark.weight > kept[-1].weight:
                kept[-1] = mark
            continue
        kept.append(mark)
    return kept


def select(
    segments: list[TranscriptSegment],
    item_ts: list[float],
    duration_s: float,
    *,
    cap: int = FRAME_CAP,
    window_s: float = DEDUP_WINDOW_S,
    sample_s: float = SAFETY_SAMPLE_S,
) -> list[FrameMark]:
    """Ranked, capped, deduplicated frame timestamps, ordered chronologically.

    Ranking is by weight then earliness, so items beat deixis beats sampling and the
    cap always keeps the most informative frames. Deterministic: no randomness.
    """
    candidates = (
        [_mark(ts, "item") for ts in item_ts]
        + deixis_marks(segments)
        + level_marks(segments)
        + sample_marks(duration_s, sample_s=sample_s)
    )
    deduped = dedupe(candidates, window_s=window_s)
    ranked = sorted(deduped, key=lambda m: (-m.weight, m.ts_s))[:cap]
    return sorted(ranked, key=lambda m: m.ts_s)
