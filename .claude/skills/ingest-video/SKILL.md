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

Sibling of `/ingest-x` (`.claude/skills/ingest-x/SKILL.md`) — same batch → per-item
sonnet subagent → ONE consolidated review digest → ONE approval → route shape, reusing
`tools/x_route.py::route_target` unchanged. This doc assumes the reader knows that
shape; it spells out in full only what differs: frame extraction driven by the
transcript (not scene-change), a two-pass split (text then vision), and deterministic
call-time resolution.

Handles **one or many** URLs (YouTube, or X video) in a single invocation. Collect
every URL the user pasted, then run the flow once over the whole set.

## Flow

### 1. Fetch the whole batch in ONE call

```bash
PYTHONPATH=. poetry run python tools/video_fetch.py <url1> <url2> … --json
```

`tools/video_fetch.py` always batches (unlike `tools/x_fetch.py`, it has no separate
single-URL path, so there is no `--batch` flag to pass). Output is a JSON **array**,
one element per URL **in the position it was requested** (`url` on each element is
that position's URL, correct even on a cache hit — do not assume array order
otherwise). Per element:

- `url` — the URL actually requested at this position
- `cached` — bool; `true` = zero network, served from `.cache/video/<id>/`
- `meta` — `{source, video_id, author, title, publish_ts_utc, duration_s, lang, url}`,
  or `null` when the video itself was unreachable
- `segments` — `[{ts_s, text, lang}, …]` (empty when there is no transcript)
- `frame_paths` — **always `[]` at this stage.** This CLI fetches metadata + transcript
  only; frames are extracted later (step 5), from a separate Python call, only for the
  moments pass 1 decides are worth a frame. Don't expect frames here — that is not a bug.
- `unavailable` — `null`, or a string reason

A caption-less video needs `GROQ_API_KEY` in the environment (`.env`) to produce a
transcript at all; without it, `unavailable` reports that explicitly (see shape 2 below).

### 2. Distinguish the three `unavailable` shapes — they are NOT the same

| Shape | `meta` | `unavailable` | Meaning | Action |
| --- | --- | --- | --- | --- |
| 1 | `null` | `"<reason>"` | The video itself is unreachable (bad URL, deleted, private, yt-dlp failure). Nothing else is known. | Tell the user by URL, drop it from the batch, continue with the rest. |
| 2 | `{...}` | `"<reason>"` | Metadata resolved fine, but no transcript could be produced — either "no captions available and no GROQ_API_KEY configured", or a Groq failure (HTTP error, audio extraction, chunking). | Because `meta` is populated, name the video (`meta.author`, `meta.title`) in the health note, and say **which of the two reasons** it was. Skip it from pass 1 onward — there is no transcript to feed. |
| 3 | `{...}` | `null` | Fully usable. | Proceed. |

Only shape-3 videos continue through the rest of this flow.

### 2b. Sibling-repo check — before spending any subagent tokens

This repo and the crypto parent (`~/repo/buibui-moon-trader-bot/`) follow overlapping
channels — Benjamin Cowen sits in both queues today, and 4 of his videos are already
ingested here. **This repo has NO routing-dedup layer at all** (the parent's
`tools/route_dedup.py` and its identity ledger were never ported, and the parent's own
ledger is per-repo regardless), so this grep is the only thing standing between you and a
double-ingest — of another repo's work *or*, until the port lands, of your own:

```bash
grep -rl -E 'video_id: *"?(<id1>|<id2>|…)"?' \
  ~/repo/buibui-moon-trader-bot/docs/plans/video-notes/ \
  docs/plans/video-notes/ 2>/dev/null
```

Both directories, deliberately. A hit in **this** repo's dir means you already ingested it
here — skip it outright; the `.cache/video/<id>/` cache only spares you the re-download, it
does not stop a re-ingest, so nothing else in this flow would catch it.

