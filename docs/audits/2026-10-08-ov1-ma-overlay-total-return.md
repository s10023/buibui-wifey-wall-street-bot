# OV-1 — the 200-session MA filter passes as a risk overlay on a total-return frame

**Date:** 2026-10-08
**Audit:** this file. Owner: Issue #418. Pre-registration: `docs/superpowers/specs/2026-10-08-edge-pillars-research-design.md` § Phase 2, frozen 2026-10-08, unchanged.

## Verdict

FOUND as an overlay, on the overlay yardstick and nothing else. Judged against buy-and-hold on
the Ken French total-return market with T-bill cash, 1929-01-02 → 2026-08-31, all three
pre-registered legs pass at 2 bps a switch: ΔUI −14.18 with a stationary-bootstrap 95% CI of
[−29.03, −3.23], an ulcer ratio of 0.382 against the 0.75 floor, and ΔSR +0.250 with a CI of
[+0.072, +0.447] against the −0.10 non-inferiority margin. The Sharpe CI clears zero as well,
so on this panel the overlay is superior, not just non-inferior. The verdict survives 5 bps and
a one-session execution lag. It is not an edge claim and does not make G1 live: an overlay never
claims an edge (`docs/north-star.md` § Two yardsticks), and the overlay's positive alpha below is
reported, not the yardstick. The result is front-loaded. The first half of the panel carries most of
it (ΔSR +0.402 to 1975, +0.097 after), and the SPY total-return cross-check from 1993 shows a
drawdown cut (ulcer ratio 0.590) at roughly equal Sharpe (+0.039, CI [−0.210, +0.277]), and is
not significant on either leg. Read OV-1 as "on a century of data the
filter roughly halves drawdown pain without costing Sharpe"; do not read it as "the filter adds
a quarter of a Sharpe going forward". The test was not blind: H1 saw the direction on the biased
frame, and the frame fix was the new information.

## Panel and frame

- French file: `F-F_Research_Data_Factors_daily_CSV.zip`, built from the CRSP 202608 database,
  sha256 prefix `2f29e22546069914`, last row 2026-08-31.
- `^GSPC` `1d` from `analytics.db`: 1927-12-30 → 2026-09-18, 24,796 closes.
- Panel: **1929-01-02 → 2026-08-31**, 25,571 sessions, 97.7 calendar years. Both dates were
  printed by the precheck before any return was computed.
- The return calendar is the French file's. It carries 1,039 in-panel Saturday sessions (NYSE
  traded Saturdays until 1952) that `^GSPC` does not, so the signal is mapped onto that calendar,
  never inner-joined: a session's position is the `^GSPC` signal at the latest close strictly
  before it. A Monday after a Saturday session therefore acts on Friday's close, which is stale by
  one session and still causal.
- Alignment was checked, not assumed. The correlation of the French market return with the
  same-dated `^GSPC` price return is +0.923 (1929–51), +0.969 (1953–75) and +0.991 (1976–2026),
  against at most +0.225 at a one-day shift either way (+0.9869 same-day over 1953–2026). The
  1953–75 shoulders are symmetric, which is CRSP's stale-price autocorrelation, not a mislabel.
  The tool prints this table in its header, `--precheck` included.
- Sharpe is on returns in excess of French `RF`, annualised by √252. The Saturday sessions make
  the panel average ~262 sessions a year, which would move a paired ΔSR by about 2%; annual
  return and switches per year use calendar years for the same reason.
- `RF` is printed to 1 bp a day, so the cash leg is quantised.

## Precision, printed before any point estimate

| Leg | Bootstrap 95% half-width |
| --- | --- |
| ΔUI (ulcer points) | 12.898 |
| ΔSR (annualised) | 0.1875 |

The ΔSR half-width matches the spec's analytic power line (0.1987), so the design was as
precise as priced.

## Primary gate at 2 bps

| Leg | Statistic | Point | 95% CI | Bar | Pass |
| --- | --- | --- | --- | --- | --- |
| 1 | ΔUI = UI(ov) − UI(bh) | −14.178 | [−29.029, −3.233] | CI hi < 0 | yes |
| 2 | UI(ov) / UI(bh) | 0.382 | — | ≤ 0.75 | yes |
| 3 | ΔSR, net | +0.250 | [+0.072, +0.447] | CI lo > −0.10 | yes |

Stationary bootstrap, mean block 252 sessions, 5,000 resamples, seed 20261008, both legs on the
same resamples.

## Reported, not gated

