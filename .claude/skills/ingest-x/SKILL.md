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
  images + quoted-tweet; a post in a thread is recovered upward to its root
  (bookmark the LAST post), and video is handed off to /ingest-video rather than
  skipped. Invoke when the user says
  "/ingest-x", pastes one or more x.com / twitter.com status URLs, or says
  "ingest this/these X post(s)".
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest X post(s)

Spec: `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`.

Handles one or many URLs in a single invocation. Collect every URL the user pasted, then run the
flow once over the whole set.

## Flow

1. **Fetch the whole batch in one call.** The tool fetches each post once (text + chart paths), with
   a randomized cooldown between network fetches and a per-id dedup cache (a re-fetched URL returns
   `"cached": true` with no network, no sleep). Always use `--batch` so the output shape, cache, and
   downloaded chart paths are uniform even for a single URL:

   ```bash
   PYTHONPATH=. poetry run python tools/x_fetch.py <url1> <url2> … --batch --json
   ```

   Output is a JSON array; per element: `url`, `cached`, `photo_paths` (local chart files, already
   downloaded — do not re-fetch), and either `post` (`author`, `author_name`, `post_ts_utc`, `text`,
   `photo_urls`, `video_present`, `is_thread`, `is_quote`, `quoted_text`, `quoted_author`) or
   `unavailable` (reason). For any `unavailable` element (protected/deleted/age-gated), tell the user
   and ask them to paste that post's text + drop a screenshot; continue that one from step 2 with the
   pasted text + image. Do not run a manual download step — `photo_paths` already holds the local
   files.

   **1a. `is_thread: true` ⇒ recover the rest of the thread before extracting.** A single post from a
   thread is a fragment, and extracting from a fragment classifies a call without the argument it
   rests on. Run:

   ```bash
   PYTHONPATH=. poetry run python tools/x_fetch.py <url> --thread --json
   ```

   It returns `{"posts": [...root → leaf...], "notes": [...]}`, each post carrying `thread_pos`
   (0 = root), `in_reply_to_id`, `in_reply_to_author` and `conversation_count`. Feed the whole chain
   to the step-2 subagent as one author's argument, in order, and keep the per-post `post_ts_utc` — a
   thread spans time, so the call time is the timestamp of the post the item came from, or the leaf
   if it cannot be attributed (the conservative choice; it gives the call the shortest forward
   window).

   **Bookmark the last post of a thread, never the parent.** The endpoint exposes the reply-to chain
   but has no replies/children field, so a thread can only be recovered upward — a bookmarked parent
   yields nothing below it.

   Two traps: `conversation_count` is not thread length (it counts everyone's replies to the
   conversation — a 2-post thread routinely reads 9); and always read `notes` — a walk that stopped
   early on an author change, a hop cap or a deleted middle post says so there, and a truncated chain
   otherwise reads as a complete one.

   **1b. `video_present: true` ⇒ hand off to `/ingest-video`, don't make the operator re-paste.**
   `tools/video_fetch.py` already matches X status URLs via `_X_RE`, and `parse_video_url` returns
   `("x-video", <status_id>)` — the Groq whisper fallback covers caption-less X video (`GROQ_API_KEY`
   is set), so the same URL runs there unchanged. Say plainly that you are handing it off, and carry
   over any chain recovered in 1a. Do not attempt the vision pass here — this skill has no frame
   extraction.

2. **Extract via a subagent — one per post, pinned to sonnet.** For each post, dispatch a
   `general-purpose` subagent (Task tool) with `model: "sonnet"` (do not inherit the main-thread
   model) and `subagent_type: "general-purpose"`. Give it: the post `text` (and `quoted_text`
   prefixed `"[quoting @<quoted_author>]"` when present), the `photo_paths`, the schema below, and
   the inline rubric in the next section. Instruct it to read each image (vision) and return only
   this JSON; it must not read any repo/SoT/memory file — the rubric below is self-contained, so a
   single image read is all it needs:

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

   `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` set it to `NOVEL` as a
   non-blocking default (routing uses `content_type` for those).