Grep the **frontmatter**, never the filename: the parent now names notes
`<date>-<author-slug>-<video_id>.md` (though its pre-#522 notes still carry title slugs)
while this repo uses `<title-slug>` throughout, so filenames are not comparable across the
two — nor even within the parent. `video_id:` in frontmatter is present in every note on
both sides.

**A hit is not automatically a skip — apply the subject rule:**

- equities / macro / gold / oil / DXY / bonds → **here**
- crypto instrument → **the parent** (it has perp data and the only working scorer)
- one video covering both legitimately yields rows in **both** repos. Two different calls,
  not a duplicate.

Route by **subject, never by repo priority.** The parent's scorer assumes 24/7 perp bars,
so a macro call scored there resolves against the wrong bars — and this repo holds ETF
*proxies* (USO/GLD/UUP) while pundits quote the **underlying's** units, so gold and DXY
fail loudly while **oil fails quietly** (USO sits in roughly the same $70-85 band as WTI
without tracking it). Both directions have a wrong home; the subject decides.

**Standing caveat:** this repo has no `tools/pundit_score.py`, so anything routed to
`docs/plans/pundit-calls.jsonl` here is a silent accumulator until that port lands. Say so
in the digest rather than implying a routed row will be scored.

### 3. Pass 1 — text-only subagent, one per video, pinned to sonnet

For each shape-3 video, dispatch a `general-purpose` subagent via the Task tool with
**`model: "sonnet"`** (do not inherit Opus) and `subagent_type: "general-purpose"`. Give
it:

- the video's `segments` array, verbatim
- `meta.publish_ts_utc`, `meta.author`, `meta.lang` — **context only**, for resolving a
  relative stated date ("last Monday") and inferring a speaker's timezone from channel
  locale. It must NOT compute a final call time itself — that happens in code, step 4.
- the inline classification rubric (below)

It must NOT read any repo, SoT, or memory file — the rubric is self-contained. Instruct
it to return ONLY this JSON:

```json
{
  "summary": "one paragraph, English",
  "stated_ts_utc": "ISO-8601 with an explicit UTC offset, or null",
  "stated_date_only": false,
  "stated_ts_raw": "verbatim quote or empty",
  "candidates": [
    {"ts": 252.0, "content_type": "setup|claim|mechanic", "specificity": 1-5, "gist": "..."}
  ]
}
```

**`stated_ts_utc` must carry an explicit UTC offset (e.g. `2026-07-14T08:00:00+08:00`),
or be `null` — never a bare local time.** `tools/video_calltime.py` rejects a naive
(offset-less) timestamp and silently falls back to publish time, so a subagent that
emits `2026-07-14T08:00:00` with no offset gets the same downstream result as emitting
nothing, just less honestly. Instruct the subagent: state the offset whenever the
speaker's timezone is inferable from context, otherwise emit `null` — never guess UTC.

Rank `candidates` by `specificity` descending. Keep the top `ITEM_CAP` (5 —
`tools/video_marks.py::ITEM_CAP`) as this video's kept items; report the rest as dropped,
with a one-line reason each (e.g. `specificity 2, below the top-5 cutoff`), for the
digest and the note.

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
- Pass `--date-only` only when `stated_date_only` was `true`.
- `--stated-raw` is always passed (an empty string is fine).
- `--ingested` is the current UTC time, e.g. `` $(date -u +%Y-%m-%dT%H:%M:%SZ) `` —
  needed so the tool can also compute `backlog`. **Capture this one value per batch and
  reuse it verbatim** everywhere `ingested_ts_utc` is written later (the Stream C line
  in step 8, the per-video note frontmatter in step 9) — do not call `date -u` again at
  those points; two separate calls could disagree by however long the batch took to
  process, and the field exists to say when THIS pipeline saw the video, not to be
  re-timestamped per write site.
- **If `meta.publish_ts_utc` is an empty string** (yt-dlp returned no timestamp field —
  rare, but possible), `video_calltime.py` raises `ValueError` rather than guessing.
  Treat that video as call-time-unresolvable and skip it with a health note; do not pass
  an empty string through.

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

Use these five fields **verbatim** in the digest, the ledger line, and the note. Never
have a subagent or the orchestrator derive `call_ts_utc` by date arithmetic — that is
exactly the look-ahead defect this tool exists to prevent (see Guardrails).

### 5. Select and extract frames

There is no CLI for this — `video_marks.select` and `video_fetch.extract_frames` are
library calls. Per shape-3 video: write its `meta` dict, `segments` array, and the kept
items' `ts` values (from step 3's top-`ITEM_CAP` candidates) to a scratch file with the
Write tool at `.cache/video/<meta.video_id>/marks_input.json` — concretely, for a video
whose `meta.video_id` is `dQw4w9WgXcQ`, the scratch file is
`.cache/video/dQw4w9WgXcQ/marks_input.json`. Then run (there is exactly ONE
`<video_id>` placeholder to substitute below, in the `Path(...).read_text()` line — for
that same example it becomes
`Path(".cache/video/dQw4w9WgXcQ/marks_input.json")`):

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from dataclasses import asdict
from pathlib import Path

