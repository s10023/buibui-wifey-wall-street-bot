"""YouTube channel auto-feed backing /ingest-feed (ST10).

Read-only discovery of new uploads across a configured channel list, plus the
explicit-outcome ledger that decides what is "new". Consumption is stamped ONLY
by `mark`, after the /ingest-video review gate routes a batch — `poll` and
`backfill` never write anything. (The historical defect this guards against:
wifey PR #68 watermark-on-send — stamping "seen" at fetch time let an aborted
run permanently consume items.) Mirrors tools/x_fetch.py: HTTP injectable for
tests, CLI for ad-hoc use.

Spec: docs/superpowers/specs/2026-07-31-st10-youtube-feed-design.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import requests
from dotenv import load_dotenv

from tools.video_marks import FRAME_CAP, ITEM_CAP

_API_BASE = "https://www.googleapis.com/youtube/v3"
_PAGE_SIZE = 50
_DEFAULT_COLD_START_DAYS = 14
_DEFAULT_MIN_DURATION_S = 180
_DEFAULT_BACKFILL_MAX = 200
EST_TOKENS_PER_S = 4
EST_TOKENS_PER_FRAME = 1400
EST_FIXED_OVERHEAD = 8000
_STATE_VERSION = 1
DEFAULT_CONFIG_PATH = Path("config/youtube_channels.toml")
DEFAULT_STATE_PATH = Path("docs/plans/yt-feed-state.json")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# YouTube channel ids are always 24 chars: "UC" + 22 of [A-Za-z0-9_-]
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_ISO_DUR_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


@dataclass(frozen=True)
class ChannelConfig:
    id: str
    name: str
    title_include: tuple[str, ...]
    title_exclude: tuple[str, ...]
    min_duration_s: int
    lang: str
    # `handle` exists so /ingest-video can find this row at all. The poll path keys on
    # `id`, but a pasted URL never goes through the poll — all it has is the video's
    # `meta.author` (an @handle), which matches neither `id` nor a CJK display `name`.
    handle: str = ""
    # Seconds of opening recap/teaser to treat as NOT-a-fresh-call. 0 = no such rule.
    # Some channels open every upload by replaying prior positions before saying
    # anything new; a `setup` extracted from that window is a past call wearing
    # today's timestamp. See `intro_recap_s` in the .example for the full rationale.
    intro_recap_s: int = 0
    # Max items /ingest-video keeps from ONE video of this channel, before ranking.
    # Imported from tools/video_marks.py rather than written as a literal, so the
    # default cannot drift from the constant it is supposed to mirror. An aggregator
    # that relays eight traders per upload needs more than a single-voice channel
    # does; specificity ranking has mis-ordered twice (relays over host-own,
    # retrospectives over forward calls), so a too-small cap on a roundup does not
    # merely trim the tail — it silently discards the payload.
    item_cap: int = ITEM_CAP
    # Suppress this channel from `poll` without destroying its state. The only
    # other ways to quiet a channel were `mark --skipped` (permanent — it burns
    # the watermark) and deferring, which re-presents the same candidates every
    # poll; a 13-video backlog therefore re-asked the operator forever.
    # Deliberately scoped to `poll` ONLY: `backfill` still reaches a paused
    # channel (it is the diagnostic that distinguishes a quiet channel from a
    # broken one) and `find_channel_for_author` still resolves it, so a
    # hand-pasted URL keeps its item_cap / intro_recap_s.
    paused: bool = False


@dataclass(frozen=True)
class FeedConfig:
    cold_start_days: int
    channels: tuple[ChannelConfig, ...]


def load_feed_config(path: Path) -> FeedConfig:
    if not path.exists():
        raise SystemExit(
            f"channel config not found: {path} (copy {path}.example and fill it in)"
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    feed = data.get("feed", {})
    channels: list[ChannelConfig] = []
    for raw in data.get("channel", []):
        cid = str(raw.get("id", ""))
        if not cid.startswith("UC"):
            raise SystemExit(
                f"channel id must start with UC (use `yt_feed.py resolve <handle>`): {cid!r}"
            )
        channels.append(
            ChannelConfig(
                id=cid,
                name=str(raw.get("name", cid)),
                title_include=tuple(
                    str(k).lower() for k in raw.get("title_include", [])
                ),
                title_exclude=tuple(
                    str(k).lower() for k in raw.get("title_exclude", [])
                ),
                min_duration_s=int(raw.get("min_duration_s", _DEFAULT_MIN_DURATION_S)),
                lang=str(raw.get("lang", "")),
                handle=str(raw.get("handle", "")),
                intro_recap_s=int(raw.get("intro_recap_s", 0)),
                item_cap=int(raw.get("item_cap", ITEM_CAP)),
                paused=bool(raw.get("paused", False)),
            )
        )
    return FeedConfig(
        cold_start_days=int(feed.get("cold_start_days", _DEFAULT_COLD_START_DAYS)),
        channels=tuple(channels),
    )


def _norm_handle(raw: str) -> str:
    return raw.strip().lstrip("@").casefold()


def channel_hint(
    config: FeedConfig, *, author: str = "", channel_id: str = ""
) -> ChannelConfig | None:
    """Find a configured channel from what /ingest-video actually has.

    Pure. Matches on `channel_id` first (exact, unambiguous), then `handle`, then
    `name` — the last two case-insensitively and ignoring a leading '@'.

    `author` is `VideoMeta.author`, which is an @handle and therefore matches neither
    the `UC…` id the poll path keys on nor a CJK display `name`. That mismatch is why
    the `handle` field exists; without it this lookup silently returns None for every
    Chinese-language channel and the intro-recap rule never fires.
    """
    if channel_id:
        for ch in config.channels:
            if ch.id == channel_id:
                return ch
    if not author:
        return None
    wanted = _norm_handle(author)
    if not wanted:
        return None
    for ch in config.channels:
        if ch.handle and _norm_handle(ch.handle) == wanted:
            return ch
    for ch in config.channels:
        if _norm_handle(ch.name) == wanted:
            return ch
    return None


def is_intro_recap(ts: float, channel: ChannelConfig | None) -> bool:
    """True when `ts` falls in the channel's opening recap/teaser window.

    Deliberately `<` rather than `<=`: `intro_recap_s` names the first second of real
    content, so a channel with no rule (0) never flags anything, including ts=0.0.
    """
    if channel is None or channel.intro_recap_s <= 0:
        return False
    return ts < channel.intro_recap_s


def uploads_playlist_id(channel_id: str) -> str:
    """A channel's uploads playlist is its UC… id with a UU prefix."""
    return "UU" + channel_id[2:]


