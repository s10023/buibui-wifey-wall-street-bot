# The dispatch recency window — a third of the alert surface never reached Telegram

**Date:** 2026-08-25
**Verdict:** FOUND and FIXED — a delivery defect, not an edge claim. No sleeve verdict
moves and no measured `avg_r` changes; the ledger was always complete. What was
incomplete was Telegram, by 120 of 351 candles (34%), every one of them the session's
first 4h bar. The remaining half of the gap is a cadence habit and is deliberately
NOT fixed in code.

## The question

The reported symptom was "no signals this week, though I ran `CATCH_UP=1 make go-live`
yesterday and today". The first job was to decide whether that was a fault at all.

**It was not.** `day_filter = "tue_thu"` suppresses on the bar's **open weekday**, and
the two runs (Mon 08-24 13:24 UTC, Tue 08-25 12:52 UTC) could only see Fri 08-21 and
Mon 08-24 bars respectively. Zero eligible candles both days, by design. Corroborated
independently: all 204 watermarks in `signal_state.json` fall on **Tue 50 / Wed 84 /
Thu 70 — zero Mon, zero Fri**. OHLCV was current and level across all 13 symbols.

The defect below was found while establishing that.

## Finding 1 — the `:wife` watermark is a dispatch oracle, and nothing else is

`fired_at_ms` looks like an alert timestamp and is not one. `upsert_signal_outcome`
sets `fired_at_ms = excluded.fired_at_ms` on conflict, and `scanner.py` rewrites the
row on **every** scan that still detects the signal — so the column records the last
*re-detection*, not the dispatch. **The ledger holds no dispatch history at all.**

What does record it: `is_backfill` marks the **primary** watermark alone
(`scanner.py:1154`), while a real send marks primary **and** wife (`scanner.py:1210`),
both inside one `if send_telegram:` block. So `primary > wife` for a key means its most
recent candle was consumed without alerting. **50 of 102 keys were in that state.**

⚠ The alternative world — a wife send failing while primary succeeded — is ruled out
rather than assumed: wife sends demonstrably worked on 08-19, and the diverging keys'
wife watermarks sit at scattered *older* dates (Jun 30, Jul 8, Jul 16, Jul 28) rather
than sharing one recent cutoff, which is the signature of backfill, not of an outage.

## Finding 2 — the counterfactual, from the bar grid

Because `fired_at_ms` is unusable, dispatch was reconstructed deterministically instead:
for a given run cadence, the newest closed candle per `(symbol, tf)` follows from the bar
grid alone. 351 ledger candles since 2026-06-01:

| Cadence @12:50 UTC | Dispatched | Silently consumed |
| --- | --- | --- |
| Mon–Fri | **231/351 (66%)** | 120 — *all* the 13:30 UTC 4h bar |
| Mon–Thu (Friday skipped) | **154/351 (44%)** | 197 (+41 × `1d`, +36 × 17:30 4h) |
| Wed–Fri only | 231/351 (66%) | 120 — **identical to Mon–Fri** |

Two separable conclusions, needing different fixes:

1. **Friday is load-bearing — 66% vs 44%.** And because Wed–Fri scores *identically* to
   Mon–Fri, **Mon/Tue pre-open runs can never alert**: they see Fri/Mon bars, which
   `tue_thu` discards. They still earn their keep on sync, ledger and outcome backfill,
   so "no-op" is true of the **Telegram leg only**.
2. **The session's first 4h bar was structurally undeliverable** — under one pre-open run
   a day it can never *be* the newest closed candle, because the session's second bar
   always outranks it.

## Finding 3 — the old cut was arbitrary, not principled

The rule dropped a candle for being stale while sending one barely fresher. At a Wed
12:50 UTC run:

| bar | closes | age at the run | old rule |
| --- | --- | --- | --- |
| Tue 17:30 UTC | Tue 21:30 | **15.3h** | dispatched |
| Tue 13:30 UTC | Tue 17:30 | **19.3h** | dropped |
| Mon 17:30 UTC | Mon 21:30 | 39.3h | dropped |

A 4-hour difference decided delivery. That is the whole case for replacing a point rule
with a bounded one — not that stale signals should ship, but that the boundary was in an
indefensible place.

## The fix

`may_dispatch_candle` in `analytics/signal/scanner.py`. The newest closed candle always
dispatches; an older one dispatches while its **close** is within `max_alert_age_hours`.
The shared base `config/strategy_params.toml` sets **24.0**, inherited by both live
configs via `extends`; the library default stays **0.0**, which reproduces the old rule
exactly, so no caller changes behaviour merely by upgrading.

**24.0 is a calendar bound, not a swept parameter.** It admits the previous session's
bars (19.3h / 15.3h) and excludes the session before (39.3h) — roughly 4h of headroom on
the side that matters and 15h of clearance on the side it must exclude. Nothing was
optimised against an outcome, which is the only way to add a live constant here without
adding an overfit one.

### What it deliberately does NOT do

⚠ **It does not recover a skipped run day.** At a Monday run, Thursday's bars are ~87h
old; admitting those means shipping signals whose entry, stop and target are four days
stale. Conclusion 1 stays a habit — **run Wed/Thu/Fri without fail** — and no code change
is proposed for it. Pricing the arbitrary boundary and the missed-day problem as one
knob would have bought a burst in exchange for a tidier story.

⚠ **It cannot replay history.** A watermark a previous backfill consumed stays consumed:
already-alerted candles are dropped upstream at `scanner.py:631` (`is_new_candle`) before
the window is ever consulted. So turning this on does not re-send the 50 keys' lost
candles — it changes the verdict only for candles a cycle sees for the first time. This
is asserted by a constructed test, not by inspection.

⚠ **It does not make a stale alert tradeable.** Under a once-daily cadence every alert
already carries candle-derived levels a full session old, so live fills cannot match the
ledger. That is inherent to Phase A (signals only, no order layer) and is out of scope.

## Transferable lessons

- **A column that looks like a timestamp of an event may be a timestamp of the last time
  something noticed the event.** `fired_at_ms` reads as "when it fired" everywhere it is
  quoted. Check the upsert's conflict clause before treating any column as a history.
- **A second channel written on the same success path is a free oracle for the first.**
  The wife watermark was never designed as instrumentation; it became the only way to
  measure this because backfill marks one channel and dispatch marks both.
- **When a rule drops X for being stale and keeps Y that is nearly as stale, the rule is
  a proxy for something else.** Here the real rule was "whatever happens to be newest",
  which under a fixed cadence is a statement about the run schedule, not about staleness.
- **Two defects behind one symptom deserve two fixes or one fix and one refusal**, never
  one knob stretched to cover both.
