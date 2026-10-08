# Edge pillars: what a survivable system needs, and where to look next

**Status: DESIGN, phase 1 (no data read). Issue #378. OV-1 pre-registration FROZEN 2026-10-08
on the operator's go; build is #418. VM pre-registration FROZEN 2026-10-08 as Amendment 1;
build is #421, which reported FOUND as an increment (`docs/audits/2026-10-08-vm-overlay-increment.md`).** Written without `analytics.db`; every
repo figure below is quoted from a tracked audit, and every power figure is a
`tools/distil_power.py` run reproduced inline. Literature claims carry their source and are kept
apart from what this repo measured.

## Verdict

The most likely durable, survivable return available to this operator on free data is the equity
premium itself, held under a risk overlay. No alpha signal is a candidate yet: nine sleeves and the
TA book are non-positive, and risk management cannot turn a non-positive signal positive. The
first experiment is **OV-1**, which re-judges the already-measured 200-day MA filter as an
overlay against buy-and-hold rather than as a sleeve. It runs on a total-return-plus-T-bill frame
that removes the two biases the H1 audit declared fatal to its buy-and-hold column, and it is
gated on the ulcer index with Sharpe non-inferiority. Turn-of-the-month exposure is the best
free *signal* candidate and ranks second. This doc is the shortlist; each candidate still needs the
operator's explicit go.

## Correction to the brief: H1 has already been measured

Issue #378 lists the "H1 hindsight study, 200-day / 40-week MA regime filter" as a live lead. It
was measured on 2026-08-20 (`docs/audits/2026-08-20-h1-ma-regime-filter.md`) and closed **as a
sleeve**: `ma200d` Sharpe 0.593 and `ma40w` 0.604 against buy-and-hold 0.422, both under
`GATE_SHARPE = 0.7`, with PBO 0.877 over the MA-window grid. It is 84.4% the same long/flat state
as 12-month TSMOM.

What that audit did **not** settle is the question the #336 ruling now asks: judged as a risk
overlay against buy-and-hold, on drawdown and net Sharpe, does it pass? The audit refused to make
that comparison, because `^GSPC` is price-only (which favours timing) and flat earned 0% (which
disfavours timing), and "their net sign is not knowable from this data". OV-1 fixes both
frame limits and changes nothing else. It is not a blind test: the direction (lower drawdown,
slightly higher Sharpe, lower return) was seen on the biased frame, and the frame fix is the new
information.

## Part 1 — Pillars

Status key: **solid** (fit for purpose), **partial** (exists but with a named gap), **missing**.
"Good" is stated for a retail, free-data-first, long-biased US-equities operator.

