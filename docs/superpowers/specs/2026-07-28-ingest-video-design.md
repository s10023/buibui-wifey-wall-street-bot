# `/ingest-video` — video → research pipeline (design)

> **Ported from parent** `s10023/buibui-moon-trader-bot` PR #513 (`8e7244d`) on
> 2026-07-31 as historical design context for `/ingest-video`. Crypto examples and
> parent-only file references (e.g. `tools/pundit_score.py`) read in parent context;
> wifey's equity re-flavor lives in the skill's inline rubric.

- **Date:** 2026-07-28
- **Status:** design approved, plan pending
- **Sibling:** `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md` (`/ingest-x`, the reference architecture)
- **Memory:** `project_video_ingest_pipeline.md`, `reference_external_repos_eval.md`

## Goal and success metric

**Goal.** Turn a pasted YouTube or X video URL — including Chinese-language video — into
routed, attributable research items in the existing three-stream pipeline, plus a durable
per-video note, at a token cost that does not require the main thread to look at the video.

**Success metric (one line).** A batch of pasted URLs produces correctly-attributed routed items
behind exactly one human review gate, with `call_ts_utc` never later than the video's publish time
and never unverifiably earlier, at ≤ `FRAME_CAP` extracted frames per video and zero stream writes
before approval.

## Non-goals

Channel tracking, the RSS unwatched-queue, YouTube authentication, playlists, live streams, and
thread-walking are all out. So is any change to the verdict taxonomy or the three sink files.
This skill adds a new *input* to the research pipeline; it does not redesign the pipeline.

## Background: why not adopt `claude-video`

`bradautomates/claude-video` (MIT) solves video → frames + transcript and was the filed
adapter of record. It is still the reference for the unglamorous parts, but it is not adopted as
a dependency, for two reasons that both bear directly on the requirements:

1. It selects frames by **scene change / keyframe** density (`efficient` ≈ 50, `balanced` ≈ 100).
   The requirement here is the opposite — frames only where the *transcript* indicates something
   is being pointed at. Scene-change selection on a talking-head trading video returns ~100
   near-identical frames of a person, and misses the one moment the chart is annotated.
2. Its `/watch` hands the full frame set to the invoking thread. This design's token argument
   depends on frames never reaching the main thread.

**Decision: build our own tool, crib its solved edge cases** — yt-dlp format selection, caption
format priority, age-gate handling, audio bitrate for ASR accuracy, ffmpeg frame extraction flags.
Read the source for those; own the architecture and the frame-selection logic.

## Architecture

Three new units with independent responsibilities, plus reused routing.

### A-priori constants

Fixed before any tuning, so a later change is a visible decision rather than a drift:

| Constant | Value | Meaning |
| --- | --- | --- |
| `ITEM_CAP` | 5 | Max routed items per video; the rest are shown as dropped candidates |
| `FRAME_CAP` | 15 | Hard ceiling on extracted frames per video |
| `DEDUP_WINDOW_S` | 45 | Marks inside this window collapse to one |
| `SAFETY_SAMPLE_S` | 300 | One frame per 5 minutes regardless of triggers |
| `BACKLOG_THRESHOLD_H` | 24 | Publish-to-ingest gap above which `backlog: true` |
| `STATED_TS_MAX_LEAD_H` | 168 | Max hours a stated call time may precede publish before it is rejected |

### `tools/video_fetch.py` — fetch and materialise

URL → `VideoAsset`. Deterministic, no LLM calls.

- Resolves metadata via `yt-dlp` (id, author, title, **publish time**, duration, language).
- Transcript: prefers existing captions in any language; falls back to Groq
  `whisper-large-v3` over extracted audio. Both normalise to one shape:
  `list[TranscriptSegment(ts_s: float, text: str, lang: str)]`.
- `extract_frames(marks)` — ffmpeg, at a caller-supplied timestamp list. Never speculative.
- Per-video dedup cache at `.cache/video/<id>/` (gitignored): metadata JSON, transcript JSON,
  frames. A re-run returns `cached: true` with zero network.
- Batch mode with a randomized cooldown *between network fetches only*, skipped on cache hits
  and before the first fetch — same contract as `tools/x_fetch.py`.
- `sleep`, `rng`, `get` (HTTP), and `run` (subprocess) are injected so tests are network-free.

### `tools/video_marks.py` — frame selection

Pure, no I/O.
`select(segments, item_ts, duration_s, *, cap=FRAME_CAP, window_s=DEDUP_WINDOW_S, sample_s=SAFETY_SAMPLE_S) -> list[FrameMark]`.

This is the novel unit and the reason `claude-video` is not adopted. Three trigger classes:

| Trigger | Example | Rationale |
| --- | --- | --- |
| Deictic phrase | "look here", "taking a long here", "this structure", "这里", "这个位置" | The speaker is pointing at something only the frame shows |
| Spoken price level | a numeric token near a symbol mention | The chart is the ground truth for the number |
| Item boundary | timestamps of pass-1 ranked items | Every routed item should carry a frame where one exists |
| Safety sample | 1 per `SAFETY_SAMPLE_S` | Prevents blindness between pointing moments |

