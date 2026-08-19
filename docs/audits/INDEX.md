# Audit index

**Generated — do not edit by hand.** Regenerate with `make docs-index`;
`make docs-index-check` (and `tests/test_docs_index.py`) fails when this file
drifts from the corpus.

**29 audits.** A verdict line is shown only where one could be read
out of a Verdict heading as prose — that is **19 of 29**.
An em dash means the doc states its verdict in a table, a blockquote or the body,
**not** that it lacks one; open the file. Nothing here is keyword-guessed.

| Date | Audit | Verdict (as written) | File |
| --- | --- | --- | --- |
| 2026-08-19 | Regime gate: a pooled banner that inverted its own table | EXCLUDED — flipping `mode` to `hard` is ruled out on current evidence. One suppressed cell is a reliable winner at Holm-adjusted… | [2026-08-19-regime-gate-pooled-banner.md](2026-08-19-regime-gate-pooled-banner.md) |
| 2026-08-19 | The correction chain, and assurance that cannot see its own class | BOUNDED. Across one cross-repo review, at least eight filed claims moved under checking, and four of them were filed while… | [2026-08-19-correction-chain-and-silent-assurance.md](2026-08-19-correction-chain-and-silent-assurance.md) |
| 2026-08-18 | H-004 — risk-off sector rotation, G3 power price | BLOCKED — unreachable on power, and priced out BEFORE any sector return was read. The claim's own falsifiable form is a NULL… | [2026-08-18-h004-sector-rotation-power.md](2026-08-18-h004-sector-rotation-power.md) |
| 2026-08-14 | MFE Timing Within the Hold Window — Exit Sub-project B, Step 1 | the +1R lock level is timing-safe on equities, and the per-tf time-stop floor is `4h` 4 bars / `1d` 3 bars — a quarter of… | [2026-08-14-mfe-timing.md](2026-08-14-mfe-timing.md) |
| 2026-08-14 | Exit-Policy Replay A/B v1 | BOUNDED — exit management is worth at most +0.368R per trade on this book, and that ceiling buys a book whose own mean R still… | [2026-08-14-exit-policy-ab-v1.md](2026-08-14-exit-policy-ab-v1.md) |
| 2026-08-14 | Edge-hunt #6 — velocity-alternation sleeve | EXCLUDED — and unlike edge-hunt #5 this is a NULL, not a refutation. The committed `broad_ls` cell fails all four gate legs at… | [2026-08-14-edge-hunt-6-velocity-alternation.md](2026-08-14-edge-hunt-6-velocity-alternation.md) |
| 2026-08-14 | Edge-hunt #5 — gap-fill "magnet" sleeve | EXCLUDED. The gap-magnet direction is not merely unsupported, it is REFUTED — cost-free, fading toward an unfilled gap edge… | [2026-08-14-edge-hunt-5-gapfill-magnet.md](2026-08-14-edge-hunt-5-gapfill-magnet.md) |
| 2026-08-13 | Warning-value audit — do the W1–W8 alert warnings predict avg_r? | INSUFFICIENT on 11 of 12 cells — the audit could not rule anything out; one SUPPRESS-CANDIDATE — `w5_wick_rejection` / long… | [2026-08-13-warning-value-audit.md](2026-08-13-warning-value-audit.md) |
| 2026-08-13 | Vacuous causality guards in the sleeve books | Two of three causality guards were vacuous — they passed with the causal shift they guard deleted. Fixed, and each is now… | [2026-08-13-vacuous-causality-guards.md](2026-08-13-vacuous-causality-guards.md) |
| 2026-08-13 | HTF-EMA Population Parity — Measured and Declined as Net Harmful | the population mismatch is REAL and large — 40.1% of the EV gate's evidence is trades the live F8 HTF-EMA gate would never have… | [2026-08-13-htf-ema-population-parity.md](2026-08-13-htf-ema-population-parity.md) |
| 2026-08-13 | Conflict-Resolver Gate Ordering — Measured and Declined | the briefed defect (EV gate ordered before the volume/bias gates) does not exist — reordering the EV gate is provably… | [2026-08-13-conflict-resolver-gate-ordering.md](2026-08-13-conflict-resolver-gate-ordering.md) |
| 2026-08-12 | Exit MFE/MAE Diagnostic — Re-run at n=264 | the 2026-06-20 INCONCLUSIVE call is SUPERSEDED at the cohort level, and it resolved in the opposite direction to the thin read… | [2026-08-12-exit-mfe-mae-diagnostic-rerun.md](2026-08-12-exit-mfe-mae-diagnostic-rerun.md) |
| 2026-08-07 | Enabling live-parity on the ratings sweep — and why the conflict resolver must stay off | — | [2026-08-07-live-parity-ratings-sweep.md](2026-08-07-live-parity-ratings-sweep.md) |
| 2026-08-07 | The EV gate blocked on point estimates: half its suppressions were noise | — | [2026-08-07-ev-gate-significance-test.md](2026-08-07-ev-gate-significance-test.md) |
| 2026-08-07 | The live EV gate's sample-size guard counted both directions and tested one | — | [2026-08-07-ev-gate-directional-sample-guard.md](2026-08-07-ev-gate-directional-sample-guard.md) |
| 2026-08-07 | `backtest_runs` has four writers and one key: the live gate was deleting the sweep's rows | — | [2026-08-07-backtest-runs-writer-collision.md](2026-08-07-backtest-runs-writer-collision.md) |
| 2026-08-06 | Confidence ratings: rated cells the config never scans, and live rows superseding sweep rows | — | [2026-08-06-orphaned-and-contaminated-confidence-ratings.md](2026-08-06-orphaned-and-contaminated-confidence-ratings.md) |
| 2026-08-06 | The live EV gate ran a 90-day window and abstained on 71% of the surface | — | [2026-08-06-live-ev-gate-window.md](2026-08-06-live-ev-gate-window.md) |
| 2026-08-06 | `bos` was never decaying, and its direction flag was never equity-derived | — | [2026-08-06-bos-timeframe-and-crypto-era-direction-flag.md](2026-08-06-bos-timeframe-and-crypto-era-direction-flag.md) |
| 2026-08-06 | ADR gate × `volume_suppress`: two filters that cancel each other out | CONFIRMED config defect, not a detector defect. Four strategies across both live configs were declared-but-voided. Fixed here;… | [2026-08-06-adr-volume-gate-conjunction.md](2026-08-06-adr-volume-gate-conjunction.md) |
| 2026-08-06 | The ADR gate is undefined on every timeframe this bot scans | — | [2026-08-06-adr-gate-timeframe-degeneracy.md](2026-08-06-adr-gate-timeframe-degeneracy.md) |
| 2026-06-24 | Honest-Exit Synthesis — the free-data equity edge-hunt arc is concluded | — | [2026-06-24-honest-exit-free-data-edge-arc.md](2026-06-24-honest-exit-free-data-edge-arc.md) |
| 2026-06-23 | Edge-Hunt #4 — PEAD-lite (post-earnings drift): gate | FAIL. The pre-committed dollar-neutral `broad_ls` cell misses the gate on every axis except PBO: net-of-cost Sharpe +0.10 (≪… | [2026-06-23-edge-hunt-4-pead-lite.md](2026-06-23-edge-hunt-4-pead-lite.md) |
| 2026-06-23 | Edge-Hunt #3 — Cross-Asset TSMOM: gate | FAIL (pre-registered gate, committed `broad × long-short` cell) | [2026-06-23-edge-hunt-3-cross-asset-tsmom.md](2026-06-23-edge-hunt-3-cross-asset-tsmom.md) |
| 2026-06-22 | Edge-Hunt #2 — Low-Beta / BAB Long Sleeve: G-gate | FAIL (pre-registered gate, committed `beta × beta-neutral L/S` cell) | [2026-06-22-edge-hunt-2-lowvol-bab.md](2026-06-22-edge-hunt-2-lowvol-bab.md) |
| 2026-06-21 | Experiment #1 — Residualized XS-Momentum: Verdict (FAIL) | — | [2026-06-21-experiment-1-residual-xsmom.md](2026-06-21-experiment-1-residual-xsmom.md) |
| 2026-06-20 | Exit MFE/MAE Diagnostic — Equity | INCONCLUSIVE — instrument shipped and working, but the live equity ledger is too young to deliver a statistical… | [2026-06-20-exit-mfe-mae-diagnostic-equity.md](2026-06-20-exit-mfe-mae-diagnostic-equity.md) |
| 2026-06-18 | P3 — XS-Momentum Sleeve: Equity G3 | FAIL on the breadth universe. The dollar-neutral long-short cross-sectional-momentum sleeve does not clear gate G3 on ~100 US… | [2026-06-18-p3-xsmom-g3-equity.md](2026-06-18-p3-xsmom-g3-equity.md) |
| 2026-06-17 | P2 — EWMAC Trend Sleeve: Equity G2 | FAIL on the breadth universe. The continuous multi-speed EWMAC trend sleeve does not clear gate G2 on ~100 US large-caps.… | [2026-06-17-p2-forecast-trend-g2-equity.md](2026-06-17-p2-forecast-trend-g2-equity.md) |
