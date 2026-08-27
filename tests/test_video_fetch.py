"""Tests for tools/video_fetch.py — no real network, no real subprocesses."""

from __future__ import annotations

import json
import random
import subprocess
from collections.abc import Callable
from dataclasses import MISSING, asdict, dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.video_fetch import (
    GROQ_MAX_BYTES,
    SOURCE_ASR,
    SOURCE_AUTO,
    SOURCE_CAPTIONS_UNKNOWN,
    SOURCE_MANUAL,
    BatchResult,
    Chapter,
    TranscriptResult,
    Unavailable,
    VideoMeta,
    _load_cached,
    _result_to_dict,
    _select_caption_track,
    _sub_langs,
    _write_cache,
    extract_frames,
    fetch_meta,
    fetch_transcript,
    fetch_video_batch,
    main,
    parse_video_url,
    parse_vtt,
    recap_window_s,
    split_audio,
)
from tools.video_marks import FrameMark, TranscriptSegment

YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
X_URL = "https://x.com/someone/status/1234567890"

YTDLP_JSON = json.dumps(
    {
        "id": "dQw4w9WgXcQ",
        "uploader_id": "@cryptoTrader",
        "title": "BTC weekly outlook",
        "timestamp": 1785247200,
        "duration": 2280,
        "language": "zh",
    }
)


@dataclass
class FakeProc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def make_run(proc: FakeProc) -> Callable[..., FakeProc]:
    def _run(cmd: list[str]) -> FakeProc:
        return proc

    return _run


def test_parse_video_url_youtube_watch() -> None:
    assert parse_video_url(YT_URL) == ("youtube", "dQw4w9WgXcQ")


def test_parse_video_url_youtube_short() -> None:
    assert parse_video_url("https://youtu.be/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")


def test_parse_video_url_x() -> None:
    assert parse_video_url(X_URL) == ("x-video", "1234567890")


def test_parse_video_url_rejects_unknown_host() -> None:
    with pytest.raises(ValueError, match="not a supported video URL"):
        parse_video_url("https://example.com/video/1")


def test_fetch_meta_maps_ytdlp_json() -> None:
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, YTDLP_JSON)))
    assert isinstance(meta, VideoMeta)
    assert meta.source == "youtube"
    assert meta.video_id == "dQw4w9WgXcQ"
    assert meta.author == "@cryptoTrader"
    assert meta.duration_s == 2280.0
    assert meta.lang == "zh"
    assert meta.publish_ts_utc.startswith("2026-")


def test_fetch_meta_unavailable_on_nonzero_exit() -> None:
    got = fetch_meta(YT_URL, run=make_run(FakeProc(1, "", "Private video")))
    assert isinstance(got, Unavailable)
    assert "Private video" in got.reason


def test_fetch_meta_unavailable_on_bad_json() -> None:
    got = fetch_meta(YT_URL, run=make_run(FakeProc(0, "not json")))
    assert isinstance(got, Unavailable)
    assert "JSON" in got.reason


def test_fetch_meta_unavailable_on_non_numeric_duration() -> None:
    payload = json.dumps({**json.loads(YTDLP_JSON), "duration": "N/A"})
    got = fetch_meta(YT_URL, run=make_run(FakeProc(0, payload)))
    assert isinstance(got, Unavailable)
    assert "duration" in got.reason


def test_fetch_meta_unavailable_on_out_of_range_timestamp() -> None:
    payload = json.dumps({**json.loads(YTDLP_JSON), "timestamp": 1e20})
    got = fetch_meta(YT_URL, run=make_run(FakeProc(0, payload)))
    assert isinstance(got, Unavailable)
    assert "timestamp" in got.reason


def test_fetch_meta_missing_timestamp_yields_empty_string_not_now() -> None:
    raw = json.loads(YTDLP_JSON)
    del raw["timestamp"]
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, json.dumps(raw))))
    assert isinstance(meta, VideoMeta)
    assert meta.publish_ts_utc == ""


def test_fetch_meta_bool_timestamp_treated_as_absent() -> None:
    payload = json.dumps({**json.loads(YTDLP_JSON), "timestamp": True})
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, payload)))
    assert isinstance(meta, VideoMeta)
    assert meta.publish_ts_utc == ""


# ---------------------------------------------------------------------------
# I2 (final review, 2026-07-28): a premiere's `timestamp` is upload time and
# `release_timestamp` is when it actually went public. Taking the earlier value
# makes the publish upper bound too early — the look-ahead-permitting direction
# for video_calltime.py's stated-time bound — so the LATER of the two wins.
# ---------------------------------------------------------------------------


def test_fetch_meta_release_timestamp_later_than_timestamp_wins() -> None:
    raw = json.loads(YTDLP_JSON)
    raw["timestamp"] = 1785247200
    raw["release_timestamp"] = 1785247200 + 3600  # premiere went live an hour later
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, json.dumps(raw))))
    assert isinstance(meta, VideoMeta)
    expected = datetime.fromtimestamp(raw["release_timestamp"], UTC).isoformat()
    assert meta.publish_ts_utc == expected


def test_fetch_meta_release_timestamp_earlier_than_timestamp_loses() -> None:
    raw = json.loads(YTDLP_JSON)
    raw["timestamp"] = 1785247200
    raw["release_timestamp"] = 1785247200 - 3600
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, json.dumps(raw))))
    assert isinstance(meta, VideoMeta)
    expected = datetime.fromtimestamp(raw["timestamp"], UTC).isoformat()
    assert meta.publish_ts_utc == expected


def test_fetch_meta_bool_release_timestamp_treated_as_absent() -> None:
    raw = json.loads(YTDLP_JSON)
    del raw["timestamp"]
    raw["release_timestamp"] = True
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, json.dumps(raw))))
    assert isinstance(meta, VideoMeta)
    assert meta.publish_ts_utc == ""


def test_fetch_meta_falls_back_to_upload_date_end_of_day() -> None:
    raw = json.loads(YTDLP_JSON)
    del raw["timestamp"]
    raw["upload_date"] = "20260714"
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, json.dumps(raw))))
    assert isinstance(meta, VideoMeta)
    assert meta.publish_ts_utc == "2026-07-14T23:59:59+00:00"


def test_fetch_meta_no_timestamp_fields_at_all_yields_empty_string() -> None:
    raw = json.loads(YTDLP_JSON)
    del raw["timestamp"]
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, json.dumps(raw))))
    assert isinstance(meta, VideoMeta)
    assert meta.publish_ts_utc == ""


VTT = """WEBVTT

00:00:04.000 --> 00:00:07.000
大盘很安静

00:00:10.500 --> 00:00:13.000
我在这里做多
"""


