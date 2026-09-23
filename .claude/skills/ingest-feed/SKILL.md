---
name: ingest-feed
description: Poll the configured YouTube channel follow list for new uploads (tools/yt_feed.py — read-only Data API polling + explicit-outcome ledger) and feed the picked videos into the existing /ingest-video batch flow, marking consumption ONLY after the review gate routes the batch. Also drives deep back-catalogue ingestion per channel via the backfill subcommand. Invoke when the user says "/ingest-feed", "what's new on youtube", "poll the channels", "backfill <channel>", or "ingest the old videos from <channel>".
---

# Ingest feed (YouTube channel auto-feed)

The wifey-side reference lives in `.claude/context/tools.md`. Companion of `/ingest-video` — this
skill only automates *discovery*; every research-sink write happens inside the `/ingest-video` flow
behind its single approval gate.

This repo is equities; the sibling repo is `~/repo/buibui-moon-trader-bot/` (crypto). If porting a
change from this file back into the parent, invert the repo references again.

## Flow

### 1. Poll (or backfill)

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py poll --json [--since 2026-08-01]
```

`--since` on `poll` only narrows the per-channel floor — an earlier value is ignored, by design. The
floor is the watermark of what the operator has already seen, so honouring an earlier `--since` would
resurface declined videos. Use it to trim a catch-up poll after a quiet week, never to reach backwards;
reaching below the floor is `backfill`'s job, which ignores it deliberately.

For a back-catalogue request ("backfill 峰哥", "ingest the old videos"), find the channel's `UC…` id in
`config/youtube_channels.toml` and run instead:

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py backfill <UC…> --json \
  [--since 2026-01-01] [--max-videos 200]
```

Save the JSON output to a scratchpad file — step 4 needs it for `--candidates-json`. Exit 1 means at
least one channel errored; report the errors and continue with the candidates that did resolve. Exit 2
means `YOUTUBE_API_KEY` is missing from `.env`.

### 2. Present the candidate table

One row per candidate: channel · title · duration · age · `est_tokens` (a ±30% ranking-grade estimate —
use it to size the tranche, never to order it). Below the table: each channel's exclusion summary
(`below_floor` / `ledgered` / `title_filtered` / `too_short` / `live_or_upcoming` / `unavailable`) and
any errors — never hide drops.

Rank by likely stream, not by `est_tokens`: order B (mechanics) > A (hypotheses) > C (daily setups),
recency as the tiebreak. `est_tokens` is a cost proxy only — ranking on it puts the cheapest rows first
regardless of whether that stream ever converts. Stream is a prior for ordering, never a filter; the
exclusion rules above are the only thing licensed to remove a row from the table.

B ranks first because it is the only stream not yet measured dead, not because it has converted —
Streams A and C have each produced no actionable results so far. State the ranking that way (a weaker
stream not yet ruled out, not a proven winner), not as the stronger claim.

Name the Stream-C ratio in the report rather than proposing a top-N list — a poll dominated by
daily-setup uploads is one small opportunity wearing a big number. The stream ranked first is also the
one `poll` is worst at finding: Stream B material sits mostly in curated playlists, which an uploads
feed cannot see, and `yt_feed.py` ships only `poll` / `backfill` / `mark` / `resolve` / `hint` — no
playlist subcommand. So a B-first ranking is only as good as the B rows the poll happens to surface;
say which constraint you're under.

Zero candidates → report that and stop.

**Re-presented-candidate guard:** if a candidate's per-video note already exists under
`docs/plans/video-notes/` (match on the video id in frontmatter), flag the row — "note exists — was
this already routed?" — a prior run may have died between routing and `mark`. Confirm with the
operator before re-ingesting it.

**Sibling-repo guard (cross-repo dedup).** Also check the crypto parent's notes — the two repos follow
overlapping channels and `route_dedup.py`'s ledger is per-repo, so nothing else catches this. Use the
same grep `/ingest-video` step 2b runs, and keep the two in sync:

```bash
grep -rl -E 'video_id: *"?(<id1>|<id2>|…)"?' \
  ~/repo/buibui-moon-trader-bot/docs/plans/video-notes/ \
  docs/plans/video-notes/ 2>/dev/null
```

Check both directories deliberately — this subsumes the re-presented-candidate guard above and also
covers the case that guard misses, since a hit in this repo's dir is a same-repo re-ingest and
`.cache/video/<id>/` only spares the re-download, never the re-ingest.

Grep the frontmatter, not the filename, in both repos. Both repos name notes
`<date>-<author-slug>-<video_id>.md`, but each also holds legacy notes with title-based filenames, so
filenames are not reliably comparable across the two repos or even within one. `video_id:` in
frontmatter is the one key present in every note on both sides — match on that, never on filenames.

A hit is not automatically a skip — apply the subject rule: equities / macro / gold / oil / DXY /
bonds → here, crypto → the parent, and a video covering both legitimately yields rows in both repos
(two different calls, not a duplicate). Route by subject, never by repo priority: the parent's scorer
assumes 24/7 perp bars, so a macro call scored there resolves against the wrong bars, and USO-vs-WTI
fails quietly. The mirror applies here — this repo's bars are RTH equities (`4h` is 2 bars/day, not
6), so a crypto call scored here has the same defect in the opposite direction. Videos that are wholly
the sibling's subject should be `mark --skipped` here, not deferred, so they stop re-presenting
forever.

