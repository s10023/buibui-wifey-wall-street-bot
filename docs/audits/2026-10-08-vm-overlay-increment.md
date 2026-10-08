# VM — volatility management adds survival beside OV-1, on a thin ulcer-ratio margin

**Date:** 2026-10-08
**Audit:** this file. Owner: Issue #421. Pre-registration: `docs/superpowers/specs/2026-10-08-edge-pillars-research-design.md` § Amendment 1, frozen 2026-10-08 (commit `c72b2e1`) before any VM return was computed, unchanged.

## Verdict

FOUND as an increment over OV-1, on the overlay yardstick only. OV-1 × VM against OV-1 itself,
1929-01-02 → 2026-08-31 at 2 bps, passes all three pre-registered legs: ΔUI −2.44 with a CI of
[−7.07, −1.02], an ulcer ratio of 0.722 against the 0.75 floor, and ΔSR +0.082 with a CI of
[−0.001, +0.170] against the −0.10 margin. The survival gain is the robust part. Its CI clears
zero in every run, it is the same size in both halves of the panel (−2.49 and −2.44), and it holds
on SPY from 1993 (ratio 0.663), where OV-1's own cross-check did not reach significance. The
ulcer-ratio leg is the fragile part: 0.722 at the primary, 0.749 with the real-time target, and
0.762 with a one-session execution lag, which reads BOUNDED. The Sharpe gain is front-loaded
(+0.183 to 1975, +0.017 after) and not significant, so read it as non-inferior, not superior.
The spec falsifier (VM against buy-and-hold) is FOUND too. As a replacement for OV-1, VM alone
is worse on the point estimate and INSUFFICIENT on the CI. Adopting OV-1 × VM as the core is the
operator's call, and it costs about 1pp of CAGR. It is not an edge claim and does not make G1 live.

## Panel and frame

- French file: OV-1's own, `F-F_Research_Data_Factors_daily_CSV.zip` from the CRSP 202608 build,
  sha256 prefix `2f29e22546069914`. The panel is OV-1's exactly, **1929-01-02 → 2026-08-31,
  25,571 sessions**, and the tool refuses to run if it is not. OV-1 was re-run after this branch's
  refactor and reproduced its audit digit for digit (`--no-spy`, so every figure except the SPY
  row).
- `σ_target` = the median of the 20-session `σ̂` over the panel = **0.00709 a session (11.26%
  annualised)**. VM's weight averages 0.830 and sits below the cap on 50.0% of sessions, as a
  median target implies.
- Pre-1952 Saturday sessions have 0.696× the weekday return volatility (1,018 Saturdays). This is
  the bias Amendment 1 flagged: `σ̂` reads slightly low before 1952, so the weight there is
  slightly high.
- Sharpe is in excess of French `RF` and annualised by √252, and CAGR uses calendar years, all as
  in OV-1.

## Precision, printed before any point estimate

| Test | ΔUI half-width | ΔSR half-width |
| --- | --- | --- |
| Increment, `ovvm` − `ov` | 3.025 | 0.0851 |
| Falsifier, `vm` − `bh` | 7.727 | 0.0877 |

The increment test is more than twice as precise on Sharpe as OV-1's own (0.1875), because the
two arms share OV-1's position and differ only by the weight.

## Gates at 2 bps

| Test | ΔUI [95% CI] | UI ratio | ΔSR [95% CI] | Verdict |
| --- | --- | --- | --- | --- |
| **Increment, `ovvm` − `ov` (headline)** | −2.443 [−7.069, −1.019] | 0.722 | +0.082 [−0.001, +0.170] | **FOUND** |
| Falsifier, `vm` − `bh` | −7.518 [−18.251, −2.796] | 0.673 | +0.104 [+0.012, +0.187] | FOUND |
| Replacement, `vm` − `ov` (reported) | +6.660 [−1.455, +12.669] | 1.759 | −0.146 [−0.333, +0.020] | INSUFFICIENT |