def test_parse_vtt_maps_cues_to_segments() -> None:
    segments = parse_vtt(VTT, lang="zh")
    assert segments == [
        TranscriptSegment(ts_s=4.0, text="大盘很安静", lang="zh"),
        TranscriptSegment(ts_s=10.5, text="我在这里做多", lang="zh"),
    ]


def test_parse_vtt_ignores_header_and_blank_lines() -> None:
    assert parse_vtt("WEBVTT\n\n\n", lang="en") == []


def _meta() -> VideoMeta:
    return VideoMeta(
        source="youtube",
        video_id="dQw4w9WgXcQ",
        author="@cryptoTrader",
        title="BTC weekly outlook",
        publish_ts_utc="2026-07-28T14:00:00+00:00",
        duration_s=2280.0,
        lang="zh",
        url=YT_URL,
    )


def test_fetch_transcript_prefers_captions(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        captured.append(cmd)
        (tmp_path / "sub.zh.vtt").write_text(VTT)
        return FakeProc(0, "")

    result = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(result, TranscriptResult)
    segments = result.segments
    assert segments[1].text == "我在这里做多"
    assert not any("whisper" in " ".join(c) for c in captured)


# ---------------------------------------------------------------------------
# CRITICAL 2 (final review, 2026-07-28): --sub-langs all pulled ~100 machine
# translations alongside the original, and sorted(glob)[0] picked alphabetically
# ("af" beats "zh") — the headline Chinese-video case got an English-derived
# machine translation mislabelled meta.lang="zh". Fix: targeted --sub-langs,
# explicit preference-ordered selection, and lang stamped from the CHOSEN file.
# ---------------------------------------------------------------------------

_AF_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\naf text\n"
_EN_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nen text\n"


def test_fetch_transcript_prefers_matching_lang_over_alphabetical(
    tmp_path: Path,
) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        (tmp_path / "sub.af.vtt").write_text(_AF_VTT)
        (tmp_path / "sub.en.vtt").write_text(_EN_VTT)
        (tmp_path / "sub.zh.vtt").write_text(VTT)
        return FakeProc(0, "")

    result = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(result, TranscriptResult)
    segments = result.segments
    assert segments[1].text == "我在这里做多"
    assert all(s.lang == "zh" for s in segments)


def test_fetch_transcript_matches_lang_prefixed_variant(tmp_path: Path) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        (tmp_path / "sub.zh-Hans.vtt").write_text(VTT)
        return FakeProc(0, "")

    result = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(result, TranscriptResult)
    segments = result.segments
    assert segments and all(s.lang == "zh-Hans" for s in segments)


def test_fetch_transcript_falls_back_to_english_without_mislabeling(
    tmp_path: Path,
) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        (tmp_path / "sub.en.vtt").write_text(_EN_VTT)
        return FakeProc(0, "")

    result = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(result, TranscriptResult)
    segments = result.segments
    assert segments and all(s.lang == "en" for s in segments)


def test_fetch_transcript_requests_targeted_sub_langs_not_all(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        captured.append(cmd)
        (tmp_path / "sub.zh.vtt").write_text(VTT)
        return FakeProc(0, "")

    fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    sub_langs = captured[0][captured[0].index("--sub-langs") + 1]
    assert sub_langs == "zh,zh-orig,en"
    assert sub_langs != "all"


def test_fetch_transcript_unavailable_without_captions_or_key(tmp_path: Path) -> None:
    got = fetch_transcript(_meta(), run=make_run(FakeProc(0, "")), work_dir=tmp_path)
    assert isinstance(got, Unavailable)
    assert "no captions" in got.reason.lower()


def test_split_audio_returns_single_chunk_when_small(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * 1024)
    chunks = split_audio(audio, 600.0, run=make_run(FakeProc(0)))
    assert chunks == [(audio, 0.0)]


def test_split_audio_splits_oversized_and_carries_offsets(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * (GROQ_MAX_BYTES * 2 + 1))
    calls: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        calls.append(cmd)
        return FakeProc(0)

    chunks = split_audio(audio, 900.0, run=_run)
    assert len(chunks) == 3
    assert [round(offset, 1) for _, offset in chunks] == [0.0, 300.0, 600.0]
    assert all(c[0] == "ffmpeg" for c in calls)


def test_split_audio_drops_chunks_ffmpeg_failed_on(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * (GROQ_MAX_BYTES * 2 + 1))
    chunks = split_audio(audio, 900.0, run=make_run(FakeProc(1)))
    assert chunks == []


MULTILINE_VTT = """WEBVTT

00:00:04.000 --> 00:00:07.000
this is the first line
and this is the second

00:00:10.500 --> 00:00:13.000
single line cue
"""


def test_parse_vtt_joins_multi_line_cues() -> None:
    assert parse_vtt(MULTILINE_VTT, lang="en") == [
        TranscriptSegment(
            ts_s=4.0, text="this is the first line and this is the second", lang="en"
        ),
        TranscriptSegment(ts_s=10.5, text="single line cue", lang="en"),
    ]


def test_parse_vtt_joins_cue_without_trailing_blank_line() -> None:
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nline one\nline two\n"
    assert parse_vtt(vtt, lang="en") == [
        TranscriptSegment(ts_s=1.0, text="line one line two", lang="en")
    ]


def test_parse_vtt_ignores_sequence_identifier_lines() -> None:
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nhello\n"
    assert parse_vtt(vtt, lang="en") == [
        TranscriptSegment(ts_s=1.0, text="hello", lang="en")
    ]


def test_split_audio_refuses_to_chunk_without_a_duration(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * (GROQ_MAX_BYTES * 2 + 1))
    assert split_audio(audio, 0.0, run=make_run(FakeProc(0))) == []


def test_split_audio_small_file_ignores_missing_duration(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * 1024)
    assert split_audio(audio, 0.0, run=make_run(FakeProc(0))) == [(audio, 0.0)]


# ---------------------------------------------------------------------------
# Self-review regression: fetch_transcript must degrade, not raise (see
# "Correctness notes" in the task-4 brief) even when Groq's own response is
# malformed — a later task calls this inside a batch loop that must survive
# one bad video.
# ---------------------------------------------------------------------------


@dataclass
class FakeHttpResp:
    status_code: int
    text: str = ""


def test_fetch_transcript_groq_skips_malformed_segments_without_raising(
    tmp_path: Path,
) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        if "-x" in cmd:  # the audio-extraction yt-dlp call
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_bytes(b"x" * 1024)
        return FakeProc(0, "")

    payload = json.dumps(
        {
            "language": "en",
            "segments": [
                {"start": 1.0, "text": "good segment"},
                {"start": "not-a-number", "text": "non-numeric start"},
                {"text": "missing start key entirely"},
                {"start": 2.0},
                {"start": 3.0, "text": "   "},
            ],
        }
    )

    def _get(
        url: str,
        *,
        headers: dict[str, str],
        files: dict[str, object],
        data: dict[str, str],
    ) -> FakeHttpResp:
        return FakeHttpResp(200, payload)

    result = fetch_transcript(
        _meta(), run=_run, get=_get, groq_key="fake-key", work_dir=tmp_path
    )
    assert isinstance(result, TranscriptResult)
    assert result.segments == [
        TranscriptSegment(ts_s=1.0, text="good segment", lang="en")
    ]
    assert result.source == SOURCE_ASR


# ---------------------------------------------------------------------------
# Task 5: extract_frames, dedup cache, batch fetch, CLI
# ---------------------------------------------------------------------------


# extract_frames now downloads the video locally once (ffmpeg cannot demux the
# meta.url web page) before seeking with ffmpeg. This fake mimics yt-dlp's -o
# output-template substitution well enough for a test: it writes a real file at
# <dest_dir>/video.mp4 so _ensure_local_media's post-download glob finds something.
def make_download_run(
    ffmpeg_calls: list[list[str]] | None = None,
    yt_dlp_calls: list[list[str]] | None = None,
    *,
    ffmpeg_returncode: int = 0,
    download_returncode: int = 0,
    write_frame_bytes: bool = False,
) -> Callable[..., FakeProc]:
    def _run(cmd: list[str]) -> FakeProc:
        if cmd[0] == "yt-dlp":
            if yt_dlp_calls is not None:
                yt_dlp_calls.append(cmd)
            if download_returncode == 0:
                template = cmd[cmd.index("-o") + 1]
                out_path = Path(template.replace("%(ext)s", "mp4"))
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"fake video bytes")
            return FakeProc(download_returncode)
        if ffmpeg_calls is not None:
            ffmpeg_calls.append(cmd)
        if ffmpeg_returncode == 0 and write_frame_bytes:
            Path(cmd[-1]).write_bytes(b"jpeg bytes")
        return FakeProc(ffmpeg_returncode)

    return _run


def _no_sleep(_seconds: float) -> None:
    """Skip extract_frames' retry backoff. Any test whose run-fake ends with zero
    frames now walks the full retry ladder, so without this the suite would pay
    the real wall-clock pauses to assert facts about attempt COUNTS."""


def test_extract_frames_one_ffmpeg_call_per_mark(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    marks = [FrameMark(20.0, "item", 3), FrameMark(90.0, "deixis", 2)]
    paths = extract_frames(_meta(), marks, tmp_path, run=make_download_run(calls))
    assert len(calls) == 2
    assert all(c[0] == "ffmpeg" for c in calls)
    assert [Path(p).name for p in paths] == ["f_0020.jpg", "f_0090.jpg"]


def test_extract_frames_skips_failed_grabs(tmp_path: Path) -> None:
    paths = extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_download_run(ffmpeg_returncode=1),
        sleep=_no_sleep,
    )
    assert paths == []


# ---------------------------------------------------------------------------
# CRITICAL 1 (final review, 2026-07-28): extract_frames used to seek meta.url —
# a web page ffmpeg cannot demux — so every grab silently failed. Frames must
# come from a locally downloaded copy, downloaded once and reused across marks.
# ---------------------------------------------------------------------------


def test_extract_frames_ffmpeg_input_is_local_path_not_meta_url(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    extract_frames(
        _meta(), [FrameMark(20.0, "item", 3)], tmp_path, run=make_download_run(calls)
    )
    assert len(calls) == 1
    i_arg = calls[0][calls[0].index("-i") + 1]
    assert i_arg != _meta().url
    assert "video" in i_arg


def test_extract_frames_downloads_once_for_five_marks(tmp_path: Path) -> None:
    ffmpeg_calls: list[list[str]] = []
    yt_dlp_calls: list[list[str]] = []
    marks = [FrameMark(float(i * 10), "item", 3) for i in range(5)]
    extract_frames(
        _meta(),
        marks,
        tmp_path,
        run=make_download_run(ffmpeg_calls, yt_dlp_calls),
    )
    assert len(yt_dlp_calls) == 1
    assert len(ffmpeg_calls) == 5


def test_local_media_download_pins_the_extractor_client(tmp_path: Path) -> None:
    """The download must name its player_client. Left to yt-dlp's own default the
    pick 403s while captions still resolve, so the vision pass loses every frame
    with no loud failure — the shape this asserts against is an ABSENT flag, which
    is why the pair is checked adjacently rather than just for the value."""
    yt_dlp_calls: list[list[str]] = []
    extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_download_run(yt_dlp_calls=yt_dlp_calls),
    )
    assert len(yt_dlp_calls) == 1
    cmd = yt_dlp_calls[0]
    assert "--extractor-args" in cmd
    assert cmd[cmd.index("--extractor-args") + 1] == "youtube:player_client=android"


def test_extract_frames_reuses_existing_local_media(tmp_path: Path) -> None:
    (tmp_path / "video.mp4").write_bytes(b"already downloaded")
    yt_dlp_calls: list[list[str]] = []
    extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_download_run(yt_dlp_calls=yt_dlp_calls),
    )
    assert yt_dlp_calls == []


