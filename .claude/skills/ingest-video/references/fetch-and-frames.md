# Fetch, sibling check and frame extraction notes

Supports `SKILL.md` steps 1, 2b and 5, including their rationale sections.

## Step 1: transcript_source and frame_paths

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

## Step 2b: why the grep, and routing by subject

This repo and the crypto parent (`~/repo/buibui-moon-trader-bot/`) follow overlapping
channels — Benjamin Cowen sits in both follow configs. `tools/route_dedup.py`'s identity
ledger is per-repo, so it cannot see the parent's work at all, and its `check` runs later in
this flow (step 7) — after the subagent spend this step exists to protect. The
`.cache/video/<id>/` cache only spares the re-download, never the re-ingest. So this grep
stays the first line of defence:

Route by subject, never by repo priority. The parent's scorer assumes 24/7 perp bars, so a
macro call scored there resolves against the wrong bars — and this repo holds ETF proxies
(USO/GLD/UUP) while pundits quote the underlying's units, so gold and DXY fail loudly while
oil fails quietly (USO sits in roughly the same $70-85 band as WTI without tracking it). Both
directions have a wrong home; the subject decides.

## Step 5: frame extraction details

Frames land at `.cache/video/<meta.video_id>/frames/f_NNNN.jpg` (already gitignored,
alongside the fetch cache). `extract_frames` downloads the video once into that same frames
directory (ffmpeg cannot seek the web-page URL directly), seeks the local copy per mark, then
deletes the downloaded video — only the frame JPEGs persist. `select()` is deterministic and
caps at `FRAME_CAP` = 15; frames can come from deixis phrases and spoken price levels even
when `item_ts` is short or empty — a video with zero routable candidates can still produce
frames via those triggers or the `SAFETY_SAMPLE_S` (300s) floor.

## Step 1 rationale

A batch of 4
carries four full transcripts; splitting them to disk and passing subagents a path saves
tens of thousands of tokens and changes no output.

The two `caption_langs_*` lists are the only provenance signal: `manual` is
author-written, `auto` is YouTube ASR, and both land on disk under the same `sub.<code>.vtt`
name.

## Step 2b rationale

Both repos name notes
`<date>-<author-slug>-<video_id>.md` (step 9), but each also holds legacy notes carrying
title slugs, so filenames are not reliably comparable across the two — nor even within one.
`video_id:` in frontmatter is present in every note on both sides.

## Step 5 rationale

`select()` returns at least the safety-sample marks for any `duration_s > 0`, so an
empty `marks` list only happens for a rare zero-duration video.
