# P3 — XS-Momentum Sleeve: Equity G3 Verdict (2026-06-18)

**Verdict: FAIL on the breadth universe.** The dollar-neutral long-short
cross-sectional-momentum sleeve does **not** clear gate G3 on ~100 US large-caps.
Combined portfolio Sharpe is **−0.156 @2 bps** and stays negative even at zero
cost (−0.097), so this is a *signal* failure, not a cost failure. The
diversification escape hatch is also closed: `corr_to_trend` is **+0.62** (not
"uncorrelated"), there is no significant alpha, and the edge does not persist
across calendar years. This is the equity answer — distinct from the parent's
crypto result (**G3 CLEARED** on 25 perps) — and a valid, publishable outcome per
the campaign spec's honesty rule.

## How this was produced

- Engine: `analytics/xsmom/` (this PR), an additive read-only port of the
  parent's cross-sectional-momentum sleeve (PRs #444 + #445), adapted
  crypto→equity (252-session annualization, funding = 0, breadth `.stocks()`
  cross-section with ETFs excluded from the demean, SPY beta proxy).
- Command: `make wifey-xsmom-audit` against the live `analytics.db`.
- Population: the N3 research breadth universe — 101 active single-name stocks
  (ETFs excluded from the book), 1d bars, 2124 sessions (≈ 2018-01 → 2026-06).
- Costs: 1 bp fee + a swept slippage; funding leg is identically zero. Short-borrow
  on the short legs is deferred to v2 (spec D2).

## Gate G3 — breadth contrast (@2 bps)

| population     | n_inst | days | sharpe | sortino | max_dd | ann_vol |   dsr |   pbo | boot_lo | boot_hi | corr_to_trend | trend_sharpe |
| -------------- | ------ | ---- | ------ | ------- | ------ | ------- | ----- | ----- | ------- | ------- | ------------- | ------------ |
| universe       |    101 | 2124 | −0.156 |  −0.141 | −1.000 |  +1.995 | 0.157 | 0.398 |  −0.718 |  +0.398 |        +0.620 |       −0.053 |
| majors (6)     |      6 | 2124 | −0.192 |  −0.187 | −0.517 |  +0.218 | 0.000 | 0.449 |  −0.861 |  +0.344 |        +0.344 |       +0.697 |

Both books are negative. Unlike the trend sleeve — where the 6-megacap "majors"
book printed +0.70 — XS-momentum on the same survivor cluster is **−0.19**:
demeaning a tiny hand-picked cross-section just nets the survivors against each
other. The breadth universe is the honest test, and it is negative with
`boot_lo` −0.72 (the CI spans zero on the wrong side), DSR 0.16 (≪ 0.95), and
`corr_to_trend` +0.62 (the book co-moves with the zero-edge trend series rather
than diversifying it).

## Dollar-neutral gate (original vs re-centered)

| book              | sharpe |   dsr |   pbo | corr_to_trend |
| ----------------- | ------ | ----- | ----- | ------------- |
| original          | −0.156 | 0.157 | 0.398 |        +0.620 |
| dollar-neutral    | −0.148 | 0.231 | 0.685 |        +0.581 |

Forcing each day's active leverage to net to zero (`xs_dollar_neutral=True`)
barely moves the headline (−0.148 vs −0.156) and lifts PBO to 0.69. The residual
net exposure was not what was driving (or sinking) the book — the relative-
strength signal itself carries no edge.

## Beta attribution (universe @2 bps)

| book           | proxy     | alpha_ann |   beta | alpha_t | hedged_sharpe |    r2 |
| -------------- | --------- | --------- | ------ | ------- | ------------- | ----- |
| original       | eq-wt-mkt |    −0.168 | −0.850 |  −0.245 |        −0.085 | 0.007 |
| original       | SPY       |    −0.295 | −0.114 |  −0.428 |        −0.148 | 0.000 |
| dollar-neutral | eq-wt-mkt |    +0.120 | −2.593 |  +0.168 |        +0.058 | 0.055 |
| dollar-neutral | SPY       |    −0.051 | −1.874 |  −0.070 |        −0.024 | 0.029 |

There is **no alpha**. The best cell (dollar-neutral vs the equal-weight market)
shows +0.12 annual alpha but with `alpha_t` +0.17 (nowhere near significance) and
a beta-hedged Sharpe of +0.06 (≈ zero). Against SPY the alpha is negative. R² is
near zero throughout — the book is neither a market-beta play nor an alpha play;
it is noise.

