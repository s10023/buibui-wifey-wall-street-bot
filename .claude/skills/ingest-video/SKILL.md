---
name: ingest-video
description: >
  Ingest one OR MORE YouTube or X video URLs into the research pipeline in a single
  call — including Chinese-language video. Fetches metadata + transcript (yt-dlp
  captions, Groq whisper-large-v3 fallback) via tools/video_fetch.py, batched with a
  randomized cooldown + a per-video dedup cache so re-runs hit zero network, then
  runs TWO sonnet subagent passes per video: pass 1 (text-only) segments the
  transcript and ranks candidate items; pass 2 (vision) reads the transcript-selected
  frames (never scene-change — tools/video_marks.py) and produces chart-corrected
  item JSON. Call time is resolved deterministically in code (tools/video_calltime.py)
  — never by the model doing date arithmetic — preferring a stated in-video time but
  bounded below the publish timestamp. Classifies via the shared content-type gate +
  4-bucket verdict taxonomy and routes (after ONE human review gate for the whole
  batch) into the same three streams as /ingest-x: A hypotheses ->
  docs/plans/thesis-inbox.md, B mechanics -> docs/plans/mechanics-backlog.md, C daily
  setups -> docs/plans/pundit-calls.jsonl, plus a durable per-video note. Invoke when
  the user says "/ingest-video", pastes one or more YouTube or X video URLs, or says
  "ingest this video" / "ingest these videos".
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest video(s)

Spec: `docs/superpowers/specs/2026-07-28-ingest-video-design.md`.

Sibling of `/ingest-x` (`.claude/skills/ingest-x/SKILL.md`) — same batch → per-item sonnet
subagent → ONE consolidated review digest → ONE approval → route shape, reusing
`tools/x_route.py::route_target` unchanged. This doc assumes the reader knows that shape; it
spells out in full only what differs: frame extraction driven by the transcript (not
scene-change), a two-pass split (text then vision), and deterministic call-time resolution.

Handles one or many URLs (YouTube, or X video) in a single invocation. Collect every URL the
user pasted, then run the flow once over the whole set.

## Flow

### 1. Fetch the whole batch in ONE call

```bash
PYTHONPATH=. poetry run python tools/video_fetch.py <url1> <url2> … --json \
  > .cache/video/_batch.json
```

**Redirect to a file — never let the batch JSON print into your context.** A batch of 4
carries four full transcripts; splitting them to disk and passing subagents a path saves
tens of thousands of tokens and changes no output. The split below prints a compact index
only:

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from pathlib import Path

