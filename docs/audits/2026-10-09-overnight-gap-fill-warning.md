# Overnight gap-fill warning — no measured value, removed from live alerts

**Audit:** this file. Owner: Issue #400. One-shot script:
`docs/plans/scripts/gap_warning_audit_400.py` (gitignored, read-only on `analytics.db`).

## Verdict

EXCLUDED: no cell earned a keep, and the warning was removed. The live alert path appended
`overnight_gap_lib.gap_fill_warning` whenever the signal candle opened away from the prior close
and never traded back to it, on the premise that unfilled gaps are magnets. `gapfill/` had already
refuted that premise (`docs/audits/2026-08-14-edge-hunt-5-gapfill-magnet.md`), and this audit
measured the warning itself. On the primary substrate the long cell is INSUFFICIENT, with a
clustered lift (warned − clean) of +0.072R and a CI of [−0.191, +0.339]. The short cell points the
other way: warned shorts averaged +0.288R against +0.029R clean, a lift of +0.258R with a CI of
[−0.012, +0.530]. Neither cell is a powered null, so this does not show the warning carries no
information. It shows that nothing measured supports it, and its only stated rationale is refuted.
Under the pre-registered rule, that drops it.

## Pre-registered rule

Written into the script's docstring before its first run on 2026-10-09:

- **Primary:** `backtest_trades` on the two live timeframes (`4h`, `1d`), deduped across saved runs
  as in H9 (`tools/warning_value_audit.py::normalize_backtest`).
- **Keep** only if `audit_guard` returns ENABLE (SUPPRESS-CANDIDATE) and the session-day-clustered
  lift CI lies entirely below zero. ENABLE alone tests the warned slice's absolute mean, which a
  net-negative book can clear with no warning-specific information.
- **Drop** otherwise: REVERSE, COSMETIC, INSUFFICIENT, or ENABLE without lift.
- The live ledger and `1wk` are corroboration and exploration only.

## Method

- The flag is re-derived with the live helpers (`get_overnight_gap`, `gap_fill_warning`), imported
  rather than reimplemented, on the window the scanner used: the signal candle plus the bar before
  it, with the signal-candle close as entry. On `4h`, that pair spans the overnight gap only when
  the signal bar is the session's first slot. This mirrors the live alert as it ran, not an
  idealised overnight gap.
- For a long, the warning's `entry < prev_close` test is implied by "not filled" (the candle's high
  stayed below the prior close), so the flag reduces to "gapped against the prior close and never
  traded back to it". The short case mirrors this.
- The two cells (long, short) form their own Holm family, so H9's 12-cell family is unchanged:
  bar ±0.05R, `min_n` 30, 10,000 resamples, seed 12345, clustered by session day
  (`audit_guard.session_day_keys`).
- The lift CI resamples session days and recomputes the warned and clean means from the resample.
  H9's `two_sample_lift_ci` is i.i.d., which is too narrow on a same-day cross-section.

## Results

Backtest: 7,096 deduped entries, 7,092 tagged. Primary (`4h` + `1d`): n = 6,595, of which 674 were
warned (10.2%), so the channel is live. The book's avg_r is −0.003.

| Cell | n warned | session days | avg warned | avg clean | warned CI | Holm p | decision | clustered lift (CI) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| long | 372 | 132 | +0.005 | −0.067 | [−0.246, +0.264] | 0.966 | INSUFFICIENT | +0.072 [−0.191, +0.339] |
| short | 302 | 146 | +0.288 | +0.029 | [+0.007, +0.564] | 0.038 | INSUFFICIENT | +0.258 [−0.012, +0.530] |

The short cell's Holm p clears 0.05, but its warned CI does not clear +0.05R, so `audit_guard` does
not return REVERSE.

Exploratory, by timeframe (no decisions drawn):

- `1d`: long lift +0.275 [−0.103, +0.675]; short +0.363 [+0.000, +0.750].
- `4h`: long −0.115 [−0.400, +0.195]; short +0.162 [−0.178, +0.494]. The `4h` long is the only
  cell that leans the warning's way, and its CI contains zero.
- `1wk` (backtested, never scanned live): 14 warned trades, too few to read.

Live ledger: 587 tagged, 36 warned (22 long, 14 short). Every cell is below `min_n`.

## What changed

- `analytics/overnight_gap_lib.py` and `tests/test_overnight_gap_lib.py` deleted.
- The scanner and `wifey signal test` no longer compute the gap or the rough TP that existed only
  to feed it, and the scan tuple no longer carries a gap.
- `format_signal_alert` and `format_confluence_alert` dropped the `gap_warning` parameter.

To re-open, use a different premise. "Gaps continue" is what `gapfill/` measured, and it would
need its own pre-registration rather than a re-read of these cells.
