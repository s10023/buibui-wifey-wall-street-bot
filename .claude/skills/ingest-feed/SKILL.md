---
name: ingest-feed
description: Poll the configured YouTube channel follow list for new uploads (tools/yt_feed.py — read-only Data API polling + explicit-outcome ledger) and feed the picked videos into the existing /ingest-video batch flow, marking consumption ONLY after the review gate routes the batch. Also drives deep back-catalogue ingestion per channel via the backfill subcommand. Invoke when the user says "/ingest-feed", "what's new on youtube", "poll the channels", "backfill <channel>", or "ingest the old videos from <channel>".
---

# Ingest feed (YouTube channel auto-feed)

Ported from parent #515 (plus #516/#529/#535/#558/#582/#585 — `yt_feed.py` was taken at
parent HEAD, not at #515, so all six land together). The upstream design spec stayed in
the parent; `.claude/context/tools.md` carries the wifey-side reference. Companion of
`/ingest-video` — this skill only automates *discovery*; every research-sink write still
happens inside the `/ingest-video` flow behind its single approval gate.

**⚠ THIS FILE WAS WRITTEN IN THE CRYPTO PARENT AND ITS CROSS-REPO DIRECTION IS FLIPPED
HERE.** Every "here"/"our"/"the sibling" below has been re-pointed for wifey: **this repo
is equities**, the sibling is `~/repo/buibui-moon-trader-bot/` (crypto). If you are
back-porting a change into the parent, invert it again.

## Flow

### 1. Poll (or backfill)

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py poll --json [--since 2026-08-01]
```

`--since` on `poll` **only narrows** the per-channel floor — an earlier value is
ignored, by design. The floor is the watermark of what the operator has already
seen, so honouring an earlier `--since` would resurface declined videos. Use it to
trim a catch-up poll after a quiet week, never to reach backwards; reaching below
the floor is `backfill`'s job, which ignores it deliberately.

For a back-catalogue request ("backfill 峰哥", "ingest the old videos"), find the
channel's `UC…` id in `config/youtube_channels.toml` and run instead:

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py backfill <UC…> --json \
  [--since 2026-01-01] [--max-videos 200]
```

Save the JSON output to a scratchpad file — step 4 needs it for `--candidates-json`.
Exit 1 means at least one channel errored; report the errors and continue with the
candidates that did resolve. Exit 2 = `YOUTUBE_API_KEY` missing from `.env`.

### 2. Present the candidate table

One row per candidate: channel · title · duration · age · `est_tokens` (a ±30%
ranking-grade estimate — rank by it, don't budget by it). Below the table: each
channel's exclusion summary (`below_floor` / `ledgered` / `title_filtered` /
`too_short` / `live_or_upcoming` / `unavailable`) and any errors — never hide drops.

Zero candidates → report that and stop.

**Re-presented-candidate guard:** if a candidate's per-video note already exists under
`docs/plans/video-notes/` (match on the video id in frontmatter), flag the row —
"note exists — was this already routed?" — a prior run may have died between routing
and `mark`. Confirm with the operator before re-ingesting it.

**Sibling-repo guard (cross-repo dedup).** Also check the **crypto parent's** notes — the
two repos follow overlapping channels and `route_dedup.py`'s ledger is per-repo, so
nothing else catches this. Same grep `/ingest-video` step 2b runs; keep the two in sync:

```bash
grep -rl -E 'video_id: *"?(<id1>|<id2>|…)"?' \
  ~/repo/buibui-moon-trader-bot/docs/plans/video-notes/ \
  docs/plans/video-notes/ 2>/dev/null
```

Both directories, deliberately — this subsumes the re-presented-candidate guard above and
covers the case that guard misses, since a hit in **this repo's** dir is a same-repo
re-ingest and `.cache/video/<id>/` only spares the re-download, never the re-ingest.

**Grep the frontmatter, not the filename — in both repos.** This repo's `/ingest-video`
names notes `<date>-<author-slug>-<title-slug>.md` (it has not received the parent's #522
`video_id`-slug fix), and the parent's own pre-#522 notes carry title slugs too, so
filenames are not comparable across the two repos *nor even within this one*. `video_id:`
in frontmatter is the one key present in every note on both sides. (Porting #522's slug
rule **here** is a `/sync-parent` item; until then, never match on filenames.)

A hit is **not** automatically a skip — apply the subject rule: **equities / macro / gold
/ oil / DXY / bonds → HERE, crypto → the parent**, and a video covering both legitimately
yields rows in both repos (two different calls, not a duplicate). Route by subject, never
by repo priority: the parent's scorer assumes 24/7 perp bars, so a macro call scored there
resolves against the wrong bars, and USO-vs-WTI fails *quietly*. **The mirror of that
applies here** — this repo's bars are RTH equities (`4h` is **2 bars/day**, not 6), so a
crypto call scored here has the same defect in the opposite direction. Videos that are
wholly the sibling's subject should be `mark --skipped` here, not deferred, so they stop
re-presenting forever. The parent's round 6 (2026-08-02) hit exactly this: 4 of 9 Cowen
candidates were already in wifey, and they were precisely the 4 macro ones.

