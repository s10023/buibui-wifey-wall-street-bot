"""Tests for tools/x_fetch.py — strict TDD, no real network calls."""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tools.x_fetch import (
    BatchResult,
    Unavailable,
    XPost,
    _format_human,
    _load_cached,
    _orig,
    download_photos,
    fetch_x_batch,
    fetch_x_post,
    main,
    parse_tweet_id,
    walk_thread,
)


@dataclass
class FakeResp:
    status_code: int
    text: str = ""
    content: bytes = b""


def make_get(resp: FakeResp) -> Callable[..., FakeResp]:
    def _get(url: str, *, headers: dict[str, str]) -> FakeResp:
        return resp

    return _get


# ---------------------------------------------------------------------------
# Task 1: parse_tweet_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://x.com/cryptic_heych/status/2071837700500644228?s=20",
            "2071837700500644228",
        ),
        ("https://twitter.com/jack/status/20", "20"),
        ("https://x.com/foo/status/123/", "123"),
    ],
)
def test_parse_tweet_id(url: str, expected: str) -> None:
    assert parse_tweet_id(url) == expected


def test_parse_tweet_id_invalid() -> None:
    with pytest.raises(ValueError):
        parse_tweet_id("https://x.com/cryptic_heych")


# ---------------------------------------------------------------------------
# Task 2: XPost model + fetch_x_post
# ---------------------------------------------------------------------------

_CRYPTIC = {
    "__typename": "Tweet",
    "text": "Today's statistical analysis\n$BTC https://t.co/x",
    "created_at": "2026-06-30T06:06:15.000Z",
    "user": {"name": "HeycH", "screen_name": "cryptic_heych"},
    "photos": [{"url": "https://pbs.twimg.com/media/ABC.jpg"}],
    "mediaDetails": [
        {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/ABC.jpg"}
    ],
}


def test_fetch_maps_fields() -> None:
    url = "https://x.com/cryptic_heych/status/2071837700500644228"
    post = fetch_x_post(url, get=make_get(FakeResp(200, json.dumps(_CRYPTIC))))
    assert isinstance(post, XPost)
    assert post.author == "cryptic_heych"
    assert post.author_name == "HeycH"
    assert post.post_ts_utc == "2026-06-30T06:06:15.000Z"
    assert post.text.startswith("Today's statistical analysis")
    assert post.photo_urls == ("https://pbs.twimg.com/media/ABC.jpg?name=orig",)
    assert post.video_present is False
    assert post.is_thread is False and post.is_quote is False


def test_fetch_video_flag() -> None:
    payload = dict(_CRYPTIC, photos=[], mediaDetails=[{"type": "video"}])
    post = fetch_x_post(
        "https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(payload)))
    )
    assert isinstance(post, XPost)
    assert post.video_present is True
    assert post.photo_urls == ()


def test_fetch_maps_quoted_tweet() -> None:
    payload = dict(
        _CRYPTIC,
        quoted_tweet={
            "text": "original call: long here",
            "user": {"screen_name": "og_caller", "name": "OG"},
        },
    )
    post = fetch_x_post(
        "https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(payload)))
    )
    assert isinstance(post, XPost)
    assert post.is_quote is True
    assert post.quoted_text == "original call: long here"
    assert post.quoted_author == "og_caller"


def test_fetch_no_quoted_tweet_empty_fields() -> None:
    post = fetch_x_post(
        "https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(_CRYPTIC)))
    )
    assert isinstance(post, XPost)
    assert post.is_quote is False
    assert post.quoted_text == ""
    assert post.quoted_author == ""


def test_fetch_tombstone() -> None:
    payload = {"__typename": "TweetTombstone"}
    res = fetch_x_post(
        "https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(payload)))
    )
    assert isinstance(res, Unavailable)


def test_fetch_http_error() -> None:
    res = fetch_x_post("https://x.com/a/status/9", get=make_get(FakeResp(404)))
    assert isinstance(res, Unavailable)


# ---------------------------------------------------------------------------
# Task 3: download_photos
# ---------------------------------------------------------------------------


def test_download_photos_writes_files(tmp_path: Path) -> None:
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url="u",
        post_ts_utc="t",
        text="x",
        photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=False,
    )
    paths = download_photos(
        post, tmp_path, get=make_get(FakeResp(200, content=b"\xff\xd8jpeg"))
    )
    assert paths == [tmp_path / "0.jpg"]
    assert (tmp_path / "0.jpg").read_bytes() == b"\xff\xd8jpeg"