| Pillar | What good looks like here | What the repo has | Status |
| --- | --- | --- | --- |
| Signal / alpha source | One source with positive net expectancy on an out-of-sample panel, a mechanism that explains why it persists, and its information not already in price | 16 detectors (`analytics/strategies/_registry.py`), nine sleeves (`analytics/{forecast,xsmom,lowvol,xasset,pead,gapfill,velocity,insider}/`), `analytics/regime.py`. All non-positive; live ledger −0.2553R, n=292 | **partial**: the machinery is solid, and no source clears its gate |
| Risk management | Per-trade stop; portfolio sizing; a vol target; portfolio heat cap; a drawdown governor and kill-switch; a survival metric that is computed and gated | Per-trade stops only on the live path (`analytics/signal/resolvers.py`, `analytics/signal/atr_floor.py`). Vol governors exist only inside research sleeves (`analytics/forecast/book.py`, `vol_target_annual = 0.20` in `analytics/forecast/config.py`). `max_drawdown` in `analytics/forecast/metrics.py` and `max_drawdown_r` in `analytics/backtest/engine.py`. No sizing, heat, governor, kill-switch, ulcer index, time under water or drawdown probability anywhere | **missing** at book level; partial per trade |
| Costs and execution | Costs priced the same way in backtest and live; gap fills on both tails; an order layer only after G3 | `analytics/backtest/cost_model.py` (ADV-bucketed half-spread, sqrt impact, borrow); `analytics/backtest/fills.py` (symmetric gap fills); `live_cost_r` in `analytics/signal/outcome_backfill.py` (ledger net of cost). `trade/` is two 0-byte files | **partial**: costs solid, execution absent by design (Phase B gated) |
| Validation and statistics | Pre-registration; DSR, PBO and bootstrap lower bound; MinTRL; multiplicity control; clustering and `n_eff`; causality tests; power priced before building | `analytics/research_guards/` (DSR, PSR, PBO, MinTRL, BH/Holm haircut, stationary bootstrap, cluster bootstrap, `n_eff`); `analytics/audit_guard.py` (`powered_null`); `tools/distil_power.py`; `tests/test_lookahead.py` | **solid**. One gap: no overlay-class yardstick (#336) |
| Data | Long, clean, adjusted daily history; total return and a cash rate; point-in-time membership where cross-sections are pooled | yfinance OHLCV (`utils/yfinance_client.py`, `auto_adjust=False`, dividends stripped); EDGAR (`utils/edgar_client.py`, `analytics/insider/form4.py`); NYSE calendar (`analytics/trading_calendar.py`); quality and freshness (`analytics/data_quality.py`, `tools/freshness_check.py`). No total-return series, no cash rate, no Ken French or FRED client, no PIT membership | **partial** |
| Portfolio construction | A combiner across sleeves with a vol target and per-sleeve attribution; beta reported beside every long-only number | `beta_attribution` (`analytics/xsmom/diagnostics.py`) and the realized-beta guardrail in sleeve reports. No `analytics/portfolio/`, no multi-sleeve combiner | **missing**, correctly so: G1 says do not port it until a sleeve has an edge |
| Ops and monitoring | Scheduled runs whose silence is visible; a live ledger; live-versus-paper tracking; decay detection | `make go-live`, the outcome ledger (`analytics/stats/live_outcomes.py`), `tools/session_digest.py`, `tools/freshness_check.py`, backups and `deploy/` jobs. No decay monitor, no paper book to track against | **partial**: liveness solid, nothing to monitor for decay |

The binding gap is not the one the brief's draft order suggests. Validation is the strongest pillar
and signal the weakest, but **risk management has no survival metric at all**. The repo cannot
currently say how likely a −25% drawdown is for any book, so a survival-first objective could not
be gated today even if the operator adopted one. OV-1 builds that piece.

## Part 2 — Survival framing

### What risk management can and cannot do

The continuous-time result makes the brief's statement exact. Hold a fraction `f` of wealth in an
asset with excess drift `μ` and volatility `σ`. Log wealth then drifts at `g = fμ − f²σ²/2` with
volatility `fσ`, and the probability that wealth **ever** falls to a fraction `x` of where it
stands now is

```text
P(ever reach x) = x ^ (2μ/(fσ²) − 1)      when g > 0
                = 1                         when g ≤ 0
```

This is the Brownian first-passage probability, as used by Thorp (2006), *The Kelly Criterion in
Blackjack, Sports Betting, and the Stock Market*. Two consequences follow:

- **If `μ ≤ 0`, then `g < 0` for every `f > 0`, and every drawdown depth is eventually reached
  with probability 1.** Sizing changes how fast ruin arrives, never whether it does. This is why
  the parent's sized paper book on raw-TA signals scored Sharpe −0.53 (parent #435, recorded in
  `docs/superpowers/specs/2026-06-21-equity-edge-hunt-roadmap-residual-xsmom-design.md`).
- **If `μ > 0`, sizing sets the survival profile.** At full Kelly (`f = μ/σ²`) the exponent is
  1, so the chance of ever halving is 50%. At half Kelly it is 3, so the chance is 12.5%.
  Halving the bet buys a fourfold cut in the chance of halving.

So risk management is a lever on a positive-expectancy exposure. The only positive-expectancy
exposure this repo has evidence for is the equity premium itself: `^GSPC` buy-and-hold earned
7.98% a year at Sharpe 0.422 over 1928–2026, price-only and before dividends (H1 audit). That is
why Part 3 leads with overlays.

### Which metric to manage