def parse_iso8601_duration(raw: str) -> int:
    match = _ISO_DUR_RE.match(raw)
    if match is None:
        raise ValueError(f"unparseable ISO-8601 duration: {raw!r}")
    parts = {k: int(v) for k, v in match.groupdict().items() if v is not None}
    return (
        parts.get("d", 0) * 86400
        + parts.get("h", 0) * 3600
        + parts.get("m", 0) * 60
        + parts.get("s", 0)
    )


def estimate_tokens(duration_s: int) -> int:
    """Ranking-grade (±30%) ingest-cost estimate — spec §9."""
    return (
        duration_s * EST_TOKENS_PER_S
        + FRAME_CAP * EST_TOKENS_PER_FRAME
        + EST_FIXED_OVERHEAD
    )


def title_excluded(title: str, channel: ChannelConfig) -> bool:
    """Case-insensitive substring filters; exclude wins over include."""
    low = title.lower()
    if any(k in low for k in channel.title_exclude):
        return True
    return bool(channel.title_include) and not any(
        k in low for k in channel.title_include
    )


def _fresh_state() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "channels": {}, "videos": {}}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _fresh_state()
    try:
        state: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"malformed state file {path}: {exc} — refusing to silently reset "
            "(that would re-queue everything ever ingested); fix or move the file"
        ) from exc
    if (
        not isinstance(state, dict)
        or state.get("version") != _STATE_VERSION
        or not isinstance(state.get("channels"), dict)
        or not isinstance(state.get("videos"), dict)
    ):
        raise SystemExit(
            f"unrecognized state shape/version in {path} — refusing to silently reset"
        )
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def floor_for(
    channel_id: str, state: dict[str, Any], now: datetime, cold_start_days: int
) -> datetime:
    """Static per-channel candidacy floor (spec §3): persisted value wins; else computed.

    Never advanced by any fetch — the entry is persisted only by `mark`.
    """
    entry = state["channels"].get(channel_id)
    if entry is not None:
        return datetime.fromisoformat(entry["floor_ts_utc"])
    return now - timedelta(days=cold_start_days)