index = []
for el in json.loads(Path(".cache/video/_batch.json").read_text(encoding="utf-8")):
    row = {"url": el["url"], "cached": el.get("cached"), "unavailable": el.get("unavailable")}
    meta = el.get("meta")
    if meta:
        out = Path(".cache/video") / meta["video_id"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "transcript.json").write_text(
            json.dumps({"meta": meta, "segments": el.get("segments", [])},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        row |= {k: meta[k] for k in
                ("video_id", "author", "title", "duration_s", "lang", "publish_ts_utc")}
        row["n_segments"] = len(el.get("segments", []))
        row["transcript_path"] = str(out / "transcript.json")
    index.append(row)
print(json.dumps(index, ensure_ascii=False, indent=2))
PY
```

Every later step reads `.cache/video/<video_id>/transcript.json` (already gitignored,
alongside the fetch cache and the frames). You work from the index; the transcripts stay on
disk.

`tools/video_fetch.py` always batches (unlike `tools/x_fetch.py`, it has no separate
single-URL path, so there is no `--batch` flag to pass). Output is a JSON array, one element
per URL in the position it was requested (`url` on each element is that position's URL,
correct even on a cache hit — do not assume array order otherwise). Per element:

- `url` — the URL actually requested at this position
- `cached` — bool; `true` = zero network, served from `.cache/video/<id>/`
- `meta` — `{source, video_id, author, title, publish_ts_utc, duration_s, lang, url,
  chapters, caption_langs_manual, caption_langs_auto}`, or `null` when the video itself was
  unreachable. `chapters` is the video's own chapter list (often empty) and feeds step 3's
  recap window. The two `caption_langs_*` lists are the only provenance signal: `manual` is
  author-written, `auto` is YouTube ASR, and both land on disk under the same `sub.<code>.vtt`
  name.
- `segments` — `[{ts_s, text, lang}, …]` (empty when there is no transcript)
- `transcript_source` — `manual_captions` | `auto_captions` | `asr_whisper` |
  `captions_unknown` | `asr_whisper_captions_missed` | `""`.
  `asr_whisper_captions_missed` is the value to act on, not merely record: the video's own
  metadata listed caption tracks but the download did not produce them, and this is
  recoverable — re-run that video rather than accepting the note. Do not treat it as
  equivalent to `asr_whisper`; "this video has no captions" and "we failed to fetch the
  captions it has" are different claims. Carry it into the note frontmatter (step 9)
  verbatim. It is not decoration: every item, `raw_quote` and call-time derives from this
  text, and an `asr_whisper` transcript is materially weaker than an author-written one —
  worst on the CN channel, where ASR is weakest and `raw_quote` accuracy is load-bearing.
  Treat a `raw_quote` lifted from an ASR transcript as quoted-with-uncertainty: if a number
  in it is decision-changing, say so in the digest rather than presenting it as the author's
  exact words. `captions_unknown` means the metadata call described no caption mappings (an
  old cache entry) — that is "we did not ask", not "it was ASR".
- `frame_paths` — always `[]` at this stage. This CLI fetches metadata + transcript only;
  frames are extracted later (step 5), from a separate Python call, only for the moments
  pass 1 decides are worth a frame. Don't expect frames here — that is not a bug.
- `unavailable` — `null`, or a string reason

A caption-less video needs `GROQ_API_KEY` in the environment (`.env`) to produce a transcript
at all; without it, `unavailable` reports that explicitly (see shape 2 below).

### 2. Distinguish the three `unavailable` shapes — they are NOT the same

| Shape | `meta` | `unavailable` | Meaning | Action |
| --- | --- | --- | --- | --- |
| 1 | `null` | `"<reason>"` | The video itself is unreachable (bad URL, deleted, private, yt-dlp failure). Nothing else is known. | Tell the user by URL, drop it from the batch, continue with the rest. |
| 2 | `{...}` | `"<reason>"` | Metadata resolved fine, but no transcript could be produced — "no captions available and no GROQ_API_KEY configured", "caption download failed and no GROQ_API_KEY configured", or a Groq failure (HTTP error, audio extraction, chunking). | Because `meta` is populated, name the video (`meta.author`, `meta.title`) in the health note, and say which of the three reasons it was. `caption download failed` is recoverable — re-run that video; the other two are not. Skip it from pass 1 onward — there is no transcript to feed. |
| 3 | `{...}` | `null` | Fully usable. | Proceed. |

Only shape-3 videos continue through the rest of this flow.

### 2b. Sibling-repo check — before spending any subagent tokens

This repo and the crypto parent (`~/repo/buibui-moon-trader-bot/`) follow overlapping
channels — Benjamin Cowen sits in both follow configs. `tools/route_dedup.py`'s identity
ledger is per-repo, so it cannot see the parent's work at all, and its `check` runs later in
this flow (step 7) — after the subagent spend this step exists to protect. The
`.cache/video/<id>/` cache only spares the re-download, never the re-ingest. So this grep
stays the first line of defence:

```bash
grep -rl -E 'video_id: *"?(<id1>|<id2>|…)"?' \
  ~/repo/buibui-moon-trader-bot/docs/plans/video-notes/ \
  docs/plans/video-notes/ 2>/dev/null
```

Both directories, deliberately. A hit in this repo's dir means you already ingested it here —
skip it outright, before any subagent runs.

Grep the frontmatter, never the filename. Both repos name notes
`<date>-<author-slug>-<video_id>.md` (step 9), but each also holds legacy notes carrying
title slugs, so filenames are not reliably comparable across the two — nor even within one.
`video_id:` in frontmatter is present in every note on both sides.

A hit is not automatically a skip — apply the subject rule:

- equities / macro / gold / oil / DXY / bonds → here
- crypto instrument → the parent (it has perp data and the only working scorer)
- one video covering both legitimately yields rows in both repos. Two different calls, not a
  duplicate.

Route by subject, never by repo priority. The parent's scorer assumes 24/7 perp bars, so a
macro call scored there resolves against the wrong bars — and this repo holds ETF proxies
(USO/GLD/UUP) while pundits quote the underlying's units, so gold and DXY fail loudly while
oil fails quietly (USO sits in roughly the same $70-85 band as WTI without tracking it). Both
directions have a wrong home; the subject decides.

**Scoring:** routed Stream-C rows are read by `tools/pundit_score.py`
(`make wifey-pundit-score`). Rows resolve on the horizon frame (1h intraday, 1d swing) over
NYSE-session windows, so a row is only scoreable once its symbol has OHLCV backfilled — check
the scorer's warnings after a batch rather than assuming.

### 3. Pass 1 — text-only subagent, one per video, pinned to sonnet

Only 2 subagents run at once — a 3rd launch is blocked outright, so on a batch of 3+ videos
pipeline steps 3 → 6 by hand in pairs rather than fanning the whole batch out at once, and
budget the wall-clock for it.

For each shape-3 video, dispatch a `general-purpose` subagent via the Task tool with
`model: "sonnet"` (do not inherit Opus) and `subagent_type: "general-purpose"`. Give it:

- the video's `transcript_path` from step 1 — the path, not the transcript. It reads the file
  itself; `segments` is the `"segments"` key inside it. Pasting the array into the prompt
  puts the whole transcript in your context for no gain.
- `meta.publish_ts_utc`, `meta.author`, `meta.lang` — context only, for resolving a relative
  stated date ("last Monday") and inferring a speaker's timezone from channel locale. It must
  not compute a final call time itself — that happens in code, step 4.
- the channel's `intro_recap_s` and `item_cap` (see below) — numbers, not rules to re-derive
- the inline classification rubric (below)

First, ask the config for this channel's two per-video knobs:

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py hint --author "<meta.author>" || true
```

Pure local config read — no API key, no network — so a hand-pasted URL resolves the same way
a polled one does. Returns `{"matched": …, "intro_recap_s": N, "item_cap": N, …}`. Run it per
video; a batch can span channels.

`hint` exits 1 if `config/youtube_channels.toml` does not exist (`load_feed_config` raises
`SystemExit` rather than returning `matched: false`). That file is gitignored and may
legitimately not exist here, since this skill's primary mode in this repo is a hand-pasted
URL with no follow list at all. A missing config is not an error condition for this step —
treat it exactly like `matched: false`, take both defaults, and carry on; that is what the
`|| true` is for.

Both knobs otherwise degrade quietly to a default: `matched: false` or `intro_recap_s: 0`
means no recap rule and nothing changes, and `item_cap` falls back to `video_marks.ITEM_CAP`
(12 here, not the parent's 5).

`intro_recap_s` is a per-channel constant, and the video's own chapters beat it. Compute the
per-video window from step 1's `meta.chapters`:

```bash
PYTHONPATH=. poetry run python -c "
import json,sys
from tools.video_fetch import Chapter, recap_window_s
ch = tuple(Chapter(**c) for c in json.load(sys.stdin))
print(recap_window_s(ch))
" <<< '<meta.chapters as JSON>'
```

A positive result replaces `intro_recap_s` for that video in both directions — a shorter
chapter window must narrow the trim too, or the override is just a bigger constant. `0.0`
means fall back to `intro_recap_s` (no leading recap chapter, or no chapters at all — commonly
the case, not the edge case).

In this repo the chapter window is the only trim that can fire — both channels in
`config/youtube_channels.toml` sit at `intro_recap_s = 0`. That means a false positive costs
more here: with no constant to fall back to, a wrongly-matched chapter is the whole trim. The
hint list is narrowed accordingly (`_RECAP_TITLE_HINTS` excludes `intro`) — an introduction
opens content, a recap replays prior calls, and only the second is what this window exists to
trim.

`item_cap` is applied by the pass-1 prompt, not by code, so a value fetched here and not
passed on does nothing — carry it into the ranking rule as a literal. A cap plumbed
everywhere except its one consumer is not plumbed.

Note this keys on `meta.author` (an @handle), which matches neither the `UC…` id the poll
path uses nor a CJK display `name` — that is why `config/youtube_channels.toml` carries a
`handle` field. A channel with no `handle` never matches, and then both knobs silently take
their defaults. Neither degradation raises anything; the run just quietly keeps less.

In this repo the `item_cap` override is nearly inert: `ITEM_CAP` is already 12 and
`item_cap + len(TAIL_OFFSETS_S) <= FRAME_CAP` caps it at 13, so the usable range is 13..13,
and a larger value silently degrades kept items to `vision_confidence: "low"` (`yt_feed.py`
does not validate it). Treat a channel needing more as a reason to revisit the global
constants, not to set this key.

**If the effective window came back non-zero** — the chapter-derived `recap_window_s` when it
is positive, `intro_recap_s` otherwise — set `is_intro_recap: true` on every candidate with
`ts < window`. For a `setup`, also set `retrospective: true` — a call lifted from a recap
block is a past call that would otherwise be stamped with today's `call_ts_utc` and score the
author on an already-resolved trade. For a `claim` or `mechanic`, set `is_intro_recap` and
keep the candidate: an idea stays portable regardless of when in the video it was said.

`retrospective` drops a `setup` in code; `is_intro_recap` does not. `route_target` takes
`retrospective` and `rejected` keyword-only and returns `None` for a `setup` with either.

The flag only drops a `setup`. A `claim`/`mechanic` marked `is_intro_recap` is kept
deliberately, and no code drops it — the human reading step 7 is the entire mechanism there,
which is why 7c prints both flags. Step 8 must pass the suppressors explicitly; they default
to `False`, so omitting them fails open and silently.

Instruct it not to read any repo, SoT, or memory file — the rubric below is written to stand
alone. That bounds what it reads, not what it knows (see the isolation warning under pass 2).
Instruct it to return only this JSON:

```json
{
  "summary": "one paragraph, English",
  "stated_ts_utc": "ISO-8601 with an explicit UTC offset, or a bare YYYY-MM-DD, or null",
  "stated_date_only": false,
  "stated_ts_raw": "verbatim quote or empty",
  "candidates": [
    {"ts": 252.0, "content_type": "setup|claim|mechanic", "specificity": 1-5,
     "is_intro_recap": false, "retrospective": false, "gist": "..."}
  ]
}
```

`is_intro_recap` / `retrospective` default to `false` and are only ever set by the
`intro_recap_s` rule above — a channel with no `handle`, or `intro_recap_s: 0`, leaves both
false and nothing changes.

**A `stated_ts_utc` carrying a time must carry an explicit UTC offset** (e.g.
`2026-07-14T08:00:00+08:00`) — never a bare local time. `tools/video_calltime.py` rejects a
naive (offset-less) timestamp and silently falls back to publish time, so a subagent that
emits `2026-07-14T08:00:00` with no offset gets the same downstream result as emitting
nothing, just less honestly. Instruct the subagent: state the offset whenever the speaker's
timezone is inferable from context — never guess UTC for a time of day.

When only the date is recoverable, emit the bare `YYYY-MM-DD` with `stated_date_only: true`
rather than inventing a time and an offset to go with it. `resolve_call_ts` accepts a naive
value on that flag alone and normalises it to `23:59:59Z`, the last instant of the stated
date — so an invented time can only credit the call earlier than the flag would. Emit `null`
when neither is recoverable.

Instruct the subagent to return every candidate it found, unranked and untruncated — the
cutoff is applied here, in code, not by the subagent. Then split them with
`video_marks.keep_items`, which applies the quality floor (`MIN_ITEM_SPECIFICITY` = 3) and
the budget cap (`ITEM_CAP` = 12) and stamps a `drop_reason` on every dropped row:

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from pathlib import Path
from tools.video_marks import keep_items

CANDIDATES = json.loads(Path("<path to pass-1 candidates JSON>").read_text(encoding="utf-8"))
kept, dropped = keep_items(CANDIDATES)
print(json.dumps({"item_ts": [c["ts"] for c in kept], "kept": kept, "dropped": dropped},
                 ensure_ascii=False, indent=2))
PY
```

`item_ts` feeds step 5. Carry `dropped` (with its `drop_reason`) into the digest and the note
verbatim — a dropped call must stay visible, because a silently lost call is
indistinguishable from a video that never made one.

Rank by `specificity` descending and keep the top `item_cap` — the number `hint` returned for
this channel, written into the prompt as a literal, falling back to `ITEM_CAP` (12) when
`matched: false`.

Know which way the tie-break leans before reading a thin result: `keep_items` ranks by
specificity desc, then `ts` asc, so a tie at `ITEM_CAP` favors earlier material — a pundit who
opens with macro and closes with single names loses the single names first, sometimes
including the exact names in the video's own title. Read a thin yield as a cap artifact
rather than a video that made no calls, and check `dropped` before concluding otherwise.

Do not hand-roll the cutoff. The floor and the cap do different jobs and one number cannot do
both: the floor stops a thin video padding vibes up to the cap just because slots exist, the
cap bounds a dense one. Both live in code for the same reason `video_calltime.py` does — a
truncation rule stated only in prose drifts.

### 4. Resolve the call time deterministically — never in the prompt

```bash
PYTHONPATH=. poetry run python tools/video_calltime.py \
  --publish <meta.publish_ts_utc> \
  [--stated <stated_ts_utc>] [--date-only] \
  --stated-raw "<stated_ts_raw>" \
  --ingested <now, UTC ISO-8601>
```

- Omit `--stated` entirely when pass 1 returned `null` — do not pass the literal string
  `"null"`.
- Pass `--date-only` only when `stated_date_only` was `true` — it is what makes a bare
  `YYYY-MM-DD` parse at all; without the flag a date-only `--stated` is naive and naive falls
  back to publish. Pinned by
  `tests/test_video_calltime.py::test_cli_date_only_accepts_a_bare_date`.
- `--stated-raw` is always passed (an empty string is fine).
- `--ingested` is the current UTC time, e.g. `` $(date -u +%Y-%m-%dT%H:%M:%SZ) `` — needed so
  the tool can also compute `backlog`. Capture this one value per batch and reuse it verbatim
  everywhere `ingested_ts_utc` is written later (the Stream C line in step 8, the per-video
  note frontmatter in step 9) — do not call `date -u` again at those points; two separate
  calls could disagree by however long the batch took to process, and the field exists to say
  when this pipeline saw the video, not to be re-timestamped per write site.
- **If `meta.publish_ts_utc` is an empty string** (yt-dlp returned no timestamp field — rare,
  but possible), `video_calltime.py` raises `ValueError` rather than guessing. Treat that
  video as call-time-unresolvable and skip it with a health note; do not pass an empty string
  through.

Output (JSON to stdout):

```json
{
  "call_ts_utc": "...",
  "call_ts_source": "stated|publish",
  "publish_ts_utc": "...",
  "stated_ts_raw": "...",
  "backlog": false
}
```

Use these five fields verbatim in the digest, the ledger line, and the note. Never have a
subagent or the orchestrator derive `call_ts_utc` by date arithmetic — that is exactly the
look-ahead defect this tool exists to prevent (see Guardrails).

### 5. Select and extract frames

There is no CLI for this — `video_marks.select` and `video_fetch.extract_frames` are library
calls. The transcript is already on disk from step 1, so there are exactly two placeholders
to substitute per shape-3 video: `VIDEO_ID` and `ITEM_TS` (the kept items' `ts` values from
step 3's top-`ITEM_CAP` candidates — a short list of floats). Do not write a
`marks_input.json` with the Write tool; that pulls the whole transcript back through your
context to hand it to a script that can read it itself.

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from dataclasses import asdict
from pathlib import Path

from tools.video_fetch import VideoMeta, extract_frames
from tools.video_marks import TranscriptSegment, select

VIDEO_ID = "<video_id>"          # e.g. "dQw4w9WgXcQ"
ITEM_TS = [<ts of each kept item from step 3>]   # e.g. [252.0, 886.0]

raw = json.loads((Path(".cache/video") / VIDEO_ID / "transcript.json").read_text(encoding="utf-8"))
meta = VideoMeta(**raw["meta"])
segments = [TranscriptSegment(**s) for s in raw["segments"]]

marks = select(segments, ITEM_TS, meta.duration_s)  # cap defaults to FRAME_CAP (15)
dest_dir = Path(".cache/video") / meta.video_id / "frames"
frame_paths = extract_frames(meta, marks, dest_dir)

print(json.dumps({"marks": [asdict(m) for m in marks], "frame_paths": frame_paths}, indent=2))
PY
```

Frames land at `.cache/video/<meta.video_id>/frames/f_NNNN.jpg` (already gitignored,
alongside the fetch cache). `extract_frames` downloads the video once into that same frames
directory (ffmpeg cannot seek the web-page URL directly), seeks the local copy per mark, then
deletes the downloaded video — only the frame JPEGs persist. `select()` is deterministic and
caps at `FRAME_CAP` = 15; frames can come from deixis phrases and spoken price levels even
when `item_ts` is short or empty — a video with zero routable candidates can still produce
frames via those triggers or the `SAFETY_SAMPLE_S` (300s) floor.

`marks` non-empty but `frame_paths` empty is a download failure, not "no chart" — keep the
two apart. `select()` returns at least the safety-sample marks for any `duration_s > 0`, so an
empty `marks` list only happens for a rare zero-duration video. If `marks` came back
non-empty but `frame_paths` is still `[]`, the video's media download failed (network error,
age-gate, region block) — record that video's health note as "frame extraction failed (media
download error)". `extract_frames` already retries the download-and-seek internally (3
attempts, short backoff), but the underlying `HTTP 403` is intermittent and server-side, so
the built-in retry does not always exhaust it — re-run the step once by hand, and take the
health note only if it comes back empty again. Do not record `chart_present: false` for that
case; that flag is reserved for step 6, where frames were produced and pass 2 actually looked
and found no chart.

