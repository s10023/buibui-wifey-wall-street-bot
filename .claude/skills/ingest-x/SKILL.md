---
name: ingest-x
description: >
  Ingest one OR MORE X/Twitter post URLs into the research pipeline in a single
  call. Fetches each post's text + chart image with NO login/scraping via the
  public syndication endpoint (tools/x_fetch.py) — batched with a randomized
  cooldown + a dedup cache so re-runs hit zero network — reads each chart with
  vision in a per-post subagent, classifies it (content-type gate -> the research
  pipeline's 4-bucket verdict taxonomy), and routes it (after ONE human review
  gate for the whole batch) into one of three streams: A hypotheses ->
  docs/plans/thesis-inbox.md, B mechanics -> docs/plans/mechanics-backlog.md,
  C daily setups -> docs/plans/pundit-calls.jsonl. Iteration 2 = text + still
  images + quoted-tweet; video is detected and skipped. Invoke when the user says
  "/ingest-x", pastes one or more x.com / twitter.com status URLs, or says
  "ingest this/these X post(s)".
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest X post(s)

Spec: `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md` (ported from
parent #466/#467; the iter-2 batch/cooldown/cache/sonnet plan stayed in the parent).

Handles **one or many** URLs in a single invocation. Collect every URL the user
pasted, then run the flow once over the whole set.

## Flow

1. **Fetch the whole batch in ONE call.** The tool fetches each post once (text +
   chart paths), with a randomized cooldown *between network fetches* and a
   per-id dedup cache (a re-fetched URL returns `"cached": true` with no network,
   no sleep). Always use `--batch` so the output shape, cache, and downloaded
   chart paths are uniform even for a single URL:

   ```bash
   PYTHONPATH=. poetry run python tools/x_fetch.py <url1> <url2> … --batch --json
   ```

   Output is a JSON **array**; per element: `url`, `cached`, `photo_paths`
   (local chart files, already downloaded — do NOT re-fetch), and either `post`
   (`author`, `author_name`, `post_ts_utc`, `text`, `photo_urls`, `video_present`,
   `is_thread`, `is_quote`, `quoted_text`, `quoted_author`) or `unavailable`
   (reason). For any `unavailable` element (protected/deleted/age-gated), tell the
   user and ask them to paste that post's text + drop a screenshot; continue that
   one from step 2 with the pasted text + image. Do NOT run the old `python -c`
   download one-liner — `photo_paths` already holds the local files.

2. **Extract via a subagent — one per post, pinned to sonnet.** For each post,
   dispatch a `general-purpose` subagent (Task tool) **with `model: "sonnet"`**
   (do not inherit the main-thread model) and `subagent_type: "general-purpose"`.
   Give it: the post `text` (and `quoted_text` prefixed
   `"[quoting @<quoted_author>]"` when present), the `photo_paths`, the schema
   below, and the **inline rubric** in the next section. Instruct it to Read each
   image (vision) and return ONLY this JSON — it must NOT read any repo/SoT/memory
   file (the rubric below is self-contained; that is the whole point — one image
   Read, no 7K-token SoT re-read):

   ```json
   {
     "symbol": "AAPL | null",
     "direction": "long | short | neutral | null",
     "entry": "...", "stop": "...", "target": "...",
     "horizon": "intraday | swing | unspecified",
     "setup_type": "free text",
     "raw_quote": "the sentence(s) the call/claim came from",
     "chart_read": "what the chart shows (levels, structure, annotations)",
     "content_type": "claim | setup | mechanic",
     "verdict": "NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE",
     "gap_note": "one line: implied primitive + does the system already have/test/freeze it?"
   }
   ```

   `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` set it
   to `NOVEL` as a non-blocking default (routing uses `content_type` for those).

3. **ONE consolidated review digest** for the whole batch. Print a single table —
   one row per post: author · `post_ts_utc` · symbol/direction · `content_type` ·
   `verdict` · proposed routing · `gap_note`; note `quoted_text` / `video_present` /
   `is_thread` / `cached` where set. Show each `chart_read` and the full extraction
   JSON below the table. Write NOTHING yet.

4. **Route on a single approval.** After the user approves the batch, for each post
   compute the destination with `tools/x_route.py::route_target(content_type, verdict)`
   (returns the sink path or `None` for a drop) and append per this table. Report a
   one-line result per post (routed → which file, or dropped → verdict).

   | content_type | verdict | Append to |
   | --- | --- | --- |
   | setup | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
   | mechanic | — | `docs/plans/mechanics-backlog.md` (a `-` list bullet) |
   | claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
   | claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |

   Create the sink file with a one-line header if it does not exist.

   **Stream C line** (`pundit-calls.jsonl`, one line, matches the research-ingestion
   spec's pundit-call schema):

   ```json
   {"source":"twitter","author":"<handle>","url":"<url>","call_ts_utc":"<post_ts_utc>","symbol":"<symbol>","direction":"<direction>","entry":"<entry>","stop":"<stop>","target":"<target>","horizon":"<horizon>","confidence":"<verbatim hedging or empty>","raw_quote":"<raw_quote>"}
   ```

   **Sign-check every Stream C row before you append it** — do not eyeball this:

   ```bash
   PYTHONPATH=. poetry run python tools/x_route.py --check-levels <<'JSON'
   <the candidate Stream C lines, one JSON object per line>
   JSON
   ```

   A long must satisfy `stop < entry < target`, a short `target < entry < stop`. A `WARN`
   row is mis-encoded, not a real call: fix the field assignment and re-run, or report it
   as a dropped candidate with the reason stated. The check **never rewrites or drops**
   anything — it prints and exits 1, and the decision stays yours.

   The trap it exists for: a level phrased as an invalidation ("**unless** it reclaims
   29,200", "invalidated above X") is a **stop**, never a `target`. Writing one into a
   short's `target` puts the row instantly in profit, and the scorer books a `WIN` at
   ~0.00 R — a fake statistic rather than a visible error (this happened on 2026-08-04).
   Note the guard's blind spot: a row stating that one level and *nothing else* has no
   second leg to contradict it, so read the invalidation phrasing yourself too.

## Inline classification rubric (self-contained — paste into the subagent prompt)

> A distilled snapshot of the SoT's Frozen / Closed / Parked state so the subagent
> classifies from the prompt alone. **Refresh from `project_todo_master.md`
> periodically** — treat as a de-biasing prior, not gospel; NOVEL still passes the
> human gate. This block is shared verbatim with `/ingest-video`'s rubric — keep
> the two in sync when either is refreshed. `content_type`: **setup** = a specific
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

- Output is a hypothesis/setup/mechanic to TEST — never an "add a detector" task. The
  detector list is frozen.
- The free-data edge arc is CONCLUDED: routing a NOVEL claim into
  `thesis-inbox.md` is *capture*, not a commitment to test — never start a new
  edge-hunt from an ingested claim without an explicit user go.
- Never auto-write a stream file before the user approves the digest — one approval
  covers the whole batch.
- Iteration 2: text + still images + quoted-tweet surfacing. No video (use
  `/ingest-video` for that), no thread-walking, no reply bodies, no scraping.
  Syndication + manual-paste are the only two fetch paths.