def _parse_since(raw: str | None) -> datetime | None:
    """Shared `--since` parser for `poll` and `backfill`.

    A bare date parses tz-naive, and comparing that to the tz-aware floor
    raises TypeError — so naive input is pinned to UTC, matching every other
    timestamp in this module.
    """
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class HttpResponse(Protocol):
    status_code: int
    text: str


class HttpGet(Protocol):
    def __call__(self, url: str, *, params: dict[str, str]) -> HttpResponse: ...


def _requests_get(url: str, *, params: dict[str, str]) -> HttpResponse:
    return requests.get(url, params=params, timeout=20)  # type: ignore[return-value]


class FeedApiError(Exception):
    """A YouTube Data API call failed; message carries HTTP status + API reason."""


_EXCLUDE_REASONS = (
    "below_floor",
    "ledgered",
    "title_filtered",
    "too_short",
    "live_or_upcoming",
    "unavailable",
)


@dataclass(frozen=True)
class Candidate:
    channel_id: str
    channel_name: str
    video_id: str
    url: str
    title: str
    publish_ts_utc: str
    duration_s: int
    age_h: float
    est_tokens: int
    lang_hint: str


@dataclass
class ChannelResult:
    channel_id: str
    channel_name: str
    floor_ts_utc: str
    candidates: list[Candidate] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _api_error_reason(body: str) -> str:
    try:
        payload = json.loads(body)
        reason = payload["error"]["errors"][0]["reason"]
        return str(reason)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return ""


def _api_get(
    get: HttpGet, api_key: str, endpoint: str, params: dict[str, str]
) -> dict[str, Any]:
    resp = get(f"{_API_BASE}/{endpoint}", params={**params, "key": api_key})
    if resp.status_code != 200:
        reason = _api_error_reason(resp.text)
        suffix = f" ({reason})" if reason else ""
        raise FeedApiError(f"{endpoint} HTTP {resp.status_code}{suffix}")
    result: dict[str, Any] = json.loads(resp.text)
    return result


def _fetch_playlist_page(
    get: HttpGet, api_key: str, playlist_id: str, page_token: str | None
) -> dict[str, Any]:
    params = {
        "part": "snippet,contentDetails",
        "playlistId": playlist_id,
        "maxResults": str(_PAGE_SIZE),
    }
    if page_token is not None:
        params["pageToken"] = page_token
    return _api_get(get, api_key, "playlistItems", params)


def _resolve_uploads_id(get: HttpGet, api_key: str, channel_id: str) -> str:
    data = _api_get(
        get, api_key, "channels", {"part": "contentDetails", "id": channel_id}
    )
    items = data.get("items", [])
    if not items:
        raise FeedApiError(f"channel not found: {channel_id}")
    uploads: str = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    return uploads


def _scan_items(
    channel: ChannelConfig,
    items: list[dict[str, Any]],
    *,
    ledger: dict[str, Any],
    floor: datetime | None,
    excluded: dict[str, int],
) -> list[dict[str, Any]]:
    """First-stage filter over playlistItems entries (pre-quota: no API calls here)."""
    survivors: list[dict[str, Any]] = []
    for item in items:
        details = item.get("contentDetails", {})
        snippet = item.get("snippet", {})
        vid = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
        pub_raw = details.get("videoPublishedAt")
        title = str(snippet.get("title", ""))
        if not vid or not pub_raw or title in ("Deleted video", "Private video"):
            excluded["unavailable"] += 1
            continue
        pub = datetime.fromisoformat(pub_raw)
        if floor is not None and pub < floor:
            excluded["below_floor"] += 1
            continue
        if vid in ledger:
            excluded["ledgered"] += 1
            continue
        if title_excluded(title, channel):
            excluded["title_filtered"] += 1
            continue
        survivors.append({"video_id": vid, "title": title, "publish": pub})
    return survivors