def test_extract_frames_failed_download_returns_empty_and_calls_no_ffmpeg(
    tmp_path: Path,
) -> None:
    ffmpeg_calls: list[list[str]] = []
    paths = extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_download_run(ffmpeg_calls, download_returncode=1),
        sleep=_no_sleep,
    )
    assert paths == []
    assert ffmpeg_calls == []


def test_extract_frames_deletes_media_but_keeps_frames(tmp_path: Path) -> None:
    paths = extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_download_run(write_frame_bytes=True),
    )
    assert paths == [str(tmp_path / "f_0020.jpg")]
    assert not list(tmp_path.glob("video.*"))
    assert (tmp_path / "f_0020.jpg").exists()


# ---------------------------------------------------------------------------
# Round-2 feed finding (2026-07-31): a transient failure here costs a WHOLE
# vision pass, silently. The skill reads `frame_paths == []`, writes a "frame
# extraction failed (media download error)" health note and skips pass 2
# entirely — yet a bare re-run with no other change returned 15/15 frames.
# So extract_frames retries, and only on total failure.
#
# Round-4 feed finding (2026-08-01): ONE retry was not enough. Two transient
# `HTTP 403`s survived the built-in retry and both cleared on a single MANUAL
# re-run — one of them on the video that produced that batch's only complete
# entry+stop+target row. Round 5 saw zero 403s, so the failure is intermittent,
# not gone. Hence 3 attempts with a short backoff: the failure is server-side
# and immediate, so retrying with no pause just spends all three attempts
# inside the same bad second.
# ---------------------------------------------------------------------------


