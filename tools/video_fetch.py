"""Fetch, transcribe, and frame-extract videos for /ingest-video.

Read-only over the public internet. yt-dlp for metadata + captions, ffmpeg for frames,
Groq whisper-large-v3 only when a video has no captions. `run` (subprocess), `get`
(HTTP), `sleep` and `rng` are injected so the test suite never touches the network —
same contract as tools/x_fetch.py.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import requests
from dotenv import load_dotenv

from tools.video_marks import FrameMark, TranscriptSegment

_YT_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})")
_X_RE = re.compile(r"(?:twitter|x)\.com/[^/]+/status/(\d+)")


class Completedish(Protocol):
    returncode: int
    stdout: str
    stderr: str


class RunProc(Protocol):
    def __call__(self, cmd: list[str]) -> Completedish: ...


def _subprocess_run(cmd: list[str]) -> Completedish:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        encoding="utf-8",
        errors="replace",  # yt-dlp's output is not ours; never fail on a stray byte
    )


# Every yt-dlp invocation starts here — never build a bare ["yt-dlp", ...].
#
# yt-dlp 2026.07.04 enables ONLY deno as a JavaScript runtime by default, and
# deno is not installed on this box (node and bun are). Without an available
# runtime yt-dlp cannot solve YouTube's signature challenge, so every path that
# downloads MEDIA fails with `HTTP Error 403: Forbidden` — including
# `_ensure_local_media`, which silently costs the entire vision pass. Captions
# still resolve without a runtime, which is what makes the failure look like one
# unlucky video rather than a broken box.
#
# `--js-runtimes` is ADDITIVE, so this only widens the accepted set: deno keeps
# its higher priority and still wins wherever it is installed.
_YT_DLP: tuple[str, ...] = ("yt-dlp", "--js-runtimes", "node")


def _caption_langs(raw: object) -> tuple[str, ...]:
    """Language codes from a yt-dlp `subtitles` / `automatic_captions` mapping."""
    if not isinstance(raw, dict):
        return ()
    return tuple(sorted(str(code) for code in raw))


@dataclass(frozen=True)
class Chapter:
    """One author-declared segment of a video, from yt-dlp's `chapters`."""

    start_s: float
    end_s: float
    title: str


# Titles a leading chapter uses when it recaps prior calls rather than opening new
# content. Matched case-insensitively as substrings, across the languages actually
# present in the follow list (en + zh-Hans/zh-Hant).
#
# This list is the content test, so a hint that merely means "the video starts
# here" belongs nowhere in it. `intro` is excluded (parent #695): a leading chapter
# titled `Intro` running 0-295s of an 1128s video marked the first 26% of an
# educational upload as a position recap -- on a channel configured
# `intro_recap_s: 0`, which is exactly wifey's setting for both live channels.
# Adding it would reproduce that defect here.
# An introduction opens content; a recap replays prior calls, and only the second is
# what this window exists to trim. A channel whose recap chapter really is titled
# `Intro` keeps its per-channel constant, which is the designed fallback -- a false
# positive silently drops Stream C setups, the only stream carrying dated calls.
# `review` and 概述 are the same shape and are unmeasured, kept because both
# routinely do head a genuine recap; treat a sighting on either as this defect again
# rather than as a new one.
_RECAP_TITLE_HINTS: tuple[str, ...] = (
    "recap",
    "review",
    "last week",
    "previous",
    "回顧",
    "回顾",
    "概述",
    "前情",
    "上回",
    "复盘",
    "復盤",
)


def recap_window_s(chapters: tuple[Chapter, ...]) -> float:
    """End of the LEADING run of recap-shaped chapters, or 0.0 when there is none.

    This is the per-video answer to the question `intro_recap_s` answers per channel.
    The constant is hand-tuned from a sample and is wrong on any upload that opens
    differently -- upstream measured a configured 120s against a recap chapter that
    actually ran to 186s, i.e. 66s of recap read as fresh content.

    Only a leading recap counts. A mid-video 回顧 is a different thing, and
    treating it as an intro would swallow the real content before it. Returning 0.0 is
    the honest "no answer here", which leaves the channel constant in charge --
    upstream found chapters on only about half its measured corpus, so degrading
    rather than overriding is the common path, not the edge case.
    """
    end = 0.0
    for chapter in chapters:
        title = chapter.title.casefold()
        if not any(hint.casefold() in title for hint in _RECAP_TITLE_HINTS):
            break
        end = max(end, chapter.end_s)
    return end