### 6. Pass 2 — vision subagent, one per video, pinned to sonnet

Dispatch whenever `frame_paths` from step 5 is non-empty — independent of whether step 3
found any candidates. When `frame_paths` is empty, which health note you write depends on
step 5's `marks` distinction:

- `marks` was also empty (a zero-duration video — rare): skip pass 2, treat every kept item
  from pass 1 as `vision_confidence: "low"`, `frame_path: null`, and record
  `chart_present: false` for that video in the digest and note.
- `marks` was non-empty (the ordinary empty-`frame_paths` case): this is the step-5 download
  failure, not "no chart" — and only after step 5's hand re-run also came back empty. Skip
  pass 2, still mark every kept item `vision_confidence: "low"` / `frame_path: null`, but
  write the step-5 health note ("frame extraction failed (media download error)") instead of
  `chart_present: false` — you never actually looked, so don't claim you did.

No subagent dispatch in either case.

Otherwise, dispatch a `general-purpose` subagent, `model: "sonnet"`,
`subagent_type: "general-purpose"` (step 3's 2-at-once dispatch cap applies here too — these
pass-2 agents share it with any pass-1 agent still running). Give it: the `frame_paths` list
(it reads each one — vision), the `transcript_path` from step 1 (the path — it reads that
file for context on what was said; do not paste `segments`), the kept items from step 3
(`ts`, `content_type`, `gist`), and the item schema below. Instruct it not to read any repo,
SoT, or memory file.