## Forward persistence (dollar-neutral, universe @2 bps)

| period      | sharpe |
| ----------- | ------ |
| 2018        | −0.181 |
| 2019        | −1.600 |
| 2020        | +0.838 |
| 2021        | −0.371 |
| 2022        | −0.007 |
| 2023        | −0.847 |
| 2024        | +0.218 |
| 2025        | −0.188 |
| 2026        | +0.820 |
| trailing_2y | −0.041 |
| trailing_1y | +0.721 |

No stable persistence: the per-year Sharpe swings from −1.60 (2019) to +0.84
(2020), the trailing-2y window is −0.04, and the only encouraging figure
(trailing-1y +0.72) is a single recent year on the heels of a −0.85 (2023) and
−0.19 (2025). This is the shape of an unstable, regime-dependent signal, not a
durable edge.

## Cost sensitivity (universe)

| slippage | sharpe | ann_ret |   dsr |   pbo |
| -------- | ------ | ------- | ----- | ----- |
| 0 bps    | −0.097 |  −0.921 | 0.000 | 0.458 |
| 2 bps    | −0.156 |  −0.930 | 0.157 | 0.398 |
| 8 bps    | −0.333 |  −0.951 | 0.000 | 0.218 |
| 16 bps   | −0.568 |  −0.970 | 0.000 | 0.070 |

Even at **zero cost** the universe Sharpe is −0.097. Costs make a weak signal
worse, but they are not what sinks it — the demeaned relative-strength signal
carries no edge across the broad large-cap cross-section.

## Per-speed XS Sharpe (H2 cycle-bias check)

| speed    | sharpe |
| -------- | ------ |
| s8_32    | −0.342 |
| s16_64   | −0.240 |
| s32_128  | −0.086 |
| s64_256  | +0.080 |
| combined | −0.156 |

The same H2 signature as the trend sleeve, only weaker: the **fast** legs bleed
(s8_32 −0.34) while the **slow** leg is barely positive (s64_256 +0.08 — vs the
trend sleeve's +0.29). Whatever faint relative-strength persistence exists in US
large-caps lives at the slow horizon and is too thin to survive the demean +
deflation.

## Why (interpretation)

Cross-sectional momentum on a broad US large-cap universe — long the relatively
strong, short the relatively weak — does not extract a positive risk-adjusted
return over 2018–2026. Large-cap relative strength is heavily arbitraged; the
demeaning removes the broad-market drift but leaves a residual that is dominated
by short-horizon reversal (the fast legs are the worst), and the slow-horizon
remnant (+0.08) is too small to clear deflation. Critically, the book co-moves
with the (already-zero-edge) trend series (`corr_to_trend` +0.62), so it offers
no diversification benefit to lean on — the combine-win escape hatch the spec
flagged for "modest-Sharpe but uncorrelated" sleeves is unavailable here.

## Caveats (none rescue the verdict)

- **Governor saturation at breadth.** Realized `ann_vol` is ~200% and `max_dd` is
  −100% (full wipeout): summing demeaned vol-parity legs across 101 names produces
  very high gross leverage, and the 20%-vol governor's `g_min=0.5` floor cannot
  scale it down to target (the parent ran ~25 names). A v2 could lower `g_min` or
  add an IDM, but Sharpe is scale-invariant — the negative Sharpe (and the
  zero-cost negativity) is unaffected by the absolute risk level, so the FAIL
  stands.
- **Funding = 0 and short-borrow deferred (spec D2):** the short legs are costless
  here beyond the swept slippage, so the real-world net would be *worse*, not
  better — the FAIL is robust to that omission.
- **Survivorship:** the universe tracks current S&P-100 membership (PIT membership
  not scraped). Bias is bounded, not eliminated; it would, if anything, flatter
  the result, so it does not rescue the verdict.

## Read for the campaign

- Both equity sleeves now have honest verdicts: **trend G2 ≈ 0 (FAIL)** and
  **XS-momentum G3 < 0 (FAIL)**. Neither single-name absolute trend nor
  cross-sectional relative strength is a positive, cost-robust, deflation-survivable
  edge on US large-caps over this window.
- The XS sleeve is **not** a combine candidate: it is negative *and* correlated
  (+0.62) to the trend series, so it neither stands alone nor diversifies.
- This closes the "port the crypto systematic stack and re-run on equities"
  question with a clean negative. Next research directions (events/earnings, a
  different cross-section, or a mean-reversion sleeve) are open questions for the
  master to-do, not extensions of this engine.