| Metric | Captures | Statistical behaviour | Verdict |
| --- | --- | --- | --- |
| Risk of ruin | Probability of hitting a loss floor | Total loss is unreachable unlevered (`x = 0` gives probability 0 above), and a partial floor is the drawdown question in the next rows; it earns its keep only with leverage or per-trade sizing | Wrong tool until a sized book exists |
| Max drawdown | The single worst peak-to-trough | One observation per path, dominated by 1929–32 on this tape (−86.2% buy-and-hold) | Report it, never gate on it |
| Time under water | Longest spell below a prior peak | Also one observation per path | Report it |
| **Ulcer index** (Martin & McCann, 1989) | `sqrt(mean(DD_t²))`, where `DD_t` is the percentage below the running peak: depth × duration over the whole path | Uses every session, so it is a smooth statistic a block bootstrap can put a CI on | **Manage this** |
| P(drawdown ≥ D within N years) | The operator's actual experience: "how likely is a −25% stretch in the next five years?" | Estimated by stationary block bootstrap (Politis & Romano, 1994); only ~20 non-overlapping 5-year windows exist since 1928 | Report it as the plain-language translation, as a curve over `D`, not a gated number |
| Conditional drawdown at risk (Chekhlov, Uryasev & Zabarankin, 2005) | Mean of the worst `α` share of drawdowns | Tail-focused, so it inherits max drawdown's thin sample | Not chosen: the ulcer index covers depth with more data behind it |

**Manage the ulcer index; report P(DD ≥ D within 5 years) for D ∈ {15, 20, 25, 30, 40}% beside
it.** The ulcer index is the only candidate that measures both depth and duration and can be
estimated with a usable interval from one index's history. The drawdown-probability curve is how
to explain it to the operator, and fixing it as a curve avoids choosing `D` after the fact.

### Should a survival-first objective replace the Sharpe bar?

Not replace: **add a second class**, as #336 already ruled. An edge-claiming sleeve keeps
`GATE_SHARPE = 0.7`, DSR, PBO, the bootstrap lower bound and beta-hedged alpha on any long-only
leg. A risk overlay is judged against buy-and-hold on ulcer index with Sharpe non-inferiority.
Holding an overlay to 0.7 asks a de-risked index to beat a bar the index itself has never met here
(0.422).

One gate wording needs the operator's attention. **G3's "≥ 3 consecutive months Sharpe ≥ 1.0"
cannot distinguish 1.0 from 0.** The standard error of an annualized Sharpe from `n` daily returns
is about `sqrt(252/n)` (Lo, 2002, *The Statistics of Sharpe Ratios*), which is 2.0 at 63 sessions,
so the 95% interval is roughly ±3.9. As written, G3 is a procedural check that paper tracks the
backtest. The statistical evidence has to come from G2's out-of-sample panel. Recommendation: keep
G3, re-label it a tracking gate, and pre-register that change before any paper book runs.

## Part 3 — Ranked candidates

