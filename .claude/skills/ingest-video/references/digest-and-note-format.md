# Digest and per-video note format

Supports `SKILL.md` steps 7 and 9.

## Step 7: the three digest parts

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

The board view is read-only and strictly outside the routing path: it writes no sink and influences no `content_type` / `verdict` / dedup ([routing-and-note-notes.md#step-7-the-board-view-is-read-only](routing-and-note-notes.md#step-7-the-board-view-is-read-only)).

**7c — The routing table.** One row per kept item across every video: video (title) · author
· `call_ts_utc` (`call_ts_source`) · `ts` · `content_type` · `retrospective` ·
`is_intro_recap` · `verdict` · proposed routing · `vision_confidence`.

Both flags must be visible in this table (step 3). `retrospective` is enforced in code for a
`setup`, so showing it lets the approver see why an item is about to be dropped rather than
wondering where it went; `is_intro_recap` on a `claim`/`mechanic` is enforced by nobody —
there the human reading this table is the only mechanism. Below the table, per video: the
dropped candidates with their reasons, the `chart_present` flag, and `backlog` when `true`.
List any shape-1 / shape-2 videos separately with their skip reason. Write nothing yet.

## Step 9: note contents

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