3. **One consolidated review digest** for the whole batch. Print a single table — one row per post:
   author · `post_ts_utc` · symbol/direction · `content_type` · `verdict` · proposed routing ·
   `gap_note`; note `quoted_text` / `video_present` / `is_thread` / `cached` where set. Show each
   `chart_read` and the full extraction JSON below the table. Write nothing yet.

   Run the dedup check before printing the digest, once per non-dropped post, so its result appears
   in the digest rather than after approval:

   ```bash
   PYTHONPATH=. poetry run python tools/route_dedup.py check \
     --source-id <status id> --item-ts 0 --sink <route_target output> \
     --text "<the gist being routed>"
   ```

   - `already_routed: true` → do not append. Show the row as "already routed", and route nothing for
     it in step 4. This is exact and needs no judgement.
   - `candidates` non-empty → not a block. Print each candidate's `excerpt` and `shared_levels` under
     that post's row and let the user decide: new row, corroboration line on the existing entry, or
     drop.
   - `semantic_scope` says what the near-duplicate pass compared against: `all-entries` (Streams A
     and B) or `same-source` (Stream C — only rows from this same status id, never another author's).
     Report it; never let an empty `candidates` list read as "checked against everything and clean".
     On Stream C the same-source scope is near-inert here, since one X post routes one item — the
     identity layer is what protects this sink.
   - Discount a hit whose `shared_levels` are all round 4-digit numbers that could be years:
     `normalize_levels` drops bare ones, but a level written `2,050` is kept by design and two
     entries can share it coincidentally.

4. **Route on a single approval.** After the user approves the batch, for each post compute the
   destination with `tools/x_route.py::route_target(content_type, verdict)` (returns the sink path or
   `None` for a drop) and append per this table. Report a one-line result per post (routed → which
   file, or dropped → verdict).

   | content_type | verdict | Append to |
   | --- | --- | --- |
   | setup | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
   | mechanic | — | `docs/plans/mechanics-backlog.md` (a `-` list bullet) |
   | claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
   | claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | drop — state "seen, verdict X", write nothing |

   Create the sink file with a one-line header if it does not exist.

   **Stream C line** (`pundit-calls.jsonl`, one line, matches the research-ingestion spec's
   pundit-call schema):

   ```json
   {"source":"twitter","author":"<handle>","url":"<url>","call_ts_utc":"<post_ts_utc>","symbol":"<symbol>","direction":"<direction>","entry":"<entry>","stop":"<stop>","target":"<target>","horizon":"<horizon>","confidence":"<verbatim hedging or empty>","raw_quote":"<raw_quote>"}
   ```

   `target` and `entry` are machine-parsed — the format is a contract, not prose.
   `tools/pundit_score.py` resolves one number per field, and a hyphenated range anywhere in the
   string creates a zone that overrides the `/`-separated ladder. Write `target` as a bare ladder —
   `640 / 660 / 690` — and put ranges in `raw_quote` instead. A clarifying parenthetical re-breaks
   it: the constraint is on the whole field, not its leading number.

   Measured against wifey's own `parse_level_field` + `select_level` (`ref_close=64,000`,
   `role="target"`): `67,000 / 70,362.23 / 82,000` resolves to **67,000**, and appending
   `(or 65k-68k)` moves it to **65,000** on a long and **68,000** on a short. The zone resolves to
   whichever edge price reaches first, so on a long it lands nearer and manufactures an optimistic
   WIN, and on a short it lands further and strands the row OPEN — the error runs in both directions
   and neither is safe. `entry` degrades gently — a `60.0K-61.2K` box is a zone by intent — but the
   same override applies.

   Run `make wifey-pundit-score` as the last action of the round — reading the row back does not
   show you the parse, and at round-end every row is still OPEN, so a bad parse is free to fix then
   and invisible later.

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

   The trap it exists for: a level phrased as an invalidation ("unless it reclaims 29,200",
   "invalidated above X") is a stop, never a `target`. Writing one into a short's `target` puts the
   row instantly in profit, and the scorer books a `WIN` at ~0.00 R — a fake statistic rather than a
   visible error. Note the guard's blind spot: a row stating that one level and nothing else has no
   second leg to contradict it, so read the invalidation phrasing yourself too.

   Encode a one-legged row by rule, not by judgement — the shape is common, not exotic. A level
   serving as both entry and stop is zero risk: encode it as entry + target with the stop left
   unstated, never as an invented gap the pundit did not give. A row with no numeric level at all is
   a dropped candidate ("nothing scoreable"), not a Stream C write. A fabricated stop is
   unfalsifiable once it is in the ledger — unlike the fake-WIN above, no guard can ever see it.

   After each successful append, record it:

   ```bash
   PYTHONPATH=. poetry run python tools/route_dedup.py mark \
     --source-id <status id> --item-ts 0 --sink <sink path>
   ```

   `mark` runs after the write, never before — marking at check time would let an abandoned review
   consume the id and dedup away the real append later. Never mark a dropped post.

   Stream C's near-duplicate exemption is across sources only: two pundits making the same call are
   two real observations and `tools/pundit_score.py` scores both authors, so collapsing those would
   delete signal. Stream C still gets the exact `already_routed` block.

   `route_target` also takes `retrospective=` / `rejected=` keyword flags that drop a `setup`. This
   pipeline extracts neither, so both stay `False` and routing here is unchanged — an X post has no
   intro-recap block to lift a stale call from. See `/ingest-video`'s step 3 for what they mean. If a
   post does show a pundit walking through a trade and then arguing against taking it, that is
   `rejected` — routing it would score the author on a trade they declined.