Settled ground is not re-proposed (nine sleeves, the TA freeze, exits, re-slicing price/volume,
illusory breadth). Every candidate is free data. Power runs are `tools/distil_power.py` with
`--units per_book_day --sr-footing annual --periods-per-year 252 --sr-variance 0.0652` (the
filed sleeve family's annualized variance, per the 2026-09-27 retraction audit) unless stated
otherwise. Session counts come from the XNYS calendar in `exchange-calendars`.

For an overlay, `distil_power`'s DSR bar prices the overlay's own Sharpe against zero, which is
not the overlay gate. The line that matters is the CI half-width, which is the precision of the
Sharpe of the daily overlay-minus-benchmark series, as in the H1 audit. The ulcer-index leg has no
closed form. Its bootstrap width is priced in the phase-2 precheck and printed before any point
estimate.

### 1. OV-1 — the 200-day MA filter as an overlay, on a corrected frame

- **Mechanism.** Equity drawdowns cluster in persistent, high-volatility downtrends, and exiting
  after a trend break cuts exposure through most of a long bear market at the cost of whipsaw in
  bull markets. Literature: Brock, Lakonishok & LeBaron (1992, *JF*); Faber (2007, *Journal of
  Wealth Management*); Moskowitz, Ooi & Pedersen (2012, *JFE*); Hurst, Ooi & Pedersen (2017,
  *JPM*, a century of trend). Repo: H1 measured it as a risk reducer (volatility 0.121 against
  0.189, max DD −51.8% against −86.2%) that pays in 1929–32 and the 2000s and gives it back in
  long bull runs (the 2010s alone cost 56.7pp of summed daily excess).
- **Why it should persist.** It sells the left tail, not an informational edge, so it does not
  need anyone to be wrong. It needs bear markets to stay persistent rather than V-shaped.
  2020 shows the failure case.
- **Pillar.** Risk management.
- **Falsifier.** Pre-registered in Phase 2 below: ulcer index CI below buy-and-hold, at least 25%
  lower, and Sharpe non-inferior within 0.10, at 2 bps a switch.
- **Data.** Free: the Ken French daily library (CRSP value-weighted market total return and the
  daily risk-free rate, from 1926) plus `^GSPC` `1d`, already in the DB from 1927. The data-cost
  policy already lists Ken French as free.
- **Build route.** A risk overlay: a new `analytics/overlay/` package and a survival module (files
  below). No detector is touched.
- **Power.** On 1929→2026 (24,522 sessions, `--n-trials 10` for the MA-window family H1 already
  looked at, `--bar 0.2`) the half-width is **0.1987**, so the null is **LICENSABLE** and a
  ±0.2 Sharpe-of-difference result is readable. The tool's own-Sharpe bar is 0.569 against a
  corpus best of 0.604. The SPY 1993→ cross-check (8,480 sessions) has a half-width of 0.3379,
  NOT LICENSABLE, which is why it is a cross-check and not the gate.
- **Overlaps.** #353 (H5, `regime.py` as an overlay on a paper book) asks a different question and
  stays blocked. #336 supplies the yardstick.

### 2. TOM — hold the index only at the turn of the month

- **Mechanism.** Month-end payroll, pension and institutional cash flows concentrate buying
  around the turn of the month. Literature: Ariel (1987, *JFE*); Lakonishok & Smidt (1988,
  *RFS*, 90 years of the Dow); McConnell & Xu (2008, *FAJ*), who report that over 1926–2005 the
  US market's excess return was concentrated in the four-day window from the last trading day to
  the third; Etula, Rinne, Suominen & Vaittinen (2020, *RFS*), who tie it to institutional
  liquidity needs. The literature reports that predictability decays after publication: McLean
  & Pontiff (2016, *JF*) find average returns about 58% lower post-publication across 97
  anomalies.
- **Why this is not a re-slice of price.** The conditioning variable is the calendar, which is
  exogenous to the tape, and the mechanism is a flow that price does not carry. The repo's
  calendar rows (H-001, H-002, H-021) died on power at a **year** unit. TOM's unit is the
  **session**, with about 12 events a year.
- **Fits the brief's framing.** "A very simple signal that captures small moves with a high
  survival rate": in the market about 4 sessions in 21, flat otherwise.
- **Pillar.** Signal, with a survival by-product.
- **Falsifier.** Window fixed from the literature: the last session of the month plus the first
  three, entered at the close before the window. Cash earns the French risk-free rate, and costs
  are 2 bps per side, 24 sides a year, with 5 bps reported. Primary panel **1988→**, out of sample
  for Ariel and Lakonishok–Smidt. It is an edge claim and long-only, so per #336 it is judged on
  **beta-hedged alpha**: hedged annualized Sharpe ≥ `GATE_SHARPE` 0.7, DSR ≥ 0.95 at
  `n_trials = 4`, and `boot_lo` > 0. Secondary panel **2006→** (out of sample for McConnell–Xu)
  is reported for sign only. Ulcer index against buy-and-hold is reported, not gated.
- **Data.** Free: the same French file plus the XNYS calendar.
- **Build route.** A new sleeve, `analytics/tom/`, reusing OV-1's frame and survival module.
- **Power.** 1988→ (9,764 sessions, `--n-trials 4`: TOM plus the three calendar rows already
  tested): own-Sharpe bar **0.533**, so `GATE_SHARPE` 0.7 binds; half-width 0.3149, null NOT
  LICENSABLE at ±0.3. 2006→ (5,223): bar 0.630, half-width 0.4305. **Reachable, but only a large
  effect is readable, and a null would be INSUFFICIENT rather than refuted.**
- **Overlaps.** None directly. It shares the calendar family with #361 and #362 and must count
  them as trials.

### 3. VM — volatility-managed index exposure, unlevered

- **Mechanism.** Equity volatility is persistent while expected returns do not rise one-for-one
  with it, so scaling exposure by inverse recent variance raises Sharpe. Literature: Moreira & Muir
  (2017, *JF*) for the claim; Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018,
  *JPM*), who find vol targeting improves equity Sharpe and trims the left tail; Cederburg,
  O'Doherty, Wang & Yan (2020, *JFE*), who find that real-time, out-of-sample versions mostly fail
  to beat their unmanaged counterparts. The literature is split, and the published gains lean on
  levering up in calm markets, which a retail operator capped at 1.0× cannot do.
