# Edge-Hunt #4 — Post-Earnings-Announcement Drift (PEAD-lite)

## Motivation

The binding constraint is recorded in memory
[[binding-constraint-no-equity-edge]]: **wifey has no trustworthy, deployable
positive-Sharpe edge.** Five sleeves now fail their pre-registered gates —
forecast trend **G2 ≈ 0** (#91), cross-sectional momentum **G3 = −0.156** (#92),
residualized XS-momentum **experiment #1 = FAIL** (#98), low-beta / BAB
**edge-hunt #2 = FAIL** (#100), and cross-asset TSMOM **edge-hunt #3 = FAIL
(clean)** (#102). The whole sizing / portfolio / risk stack is built and
portable, but it is *downstream* of a signal we do not have.

This is **edge-hunt #4** on the roadmap fixed in the experiment-#1 spec
(`docs/superpowers/specs/2026-06-21-equity-edge-hunt-roadmap-residual-xsmom-design.md`):
**post-earnings-announcement drift over free EDGAR earnings data.** The roadmap
catalogs #4 as `new ingestion / greenfield / longable / gated behind #1-#2` and
names it the **last free-data family before the honest-exit criterion** ("if all
of #1-#4 fail clean … trigger the $29/mo Polygon upgrade or accept that the
free-data US-equity edge needs paid event / flow data"). Each edge family gets
its own spec → plan → implementation cycle; this spec designs #4 in full.

## The lesson from #1-#3 that shapes this design

Every one of the five failures was a **price / time-series factor** — trend,
cross-sectional relative-strength momentum, beta, cross-asset trend. They share a
DNA: they extract a signal from *price history alone*. We have now tested that
DNA five ways and it does not survive net of cost on free data.

PEAD is **categorically different**: it is an *event / fundamentals* anomaly. The
signal is the **earnings surprise** — a non-price input — and the tradable effect
is the empirically robust under-reaction *drift* in the weeks after the
announcement. This is the first edge family on the roadmap whose information
source is orthogonal to the five fails. Declaring "no free-data edge exists"
after testing only price-factors would be an overgeneralization; PEAD is the
hypothesis that closes that gap.

Two design consequences inherited from the prior fails:

1. **Read the verdict on a market-neutral cell, never a long-only one.** #1's
   only positive number (+0.88 long-only) and #2's (+17.5 β long-only) were
   survivorship + bull-market artifacts, not edges. The committed cell here is
   the **dollar-neutral long-short** book; the long-only form is a *deployable
   annotation*, not the gate.
2. **Carry the realized-β-to-SPY guardrail** introduced in #2/#3. A dollar-neutral
   PEAD book (long positive-surprise, short negative-surprise) should be roughly
   beta-balanced and event-driven; realized β ≈ 0 confirms the result is a genuine
   anomaly and not equity beta in disguise.

## The free-data signal path (de-risked before this spec was written)

A live probe confirmed the make-or-break question — **can a deep-history PEAD
signal be built from free data without paid analyst estimates?** — is **yes**:

- **EDGAR `companyfacts` (`https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`)**
  returns structured XBRL `us-gaap:EarningsPerShareDiluted` facts. The AAPL probe
  returned **309 quarterly 10-Q/10-K facts, filing dates 2009→2026 (~17 yr),
  period-ends 2007→2026**, each carrying `val`, `start`, `end`, `fp`
  (`Q1`/`Q2`/`Q3`/`FY`), `fy`, `filed`, `form`, `accn`. That is everything needed
  for the canonical academic surprise measure.
- **No paid estimates required.** PEAD's surprise driver does **not** have to be
  an analyst consensus (the paid path). The Foster–Olsen–Shevlin / Bernard–Thomas
  literature standard is a **seasonal-random-walk SUE** (this quarter's EPS minus
  the same fiscal quarter a year ago, scaled by the rolling std of that change) —
  built entirely from reported EPS history. EDGAR delivers exactly that, with
  decades of depth.
- **No new poetry dependency.** EDGAR is plain JSON over stdlib `urllib`;
  yfinance's `get_earnings_dates` (which needs an uninstalled `lxml` and carries
  only a shallow surprise history) is *not* used.

If EDGAR coverage had been thin or estimate-gated, that would itself have been the
honest-exit trigger reached cheaply. It is not — the path is clean and deep, so we
build.

## Hypothesis and null

- **Null under attack:** "Post-earnings-announcement drift, traded on a
  free-data seasonal-random-walk surprise over the S&P-500 universe, has no edge
  net of realistic costs."
- **Economic prior:** PEAD is one of the most-replicated anomalies in finance
  (Ball & Brown 1968; Bernard & Thomas 1989/1990; Foster, Olsen & Shevlin 1984).
  Prices under-react to earnings news and drift in the direction of the surprise
  for ~60 trading days. The premium is usually attributed to investor
  under-reaction, limited attention, and slow information diffusion. It is an
  *event* anomaly, not a price-trend anomaly, so it is unconfounded by the
  bull-market / survivorship trap that faked the prior results.
- **The open empirical question** is whether it **survives in a free,
  S&P-500-only, large-cap proxy net of realistic costs** over the post-2010
  window. PEAD is known to concentrate in **smaller, less-covered** names; the
  S&P-500 universe is the most-arbitraged, most-covered slice of the market, where
  the drift is *most* likely to have been competed away. Our universe is therefore
  a deliberately **hard** test — a pass here is strong, a fail is partly
  attributable to large-cap efficiency (flagged, see Open Questions).
- **Decisive either way.** A pre-registered pass → the first trustworthy,
  event-driven, beta-diversified sleeve, and the binding constraint finally
  cracks. A clean fail → **all four roadmap free-data families are exhausted**,
  which by the roadmap's own criterion triggers the honest-exit decision
  ($29/mo Polygon depth/breadth/estimates, or accept paid event-flow data is
  required). We learn it at the cost of one greenfield ingestion the project
  needed anyway.

## Construction

Unlike #1-#3, PEAD is **not** a `forecast/` or `xsmom/` reuse — the book is an
**event-driven, overlapping-holding-period** portfolio, which is genuinely new.
The reused pieces are the OHLCV loader, `forecast.vol` (inverse-vol position
scaling), the Phase-0.4 cost model surface, and the `research_guards` gate stack.

### Signal — seasonal-random-walk SUE (causal)

For each name, per fiscal quarter `q`:

1. **Quarterly diluted EPS** from EDGAR `companyfacts`, restricted to
   quarter-duration facts (`end − start ≈ 84-98 days`). Q4 is derived as
   `FY − (Q1 + Q2 + Q3)` only when all three interim quarters and the FY value are
   present; otherwise that quarter yields no signal (coverage loss, not bias —
   flagged).
2. **Originally-filed value only.** When a period `(fy, fp)` appears more than once
   (restatements / amendments), keep the **earliest `filed`** record. Restated
   values are look-ahead and are discarded. This is causality-critical.
3. **Unexpected earnings:** `UE_q = EPS_q − EPS_{q-4}` (same fiscal quarter, prior
   year — the seasonal random walk).
4. **SUE:** `SUE_q = UE_q / std(UE over the trailing ≤8 quarters)`, using only
   quarters whose announcement preceded `q`'s. Names with `<4` prior UE
   observations to estimate the std yield no signal that quarter (warm-up).

### Announcement date — the causal entry anchor

The drift is anchored on **when the surprise became public**, never on the period
the EPS covers:

- **Primary: the 8-K item-2.02 filing date** ("Results of Operations and
  Financial Condition" — the earnings press release), from EDGAR `submissions`
  (`https://data.sec.gov/submissions/CIK{cik}.json`), matched to the quarter whose
  `period_end` it follows and whose 10-Q/10-K `filed` it precedes.
- **Fallback: the 10-Q/10-K `filed` date** when no matching 8-K item-2.02 is
  found.
- **Entry is strictly `announce_date + 1` trading session** (the next NYSE session
  via `analytics.trading_calendar`), so no position is ever taken on or before the
  bar that disclosed the surprise.

Using the 8-K (not the later 10-Q) matters: PEAD enters at the announcement, and a
late 10-Q entry (weeks after) would skip the early drift and make a null
uninterpretable. The 10-Q fallback is conservative and flagged.

### Book — overlapping 60-day drift cohorts

The Bernard–Thomas drift window is **60 trading days, pre-registered and not
swept** (sweeping the window would re-introduce selection bias). Construction:

1. On each name's entry session, fix its **target weight ∝ its cross-sectional
   SUE z-score** at that announcement (continuous, house-style — no arbitrary
   quantile cutoff), inverse-vol scaled via `forecast.vol` (trailing realized vol,
   `.shift(1)`-guarded) so a volatile name and a quiet name carry equal risk.
2. Hold the position **flat for 60 trading sessions, then close.** Overlapping
   cohorts (a name can re-enter on its next announcement) sum into the daily book.
3. **Dollar-neutral (committed cell):** each day, demean the active cohort's
   weights so `Σw = 0` (long high-SUE, short low-SUE). **Long-only (deployable
   cell):** clip the SUE z-score at 0 before weighting (top-half, long-or-flat) —
   the same default-off clip pattern used by #2/#3.
4. **Portfolio vol-normalisation** to a fixed annual target, causal trailing
   governor, reusing the established sizing convention.
5. **Honest costs** = `|Δweight| × (fee + slippage)` per the Phase-0.4 cost
   surface, reported at **0 / 2 / 8 bps** slippage. PEAD churns its whole book
   every ~60 days and concentrates trades in event windows, so it is **more**
   cost-sensitive than TSMOM — a death-by-8 bps is plausible and informative.

### The four book forms (the 2×2 grid)

| universe ↓ \ construct → | long-short (dollar-neutral) | long-only (clip ≥ 0) |
| --- | --- | --- |
| **broad S&P-500** | **◀ GATED CELL** | deployable (flagged) |
| large-cap (S&P-100 snapshot) | breadth-vs-liquidity contrast | deployable (flagged) |

- `broad_ls` — **the pre-committed gated cell**: dollar-neutral SUE drift over the
  full breadth universe. The cleanest test of the anomaly.
- `broad_long` — broad universe, long-flat. The deployable no-short form.
- `mega_ls` — the S&P-100 (`config/universe_sp100_snapshot.json`) sub-universe,
  dollar-neutral. The "is the drift just a smaller/less-liquid-name effect that
  vanishes in mega-caps?" contrast.
- `mega_long` — mega sub-universe, long-flat.

The verdict is read on `broad_ls` **only** — not whichever of the four scores
highest (that would re-introduce selection bias). The other three are diagnostic
context, and the full four-book family feeds the DSR deflation and the CSCV / PBO
trial count.

### Equity-beta guardrail (the thesis check)

The realized β of the committed `broad_ls` book to **SPY** is reported as a
first-class diagnostic and expected ≈ 0 (an event-driven, dollar-neutral book is
beta-balanced by construction). SPY is loaded separately as the equity benchmark.
A Sharpe pass with a large positive equity β is flagged "drifting with the equity
bull in disguise," not a clean anomaly — the structural analogue of the #2/#3
guardrail.

## Universe (pre-registered)

- **Broad arm:** the existing research breadth universe `config/universe.json`
  (508 members ≈ current S&P 500 + 4 ETFs; single-names only via
  `ResearchUniverse.stocks()`). No new universe artifact — PEAD reuses the equity
  panel, unlike #3's frozen ETF basket.
- **Mega arm:** `config/universe_sp100_snapshot.json` ∩ active universe (the same
  "mega" sub-universe experiment #1 used).
- **History:** EDGAR EPS depth allows pre-2010 starts, but the OHLCV panel is
  backfilled from **2018** (the breadth-universe backfill window) and the
  S&P-500 membership is current-list (survivorship). The PEAD backtest therefore
  runs **2018→present** on the equity panel; a deeper run is gated on a deeper
  OHLCV backfill (Open Questions). ~7 years × the full announcement cross-section
  is still a large event count for DSR / PBO / MinTRL because every name
  contributes ~4 events/year (≈ 500 names × 7 yr × 4 ≈ thousands of drift
  cohorts), unlike the thin daily panels of #1-#3.
- **Survivorship:** the same bounded, flagged caveat as every equity sleeve — the
  S&P-500 list is current (no PIT membership feed). PEAD is *less* exposed to the
  long-only survivorship trap than #1/#2 because the committed cell is
  dollar-neutral, but the universe selection bias is noted, not eliminated.

## New persisted data (the greenfield piece)

PEAD needs earnings facts the OHLCV store does not have. Following the
xasset-backfill pattern (ingest once, read-only thereafter):

- **`utils/edgar_client.py`** — stdlib-`urllib` EDGAR client (no key, polite
  `User-Agent` + ≤10 req/s throttle). `company_tickers()` (ticker→CIK map from
  `https://www.sec.gov/files/company_tickers.json`, cached), `company_facts(cik)`
  (diluted-EPS quarterly facts), `submissions(cik)` (8-K item-2.02 dates). Pure
  fetch + parse; no DB, no module-level side effects (mirrors
  `utils/yfinance_client.py`).
- **`earnings_facts` table** (new, additive) — `(symbol, cik, fy, fp, period_end,
  eps_diluted, announce_date, filed_date, accn, source)`, upserted via a new
  `analytics/store/earnings.py` module following the sealed `_upsert`
  register/unregister convention. Added to the migration list **and**
  `init_schema` CREATE TABLE (it is a brand-new table touched by nothing legacy,
  so there is no positional-INSERT hazard). Read-only by every audit path after
  ingestion.
- **`wifey analytics earnings-backfill`** (new CLI subcommand, or a `tools/`
  one-shot if the CLI surface is heavier than warranted — decided in the plan) +
  **`make wifey-pead-backfill`** — loops the universe CIKs, fetches + parses +
  upserts. One-shot; not part of the daily `make go-live` watchlist refresh.

This new table and client are the only writes. The book / replay / report / audit
layer is read-only over `earnings_facts` + `ohlcv`, so — like #1-#3 — **no
detector, no signal path, no backtest golden is touched.**

## Acceptance gate (pre-registered — fixed before any result exists)

The bar is fixed **now**, before any number is computed, and mirrors the
edge-hunt #2 / #3 gates verbatim so all three are directly comparable:

- **PASS = all of:** net-of-cost **DSR ≥ 0.95**, **PBO ≤ 0.5**, bootstrap-CI
  **lower bound > 0**, **n ≥ MinTRL**, and **OOS Sharpe ≥ 0.7**.
- **Pre-committed cell:** the verdict is read on the **`broad_ls`** book. The
  other three books are diagnostic context and feed the PBO / DSR trial count
  (grid = 4).
- **Deploy-grade tier (annotation, not the pass/fail line):** Sharpe **≥ 1.0**
  *and* the long-flat translated form (`broad_long`) holds up. A `≥ 0.7` pass is
  "real edge, candidate sleeve"; a `≥ 1.0` pass is "deploy-grade, fast-track."
- **Costs in every P&L surface:** report Sharpe at **0 / 2 / 8 bps** slippage
  (`--slippage-bps`, like the #1/#2/#3 audits). A pass that dies by 8 bps is
  "thin / parked," not a deploy — and PEAD's higher turnover makes this the most
  load-bearing of the four edge-hunt gates.
- **Equity-beta guardrail:** the realized β of `broad_ls` to SPY is reported and
  expected ≈ 0. A Sharpe pass with a large positive equity β is flagged suspect.

The `≥ 0.7` Sharpe floor paired with a net-of-cost **DSR ≥ 0.95 deflated over the
full four-book trial count** is a high bar against thin / overfit edges.

## Components (units, each one purpose, additive)

A new self-contained package `analytics/pead/` mirroring the `forecast/` /
`xsmom/` / `lowvol/` / `xasset/` split, plus the EDGAR client, the earnings store
module, a backfill entry point, and a read-only audit CLI.

- **`utils/edgar_client.py`** — pure EDGAR fetch/parse (above). Testable units:
  `parse_eps_facts(companyfacts_json)`, `parse_announce_dates(submissions_json)`,
  `ticker_to_cik(company_tickers_json, ticker)` — all fed JSON fixtures, no
  network in tests.
- **`analytics/store/earnings.py`** — `upsert_earnings_facts` +
  `get_earnings_facts(conn, symbols=…)`, sealed `_upsert` convention; in-memory
  DuckDB tested.
- **`analytics/pead/signals.py`** — pure, no I/O. `seasonal_sue(eps_panel)`
  (causal SUE per the rules above, `.shift`-guarded), `cross_sectional_z`,
  `sue_leverage(...)` (z-weight → inverse-vol scale → demean for L/S **or**
  clip≥0 for long-only → portfolio vol-normalise), with the 60-day overlapping
  cohort logic. The causality heart of the sleeve.
- **`analytics/pead/replay.py`** — the **only** DB-touching module, read-only.
  Loads `earnings_facts` + 1d closes (reusing `forecast.replay.load_daily_inputs`
  for the OHLCV side), builds the SUE panel, and runs the 2×2 grid;
  `pead_market_return(...)` returns the SPY benchmark separately (no double-count).
- **`analytics/pead/report.py`** — pure. `PeadGridReport` (per-cell `G2`-style
  report, per-cell realized-β to SPY, `committed_key`, `passed`, `deploy_grade`)
  and `evaluate_pead_grid(...)`, reusing `forecast.report.evaluate`
  (DSR/PBO/boot-CI/MinTRL) per cell over the four-cell family and
  `xsmom.diagnostics.beta_attribution` for the guardrail. Gate
  (`_GATE_SHARPE = 0.7`, `_DEPLOY_SHARPE = 1.0`) read on `broad_ls`.
- **`analytics/pead/__init__.py`** — eager re-exports, matching the siblings.
- **CLI / Makefile** — `tools/pead_backfill.py` with `make wifey-pead-backfill`,
  and `tools/pead_audit.py` with `make wifey-pead-audit`
  (read-only verdict CLI: 2×2 grid at 0/2/8 bps, per-cell DSR/PBO/boot-CI/MinTRL,
  realized equity-β, long-flat leg Sharpe, PASS/FAIL + deploy flag on `broad_ls`;
  `build_grid` is the testable unit).
- **`tests/`** — `test_edgar_client.py` (JSON-fixture parse, Q4 derivation,
  earliest-filed dedup, 8-K matching), `test_earnings_store.py` (in-memory
  upsert/get round-trip), `test_pead_signals.py` (SUE math, causality perturbation
  — a future EPS fact must not move any earlier position; cohort overlap;
  long-only clip), `test_pead_replay.py` (four cells returned, SPY benchmark a
  Series, seeded in-memory DuckDB), `test_pead_report.py` (grid keys, committed
  key, gate boolean, finite β).
- **`docs/audits/2026-06-23-edge-hunt-4-pead-lite.md`** — the verdict note,
  written after the audit runs.

## Data flow

```text
config/universe.json + sp100 snapshot        wifey-pead-backfill (one-shot)
        │                                              │ EDGAR companyfacts + submissions
        ▼                                              ▼
ResearchUniverse.stocks()  ──►  utils/edgar_client.py  ──►  earnings_facts table (DuckDB)
                                                              │           ▲ ohlcv table
                                                              │           │ (existing)
                                                              ▼           │
                              analytics/pead/replay.py  (SUE panel + 1d closes
                                        │                → sue_leverage → grid ×4 + SPY)
                                        ▼
                              analytics/pead/report.py  (forecast.evaluate per cell over
                                        │                4-cell family + beta_attribution
                                        ▼                → gate on broad_ls)
                              tools/pead_audit.py  →  docs/audits/2026-06-23-edge-hunt-4-pead-lite.md
```

`earnings_facts` is the only new write, populated once by the backfill. Every
audit path is read-only over `earnings_facts` + `ohlcv`, with no detector / signal
/ backtest import ⇒ **regression goldens byte-identical.**

## Testing

- **Causality is the load-bearing invariant.** PEAD is the most look-ahead-prone
  sleeve yet (two dates per fact: `period_end` vs `announce`). A perturbation test
  per transform: inject a fact whose `announce_date` is in the future and assert
  no position dated earlier changes; restate a past period and assert the
  originally-filed value still drives the signal (RED-without-the-dedup verified,
  non-vacuous).
- **EDGAR parse tests** over committed JSON fixtures (a trimmed AAPL companyfacts +
  submissions sample) — never a live network call in the suite, per house rules.
  Cover Q4 derivation, earliest-filed dedup, the 8-K item-2.02 match, and the
  10-Q fallback.
- **Signal-math tests:** hand-computed SUE on a tiny synthetic EPS panel; the
  inverse-vol scaling and dollar-neutral demean; the long-only clip leaves no
  negative weight.
- **Replay tests** seed an in-memory DuckDB with deterministic OHLCV + a handful
  of earnings facts and assert the four cells are returned and the SPY benchmark
  is a non-empty Series — the `xasset` / `lowvol` replay pattern.
- **Regression goldens** (`make test-regression`) must stay byte-identical — if
  one moves, STOP, it means an unintended import onto the signal / backtest path.
- Full `make lint-py` (ruff), `make typecheck` (mypy strict), `make test`,
  hand-formatted markdown.

## Out of scope (YAGNI)

- No paid analyst-estimate consensus — the seasonal-random-walk SUE is the
  deliberately-free surprise. Analyst-based SUE is the paid-data retest, a
  separate pre-registration.
- No deeper pre-2018 OHLCV backfill in this cycle — the PEAD run uses the existing
  breadth-universe panel; a deeper run is a follow-up if the shallow run is
  promising-but-thin (Open Questions).
- No sizing / portfolio / paper-book work — gated on ≥ 2 clearing sleeves per the
  roadmap convergence rule.
- No new boolean TA detectors, no tp_r / gate / threshold sweeps (frozen
  category).
- No drift-window sweep, no quantile sweep — the 60-day window and continuous
  z-weighting are pre-registered constants, not tuned.
- No intraday / announcement-timing precision (pre- vs post-market 8-K) — entry is
  the next NYSE session after the filing date, conservatively.
- No short-borrow / hard-to-borrow modelling beyond the book's turnover cost —
  consistent with the prior sleeves; noted as mildly optimistic for the
  short leg.
- No live PEAD signal wiring into the daemon — this is a research verdict; a
  live sleeve is gated on a pass.

## Open questions

- **CLI subcommand vs `tools/` one-shot for the backfill.** A new
  `wifey analytics earnings-backfill` is the clean home, but the ingestion touches
  a new table + client and may warrant staying a `tools/` script first (as the
  xasset backfill effectively did via a Makefile loop). Decided in the plan;
  not gate-affecting.
- **Backfill depth vs OHLCV depth.** EDGAR EPS reaches ~2010 but the equity OHLCV
  panel starts 2018. If the 2018→present run passes the gate but is MinTRL-thin, a
  follow-up deeper OHLCV backfill (pre-2018) is the cheap robustness extension —
  not done pre-emptively.
- **Large-cap efficiency caveat.** PEAD is strongest in small/less-covered names;
  the S&P-500 universe is the hard case. A clean fail on `broad_ls` should be read
  as "no PEAD in large-caps net of cost on free data," **not** "no PEAD" — the
  small-cap retest needs a universe wifey does not yet carry (flagged for the
  honest-exit framing, not in scope).
- **8-K item-2.02 coverage.** Not every earnings release is tagged cleanly; the
  10-Q `filed` fallback covers the gaps but enters later. The fraction of cohorts
  on the fallback path is reported in the audit as a data-quality diagnostic.