def _resolve_durations(
    get: HttpGet,
    api_key: str,
    survivors: list[dict[str, Any]],
    channel: ChannelConfig,
    now: datetime,
    excluded: dict[str, int],
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for start in range(0, len(survivors), _PAGE_SIZE):
        chunk = survivors[start : start + _PAGE_SIZE]
        data = _api_get(
            get,
            api_key,
            "videos",
            {
                "part": "contentDetails,snippet",
                "id": ",".join(s["video_id"] for s in chunk),
            },
        )
        by_id = {v["id"]: v for v in data.get("items", [])}
        for s in chunk:
            video = by_id.get(s["video_id"])
            if video is None:
                excluded["unavailable"] += 1
                continue
            if video.get("snippet", {}).get("liveBroadcastContent", "none") in (
                "live",
                "upcoming",
            ):
                excluded["live_or_upcoming"] += 1
                continue
            try:
                duration_s = parse_iso8601_duration(
                    video.get("contentDetails", {}).get("duration", "")
                )
            except ValueError:
                excluded["unavailable"] += 1
                continue
            if duration_s < channel.min_duration_s:
                excluded["too_short"] += 1
                continue
            publish: datetime = s["publish"]
            candidates.append(
                Candidate(
                    channel_id=channel.id,
                    channel_name=channel.name,
                    video_id=s["video_id"],
                    url=f"https://www.youtube.com/watch?v={s['video_id']}",
                    title=s["title"],
                    publish_ts_utc=publish.isoformat(),
                    duration_s=duration_s,
                    age_h=round((now - publish).total_seconds() / 3600, 1),
                    est_tokens=estimate_tokens(duration_s),
                    lang_hint=channel.lang,
                )
            )
    return candidates


def poll_channel(
    channel: ChannelConfig,
    state: dict[str, Any],
    *,
    now: datetime,
    get: HttpGet,
    api_key: str,
    cold_start_days: int,
    since: datetime | None = None,
) -> ChannelResult:
    """Daily-feed scan of one channel. Strictly read-only — writes nothing.

    `since` can only NARROW the candidacy floor, never widen it: the floor is
    the watermark recording what the operator has already seen, so honouring
    an earlier `--since` would resurface declined videos. Reaching genuinely
    below the floor is `backfill`'s job, which ignores it by design.
    """
    floor = floor_for(channel.id, state, now, cold_start_days)
    if since is not None and since > floor:
        floor = since
    excluded = dict.fromkeys(_EXCLUDE_REASONS, 0)
    result = ChannelResult(
        channel.id, channel.name, floor.isoformat(), [], excluded, []
    )
    try:
        try:
            page = _fetch_playlist_page(
                get, api_key, uploads_playlist_id(channel.id), None
            )
        except FeedApiError as exc:
            if "404" not in str(exc):
                raise
            uploads = _resolve_uploads_id(get, api_key, channel.id)
            page = _fetch_playlist_page(get, api_key, uploads, None)
        survivors = _scan_items(
            channel,
            page.get("items", []),
            ledger=state["videos"],
            floor=floor,
            excluded=excluded,
        )
        result.candidates = _resolve_durations(
            get, api_key, survivors, channel, now, excluded
        )
    except FeedApiError as exc:
        result.errors.append(str(exc))
    return result


def backfill_channel(
    channel: ChannelConfig,
    state: dict[str, Any],
    *,
    now: datetime,
    get: HttpGet,
    api_key: str,
    since: datetime | None,
    max_videos: int,
) -> ChannelResult:
    """Deep back-catalogue scan (spec §5): floor ignored, ledger respected, read-only.

    `since` reuses the "below_floor" exclusion bucket (= "older than --since" here);
    `max_videos` bounds playlist entries examined, keeping quota predictable.
    """
    excluded = dict.fromkeys(_EXCLUDE_REASONS, 0)
    result = ChannelResult(channel.id, channel.name, "", [], excluded, [])
    collected: list[dict[str, Any]] = []
    token: str | None = None
    try:
        uploads = uploads_playlist_id(channel.id)
        while True:
            try:
                page = _fetch_playlist_page(get, api_key, uploads, token)
            except FeedApiError as exc:
                if token is None and "404" in str(exc):
                    uploads = _resolve_uploads_id(get, api_key, channel.id)
                    page = _fetch_playlist_page(get, api_key, uploads, token)
                else:
                    raise
            items = page.get("items", [])
            if not items:
                # guards a malformed API page (nextPageToken present but zero
                # items) from paging forever with no progress
                break
            collected.extend(items[: max_videos - len(collected)])
            token = page.get("nextPageToken")
            last_pub_raw = (
                items[-1].get("contentDetails", {}).get("videoPublishedAt")
                if items
                else None
            )
            past_since = (
                since is not None
                and last_pub_raw is not None
                and datetime.fromisoformat(last_pub_raw) < since
            )
            if token is None or len(collected) >= max_videos or past_since:
                break
        survivors = _scan_items(
            channel, collected, ledger=state["videos"], floor=since, excluded=excluded
        )
        result.candidates = _resolve_durations(
            get, api_key, survivors, channel, now, excluded
        )
    except FeedApiError as exc:
        result.errors.append(str(exc))
    return result


def run_mark(
    state_path: Path,
    *,
    ingested: list[str],
    skipped: list[str],
    channel_seen: list[str],
    candidates_json: Path | None,
    now: datetime,
) -> int:
    """The ONLY state writer. Every entry is an explicit outcome (spec §3)."""
    overlap = set(ingested) & set(skipped)
    if overlap:
        raise SystemExit(
            f"video id(s) in both --ingested and --skipped: {sorted(overlap)}"
        )
    state = load_state(state_path)
    meta: dict[str, dict[str, str]] = {}
    derived_seen: list[str] = []
    if candidates_json is not None:
        payload = json.loads(candidates_json.read_text(encoding="utf-8"))
        for cand in payload.get("candidates", []):
            meta[cand["video_id"]] = {
                "channel_id": cand.get("channel_id", ""),
                "title": cand.get("title", ""),
            }
        # The poll payload's `channels` array already carries both fields
        # --channel-seen wants, so derive the pairs rather than making the
        # operator repeat the flag once per followed channel (9x today).
        # Entries missing either field are skipped: poll always emits both, so
        # their absence means a different payload shape, and the explicit flag
        # stays available for that. These are appended AFTER the explicit
        # pairs because the write below is a setdefault — first writer wins, so
        # an explicit --channel-seen still overrides a derived one.
        for chan in payload.get("channels", []):
            cid_raw = chan.get("channel_id")
            floor_raw = chan.get("floor_ts_utc")
            if cid_raw and floor_raw:
                derived_seen.append(f"{cid_raw}={floor_raw}")
    count = 0
    for status, ids in (("ingested", ingested), ("skipped", skipped)):
        for vid in ids:
            if not _VIDEO_ID_RE.match(vid):
                raise SystemExit(f"not a YouTube video id: {vid!r}")
            enrich = meta.get(vid, {})
            state["videos"][vid] = {
                "status": status,
                "channel_id": enrich.get("channel_id") or None,
                "title": enrich.get("title") or None,
                "decided_ts_utc": now.isoformat(),
            }
            count += 1
    for pair in [*channel_seen, *derived_seen]:
        cid, sep, floor_raw = pair.partition("=")
        if sep != "=" or not _CHANNEL_ID_RE.match(cid) or not floor_raw:
            raise SystemExit(f"bad --channel-seen (want UC…=<iso ts>): {pair!r}")
        try:
            floor_dt = datetime.fromisoformat(floor_raw)
        except ValueError as exc:
            raise SystemExit(f"bad --channel-seen timestamp: {floor_raw!r}") from exc
        if floor_dt.tzinfo is None:
            # mirrors tools/video_calltime.py: a naive timestamp is REJECTED,
            # never assumed-UTC — assuming a zone would silently apply an
            # unrecorded inference, and floor_for later parses this value
            # naive too, which would crash the next poll comparing it against
            # an aware pub timestamp
            raise SystemExit(
                "bad --channel-seen timestamp (naive, no UTC offset — pass "
                "the floor_ts_utc value from poll's JSON output verbatim): "
                f"{floor_raw!r}"
            )
        # setdefault is load-bearing: an existing floor is STATIC and never moves
        state["channels"].setdefault(
            cid, {"added_ts_utc": now.isoformat(), "floor_ts_utc": floor_raw}
        )
    save_state(state_path, state)
    return count


def resolve_handle(get: HttpGet, api_key: str, handle: str) -> str:
    """Handle → ready-to-paste [[channel]] TOML block. Never writes config."""
    normalized = handle if handle.startswith("@") else f"@{handle}"
    data = _api_get(
        get, api_key, "channels", {"part": "id,snippet", "forHandle": normalized}
    )
    items = data.get("items", [])
    if not items:
        raise SystemExit(f"no channel found for handle {normalized!r}")
    cid = items[0]["id"]
    name = items[0]["snippet"]["title"]
    return (
        "[[channel]]\n"
        f'id = "{cid}"\n'
        f'name = "{name}"\n'
        f'handle = "{normalized}"\n'
        "title_include = []\n"
        'title_exclude = ["#shorts"]\n'
        f"min_duration_s = {_DEFAULT_MIN_DURATION_S}\n"
        'lang = ""\n'
        "intro_recap_s = 0\n"
    )


def _results_to_dict(
    results: list[ChannelResult],
    now: datetime,
    paused: tuple[ChannelConfig, ...] = (),
) -> dict[str, Any]:
    return {
        "generated_utc": now.isoformat(),
        "candidates": [asdict(c) for r in results for c in r.candidates],
        # Reported, never omitted: a silently-dropped channel is
        # indistinguishable from a broken one, which is the exact confusion the
        # `backfill`-to-diagnose rule exists to resolve.
        "paused": [{"channel_id": c.id, "channel_name": c.name} for c in paused],
        "channels": [
            {
                "channel_id": r.channel_id,
                "channel_name": r.channel_name,
                "floor_ts_utc": r.floor_ts_utc,
                "excluded": r.excluded,
                "errors": r.errors,
            }
            for r in results
        ],
    }


def _format_human(
    results: list[ChannelResult], paused: tuple[ChannelConfig, ...] = ()
) -> str:
    lines: list[str] = []
    for pc in paused:
        lines.append(f"# PAUSED {pc.name} ({pc.id}) — not polled (paused = true)")
    for r in results:
        drops = ", ".join(f"{k}={v}" for k, v in r.excluded.items() if v)
        lines.append(
            f"# {r.channel_name} ({r.channel_id})"
            + (f" — excluded: {drops}" if drops else "")
        )
        for e in r.errors:
            lines.append(f"  ERROR: {e}")
        for c in r.candidates:
            mins = c.duration_s // 60
            lines.append(
                f"  {c.video_id}  {mins:>4}m  {c.age_h:>7.1f}h  ~{c.est_tokens // 1000}k tok  {c.title}"
            )
    total = sum(len(r.candidates) for r in results)
    lines.append(f"# {total} candidate(s)")
    return "\n".join(lines)


_ID_LIST_FLAGS = ("--ingested", "--skipped")


def _extract_id_lists(argv: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Pull ``--ingested`` / ``--skipped`` values out of argv before argparse sees them.

    A YouTube id may legitimately start with ``-`` (``-mx3UwwJ5P4``), and argparse
    reads that as a flag. With ``nargs="*"`` the value is **silently dropped** and
    ``mark`` records nothing — and ``mark`` is the only writer of consumption state,
    so a lost id re-presents that video forever with no other symptom. The ``=``
    form does not rescue it either: ``nargs="*"`` carries only ONE value that way,
    so ``--ingested=-mx3UwwJ5P4 abc`` loses ``abc``.

    Values run until the next ``--``-prefixed token. Every flag on the ``mark``
    parser is long-form, so no 11-character id can be mistaken for one. Both call
    shapes survive: ``--ingested a b c`` and ``--ingested=-mx3UwwJ5P4``.
    """
    found: dict[str, list[str]] = {f.lstrip("-"): [] for f in _ID_LIST_FLAGS}
    rest: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        flag = next(
            (f for f in _ID_LIST_FLAGS if tok == f or tok.startswith(f + "=")), None
        )
        if flag is None:
            rest.append(tok)
            i += 1
            continue
        key = flag.lstrip("-")
        if tok.startswith(flag + "="):
            found[key].append(tok[len(flag) + 1 :])
            i += 1
            continue
        i += 1
        while i < len(argv) and not argv[i].startswith("--"):
            found[key].append(argv[i])
            i += 1
    return found, rest


def main(
    argv: list[str] | None = None,
    *,
    get: HttpGet = _requests_get,
    now: datetime | None = None,
) -> int:
    load_dotenv()  # YOUTUBE_API_KEY may live only in .env
    parser = argparse.ArgumentParser(
        description="YouTube channel auto-feed for /ingest-feed (read-only except `mark`)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_poll = sub.add_parser(
        "poll", help="list new uploads across the configured channels"
    )
    p_poll.add_argument(
        "--since",
        default=None,
        help="ISO date/ts; NARROWS the floor only (an earlier value is ignored — "
        "use `backfill` to reach below it)",
    )
    p_back = sub.add_parser(
        "backfill", help="page a channel's deep back-catalogue (floor ignored)"
    )
    p_back.add_argument("channel_id", help="UC… id; must exist in the channel config")
    p_back.add_argument(
        "--since", default=None, help="ISO date/ts; stop at older uploads"
    )
    p_back.add_argument(
        "--max-videos",
        type=int,
        default=_DEFAULT_BACKFILL_MAX,
        help="max playlist entries examined (quota bound)",
    )
    for p in (p_poll, p_back):
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
        p.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
        p.add_argument("--json", action="store_true", dest="as_json")

    p_mark = sub.add_parser(
        "mark", help="record explicit outcomes (the ONLY state writer)"
    )
    p_mark.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    p_mark.add_argument("--ingested", nargs="*", default=[])
    p_mark.add_argument("--skipped", nargs="*", default=[])
    p_mark.add_argument(
        "--channel-seen",
        action="append",
        default=[],
        help="UC…=<floor iso ts> — persists a channel entry (setdefault only)",
    )
    p_mark.add_argument(
        "--candidates-json",
        type=Path,
        default=None,
        help="poll/backfill --json output; enriches ledger rows",
    )

    p_res = sub.add_parser(
        "resolve", help="handle → ready-to-paste [[channel]] TOML block"
    )
    p_res.add_argument("handle")

    p_hint = sub.add_parser(
        "hint",
        help="per-channel ingest hints (intro_recap_s) for /ingest-video; needs no API key",
    )
    p_hint.add_argument(
        "--author", default="", help="VideoMeta.author, e.g. @GiantCutie-K"
    )
    p_hint.add_argument("--channel-id", default="", help="UC… id, if known")
    p_hint.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    # `--ingested` / `--skipped` are extracted BEFORE argparse so a `-`-leading
    # video id survives; they stay declared above only so `--help` lists them.
    id_lists, argv_rest = _extract_id_lists(
        list(argv) if argv is not None else sys.argv[1:]
    )
    args = parser.parse_args(argv_rest)
    args.ingested = id_lists["ingested"]
    args.skipped = id_lists["skipped"]
    now_dt = now if now is not None else datetime.now(UTC)

    if args.cmd == "mark":
        count = run_mark(
            args.state,
            ingested=args.ingested,
            skipped=args.skipped,
            channel_seen=args.channel_seen,
            candidates_json=args.candidates_json,
            now=now_dt,
        )
        print(f"marked {count} video(s) in {args.state}")
        return 0

    if args.cmd == "hint":
        # Pure local config read — deliberately ahead of the API-key gate below, so
        # /ingest-video can ask for a hint without YOUTUBE_API_KEY set.
        match = channel_hint(
            load_feed_config(args.config),
            author=args.author,
            channel_id=args.channel_id,
        )
        print(
            json.dumps(
                {
                    "matched": match is not None,
                    "channel_id": match.id if match else "",
                    "name": match.name if match else "",
                    "handle": match.handle if match else "",
                    "intro_recap_s": match.intro_recap_s if match else 0,
                    "item_cap": match.item_cap if match else ITEM_CAP,
                },
                ensure_ascii=False,
            )
        )
        return 0

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        print(
            "YOUTUBE_API_KEY is not set — add it to .env (see .env.example)",
            file=sys.stderr,
        )
        return 2

    if args.cmd == "resolve":
        print(resolve_handle(get, api_key, args.handle), end="")
        return 0

    cfg = load_feed_config(args.config)
    state = load_state(args.state)
    paused: tuple[ChannelConfig, ...] = ()
    if args.cmd == "poll":
        paused = tuple(ch for ch in cfg.channels if ch.paused)
        poll_since = _parse_since(args.since)
        results = [
            poll_channel(
                ch,
                state,
                now=now_dt,
                get=get,
                api_key=api_key,
                cold_start_days=cfg.cold_start_days,
                since=poll_since,
            )
            for ch in cfg.channels
            if not ch.paused
        ]
    else:  # backfill — deliberately reaches paused channels too
        by_id = {ch.id: ch for ch in cfg.channels}
        channel = by_id.get(args.channel_id)
        if channel is None:
            raise SystemExit(
                f"channel {args.channel_id} not in config {args.config} — add it first "
                "(filters live in config)"
            )
        since = _parse_since(args.since)
        results = [
            backfill_channel(
                channel,
                state,
                now=now_dt,
                get=get,
                api_key=api_key,
                since=since,
                max_videos=args.max_videos,
            )
        ]

    print(
        json.dumps(_results_to_dict(results, now_dt, paused), indent=2)
        if args.as_json
        else _format_human(results, paused)
    )
    return 1 if any(r.errors for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
