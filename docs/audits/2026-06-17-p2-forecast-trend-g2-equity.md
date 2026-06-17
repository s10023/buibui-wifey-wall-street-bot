# P2 — EWMAC Trend Sleeve: Equity G2 Verdict (2026-06-17)

**Verdict: FAIL on the breadth universe.** The continuous multi-speed EWMAC
trend sleeve does **not** clear gate G2 on ~100 US large-caps. Portfolio Sharpe
is flat-to-negative (−0.05 @2bps) and stays negative even at zero cost, so this
is a *signal* failure, not a cost failure. This is the equity answer — distinct
from the parent's crypto result (+0.36 Sharpe on 25 perps) — and a valid,
publishable outcome per the campaign spec's honesty rule.

## How this was produced

- Engine: `analytics/forecast/` (this PR), an additive read-only port of the
  parent's EWMAC trend sleeve, adapted crypto→equity (252-session
  annualization, funding = 0, breadth universe).
- Command: `make wifey-forecast-audit` against the live `analytics.db`.
- Population: the N3 research breadth universe — 101 active single-name stocks
  (ETFs excluded from the book), 1d bars, 2124 sessions (≈ 2018-01 → 2026-06).
- Costs: 1 bp fee + a swept slippage; funding leg is identically zero (equities
  have none). Short-borrow on the short legs is deferred to v2 (spec D2).

## Gate G2 — breadth contrast (@2 bps)

| population     | n_inst | days | sharpe | sortino | max_dd | ann_ret | ann_vol |   dsr |   pbo | boot_lo | boot_hi |
| -------------- | ------ | ---- | ------ | ------- | ------ | ------- | ------- | ----- | ----- | ------- | ------- |
| universe       |    101 | 2124 | −0.053 |  −0.047 | −0.287 |  −0.012 |  +0.113 | 0.183 | 0.132 |  −0.640 |  +0.541 |
| majors (6)     |      6 | 2124 | +0.697 |  +0.664 | −0.228 |  +0.129 |  +0.204 | 0.938 | 0.671 |  +0.054 |  +1.330 |

The 6-megacap "majors" book (AAPL/MSFT/NVDA/AMZN/GOOGL/META) shows a +0.70
Sharpe — but it is 6 hand-picked survivors that trended hard over the window,
its PBO is 0.67 (overfit-flagged, > 0.5) and its DSR 0.94 sits just under the
0.95 bar. The breadth universe is the honest test, and it is flat-to-negative.

## Cost sensitivity (universe)

| slippage | sharpe | ann_ret |   dsr |   pbo |
| -------- | ------ | ------- | ----- | ----- |
| 0 bps    | −0.015 |  −0.008 | 0.224 | 0.152 |
| 2 bps    | −0.053 |  −0.012 | 0.183 | 0.132 |
| 8 bps    | −0.165 |  −0.025 | 0.090 | 0.084 |
| 16 bps   | −0.315 |  −0.041 | 0.040 | 0.040 |

Even at **zero cost** the universe Sharpe is −0.015. Trading costs make a weak
signal worse, but they are not what sinks it — the raw absolute-trend signal
carries no edge across the broad large-cap cross-section.

## Per-speed Sharpe (H2 cycle-bias check)

| speed    | sharpe |
| -------- | ------ |
| s8_32    | −0.268 |
| s16_64   | −0.181 |
| s32_128  | +0.050 |
| s64_256  | +0.289 |
| combined | −0.053 |

The classic H2 signature: the **fast** legs bleed (s8_32 −0.27) while the
**slow** legs are mildly positive (s64_256 +0.29). The equal-weight combine
averages to ≈ 0. Whatever weak persistence exists in US large-caps lives at the
slow (multi-month) horizon, not the fast.

## Why (interpretation)

Single-name *absolute* time-series trend is a much weaker, more-arbitraged
signal in US large-cap equities than in crypto perps. Large-cap daily dynamics
are dominated by broad-market beta and mean-reversion rather than persistent
idiosyncratic trend, so a vol-targeted long/short EWMAC book on the cross-section
nets to noise. The slow-leg positivity is consistent with a small momentum
premium that is (a) concentrated in the slow horizon and (b) too thin to survive
deflation across the speed family.

## Read for PR 2 (cross-sectional momentum)

- The trend sleeve is **not** a positive, cost-robust core on equities, so it
  cannot serve as the portfolio anchor the way it did on crypto.
- `corr_to_trend` is near-meaningless as a bar to beat here: the combined trend
  series is ≈ 0 Sharpe, so XS-momentum returns will be ~uncorrelated to a
  zero-edge series by construction. **The XS sleeve must stand on its own G3
  number, not on a correlation/diversification argument against trend.**
- The slow-leg positivity (s64_256 +0.29) is a faint signal that *relative*
  strength — what cross-sectional momentum captures via demeaning — may extract
  more than absolute trend does. That keeps PR 2 worth running, but the prior
  should be sober: a negative equity G3 is a real possible outcome.

## Caveats

- Funding = 0 and short-borrow deferred (spec D2): the short legs are costless
  here beyond the swept slippage, so the real-world net would be *worse*, not
  better — the FAIL is robust to that omission.
- Survivorship: the universe tracks current S&P-100 membership (PIT membership
  not scraped). Bias is bounded, not eliminated; it would, if anything, flatter
  the result, so it does not rescue the verdict.
