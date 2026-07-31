# X-post ingest — iteration 1 (design)

> **Ported from parent** `s10023/buibui-moon-trader-bot` PRs #466/#467
> (`6d81a43`/`dd3176f`) on 2026-07-31 as historical design context for `/ingest-x`.
> Crypto examples and parent-only file references read in parent context; wifey's
> equity re-flavor lives in the skill's inline rubric.

**Date:** 2026-06-30
**Status:** approved (direction), ready for plan
**Scope of this spec:** Iteration 1 — a single pasted X/Twitter post URL → text + still
images → vision extraction → de-biased classify → route into the existing three streams.
Video, threads, and bulk/timeline ingestion are explicit non-goals here (roadmap below).
**Priority:** DEFERRED / LOW — a background quality-of-life + knowledge-retention tool. Per
the SoT, idea capture is **not** the binding constraint (testing capacity is), so this is
NOT ahead of the second-strong-edge brainstorm or XS-solo deploy-hardening. Built now at
explicit operator request, as a cheap iterate-able first slice.
**Parent spec:** `docs/superpowers/specs/2026-06-20-research-ingestion-pipeline-design.md`
(the research-ingestion knowledge pipeline). This spec realizes that pipeline's **manual-paste
adapter** as a concrete, automated **X-URL syndication adapter**, and reuses its verdict
taxonomy + three-stream routing unchanged.

## Context

The operator reads a lot of crypto trading content on X and wants a single post to flow into
the buibui system instead of evaporating: *paste a post URL, call a skill, and have everything
downstream automated* — read the text, read the chart, decide whether it implies a testable
hypothesis / a daily setup worth scoring / a system mechanic / nothing new.

The parent pipeline spec already designed the hard conceptual parts and they are **inherited
unchanged**:

- The **de-biased verdict taxonomy** (NOVEL / ALREADY-TESTED / FROZEN-CATEGORY /
  NOT-FALSIFIABLE) — the load-bearing contract that protects the testing bottleneck.
- The **three streams** (A hypotheses → `thesis-inbox.md`; B mechanics → `mechanics-backlog.md`;
  C daily setups → `pundit-calls.jsonl`).
- The **source-adapter shape** `{source, author, timestamp, text, url}`, the subagent-per-item
  extraction (keeps media/transcript bytes out of the main context), the gitignored
  cache + seen-watermark, and the human review gate ("reviewed commit, never auto-write").

What this spec adds is the X-specific front end and the chart-vision path.

### The fetch problem, solved by evidence (2026-06-30)

An X URL is SPA/login-gated, so markitdown cannot fetch it from the URL alone. The parent spec
assumed "manual-paste" meant pasting the *text*. We instead validated a **no-auth, no-scraping**
path empirically (this session):

- X's public **syndication endpoint** (`cdn.syndication.twimg.com/tweet-result?id=<id>&token=<t>`,
  the data path that powers embedded tweets; what Vercel's `react-tweet` uses) returns the post's
  **clean text + media URLs as JSON** for public posts, with no login and no browser automation.
  This is X's own embed path — not scraping, not a third-party mirror (Nitter), not the $200/mo API.
- Verified live on the operator's real post `x.com/cryptic_heych/status/2071837700500644228`:
  HTTP 200 → text `"Today's statistical analysis $BTC …"`, author, UTC timestamp, and a direct
  full-res chart image (`?name=orig` = 259 KB) that downloads cleanly.

Consequence: **markitdown is not in the X path.** The text arrives clean from JSON; the chart goes
to vision (OCR mangles candlesticks/axes). markitdown remains the parent pipeline's normalizer for
YouTube transcripts / PDFs / articles.

### Success metric