def make_flaky_run(
    yt_dlp_calls: list[list[str]] | None = None,
    *,
    fail_downloads: int = 0,
    ffmpeg_fails_on_attempts: tuple[int, ...] = (),
) -> Callable[..., FakeProc]:
    """Fake whose failures are keyed to the attempt number (= download count).

    `fail_downloads` fails the first N yt-dlp calls (transient network shape);
    `ffmpeg_fails_on_attempts` instead lets the download succeed but fails every
    seek on those attempts (the corrupt/partial-media shape — the file exists,
    ffmpeg just cannot demux it).
    """
    downloads = 0

    def _run(cmd: list[str]) -> FakeProc:
        nonlocal downloads
        if cmd[0] == "yt-dlp":
            downloads += 1
            if yt_dlp_calls is not None:
                yt_dlp_calls.append(cmd)
            if downloads <= fail_downloads:
                return FakeProc(1)
            out_path = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4"))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fake video bytes")
            return FakeProc(0)
        if downloads in ffmpeg_fails_on_attempts:
            return FakeProc(1)
        Path(cmd[-1]).write_bytes(b"jpeg bytes")
        return FakeProc(0)

    return _run


def test_extract_frames_retries_once_after_a_transient_download_failure(
    tmp_path: Path,
) -> None:
    yt_dlp_calls: list[list[str]] = []
    paths = extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_flaky_run(yt_dlp_calls, fail_downloads=1),
        sleep=_no_sleep,
    )
    assert [Path(p).name for p in paths] == ["f_0020.jpg"]
    assert len(yt_dlp_calls) == 2


def test_extract_frames_retries_once_when_every_seek_fails_on_the_first_copy(
    tmp_path: Path,
) -> None:
    # Download "succeeds" but the media is unusable, so all marks fail. The
    # first attempt's media is unlinked, so the retry re-downloads a good copy.
    yt_dlp_calls: list[list[str]] = []
    paths = extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3), FrameMark(90.0, "deixis", 2)],
        tmp_path,
        run=make_flaky_run(yt_dlp_calls, ffmpeg_fails_on_attempts=(1,)),
        sleep=_no_sleep,
    )
    assert [Path(p).name for p in paths] == ["f_0020.jpg", "f_0090.jpg"]
    assert len(yt_dlp_calls) == 2


def test_extract_frames_retries_at_most_twice(tmp_path: Path) -> None:
    yt_dlp_calls: list[list[str]] = []
    paths = extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_flaky_run(yt_dlp_calls, fail_downloads=99),
        sleep=_no_sleep,
    )
    assert paths == []
    assert len(yt_dlp_calls) == 3


def test_extract_frames_recovers_on_the_third_attempt(tmp_path: Path) -> None:
    # The round-4 shape: two consecutive transient 403s, then success. Under one
    # retry this returned [] and cost the whole vision pass for that video.
    yt_dlp_calls: list[list[str]] = []
    paths = extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_flaky_run(yt_dlp_calls, fail_downloads=2),
        sleep=_no_sleep,
    )
    assert [Path(p).name for p in paths] == ["f_0020.jpg"]
    assert len(yt_dlp_calls) == 3


def test_extract_frames_backs_off_between_retries_only(tmp_path: Path) -> None:
    # One pause per RETRY, never before the first attempt (that would delay every
    # healthy video) and never after the last (nothing is waiting on it). The
    # pause has to be there at all: the 403 is server-side and returns instantly,
    # so a zero-delay retry loop spends all three attempts in the same bad second.
    sleeps: list[float] = []
    extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_flaky_run(fail_downloads=99),
        sleep=sleeps.append,
    )
    assert len(sleeps) == 2
    assert sleeps == sorted(sleeps)
    assert all(0 < s <= 10 for s in sleeps)


def test_extract_frames_does_not_sleep_when_the_first_attempt_works(
    tmp_path: Path,
) -> None:
    sleeps: list[float] = []
    extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_download_run(),
        sleep=sleeps.append,
    )
    assert sleeps == []


def test_extract_frames_retries_keep_the_js_runtime_flag(tmp_path: Path) -> None:
    # The #520 defect class, one layer down: a retry path that rebuilds a bare
    # ["yt-dlp", ...] drops --js-runtimes, so every retry 403s exactly like the
    # attempt it exists to rescue. A network-free suite can only see the SHAPE.
    yt_dlp_calls: list[list[str]] = []
    extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3)],
        tmp_path,
        run=make_flaky_run(yt_dlp_calls, fail_downloads=99),
        sleep=_no_sleep,
    )
    assert len(yt_dlp_calls) == 3
    assert [cmd for cmd in yt_dlp_calls if not _enables_js_runtime(cmd)] == []


def test_extract_frames_does_not_retry_a_partial_success(tmp_path: Path) -> None:
    # One seek lands past the end of the video — a per-mark fact, not a
    # transient one. Re-downloading the whole video to re-fail it is pure cost.
    yt_dlp_calls: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        if cmd[0] == "yt-dlp":
            yt_dlp_calls.append(cmd)
            out_path = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4"))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fake video bytes")
            return FakeProc(0)
        if cmd[cmd.index("-ss") + 1] == "90.0":
            return FakeProc(1)
        Path(cmd[-1]).write_bytes(b"jpeg bytes")
        return FakeProc(0)

    paths = extract_frames(
        _meta(),
        [FrameMark(20.0, "item", 3), FrameMark(90.0, "deixis", 2)],
        tmp_path,
        run=_run,
    )
    assert [Path(p).name for p in paths] == ["f_0020.jpg"]
    assert len(yt_dlp_calls) == 1


def test_extract_frames_without_marks_does_not_retry(tmp_path: Path) -> None:
    # Zero frames is the CORRECT answer for zero marks, not a failure to retry.
    yt_dlp_calls: list[list[str]] = []
    paths = extract_frames(_meta(), [], tmp_path, run=make_flaky_run(yt_dlp_calls))
    assert paths == []
    assert len(yt_dlp_calls) == 1