def _parse_chapters(raw: object) -> tuple[Chapter, ...]:
    """yt-dlp `chapters` -> Chapter tuple, dropping anything malformed.

    The list is author-supplied, so a partial entry is a live possibility; one bad
    chapter must not cost the whole fetch.
    """
    if not isinstance(raw, list):
        return ()
    out: list[Chapter] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry["start_time"])
            end = float(entry["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(
            Chapter(start_s=start, end_s=end, title=str(entry.get("title") or ""))
        )
    return tuple(out)


@dataclass(frozen=True)
class VideoMeta:
    source: str
    video_id: str
    author: str
    title: str
    publish_ts_utc: str
    duration_s: float
    lang: str
    url: str
    chapters: tuple[Chapter, ...] = ()
    # `subtitles` is author-written, `automatic_captions` is YouTube ASR. Both land
    # on disk as `sub.<code>.vtt`, so the filename cannot tell them apart — these two
    # fields are the ONLY provenance signal, and they come free with the metadata call.
    caption_langs_manual: tuple[str, ...] = ()
    caption_langs_auto: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unavailable:
    reason: str


def parse_video_url(url: str) -> tuple[str, str]:
    match = _YT_RE.search(url)
    if match:
        return "youtube", match.group(1)
    match = _X_RE.search(url)
    if match:
        return "x-video", match.group(1)
    raise ValueError(f"not a supported video URL: {url!r}")


def _numeric_ts(value: object) -> float | None:
    """A usable epoch-seconds field: int/float, explicitly not bool (bool is an int
    subclass, and `timestamp: true` in yt-dlp JSON must be treated as absent, not 1)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _resolve_publish_ts(data: dict[str, object]) -> str | Unavailable:
    """`max(timestamp, release_timestamp)` when both are usable — for a premiere,
    `timestamp` is upload time and `release_timestamp` is when it actually went
    public; taking the earlier value makes the publish upper bound too early, which
    is the look-ahead-permitting direction for `video_calltime.py`'s bound. Falls
    back to `upload_date` (YYYYMMDD) at end-of-day UTC — the same conservative
    end-of-day convention `video_calltime.py` already uses for date-only stated
    times — only when neither numeric field is usable; otherwise `""`.
    """
    candidates = [
        t
        for t in (
            _numeric_ts(data.get("timestamp")),
            _numeric_ts(data.get("release_timestamp")),
        )
        if t is not None
    ]
    if candidates:
        chosen = max(candidates)
        try:
            return datetime.fromtimestamp(chosen, UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return Unavailable(f"unusable timestamp in yt-dlp JSON: {chosen!r}")
    upload_date = data.get("upload_date")
    if isinstance(upload_date, str) and upload_date:
        try:
            day = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return ""
        return day.replace(hour=23, minute=59, second=59).isoformat()
    return ""


def fetch_meta(url: str, *, run: RunProc = _subprocess_run) -> VideoMeta | Unavailable:
    source, video_id = parse_video_url(url)
    proc = run([*_YT_DLP, "--dump-json", "--no-warnings", "--skip-download", url])
    if proc.returncode != 0:
        return Unavailable(proc.stderr.strip() or f"yt-dlp exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Unavailable("yt-dlp returned non-JSON output")
    if not isinstance(data, dict):
        return Unavailable("yt-dlp returned unexpected JSON shape")
    publish = _resolve_publish_ts(data)
    if isinstance(publish, Unavailable):
        return publish
    try:
        duration = float(data.get("duration") or 0.0)
    except (TypeError, ValueError):
        return Unavailable(
            f"unusable duration in yt-dlp JSON: {data.get('duration')!r}"
        )
    return VideoMeta(
        source=source,
        video_id=video_id,
        author=str(data.get("uploader_id") or data.get("uploader") or ""),
        title=str(data.get("title") or ""),
        publish_ts_utc=publish,
        duration_s=duration,
        lang=str(data.get("language") or ""),
        url=url,
        chapters=_parse_chapters(data.get("chapters")),
        caption_langs_manual=_caption_langs(data.get("subtitles")),
        caption_langs_auto=_caption_langs(data.get("automatic_captions")),
    )


GROQ_MAX_BYTES = (
    24 * 1024 * 1024
)  # Groq rejects >25MB; leave headroom for multipart overhead

_VTT_CUE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}"
)
_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Fed to ASR so ticker/TA jargon is not mangled at the source. Cheapest of the three
# transcript-quality layers; the other two live in the vision pass.
_ASR_VOCAB = (
    "FVG, OTE, BOS, CHoCH, liquidity sweep, order block, equal highs, equal lows, "
    "S&P 500, Nasdaq, SPY, QQQ, VIX, NVDA, AAPL, TSLA, MSTR, premarket, after-hours, "
    "earnings, EPS, guidance, FOMC, longs, shorts"
)


class HttpResponse(Protocol):
    status_code: int
    text: str


class HttpPost(Protocol):
    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        files: dict[str, object],
        data: dict[str, str],
    ) -> HttpResponse: ...


def parse_vtt(text: str, lang: str) -> list[TranscriptSegment]:
    """WebVTT cues → segments. Cue start time is the segment timestamp.

    A cue's text may wrap over several lines — YouTube auto-captions routinely do —
    so every line of a cue is joined into one segment. Keeping only the first line
    would silently discard a majority of a real transcript.
    """
    segments: list[TranscriptSegment] = []
    pending_ts: float | None = None
    pending_lines: list[str] = []

    def _flush() -> None:
        nonlocal pending_ts, pending_lines
        if pending_ts is not None and pending_lines:
            segments.append(
                TranscriptSegment(
                    ts_s=pending_ts, text=" ".join(pending_lines), lang=lang
                )
            )
        pending_ts = None
        pending_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        match = _VTT_CUE_RE.search(stripped)
        if match:
            _flush()
            hours, minutes, seconds, millis = (int(g) for g in match.groups())
            pending_ts = hours * 3600 + minutes * 60 + seconds + millis / 1000
            continue
        if not stripped:
            _flush()
            continue
        if pending_ts is None or stripped == "WEBVTT":
            continue
        pending_lines.append(stripped)
    _flush()
    return segments


_SUB_FILENAME_RE = re.compile(r"^sub\.(.+)\.vtt$")


# A caption request longer than this cannot be a targeted one: it is a
# translate matrix, which YouTube answers with HTTP 429 partway through.
_MAX_SUB_LANGS = 6


def _base_lang(code: str) -> str:
    """`en-US` -> `en`. YouTube lists regional variants that no caption code
    matches, while the base language is usually right there in the list."""
    return code.split("-")[0] if code else ""


def _select_caption_track(vtts: list[Path], lang: str) -> Path | None:
    """Pick the right track out of yt-dlp's `--sub-langs` results.

    Preference order: exact `lang`, then `lang-orig`, then the BASE language
    (`pt-BR` -> `pt`), then any code sharing that base (`zh` -> `zh-Hans`), then
    `en`, then whatever carries YouTube's `-orig` suffix, which marks the track
    the video was actually spoken in.

    Two last-resort rules, both measured. A bare `sorted(...)[0]` would close this
    function, and alphabetical order is how `aa` (Afar) beat every other
    candidate on 9avrSmPczP4. So a blind pick happens only when nothing
    knows the language: with a known `lang` and no related track, this returns
    None and the caller falls through to ASR, because ASR in the real language
    beats a machine translation into an unrelated one.
    """
    if not vtts:
        return None
    by_code: dict[str, Path] = {}
    for path in vtts:
        match = _SUB_FILENAME_RE.match(path.name)
        if match:
            by_code[match.group(1)] = path
    base = _base_lang(lang)
    if lang:
        for key in (lang, f"{lang}-orig", base, f"{base}-orig"):
            if key in by_code:
                return by_code[key]
        for code, path in by_code.items():
            if _base_lang(code) == base:
                return path
    if "en" in by_code:
        return by_code["en"]
    for code, path in by_code.items():
        if code.endswith("-orig"):
            return path
    if lang:
        return None
    return vtts[0]


# Where a transcript's text came from. Recorded because every item, `raw_quote` and
# call-time derives from that text, and an ASR transcript is a materially weaker
# source than an author-written one — worst on the zh channels, where ASR is weakest
# and `raw_quote` accuracy is load-bearing.
SOURCE_MANUAL = "manual_captions"
SOURCE_AUTO = "auto_captions"
SOURCE_ASR = "asr_whisper"
# A caption track whose provenance the metadata call did not describe — an old cache
# entry, or a `--dump-json` payload with neither mapping. Deliberately NOT folded into
# `auto`: "we did not ask" and "we asked and it was ASR" are different claims.
SOURCE_CAPTIONS_UNKNOWN = "captions_unknown"
# The metadata listed caption tracks, but the download did not produce them, so the text
# is ASR standing in for a track that SHOULD have been used. Deliberately NOT folded into
# SOURCE_ASR, for the same reason `captions_unknown` is not folded into `auto`: "this
# video has no captions" and "we failed to fetch the captions it has" are different
# claims, and only the second is recoverable by re-running the fetch.
SOURCE_ASR_CAPTIONS_MISSED = "asr_whisper_captions_missed"


@dataclass(frozen=True)
class TranscriptResult:
    segments: list[TranscriptSegment]
    source: str
    lang: str


def _sub_langs(meta: VideoMeta) -> str:
    """The `--sub-langs` request list: the tracks that exist, narrowest first.

    This is ST46's widening fix plus the regression it shipped. ST46 widened
    the request with the codes `--dump-json` said exist, because yt-dlp returns
    `language: null` on a large slice of the follow list and asking for `en`
    alone sent 17 of 89 zh notes to ASR while an author-written track sat
    unrequested. `caption_langs_*` come from that same metadata call, so
    reading them costs no extra request.

    The regression: on a channel whose `meta.lang` is a regional variant with a
    full auto-translate matrix, the widening asked for the matrix. Measured
    2026-08-20 on 9avrSmPczP4 — `language: "en-US"`, no author-written track,
    **157** auto codes led by `ab`/`aa`/`af`, `en` at index 32 and `en-US`
    absent entirely. Re-running that request live answers
    `Downloading subtitles: ab, aa, en` then `HTTP Error 429: Too Many
    Requests`, so the transcript's language was decided by which file survived
    the rate limit: an Afar machine translation, recorded as `auto_captions`.

    Hence two rules. A regional `lang` resolves down to the base track that
    exists (`en-US` -> `en`), and the list is capped, so no video can request a
    translate matrix again. The cap only ever trims the widening tail — the
    resolved language and the author-written tracks are added first.
    """
    known = (*meta.caption_langs_manual, *meta.caption_langs_auto)
    wanted: list[str] = []

    def _add(code: str) -> None:
        if code and code not in wanted:
            wanted.append(code)

    base = _base_lang(meta.lang)
    if meta.lang:
        # With no metadata to check against, the declared language is the only
        # signal there is; when there IS a list, an unlisted code is a request
        # for nothing and must not push the real track down the queue.
        if not known or meta.lang in known:
            _add(meta.lang)
            _add(f"{meta.lang}-orig")
        for code in (base, f"{base}-orig"):
            if code in known:
                _add(code)
    for code in meta.caption_langs_manual:
        _add(code)
    if not wanted:
        # No usable language signal: `-orig` marks the spoken track, so it is a
        # better guess than the head of an alphabetical list.
        for code in known:
            if code.endswith("-orig"):
                _add(code)
        for code in known:
            _add(code)
    _add("en")
    return ",".join(wanted[:_MAX_SUB_LANGS])


def _caption_source(meta: VideoMeta, lang_code: str) -> str:
    """Author-written vs ASR for the CHOSEN track.

    A code can appear in both mappings (measured on 4Dkw1jz04lY, where `zh-Hant` is in
    each). yt-dlp writes the manual track in that case, so manual wins the tie here too.
    """
    if lang_code in meta.caption_langs_manual:
        return SOURCE_MANUAL
    if lang_code in meta.caption_langs_auto:
        return SOURCE_AUTO
    return SOURCE_CAPTIONS_UNKNOWN


# A caption download is retried once: the observed failure mode is a transient HTTP 429
# or network drop, which a second attempt clears. More attempts would turn a rate limit
# into a longer rate limit.
_CAPTION_ATTEMPTS = 2
_CAPTION_RETRY_DELAY_S = 5.0


def _download_captions(
    meta: VideoMeta,
    sub_langs: str,
    work_dir: Path,
    *,
    run: RunProc,
    sleep: Callable[[float], None],
) -> tuple[list[Path], bool]:
    """Download the requested tracks. Returns `(vtts, captions_were_missed)`.

    **The second element is the whole point of this function.** Globbing for
    `sub*.vtt` after discarding yt-dlp's result lets a transient 429 produce an empty
    list byte-identical to the empty list a caption-less video produces, and the ASR
    fallback silently and permanently downgrades a note whose author track is
    sitting right there. Upstream measured this on a video whose own
    metadata listed `en` and `en-orig`: nothing in the selection path was ever broken,
    which is why two earlier caption fixes did not cover it. **Neither was about
    selection**; this one is a line earlier, about the download.

    The discriminator is the metadata, not the exit code alone: `_sub_langs` always
    appends `en` as a last resort, so a caption-less video legitimately requests a track
    that cannot land and yt-dlp still exits 0. A miss is therefore *the metadata listed
    tracks and none arrived*, plus any non-zero exit.
    """
    listed = bool(meta.caption_langs_manual or meta.caption_langs_auto)
    vtts: list[Path] = []
    failed = False
    for attempt in range(_CAPTION_ATTEMPTS):
        proc = run(
            [
                *_YT_DLP,
                "--skip-download",
                "--write-subs",
                "--write-auto-subs",
                "--sub-format",
                "vtt",
                "--sub-langs",
                sub_langs,
                "-o",
                str(work_dir / "sub"),
                meta.url,
            ]
        )
        vtts = sorted(work_dir.glob("sub*.vtt"))
        failed = proc.returncode != 0 or (listed and not vtts)
        if not failed or attempt == _CAPTION_ATTEMPTS - 1:
            break
        sleep(_CAPTION_RETRY_DELAY_S)
    return vtts, failed and not vtts


def fetch_transcript(
    meta: VideoMeta,
    *,
    run: RunProc = _subprocess_run,
    get: HttpPost | None = None,
    groq_key: str | None = None,
    work_dir: Path = Path(".cache/video"),
    sleep: Callable[[float], None] = time.sleep,
) -> TranscriptResult | Unavailable:
    """Existing captions in any language first; Groq whisper-large-v3 only when absent.

    Requests a targeted `--sub-langs` list (never "all" — see `_select_caption_track`)
    and stamps each segment with the CHOSEN file's own language code, never
    `meta.lang` blindly: a video with no `meta.lang` track available may legitimately
    fall back to English captions, and mislabeling that fallback as `meta.lang` would
    silently corrupt `raw_quote`'s language guarantee.

    Returns the provenance alongside the segments rather than leaving a caller to
    re-derive it: re-deriving would mean a second copy of `_select_caption_track`'s
    preference order, and two sites that must mirror each other drift silently.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    sub_langs = _sub_langs(meta)
    vtts, missed = _download_captions(meta, sub_langs, work_dir, run=run, sleep=sleep)
    chosen = _select_caption_track(vtts, meta.lang)
    if chosen is not None:
        match = _SUB_FILENAME_RE.match(chosen.name)
        lang_code = match.group(1) if match else (meta.lang or "en")
        return TranscriptResult(
            segments=parse_vtt(chosen.read_text(encoding="utf-8"), lang=lang_code),
            source=_caption_source(meta, lang_code),
            lang=lang_code,
        )
    if groq_key is None or get is None:
        return Unavailable(
            "caption download failed and no GROQ_API_KEY configured"
            if missed
            else "no captions available and no GROQ_API_KEY configured"
        )
    asr = _transcribe_groq(meta, run=run, get=get, groq_key=groq_key, work_dir=work_dir)
    if isinstance(asr, Unavailable):
        return asr
    return TranscriptResult(
        segments=asr,
        source=SOURCE_ASR_CAPTIONS_MISSED if missed else SOURCE_ASR,
        lang=asr[0].lang if asr else (meta.lang or ""),
    )


def split_audio(
    audio: Path,
    duration_s: float,
    *,
    run: RunProc,
    max_bytes: int = GROQ_MAX_BYTES,
) -> list[tuple[Path, float]]:
    """Split oversized audio into (chunk, time_offset) pairs. Offsets restore absolute
    timestamps after per-chunk transcription."""
    size = audio.stat().st_size
    if size <= max_bytes:
        return [(audio, 0.0)]
    if duration_s <= 0:
        # Without a duration we cannot compute per-chunk offsets, and a chunk at the
        # wrong offset silently mis-times every segment it carries. Refusing to chunk
        # surfaces as Unavailable, which is recoverable; mis-timed segments are not.
        return []
    parts = -(-size // max_bytes)  # ceil
    span = duration_s / parts
    chunks: list[tuple[Path, float]] = []
    for i in range(parts):
        offset = i * span
        out = audio.with_name(f"{audio.stem}_{i:02d}{audio.suffix}")
        proc = run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(offset),
                "-t",
                str(span),
                "-i",
                str(audio),
                "-c",
                "copy",
                str(out),
            ]
        )
        if proc.returncode == 0:
            chunks.append((out, offset))
    return chunks