- **Pillar.** Risk management. It is also the vol-target component the north star already names.
- **Falsifier.** The overlay yardstick, identical to OV-1's gate, with weight
  `min(1, σ_target / σ̂_20d)` and `σ_target` fixed at the 1929→ median realized volatility.
  Measured **after** OV-1, so its increment over OV-1 is a paired comparison, not a fresh trial.
- **Data.** Free: the French file.
- **Build route.** A second rule in `analytics/overlay/`.
- **Power.** 1929→ at `--n-trials 3`: own-Sharpe bar 0.419, and the same 0.1987 half-width as
  OV-1. Readable.
- **Why third.** The 1.0× cap removes the half of the mechanism the literature's gains depend on,
  and its de-risking overlaps OV-1's in the same bear markets. Its question is whether it adds
  anything beside OV-1.

### 4. FOMC — pre-announcement drift at daily granularity

- **Mechanism.** Lucca & Moench (2015, *JF*) report that the S&P 500 earned a large share of its
  1994–2011 excess return in the 24 hours before scheduled FOMC announcements. Kurov, Wolfe &
  Gilbert (2021, *Finance Research Letters*) report that the drift largely disappeared after
  publication.
- **Pillar.** Signal (new information: the announcement calendar).
- **Falsifier.** Hold the index from the close before a scheduled announcement day to that day's
  close. Gate: mean event return > 0 with DSR ≥ 0.95 at `n_trials = 1` on 2012→ events (out of
  sample for the paper).
- **Data.** Free, but hand-built: the meeting dates come from federalreserve.gov. The effect lives
  in a 2pm-to-2pm window, and yfinance intraday reaches about two years back, so a daily bar
  dilutes the effect with the post-announcement reaction.
- **Power.** `--units per_alert --sr-footing per_obs --n-obs 118 --n-trials 1` (8 scheduled
  meetings a year, 2012 to September 2026): the required per-event Sharpe is **0.153**, and the
  null is LICENSABLE at ±0.3 (half-width 0.1804). The power is adequate, but the effect the
  literature reports after publication is near zero, and the daily bar dilutes whatever remains.
- **Verdict.** Measure only if candidates 1–3 are done. The expected result is a null.

### 5. H-024 extension — insider Form 4 history back to 2003

- **Mechanism.** The one non-price sleeve's premise was not refuted, only underpowered, and the
  audit names "more history" as the lever (`docs/audits/2026-09-20-h024-insider-phase3.md`). The
  fetch starts in 2015, while EDGAR serves structured Form 4 XML from mid-2003.
- **Why it ranks last.** The same audit shows that more precision would establish only that the
  opportunistic arm is reliably *worse* (T1 −0.468 net, `boot_lo` −1.054): "not a deployable
  finding". A longer panel over today's S&P 500 members also deepens survivorship. It sharpens a
  question about the paper and buys no sleeve.
- **Verdict.** **Do not build** while the goal is an edge. It is listed so the history lever is
  not mistaken for an open opportunity.

### Considered and excluded on arrival

