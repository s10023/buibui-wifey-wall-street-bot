---
name: ingest-video
description: >
  Ingest one or more YouTube or X video URLs, Chinese-language video included,
  into the research pipeline: transcript and transcript-selected chart frames are
  read, call time is resolved in code, each item is classified, and after one
  human review gate for the whole batch it is routed to the thesis inbox, the
  mechanics backlog or the pundit-calls ledger, plus a per-video note. Invoke when
  the user says "/ingest-video", pastes YouTube or X video URLs, or asks to ingest
  a video.
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

## Gotchas

One line per hazard; the narrative behind each is in `references/`.

- **Batch JSON stays on disk.** Redirect `video_fetch.py` to a file and pass subagents a `transcript_path`, never the transcript (step 1).
- **`asr_whisper_captions_missed` is recoverable.** Re-run that video, and write `transcript_source` into the note from step 1's JSON ([notes](references/fetch-and-frames.md#step-1-transcript_source-and-frame_paths)).
- **Grep the sibling repo first, by frontmatter.** Match `video_id:` in both repos' notes, never filenames, and route by subject ([notes](references/fetch-and-frames.md#step-2b-why-the-grep-and-routing-by-subject)).
- **Two subagents at once.** A third launch is refused, so pipeline steps 3 to 6 in pairs (step 3).
- **Sonnet-pinned is not context-free.** The no-file-reads instruction bounds what a subagent reads, not what it knows ([notes](references/pass-prompts.md#pass-2-isolation-warning)).
- **Call time is never computed in a prompt.** A stated time needs an explicit UTC offset or `stated_date_only`; resolution runs through `tools/video_calltime.py` (step 4).
- **`item_cap` is applied by the prompt.** Write the `hint` value into the ranking rule as a literal ([notes](references/pass1-selection-notes.md#handle-matching-and-item_cap)).
- **A thin yield is a cap artifact first.** `keep_items` favours earlier material at a tie, so check `dropped` ([notes](references/pass1-selection-notes.md#tie-break-and-cutoff)).
- **Only `retrospective` drops a `setup` in code.** `is_intro_recap` drops nothing, and step 8 must pass both suppressors explicitly ([notes](references/pass1-selection-notes.md#recap-flags)).
- **Empty `frame_paths` with non-empty `marks` is a download failure.** Do not record `chart_present: false` for it (steps 5 and 6).
- **The chart wins only on a drawn value.** A ticker reading or a symbol normalisation is not a `corrected_from` correction ([notes](references/pass-prompts.md#pass-2-return-contract)).
- **`confidence` and `vision_confidence` never merge.** Video rows write `confidence` empty ([notes](references/pass-prompts.md#confidence-versus-vision_confidence)).
- **Dedup is advisory and the board view is read-only.** Only `already_routed` blocks, and `mark` runs after the write, never at check time ([notes](references/routing-and-note-notes.md#step-7-the-board-view-is-read-only)).
- **`target` and `entry` are a machine-parsed contract.** A bare ladder, no ranges; an invalidation level is a stop; run `--check-levels` (step 8, [notes](references/routing-and-note-notes.md#step-8-invalidation-levels)).
- **Deep links are separator-aware.** `&t=` after `?`, otherwise `?t=` ([notes](references/routing-and-note-notes.md#step-8-deep-links)).
- **Note filename uses `video_id`.** A title slug collapses for non-Latin titles ([notes](references/routing-and-note-notes.md#step-9-note-filename)).

## Flow

### 1. Fetch the whole batch in ONE call

```bash
PYTHONPATH=. poetry run python tools/video_fetch.py <url1> <url2> … --json \
  > .cache/video/_batch.json
```

**Redirect to a file — never let the batch JSON print into your context.** ([why](references/fetch-and-frames.md#step-1-rationale)) The split below prints a compact index
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
  recap window. ([why](references/fetch-and-frames.md#step-1-rationale))
- `segments` — `[{ts_s, text, lang}, …]` (empty when there is no transcript)
- `transcript_source` — `manual_captions` | `auto_captions` | `asr_whisper` |
  `captions_unknown` | `asr_whisper_captions_missed` | `""`.
  Re-run a video flagged `asr_whisper_captions_missed` (recoverable) and carry `transcript_source` into the note frontmatter verbatim; ASR quotes are quoted-with-uncertainty. Why: [references/fetch-and-frames.md#step-1-transcript_source-and-frame_paths](references/fetch-and-frames.md#step-1-transcript_source-and-frame_paths).
- `frame_paths` — always `[]` at this stage; frames are extracted in step 5 ([references/fetch-and-frames.md#step-1-transcript_source-and-frame_paths](references/fetch-and-frames.md#step-1-transcript_source-and-frame_paths)).
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

Grep both repos' video notes for the video ids before any subagent spend; `tools/route_dedup.py` is per-repo and the cache only spares the re-download ([references/fetch-and-frames.md#step-2b-why-the-grep-and-routing-by-subject](references/fetch-and-frames.md#step-2b-why-the-grep-and-routing-by-subject)):

```bash
grep -rl -E 'video_id: *"?(<id1>|<id2>|…)"?' \
  ~/repo/buibui-moon-trader-bot/docs/plans/video-notes/ \
  docs/plans/video-notes/ 2>/dev/null
```

Both directories, deliberately. A hit in this repo's dir means you already ingested it here —
skip it outright, before any subagent runs.

Grep the frontmatter, never the filename. ([why](references/fetch-and-frames.md#step-2b-rationale))

A hit is not automatically a skip — apply the subject rule:

- equities / macro / gold / oil / DXY / bonds → here
- crypto instrument → the parent (it has perp data and the only working scorer)
- one video covering both legitimately yields rows in both repos. Two different calls, not a
  duplicate.

Route by subject, never by repo priority ([references/fetch-and-frames.md#step-2b-why-the-grep-and-routing-by-subject](references/fetch-and-frames.md#step-2b-why-the-grep-and-routing-by-subject)).

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
  itself; `segments` is the `"segments"` key inside it. ([why](references/pass1-selection-notes.md#step-3-rationale))
- `meta.publish_ts_utc`, `meta.author`, `meta.lang` — context only, for resolving a relative
  stated date ("last Monday") and inferring a speaker's timezone from channel locale. It must
  not compute a final call time itself — that happens in code, step 4.
- the channel's `intro_recap_s` and `item_cap` (see below) — numbers, not rules to re-derive
- the inline classification rubric (below)

First, ask the config for this channel's two per-video knobs:

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py hint --author "<meta.author>" || true
```

`hint` is a pure local config read. A missing `config/youtube_channels.toml` makes it exit 1, which is treated exactly like `matched: false` (both defaults; that is what `|| true` is for) ([references/pass1-selection-notes.md#hint-config](references/pass1-selection-notes.md#hint-config)).

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

In this repo the chapter window is the only trim that can fire ([references/pass1-selection-notes.md#chapter-window](references/pass1-selection-notes.md#chapter-window)).

`item_cap` is applied by the pass-1 prompt, not by code, so a value fetched here and not
passed on does nothing — carry it into the ranking rule as a literal. ([why](references/pass1-selection-notes.md#step-3-rationale))

A channel with no `handle` never matches, and the `item_cap` override is nearly inert here ([references/pass1-selection-notes.md#handle-matching-and-item_cap](references/pass1-selection-notes.md#handle-matching-and-item_cap)).

**If the effective window came back non-zero** — the chapter-derived `recap_window_s` when it
is positive, `intro_recap_s` otherwise — set `is_intro_recap: true` on every candidate with
`ts < window`. For a `setup`, also set `retrospective: true` — a call lifted from a recap
block is a past call that would otherwise be stamped with today's `call_ts_utc` and score the
author on an already-resolved trade. For a `claim` or `mechanic`, set `is_intro_recap` and
keep the candidate: an idea stays portable regardless of when in the video it was said.

`retrospective` drops a `setup` in code; `is_intro_recap` drops nothing, so step 7c prints both flags and step 8 must pass the suppressors explicitly ([references/pass1-selection-notes.md#recap-flags](references/pass1-selection-notes.md#recap-flags)).

Instruct it not to read any repo, SoT, or memory file — the rubric below is written to stand
alone. That bounds what it reads, not what it knows (see the isolation warning under pass 2).
Read [references/pass-prompts.md](references/pass-prompts.md#pass-1-return-contract) and paste it into the pass-1 prompt: it carries the JSON schema, the explicit-UTC-offset rule and the date-only rule.

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

Read a thin yield as a cap artifact (check `dropped`), and do not hand-roll the cutoff ([references/pass1-selection-notes.md#tie-break-and-cutoff](references/pass1-selection-notes.md#tie-break-and-cutoff)).

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
  back to publish. ([why](references/pass1-selection-notes.md#step-4-rationale))
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

Frames land at `.cache/video/<meta.video_id>/frames/f_NNNN.jpg`; `select()` caps at `FRAME_CAP` = 15 ([references/fetch-and-frames.md#step-5-frame-extraction-details](references/fetch-and-frames.md#step-5-frame-extraction-details)).

`marks` non-empty but `frame_paths` empty is a download failure, not "no chart" — keep the
two apart. ([why](references/fetch-and-frames.md#step-5-rationale)) If `marks` came back
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

A `general-purpose` subagent is not context-free: not reading files bounds what it reads, not what it knows ([references/pass-prompts.md#pass-2-isolation-warning](references/pass-prompts.md#pass-2-isolation-warning)).

Read [references/pass-prompts.md](references/pass-prompts.md#pass-2-return-contract) and paste it into the pass-2 prompt: it carries the JSON schema and the rules for the subagent.

**`confidence` vs `vision_confidence` — never merge these, they mean different things.**
Why they stay separate: [references/pass-prompts.md#confidence-versus-vision_confidence](references/pass-prompts.md#confidence-versus-vision_confidence).

### 7. ONE consolidated digest for the whole batch

The digest has three parts, in this order. ([why](references/routing-and-note-notes.md#step-7-rationale))

Read [references/digest-and-note-format.md#step-7-the-three-digest-parts](references/digest-and-note-format.md#step-7-the-three-digest-parts) and print the digest exactly in that shape: 7a per-video summaries, then 7b the board view (read-only, outside the routing path), then 7c the routing table with both flags visible. Write nothing yet.

Why summaries come first: [references/routing-and-note-notes.md#step-7-why-summaries-come-first](references/routing-and-note-notes.md#step-7-why-summaries-come-first).

**For any video where `call_ts_source == "stated"`, also print `stated_ts_raw`,
`publish_ts_utc`, and the delta between the stated and publish times** (e.g. "stated
2026-07-14T08:00:00+08:00 vs publish 2026-07-14T22:10:00+00:00, Δ14h"). ([why](references/routing-and-note-notes.md#step-7-rationale))

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
  entry, or drop. ([why](references/routing-and-note-notes.md#step-7-rationale))
- `semantic_scope` says what the near-duplicate pass actually compared against:
  `all-entries` (Streams A and B), `same-source` (Stream C — only this video's own earlier
  rows, never another author's), or `none`. Report it; never let an empty `candidates` list
  read as "checked against everything and clean".
- Discount a hit whose `shared_levels` are all round 4-digit numbers that could be years.
  ([why](references/routing-and-note-notes.md#step-7-rationale))

On Streams A and B an empty `candidates` list means nothing scored above threshold, not "not a duplicate" ([references/routing-and-note-notes.md#step-7-streams-a-and-b-scope](references/routing-and-note-notes.md#step-7-streams-a-and-b-scope)).

Then run the intra-video pass, once per video that has two or more Stream-C-bound items.
([why](references/routing-and-note-notes.md#step-7-rationale)) Write the video's pending Stream C items (the pass-2 item dicts
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

Pass both suppressors — they are the whole point of pass 1 setting them. ([why](references/routing-and-note-notes.md#step-8-rationale)) A `setup` with either flag returns `None`.

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
`symbol: ""` to `pundit-calls.jsonl`. ([why](references/routing-and-note-notes.md#step-8-rationale)) If pass 2 could not resolve
a symbol for a `setup` item, treat it as a dropped candidate instead (reason: "no symbol
resolved") in the digest and the per-video note, not a Stream C write.

**After each successful append, record it:**

```bash
PYTHONPATH=. poetry run python tools/route_dedup.py mark \
  --source-id <meta.video_id> --item-ts <item ts> --sink <sink path>
```

`mark` runs after the write, never before — marking at check time would let an abandoned
review consume the id and dedup away the real append later. Never mark a dropped item.

Stream C's near-duplicate exemption is across sources only; within one `source_id` step 7's `same-source` scope and `pairs` still run ([references/routing-and-note-notes.md#step-8-stream-c-dedup-scope](references/routing-and-note-notes.md#step-8-stream-c-dedup-scope)).

**Deep-link rule — separator-aware.** A YouTube timestamp deep link must respect whatever the
URL already has:

- if the URL contains `?` (e.g. `https://www.youtube.com/watch?v=<id>`), append `&t=<ts>s`
- if it does not (e.g. `https://youtu.be/<id>`), append `?t=<ts>s` instead

Never append `&t=` to a URL with no `?`: the timestamp is silently dropped ([references/routing-and-note-notes.md#step-8-deep-links](references/routing-and-note-notes.md#step-8-deep-links)).

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
re-breaks it, because the constraint is on the whole field, not its leading number. ([why](references/routing-and-note-notes.md#step-8-rationale))

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

A level phrased as an invalidation is a stop, never a `target`; the check cannot see a one-level row, so read the phrasing yourself ([references/routing-and-note-notes.md#step-8-invalidation-levels](references/routing-and-note-notes.md#step-8-invalidation-levels)).

([why](references/routing-and-note-notes.md#step-8-rationale))

- A level serving as both entry and stop is zero risk, and the check flags it correctly.
  Encode it as entry + target with the stop left unstated — never invent a gap the pundit did
  not give.
- A row with no numeric level at all is a dropped candidate ("nothing scoreable"), not a
  Stream C write.

([why](references/routing-and-note-notes.md#step-8-rationale))

`source` is `youtube` or `x-video` (from `meta.source`, verbatim — `tools/video_fetch.py`
already resolves this). `confidence` is always written as an empty string for a video row. ([why](references/routing-and-note-notes.md#step-8-rationale)) `vision_confidence` carries pass 2's high/medium/low rating
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

Name the note by `video_id`, never by a title slug ([references/routing-and-note-notes.md#step-9-note-filename](references/routing-and-note-notes.md#step-9-note-filename)).

Contents: read [references/digest-and-note-format.md#step-9-note-contents](references/digest-and-note-format.md#step-9-note-contents) and write the note in that shape (YAML frontmatter, summary, items table, dropped candidates, frame references, then the transcript appended by code below).

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

Read [references/classification-rubric.md](references/classification-rubric.md) and paste it verbatim into BOTH the pass-1 and pass-2 subagent prompts.

## Guardrails

- Never write to a stream before the user approves the digest — one approval covers the
  whole batch, exactly as in `/ingest-x`.
- `call_ts_utc` never comes from the model's own date arithmetic. Pass 1 only extracts a
  candidate `stated_ts_utc`/`stated_ts_raw`; the actual resolution — stated-vs-publish,
  bounding, backlog — always runs through `tools/video_calltime.py` (step 4). ([why](references/routing-and-note-notes.md#guardrails-rationale))
- Output is a hypothesis/setup/mechanic to test — never an "add a detector" task. The TA
  detector book is frozen, same as `/ingest-x`.
- The free-data edge arc is concluded: routing a NOVEL claim into `thesis-inbox.md` is
  capture, not a commitment to test — never start a new edge-hunt from an ingested claim
  without an explicit user go (same as `/ingest-x`).
- Routing dedup is `tools/route_dedup.py` — `check` in step 7, `mark` in step 8, and it is
  advisory except for `already_routed`. Its semantic pass surfaces candidates for the digest
  and never drops anything: a false positive costs a glance, a false negative costs a
  corrupted sink, and your review gate stays the decision point. ([why](references/routing-and-note-notes.md#guardrails-rationale)) Read a familiar-sounding `claim` yourself.
- Two subagent passes, both pinned to `model: "sonnet"` — never let either inherit Opus.
  Neither may read any repo, SoT, or memory file; the rubric above is the only context
  either needs beyond the video's own transcript/frames.
- `FRAME_CAP` (15), `ITEM_CAP` (12) and `MIN_ITEM_SPECIFICITY` (3) are a-priori constants in
  `tools/video_marks.py`, applied by `keep_items` (step 3). Changing any of them is a
  visible, deliberate edit to the design spec's constants table — not a silent tuning knob
  inside a subagent prompt. `ITEM_CAP` is additionally bounded by
  `ITEM_CAP + len(TAIL_OFFSETS_S) <= FRAME_CAP` (so 13 is the ceiling); past it, kept items
  stop getting their own frame and silently degrade to `vision_confidence: "low"`.