# fetch_video_batch passes work_dir=cache_dir/<video_id> to fetch_transcript, so a fake
# that writes captions to tmp_path itself would be globbed for in the wrong directory.
# Derive the location from yt-dlp's own -o argument, which is how yt-dlp names sub files.
def make_ytdlp_run(
    calls: list[list[str]] | None = None, fail_substr: str | None = None
) -> Callable[..., FakeProc]:
    def _run(cmd: list[str]) -> FakeProc:
        if calls is not None:
            calls.append(cmd)
        joined = " ".join(cmd)
        if fail_substr is not None and fail_substr in joined:
            return FakeProc(1, "", "Private video")
        if "--dump-json" in cmd:
            return FakeProc(0, YTDLP_JSON)
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.with_name(f"{out.name}.zh.vtt").write_text(VTT, encoding="utf-8")
        return FakeProc(0)

    return _run


def test_batch_second_network_fetch_sleeps_first_does_not(tmp_path: Path) -> None:
    slept: list[float] = []
    fetch_video_batch(
        [YT_URL, "https://youtu.be/AAAAAAAAAAA"],
        cache_dir=tmp_path,
        run=make_ytdlp_run(),
        sleep=slept.append,
        rng=random.Random(0),
    )
    assert len(slept) == 1


def test_batch_cache_hit_does_no_network_and_no_sleep(tmp_path: Path) -> None:
    slept: list[float] = []
    calls: list[list[str]] = []
    run = make_ytdlp_run(calls)

    first_run = fetch_video_batch(
        [YT_URL], cache_dir=tmp_path, run=run, sleep=slept.append
    )
    assert first_run[0].cached is False
    calls_after_first = len(calls)

    results = fetch_video_batch(
        [YT_URL], cache_dir=tmp_path, run=run, sleep=slept.append
    )
    assert len(calls) == calls_after_first
    assert results[0].cached is True
    assert results[0].segments[1].text == "我在这里做多"
    assert slept == []


def test_batch_isolates_one_bad_video(tmp_path: Path) -> None:
    results = fetch_video_batch(
        ["https://youtu.be/BBBBBBBBBBB", YT_URL],
        cache_dir=tmp_path,
        run=make_ytdlp_run(fail_substr="BBBBBBBBBBB"),
    )
    assert isinstance(results[0].meta, Unavailable)
    assert isinstance(results[1].meta, VideoMeta)


# ---------------------------------------------------------------------------
# I1 (final review, 2026-07-28): _subprocess_run sets timeout=600, and
# subprocess.TimeoutExpired subclasses SubprocessError, not OSError — the old
# `except OSError` let one hung yt-dlp abort the whole batch with a traceback,
# contradicting "one bad video never kills the batch".
# ---------------------------------------------------------------------------


def test_batch_survives_a_hung_subprocess_timeout(tmp_path: Path) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        if "CCCCCCCCCCC" in " ".join(cmd):
            raise subprocess.TimeoutExpired(cmd, 600)
        return make_ytdlp_run()(cmd)

    results = fetch_video_batch(
        ["https://youtu.be/CCCCCCCCCCC", YT_URL], cache_dir=tmp_path, run=_run
    )
    assert isinstance(results[0].meta, Unavailable)
    assert isinstance(results[1].meta, VideoMeta)


# ---------------------------------------------------------------------------
# Fix round 1: cache-hit URL must be the URL actually requested at that
# position (not whichever URL first populated the video_id), and a
# transcript-only failure must not discard the already-fetched VideoMeta.
# ---------------------------------------------------------------------------


def test_cache_hit_returns_the_url_actually_requested(tmp_path: Path) -> None:
    run = make_ytdlp_run()
    fetch_video_batch(["https://youtu.be/dQw4w9WgXcQ"], cache_dir=tmp_path, run=run)
    results = fetch_video_batch([YT_URL], cache_dir=tmp_path, run=run)
    assert results[0].cached is True
    assert results[0].url == YT_URL


def test_transcript_failure_keeps_meta_and_reports_separately(tmp_path: Path) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        if "--dump-json" in cmd:
            return FakeProc(0, YTDLP_JSON)
        return FakeProc(0)  # no captions written, no groq key configured

    results = fetch_video_batch([YT_URL], cache_dir=tmp_path, run=_run)
    assert isinstance(results[0].meta, VideoMeta)
    assert results[0].meta.author == "@cryptoTrader"
    assert results[0].transcript_error != ""
    assert results[0].segments == []


def test_result_to_dict_surfaces_transcript_error_as_unavailable(
    tmp_path: Path,
) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        if "--dump-json" in cmd:
            return FakeProc(0, YTDLP_JSON)
        return FakeProc(0)

    results = fetch_video_batch([YT_URL], cache_dir=tmp_path, run=_run)
    payload = _result_to_dict(results[0])
    assert payload["unavailable"]
    assert payload["meta"] is not None


# ---------------------------------------------------------------------------
# Fix round 2: main() must load .env before reading GROQ_API_KEY, or the
# operator's key silently never reaches the Groq fallback, and the resulting
# "no GROQ_API_KEY configured" error misdirects (key is present, just unread).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Deferred-list item (final review, 2026-07-28): now that extract_frames
# downloads real media and produces real JPEGs, a cache hit whose frames were
# since deleted (e.g. a pruned .cache/) must not send pass 2 to nonexistent
# files.
# ---------------------------------------------------------------------------


def test_load_cached_drops_frame_paths_pointing_at_deleted_files(
    tmp_path: Path,
) -> None:
    video_id = "dQw4w9WgXcQ"
    asset_dir = tmp_path / video_id
    asset_dir.mkdir(parents=True)
    existing_frame = asset_dir / "f_0020.jpg"
    existing_frame.write_bytes(b"jpeg")
    missing_frame = asset_dir / "f_0090.jpg"  # never written
    payload = {
        "url": YT_URL,
        "meta": asdict(_meta()),
        "segments": [],
        "frame_paths": [str(existing_frame), str(missing_frame)],
        "fetched_at_utc": "2026-07-28T00:00:00+00:00",
    }
    (asset_dir / "asset.json").write_text(json.dumps(payload))

    cached = _load_cached(tmp_path, video_id)
    assert cached is not None
    assert cached.frame_paths == [str(existing_frame)]


def test_main_loads_dotenv_before_reading_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        "tools.video_fetch.load_dotenv", lambda *a, **k: called.append(True)
    )
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    main(["https://example.com/not-a-video", "--json", "--cache-dir", str(tmp_path)])
    assert called == [True]


# ---------------------------------------------------------------------------
# Round-3 feed finding (2026-07-31): yt-dlp 2026.07.04 enables ONLY deno as a
# JavaScript runtime by default, and deno is not installed on this box (node and
# bun are). Captions still resolve without one, so the failure disguises itself
# as a single unlucky video — but every path that downloads MEDIA dies with
# `HTTP Error 403: Forbidden`, including _ensure_local_media. That returns None,
# extract_frames returns [], and /ingest-video reads the empty list as a media
# failure and skips the whole vision pass. Last session that would have cost all
# six videos their chart correction had it not been caught mid-run.
#
# A network-free suite cannot catch a wrong argument to a real binary, so the
# only thing worth asserting is the command SHAPE, at every call site.
# ---------------------------------------------------------------------------


