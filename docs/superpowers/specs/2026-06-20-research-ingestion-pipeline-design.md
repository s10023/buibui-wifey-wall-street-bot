# Research-ingestion knowledge pipeline (design)

> **Ported from parent** `s10023/buibui-moon-trader-bot` PR #449 (`a6625b7`) on
> 2026-07-31 as historical design context for the `/ingest-x` + `/ingest-video`
> skills. Crypto examples and parent-only file references read in parent context;
> wifey's equity re-flavor lives in the skills' inline rubrics.

**Date:** 2026-06-20
**Status:** approved (direction), ready for plan
**Scope of this spec:** Phase 1 — the lean capture+route MVP. Phases 2–3 and
additional source adapters are sketched as a roadmap, not specified here.
**Priority:** DEFERRED / LOW — a background tool, explicitly NOT ahead of the
second-strong-edge brainstorm or XS-solo deploy-hardening (see §10).

## Context

The operator consumes a lot of crypto trading content (YouTube daily; Twitter
threads; the occasional setup video) and wants that knowledge to flow into the
buibui system instead of evaporating. The naive read of this request — "summarize
every video into a backlog of features to build" — is **actively dangerous on this
codebase** and must be designed against:

- The detector list is **FROZEN on purpose** (0/123 DSR; the 2026-06-04
  conditional-edge test returned **NO**). Most things buibui "is missing" are
  missing *because they were tested and killed*, not because nobody thought of them.
- The SoT is explicit that **the bottleneck is testing capacity, not idea
  capture** — it is why the $200/mo X API was rejected in favour of the manual
  "Thesis Inbox" workflow. A content firehose makes the *cheap* side (capture)
  cheaper while the *expensive* side (de-biased validation through the guards) is
  unchanged.

Therefore the pipeline's job is **precision, not recall**: turn hours of content
into a *small* number of genuinely-novel, de-duplicated, falsifiable claims, each
pre-classified against what the system has already tested / frozen / killed. The
output is always a **hypothesis to test**, never an implementation task. "Missing"
≠ "worth building."

This reframe is what makes "test first, then grow" true rather than a slogan, and
it is the single design element that separates this pipeline from a generic
"YouTube summarizer."

### Success metric

A run over N videos/posts yields a **review-ready digest** in which (a) every
already-killed / frozen-category / non-falsifiable claim has been auto-dropped or
tagged-and-set-aside, and (b) the only items reaching an inbox are **novel,
falsifiable** hypotheses or **concrete system mechanics** — each carrying a source
citation (channel · video · timestamp). Measured cheaply by: on a hand-labelled
sample of real transcripts, the digest surfaces ≥ the obvious novel items and
floods the inbox with ≤ a small, reviewable number of false-novel items.

## Decisions (locked in brainstorm)

| Decision | Choice | Why |
| --- | --- | --- |
| Optimisation target | Precision into a de-biased testing queue (not throughput into a task backlog) | The frozen-detector history; testing capacity is the binding constraint |
| Output unit | A **hypothesis to test**, never an `[P1] add detector` task | "Missing" ≠ "worth building"; auto-task framing re-litigates closed evidence |
| Source handling | A **source-adapter layer** emitting `{source, author, timestamp, text, url}`; everything downstream is source-agnostic | Makes YouTube / Twitter / Substack / podcast interchangeable; Twitter is "just another source" the moment text exists |
| Phase-1 adapters | **YouTube (`yt-dlp`)** + **manual-paste** (covers Twitter / anything) | yt-dlp is free, no API key, robust, caches to disk; paste covers the sources without a free API |
| Transcript fetch | `yt-dlp --flat-playlist` to enumerate, `--write-auto-subs --skip-download` per video | Free, no metered cost (free-first policy); auto-subs are good enough for Phase 1 |
| Extraction execution | **Subagent per item** — gets the transcript *file path* + schema, returns small JSON | Keeps 5–20k-token transcripts **out of the main context**; parallelizable across a backlog |
| Classification reference | The **living SoT** (`thesis-inbox.md`, Closed/Parked, frozen-detector list, recalibrate verdicts) | A hand-maintained `buibui_features` YAML would drift; the living docs are the real source of truth |
| Incrementality | A gitignored "seen items" watermark + transcript cache | Channel re-runs only process new uploads — mirrors the cooldown / sync watermarks |
| Human gate | Nothing auto-commits to an inbox; a review digest is presented first | The project's "reviewed commit, never auto-write config" rule |
| Orchestration surface | A **skill** (`/video-ingest`, working name) + `tools/` scripts | Matches the existing skill + tools + CLI convention; no external MCP, no API key in Phase 1 |
| Search (if wanted) | context-mode FTS5 (`ctx_index`) over the transcript cache | Already present, free, local — no Qdrant / vector DB (YAGNI) |