Stationary bootstrap, mean block 252, 5,000 resamples, seed 20261008, both legs of a test on the
same resamples.

## Reported, not gated

| Arm | CAGR | Vol | Sharpe | Ulcer | Max DD | Longest under water | Mean exposure | Turnover/yr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bh` | 9.77% | 0.173 | 0.431 | 22.96 | −84.1% | 18.3 y | 100% | 0 |
| `ov` | 10.80% | 0.109 | 0.681 | 8.78 | −44.6% | 5.1 y | 67.4% | 6.02 |
| `vm` | 8.91% | 0.108 | 0.535 | 15.44 | −55.7% | 8.4 y | 83.0% | 3.32 |
| `ovvm` | 9.84% | 0.083 | 0.763 | 6.34 | −19.7% | 7.0 y | 60.1% | 6.50 |

At 5 bps: `ov` 10.60% / 0.665, `vm` 8.81% / 0.526, `ovvm` 9.62% / 0.740. Turnover is Σ|Δposition|
per calendar year, so OV-1's six full switches and VM's many small daily rebalances share a unit.

P(a drawdown of at least D within 5 years), 5,000 paired bootstrap paths of 1,260 sessions:

| Arm | 15% | 20% | 25% | 30% | 40% |
| --- | --- | --- | --- | --- | --- |
| `bh` | 91.7% | 79.8% | 63.6% | 47.1% | 25.1% |
| `ov` | 44.4% | 18.8% | 9.4% | 6.9% | 3.5% |
| `vm` | 75.5% | 45.1% | 26.9% | 15.1% | 3.5% |
| `ovvm` | 9.5% | 1.2% | 0.1% | 0.0% | 0.0% |

The north star's budget is P(DD ≥ 20% in 5 years) ≤ 20%. OV-1 meets it with 1.2pp to spare
(18.8%); OV-1 × VM meets it at 1.2%. These are resamples of one century's tape, so the tail
cells inherit its handful of crises: read the curve's shape, not a cell's last digit.

Where the drawdowns sit (worst peak-to-trough inside each window; read-only one-shot
`docs/plans/scripts/vm_dd_episodes.py <OV-1's zip>`, gitignored and covered by `make backup`):

| Window | `bh` | `ov` | `vm` | `ovvm` |
| --- | --- | --- | --- | --- |
| 1929-09 → 1932-12 | −84.1% | −40.8% | −55.7% | −15.7% |
| 1937 → 1938 | −51.0% | −13.7% | −33.9% | −10.1% |
| 1987-08 → 1987-12 | −33.1% | −10.2% | −18.7% | −7.5% |
| 2000-03 → 2002-12 | −49.2% | −21.0% | −36.0% | −10.3% |
| 2007-10 → 2009-06 | −54.6% | −11.1% | −25.7% | −6.4% |
| 2020-02 → 2020-06 | −34.2% | −18.8% | −12.8% | −10.9% |
| 2022 | −25.4% | −16.6% | −14.3% | −9.7% |

The mechanism is visible here. OV-1's worst stretch (−44.6%, 1932-09 → 1933-03) is a whipsaw: it
re-entered the violent 1932 rally, then sat through the 1933 banking crisis. At 1930s volatility
VM's weight is near 0.2, which cuts exactly that re-entry. In 2020, OV-1's named failure case, VM
alone beats OV-1 (−12.8% against −18.8%), and the product halves OV-1's loss. OV-1 × VM's worst
stretch is a slow 1937 → 1942 grind (−19.7%), which neither rule is built to catch.