from tools.video_fetch import VideoMeta, extract_frames
from tools.video_marks import TranscriptSegment, select

raw = json.loads(Path(".cache/video/<video_id>/marks_input.json").read_text())
meta = VideoMeta(**raw["meta"])
segments = [TranscriptSegment(**s) for s in raw["segments"]]

marks = select(segments, raw["item_ts"], meta.duration_s)  # cap defaults to FRAME_CAP (15)
dest_dir = Path(".cache/video") / meta.video_id / "frames"
frame_paths = extract_frames(meta, marks, dest_dir)

print(json.dumps({"marks": [asdict(m) for m in marks], "frame_paths": frame_paths}, indent=2))
PY
```

`marks_input.json` shape:
`{"meta": <verbatim meta dict from step 1>, "segments": <verbatim segments array from
step 1>, "item_ts": [<ts of each kept item from step 3>]}`. Frames land at
`.cache/video/<meta.video_id>/frames/f_NNNN.jpg` (already gitignored, alongside the
fetch cache). `extract_frames` downloads the video once into that same frames
directory (ffmpeg cannot seek the web-page URL directly), seeks the local copy per
mark, then deletes the downloaded video — only the frame JPEGs persist. `select()` is
deterministic and caps at `FRAME_CAP` = 15; frames can come from deixis phrases and
spoken price levels even when `item_ts` is short or empty — a video with zero routable
candidates can still produce frames via those triggers or the `SAFETY_SAMPLE_S` (300s)
floor.

**`marks` non-empty but `frame_paths` empty is a download failure, not "no chart" —
keep the two apart.** `select()` returns at least the safety-sample marks for any
`duration_s > 0`, so an empty `marks` list only happens for a (rare) zero-duration
video. If `marks` came back non-empty here but `frame_paths` is still `[]`, the
video's media download failed (network error, age-gate, region block) — record that
video's health note as "frame extraction failed (media download error)". Do NOT record
`chart_present: false` for that case; that flag is reserved for step 6, where frames
WERE produced and pass 2 actually looked at them and found no chart.

### 6. Pass 2 — vision subagent, one per video, pinned to sonnet

Dispatch whenever `frame_paths` from step 5 is non-empty — independent of whether step 3
found any candidates (see note above). When `frame_paths` is empty, which health note
you write depends on step 5's `marks` distinction:

- `marks` was also empty (a zero-duration video — rare): skip pass 2, treat every kept
  item from pass 1 as `vision_confidence: "low"`, `frame_path: null`, and record
  `chart_present: false` for that video in the digest and note.
- `marks` was non-empty (the ordinary empty-`frame_paths` case): this is the step-5
  download failure, not "no chart". Skip pass 2, still mark every kept item
  `vision_confidence: "low"` / `frame_path: null`, but write the step-5 health note
  ("frame extraction failed (media download error)") instead of `chart_present: false`
  — you never actually looked, so don't claim you did.

No subagent dispatch in either case.

Otherwise, dispatch a `general-purpose` subagent, **`model: "sonnet"`**,
`subagent_type: "general-purpose"`. Give it: the `frame_paths` list (it Reads each one —
vision), the `segments` array (context for what was said), the kept items from step 3
(`ts`, `content_type`, `gist`), and the item schema below. It must NOT read any repo,
SoT, or memory file. Instruct it to return ONLY this JSON:

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

- Where a frame shows a number/level/structure that **contradicts** the transcript, the
  chart wins: use the chart's value in `entry`/`stop`/`target`/`chart_read`, and record
  the transcript's original claim in `corrected_from`. When nothing was corrected, leave
  `corrected_from` empty.
- Anything the frames do **not** visually corroborate (no frame near that `ts`, or the
  nearest frame doesn't show what was said) gets `vision_confidence: "low"`. Reserve
  `"high"` for a frame that directly confirms the claim; `"medium"` for
  partial/ambiguous support.
- `raw_quote` stays in the transcript's original language (Chinese stays Chinese);
  `raw_quote_en` is always English (identical to `raw_quote` when the source is already
  English).
- `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` default it
  to `NOVEL` (non-blocking — routing uses `content_type` for those, same as `/ingest-x`).
- `chart_present: false` when no frame in this video shows a chart at all (pure
  talking-head) — still emit `items` from the transcript alone, all
  `vision_confidence: "low"`, `frame_path: null`.

**`confidence` vs `vision_confidence` — never merge these, they mean different things.**
`confidence` means the same thing across **every** source already in
`docs/plans/pundit-calls.jsonl`: the pundit's verbatim hedging phrase, or empty. This
pipeline does not extract that from a video (pass 2's contract is a visual-corroboration
read, not a hedging-language read — see the next step's Stream C schema for how the two
fields coexist without colliding). `vision_confidence` is **video-only** and records
whether pass 2 could visually corroborate the item against a frame — a property no other
source in the ledger has or needs. Keeping them as separate columns means a future
`GROUP BY confidence` (or any other query over the hedging-language column) stays honest
across every source, instead of silently mixing two incompatible populations.

### 7. ONE consolidated digest for the whole batch

Print a single table — one row per kept item across every video: video (title) · author ·
`call_ts_utc` (`call_ts_source`) · `ts` · `content_type` · `verdict` · proposed routing ·
`vision_confidence`. Below the table, per video: the pass-1 `summary`, the dropped
candidates with their reasons, the `chart_present` flag, and `backlog` when `true`. List
any shape-1 / shape-2 videos separately with their skip reason. **Write nothing yet.**

**For any video where `call_ts_source == "stated"`, also print `stated_ts_raw`,
`publish_ts_utc`, and the delta between the stated and publish times (e.g. "stated
2026-07-14T08:00:00+08:00 vs publish 2026-07-14T22:10:00+00:00, Δ14h").** A stated time
can move `call_ts_utc` up to `STATED_TS_MAX_LEAD_H` (168h) earlier than publish, and this
digest — specifically the human looking at it — is the only runtime control on that
input; `call_ts_utc (call_ts_source)` alone does not show the approver the quote that
justified the shift, so they cannot judge it. Showing the raw quote and the gap is what
lets the approver reject a fabricated or implausible timestamp before it reaches the
ledger.

### 8. Route on a single approval

After the user approves the batch, for each item compute the destination with
`tools/x_route.py::route_target(content_type, verdict)` (same taxonomy, unchanged
import — do not fork it) and append per this table, identical to `/ingest-x`:

| content_type | verdict | Append to |
| --- | --- | --- |
| setup | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
| mechanic | — | `docs/plans/mechanics-backlog.md` (a `-` list bullet) |
| claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
| claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |

Create the sink file with a one-line header if it does not exist. Report a one-line
result per item (routed → which file, or dropped → verdict).

**Stream C requires a real `symbol` — never route a `setup` item with `symbol: null` or
`symbol: ""` to `pundit-calls.jsonl`.** The parent's pundit scorer (not yet ported to
wifey) has no null check of its own; it would read the literal string `"None"` as a
symbol and pollute the scored ledger. If pass 2 could not resolve a symbol for a
`setup` item, treat it as a dropped candidate instead (reason: "no symbol resolved")
in the digest and the per-video note, not a Stream C write.

**Before appending a `claim`, grep the target sink for the gist first** — see Guardrails
on the inherited dedup gap.

**Deep-link rule — separator-aware, do not reintroduce the bug.** A YouTube timestamp
deep link must respect whatever the URL already has:

- if the URL contains `?` (e.g. `https://www.youtube.com/watch?v=<id>`), append
  `&t=<ts>s`