| Arm | bps | CAGR | Vol | Sharpe | Ulcer | Max DD | Longest under water | In market | Switches/yr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bh` | 2 | 9.77% | 0.173 | 0.431 | 22.96 | −84.1% | 18.3 y | 100% | 0 |
| `ov` | 2 | 10.80% | 0.109 | 0.681 | 8.78 | −44.6% | 5.1 y | 67.4% | 6.02 |
| `ov` | 5 | 10.60% | 0.109 | 0.665 | 8.99 | −44.9% | 5.1 y | 67.4% | 6.02 |

The overlay's higher CAGR is mostly volatility drag, not a higher arithmetic return: at 0.17
volatility buy-and-hold loses about 1.5pp a year to compounding against the overlay's ~0.6pp.

P(a drawdown of at least D within 5 years), 5,000 paired bootstrap paths of 1,260 sessions:

| Arm | 15% | 20% | 25% | 30% | 40% |
| --- | --- | --- | --- | --- | --- |
| `bh` | 91.7% | 79.8% | 63.6% | 47.1% | 25.1% |
| `ov` | 44.4% | 18.8% | 9.4% | 6.9% | 3.5% |

The pre-registration expected realised beta and beta-hedged alpha to be ≤ 0. Beta is +0.402
and the alpha is **+4.46% a year (iid OLS t +5.30)**, which is the opposite sign. Do not quote
that t: it ignores volatility clustering, the very structure the filter exploits, and under #336
alpha is not the overlay yardstick. Quote it only as "the overlay is not a de-levered market in
disguise", never as an edge.

## Sensitivity

| Run | ΔUI [95% CI] | UI ratio | ΔSR [95% CI] | Gate reading |
| --- | --- | --- | --- | --- |
| 5 bps | −13.97 [−28.78, −3.00] | 0.392 | +0.234 [+0.056, +0.430] | FOUND |
| one-session execution lag | −13.49 [−28.59, −2.62] | 0.412 | +0.211 [+0.028, +0.404] | FOUND |
| first half, 1929-01-02 → 1975-12-09 | −20.03 | — | +0.402 | sign only |
| second half, 1975-12-10 → 2026-08-31 | −5.17 | — | +0.097 | sign only |
| SPY total return, 1993-02-01 → 2026-08-31 (8,453 sessions) | −5.92 [−18.92, +2.48] | 0.590 | +0.039 [−0.210, +0.277] | leg-1 CI straddles 0 |

The SPY run uses yfinance's dividend-adjusted SPY close and French `RF`, with the signal still on
`^GSPC`. Its ΔSR half-width (0.244) is narrower than the spec priced (0.338), but it still cannot
overturn the primary, as pre-registered. On the verdict function it returns `UNREGISTERED`, the
case the pre-registration names no verdict for. Here it is a sensitivity reading and needs no
ruling.

## Causality

`tests/test_overlay_rules.py::TestCausality` truncates a synthetic close series at six cuts and
requires every position the truncated data can determine to equal the full-series answer: 0 of 6
disagree for the OV-1 rule at lag 1 and at lag 2. Two positive controls observe the same channel:
a rule reading tomorrow's close is flagged on 6 of 6 cuts, and the same-session `lag=0` mapping on
3 of 6 (it differs only where the day's signal flips).

## What this licenses and what it does not

- **Licenses:** #419, the two-track north-star decision, which waited on this verdict. On this
  evidence a survival-managed core is viable: market exposure with the 200-session filter, judged
  on drawdown.
- **Does not license:** any edge, G1 or sizing claim. The panel is one index, so `k=1` and no
  `n_eff` deflator applies, and the window was chosen in H1 from a flat profile (PBO 0.877 over
  the MA-window grid), so `ma200d` is the canonical window, not a tuned one.
- **Expectation for live use:** the post-1993 evidence is a drawdown cut at about equal Sharpe.
  The 1929–32 and 2000s bear markets supply most of the century's advantage, and V-shaped crashes
  (2020) are the known failure case.
- **Not modelled:** taxes. Six switches a year realise short-term gains in a taxable account;
  an IRA does not care.
- **Frame limit, bounded:** the signal is the S&P 500 price index and the returns are the CRSP
  value-weighted total market (correlation 0.987 on shared sessions since 1953). The SPY run puts
  both on one investable instrument from 1993.

Reproduce: `make wifey-overlay-audit ARGS=--precheck`, then `make wifey-overlay-audit`, about ten
minutes. This run read the zip above through `--french-zip`; without it the tool downloads the
current file, whose newer CRSP build moves the end date.