| Candidate | Why excluded |
| --- | --- |
| Short-term index reversal (for example RSI(2) dips) | A boolean price detector under the TA freeze, and a re-slice of price |
| Overnight-return capture | A re-slice of price; about 504 sides a year, where the gapfill audit shows a 1 bp fee alone costs ~0.9 Sharpe at high turnover |
| Construction-debt fix for BAB and PEAD-broad | The honest-exit audit expects it to confirm the clean-arm nulls; low edge EV |
| Point-in-time, survivorship-free universe (Polygon, ~$29/mo) | Out of scope under the 2026-10-01 free-data constraint. It is the recognised unlock for PEAD in small caps and for H-023, and becomes the first candidate if the operator lifts that constraint |
| Midterm and political cycle (#361, #362) | Year unit; H-001/H-002 need 305+ midterm years (`docs/audits/2026-08-20-h001-h002-midterm-cycle-power-precheck.md`) |

## Decisions for the operator

1. Go or no-go on OV-1 as the next build, and separately on each later candidate (the free-data
   arc's per-candidate rule).
2. Adopt the overlay class (ulcer index, Sharpe non-inferiority) as a second yardstick beside
   `GATE_SHARPE`, as #336 ruled, and decide whether the north star becomes two-track: a
   survival-managed core plus alpha sleeves only once one clears G2.
3. Re-label G3 as a tracking gate, since three months cannot test a Sharpe.

**Ruled 2026-10-08, all as recommended.** (1) OV-1 is a go: #418. (2) The overlay yardstick is
adopted now, in `docs/north-star.md` § Two yardsticks; the two-track north star is decided after
OV-1 reports: #419. (3) G3 is a tracking gate in `docs/north-star.md`. Later candidates still
need their own go.

## Phase 2 — what to measure first, on the machine with the data

**The single first experiment is OV-1.** Its pre-registration below has been frozen since the
operator's go on 2026-10-08. Changing any line re-opens the trial count.

### Pre-registration

- **Panel.** The first session with 200 prior `^GSPC` closes on or after 1929-01-02, through the
  last session in the downloaded French daily file. Record both dates in the audit header before
  any return is computed.
- **Arms.**
  - `bh`: hold the market every session, earning French `Mkt-RF + RF`.
  - `ov`: long the market at session `t+1` when the `^GSPC` close at `t` is above its 200-session
    simple moving average, otherwise earn French `RF`. The signal is H1's `ma200d` rule
    unchanged, so the only difference from H1 is the frame.
- **Costs.** 2 bps per unit of |Δposition|, with 5 bps reported.
- **Primary gate. FOUND (overlay pass) only if all three legs hold:**
  1. `ΔUI = UI(ov) − UI(bh)` has a stationary-bootstrap 95% CI upper bound below 0. Use
     `analytics/research_guards/bootstrap.py`, `method="stationary"`, mean block 252 sessions,
     5,000 resamples, seed 20261008.
  2. `UI(ov) / UI(bh) ≤ 0.75` (point estimate): an economic floor of at least a quarter less
     ulcer.
  3. Sharpe non-inferiority: `SR(ov) − SR(bh)`, net and annualized, has a bootstrap 95% CI lower
     bound above −0.10.
- **Other verdicts.** **EXCLUDED** if the leg-1 CI lies wholly at or above 0, or the leg-3 CI
  upper bound is below −0.10. **BOUNDED** if leg 1 passes and leg 2 or 3 does not, or leg 3's CI
  straddles −0.10 (INSUFFICIENT on that leg). The precheck prints both bootstrap half-widths
  before any point estimate.
- **Reported, not gated.**
  - Max drawdown, the longest time under water, and the P(DD ≥ D within 5 years) curve for
    D ∈ {15, 20, 25, 30, 40}%.
  - Annual return, volatility and switches per year.
  - Realized beta and beta-hedged alpha, which are expected to be ≤ 0 and are not the yardstick
    under #336.
  - Sensitivity runs: a 1-session execution lag; split halves (sign only); and an SPY-adjusted
    1993→ cross-check, whose half-width of 0.338 makes it unable to overturn the primary.
- **Causality.** A truncated-series test over 6 cuts, with a deliberately non-causal positive
  control that must be flagged, as in H1.
- **Known frame limit.** The signal is on the S&P 500 price index and the returns are the CRSP
  value-weighted total market. The two are close substitutes but not the same index. The SPY
  cross-check bounds the gap on an investable instrument.

### Files it would touch

- `utils/french_client.py`: download and parse the Ken French daily factors with no network in
  tests (a `client` parameter, per repo convention). It is the first non-yfinance price source,
  so the data-cost policy row becomes "have".
- `analytics/research_guards/survival.py`: `drawdown_series`, `ulcer_index`, `time_under_water`
  and `drawdown_breach_curve` (bootstrap). This fills the Risk pillar's missing survival metric
  and is reusable by every later book.
- `analytics/overlay/{frame,rules,replay,report}.py`: the two-arm replay and the gate above.
  Candidate 3 adds a rule here later.
- `tests/test_french_client.py`, `tests/test_survival.py` and `tests/test_overlay_*.py`,
  including the causality test with its positive control.
- A `wifey-overlay-audit` Makefile target; an audit in `docs/audits/` with a Verdict heading, then
  `make docs-index`. A README and `.claude/context/analytics.md` row for the new packages.
- Not touched: `config/`, `DETECTOR_REGISTRY`, the live signal path, and `analytics.db`'s schema
  (the French series is read per run; whether to persist it is a later decision).

`analytics/**/*.py` is in CI's regression filter, so the build branch also runs
`make test-regression`.

## Amendment 1 — VM pre-registration (#421)

**FROZEN 2026-10-08, before any VM return was computed**, on the operator's go after OV-1 reported
FOUND. OV-1's pre-registration above is unchanged except for the naming in "Verdict function"
below, which changes none of its readings. Changing any line here re-opens the trial count.

The question is whether volatility management adds survival **beside** OV-1, now the adopted core.
It is a paired increment over OV-1, not a fresh trial.

### Rule

- `σ̂_t`: sample standard deviation (ddof 1) of the frame's market total returns over the 20
  sessions ending at `t`, inclusive, on the return calendar.
- Weight held over session `t+1`: `w = min(1, σ_target / σ̂_t)`. Unlevered, rebalanced daily, no
  band.
- `σ_target`: the median of `σ̂_t` over the OV-1 panel's sessions, computed once on the primary
  frame and printed by the precheck. It is in-sample by construction (the spec's wording), so the
  real-time version is a sensitivity run below.

### Panel, frame, costs and bootstrap

All as OV-1: French `Mkt-RF + RF` and `RF`, the OV-1 panel's exact sessions (so every arm is
paired), 2 bps per unit of |Δposition| with 5 bps reported, and a stationary bootstrap with mean
block 252, 5,000 resamples and seed 20261008, every leg on the same resamples. Daily rebalancing
with no band charges every small weight change, which is conservative.

### Arms

- `bh`: the market every session.
- `ov`: OV-1, unchanged.
- `vm`: weight `w` in the market, `1 − w` in `RF`.
- `ovvm`: OV-1 × VM, weight `pos_ov × w`.

### Gates

Both tests use OV-1's three legs and bars unchanged: ΔUI CI upper bound below 0, ulcer ratio
≤ 0.75 on the point estimate, and ΔSR CI lower bound above −0.10.

1. **Increment test (the headline): `ovvm` against `ov`.** This alone decides whether the core
   could change. FOUND means OV-1 × VM cuts ulcer by at least a further quarter at non-inferior
   Sharpe, and adopting it is still the operator's call. Any other verdict leaves the core at
   OV-1.
2. **Spec falsifier: `vm` against `bh`.** This is Part 3's literal test of VM as an overlay. It
   decides nothing operational, because VM can enter the core only as OV-1 × VM, so no
   multiplicity correction is applied between the two.

Reported, not gated: `vm` against `ov` (VM as a replacement for OV-1).

### Verdict function

The OV-1 pre-registration named no verdict for a leg-1 CI that straddles zero. **It is
`INSUFFICIENT`**: the survival change is not distinguishable from zero, and the premise is
neither shown nor refuted. The full order is:

1. EXCLUDED if the leg-1 CI lies wholly at or above 0, or the leg-3 CI upper bound is below −0.10.
2. FOUND if all three legs pass.
3. BOUNDED if leg 1 passes and leg 2 or 3 does not.
4. INSUFFICIENT otherwise (the leg-1 CI contains 0).

`analytics/overlay/report.py::overlay_verdict` returned `UNREGISTERED` for case 4, and returns
`INSUFFICIENT` from this amendment on. That changes no OV-1 reading: its primary and both gated
sensitivities were FOUND, and only the SPY cross-check, which cannot overturn the primary, fell
in case 4. An INSUFFICIENT headline is filed as EXCLUDED as an addition to the core, with the
premise recorded as not refuted, kept apart as in the H-024 audit.

### Reported, not gated

- Per arm: CAGR, volatility, Sharpe, ulcer, max drawdown, longest time under water, mean exposure
  and turnover per year (Σ|Δposition| per calendar year), plus the P(DD ≥ D within 5 years) curve
  for D ∈ {15, 20, 25, 30, 40}%.
- Sensitivity runs: 5 bps; a 1-session execution lag on both signals; a **real-time
  `σ_target`** (the expanding median of `σ̂` through `t`, the Cederburg et al. critique);
  split halves (sign only); and SPY total return from 1993, with `σ̂` from SPY's own returns and
  the primary `σ_target`.

### Causality and frame limits

- A truncated-series test over 6 cuts on the weight, with the `lag=0` weight (which reads the
  return of the session being earned) as a positive control that must be flagged.
- Pre-1952 Saturday sessions were short (two hours) and are expected to be quieter, so inside the
  20-session window they would bias `σ̂` low before 1952 and the weight slightly high. The
  precheck prints the Saturday-to-weekday volatility ratio so the size of this is measured.
- `σ̂` is on the CRSP total market, OV-1's signal is on `^GSPC`: each rule reads its own natural
  input.