Beta and beta-hedged alpha (OLS on the market's excess return, iid): `ov` +0.402 / +4.46% a
year, `vm` +0.556 / +1.65%, `ovvm` +0.285 / +4.24% (t +6.34). As in OV-1, do not quote the t,
which ignores the volatility clustering both rules exploit, and do not read `ovvm`'s Sharpe of
0.763 against `GATE_SHARPE`: an overlay is not judged on that bar.

## Sensitivity

| Run | Increment `ovvm` − `ov` | Falsifier `vm` − `bh` | Replacement `vm` − `ov` |
| --- | --- | --- | --- |
| 5 bps | ΔUI −2.41 [−7.20, −1.02], ratio 0.732, ΔSR +0.075 [−0.007, +0.163]: FOUND | FOUND (0.679) | INSUFFICIENT |
| one-session execution lag | ΔUI −2.26 [−7.14, −0.91], ratio **0.762**, ΔSR +0.073 [−0.008, +0.156]: **BOUNDED** | FOUND (0.687) | INSUFFICIENT |
| real-time `σ_target` (expanding median) | ΔUI −2.21 [−6.40, −0.77], ratio **0.749**, ΔSR +0.067 [−0.010, +0.142]: FOUND | FOUND (0.659) | INSUFFICIENT |
| first half, 1929-01-02 → 1975-12-09 | ΔUI −2.49, ΔSR +0.183 | ΔUI −9.88, ΔSR +0.131 | ΔUI +10.15, ΔSR −0.271 |
| second half, 1975-12-10 → 2026-08-31 | ΔUI −2.44, ΔSR +0.017 | ΔUI −3.91, ΔSR +0.071 | ΔUI +1.26, ΔSR −0.026 |
| SPY total return, 1993-03-02 → 2026-08-31 (8,433 sessions) | ΔUI −2.87 [−5.56, −1.04], ratio 0.663, ΔSR +0.018 [−0.057, +0.090]: FOUND | FOUND (0.690) | INSUFFICIENT |

Split halves are sign only. The SPY run uses SPY's own returns for `σ̂` and the primary
`σ_target`, with OV-1's signal still on `^GSPC`.

Three readings follow. First, the leg-1 survival gain is stable: its CI clears zero in every run
and its size barely moves across eras, unlike OV-1's front-loaded result. Second, leg 2 is a
margin, not a cushion: every full-panel run lands within 0.03 of the 0.75 floor, and the
execution lag lands on the wrong side of it. Third, the real-time target, which answers the Cederburg et
al. critique of in-sample vol targets, costs only 0.027 of ratio, so the in-sample `σ_target` is
not what carries the result.

## Causality

`tests/test_overlay_vm.py::TestCausality` adds +20% a session to every return after each of six
cuts and requires every weight held through the session after the cut to be unchanged: 0 of 6
disagree at lag 1, at lag 2 and with the real-time target. The positive control, the `lag=0`
weight that reads the session being earned, is flagged on 6 of 6. A first version of the shock
(×5 plus 1%) was swallowed by the weight's cap on 3 of 6 cuts, which is why the shock is large.

## What this licenses and what it does not

- **Licenses:** an operator decision on whether the survival core becomes OV-1 × VM. The case
  for it is a materially smaller drawdown (max −19.7% against −44.6%, P(DD ≥ 20% in 5 years) 1.2%
  against 18.8%) at non-inferior Sharpe. The case against is about 1pp a year of CAGR (9.84%
  against 10.80%), 60% mean exposure, and daily fractional rebalancing, which a live book would
  band. The pre-registration says a FOUND increment is still the operator's call.
- **Does not license:** any edge, G1 or sizing claim. The panel is one index (`k = 1`). The
  20-session window and the median target were fixed by the spec, not tuned, but the ulcer ratio's
  thin margin means a reasonable variant (the execution lag) reads BOUNDED, so do not describe
  the increment as robust on leg 2.
- **Does not license:** VM as a replacement for OV-1. On the point estimate VM alone has 1.76×
  OV-1's ulcer, and its CI is INSUFFICIENT.
- **Not modelled:** taxes, and a rebalancing band. Banding would cut VM's turnover, but it is a
  new parameter, and choosing one now would be fitted to this result.

Reproduce: `make wifey-vm-audit ARGS="--precheck --french-zip <OV-1's zip>"`, then the same
without `--precheck`. The full run takes about 45 minutes, so run it in the background. Without
`--french-zip` the tool downloads the current file, whose newer CRSP build moves the end date.
