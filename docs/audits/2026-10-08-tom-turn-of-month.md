# TOM — the turn-of-the-month premium is real on 1988→ but too small to hedge into a sleeve, and gone since 2006

**Date:** 2026-10-08
**Audit:** this file. Owner: Issue #422. Pre-registration: `docs/superpowers/specs/2026-10-08-edge-pillars-research-design.md` § Amendment 2, frozen 2026-10-08 (commit `4922b48`) before any TOM return was computed, unchanged.

## Verdict

EXCLUDED as a deployable sleeve; the premise is not refuted on the primary panel, but it has not
held since 2006. On 1988-01-04 → 2026-08-31 at 2 bps, the beta-hedged annualized Sharpe is
+0.290 with a stationary-bootstrap CI of [+0.004, +0.621]. The upper bound sits below
`GATE_SHARPE` 0.7, so a gate-sized hedged Sharpe is ruled out, and DSR is 0.553 at four trials.
The lower bound only just clears zero, so the premise reads positive on the primary panel, and
the literature's own statistic agrees: the market's mean daily excess return is +8.92 bp inside
the window and +2.65 bp outside, a difference of +6.27 bp with a CI of [+1.17, +11.71]. That
reading does not survive any sensitivity. At 5 bps or with a one-session lag the CI contains zero,
the second half of the panel is +0.006, and the out-of-sample 2006→ panel is −0.009. The split
was registered for sign only, so this is consistent with the post-publication decay the spec
cited, not a test of it. TOM is not reopened as an overlay: an edge claim cannot change
yardstick after its result is known (`docs/north-star.md` § Two yardsticks).

## Panel and frame

- French file: the one OV-1 and VM used, the CRSP 202608 build, sha256 prefix
  `2f29e22546069914`. File span 1926-07-01 → 2026-08-31, ending on a month's last session, so
  `complete_months` cut nothing.
- **Primary: 1988-01-04 → 2026-08-31, 9,738 sessions**, 38.66 calendar years. **Secondary:
  2006-01-03 → 2026-08-31, 5,197 sessions.** Part 3's power runs used 9,764 and 5,223 sessions,
  26 more each, consistent with an XNYS count run to a later end date; at the measured counts the
  bars are 0.5333 and 0.6310, within 0.001 of the spec's.
- The window holds 19.1% of sessions, and every one of the 464 primary months has exactly four
  window sessions.
- `tom` turns over 24.01 units a year, two sides per window.
- Market is French `Mkt-RF + RF`, cash is French `RF`, and the tool never opens `analytics.db`.

## Precision, printed before any point estimate

| Panel | Hedged-SR CI half-width | Part 3 analytic |
| --- | --- | --- |
| 1988→ | 0.3085 | 0.3149 |
| 2006→ | 0.2817 | 0.4305 |

The primary half-width matches the spec's analytic figure. The secondary's bootstrap width is
narrower than the analytic one, but the secondary decides nothing.

## Gate at 2 bps

| Leg | Bar | Result |
| --- | --- | --- |
| Hedged annualized Sharpe | ≥ 0.7 | +0.290 |
| DSR (4 trials, `sr_variance` 0.0652/252) | ≥ 0.95 | 0.553 |
| Bootstrap 95% CI | lower bound > 0 | [+0.004, +0.621] |

`β` is +0.185, close to the 19.1% window share. Attribution: alpha +2.01% a year, t +1.80.
Verdict function: the CI's upper bound is below 0.7, so EXCLUDED; the premise reads positive
because the lower bound is above zero.

## Reported, not gated

| Arm | bps | Ann. return | Ann. vol | Sharpe | Ulcer | Max DD | TUW (y) | In market |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bh` | 2 | 11.64% | 0.1782 | 0.544 | 13.89 | −54.6% | 6.6 | 100.0% |
| `tom` | 2 | 6.63% | 0.0767 | 0.496 | 4.66 | −19.2% | 5.3 | 19.1% |
| `tom` | 5 | 5.86% | 0.0768 | 0.402 | 5.27 | −19.8% | 6.2 | 19.1% |

Unhedged, `tom` earns 57% of the market's annual return in 19% of the sessions. Most of that is
the equity premium earned on those days, which the hedge removes.

## Sensitivity

| Run | `β` | Hedged SR | CI | DSR | Verdict | Premise |
| --- | --- | --- | --- | --- | --- | --- |
| Primary, 2 bps | +0.185 | +0.290 | [+0.004, +0.621] | 0.553 | EXCLUDED | positive |
| 5 bps | +0.185 | +0.186 | [−0.101, +0.509] | 0.305 | EXCLUDED | not refuted |
| 1-session lag | +0.202 | +0.169 | [−0.129, +0.462] | 0.268 | EXCLUDED | not refuted |
| First half, 1988-01-04 → 2007-04-25 | +0.199 | +0.646 | [+0.198, +1.168] | 0.950 | BOUNDED | positive |
| Second half, 2007-04-26 → 2026-08-31 | +0.177 | +0.006 | [−0.299, +0.283] | 0.124 | EXCLUDED | not refuted |
| 2006→ (secondary) | +0.177 | −0.009 | [−0.301, +0.262] | 0.103 | EXCLUDED | not refuted |

The halves and the secondary panel are registered for sign only. The lag run shifts the whole
window one session later, so it holds the first four sessions of the month and drops the month's
last session.

## Causality

`tests/test_tom.py::TestCausality` cuts the calendar at six mid-month sessions. Every label before
each cut is unchanged, and a positive control on the same channel asserts that the truncated
calendar mislabels the cut session itself as a month end, 6 of 6. A third test perturbs every
return and asserts the positions do not move, with a control that the perturbation reached the
frame. The rule's signature takes dates only.

## What this licenses and what it does not

- Licensed: TOM is not a sleeve on free data at 2 bps; a hedged Sharpe of 0.7 is outside the CI.
- Licensed: on 1988→ the market's excess return is higher inside the window than outside, with a
  CI clear of zero.
- Not licensed: "TOM is dead". The 2006→ and second-half nulls are sign-only readings with CIs
  that still reach +0.26 and +0.28.
- Not licensed: any window variant. Each would be a fifth trial, and the spec ran none.
- Reopening needs an explicit user go and a stated lever. More history does not help, since
  the panel already starts after the original papers and the recent half is the null one.

Reproduce: `make wifey-tom-audit ARGS="--precheck --french-zip <zip>"`, then the same without
`--precheck`. Without `--french-zip` the tool downloads the current file, whose CRSP build may
differ from this one.