def _ytdlp_kind(cmd: list[str]) -> str:
    if "--dump-json" in cmd:
        return "meta"
    if "--write-subs" in cmd:
        return "captions"
    if "-x" in cmd:
        return "audio"
    return "media"


def _enables_js_runtime(cmd: list[str]) -> bool:
    return any(
        flag == "--js-runtimes" and value == "node"
        for flag, value in zip(cmd, cmd[1:], strict=False)
    )


def test_every_ytdlp_call_site_enables_an_installed_js_runtime(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        calls.append(cmd)
        if cmd[0] != "yt-dlp":
            return FakeProc(0)
        kind = _ytdlp_kind(cmd)
        if kind == "meta":
            return FakeProc(0, YTDLP_JSON)
        if kind == "audio":
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"x" * 1024)
        elif kind == "media":
            out = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fake video bytes")
        return FakeProc(0)

    def _get(
        url: str,
        *,
        headers: dict[str, str],
        files: dict[str, object],
        data: dict[str, str],
    ) -> FakeHttpResp:
        return FakeHttpResp(200, json.dumps({"language": "zh", "segments": []}))

    # No captions are written, so this walks the Groq fallback too and reaches
    # the audio-extraction call site.
    fetch_meta(YT_URL, run=_run)
    fetch_transcript(_meta(), run=_run, get=_get, groq_key="k", work_dir=tmp_path)
    extract_frames(_meta(), [FrameMark(20.0, "item", 3)], tmp_path, run=_run)

    ytdlp = [cmd for cmd in calls if cmd[0] == "yt-dlp"]
    assert {_ytdlp_kind(cmd) for cmd in ytdlp} == {"meta", "captions", "audio", "media"}
    assert [cmd for cmd in ytdlp if not _enables_js_runtime(cmd)] == []


# ---------------------------------------------------------------------------
# ST46 — `--dump-json` already returns `subtitles` and `automatic_captions` on
# the call `fetch_meta` ALREADY MAKES, so using them costs parsing, not quota.
# Measured upstream 2026-08-20 on three ingested videos:
#   4Dkw1jz04lY  language=None  subtitles=[zh-Hant]
#   f6cUsj7u8nY  language=None  subtitles=[zh]
#   3iHFAoxunzA  language=None  subtitles=[]
# The first line is the defect: with `language` absent, `meta.lang` is "" and the
# old `_sub_langs` asked for `en` alone, so yt-dlp answered "There are no subtitles
# for the requested languages" and a video with an AUTHOR-WRITTEN zh-Hant track fell
# through to ASR — worst exactly where ASR is weakest.
# ---------------------------------------------------------------------------

CAPTION_JSON = json.dumps(
    {
        **json.loads(YTDLP_JSON),
        "language": None,
        "subtitles": {"zh-Hant": [{"ext": "vtt"}]},
        "automatic_captions": {"zh-Hant": [{"ext": "vtt"}], "en": [{"ext": "vtt"}]},
    }
)


# Chapters ride in on the `--dump-json` call `fetch_meta` ALREADY makes, so reading
# them costs parsing, not quota. `CHAPTER_JSON` is also the fixture that keeps
# `test_cache_round_trip_covers_every_video_meta_field` from going vacuous: it is the
# only payload that leaves NO VideoMeta field sitting at its default.
CHAPTER_JSON = json.dumps(
    {
        **json.loads(YTDLP_JSON),
        "language": None,
        "chapters": [
            {"start_time": 0, "title": "策略回顧", "end_time": 186},
            {"start_time": 186, "title": "BTC技術分析", "end_time": 519},
        ],
        "subtitles": {"zh-Hant": [{"ext": "vtt"}]},
        "automatic_captions": {"zh-Hant": [{"ext": "vtt"}], "en": [{"ext": "vtt"}]},
    }
)


def _chapter_payload(*titled: tuple[float, float, str]) -> str:
    return json.dumps(
        {
            **json.loads(YTDLP_JSON),
            "chapters": [
                {"start_time": a, "end_time": b, "title": t} for a, b, t in titled
            ],
        }
    )


def test_fetch_meta_parses_chapters() -> None:
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, CHAPTER_JSON)))
    assert isinstance(meta, VideoMeta)
    assert [c.title for c in meta.chapters] == ["策略回顧", "BTC技術分析"]
    assert meta.chapters[0].start_s == 0.0
    assert meta.chapters[0].end_s == 186.0


def test_fetch_meta_chapters_empty_when_absent() -> None:
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, YTDLP_JSON)))
    assert isinstance(meta, VideoMeta)
    assert meta.chapters == ()


def test_fetch_meta_skips_malformed_chapters_without_raising() -> None:
    """yt-dlp's chapter list is author-supplied, so a partial entry is a live
    possibility and must not cost the whole fetch."""
    payload = json.dumps(
        {
            **json.loads(YTDLP_JSON),
            "chapters": [
                {"title": "no times"},
                {"start_time": 10, "end_time": 20, "title": "good"},
                "not even a dict",
                {"start_time": "x", "end_time": 30, "title": "bad times"},
            ],
        }
    )
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, payload)))
    assert isinstance(meta, VideoMeta)
    assert [c.title for c in meta.chapters] == ["good"]


def test_recap_window_reads_the_leading_recap_chapter() -> None:
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, CHAPTER_JSON)))
    assert isinstance(meta, VideoMeta)
    assert recap_window_s(meta.chapters) == 186.0


def test_recap_window_zero_when_no_chapter_looks_like_a_recap() -> None:
    meta = fetch_meta(
        YT_URL, run=make_run(FakeProc(0, _chapter_payload((0, 300, "認識市場結構"))))
    )
    assert isinstance(meta, VideoMeta)
    assert recap_window_s(meta.chapters) == 0.0


def test_recap_window_zero_without_chapters() -> None:
    """Degrade to the per-channel constant rather than trimming everything."""
    assert recap_window_s(()) == 0.0


def test_recap_window_only_counts_a_LEADING_recap_chapter() -> None:
    """A mid-video recap is a different thing and must not swallow real content."""
    meta = fetch_meta(
        YT_URL,
        run=make_run(
            FakeProc(
                0,
                _chapter_payload((0, 100, "BTC技術分析"), (100, 200, "回顧")),
            )
        ),
    )
    assert isinstance(meta, VideoMeta)
    assert recap_window_s(meta.chapters) == 0.0