Marks are deduplicated inside a time window, so a twenty-second riff about "here" yields one
frame rather than eight near-identical ones, then ranked and capped.

Being pure makes the trigger vocabulary directly unit-testable, which matters because it is the
piece most likely to need tuning against real videos.

### `.claude/skills/ingest-video/SKILL.md` — orchestration

No logic; dispatch and gating only. Two subagent passes, both **pinned to sonnet** with
self-contained inline rubrics (no SoT or memory re-reads inside the subagent):

- **Pass 1 — text only.** Transcript in. Segments the video, ranks candidate items by
  specificity, emits item timestamps and deictic moments. Cheap; decides which frames exist.
- **Pass 2 — vision.** Selected frames plus transcript in. Produces item JSONs, corrects
  transcript numbers against the charts, flags what it cannot corroborate.

Then one consolidated digest for the whole batch, one approval, then writes.

### Routing

Reuses `tools/x_route.py::route_target(content_type, verdict)` **unchanged**. Same three streams,
same 4-bucket taxonomy, no fork. The inline classification rubric is shared with `/ingest-x` and
refreshed from `project_todo_master.md` on the same cadence.

## Data flow

```text
URLs → video_fetch.batch()                     # yt-dlp metadata + captions, or audio → Groq
     → transcript segments [(ts, text, lang)]
     → PASS 1 subagent (sonnet, text only)     # segment, rank candidates, emit marks
     → video_marks.select(...)                 # pure: merge, dedup, cap
     → video_fetch.extract_frames(marks)       # ffmpeg, 8–15 images
     → PASS 2 subagent (sonnet, vision)        # items JSON, chart-corrected, confidence
     → ONE digest across the batch → ONE approval
     → writes: 3 streams (via x_route) + per-video note
```

The two-pass split is load-bearing: pass 1 is cheap text and decides *which* frames exist, so
frames are never extracted speculatively. That is what keeps a 38-minute video at 8–15 images
instead of ~100.

## Schemas

### Item (extends the `/ingest-x` extraction schema)

The `/ingest-x` fields are unchanged. Four are added:

| Field | Meaning |
| --- | --- |
| `ts` | offset in the video, seconds |
| `frame_path` | local frame for this item, or null |
| `vision_confidence` | `high` / `medium` / `low` — low when pass 2 could not corroborate visually. Distinct from `confidence` (the pundit's verbatim hedging phrase, unchanged from `/ingest-x`) — `confidence` is retained on video rows but always written empty, since this pipeline does not extract hedging language from a video |
| `raw_quote_en` | English translation; `raw_quote` stays in the original language |

Where pass 2 reads a number off the chart that contradicts the transcript, the chart wins and the
item records `corrected_from`.

### Stream C line (`docs/plans/pundit-calls.jsonl`)

Matches the existing pundit-call schema, with the video additions:

```json
{"source":"youtube","author":"<handle>","url":"<url>&t=<ts>s","ts":252.0,"call_ts_utc":"<resolved call time>","call_ts_source":"stated|publish","publish_ts_utc":"<publish time>","stated_ts_raw":"<verbatim quote or empty>","ingested_ts_utc":"<now>","backlog":false,"symbol":"...","direction":"...","entry":"...","stop":"...","target":"...","horizon":"...","confidence":"","vision_confidence":"high|medium|low","raw_quote":"<original language>","raw_quote_en":"<english>"}
```

`source` is `youtube` or `x-video`. The URL carries a timestamp deep link so a scored call points
at the moment it was made rather than a 38-minute video; X does not support this, so those
degrade to a plain URL plus the `ts` field.

### Per-video note (`docs/plans/video-notes/<date>-<author>-<slug>.md`, gitignored)

YAML frontmatter (source, id, author, duration, language, publish time, ingest time) followed by:
summary, an items table with timestamps and routing outcome, dropped candidates with the reason,
frame references, and the cleaned bilingual transcript.

Dropped candidates appear in the digest and the note but never in the streams, so the effect of
the cap is visible and can be raised if it is cutting real signal.

## Language handling

Chinese is supported end to end. Most Chinese YouTube channels carry auto-captions that yt-dlp
pulls for free; Groq `whisper-large-v3` handles the rest, including accented Mandarin.

The transcript is retained in the original language with an English translation. Extraction JSON
is English. **`raw_quote` stays in the original language** with `raw_quote_en` alongside it,
because `pundit-calls.jsonl` is scoring evidence and a translated paraphrase would quietly
degrade the ledger's fidelity.

## Correctness: the ledger-integrity constraint

**`call_ts_utc` is when the call was made — never the ingest time.**

`tools/pundit_score.py` resolves every call in `pundit-calls.jsonl` against OHLCV forward from
`call_ts_utc`. Getting this wrong on a backlog video would score a three-week-old call against the
three weeks of price action the pundit already knew about — a look-ahead defect that would inflate
every author's hit rate and corrupt `docs/plans/pundit-priors.json`, which feeds the daily brief
and the F2 trade card.

### Resolution order: stated time, then publish time

Speakers often open with the date and time ("it's Monday the 28th, 8am"). That is closer to when
the call was actually made than the publish timestamp, so it is preferred — **but it is
pundit-supplied and unverifiable, and it moves in the look-ahead-permitting direction.** Publish
time is an upper bound taken from platform metadata; a stated time is a claim. A speaker who
records late and states an earlier time would be credited with price action they had already seen.

So the stated time is preferred but bounded:

1. Pass 1 extracts `stated_ts` when the speaker states a date and/or time near the start.
2. Accept it only when **all** hold:
   - it parses, and its timezone is explicit or inferable from the channel locale (the inference
     is recorded, not silently applied);
   - `stated_ts < publish_ts` — a stated time at or after publish is nonsense and is rejected;
   - `publish_ts - stated_ts <= 168h` — a video claiming to predate publication by more than a
     week is a re-upload or a false claim.
3. When only a **date** is stated with no resolvable time of day, use the **end** of that date in
   the resolved zone, clamped below `publish_ts`. The conservative edge, so the pundit is never
   credited with intraday movement they may not have seen.
4. Otherwise `call_ts_utc = publish_ts`.

### Fields always persisted

| Field | Meaning |
| --- | --- |
| `call_ts_utc` | The resolved call time used by the scorer |
| `call_ts_source` | `stated` or `publish` |
| `publish_ts_utc` | Always recorded, whichever source won |
| `stated_ts_raw` | The verbatim quote the stated time came from, for audit |
| `ingested_ts_utc` | When this pipeline saw it |
| `backlog` | `true` when publish precedes ingest by more than `BACKLOG_THRESHOLD_H` |

`backlog` is computed from **publish** time, not stated time — it describes our ingest lag, not
the pundit's.

### Why `call_ts_source` is worth persisting

It makes an unverifiable input testable. If an author's `stated`-sourced calls score
systematically better than their `publish`-sourced ones, that is evidence of a gamed timestamp,
and it is visible in the ledger rather than silently inflating their prior. Deferred as an audit,
not built now — but the field has to exist from day one or the evidence is unrecoverable.

This is also why the human gate stays mandatory regardless of batch size.

## Transcript quality

No attempt is made to produce a clean transcript. **The transcript is scratch; the extraction JSON
is the artifact.** A transcript error matters only if it changes a routed claim, and every routed
claim passes the human digest. Three cheap layers instead of one expensive one:

1. A domain vocabulary hint passed to ASR (FVG, OTE, liquidity sweep, BOS, ticker names) fixes
   most jargon mangling at the source.
2. Frames are ground truth: pass 2 corrects transcript numbers against the chart it is reading.
3. Anything pass 2 cannot corroborate visually is flagged `vision_confidence: low` rather than
   silently repaired.

## Failure modes

| Case | Behaviour |
| --- | --- |
| Unavailable / private / age-gated | `unavailable` reason returned; falls back to the manual-paste path, as `x_fetch.py` does |
| Groq failure or rate limit | That video degrades to captions-only, or is skipped with a health note. Per-video try/except — one bad video never kills the batch, mirroring `run_backfill`'s per-symbol resilience |
| No captions and no ASR available | Skipped with a health note naming the reason |
| Audio > Groq's 25 MB cap | Chunked. Low-bitrate opus keeps roughly three hours under the cap |
| No chart on screen | Pass 2 returns `chart_present: false`; the note records it, so you learn which channels are worth frames |
| Transcript contradicts chart | Chart wins; item records `corrected_from` — treated as evidence, not silently overwritten |

## Testing

- `video_marks` is pure and carries most of the tests: trigger vocabulary in English and Chinese,
  dedup window, cap, ranking order.
- `video_fetch` injects `run`, `get`, `sleep`, `rng`; tests run against recorded yt-dlp JSON and a
  fake ffmpeg. **No network in the suite**, matching `x_fetch.py`'s existing contract.
- Fixture transcripts include a Chinese one.
- Nothing touches `analytics.db`. Blast radius is `docs/plans/` and `.cache/` only.
- `make lint-py`, `make typecheck` (mypy strict), `make test` all green before merge.

## Dependencies

- `yt-dlp` — new Poetry dependency.
- `ffmpeg` — already present on the box.
- `GROQ_API_KEY` — new, in `.env`, free tier. Used only for caption-less videos.

## Known gaps carried forward

`/ingest-x` iteration 2 never built sink-grep routing dedup (backlog item #7). Bulk video ingest
makes it bite harder, because a pundit repeats the same thesis across a week of uploads. The MVP
**inherits this gap** rather than fixing it. Recorded here so it is not rediscovered as a surprise.

## Deferred, and cheap when wanted

Channel tracking does not need YouTube auth. Every channel exposes a public RSS feed at
`/feeds/videos.xml?channel_id=…`, so a committed channel list plus RSS plus a watched-ledger gives
the unwatched queue with no account and no API quota. The `.cache/video/<id>/` dedup cache already
serves as that watched-ledger, so the MVP pays nothing extra for it. Authentication
(`yt-dlp --cookies-from-browser`) becomes necessary only to read the actual subscriptions list or
members-only videos.
