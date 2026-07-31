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
from typing import Protocol

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
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)


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
    proc = run(["yt-dlp", "--dump-json", "--no-warnings", "--skip-download", url])
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


def _select_caption_track(vtts: list[Path], lang: str) -> Path | None:
    """Pick the right track out of yt-dlp's `--sub-langs` results.

    `--sub-langs all` used to be requested here, which returns the original track
    plus roughly a hundred machine translations; picking `sorted(...)[0]` then chose
    alphabetically ("af" beats "zh"). Preference order now: exact `lang` match, then
    `lang-orig`, then any code that starts with `lang` (handles `zh-Hans`), then
    `en`, then whatever is left — deterministic over the (already sorted) glob order.
    """
    if not vtts:
        return None
    by_code: dict[str, Path] = {}
    for path in vtts:
        match = _SUB_FILENAME_RE.match(path.name)
        if match:
            by_code[match.group(1)] = path
    if lang:
        if lang in by_code:
            return by_code[lang]
        orig_key = f"{lang}-orig"
        if orig_key in by_code:
            return by_code[orig_key]
        for code, path in by_code.items():
            if code.startswith(lang):
                return path
    if "en" in by_code:
        return by_code["en"]
    return vtts[0]


def fetch_transcript(
    meta: VideoMeta,
    *,
    run: RunProc = _subprocess_run,
    get: HttpPost | None = None,
    groq_key: str | None = None,
    work_dir: Path = Path(".cache/video"),
) -> list[TranscriptSegment] | Unavailable:
    """Existing captions in any language first; Groq whisper-large-v3 only when absent.

    Requests a targeted `--sub-langs` list (never "all" — see `_select_caption_track`)
    and stamps each segment with the CHOSEN file's own language code, never
    `meta.lang` blindly: a video with no `meta.lang` track available may legitimately
    fall back to English captions, and mislabeling that fallback as `meta.lang` would
    silently corrupt `raw_quote`'s language guarantee.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    sub_langs = f"{meta.lang},{meta.lang}-orig,en" if meta.lang else "en"
    run(
        [
            "yt-dlp",
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
    chosen = _select_caption_track(vtts, meta.lang)
    if chosen is not None:
        match = _SUB_FILENAME_RE.match(chosen.name)
        lang_code = match.group(1) if match else (meta.lang or "en")
        return parse_vtt(chosen.read_text(encoding="utf-8"), lang=lang_code)
    if groq_key is None or get is None:
        return Unavailable("no captions available and no GROQ_API_KEY configured")
    return _transcribe_groq(
        meta, run=run, get=get, groq_key=groq_key, work_dir=work_dir
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
            "yt-dlp",
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
    """Download the video once so ffmpeg can seek a LOCAL file.

    `meta.url` is a web page (e.g. a YouTube watch URL) — ffmpeg cannot demux that,
    so every `-i meta.url` seek used to fail silently and `frame_paths` was always
    `[]`. Reused when a `video.*` file already exists in `dest_dir` (e.g. a prior
    call left one behind); returns `None` on a failed/empty download, never raises.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(dest_dir.glob("video.*"))
    if existing:
        return existing[0]
    proc = run(
        [
            "yt-dlp",
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


def extract_frames(
    meta: VideoMeta,
    marks: list[FrameMark],
    dest_dir: Path,
    *,
    run: RunProc = _subprocess_run,
) -> list[str]:
    """One ffmpeg seek per mark, against a locally downloaded copy of the video —
    never `meta.url` directly (see `_ensure_local_media`). Never speculative — marks
    come from the transcript pass. The downloaded media is removed once every mark
    has been attempted; frames are the artifact, the source file is not, and the
    operator's backlog makes unbounded video files in `.cache/` a real cost.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
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


def _cache_file(cache_dir: Path, video_id: str) -> Path:
    return cache_dir / video_id / "asset.json"


def _load_cached(cache_dir: Path, video_id: str) -> BatchResult | None:
    path = _cache_file(cache_dir, video_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        # A cached frame_paths entry can point at a JPEG deleted since it was
        # written (extract_frames now downloads real media and can be re-run with
        # a pruned .cache/); a stale path here would silently send pass 2 to a
        # nonexistent file, so drop anything that no longer exists on disk.
        frame_paths = [p for p in data.get("frame_paths", []) if Path(p).exists()]
        return BatchResult(
            url=data["url"],
            meta=VideoMeta(**data["meta"]),
            segments=[TranscriptSegment(**s) for s in data["segments"]],
            frame_paths=frame_paths,
            cached=True,
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
                "fetched_at_utc": datetime.now(UTC).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        )
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
            segments = fetch_transcript(
                meta,
                run=run,
                get=get,
                groq_key=groq_key,
                work_dir=cache_dir / video_id,
            )
            if isinstance(segments, Unavailable):
                results.append(
                    BatchResult(url=url, meta=meta, transcript_error=segments.reason)
                )
                continue
            result = BatchResult(url=url, meta=meta, segments=segments)
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
