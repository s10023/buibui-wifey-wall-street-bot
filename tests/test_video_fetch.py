"""Tests for tools/video_fetch.py — no real network, no real subprocesses."""

from __future__ import annotations

import json
import random
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.video_fetch import (
    GROQ_MAX_BYTES,
    Unavailable,
    VideoMeta,
    _load_cached,
    _result_to_dict,
    extract_frames,
    fetch_meta,
    fetch_transcript,
    fetch_video_batch,
    main,
    parse_video_url,
    parse_vtt,
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

    segments = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(segments, list)
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

    segments = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(segments, list)
    assert segments[1].text == "我在这里做多"
    assert all(s.lang == "zh" for s in segments)


def test_fetch_transcript_matches_lang_prefixed_variant(tmp_path: Path) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        (tmp_path / "sub.zh-Hans.vtt").write_text(VTT)
        return FakeProc(0, "")

    segments = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(segments, list)
    assert segments and all(s.lang == "zh-Hans" for s in segments)


def test_fetch_transcript_falls_back_to_english_without_mislabeling(
    tmp_path: Path,
) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        (tmp_path / "sub.en.vtt").write_text(_EN_VTT)
        return FakeProc(0, "")

    segments = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(segments, list)
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

    segments = fetch_transcript(
        _meta(), run=_run, get=_get, groq_key="fake-key", work_dir=tmp_path
    )
    assert segments == [TranscriptSegment(ts_s=1.0, text="good segment", lang="en")]


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
