"""Read-only X/Twitter post fetcher via the public syndication endpoint.

No auth, no scraping: hits cdn.syndication.twimg.com/tweet-result (the data path
that powers embedded tweets) and maps the JSON into an XPost. A non-empty `token`
query param is required but its value is not validated by the endpoint, so a fixed
dummy is used (verified 2026-06-30). Mirrors tools/journal_fetch.py: read-only,
HTTP injectable for tests, CLI for ad-hoc use.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import requests

_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
_TOKEN = "a"  # any non-empty value works; the endpoint does not validate it
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_ID_RE = re.compile(r"(?:twitter|x)\.com/[^/]+/status/(\d+)")


class HttpResponse(Protocol):
    status_code: int
    text: str
    content: bytes


class HttpGet(Protocol):
    def __call__(self, url: str, *, headers: dict[str, str]) -> HttpResponse: ...


def _requests_get(url: str, *, headers: dict[str, str]) -> HttpResponse:
    return requests.get(url, headers=headers, timeout=20)  # type: ignore[return-value]


def parse_tweet_id(url: str) -> str:
    match = _ID_RE.search(url)
    if not match:
        raise ValueError(f"not an X/Twitter status URL: {url!r}")
    return match.group(1)


@dataclass(frozen=True)
class XPost:
    source: str
    author: str  # @handle (screen_name)
    author_name: str
    url: str
    post_ts_utc: str
    text: str
    photo_urls: tuple[str, ...]  # full-res (?name=orig)
    video_present: bool
    is_thread: bool
    is_quote: bool
    quoted_text: str = ""  # nested quoted_tweet body, best-effort
    quoted_author: str = ""  # nested quoted_tweet @handle, best-effort
    # Thread fields. Defaults are load-bearing: _load_cached does XPost(**raw) and
    # every cache entry written before these existed lacks the keys.
    in_reply_to_id: str = ""  # the post this one replies to, "" at the root
    in_reply_to_author: str = ""  # @handle replied to; == author on a self-thread
    conversation_count: int = 0  # replies to the CONVERSATION, never thread length
    thread_pos: int = 0  # 0 = root, ascending toward the leaf


@dataclass(frozen=True)
class Unavailable:
    reason: str


def _orig(url: str) -> str:
    base = url.split("?", 1)[0]
    return f"{base}?name=orig"


def fetch_x_post(url: str, *, get: HttpGet = _requests_get) -> XPost | Unavailable:
    tweet_id = parse_tweet_id(url)
    api = f"{_SYNDICATION_URL}?id={tweet_id}&lang=en&token={_TOKEN}"
    resp = get(api, headers={"User-Agent": _UA})
    if resp.status_code != 200:
        return Unavailable(f"HTTP {resp.status_code}")
    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError:
        return Unavailable("non-JSON response")
    if not isinstance(data, dict):
        return Unavailable("unexpected JSON shape")
    if not data or data.get("__typename") == "TweetTombstone" or "text" not in data:
        return Unavailable("post unavailable (protected/deleted/age-gated)")
    media = data.get("mediaDetails") or []
    video_present = any(m.get("type") in ("video", "animated_gif") for m in media)
    photos = tuple(
        _orig(p["url"])
        for p in (data.get("photos") or [])
        if isinstance(p, dict) and p.get("url")
    )
    user = data.get("user") or {}
    quoted = data.get("quoted_tweet") or {}
    quoted_user = quoted.get("user") or {} if isinstance(quoted, dict) else {}
    return XPost(
        source="twitter",
        author=user.get("screen_name", ""),
        author_name=user.get("name", ""),
        url=url,
        post_ts_utc=data.get("created_at", ""),
        text=data.get("text", ""),
        photo_urls=photos,
        video_present=video_present,
        is_thread=data.get("parent") is not None,
        is_quote=data.get("quoted_tweet") is not None,
        quoted_text=quoted.get("text", "") if isinstance(quoted, dict) else "",
        quoted_author=quoted_user.get("screen_name", ""),
        in_reply_to_id=str(data.get("in_reply_to_status_id_str") or ""),
        in_reply_to_author=str(data.get("in_reply_to_screen_name") or ""),
        conversation_count=int(data.get("conversation_count") or 0),
    )


def download_photos(
    post: XPost, dest_dir: Path, *, get: HttpGet = _requests_get
) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, photo_url in enumerate(post.photo_urls):
        resp = get(photo_url, headers={"User-Agent": _UA})
        if resp.status_code != 200:
            continue
        out = dest_dir / f"{i}.jpg"
        out.write_bytes(resp.content)
        paths.append(out)
    return paths


@dataclass(frozen=True)
class BatchResult:
    url: str
    post: XPost | Unavailable
    photo_paths: list[str] = field(default_factory=list)
    cached: bool = False


@dataclass
class ThreadChain:
    """One author's self-thread, root -> leaf, plus why the walk stopped."""

    posts: list[XPost] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def walk_thread(
    url: str,
    *,
    max_hops: int = 25,
    cache_dir: Path = Path(".cache/x-posts"),
    min_delay: float = 4.0,
    max_delay: float = 12.0,
    get: HttpGet = _requests_get,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> ThreadChain:
    """Walk a self-thread UPWARD from its tail and return it root -> leaf.

    The syndication endpoint exposes ``in_reply_to_status_id_str`` but has no
    replies/children field, so a thread can only be recovered from its LAST post
    — bookmarking the parent yields nothing below it.

    Stops at the root, on an author change (climbing further would harvest a
    different pundit's words), at ``max_hops``, or on an unavailable hop. Every
    stop except reaching the root records a note; a truncated chain that reported
    nothing would read as a complete one. Never raises on a broken chain.

    Reads the per-id cache so an ancestor already ingested costs no network. Does
    not WRITE it: cache entries carry ``photo_paths``, and writing one here with
    no photos would make a later ingest of that post skip its chart download.
    """
    rng = rng or random.Random()
    walked: list[XPost] = []
    notes: list[str] = []
    did_network = False
    next_url: str | None = url
    while next_url is not None:
        if len(walked) >= max_hops:
            notes.append(
                f"stopped: max_hops={max_hops} reached — chain truncated, "
                "older posts were NOT fetched"
            )
            break
        tweet_id = parse_tweet_id(next_url)
        cached = _load_cached(cache_dir, tweet_id)
        if cached is not None:
            post = cached[0]
        else:
            if did_network:
                sleep(rng.uniform(min_delay, max_delay))
            did_network = True
            fetched = fetch_x_post(next_url, get=get)
            if isinstance(fetched, Unavailable):
                notes.append(f"stopped at {tweet_id}: {fetched.reason}")
                break
            post = fetched
        walked.append(post)
        if not post.in_reply_to_id:
            break  # root reached — the normal stop, no note
        if post.in_reply_to_author and post.in_reply_to_author != post.author:
            notes.append(
                f"stopped: @{post.author} is replying to @{post.in_reply_to_author}, "
                "not self-threading"
            )
            break
        next_url = f"https://x.com/{post.author}/status/{post.in_reply_to_id}"
    walked.reverse()
    return ThreadChain(
        posts=[replace(p, thread_pos=i) for i, p in enumerate(walked)],
        notes=notes,
    )


def _cache_path(cache_dir: Path, tweet_id: str) -> Path:
    return cache_dir / f"{tweet_id}.json"


def _load_cached(cache_dir: Path, tweet_id: str) -> tuple[XPost, list[str]] | None:
    path = _cache_path(cache_dir, tweet_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = dict(data["post"])
        raw["photo_urls"] = tuple(raw.get("photo_urls", ()))
        return XPost(**raw), list(data.get("photo_paths", []))
    except (json.JSONDecodeError, KeyError, TypeError):
        return None  # corrupt cache ⇒ treat as a miss, re-fetch


def _write_cache(
    cache_dir: Path, tweet_id: str, post: XPost, photo_paths: list[str]
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "post": asdict(post),
        "photo_paths": photo_paths,
        "fetched_at_utc": datetime.now(UTC).isoformat(),
    }
    _cache_path(cache_dir, tweet_id).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def fetch_x_batch(
    urls: list[str],
    *,
    cache_dir: Path = Path(".cache/x-posts"),
    media_root: Path = Path(".cache/x-media"),
    min_delay: float = 4.0,
    max_delay: float = 12.0,
    force: bool = False,
    get: HttpGet = _requests_get,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> list[BatchResult]:
    """Fetch several posts once each, with a randomized cooldown between *network*
    fetches and a per-id dedup cache (re-runs hit zero network). Cache hits add no
    pause; the first network fetch is never delayed. ``sleep``/``rng``/``get`` are
    injected for deterministic tests. Downloads charts once (no double-fetch)."""
    rng = rng or random.Random()
    results: list[BatchResult] = []
    did_network = False
    for url in urls:
        try:
            tweet_id = parse_tweet_id(url)
        except ValueError as exc:
            results.append(BatchResult(url=url, post=Unavailable(str(exc))))
            continue
        if not force:
            cached = _load_cached(cache_dir, tweet_id)
            if cached is not None:
                post, photo_paths = cached
                results.append(
                    BatchResult(
                        url=url, post=post, photo_paths=photo_paths, cached=True
                    )
                )
                continue
        if did_network:
            sleep(rng.uniform(min_delay, max_delay))
        did_network = True
        post_or_err = fetch_x_post(url, get=get)
        if isinstance(post_or_err, Unavailable):
            results.append(BatchResult(url=url, post=post_or_err))
            continue
        photo_paths = [
            str(p) for p in download_photos(post_or_err, media_root / tweet_id, get=get)
        ]
        _write_cache(cache_dir, tweet_id, post_or_err, photo_paths)
        results.append(
            BatchResult(
                url=url, post=post_or_err, photo_paths=photo_paths, cached=False
            )
        )
    return results


def _format_human(post: XPost) -> str:
    lines = [
        f"@{post.author} ({post.author_name})  {post.post_ts_utc}",
        post.text,
        f"photos: {len(post.photo_urls)}  video: {post.video_present}  "
        f"thread: {post.is_thread}  quote: {post.is_quote}",
    ]
    if post.quoted_text:
        lines.append(f"  ↳ quoting @{post.quoted_author}: {post.quoted_text}")
    lines.extend(f"  {u}" for u in post.photo_urls)
    return "\n".join(lines)


def _result_to_dict(result: BatchResult) -> dict[str, object]:
    base: dict[str, object] = {
        "url": result.url,
        "cached": result.cached,
        "photo_paths": result.photo_paths,
    }
    if isinstance(result.post, Unavailable):
        return {**base, "post": None, "unavailable": result.post.reason}
    return {**base, "post": asdict(result.post), "unavailable": None}


def main(
    argv: list[str] | None = None,
    *,
    get: HttpGet = _requests_get,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch one or more X posts via the public syndication endpoint (read-only)."
    )
    parser.add_argument("urls", nargs="+", help="one or more X/Twitter status URLs")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--batch", action="store_true", help="force batch mode for a single URL"
    )
    parser.add_argument("--force", action="store_true", help="ignore the dedup cache")
    parser.add_argument(
        "--thread",
        action="store_true",
        help="walk the self-thread upward from this post (pass the LAST post)",
    )
    parser.add_argument(
        "--min-delay", type=float, default=4.0, help="min cooldown seconds"
    )
    parser.add_argument(
        "--max-delay", type=float, default=12.0, help="max cooldown seconds"
    )
    parser.add_argument("--cache-dir", default=".cache/x-posts", help="dedup cache dir")
    parser.add_argument(
        "--media-root", default=".cache/x-media", help="downloaded-chart dir"
    )
    args = parser.parse_args(argv)

    if args.thread:
        rc = 0
        for url in args.urls:
            chain = walk_thread(
                url,
                cache_dir=Path(args.cache_dir),
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                get=get,
                sleep=sleep,
            )
            if not chain.posts:
                print(f"UNAVAILABLE: {url}", file=sys.stderr)
                rc = 1
                continue
            if args.json:
                print(
                    json.dumps(
                        {
                            "posts": [asdict(p) for p in chain.posts],
                            "notes": chain.notes,
                        },
                        indent=2,
                    )
                )
            else:
                # The recovered count is the review surface: a bookmark that
                # silently expanded into N posts cannot be reviewed as one.
                print(f"thread: {len(chain.posts)} posts @{chain.posts[0].author}")
                for p in chain.posts:
                    print(f"\n--- [{p.thread_pos}] {p.url}\n{_format_human(p)}")
                for note in chain.notes:
                    print(f"\n! {note}", file=sys.stderr)
        return rc

    if len(args.urls) == 1 and not args.batch:
        result = fetch_x_post(args.urls[0], get=get)
        if isinstance(result, Unavailable):
            print(f"UNAVAILABLE: {result.reason}", file=sys.stderr)
            return 1
        print(
            json.dumps(asdict(result), indent=2) if args.json else _format_human(result)
        )
        return 0

    results = fetch_x_batch(
        args.urls,
        cache_dir=Path(args.cache_dir),
        media_root=Path(args.media_root),
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        force=args.force,
        get=get,
        sleep=sleep,
    )
    if args.json:
        print(json.dumps([_result_to_dict(r) for r in results], indent=2))
    else:
        for r in results:
            tag = " [cached]" if r.cached else ""
            if isinstance(r.post, Unavailable):
                print(f"UNAVAILABLE ({r.post.reason}): {r.url}")
            else:
                print(f"{_format_human(r.post)}{tag}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