def _transcribe_groq(
    meta: VideoMeta,
    *,
    run: RunProc,
    get: HttpPost,
    groq_key: str,
    work_dir: Path,
) -> list[TranscriptSegment] | Unavailable:
    audio = work_dir / f"{meta.video_id}.opus"
    proc = run(
        [
            *_YT_DLP,
            "-f",
            "bestaudio",
            "-x",
            "--audio-format",
            "opus",
            "--audio-quality",
            "6",
            "-o",
            str(audio),
            meta.url,
        ]
    )
    if proc.returncode != 0 or not audio.exists():
        return Unavailable(proc.stderr.strip() or "audio extraction failed")
    segments: list[TranscriptSegment] = []
    chunks = split_audio(audio, meta.duration_s, run=run)
    if not chunks:
        return Unavailable("audio chunking failed")
    for chunk, offset in chunks:
        with chunk.open("rb") as handle:
            resp = get(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": handle},
                data={
                    "model": "whisper-large-v3",
                    "response_format": "verbose_json",
                    "prompt": _ASR_VOCAB,
                },
            )
        if resp.status_code != 200:
            return Unavailable(f"Groq HTTP {resp.status_code}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            return Unavailable("Groq returned non-JSON output")
        lang = str(payload.get("language") or meta.lang or "en")
        for entry in payload.get("segments", []):
            try:
                text = str(entry["text"]).strip()
                ts_s = float(entry["start"]) + offset
            except (KeyError, TypeError, ValueError):
                # Malformed segment from Groq (missing/non-numeric field) — skip it
                # rather than crashing the whole transcript; fetch_transcript must
                # degrade, not raise, since a later task calls it inside a batch loop.
                continue
            if text:
                segments.append(TranscriptSegment(ts_s=ts_s, text=text, lang=lang))
    return segments


def _ensure_local_media(
    meta: VideoMeta, dest_dir: Path, *, run: RunProc
) -> Path | None:
    """Download the video once so ffmpeg can seek a local file.

    `meta.url` is a web page (e.g. a YouTube watch URL) — ffmpeg cannot demux that,
    so every `-i meta.url` seek fails silently and `frame_paths` is always
    `[]`. Reused when a `video.*` file already exists in `dest_dir` (e.g. a prior
    call left one behind); returns `None` on a failed/empty download, never raises.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(dest_dir.glob("video.*"))
    if existing:
        return existing[0]
    proc = run(
        [
            *_YT_DLP,
            # Pin the extractor client. Left to its own default selection yt-dlp
            # picks `android_vr` here, which 403s deterministically on the media
            # fetch while captions still resolve — so the vision pass silently
            # loses every frame and the run looks merely unlucky. Measured on
            # 2026.07.04, the stable this repo pinned before the nightly bump:
            # `android`, `mweb` and `web_embedded` all download, `tv` fails to
            # load, and `web_safari`/`ios` cannot serve the format below, so they
            # are not substitutes. The built-in retry cannot help — it was written
            # for an intermittent 403 and this one is not.
            "--extractor-args",
            "youtube:player_client=android",
            "-f",
            "bv*[height<=1080]",
            "-o",
            str(dest_dir / "video.%(ext)s"),
            "--no-playlist",
            meta.url,
        ]
    )
    if proc.returncode != 0:
        return None
    produced = sorted(dest_dir.glob("video.*"))
    return produced[0] if produced else None


def _attempt_frames(
    meta: VideoMeta, marks: list[FrameMark], dest_dir: Path, *, run: RunProc
) -> list[str]:
    local_media = _ensure_local_media(meta, dest_dir, run=run)
    if local_media is None:
        return []
    paths: list[str] = []
    try:
        for mark in marks:
            out = dest_dir / f"f_{int(mark.ts_s):04d}.jpg"
            proc = run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(mark.ts_s),
                    "-i",
                    str(local_media),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(out),
                ]
            )
            if proc.returncode == 0:
                paths.append(str(out))
    finally:
        local_media.unlink(missing_ok=True)
    return paths


# One pause per retry, so 3 attempts total. Measured 2026-08-01: two transient
# `HTTP 403`s survived a single retry and both cleared on a manual re-run — one
# of them on the video carrying that batch's only complete entry+stop+target row. The pause is what makes the extra attempt worth
# anything: a 403 is server-side and returns instantly, so a zero-delay loop
# spends every attempt inside the same bad second. Kept short because a whole
# batch pays this serially, and the ceiling only binds on videos already lost.
_FRAME_RETRY_BACKOFF_S: tuple[float, ...] = (2.0, 5.0)


def extract_frames(
    meta: VideoMeta,
    marks: list[FrameMark],
    dest_dir: Path,
    *,
    run: RunProc = _subprocess_run,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """One ffmpeg seek per mark, against a locally downloaded copy of the video —
    never `meta.url` directly (see `_ensure_local_media`). Never speculative — marks
    come from the transcript pass. The downloaded media is removed once every mark
    has been attempted; frames are the artifact, the source file is not, and the
    operator's backlog makes unbounded video files in `.cache/` a real cost.

    Retries the whole download-and-seek when asked for marks and given back
    nothing, because that outcome is silently expensive: `/ingest-video` reads an
    empty list as a media-download failure, skips the vision pass for that video
    and records a health note, so one transient yt-dlp error costs the entire
    chart read (observed 2026-07-31 — a bare re-run then returned 15/15 frames).
    Total failure is the only retryable shape: a partial result means those marks
    individually failed to seek, and re-downloading to re-fail them is pure cost.

    Attempts are spaced by `_FRAME_RETRY_BACKOFF_S` — see there for why one retry
    proved too few.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths = _attempt_frames(meta, marks, dest_dir, run=run)
    if not marks:
        return paths
    for backoff in _FRAME_RETRY_BACKOFF_S:
        if paths:
            break
        sleep(backoff)
        paths = _attempt_frames(meta, marks, dest_dir, run=run)
    return paths


@dataclass(frozen=True)
class BatchResult:
    url: str
    meta: VideoMeta | Unavailable
    segments: list[TranscriptSegment] = field(default_factory=list)
    frame_paths: list[str] = field(default_factory=list)
    cached: bool = False
    # Non-empty only when `meta` holds a real VideoMeta but the transcript itself
    # could not be produced — distinct from `meta` being Unavailable (video unreachable).
    transcript_error: str = ""
    # One of SOURCE_* — how the text was obtained. "" only when there is no transcript.
    transcript_source: str = ""


def _cache_file(cache_dir: Path, video_id: str) -> Path:
    return cache_dir / video_id / "asset.json"


def _meta_from_cache(raw: dict[str, Any]) -> VideoMeta:
    """Rehydrate VideoMeta from `asdict` output.

    Written out field by field ON PURPOSE. `VideoMeta(**raw)` would store the
    `chapters` field as the plain `list[dict]` `asdict` produced, and the
    `caption_langs_*` fields as plain `list[str]` — a frozen
    dataclass does no coercion, so each field would claim `tuple[str, ...]` while
    holding a list. **mypy cannot see that**: `**` builds the lie at runtime. A
    missing key raises, which `_load_cached` already treats as a cache miss — the
    right answer for an entry we cannot trust.

    `tests/test_video_fetch.py::test_cache_round_trip_covers_every_video_meta_field` pins
    this against VideoMeta's own field list, so adding a field there fails loudly here
    rather than silently dropping it from every cached read.
    """
    return VideoMeta(
        source=str(raw["source"]),
        video_id=str(raw["video_id"]),
        author=str(raw["author"]),
        title=str(raw["title"]),
        publish_ts_utc=str(raw["publish_ts_utc"]),
        duration_s=float(raw["duration_s"]),
        lang=str(raw["lang"]),
        url=str(raw["url"]),
        chapters=tuple(
            c if isinstance(c, Chapter) else Chapter(**c)
            for c in raw.get("chapters") or ()
        ),
        caption_langs_manual=tuple(
            str(c) for c in raw.get("caption_langs_manual") or ()
        ),
        caption_langs_auto=tuple(str(c) for c in raw.get("caption_langs_auto") or ()),
    )


def _load_cached(cache_dir: Path, video_id: str) -> BatchResult | None:
    path = _cache_file(cache_dir, video_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # A cached frame_paths entry can point at a JPEG deleted since it was
        # written (extract_frames downloads real media and can be re-run with
        # a pruned .cache/); a stale path here would silently send pass 2 to a
        # nonexistent file, so drop anything that is not on disk.
        frame_paths = [p for p in data.get("frame_paths", []) if Path(p).exists()]
        return BatchResult(
            url=data["url"],
            meta=_meta_from_cache(data["meta"]),
            segments=[TranscriptSegment(**s) for s in data["segments"]],
            frame_paths=frame_paths,
            cached=True,
            # Absent on every entry written before ST46. "" then means "this cache
            # predates provenance", which is exactly what SOURCE_CAPTIONS_UNKNOWN says
            # downstream — do NOT default it to a real source.
            transcript_source=data.get("transcript_source", ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None  # corrupt cache ⇒ treat as a miss, re-fetch


def _write_cache(cache_dir: Path, result: BatchResult, meta: VideoMeta) -> None:
    path = _cache_file(cache_dir, meta.video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "url": result.url,
                "meta": asdict(meta),
                "segments": [asdict(s) for s in result.segments],
                "frame_paths": result.frame_paths,
                "transcript_source": result.transcript_source,
                "fetched_at_utc": datetime.now(UTC).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def fetch_video_batch(
    urls: list[str],
    *,
    cache_dir: Path = Path(".cache/video"),
    min_delay: float = 4.0,
    max_delay: float = 12.0,
    force: bool = False,
    run: RunProc = _subprocess_run,
    get: HttpPost | None = None,
    groq_key: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> list[BatchResult]:
    """Fetch metadata + transcript per video once, with a randomized cooldown between
    *network* fetches and a per-id dedup cache. Cache hits add no pause; the first
    network fetch is never delayed. One failing video never kills the batch."""
    rng = rng or random.Random()
    results: list[BatchResult] = []
    did_network = False
    for url in urls:
        try:
            _, video_id = parse_video_url(url)
        except ValueError as exc:
            results.append(BatchResult(url=url, meta=Unavailable(str(exc))))
            continue
        if not force:
            cached = _load_cached(cache_dir, video_id)
            if cached is not None:
                results.append(replace(cached, url=url))
                continue
        if did_network:
            sleep(rng.uniform(min_delay, max_delay))
        did_network = True
        try:
            meta = fetch_meta(url, run=run)
            if isinstance(meta, Unavailable):
                results.append(BatchResult(url=url, meta=meta))
                continue
            transcript = fetch_transcript(
                meta,
                run=run,
                get=get,
                groq_key=groq_key,
                work_dir=cache_dir / video_id,
            )
            if isinstance(transcript, Unavailable):
                results.append(
                    BatchResult(url=url, meta=meta, transcript_error=transcript.reason)
                )
                continue
            result = BatchResult(
                url=url,
                meta=meta,
                segments=transcript.segments,
                transcript_source=transcript.source,
            )
            _write_cache(cache_dir, result, meta)
            results.append(result)
        except (OSError, subprocess.SubprocessError) as exc:
            # one bad video never kills the batch. subprocess.TimeoutExpired
            # subclasses SubprocessError, not OSError — _subprocess_run sets
            # timeout=600, so a hung yt-dlp/ffmpeg call must be caught here too,
            # not just a plain OSError.
            results.append(
                BatchResult(url=url, meta=Unavailable(f"{type(exc).__name__}: {exc}"))
            )
    return results


def _result_to_dict(result: BatchResult) -> dict[str, object]:
    base: dict[str, object] = {
        "url": result.url,
        "cached": result.cached,
        "frame_paths": result.frame_paths,
        "transcript_source": result.transcript_source,
    }
    if isinstance(result.meta, Unavailable):
        return {**base, "meta": None, "segments": [], "unavailable": result.meta.reason}
    return {
        **base,
        "meta": asdict(result.meta),
        "segments": [asdict(s) for s in result.segments],
        "unavailable": result.transcript_error or None,
    }


def _requests_post(
    url: str, *, headers: dict[str, str], files: dict[str, object], data: dict[str, str]
) -> HttpResponse:
    # HttpPost's `files: dict[str, object]` is the deliberately-loose injectable-protocol
    # shape (fakes in tests, real file handles in production); requests' own stub wants a
    # narrower Mapping type. Both are runtime-compatible for our one caller (_transcribe_groq
    # passes {"file": <BufferedReader>}), so both codes are ignored at this one boundary.
    return requests.post(  # type: ignore[return-value]
        url,
        headers=headers,
        files=files,  # type: ignore[arg-type]
        data=data,
        timeout=300,
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Fetch video metadata + transcript for /ingest-video (read-only)."
    )
    parser.add_argument("urls", nargs="+", help="one or more YouTube / X video URLs")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--force", action="store_true", help="ignore the dedup cache")
    parser.add_argument("--min-delay", type=float, default=4.0)
    parser.add_argument("--max-delay", type=float, default=12.0)
    parser.add_argument("--cache-dir", default=".cache/video")
    args = parser.parse_args(argv)

    results = fetch_video_batch(
        args.urls,
        cache_dir=Path(args.cache_dir),
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        force=args.force,
        get=_requests_post,
        groq_key=os.environ.get("GROQ_API_KEY"),
    )
    if args.json:
        print(
            json.dumps(
                [_result_to_dict(r) for r in results], indent=2, ensure_ascii=False
            )
        )
    else:
        for r in results:
            if isinstance(r.meta, Unavailable):
                print(f"UNAVAILABLE ({r.meta.reason}): {r.url}")
            elif r.transcript_error:
                print(
                    f"{r.meta.author}  {r.meta.title}  "
                    f"TRANSCRIPT UNAVAILABLE ({r.transcript_error})"
                )
            else:
                tag = " [cached]" if r.cached else ""
                print(
                    f"{r.meta.author}  {r.meta.title}  {r.meta.publish_ts_utc}  "
                    f"{len(r.segments)} segments{tag}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