For a large backfill, recommend a tranche sized to the remaining session quota rather
than ingesting the whole list — the ledger carries the progress across days.

### 3. Operator picks

Ask which candidates to ingest. Three outcomes per candidate, and only the first two
are ever marked:

- **ingest** — goes into the batch (→ `mark --ingested` in step 5)
- **skip** — operator explicitly declines, permanently (→ `mark --skipped`)
- **defer** (the default for anything not named) — NO mark; it simply reappears next
  poll/backfill

**When a whole channel keeps re-presenting a backlog the operator is not ready for,
neither `skip` nor `defer` is the right tool** — `skip` burns the watermark permanently
and `defer` re-asks forever (a 13-video backlog re-asked on every single poll). Set
`paused = true` on that channel in `config/youtube_channels.toml` instead. It suppresses
`poll` only: `backfill` still reaches the channel, which matters because `backfill` is
the diagnostic that tells a genuinely quiet channel from a broken one, and a hand-pasted
URL still resolves the row so `item_cap` / `intro_recap_s` keep applying. Paused
channels are **printed on every poll** rather than silently omitted, for the same
reason — an invisible pause is indistinguishable from a broken feed. Unpause by
deleting the line; nothing was lost.

### 4. Run the ingest batch

Execute the `/ingest-video` flow (`.claude/skills/ingest-video/SKILL.md`), steps 1–9,
over the picked URLs. Follow that skill by reference — do not restate or fork it here.
Its digest + single approval remain the only gate before any research-sink write.

### 5. Mark — immediately after routing completes

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py mark \
  --ingested <picked ids…> --skipped <explicitly skipped ids…> \
  --candidates-json <scratchpad file from step 1>
```

`--ingested` covers only candidates whose step-4 routing actually completed — if
`/ingest-video` dropped or failed on a picked video, omit its id here; it gets NO mark
and simply re-presents next poll/backfill, same as a deferred candidate. Do this in the
same turn as routing: the gap between routing and mark is the one failure window (see
Guardrails).

**You no longer pass `--channel-seen` by hand.** The poll payload's `channels` array
already carries both fields it wanted, so `--candidates-json` now derives the pairs —
previously this flag took ONE pair per use and had to be repeated once per followed
channel (nine times today), with the values copied out of the very file already being
passed on the line above. The flag still exists and still wins over a derived pair, for
the rare case of persisting a channel the poll did not report. Either way the write is a
`setdefault`, so a recorded floor is static and **can never move** — the derived pairs go
through the same path, which is what keeps the watermark-advances-on-its-own defect class
out of this command.

### 6. Report

Per candidate: ingested (→ which streams) / skipped / deferred. Include the mark
summary line ("marked N video(s)") as evidence the ledger write happened.

**Carry `/ingest-video`'s 7a summaries and 7b board view into this report — do not drop
them on the way out.** They are the half of the batch an operator actually reads to learn
what was said, and a run that reports only "N routed / M dropped" has thrown that away
while looking complete. The board view in particular is the only record of what the batch
said *collectively*: who is positioned which way, which levels several pundits name
independently, and where they read the same structure to opposite conclusions.

**Also say what the round cost and what it produced against the binding constraint.** One
line: videos × approximate tokens, and whether any routed item is a genuinely new *data*
axis rather than another setup or mechanic. The pipelines are the system's only generative
input, but **filed-to-tested conversion is the number that decides whether they are worth
their cost** — a round that files a fifth untested axis is not the same result as a round
that files the first testable one, and the report should not read as though it were.

## Guardrails

- **The historical defect this design exists to avoid — THIS repo's PR #68**
  (watermark-on-send; the parent's note calls it "wifey #68" from its side):
  consumption stamped at fetch lets an aborted
  run permanently eat videos. Therefore `poll`/`backfill` write NOTHING; only `mark`
  writes, and only after the review gate routed the batch. Never "optimize" by marking
  early, and never edit `docs/plans/yt-feed-state.json` by hand.
- If a run dies between routing and `mark`, the routed videos re-present next poll —
  that is deliberate (visible duplication beats invisible loss). The step-2 note-exists
  guard is how the duplicate gets caught.
- This skill never writes to `thesis-inbox.md` / `mechanics-backlog.md` /
  `pundit-calls.jsonl` itself — those writes live inside `/ingest-video`'s flow only.
- A follow-list change (`config/youtube_channels.toml`) is a deliberate operator edit;
  `resolve` prints a block to paste, it never writes config.
- When adding a channel that opens every upload by recapping prior positions, fill in
  `handle` and `intro_recap_s` in that block. Without them `/ingest-video` routes those
  opening past-calls as if they were today's — on 2026-08-03 that put a never-filled
  entry and a past trade's exit management into the sinks. Both keys are opt-in; a
  channel without them behaves exactly as before, and `handle` is required for
  `intro_recap_s` to have any effect at all.
- Quota: poll ≈ 2–3 units/channel/day against 10,000/day — never call `search.list`
  (100 units); the tool doesn't, don't add it.