## Inline classification rubric (self-contained — paste into the subagent prompt)

> A distilled snapshot of the SoT's Frozen / Closed / Parked state so the subagent classifies from
> the prompt alone. Refresh from `docs/north-star.md` (Frozen) and
> `project_todo_master.md` (Closed) periodically — treat as a de-biasing
> prior, not gospel; NOVEL still passes the human gate. This block is shared verbatim with
> `/ingest-video`'s rubric — keep the two in sync when either is refreshed. `content_type`:
> **setup** = a specific symbol+direction+levels trade call → Stream C; **mechanic** = an
> exit/risk/data/microstructure execution rule → Stream B; **claim** = a generalizable
> market-behaviour assertion → verdict below.

**Frozen — never propose a new TA detector.** The TA detector book (wicks, marubozu, ORB, FVG,
BOS/market-structure, EQH/EQL, order block, trend day, engulfing, pin bar, inside bar, hammer, doji,
morning/evening star, fib retracement / golden zone, OTE, EMA — plus the stripped crypto-era
detectors: liquidity sweep, funding extreme, SMT, CVD divergence) is frozen under an inherited
category verdict (TA = negative-EV; no new boolean detectors, no tp_r/gate/threshold sweeps). A claim
that just restates one of these candlestick/structure patterns → `FROZEN-CATEGORY`.

**Scope — the freeze binds this repo's equity signal engine, nothing wider.** It covers the boolean
bar-pattern detectors in `analytics/strategies/_registry.py::DETECTOR_REGISTRY`, evaluated at
`4h`/`1d`/`1wk` on the US-equity universe. It is a policy — an inherited category verdict — never a
measurement about moving averages or price structure in general. Mark `FROZEN-CATEGORY` only when
the claim would land as a new detector or a new sweep in that book. A monthly-timeframe MA band, a
regime or drawdown classifier, and anything on another asset class or horizon are all out of scope:
judge them on their merits. When unsure, return `NOVEL` and let the human review gate decide — a
wrong `FROZEN-CATEGORY` drops the item silently, a wrong `NOVEL` costs one line of review.