## The de-biased verdict taxonomy (the load-bearing contract)

The extractor↔router interface. Every extracted claim is classified into exactly
one bucket; only the first reaches an inbox:

```text
NOVEL              -> falsifiable, not already in H-inbox/Closed/Parked, not a frozen category
                     -> route to the appropriate stream (A or B)
ALREADY-TESTED     -> matches a Closed item; carry its verdict; DROP (with a one-line "seen, verdict X")
FROZEN-CATEGORY    -> maps to a frozen boolean-TA detector; DROP (with the freeze rationale)
NOT-FALSIFIABLE    -> vibes / unmeasurable / no avg_r split possible; DROP
```

`ALREADY-TESTED` and `FROZEN-CATEGORY` drops are *surfaced in the digest* (so the
operator sees "yes, the channel said X, we already killed it") but never enter an
inbox. This is the mechanism that protects the testing bottleneck.

## Architecture — four stages over a source-adapter layer

```text
                +-- youtube adapter (yt-dlp)      --+
source adapters |-- manual-paste adapter           |--> {source, author, ts, text, url}
                +-- (future: twitter, whisper, RSS)-+
                                  |
            (1) enumerate + fetch  v   -> transcript cache + seen-watermark (gitignored)
                                  |
            (2) extract            v   -> subagent per item, returns structured JSON
                                  |
            (3) classify           v   -> de-biased verdict taxonomy vs living SoT
                                  |
            (4) route              v
        +-------------------------+--------------------------+
        |                         |                          |
   Stream A                  Stream B                   Stream C
   novel hypotheses          system mechanics           daily setups
   -> thesis-inbox.md        -> mechanics-backlog.md     -> pundit ledger
   (dedup'd H-rows)          (L5-L8 stack ideas)         (Phase 1: structured log only)
```

### The three streams

- **Stream A — generalizable hypotheses.** Falsifiable claims about market
  behaviour. Dedup'd against the H-inbox / Closed / Parked, then appended (after
  review) as draft `H`-rows in `docs/plans/thesis-inbox.md` for the existing
  formalise→test workflow.
- **Stream B — system mechanics.** Not strategies: data sources, execution/exit
  heuristics, risk-management lessons, microstructure facts — things that feed the
  L5–L8 monetization stack. Routed to a new `docs/plans/mechanics-backlog.md`.
  (Phase-2 enrichment: the journal/ledger feedback loop, §8.)