One pasted X post URL becomes, with a single skill call and one human approval, **either** a
correctly-routed entry in the right stream **or** an explicit, surfaced drop with its reason —
with the chart understood (not OCR'd), and the image bytes never entering the main conversation.
Precision over throughput: a frozen/already-tested claim must be dropped with its rationale, never
queued.

## Decisions (locked in brainstorm)

| Decision | Choice | Why |
| --- | --- | --- |
| Fetch mechanism | **Syndication API** (`tweet-result`) with manual-paste fallback | Empirically validated; no login, no scraping, no anti-bot fight; free |
| Fallback | On tombstone / protected / age-gated / deleted → skill prompts the operator to paste text + drop a screenshot | Graceful degradation; covers the ~minority the endpoint can't serve |
| Iteration-1 media | **Text + still images.** Video detected → reported + skipped | Covers the dominant trader-post shape (text + chart); video is a heavier subsystem (yt-dlp/Whisper/frame-sampling) deferred to iter-2 |
| Text extraction | Clean text straight from syndication JSON; **no markitdown** | The text is already clean; markitdown adds nothing on this path |
| Chart understanding | **Claude vision** in a subagent, not OCR | OCR is lossy on charts; subagent isolation keeps image bytes out of main context (the token-efficiency goal) |
| Classification | A content-type gate (setup / mechanic / claim) → the parent's **4-bucket verdict taxonomy** on the claim path (taxonomy unchanged) | Protects the testing bottleneck; "missing" ≠ "worth building" |
| Routing | The parent spec's **three streams**, unchanged | A→thesis-inbox, B→mechanics-backlog, C→pundit-calls |
| "Gap vs my setup" | A **light one-line `gap_note`** in iter-1 (implied primitive + has/tested/froze?) | Answers the operator's question + drives the verdict; the heavy losing-ledger mining is the parent spec's Phase-2 journal-feedback loop |
| Human gate | A **review digest** is presented; nothing is written to a stream until approved | The project's "reviewed commit, never auto-write" rule |
| Orchestration surface | A **skill** (`/ingest-x`) + a `tools/` fetcher | Matches the existing skill + tools convention (mirrors `/journal-trade` + `tools/journal_fetch.py`) |

## Architecture

```text
/ingest-x <url>
  │
  ├─(1) FETCH  (tools/x_fetch)
  │      url → tweet id → syndication tweet-result JSON
  │      → { source:"twitter", author, author_name, url, post_ts_utc, text,
  │          photo_urls[], video_present, is_thread, is_quote }
  │      → download photos full-res (?name=orig) to .cache/x-media/<id>/
  │      → tombstone/protected/error ⇒ Unavailable(reason)  ── fallback ──┐
  │                                                                        │
  ├─(2) EXTRACT  (subagent per post)                                       │
  │      inputs: clean text + chart image path(s) + extraction schema      │
  │              + classification context (frozen-detector list,           │
  │                thesis-inbox, Closed/Parked)                            │
  │      reads chart via VISION; returns a SMALL JSON only:                │
  │        { symbol, direction, entry, stop, target, horizon, setup_type,  │
  │          raw_quote, chart_read, content_type, verdict, stream,         │
  │          gap_note }                                                    │
  │      (image bytes stay in the subagent; never enter main context)      │
  │                                                                        │
  ├─(3) REVIEW DIGEST → operator   (extraction + verdict + routing + gap)  │
  │      ◄──────────────── fallback: "paste post text / drop screenshot" ──┘
  │                         then resume at (2)
  │
  └─(4) on approval → append to the routed stream file (never before)
```

### (1) The fetcher — `tools/x_fetch`

Read-only, no DB writes; mirrors `tools/journal_fetch.py`'s shape. Responsibilities:

- Parse the tweet ID from any `x.com|twitter.com/<user>/status/<id>` URL (strip `?s=` etc.).
- Derive the syndication `token` and GET `tweet-result`; parse JSON into the adapter struct above.
- Detect `video_present` from `mediaDetails[].type ∈ {video, animated_gif}` and **flag, not fetch**.
- Download each photo at `?name=orig` into a gitignored per-id media dir; return local paths.
- On `__typename == "TweetTombstone"`, non-200, or empty payload → return `Unavailable(reason)`.

**Implementation note (resolved 2026-06-30):** the `token` query param must be *present and
non-empty* but its value is **not validated** by the endpoint — `token=a` returns a byte-identical
payload to the algorithmically-derived token (verified on both test tweets; only an *absent* token
fails, returning `{}`). So no JS-float `.toString(36)` port is needed: the fetcher is **pure
Python** passing a fixed dummy token.

### (2) The extract subagent

One subagent per post (the parent spec's pattern). It receives the clean text and the **file
path(s)** of the downloaded chart(s) plus the schema and the classification context, reads the
chart with vision, and returns a small structured JSON. The image bytes and any chain-of-thought
stay inside the subagent — only the JSON returns to the main conversation. Schema:

```text
source        twitter
author        @handle           author_name  display name
url           post url          post_ts_utc  UTC publish time
text          clean post text   video_present bool (iter-1: report-only)
media[]       {type, path, chart_read}     # chart_read = vision summary of each image
# --- extraction ---
symbol        BTCUSDT | null    direction  long|short|neutral|null
entry / stop / target           horizon    intraday|swing|unspecified
setup_type    free text         raw_quote  the sentence(s) the call/claim came from
chart_read    what the chart shows (levels, structure, annotations)
# --- classification ---
content_type  claim | setup | mechanic        # what KIND of post this is (the routing gate)
verdict       NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE
              # the parent's 4-bucket taxonomy; applies only when content_type = claim
stream        A | B | C | drop
gap_note      one line: implied primitive + does the system already have/test/freeze it?
```

### (3)–(4) Classify, route, and the human gate

Routing is a **content-type gate**, then the parent's 4-bucket taxonomy on the claim path:

| content_type | verdict | Stream | Destination |
| --- | --- | --- | --- |
| **setup** (symbol + direction + levels), e.g. cryptic_heych's "$BTC analysis" | n/a | C | a record in `docs/plans/pundit-calls.jsonl` (parent's pundit-call schema) |
| **mechanic** (exit / risk / data / microstructure) | n/a | B | `docs/plans/mechanics-backlog.md` |
| **claim** (generalizable market behaviour) | NOVEL | A | draft `H`-row appended to `docs/plans/thesis-inbox.md` |
| **claim** | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | drop | **not written**; surfaced in the digest with its reason ("seen — verdict X") |

The **review digest** is printed first; only on operator approval is the routed file appended.
Nothing auto-commits to git. The `gap_note` is shown for every post regardless of verdict.

## File layout / outputs (gitignored except the skill, tool, and this spec)

```text
.cache/x-posts/<id>.json        # fetched adapter struct + extraction (cache + re-run guard)
.cache/x-media/<id>/*.jpg       # downloaded full-res charts
docs/plans/thesis-inbox.md      # Stream A (exists)
docs/plans/mechanics-backlog.md # Stream B (new, shared with parent pipeline)
docs/plans/pundit-calls.jsonl   # Stream C (new, shared with parent pipeline)
.claude/skills/ingest-x/SKILL.md  # orchestration skill (committed)
tools/x_fetch.{py|mjs}          # syndication fetcher (committed; language per the plan's spike)
```

## Error handling / fallback

- **Unavailable post** (tombstone/protected/deleted/age-gated): skill states the reason and asks
  the operator to paste the text and drop a screenshot; extraction resumes at step (2) with the
  pasted inputs — the rest of the flow is identical.
- **Video present:** reported in the digest as "video detected — skipped (iter-2)"; text + any
  still images are still processed.
- **Thread / quote-tweet:** iter-1 processes only the single fetched post; the digest notes a
  thread/quote was detected so the operator can paste siblings if wanted. (Thread-walking = iter-2.)
- **Syndication endpoint breaks entirely** (X tightens it): the manual-paste fallback is the
  permanent floor, so the skill never hard-fails.

## Testing

- `tools/x_fetch` unit tests with a **recorded syndication JSON fixture** (no live network in
  tests — mirrors the repo rule): ID parsing, token derivation parity vs the Node reference, the
  adapter-struct mapping, tombstone → Unavailable, video detection, photo-URL `?name=orig` rewrite.
- The extraction schema is validated structurally (required keys, enum domains) on a fixture.
- The skill's routing table is exercised with canned extraction JSONs (one per verdict) asserting
  the correct destination file (or drop) — no network, no real X calls.

## Non-goals (drift guards)

- **No new TA detector** — output is always a hypothesis/setup/mechanic to test, never an
  `[add detector]` task (the frozen-detector list stands).
- **No video, no Whisper, no frame-sampling** in iter-1.
- **No thread/timeline/bulk ingestion**, no channel enumeration (that is the parent spec's
  YouTube path; X bulk is the parent's deferred Twitter-automated adapter).
- **No browser automation / logged-in scraping** — the syndication + paste paths are the only
  two, by design.
- **No auto-write to any stream or git** without the review-gate approval.
- **No resolution/scoring** of pundit calls — Stream C is a structured *log* in iter-1; resolution
  is the parent spec's Phase 2.

## Open questions (for the plan)

- ~~Fetcher language~~ **RESOLVED:** pure Python — a fixed dummy `token` suffices (see § The
  fetcher), no JS-float port or Node helper needed.
- Exact `pundit-calls.jsonl` line schema reuse: confirm it matches the parent spec's pundit-call
  schema field-for-field so Phase-2 resolution can consume both YouTube and X uniformly.
- Skill name: `/ingest-x` (working) vs folding into a future unified `/ingest <url>` front door.

## Relationship to the parent spec

This spec is additive and conformant. The parent pipeline spec
(`2026-06-20-research-ingestion-pipeline-design.md`) gets a 2-line note that its **manual-paste
adapter is realized here as the X-URL syndication adapter**, and that the **fetch mechanism for X
is the syndication API** (decided + validated 2026-06-30). No taxonomy, stream, or schema in the
parent changes.