- if it does **not** (e.g. `https://youtu.be/<id>`), append `?t=<ts>s` instead

Blindly appending `&t=<ts>s` to a `youtu.be` URL produces
`https://youtu.be/<id>&t=90s`, which is **broken** — with no prior `?`, the `&` never
starts a query string and the timestamp is silently dropped by the player. Always branch
on whether `"?"` is already in the URL before appending.

X video URLs get **no** timestamp deep link at all (the platform doesn't support one) —
persist the plain URL, and carry the offset separately in the `ts` field instead (added
below for every source, not just X, so it's never only recoverable by re-parsing the
URL).

**Stream C line** (`pundit-calls.jsonl`, one JSON line, extends the `/ingest-x` schema):

```json
{"source":"youtube","author":"<handle>","url":"<url, with the deep link above for youtube>","ts":252.0,"call_ts_utc":"<resolved call time>","call_ts_source":"stated|publish","publish_ts_utc":"<publish time>","stated_ts_raw":"<verbatim quote or empty>","ingested_ts_utc":"<now>","backlog":false,"symbol":"...","direction":"...","entry":"...","stop":"...","target":"...","horizon":"...","confidence":"","vision_confidence":"high|medium|low","raw_quote":"<original language>","raw_quote_en":"<english>","corrected_from":"<transcript's original value, or empty>"}
```

`source` is `youtube` or `x-video` (from `meta.source`, verbatim — `tools/video_fetch.py`
already resolves this). **`confidence` is always written as an empty string for a video
row.** It keeps its `/ingest-x` meaning (the pundit's verbatim hedging phrase) — this
pipeline does not currently extract that from a video, so the honest value is "not
collected," not a repurposed visual-corroboration score. `vision_confidence` carries pass
2's high/medium/low rating instead; see the "never merge these" note in step 6.

### 9. Write the per-video note

One file per video (not per item) at
`docs/plans/video-notes/<date>-<author-slug>-<title-slug>.md` — already gitignored via
`docs/plans/`. `<date>` is the ingest date (UTC, `YYYY-MM-DD`). Slugify both the author
and title the same way:

```bash
slug() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+|-+$//g' | cut -c1-40; }
note_path="docs/plans/video-notes/$(date -u +%F)-$(slug "$AUTHOR")-$(slug "$TITLE").md"
```

If two videos in the same batch collide on the same path (same author, same day, near-
identical title), append the `video_id` to disambiguate rather than overwriting.

Contents:

- YAML frontmatter: `source`, `video_id`, `url`, `author`, `title`, `duration_s`, `lang`,
  `publish_ts_utc`, `call_ts_utc`, `call_ts_source`, `stated_ts_raw`, `ingested_ts_utc`,
  `backlog`, `chart_present`
- the pass-1 `summary`
- an items table: `ts` · `content_type` · `verdict` · routing outcome ·
  `vision_confidence` · `frame_path`
- the dropped candidates, with reasons
- frame references (path + `ts` for every extracted frame, including ones that produced
  no routed item — that's how you learn the sampling triggers are working)
- the transcript, original language, as fetched — **not proofread; it is scratch, not
  the artifact** (see spec §Transcript quality). Only routed items carry a reviewed
  `raw_quote`/`raw_quote_en` pair; the rest of the transcript is left as-is.

## Inline classification rubric (self-contained — paste into BOTH the pass-1 and pass-2 subagent prompts)

> A distilled snapshot of the SoT's Frozen / Closed / Parked state so each subagent
> classifies from the prompt alone. **Refresh from `project_todo_master.md`
> periodically** — treat as a de-biasing prior, not gospel; NOVEL still passes the
> human gate. This block is shared verbatim with `/ingest-x`'s rubric — keep the two in
> sync when either is refreshed. `content_type`: **setup** = a specific
> symbol+direction+levels trade call → Stream C; **mechanic** = an exit/risk/data/
> microstructure execution rule → Stream B; **claim** = a generalizable
> market-behaviour assertion → verdict below.

**Frozen — never propose a new TA detector.** The TA detector book (wicks,
marubozu, ORB, FVG, BOS/market-structure, EQH/EQL, order block, trend day,
engulfing, pin bar, inside bar, hammer, doji, morning/evening star, fib
retracement / golden zone, OTE, EMA — plus the stripped crypto-era detectors:
liquidity sweep, funding extreme, SMT, CVD divergence) is frozen under an
inherited category verdict (TA = negative-EV; no new boolean detectors, no
tp_r/gate/threshold sweeps). A claim that just restates one of these
candlestick/structure patterns → `FROZEN-CATEGORY`.

**Already-tested (verdict known → `ALREADY-TESTED`, drop unless materially new
evidence).** All six research sleeves audited on the ~500-name US-equity breadth
universe FAILED their gates; the free-data edge arc is CONCLUDED (honest exit,
2026-06-24):

- Absolute **trend** (multi-speed EWMAC): G2 FAIL — portfolio Sharpe −0.05,
  negative even pre-cost (a signal failure, not a cost failure).
- Cross-sectional **XS momentum** (relative strength): G3 FAIL — combined Sharpe
  −0.156 @2bps, corr_to_trend +0.62 (not even a diversification win).
- **Residualized XS-mom** (beta-stripped + skip-month): FAIL — committed L/S cell
  +0.15, DSR 0.44; the long-only +0.88 leg is survivorship/beta-confounded.
- **Low-vol / BAB** (beta-neutral L/S): FAIL — and the realized-β guardrail fired
  (β +3.9): ex-ante β-neutralization did not deliver market-neutrality.
- **Cross-asset TSMOM** (13-ETF basket): FAIL, clean — β-guardrail held; +0.41
  cost-free, never ≥0.7 gate. Futures-grade breadth is a paid-data question.
- **PEAD-lite** (seasonal SUE on free EDGAR data): FAIL — β-guardrail fired on
  the broad arm; the controlled mega arm showed *negative* drift. No PEAD in
  liquid large-caps net of cost on free data.
- DOW / day-of-week seasonality (e.g. "Monday is the weekly high → short"):
  parent-inherited verdict — base rate real but the tradeable edge decays OOS;
  the gorgeous version is look-ahead.
- Exit-policy fixes ("your stops are wrong, not your entries"): MFE/MAE
  diagnostic run, INCONCLUSIVE at n=22 — live ledger too young; re-audit before
  building anything.

**Parked / captured / blocked:**

- IPO post-hype dip-buy (long a faded recent IPO reclaiming its listing price):
  **already captured** as a thesis memo — a reassertion is "already in inbox",
  don't duplicate the H-row.
- Gap-fill magnet (unfilled gaps as intraday magnets, esp. in range regime):
  **already captured** as a thesis memo — same rule.
- Anything requiring paid data (Polygon intraday, options flow, L2/auction
  feeds, futures breadth): `NOVEL` in principle but **data-blocked** — say so in
  `gap_note` (paid data currently declined).

**`NOT-FALSIFIABLE`:** vibes / no testable prediction / unfalsifiable hindsight.
**`NOVEL`:** a genuinely new, testable, uncovered market-behaviour claim.

## Guardrails

- Never write to a stream before the user approves the digest — one approval covers the
  whole batch, exactly as in `/ingest-x`.
- `call_ts_utc` never comes from the model's own date arithmetic. Pass 1 only extracts a
  candidate `stated_ts_utc`/`stated_ts_raw`; the actual resolution — stated-vs-publish,
  bounding, backlog — always runs through `tools/video_calltime.py` (step 4). A subagent
  or the orchestrator computing this by hand reintroduces the exact look-ahead defect the
  tool exists to prevent (see the spec's "ledger-integrity constraint").
- Output is a hypothesis/setup/mechanic to TEST — never an "add a detector" task. The
  TA detector book is frozen, same as `/ingest-x`.
- The free-data edge arc is CONCLUDED: routing a NOVEL claim into
  `thesis-inbox.md` is *capture*, not a commitment to test — never start a new
  edge-hunt from an ingested claim without an explicit user go (same as
  `/ingest-x`).
- The inherited sink-grep dedup gap (`/ingest-x` iteration-2 backlog item #7, never
  built): nothing here checks whether a claim already exists in the target sink before
  appending. Bulk video ingest makes this bite harder than single X posts, because a
  pundit routinely repeats the same thesis across a week of uploads. Before appending a
  `claim` that reads familiar, grep `docs/plans/thesis-inbox.md` yourself; there is no
  automated guard against a duplicate H-row.
- Two subagent passes, both pinned to `model: "sonnet"` — never let either inherit Opus.
  Neither may read any repo, SoT, or memory file; the rubric above is the only context
  either needs beyond the video's own transcript/frames.
- `FRAME_CAP` (15) and `ITEM_CAP` (5) are a-priori constants in
  `tools/video_marks.py`. Raising either is a visible, deliberate change to the design
  spec's constants table — not a silent tuning knob inside a subagent prompt.