def test_recap_window_ignores_a_plain_educational_intro() -> None:
    """Parent #695: a leading chapter titled `Intro` running 0-295s of an 1128s video
    marked the first 26% of an educational upload as a position recap -- on a channel
    configured `intro_recap_s: 0`, which is EXACTLY wifey's setting for both live
    channels. An introduction OPENS content; a recap REPLAYS prior calls."""
    meta = fetch_meta(
        YT_URL, run=make_run(FakeProc(0, _chapter_payload((0, 295, "Intro"))))
    )
    assert isinstance(meta, VideoMeta)
    assert recap_window_s(meta.chapters) == 0.0


def test_recap_window_ignores_a_longer_introduction_title() -> None:
    """The hint matches as a substring, so `Introduction ...` carried the defect too."""
    meta = fetch_meta(
        YT_URL,
        run=make_run(
            FakeProc(0, _chapter_payload((0, 240, "Introduction to Market Structure")))
        ),
    )
    assert isinstance(meta, VideoMeta)
    assert recap_window_s(meta.chapters) == 0.0


def test_recap_window_still_reads_an_intro_that_says_it_recaps() -> None:
    """The narrowing must not cost the real case."""
    meta = fetch_meta(
        YT_URL,
        run=make_run(
            FakeProc(0, _chapter_payload((0, 300, "Intro & Recap of Last Week")))
        ),
    )
    assert isinstance(meta, VideoMeta)
    assert recap_window_s(meta.chapters) == 300.0


def test_cache_round_trip_rehydrates_chapters_as_objects(tmp_path: Path) -> None:
    """`asdict` flattens chapters to dicts and `VideoMeta(**raw)` would store them AS-IS.

    A frozen dataclass does no coercion, so the field would claim `tuple[Chapter, ...]`
    while holding `list[dict]`, and `recap_window_s` would die on `chapter.title` at the
    first cache hit. mypy cannot see it -- `**` builds the lie at runtime.
    """
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, CHAPTER_JSON)))
    assert isinstance(meta, VideoMeta)
    _write_cache(tmp_path, BatchResult(url=YT_URL, meta=meta), meta)
    got = _load_cached(tmp_path, meta.video_id)
    assert got is not None
    assert isinstance(got.meta, VideoMeta)
    assert all(isinstance(c, Chapter) for c in got.meta.chapters)
    # The consumer that would have blown up on raw dicts.
    assert recap_window_s(got.meta.chapters) == 186.0


def test_fetch_meta_records_caption_provenance_sets() -> None:
    """`subtitles` is author-written, `automatic_captions` is ASR. Nothing
    downstream could tell them apart before this — both land as `sub.<code>.vtt`."""
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, CAPTION_JSON)))
    assert isinstance(meta, VideoMeta)
    assert meta.caption_langs_manual == ("zh-Hant",)
    assert meta.caption_langs_auto == ("en", "zh-Hant")


def _meta_no_lang() -> VideoMeta:
    """The measured real shape: yt-dlp returns `language: null`, but the metadata
    call still names an author-written zh-Hant track and an ASR one."""
    return VideoMeta(
        source="youtube",
        video_id="4Dkw1jz04lY",
        author="@GiantCutie-K",
        title="BTC",
        publish_ts_utc="2026-08-17T00:00:00+00:00",
        duration_s=519.0,
        lang="",
        url=YT_URL,
        caption_langs_manual=("zh-Hant",),
        caption_langs_auto=("en", "zh-Hant"),
    )


def test_sub_langs_asks_for_the_track_that_exists_when_language_is_null(
    tmp_path: Path,
) -> None:
    """THE ST46 defect. Before this, `meta.lang == ""` produced `--sub-langs en`
    alone, yt-dlp answered "There are no subtitles for the requested languages",
    and a video with a human-written zh-Hant transcript fell through to ASR."""
    captured: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        captured.append(cmd)
        (tmp_path / "sub.zh-Hant.vtt").write_text(VTT)
        return FakeProc(0, "")

    result = fetch_transcript(_meta_no_lang(), run=_run, work_dir=tmp_path)
    sub_langs = captured[0][captured[0].index("--sub-langs") + 1]
    assert "zh-Hant" in sub_langs.split(",")
    assert sub_langs != "en"
    assert isinstance(result, TranscriptResult)
    assert result.source == SOURCE_MANUAL
    assert result.lang == "zh-Hant"


def test_sub_langs_unchanged_when_language_is_present() -> None:
    """Regression guard: the widening must not disturb the case that already worked."""
    assert _sub_langs(_meta()) == "zh,zh-orig,en"


def test_sub_langs_offers_author_written_codes_before_asr_ones() -> None:
    assert _sub_langs(_meta_no_lang()).split(",")[0] == "zh-Hant"


def test_transcript_source_is_auto_when_only_an_asr_track_matches(
    tmp_path: Path,
) -> None:
    meta = replace(
        _meta_no_lang(), caption_langs_manual=(), caption_langs_auto=("zh-Hant",)
    )

    def _run(cmd: list[str]) -> FakeProc:
        (tmp_path / "sub.zh-Hant.vtt").write_text(VTT)
        return FakeProc(0, "")

    result = fetch_transcript(meta, run=_run, work_dir=tmp_path)
    assert isinstance(result, TranscriptResult)
    assert result.source == SOURCE_AUTO


def test_transcript_source_unknown_is_not_folded_into_auto(tmp_path: Path) -> None:
    """An old cache entry names no caption mappings. "we did not ask" and "we asked
    and it was ASR" are different claims, so they get different values."""

    def _run(cmd: list[str]) -> FakeProc:
        (tmp_path / "sub.zh.vtt").write_text(VTT)
        return FakeProc(0, "")

    result = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(result, TranscriptResult)
    assert result.source == SOURCE_CAPTIONS_UNKNOWN
    assert result.source != SOURCE_AUTO


def test_result_to_dict_carries_transcript_source() -> None:
    """The skill writes the note from this JSON, so provenance has to reach it."""
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, CAPTION_JSON)))
    assert isinstance(meta, VideoMeta)
    out = _result_to_dict(
        BatchResult(url=YT_URL, meta=meta, transcript_source=SOURCE_MANUAL)
    )
    assert out["transcript_source"] == SOURCE_MANUAL
    meta_out = out["meta"]
    assert isinstance(meta_out, dict)
    # asdict keeps tuples as tuples; json.dumps writes either as a JSON array.
    assert meta_out["caption_langs_manual"] == ("zh-Hant",)
    assert json.loads(json.dumps(out, ensure_ascii=False))["meta"][
        "caption_langs_manual"
    ] == ["zh-Hant"]