def test_download_photos_skips_errors(tmp_path: Path) -> None:
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url="u",
        post_ts_utc="t",
        text="x",
        photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=False,
    )
    paths = download_photos(post, tmp_path, get=make_get(FakeResp(500)))
    assert paths == []


# ---------------------------------------------------------------------------
# Task 4: CLI / _format_human
# ---------------------------------------------------------------------------


def test_format_human() -> None:
    post = XPost(
        source="twitter",
        author="cryptic_heych",
        author_name="HeycH",
        url="u",
        post_ts_utc="2026-06-30T06:06:15.000Z",
        text="Today's analysis $BTC",
        photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=False,
    )
    out = _format_human(post)
    assert "@cryptic_heych" in out
    assert "Today's analysis $BTC" in out
    assert "photos: 1" in out
    assert "ABC.jpg?name=orig" in out


def test_format_human_shows_quote() -> None:
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url="u",
        post_ts_utc="t",
        text="new take",
        photo_urls=(),
        video_present=False,
        is_thread=False,
        is_quote=True,
        quoted_text="the original call",
        quoted_author="og_caller",
    )
    out = _format_human(post)
    assert "quoting @og_caller" in out
    assert "the original call" in out


# ---------------------------------------------------------------------------
# Fix robustness: non-dict JSON + missing photo url
# ---------------------------------------------------------------------------


def test_fetch_non_dict_json() -> None:
    res = fetch_x_post(
        "https://x.com/a/status/9",
        get=make_get(FakeResp(200, "[1,2,3]")),
    )
    assert isinstance(res, Unavailable)


def test_orig_strips_existing_query() -> None:
    assert (
        _orig("https://pbs.twimg.com/media/ABC.jpg?format=jpg&name=small")
        == "https://pbs.twimg.com/media/ABC.jpg?name=orig"
    )


# ---------------------------------------------------------------------------
# Batch fetch: cooldown + dedup cache
# ---------------------------------------------------------------------------


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, secs: float) -> None:
        self.calls.append(secs)


def _meta(text: str, photos: list[str] | None = None) -> str:
    payload = dict(
        _CRYPTIC,
        text=text,
        photos=[{"url": u} for u in (photos or [])],
        mediaDetails=[],
    )
    return json.dumps(payload)


def make_routed_get(
    meta_by_id: dict[str, FakeResp],
    photo: FakeResp | None = None,
    calls: list[str] | None = None,
) -> Callable[..., FakeResp]:
    def _get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if calls is not None:
            calls.append(url)
        if "tweet-result" in url:
            m = re.search(r"id=(\d+)", url)
            return meta_by_id.get(m.group(1) if m else "", FakeResp(404))
        return photo or FakeResp(404)

    return _get


def _url(tid: str) -> str:
    return f"https://x.com/a/status/{tid}"


def test_batch_jitters_between_network_fetches_only(tmp_path: Path) -> None:
    get = make_routed_get(
        {"1": FakeResp(200, _meta("one")), "2": FakeResp(200, _meta("two"))}
    )
    sleep = RecordingSleep()
    results = fetch_x_batch(
        [_url("1"), _url("2")],
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        min_delay=3.0,
        max_delay=7.0,
        get=get,
        sleep=sleep,
        rng=random.Random(0),
    )
    assert len(results) == 2
    assert all(isinstance(r, BatchResult) for r in results)
    assert all(isinstance(r.post, XPost) and not r.cached for r in results)
    # exactly one jittered pause — between the two network fetches, never before the first
    assert len(sleep.calls) == 1
    assert 3.0 <= sleep.calls[0] <= 7.0