**Not reading files does not make the agent context-free.** A `general-purpose` subagent can
surface project knowledge (e.g. citing `CLAUDE.md` or `MEMORY.md` findings) it was never
given and never read, so the rubric's "self-contained" framing bounds what the agent reads,
not what it already knows. This matters even when what leaks is accurate: the rubric is a
distilled snapshot so the extractor classifies against a frozen prior, and an agent silently
drawing on the live SoT is a different experiment from the documented one. Treat the leak as
present when designing any experiment that depends on isolation. This is confirmed for
`general-purpose` specifically, the type both passes actually dispatch — `Explore` and a
`tools:`-restricted custom agent are untested here, so do not assume switching agent types
closes it.

Instruct it to return only this JSON:

```json
{
  "chart_present": true,
  "items": [
    {
      "ts": 252.0,
      "frame_path": ".cache/video/<id>/frames/f_0252.jpg",
      "symbol": "AAPL | null",
      "direction": "long | short | neutral | null",
      "entry": "...", "stop": "...", "target": "...",
      "horizon": "intraday | swing | unspecified",
      "setup_type": "free text",
      "raw_quote": "the sentence(s) the call/claim came from, ORIGINAL language",
      "raw_quote_en": "English translation of raw_quote",
      "chart_read": "what the frame shows (levels, structure, annotations)",
      "content_type": "claim | setup | mechanic",
      "verdict": "NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE",
      "gap_note": "one line: implied primitive + does the system already have/test/freeze it?",
      "vision_confidence": "high | medium | low",
      "corrected_from": "the transcript's original value, or empty"
    }
  ]
}
```

Rules for the subagent:

- Where a frame shows a number/level/structure that contradicts the transcript, the chart
  wins: use the chart's value in `entry`/`stop`/`target`/`chart_read`, and record the
  transcript's original claim in `corrected_from`. When nothing was corrected, leave
  `corrected_from` empty.
