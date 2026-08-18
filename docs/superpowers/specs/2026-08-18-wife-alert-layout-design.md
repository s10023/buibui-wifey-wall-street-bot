# Wife-channel alert layout — condensed mirror (design)

- **Date:** 2026-08-18
- **Status:** design approved, implementation pending
- **Surface:** `signals/alert_formatter.py::format_wife_alert` / `format_wife_confluence_alert`
- **SoT row:** N4 (polish the Telegram alert copy) — ungated, not gated on G1–G4

## Goal and success metric

**Goal.** Re-shape the wife channel so it carries the personal channel's skeleton in condensed
form, replacing a layout that was locked at "Minimal" by user choice and has since been re-opened.

**Success metric.** Both channels render the same trade in a recognisably shared shape, the wife
render fits a phone without wrapping, and nothing an alert drops becomes unrecoverable — every
omitted field is still reconstructible from `signal_alert_outcomes`.

## Why this is being re-opened

The Minimal layout was a deliberate user choice, not a default, so re-opening it is a decision
rather than an improvement. Two things prompted it:

1. The layout was never read in situ. The wife message is built only inside `if send_telegram:`
   (`analytics/signal/scanner.py:1204`, `analytics/signal_test_runner.py:398`), and
   `TELEGRAM_WIFE_DRY_RUN=1` logs the first line plus a character count
   (`utils/telegram_router.py:57`). There is no way to read the rendered body without sending it
   to the real channel — so the layout was locked without anyone seeing it rendered.
2. `HOLD` names a position instruction. It is fired by a **short** signal and presumes the reader
   already holds something, when the intended meaning is "take no action".

The previous decision lived only in session memory, which is why it was re-litigated. This file is
its durable home; the next session should read it before changing the layout again.

## Decision

Adopt the condensed mirror, and rename `HOLD` to `WAIT`.

### BUY (long)

```text
BUY — $AAPL 4h  ★★★☆☆
Entry 326.99  ·  15-Jul 21:30 MYT

Stop 320.45 (−2.0%)  ·  Target 340.07 (+4.0%)

⚠️ Low volume — weaker conviction
```

### WAIT (short)

```text
WAIT — $AAPL 4h
306.47  ·  10-Aug 21:30 MYT
Sit tight — conditions look weak
```

### What changed and why

| Change | Reason |
| --- | --- |
| `HOLD` → `WAIT` | "Hold" is a position instruction presuming an open position; the reader is long-only and the intent is "no action" |
| Stars added to the header | Conviction at a glance, no jargon, already computed |
| `SL:`/`TP:` → `Stop`/`Target` | Plain English for a non-trader |
| `2.0R` dropped, percentages signed | R-multiples are trader jargon; a signed percentage is directly readable |
| Levels collapse to one line | Halves the vertical space on a phone |
| At most **one** warning, most severe first | Restores genuinely actionable information Minimal dropped, without importing the full block |

### What stays out

The backtest edge line, the stats block, the strategy name, the reason string and the session tag.
None is actionable for this reader, and each remains recoverable from `signal_alert_outcomes`.
The `n/a (0 longs)` edge line in particular ships a null as a headline and must not cross over.

## Implementation notes

- Both wife formatters live in `signals/alert_formatter.py`; `format_wife_alert` already delegates
  to `format_wife_confluence_alert`, so the layout has exactly one implementation site.
- Warning selection reuses `_build_candle_warnings`, which the personal channel already calls. The
  wife formatter currently takes no `ohlcv_df`, so the parameter has to be threaded from both call
  sites for warnings to be available; with no frame passed, the warning line is simply absent.
- Severity ordering does not exist yet — `_build_candle_warnings` appends in a fixed source order
  and never sorts. "Most severe first" therefore needs an explicit rank; it cannot reduce to "the
  first entry", because the first entry is `⚡ Volume spike — high conviction`, a *positive* note.
  Taking the head of the list would sometimes render an encouragement under a ⚠️ heading.

## Testing

- Unit tests for both variants in `tests/test_alert_formatter.py`, asserting the `WAIT` header, the
  absence of `HOLD`, signed percentages, and that no `R` multiple appears.
- A negative test that the edge line and stats block never reach the wife render.
- A local render path so the body can be read without sending: the precondition named above, and a
  prerequisite for reviewing any future change to this layout.