def test_batch_second_run_hits_cache_no_network_no_sleep(tmp_path: Path) -> None:
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    first = fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=make_routed_get({"1": FakeResp(200, _meta("hello"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert first[0].cached is False

    calls: list[str] = []
    sleep = RecordingSleep()
    second = fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=make_routed_get({"1": FakeResp(404)}, calls=calls),  # would 404 if hit
        sleep=sleep,
        rng=random.Random(0),
    )
    assert second[0].cached is True
    assert isinstance(second[0].post, XPost)
    assert second[0].post.text == "hello"
    assert calls == []  # no network at all
    assert sleep.calls == []  # cache hit adds no pause


def test_batch_writes_cache_and_downloads_photos(tmp_path: Path) -> None:
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    get = make_routed_get(
        {
            "5": FakeResp(
                200, _meta("chart", photos=["https://pbs.twimg.com/media/Z.jpg"])
            )
        },
        photo=FakeResp(200, content=b"\xff\xd8img"),
    )
    results = fetch_x_batch(
        [_url("5")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=get,
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert (cache_dir / "5.json").exists()
    downloaded = media_root / "5" / "0.jpg"
    assert downloaded.exists() and downloaded.read_bytes() == b"\xff\xd8img"
    assert results[0].photo_paths == [str(downloaded)]


def test_batch_force_refetches_despite_cache(tmp_path: Path) -> None:
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=make_routed_get({"1": FakeResp(200, _meta("old"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    calls: list[str] = []
    results = fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=media_root,
        force=True,
        get=make_routed_get({"1": FakeResp(200, _meta("new"))}, calls=calls),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert results[0].cached is False
    assert calls  # network was hit
    assert isinstance(results[0].post, XPost) and results[0].post.text == "new"


def test_main_batch_json_emits_array(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    get = make_routed_get(
        {"1": FakeResp(200, _meta("one")), "2": FakeResp(200, _meta("two"))}
    )
    rc = main(
        [
            _url("1"),
            _url("2"),
            "--json",
            "--min-delay",
            "0",
            "--max-delay",
            "0",
            "--cache-dir",
            str(tmp_path / "c"),
            "--media-root",
            str(tmp_path / "m"),
        ],
        get=get,
        sleep=lambda _s: None,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["url"] == _url("1")
    assert out[0]["cached"] is False
    assert out[0]["post"]["text"] == "one"
    assert out[1]["post"]["text"] == "two"


def test_main_single_url_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    get = make_get(FakeResp(200, _meta("solo")))
    rc = main([_url("1"), "--json"], get=get)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, dict)  # single-URL path still emits one object, not an array
    assert out["text"] == "solo"


def test_batch_unavailable_not_cached(tmp_path: Path) -> None:
    cache_dir = tmp_path / "posts"
    results = fetch_x_batch(
        [_url("9")],
        cache_dir=cache_dir,
        media_root=tmp_path / "media",
        get=make_routed_get({"9": FakeResp(404)}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert isinstance(results[0].post, Unavailable)
    assert not (cache_dir / "9.json").exists()


# ---------------------------------------------------------------------------
# Thread walk — recover a self-thread from its tail (spec 2026-08-10)
# ---------------------------------------------------------------------------


def _thread_meta(
    tid: str,
    author: str,
    text: str,
    reply_to: str | None = None,
    reply_to_author: str | None = None,
    conv: int = 0,
) -> str:
    payload = dict(
        _CRYPTIC,
        text=text,
        photos=[],
        mediaDetails=[],
        id_str=tid,
        user={"name": author.upper(), "screen_name": author},
        conversation_count=conv,
    )
    if reply_to is not None:
        payload["in_reply_to_status_id_str"] = reply_to
        payload["in_reply_to_screen_name"] = reply_to_author or author
        payload["parent"] = {"id_str": reply_to, "text": "parent body"}
    return json.dumps(payload)


def _ids(calls: list[str]) -> list[str]:
    out = []
    for u in calls:
        m = re.search(r"id=(\d+)", u)
        if "tweet-result" in u and m:
            out.append(m.group(1))
    return out


def _selfthread() -> dict[str, FakeResp]:
    return {
        "3": FakeResp(200, _thread_meta("3", "a", "leaf", reply_to="2")),
        "2": FakeResp(200, _thread_meta("2", "a", "mid", reply_to="1")),
        "1": FakeResp(200, _thread_meta("1", "a", "root", conv=9)),
    }


def test_walk_follows_chain_in_request_order(tmp_path: Path) -> None:
    calls: list[str] = []
    chain = walk_thread(
        _url("3"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(_selfthread(), calls=calls),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert _ids(calls) == ["3", "2", "1"]  # it really walks, leaf -> root
    assert [p.text for p in chain.posts] == ["root", "mid", "leaf"]  # returned in order
    assert [p.thread_pos for p in chain.posts] == [0, 1, 2]
    assert chain.posts[0].conversation_count == 9


def test_walk_stops_at_root(tmp_path: Path) -> None:
    calls: list[str] = []
    chain = walk_thread(
        _url("1"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(
            {"1": FakeResp(200, _thread_meta("1", "a", "solo"))}, calls=calls
        ),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert [p.text for p in chain.posts] == ["solo"]
    assert _ids(calls) == ["1"]
    assert chain.notes == []


def test_walk_stops_when_author_changes(tmp_path: Path) -> None:
    calls: list[str] = []
    chain = walk_thread(
        _url("2"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(
            {
                "2": FakeResp(
                    200,
                    _thread_meta("2", "a", "reply", reply_to="1", reply_to_author="b"),
                ),
                "1": FakeResp(200, _thread_meta("1", "b", "someone else")),
            },
            calls=calls,
        ),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert [p.text for p in chain.posts] == ["reply"]
    assert _ids(calls) == ["2"]  # never climbed into @b's post
    assert any("@b" in n for n in chain.notes)


def test_walk_hop_cap_is_reported(tmp_path: Path) -> None:
    metas = {
        str(i): FakeResp(200, _thread_meta(str(i), "a", f"p{i}", reply_to=str(i - 1)))
        for i in range(2, 11)
    }
    metas["1"] = FakeResp(200, _thread_meta("1", "a", "p1"))
    chain = walk_thread(
        _url("10"),
        max_hops=3,
        cache_dir=tmp_path / "posts",
        get=make_routed_get(metas),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert len(chain.posts) == 3
    assert any("max_hops" in n for n in chain.notes)


def test_walk_broken_chain_returns_partial(tmp_path: Path) -> None:
    chain = walk_thread(
        _url("3"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(
            {"3": FakeResp(200, _thread_meta("3", "a", "leaf", reply_to="2"))}
        ),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert [p.text for p in chain.posts] == ["leaf"]
    assert any("404" in n for n in chain.notes)


def test_walk_reuses_cached_ancestor_without_network(tmp_path: Path) -> None:
    cache_dir = tmp_path / "posts"
    fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=tmp_path / "media",
        get=make_routed_get({"1": FakeResp(200, _thread_meta("1", "a", "root"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    calls: list[str] = []
    chain = walk_thread(
        _url("2"),
        cache_dir=cache_dir,
        get=make_routed_get(
            {"2": FakeResp(200, _thread_meta("2", "a", "leaf", reply_to="1"))},
            calls=calls,
        ),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert [p.text for p in chain.posts] == ["root", "leaf"]
    assert _ids(calls) == ["2"]  # ancestor served from cache, no network


def test_walk_sleeps_between_network_hops_only(tmp_path: Path) -> None:
    sleeper = RecordingSleep()
    walk_thread(
        _url("3"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(_selfthread()),
        sleep=sleeper,
        rng=random.Random(0),
    )
    assert len(sleeper.calls) == 2  # 3 network fetches, none before the first


def test_pre_change_cache_entry_still_loads(tmp_path: Path) -> None:
    """A cache entry written before the thread fields existed must still load.

    Upstream this guarded 167 real entries; wifey's `.cache/x-posts/` is empty, so
    here it is the positive control for the field defaults rather than a repair.
    `_load_cached` also catches `TypeError` as a miss, so the failure mode would be
    a silent re-fetch, not a crash — which is exactly why it needs asserting.
    """
    cache_dir = tmp_path / "posts"
    cache_dir.mkdir(parents=True)
    legacy = {
        "post": {
            "source": "twitter",
            "author": "a",
            "author_name": "A",
            "url": _url("1"),
            "post_ts_utc": "2026-08-01T00:00:00.000Z",
            "text": "legacy",
            "photo_urls": [],
            "video_present": False,
            "is_thread": False,
            "is_quote": False,
            "quoted_text": "",
            "quoted_author": "",
        },
        "photo_paths": [],
    }
    (cache_dir / "1.json").write_text(json.dumps(legacy), encoding="utf-8")
    loaded = _load_cached(cache_dir, "1")
    assert loaded is not None
    post, _ = loaded
    assert post.text == "legacy"
    assert post.in_reply_to_id == ""
    assert post.in_reply_to_author == ""
    assert post.conversation_count == 0
    assert post.thread_pos == 0


def test_main_thread_flag_emits_chain(tmp_path: Path, capsys: Any) -> None:
    rc = main(
        [_url("3"), "--thread", "--json", "--cache-dir", str(tmp_path / "posts")],
        get=make_routed_get(_selfthread()),
        sleep=RecordingSleep(),
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [p["text"] for p in out["posts"]] == ["root", "mid", "leaf"]
    assert out["notes"] == []


def test_main_thread_human_shows_recovered_count(tmp_path: Path, capsys: Any) -> None:
    rc = main(
        [_url("3"), "--thread", "--cache-dir", str(tmp_path / "posts")],
        get=make_routed_get(_selfthread()),
        sleep=RecordingSleep(),
    )
    assert rc == 0
    assert "thread: 3 posts" in capsys.readouterr().out