- The chart wins only on a drawn value — a line, annotation, printed label, or measured
  readout placed on the chart. A value inferred from where the live price ticker happens to
  sit is not a correction: put that reading in `chart_read`, keep the spoken value in
  `entry`/`stop`/`target`, and leave `corrected_from` empty. A pundit is scored on the level
  they stated — e.g. a stated 61,000 pivot must not be silently moved to 61,600 just because
  the ticker sits near a drawn arc's anchor; that would be applying the rule to the wrong
  class of evidence.
- `corrected_from` carries chart-vs-transcript corrections only. A symbol normalisation is
  not one — normalise `symbol` and leave `corrected_from` empty. Here that means an index
  call resolving to its index ticker (`S&P 500` → `^GSPC`, never an ETF proxy) or a
  class-share form yfinance expects (`BRK.B` → `BRK-B`), so the scorer resolves against the
  right bars. Same reason `confidence` and `vision_confidence` stay separate — a field
  carrying two semantics can be queried for neither.
- Anything the frames do not visually corroborate (no frame near that `ts`, or the nearest
  frame doesn't show what was said) gets `vision_confidence: "low"`. Reserve `"high"` for a
  frame that directly confirms the claim; `"medium"` for partial/ambiguous support.
- `raw_quote` stays in the transcript's original language (Chinese stays Chinese);
  `raw_quote_en` is always English (identical to `raw_quote` when the source is already
  English).
- `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` default it to
  `NOVEL` (non-blocking — routing uses `content_type` for those, same as `/ingest-x`).
- `chart_present: false` when no frame in this video shows a chart at all (pure
  talking-head) — still emit `items` from the transcript alone, all
  `vision_confidence: "low"`, `frame_path: null`.

**`confidence` vs `vision_confidence` — never merge these, they mean different things.**
`confidence` means the same thing across every source already in
`docs/plans/pundit-calls.jsonl`: the pundit's verbatim hedging phrase, or empty. This
pipeline does not extract that from a video (pass 2's contract is a visual-corroboration
read, not a hedging-language read). `vision_confidence` is video-only and records whether
pass 2 could visually corroborate the item against a frame — a property no other source in
the ledger has or needs. Keeping them as separate columns keeps a future
`GROUP BY confidence` (or any other query over the hedging-language column) honest across
every source, instead of silently mixing two incompatible populations.

### 7. ONE consolidated digest for the whole batch

The digest has three parts, in this order. The order is load-bearing, not cosmetic — see "Why
summaries come first" below.

**7a — What each pundit said. The pass-1 `summary` per video, above the table.** One short
paragraph each, in the operator's reading order (freshest first is fine). Do not bury these
under the table and do not compress them to a clause: this is the part of the digest a human
reads to learn what was said, and it is the only place the unroutable substance of a video
survives at all.

**7b — The board view. Cross-video synthesis, in prose, 3-6 sentences.** Streams A/B/C are
per-item sinks, so agreement and disagreement between pundits exist only in the relationship
between rows and are recorded nowhere. Surface at minimum:

- who is directionally long / short / neutral, and who is already positioned (a pundit
  managing an open trade is not a neutral observer of it);
- levels two or more of them name independently — the strongest signal the batch carries,
  and invisible per-item;
- where they read the same structure and draw opposite conclusions;
- a short "dropped but worth knowing" list: items cut for being unscoreable or below the
  `ITEM_CAP` specificity cut are frequently the most informative in the batch, and the drop
  reason says nothing about how interesting they were.

Build this yourself from data already in hand — no extra subagent, no extra tokens.

**The board view is read-only and strictly outside the routing path.** It must not write to a
sink, must not influence `content_type` / `verdict` / dedup, and must not feed anything
downstream (wifey has no daily Brief; the constraint is forward-looking). A narrative digest
of pundit opinion is exactly the thing that can become a trading input without ever earning a
track record, which is what `tools/pundit_score.py` exists to prevent. It is information, not
evidence — say so if it is ever quoted back as a reason to take a trade.

**7c — The routing table.** One row per kept item across every video: video (title) · author
· `call_ts_utc` (`call_ts_source`) · `ts` · `content_type` · `retrospective` ·
`is_intro_recap` · `verdict` · proposed routing · `vision_confidence`.

Both flags must be visible in this table (step 3). `retrospective` is enforced in code for a
`setup`, so showing it lets the approver see why an item is about to be dropped rather than
wondering where it went; `is_intro_recap` on a `claim`/`mechanic` is enforced by nobody —
there the human reading this table is the only mechanism. Below the table, per video: the
dropped candidates with their reasons, the `chart_present` flag, and `backlog` when `true`.
List any shape-1 / shape-2 videos separately with their skip reason. Write nothing yet.

**Why summaries come first.** Part of the reason this pipeline exists is to receive what is
in a video without watching it. The routing streams alone don't serve that goal — they
capture only what can be scored or tested, so an operator reading a routing table alone
learns nothing that cannot be scored. A batch of several pundits sharing a pivot zone, or
reading the same chart structure to opposite conclusions, is exactly the shape no routing
stream records.

**For any video where `call_ts_source == "stated"`, also print `stated_ts_raw`,
`publish_ts_utc`, and the delta between the stated and publish times** (e.g. "stated
2026-07-14T08:00:00+08:00 vs publish 2026-07-14T22:10:00+00:00, Δ14h"). A stated time can
move `call_ts_utc` up to `STATED_TS_MAX_LEAD_H` (168h) earlier than publish, and this
digest — specifically the human looking at it — is the only runtime control on that input;
`call_ts_utc (call_ts_source)` alone does not show the approver the quote that justified the
shift, so they cannot judge it. Showing the raw quote and the gap is what lets the approver
reject a fabricated or implausible timestamp before it reaches the ledger.

Run the dedup check before printing the digest, once per non-dropped item, so its result
appears in the digest rather than after approval. `--item-ts` is the item's own `ts` — never
omit it, and never key on the video id alone: one video legitimately yields several items,
and collapsing them would delete real rows.

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py check \
  --source-id <meta.video_id> --item-ts <item ts> --sink <route_target output> \
  --text "<the gist being routed>"
```

- `already_routed: true` → do not append. Show the item as "already routed" and route
  nothing for it in step 8. Exact match, no judgement needed.
- `candidates` non-empty → not a block. Print each candidate's `excerpt` and `shared_levels`
  under that item and let the user decide: new row, corroboration line on the existing
  entry, or drop. Bulk video ingest makes this the common case — a pundit routinely repeats
  one thesis across a week of uploads.
- `semantic_scope` says what the near-duplicate pass actually compared against:
  `all-entries` (Streams A and B), `same-source` (Stream C — only this video's own earlier
  rows, never another author's), or `none`. Report it; never let an empty `candidates` list
  read as "checked against everything and clean".
- Discount a hit whose `shared_levels` are all round 4-digit numbers that could be years.
  Bare ones are dropped by `normalize_levels`, but a level written `2,050` is kept by design
  and two entries can share it coincidentally.

On Streams A and B, `all-entries` scope does not make an empty `candidates` list mean "not a
duplicate" — read it as "nothing scored above threshold." Both streams are `SEMANTIC_SINKS`,
so `semantic_scope` (`tools/route_dedup.py:540`) always returns `all-entries` and
`_comparable_entries` (`:550`) returns every entry in the file, so a near-verbatim
restatement is already in scope and can still rank below threshold. That is a lexical ranking
limit, not a scope gap, which is why the digest's human gate is doing the real work here — a
ranking miss becomes more likely as `mechanics-backlog.md` grows.

Then run the intra-video pass, once per video that has two or more Stream-C-bound items.
`check` cannot catch these: every check runs before the approval that writes anything, so
when a video's items are checked none of them are on disk yet — two legs of one position are
only findable item-vs-item. Write the video's pending Stream C items (the pass-2 item dicts
are enough — it reads `symbol`/`direction`/`entry`/`stop`/`target`/`raw_quote*`/`ts`) to a
scratch file, then:

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py pairs \
  --items .cache/video/<video_id>/pending_calls.json
```

Print every returned pair under that video: both `ts` values, `score`, `shared_levels`, and
both excerpts. This is advisory, never a block — two legs of one position and two genuinely
distinct calls on one symbol look alike by construction, and only the operator knows which
they are watching. Treat a hit as worth reading, not as routine noise.

### 8. Route on a single approval

After the user approves the batch, for each item compute the destination with
`tools/x_route.py::route_target` (same taxonomy, unchanged import — do not fork it) and
append per this table, identical to `/ingest-x`:

```python
route_target(content_type, verdict,
             retrospective=item.get("retrospective", False),
             rejected=item.get("rejected", False))
```

Pass both suppressors — they are the whole point of pass 1 setting them. They are
keyword-only and default to `False`, so omitting them silently routes everything with no
error. A `setup` with either flag returns `None`.

| content_type | verdict | Append to |
| --- | --- | --- |
| setup | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
| setup | `retrospective` or `rejected` | drop — say which flag; write nothing |
| mechanic | — | `docs/plans/mechanics-backlog.md` (a `-` list bullet) |
| claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
| claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | drop — state "seen, verdict X", write nothing |

Create the sink file with a one-line header if it does not exist. Report a one-line result
per item (routed → which file, or dropped → verdict).

**Stream C requires a real `symbol`** — never route a `setup` item with `symbol: null` or
`symbol: ""` to `pundit-calls.jsonl`. The scorer guards this (`_INVALID_SYMBOLS`) and skips
such a row with a "no symbol resolved" warning, but a skipped row is still a wasted write and
the guard is a backstop, not a licence to route unresolved items. If pass 2 could not resolve
a symbol for a `setup` item, treat it as a dropped candidate instead (reason: "no symbol
resolved") in the digest and the per-video note, not a Stream C write.

**After each successful append, record it:**

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py mark \
  --source-id <meta.video_id> --item-ts <item ts> --sink <sink path>
```

`mark` runs after the write, never before — marking at check time would let an abandoned
review consume the id and dedup away the real append later. Never mark a dropped item.

Stream C's near-duplicate exemption is across sources only: two pundits making the same call
are two real observations and `tools/pundit_score.py` scores both authors, so collapsing
those would delete signal. It never justified one video restating its own call, which is how
an entry leg and a target leg of a single position become two rows — so within one
`source_id` the pass does run (step 7's `same-source` scope plus the `pairs` call). Stream C
also still gets the exact `already_routed` block.

**Deep-link rule — separator-aware.** A YouTube timestamp deep link must respect whatever the
URL already has:

- if the URL contains `?` (e.g. `https://www.youtube.com/watch?v=<id>`), append `&t=<ts>s`
- if it does not (e.g. `https://youtu.be/<id>`), append `?t=<ts>s` instead

Blindly appending `&t=<ts>s` to a `youtu.be` URL produces `https://youtu.be/<id>&t=90s`,
which is broken — with no prior `?`, the `&` never starts a query string and the timestamp is
silently dropped by the player. Always branch on whether `"?"` is already in the URL before
appending.

X video URLs get no timestamp deep link at all (the platform doesn't support one) — persist
the plain URL, and carry the offset separately in the `ts` field, which the schema below
includes for every source so it's never only recoverable by re-parsing the URL.

**Stream C line** (`pundit-calls.jsonl`, one JSON line, extends the `/ingest-x` schema):

```json
{"source":"youtube","author":"<handle>","url":"<url, with the deep link above for youtube>","ts":252.0,"call_ts_utc":"<resolved call time>","call_ts_source":"stated|publish","publish_ts_utc":"<publish time>","stated_ts_raw":"<verbatim quote or empty>","ingested_ts_utc":"<now>","backlog":false,"symbol":"...","direction":"...","entry":"...","stop":"...","target":"...","horizon":"...","confidence":"","vision_confidence":"high|medium|low","raw_quote":"<original language>","raw_quote_en":"<english>","corrected_from":"<transcript's original value, or empty>"}
```

**`target` and `entry` are machine-parsed — the format is a contract, not prose.**
`tools/pundit_score.py` resolves one number per field, and a hyphenated range anywhere in the
string creates a zone that overrides the `/`-separated ladder. Write `target` as a bare
ladder — `640 / 660 / 690` — with ranges in `raw_quote` instead; a clarifying parenthetical
re-breaks it, because the constraint is on the whole field, not its leading number. The zone
resolves to whichever edge price reaches first, so on a long it lands nearer and manufactures
an optimistic WIN, while on a short it lands further and strands the row OPEN — the error
runs in both directions and neither is safe. This is the same rule `/ingest-x` step 4 states;
both skills write this file, one parser scores both.

Run `make wifey-pundit-score` as the last action of the round — reading the row back does not
show you the parse, and at round-end every row is still OPEN, so a bad parse is free to fix
then and invisible later.

Sign-check every Stream C row before you append it — do not eyeball this:

```bash
PYTHONPATH=. poetry run python tools/x_route.py --check-levels <<'JSON'
<the candidate Stream C lines, one JSON object per line>
JSON
```

A long must satisfy `stop < entry < target`, a short `target < entry < stop`. A `WARN` row is
mis-encoded, not a real call: fix the field assignment and re-run, or report it as a dropped
candidate with the reason stated. The check never rewrites or drops anything — it prints and
exits 1, and the decision stays yours.

The trap it exists for: a level phrased as an invalidation ("unless it reclaims 29,200",
"跌破/站回 X 就反转", "invalidated above X") is a stop, never a `target`. Writing one into a
short's `target` puts the row instantly in profit, and the scorer books a `WIN` at ~0.00 R —
a fake statistic rather than a visible error. Note the guard's blind spot: a row stating that
one level and nothing else has no second leg to contradict it, so read the invalidation
phrasing yourself too — pass 2 translating a CN hedge clause is exactly where this slips
through.

That blind spot is this pipeline's common case, not an edge case, so encode it by rule rather
than re-deriving one per run: a TA-narrating pundit commonly states one level and stops.

- A level serving as both entry and stop is zero risk, and the check flags it correctly.
  Encode it as entry + target with the stop left unstated — never invent a gap the pundit did
  not give.
- A row with no numeric level at all is a dropped candidate ("nothing scoreable"), not a
  Stream C write.

Inventing a plausible stop is worse than leaving the leg empty: it is unfalsifiable once it
is in the ledger, and unlike the fake-`WIN` above no guard can ever see it.

`source` is `youtube` or `x-video` (from `meta.source`, verbatim — `tools/video_fetch.py`
already resolves this). `confidence` is always written as an empty string for a video row. It
keeps its `/ingest-x` meaning (the pundit's verbatim hedging phrase) — this pipeline does not
extract that from a video, so the honest value is "not collected," not a repurposed
visual-corroboration score. `vision_confidence` carries pass 2's high/medium/low rating
instead; see the "never merge these" note in step 6.

### 9. Write the per-video note

One file per video (not per item) at
`docs/plans/video-notes/<date>-<author-slug>-<video_id>.md` — already gitignored via
`docs/plans/`. `<date>` is the ingest date (UTC, `YYYY-MM-DD`).

```bash
author_slug=$(printf '%s' "$AUTHOR" | tr '[:upper:]' '[:lower:]' \
  | tr -cs 'a-z0-9' '-' | sed -E 's/^-+|-+$//g' | cut -c1-40)
note_path="docs/plans/video-notes/$(date -u +%F)-$author_slug-$VIDEO_ID.md"
```

Use a named variable, not a `$1` helper function — a shell positional inside a skill code
block can render substituted rather than literal.

**`video_id`, not a title slug — this is the rule, not a collision fallback.** The slug
pipeline keeps only `[a-z0-9]`, so a non-Latin title slugifies to the empty string and every
note from that channel would collapse onto one path, silently overwriting all but the last.
This repo's channels are mostly English, so the collision risk is lower here — but `video_id`
is unique by construction, is the key everything else in this flow is already filed under
(`.cache/video/<video_id>/`, the routing ledger, the YouTube deep link), and is what makes the
step-2b cross-repo check comparable across the two repos instead of guesswork.

A non-Latin handle collapses the same way — `meta.author` is usually the Latin `@handle`, but
not always, and then the name reads `<date>--<video_id>.md` with an empty author segment.
Ugly, still unique, still correct: do not "fix" it by putting the title back.

Some existing notes use an older `<title-slug>` filename form. Leave them — step 2b greps
frontmatter `video_id`, never filenames, so the two forms coexist safely.

Contents:

- YAML frontmatter: `source`, `video_id`, `url`, `author`, `title`, `duration_s`, `lang`,
  `publish_ts_utc`, `call_ts_utc`, `call_ts_source`, `stated_ts_raw`, `ingested_ts_utc`,
  `backlog`, `chart_present`, `transcript_source`. `transcript_source` is written from
  step 1's JSON, never narrated from memory.
- the pass-1 `summary`
- an items table: `ts` · `content_type` · `verdict` · routing outcome ·
  `vision_confidence` · `frame_path`
- the dropped candidates, with reasons
- frame references (path + `ts` for every extracted frame, including ones that produced no
  routed item — that's how you learn the sampling triggers are working)
- the transcript, original language, as fetched — not proofread; it is scratch, not the
  artifact (see spec §Transcript quality). Only routed items carry a reviewed
  `raw_quote`/`raw_quote_en` pair; the rest of the transcript is left as-is.

Write everything above the transcript with the Write tool, then append the transcript with
code — it is already on disk from step 1, and retyping it is exactly the round-trip step 1
exists to avoid:

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from pathlib import Path

VIDEO_ID = "<video_id>"
NOTE = Path("<note_path>")

raw = json.loads((Path(".cache/video") / VIDEO_ID / "transcript.json").read_text(encoding="utf-8"))
lines = "\n".join(f"- `{s['ts_s']:.1f}` {s['text']}" for s in raw["segments"])
with NOTE.open("a", encoding="utf-8") as fh:
    fh.write(f"\n## Transcript (as fetched, not proofread)\n\n{lines}\n")
PY
```

## Inline classification rubric (self-contained — paste into BOTH the pass-1 and pass-2 subagent prompts)

> A distilled snapshot of the SoT's Frozen / Closed / Parked state so each subagent
> classifies from the prompt alone. Refresh from `docs/north-star.md` (Frozen) and
> `project_todo_master.md` (Closed) periodically —
> treat as a de-biasing prior, not gospel; NOVEL still passes the human gate. This block is
> shared verbatim with `/ingest-x`'s rubric — keep the two in sync when either is refreshed.
> `content_type`: **setup** = a specific symbol+direction+levels trade call → Stream C;
> **mechanic** = an exit/risk/data/microstructure execution rule → Stream B; **claim** = a
> generalizable market-behaviour assertion → verdict below.

**Frozen — never propose a new TA detector.** The TA detector book (wicks, marubozu, ORB,
FVG, BOS/market-structure, EQH/EQL, order block, trend day, engulfing, pin bar, inside bar,
hammer, doji, morning/evening star, fib retracement / golden zone, OTE, EMA — plus the
stripped crypto-era detectors: liquidity sweep, funding extreme, SMT, CVD divergence) is
frozen under an inherited category verdict (TA = negative-EV; no new boolean detectors, no
tp_r/gate/threshold sweeps). A claim that just restates one of these candlestick/structure
patterns → `FROZEN-CATEGORY`.

**Scope — the freeze binds this repo's equity signal engine, nothing wider.** It covers the
boolean bar-pattern detectors in `analytics/strategies/_registry.py::DETECTOR_REGISTRY`,
evaluated at `4h`/`1d`/`1wk` on the US-equity universe. It is a policy — an inherited
category verdict — never a measurement about moving averages or price structure in general.
Mark `FROZEN-CATEGORY` only when the claim would land as a new detector or a new sweep in
that book. A monthly-timeframe MA band, a regime or drawdown classifier, and anything on
another asset class or horizon are all out of scope: judge them on their merits. When
unsure, return `NOVEL` and let the human review gate decide — a wrong `FROZEN-CATEGORY`
drops the item silently, a wrong `NOVEL` costs one line of review.

**Already-tested (verdict known → `ALREADY-TESTED`, drop unless materially new evidence).**
All eight research sleeves measured on US equities came back non-positive and the free-data
edge arc is concluded (`CLAUDE.md` § Sleeve verdicts):

- Absolute **trend** (multi-speed EWMAC): G2 FAIL — portfolio Sharpe −0.05, negative even
  pre-cost (a signal failure, not a cost failure).
- Cross-sectional **XS momentum** (relative strength): G3 FAIL — combined Sharpe −0.156
  @2bps, corr_to_trend +0.62 (not even a diversification win).
- **Residualized XS-mom** (beta-stripped + skip-month): FAIL — committed L/S cell +0.15,
  DSR 0.44; the long-only +0.88 leg is survivorship/beta-confounded.
- **Low-vol / BAB** (beta-neutral L/S): FAIL — the realized-β guardrail fired (β +3.9):
  ex-ante β-neutralization did not deliver market-neutrality.
- **Cross-asset TSMOM** (13-ETF basket): FAIL, clean — β-guardrail held; +0.41 cost-free,
  never ≥0.7 gate. Futures-grade breadth is a paid-data question.
- **PEAD-lite** (seasonal SUE on free EDGAR data): FAIL — β-guardrail fired on the broad
  arm; the controlled mega arm showed negative drift. No PEAD in liquid large-caps net of
  cost on free data.
- **Gap-fill magnet** (unfilled gaps as magnets / "gaps always fill"): EXCLUDED, direction
  REFUTED — cost-free the magnet returns −0.460, so gaps continue rather than revert; the
  post-hoc inverse never reaches the bar, and ~211× daily gross turnover kills both
  directions. "90.3% of gaps fill within 60 sessions" is true and almost entirely diffusion
  (matched placebo 88.9%).
- **Velocity alternation** (the pace of a decline predicts the pace of the next move):
  EXCLUDED as a null — β-guardrail fired, beta-hedged −0.169 at alpha t −0.49, and the
  velocity ratio performs indistinguishably from depth alone.
- DOW / day-of-week seasonality (e.g. "Monday is the weekly high → short"): parent-inherited
  verdict — base rate real but the tradeable edge decays OOS; the version that looks clean
  is look-ahead.
- Exit-policy fixes ("your stops are wrong, not your entries"): BOUNDED — the replay A/B
  measured the lever's ceiling at +0.368R of paired uplift, entirely the time-stop, and no
  arm's own mean R clears zero. Re-run trigger is ledger growth, not a restated claim.

**Parked / captured / blocked:**

- IPO post-hype dip-buy (long a faded recent IPO reclaiming its listing price): already
  captured as a thesis memo — a reassertion is "already in inbox", don't duplicate the
  H-row.
- Anything requiring paid data (Polygon intraday, options flow, L2/auction feeds, futures
  breadth): `NOVEL` in principle but data-blocked — say so in `gap_note` (paid data
  currently declined).

**`NOT-FALSIFIABLE`:** vibes / no testable prediction / unfalsifiable hindsight.
**`NOVEL`:** a genuinely new, testable, uncovered market-behaviour claim.

## Guardrails

- Never write to a stream before the user approves the digest — one approval covers the
  whole batch, exactly as in `/ingest-x`.
- `call_ts_utc` never comes from the model's own date arithmetic. Pass 1 only extracts a
  candidate `stated_ts_utc`/`stated_ts_raw`; the actual resolution — stated-vs-publish,
  bounding, backlog — always runs through `tools/video_calltime.py` (step 4). A subagent or
  the orchestrator computing this by hand reintroduces the exact look-ahead defect the tool
  exists to prevent (see the spec's "ledger-integrity constraint").
- Output is a hypothesis/setup/mechanic to test — never an "add a detector" task. The TA
  detector book is frozen, same as `/ingest-x`.
- The free-data edge arc is concluded: routing a NOVEL claim into `thesis-inbox.md` is
  capture, not a commitment to test — never start a new edge-hunt from an ingested claim
  without an explicit user go (same as `/ingest-x`).
- Routing dedup is `tools/route_dedup.py` — `check` in step 7, `mark` in step 8, and it is
  advisory except for `already_routed`. Its semantic pass surfaces candidates for the digest
  and never drops anything: a false positive costs a glance, a false negative costs a
  corrupted sink, and your review gate stays the decision point. Bulk video ingest is what
  makes this bite harder than single X posts, because a pundit repeats one thesis across a
  week of uploads — and note the limit that follows from Stream C's `same-source` scope:
  restatement by one author across two uploads is invisible to it, deliberately, since two
  calls a week apart are two genuine observations the scorer resolves against different
  bars. Read a familiar-sounding `claim` yourself.
- Two subagent passes, both pinned to `model: "sonnet"` — never let either inherit Opus.
  Neither may read any repo, SoT, or memory file; the rubric above is the only context
  either needs beyond the video's own transcript/frames.
- `FRAME_CAP` (15), `ITEM_CAP` (12) and `MIN_ITEM_SPECIFICITY` (3) are a-priori constants in
  `tools/video_marks.py`, applied by `keep_items` (step 3). Changing any of them is a
  visible, deliberate edit to the design spec's constants table — not a silent tuning knob
  inside a subagent prompt. `ITEM_CAP` is additionally bounded by
  `ITEM_CAP + len(TAIL_OFFSETS_S) <= FRAME_CAP` (so 13 is the ceiling); past it, kept items
  stop getting their own frame and silently degrade to `vision_confidence: "low"`.