def test_cache_round_trip_preserves_transcript_source(tmp_path: Path) -> None:
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, CAPTION_JSON)))
    assert isinstance(meta, VideoMeta)
    _write_cache(
        tmp_path,
        BatchResult(url=YT_URL, meta=meta, transcript_source=SOURCE_ASR),
        meta,
    )
    got = _load_cached(tmp_path, meta.video_id)
    assert got is not None
    assert got.transcript_source == SOURCE_ASR


def test_pre_st46_cache_entry_still_loads_and_claims_no_provenance(
    tmp_path: Path,
) -> None:
    """Every cache entry written before ST46 lacks all four keys. It must load, and it
    must NOT claim a source it never measured."""
    path = tmp_path / "oldvid" / "asset.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "url": YT_URL,
                "meta": {
                    "source": "youtube",
                    "video_id": "oldvid",
                    "author": "@a",
                    "title": "t",
                    "publish_ts_utc": "2026-07-01T00:00:00+00:00",
                    "duration_s": 10.0,
                    "lang": "",
                    "url": YT_URL,
                },
                "segments": [],
                "frame_paths": [],
            }
        )
    )
    got = _load_cached(tmp_path, "oldvid")
    assert got is not None
    assert isinstance(got.meta, VideoMeta)
    assert got.transcript_source == ""


def test_cache_round_trip_covers_every_video_meta_field(tmp_path: Path) -> None:
    """`_meta_from_cache` lists VideoMeta's fields by hand, so it can drift behind the
    dataclass. Pin it against the real field list: a new field that is not carried
    through fails HERE rather than silently reading back as its default forever."""
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, CHAPTER_JSON)))
    assert isinstance(meta, VideoMeta)
    # POSITIVE CONTROL on the control. The round-trip assertion below compares the
    # cached value to the fetched one, so any field the FIXTURE leaves at its default
    # compares a default to a default and passes whether or not `_meta_from_cache`
    # carries it. That is not a hypothetical: `chapters` was added with a fixture that
    # had no `chapters` key, and deleting the field from `_meta_from_cache` left this
    # test GREEN. Assert the fixture actually moves every defaulted field first.
    at_default = sorted(
        f.name
        for f in fields(VideoMeta)
        if f.default is not MISSING and getattr(meta, f.name) == f.default
    )
    assert not at_default, (
        "CHAPTER_JSON leaves these fields at their default, so the round-trip "
        f"assertion below cannot fail for them: {at_default}"
    )
    _write_cache(tmp_path, BatchResult(url=YT_URL, meta=meta), meta)
    got = _load_cached(tmp_path, meta.video_id)
    assert got is not None
    assert isinstance(got.meta, VideoMeta)
    for f in fields(VideoMeta):
        assert getattr(got.meta, f.name) == getattr(meta, f.name), f.name


# ---------------------------------------------------------------------------
# The `en-US` regression in ST46's own widening, measured 2026-08-20 on
# 9avrSmPczP4 (@benjaminjcowen). yt-dlp reports `language: "en-US"`, ZERO
# author-written tracks and **157** auto-caption codes led by `ab`, `aa`, `af`;
# `en` sits at index 32 and `en-US` does not exist at all. So the widening
# asked for the WHOLE 157-code translate matrix, and re-running that request
# live returns `Downloading subtitles: ab, aa, en` followed by
# `HTTP Error 429: Too Many Requests` — whichever file lands is then whatever
# survived the rate limit. The ingested transcript was a machine translation
# into Afar, recorded as `auto_captions` with the language silently hidden.
#
# Two independent properties are needed, because either one alone still ships a
# wrong-language transcript: resolve a REGIONAL `meta.lang` down to the base
# track that exists, and never request the matrix in the first place.
# ---------------------------------------------------------------------------

# The head of the real 157-code list, plus the two codes that matter.
_TRANSLATE_MATRIX = (
    "ab",
    "aa",
    "af",
    "ak",
    "sq",
    "am",
    "ar",
    "hy",
    "en",
    "en-orig",
    "zh-Hans",
)


def _meta_regional_lang() -> VideoMeta:
    return VideoMeta(
        source="youtube",
        video_id="9avrSmPczP4",
        author="@benjaminjcowen",
        title="Bitcoin",
        publish_ts_utc="2026-08-19T00:00:00+00:00",
        duration_s=1200.0,
        lang="en-US",
        url=YT_URL,
        caption_langs_manual=(),
        caption_langs_auto=_TRANSLATE_MATRIX,
    )


def test_sub_langs_resolves_a_regional_language_to_its_base_track() -> None:
    """`en-US` matches no caption code; `en` and `en-orig` both exist."""
    requested = _sub_langs(_meta_regional_lang()).split(",")
    assert requested[0] == "en"
    assert "aa" not in requested
    assert "ab" not in requested


def test_sub_langs_never_requests_the_whole_translate_matrix() -> None:
    """The 429 guard. 157 requested codes is what turned one video into a
    partial download whose surviving file decided the transcript's language."""
    requested = _sub_langs(_meta_regional_lang()).split(",")
    assert len(requested) <= 4, requested


def test_select_caption_track_prefers_the_base_language_over_english(
    tmp_path: Path,
) -> None:
    """A `pt-BR` upload with `pt` and `en` tracks must not be read in English."""
    for code in ("en", "pt"):
        (tmp_path / f"sub.{code}.vtt").write_text(VTT)
    chosen = _select_caption_track(sorted(tmp_path.glob("sub*.vtt")), "pt-BR")
    assert chosen is not None
    assert chosen.name == "sub.pt.vtt"


def test_select_caption_track_prefers_an_original_track_to_an_alphabetical_guess(
    tmp_path: Path,
) -> None:
    """`vtts[0]` is alphabetical, which is how `aa` beat the original track."""
    for code in ("aa", "en-orig"):
        (tmp_path / f"sub.{code}.vtt").write_text(VTT)
    chosen = _select_caption_track(sorted(tmp_path.glob("sub*.vtt")), "de-AT")
    assert chosen is not None
    assert chosen.name == "sub.en-orig.vtt"


def test_fetch_transcript_refuses_a_track_unrelated_to_a_KNOWN_language(
    tmp_path: Path,
) -> None:
    """The measured outcome: only `sub.aa.vtt` survived the 429, and an Afar
    machine translation was ingested as the video's transcript. ASR in the real
    language beats a machine translation into an unrelated one, so this must
    fall through rather than return a transcript."""

    def _run(cmd: list[str]) -> FakeProc:
        (tmp_path / "sub.aa.vtt").write_text(VTT)
        return FakeProc(0, "")

    result = fetch_transcript(_meta_regional_lang(), run=_run, work_dir=tmp_path)
    assert isinstance(result, Unavailable)
