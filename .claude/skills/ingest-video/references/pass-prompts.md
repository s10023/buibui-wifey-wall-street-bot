# Subagent prompt contracts and field notes

Supports `SKILL.md` steps 3 and 6.

## Pass-1 return contract

Instruct it to return only this JSON:

```json
{
  "summary": "one paragraph, English",
  "stated_ts_utc": "ISO-8601 with an explicit UTC offset, or a bare YYYY-MM-DD, or null",
  "stated_date_only": false,
  "stated_ts_raw": "verbatim quote or empty",
  "candidates": [
    {"ts": 252.0, "content_type": "setup|claim|mechanic", "specificity": 1-5,
     "is_intro_recap": false, "retrospective": false, "gist": "..."}
  ]
}
```

`is_intro_recap` / `retrospective` default to `false` and are only ever set by the
`intro_recap_s` rule above — a channel with no `handle`, or `intro_recap_s: 0`, leaves both
false and nothing changes.

**A `stated_ts_utc` carrying a time must carry an explicit UTC offset** (e.g.
`2026-07-14T08:00:00+08:00`) — never a bare local time. `tools/video_calltime.py` rejects a
naive (offset-less) timestamp and silently falls back to publish time, so a subagent that
emits `2026-07-14T08:00:00` with no offset gets the same downstream result as emitting
nothing, just less honestly. Instruct the subagent: state the offset whenever the speaker's
timezone is inferable from context — never guess UTC for a time of day.

When only the date is recoverable, emit the bare `YYYY-MM-DD` with `stated_date_only: true`
rather than inventing a time and an offset to go with it. `resolve_call_ts` accepts a naive
value on that flag alone and normalises it to `23:59:59Z`, the last instant of the stated
date — so an invented time can only credit the call earlier than the flag would. Emit `null`
when neither is recoverable.

## Pass-2 isolation warning

**Not reading files does not make the agent context-free.** A `general-purpose` subagent can
surface project knowledge (e.g. citing `CLAUDE.md` or `MEMORY.md` findings) it was never
given and never read, so the rubric's "self-contained" framing bounds what the agent reads,
not what it already knows. This matters even when what leaks is accurate: the rubric is a
distilled snapshot so the extractor classifies against a frozen prior, and an agent silently
drawing on the live SoT is a different experiment from the documented one. Treat the leak as
present when designing any experiment that depends on isolation. This is confirmed for
`general-purpose` specifically, the type both passes actually dispatch — `Explore` and a
`tools:`-restricted custom agent are untested here, so do not assume switching agent types
closes it.

## Pass-2 return contract

Instruct it to return only this JSON:

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

- Where a frame shows a number/level/structure that contradicts the transcript, the chart
  wins: use the chart's value in `entry`/`stop`/`target`/`chart_read`, and record the
  transcript's original claim in `corrected_from`. When nothing was corrected, leave
  `corrected_from` empty.
- The chart wins only on a drawn value — a line, annotation, printed label, or measured
  readout placed on the chart. A value inferred from where the live price ticker happens to
  sit is not a correction: put that reading in `chart_read`, keep the spoken value in
  `entry`/`stop`/`target`, and leave `corrected_from` empty. A pundit is scored on the level
  they stated — e.g. a stated 61,000 pivot must not be silently moved to 61,600 just because
  the ticker sits near a drawn arc's anchor; that would be applying the rule to the wrong
  class of evidence.
- `corrected_from` carries chart-vs-transcript corrections only. A symbol normalisation is
  not one — normalise `symbol` and leave `corrected_from` empty. Here that means an index
  call resolving to its index ticker (`S&P 500` → `^GSPC`, never an ETF proxy) or a
  class-share form yfinance expects (`BRK.B` → `BRK-B`), so the scorer resolves against the
  right bars. Same reason `confidence` and `vision_confidence` stay separate — a field
  carrying two semantics can be queried for neither.
- Anything the frames do not visually corroborate (no frame near that `ts`, or the nearest
  frame doesn't show what was said) gets `vision_confidence: "low"`. Reserve `"high"` for a
  frame that directly confirms the claim; `"medium"` for partial/ambiguous support.
- `raw_quote` stays in the transcript's original language (Chinese stays Chinese);
  `raw_quote_en` is always English (identical to `raw_quote` when the source is already
  English).
- `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` default it to
  `NOVEL` (non-blocking — routing uses `content_type` for those, same as `/ingest-x`).
- `chart_present: false` when no frame in this video shows a chart at all (pure
  talking-head) — still emit `items` from the transcript alone, all
  `vision_confidence: "low"`, `frame_path: null`.

## confidence versus vision_confidence

`confidence` means the same thing across every source already in
`docs/plans/pundit-calls.jsonl`: the pundit's verbatim hedging phrase, or empty. This
pipeline does not extract that from a video (pass 2's contract is a visual-corroboration
read, not a hedging-language read). `vision_confidence` is video-only and records whether
pass 2 could visually corroborate the item against a frame — a property no other source in
the ledger has or needs. Keeping them as separate columns keeps a future
`GROUP BY confidence` (or any other query over the hedging-language column) honest across
every source, instead of silently mixing two incompatible populations.