For a large backfill, recommend a tranche sized to the remaining session quota rather than ingesting
the whole list — the ledger carries the progress across days.

**Propose an even tranche — 4 or 6, never 5.** `/ingest-video` hand-pipelines its two passes in pairs
against a hard 2-subagent cap, so an odd batch strands the last video running alone against an idle
slot, and it pays that penalty twice (once in pass 1, once in pass 2). A batch of 5 costs 3 rounds per
pass with the last round half-empty both times, where 4 costs 2 full rounds and 6 costs 3. If the
operator picks an odd count anyway that is their call — just never propose one.

### 3. Operator picks

Ask which candidates to ingest. Three outcomes per candidate, and only the first two are ever marked:

- **ingest** — goes into the batch (→ `mark --ingested` in step 5)
- **skip** — operator explicitly declines, permanently (→ `mark --skipped`)
- **defer** (the default for anything not named) — no mark; it simply reappears next poll/backfill

When a whole channel keeps re-presenting a backlog the operator is not ready for, neither `skip` nor
`defer` is the right tool — `skip` burns the watermark permanently and `defer` re-asks forever, since
an unready backlog would otherwise reappear on every poll. Set `paused = true` on that channel in
`config/youtube_channels.toml` instead. It suppresses `poll` only: `backfill` still reaches the
channel, which matters because `backfill` is the diagnostic that tells a genuinely quiet channel from
a broken one, and a hand-pasted URL still resolves the row so `item_cap` / `intro_recap_s` keep
applying. Paused channels are printed on every poll rather than silently omitted, for the same
reason — an invisible pause is indistinguishable from a broken feed. Unpause by deleting the line;
nothing was lost.

### 4. Run the ingest batch

Execute the `/ingest-video` flow (`.claude/skills/ingest-video/SKILL.md`), steps 1–9, over the picked
URLs. Follow that skill by reference — do not restate or fork it here. Its digest + single approval
remain the only gate before any research-sink write.

### 5. Mark — immediately after routing completes

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py mark \
  --ingested <picked ids…> --skipped <explicitly skipped ids…> \
  # a leading-dash id (e.g. -mx3UwwJ5P4) is handled directly
  --candidates-json <scratchpad file from step 1>
```

`--ingested` covers only candidates whose step-4 routing actually completed — if `/ingest-video`
dropped or failed on a picked video, omit its id here; it gets no mark and simply re-presents next
poll/backfill, same as a deferred candidate. Do this in the same turn as routing: the gap between
routing and mark is the one failure window (see Guardrails).

`--candidates-json` derives the `--channel-seen` pairs automatically from the poll payload's
`channels` array. Pass `--channel-seen` by hand only to persist a channel the poll did not report — it
wins over a derived pair when both are present. Either way the write is a `setdefault`, so a recorded
floor is static and can never move once set — this is what keeps a watermark from silently advancing
on its own.

### 6. Report

Per candidate: ingested (→ which streams) / skipped / deferred. Include the mark summary line
("marked N video(s)") as evidence the ledger write happened.

Carry `/ingest-video`'s 7a summaries and 7b board view into this report rather than reducing it to
routed/dropped counts — the board view is the only record of what the batch said collectively: who is
positioned which way, which levels multiple pundits name independently, and where they read the same
structure to opposite conclusions.

Also report what the round cost against the binding constraint: videos × approximate tokens, and
whether any routed item opens a genuinely new data axis rather than another setup or mechanic.
Filed-to-tested conversion is what decides whether the pipeline is worth its cost, so distinguish a
round that files a fifth untested axis from one that files the first testable one.

## Guardrails

- `poll`/`backfill` write nothing; only `mark` writes, and only after the review gate has routed the
  batch. Consumption stamped at fetch time would let an aborted run permanently eat videos. Never mark
  early, and never hand-edit `docs/plans/yt-feed-state.json`.
- If a run dies between routing and `mark`, the routed videos re-present next poll — that is deliberate
  (visible duplication beats invisible loss). The step-2 note-exists guard is how the duplicate gets
  caught.
- This skill never writes to `thesis-inbox.md` / `mechanics-backlog.md` / `pundit-calls.jsonl` itself —
  those writes live inside `/ingest-video`'s flow only.
- A follow-list change (`config/youtube_channels.toml`) is a deliberate operator edit; `resolve` prints
  a block to paste, it never writes config.
- When adding a channel that opens every upload by recapping prior positions, fill in `handle` and
  `intro_recap_s` in that block. Without them `/ingest-video` routes those opening past-calls as if
  they were today's, filing stale entries and a past trade's exit management as live. Both keys are
  opt-in — a channel without them is unaffected, and `handle` is required for `intro_recap_s` to have
  any effect at all. Fill it in even though a video's own chapters override it when present: chapters
  are absent on roughly half of videos, so the constant is a fallback rather than dead weight, and
  where both exist the chapter wins, so a roughly-right constant costs nothing.
- Quota: poll ≈ 2–3 units/channel/day against 10,000/day — never call `search.list` (100 units); the
  tool doesn't, don't add it.