- **Stream C — daily setups as scoreable forecasts.** A setup ("BTC long above
  65k, target 68k, invalidation 63k") is a forecast with a track record waiting to
  be measured. **Phase 1 logs each as a structured, resolvable record** in a pundit
  ledger; resolution + scoring is Phase 2. Phase 1's real job is to prove
  extraction can recover exact levels from messy auto-captions before the scoring
  loop is built on top.

### Pundit-call schema (Stream C, structured log)

Mirrors the journal frontmatter so it is machine-parseable and later resolvable by
the existing outcome machinery. Keyed by `(source, author)` so a Twitter handle and
a YouTube channel are both "pundits":

```text
source        # youtube | twitter | manual
author        # channel name / handle
url           # video / post
call_ts_utc   # when the call was made (publish time)
symbol        # BTCUSDT, ...
direction     # long | short
entry         # level or "market"
stop          # invalidation level
target        # tp level(s)
horizon       # intraday | swing | unspecified
confidence    # verbatim hedging language, if any
raw_quote     # the sentence(s) the call was parsed from (audit trail)
# --- filled in Phase 2 (resolution) ---
outcome_r     # net-of-cost R, resolved vs OHLCV
resolved_at_utc
```

Phase 1 writes these as JSONL (`docs/plans/pundit-calls.jsonl`); Phase 2 promotes
to a DuckDB `pundit_calls` table when resolution needs OHLCV joins.

## Context-efficiency

Raw transcripts (5–20k tokens each, dozens per backlog) must never enter the main
conversation. Stage (2) dispatches a **subagent per item** with the transcript file
path + the extraction schema + the current SoT reference; the subagent returns only
the small structured JSON. A backlog fans out across parallel subagents. The
transcript cache + context-mode FTS5 keep raw text on disk and searchable without
re-reading it into context.

## Outputs / file layout (all gitignored except the skill/tools/spec)

```text
config/video_channels.toml         # tracked channels + per-channel type tag (like coins.json)
.cache/transcripts/<id>.json       # transcript cache
.cache/ingest-seen.json            # processed-items watermark
docs/plans/thesis-inbox.md         # Stream A (exists)
docs/plans/mechanics-backlog.md    # Stream B (new)
docs/plans/pundit-calls.jsonl      # Stream C structured log (new)
.claude/skills/video-ingest/       # orchestration skill (committed)
tools/ingest_*.py                  # yt-dlp wrapper, (Phase 2) setup-resolver (committed)
```

## Roadmap (Phases 2–3 — sketched, not specified here)

- **Phase 2 — Resolve + score (the de-biased payoff).** Promote the pundit ledger
  to a DuckDB table; resolve each call's `outcome_r` net-of-cost against OHLCV by
  **reusing the existing outcome machinery** (`backfill_outcomes` pattern,
  net-`R` cost model); produce a per-`(source, author)` track record
  (Sharpe / avg_r / win-rate). Gated on Phase-1 extraction quality being good
  enough to recover exact levels.
- **Phase 3 — Mine winners.** Only channels/handles with a *proven* track record
  get their method mined into Stream-A hypotheses; everyone else is auto-dropped —
  never touching the testing queue. This is the new edge-discovery instrument the
  system does not currently have.
- **Twitter-automated adapter.** Deferred for the reasons the SoT already deferred
  it (X API $200/mo not justified; scraping ToS-fraught/brittle). The manual-paste
  adapter covers Twitter in the meantime, and short content is cheap to paste — so
  little is lost by deferring. A Playwright-logged-in-as-self adapter is a *possible*
  future opt-in, flagged but not recommended (brittle + grey-area).
- **Whisper audio fallback.** For the rare no-subtitle video (`yt-dlp` audio →
  Whisper). Crypto videos almost always have auto-subs, so Phase-2+ nicety only.

## The journal/ledger feedback loop (Phase 2, Stream-B enrichment)

The strongest idea from the ChatGPT cross-reference, made de-biased: fuse external
knowledge with the operator's *own empirical record* — *"which concepts the experts
stress are repeatedly absent from my **losing** trades?"* Two adjustments keep it
rigorous:

- Point it primarily at the **live outcome ledger** (`signal_alert_outcomes`,
  thousands of resolved rows), not only the ~6-entry journal — that is where the
  statistical power is.
- Its output is still a **falsifiable hypothesis** ("losing short reversals
  correlate with absent HTF-trend confirmation → test as an avg_r split via
  `audit_guard`"), routed through the same verdict taxonomy — never a task.

## Non-goals (drift guards)

- **No auto-generated implementation tasks / detector adds.** The output is
  hypotheses; building is a separate, gated decision.
- **No new boolean-TA detectors** from this pipeline (frozen-detector list stands).
- **No daily auto-Telegram of "recommended tasks"** (Phase-1) — that is the flood
  risk in concrete form; a digest is reviewed by a human first. A *digest*
  notification can be reconsidered once precision is proven.
- **No vector DB, no MCP server, no paid API** in Phase 1.
- **Never jumps the deploy-path queue** (§10).

## Priority / SoT entry (§10)

Recommended SoT placement: a **LOW-priority background item** under "Parked /
explicitly NOT now → revisit", with this note:

> **Research-ingestion pipeline (Phase 1 MVP)** — YouTube/`yt-dlp` + manual-paste
> adapters → de-biased extract/classify/route into the three streams. Accelerates
> the *front* of the funnel (idea capture), which is **not** the binding constraint
> (testing capacity is) — so it is a quality-of-life + knowledge-retention tool,
> NOT on the critical path to G3/deploy. **The one piece with real strategic merit
> is Stream C (pundit scoring, Phase 2)** — a *new* edge-discovery instrument —
> promoted to a real research item only after Phase-1 extraction quality is proven.
> Build the cheap Phase-1 MVP as background work; never ahead of the
> second-strong-edge brainstorm or XS-solo deploy-hardening.

## Open questions (for the plan / Phase 2)

- Extraction-quality bar: what hand-labelled-sample precision is "good enough" to
  green-light Phase 2 resolution?
- Pundit-call parsing: how to handle vague verbal setups ("around 65k-ish, looking
  for continuation") — log with a `confidence`/`fuzzy` flag and exclude from
  resolution, or attempt a band?
- Digest delivery: terminal-only (Phase 1) vs an opt-in Telegram digest once
  precision is proven.