**Already-tested (verdict known → `ALREADY-TESTED`, drop unless materially new evidence).** All
eight research sleeves measured on US equities came back non-positive and the free-data edge arc is
concluded (`CLAUDE.md` § Sleeve verdicts):

- Absolute **trend** (multi-speed EWMAC): G2 FAIL — portfolio Sharpe −0.05, negative even pre-cost
  (a signal failure, not a cost failure).
- Cross-sectional **XS momentum** (relative strength): G3 FAIL — combined Sharpe −0.156 @2bps,
  corr_to_trend +0.62 (not even a diversification win).
- **Residualized XS-mom** (beta-stripped + skip-month): FAIL — committed L/S cell +0.15, DSR 0.44;
  the long-only +0.88 leg is survivorship/beta-confounded.
- **Low-vol / BAB** (beta-neutral L/S): FAIL — the realized-β guardrail fired (β +3.9): ex-ante
  β-neutralization did not deliver market-neutrality.
- **Cross-asset TSMOM** (13-ETF basket): FAIL, clean — β-guardrail held; +0.41 cost-free, never
  ≥0.7 gate. Futures-grade breadth is a paid-data question.
- **PEAD-lite** (seasonal SUE on free EDGAR data): FAIL — β-guardrail fired on the broad arm; the
  controlled mega arm showed negative drift. No PEAD in liquid large-caps net of cost on free data.
- **Gap-fill magnet** (unfilled gaps as magnets / "gaps always fill"): EXCLUDED, direction REFUTED —
  cost-free the magnet returns −0.460, so gaps continue rather than revert; the post-hoc inverse
  never reaches the bar, and ~211× daily gross turnover kills both directions. "90.3% of gaps fill
  within 60 sessions" is true and almost entirely diffusion (matched placebo 88.9%).
- **Velocity alternation** (the pace of a decline predicts the pace of the next move): EXCLUDED as a
  null — β-guardrail fired, beta-hedged −0.169 at alpha t −0.49, and the velocity ratio performs
  indistinguishably from depth alone.
- DOW / day-of-week seasonality (e.g. "Monday is the weekly high → short"): parent-inherited
  verdict — base rate real but the tradeable edge decays OOS; the version that looks clean is
  look-ahead.
- Exit-policy fixes ("your stops are wrong, not your entries"): BOUNDED — the replay A/B measured
  the lever's ceiling at +0.368R of paired uplift, entirely the time-stop, and no arm's own mean R
  clears zero. Re-run trigger is ledger growth, not a restated claim.

**Parked / captured / blocked:**

- IPO post-hype dip-buy (long a faded recent IPO reclaiming its listing price): already captured as
  a thesis memo — a reassertion is "already in inbox", don't duplicate the H-row.
- Anything requiring paid data (Polygon intraday, options flow, L2/auction feeds, futures breadth):
  `NOVEL` in principle but data-blocked — say so in `gap_note` (paid data currently declined).

**`NOT-FALSIFIABLE`:** vibes / no testable prediction / unfalsifiable hindsight.
**`NOVEL`:** a genuinely new, testable, uncovered market-behaviour claim.

## Guardrails

- Output is a hypothesis/setup/mechanic to test, never an "add a detector" task — the detector list
  is frozen.
- The free-data edge arc is concluded: routing a NOVEL claim into `thesis-inbox.md` is capture, not
  a commitment to test. Never start a new edge-hunt from an ingested claim without an explicit user
  go.
- Never auto-write a stream file before the user approves the digest — one approval covers the whole
  batch.
- Scope: text + still images + quoted-tweet surfacing + upward self-thread recovery (1a). Video is
  handed to `/ingest-video` (1b), not skipped. Still out of scope: downward thread expansion (the
  endpoint has no children field — structurally impossible), reply bodies from other authors, and
  scraping. Syndication + manual-paste are the only two fetch paths.
