# Phase 0 — Correctness Foundation ("Stop Fooling Myself")

**Date:** 2026-06-05
**Status:** Spec — ready for review
**Parent roadmap:** `docs/superpowers/specs/2026-06-05-us-equities-top-tier-gap-map-design.md` §4 Phase 0
**Constraints:** correctness-first · free-data-only
**Scope:** the trust layer. Until these land, no backtest number is believable. This spec covers one
phase containing **five independently shippable deliverables** (0.1–0.5), each with its own
definition-of-done and its own future plan/PR. They are ordered by dependency, not bundled.

---

## 1. Goal & non-goals

**Goal:** make every backtest number produced by this repo *honest* — free of survivorship/selection
bias misuse, free of lookahead, haircut for multiple-testing, and net of realistic equity costs —
so that the edge work in Phases 1+ optimizes a real signal instead of an artifact.

**Non-goals (explicitly deferred):** new alpha, cross-sectional ranking, risk model, portfolio
construction, execution. Phase 0 changes *measurement*, not *strategy*.

## 2. Grounding — what the codebase does today (verified 2026-06-05)

| Concern | Current reality | Implication |
| --- | --- | --- |
| Fills | `analytics/backtest/engine.py`: `entry_idx = sig_idx + 1; entry_price = opens_np[entry_idx]` | **Next-bar-open already correct** — *not* Phase-0 work. Removes that item from the parent doc's 0.4. |
| Costs | `Trade.r_multiple`: `fee_drag_r = 2.0 * fee_pct * entry_price / risk` — flat fee, both legs | No spread, no market impact, no short borrow. Crypto-style. **0.4 target.** |
| WFO scoring | `param_sweep._score = avg_r × win_rate × √closed`; `decay = oos/is`; `overfit = decay<0.4 or oos avg_r<0` | Crude. **No Sharpe anywhere.** No trial-count haircut. **0.3 target.** |
| CV split | `param_sweep._split_ohlcv` = single contiguous IS/OOS split | No purge, no embargo → leakage at the boundary. **0.3 target.** |
| Universe | `utils/config_validation.load_stocks_config` → flat dict of 13 current mega-caps; `analytics_runner._resolve_symbols` static list | **No as-of concept.** Survivorship/selection bias unstated. **0.1 target.** |
| Ingest validation | `data_sync` raises only on empty; `data_fetcher` `.dropna()` in 4h resample | **No data-quality gate.** **0.5 target.** |

Two corrections to the parent roadmap, locked here: (a) next-bar-open fills are **done**; (b) a real
Sharpe must be built before a *deflated* Sharpe (0.3 depends on it).

## 3. Cross-cutting design principles

- **Default-off / backward-compatible where it changes numbers.** New cost model and new CV both
  ship behind config flags whose defaults reproduce today's output, so regression goldens stay
  byte-identical until the change is deliberately switched on and recalibrated. The one exception is
  0.4-on, which is *meant* to move P&L (see §4.4 DoD: golden refresh + `make db-update` required).
- **Methodology over data spend.** Every deliverable here is free; none requires a paid feed.
- **Reuse the harness.** Hook into `param_sweep`, `engine.py`, `data_sync`, and the Stats UI rather
  than building parallel infrastructure.
- **Each deliverable is a unit:** clear purpose, a typed interface, testable in isolation
  (`duckdb.connect(":memory:")` for DB-touching parts).

---

## 4. Deliverables

### 4.1 Universe-as-of-date policy (bias bounding)

**Problem:** backtests silently use *today's* 13 mega-caps → forward-looking selection. On this
universe the *delisting* bias is small (mega-caps rarely delist mid-sample), but it is currently
**unstated and unbounded**, and any future universe expansion would silently inflate results.

**Design:**

- Add an explicit `universe` concept with metadata: `as_of` date semantics + a documented bias
  classification per symbol set. Minimum viable form: extend `stocks.json` (or a sibling
  `config/universe.json`) with a `universe_policy` block — `{ "scope": "liquid_large_cap",
  "as_of": "fixed|today", "survivorship_note": "..." }` — validated in `config_validation.py`.
- `analytics_runner._resolve_symbols` and the backtest runner stamp the active policy onto every
  saved backtest run (new column / metadata on `backtest_runs`) so results are self-describing.
- Backtest output (CLI + Stats UI) **prints the universe policy + survivorship caveat** alongside
  the headline metrics — the honesty surface.
- **Default deliverable = bound-the-universe + state-the-bound.** Free PIT S&P-constituent scraping
  (GitHub/Wikipedia revision datasets) is an *optional follow-on*, not in this DoD — it only buys
  selection-bias correction, while delisted *prices* remain unavailable on free data.

**DoD:** no code path uses an implicit "today's tickers" set without an attached, printed policy;
every saved backtest run records its universe policy; a one-paragraph survivorship bound is rendered
in backtest output. **Goldens unmoved** (additive metadata; default policy describes current
behaviour).

### 4.2 Lookahead / leakage audit

**Problem:** no formal guarantee the pipeline is causal. Highest-risk spots: detector bar indexing
(`iloc[-1]` vs `[-2]`), regime-gate timing, any statistic computed over a full series, and
**split/dividend-adjustment lookahead** (yfinance fully-adjusted series embed future corporate
actions into past prices).

**Design:**

- Produce a written audit (`docs/redesign/phase0-lookahead-audit.md`) enumerating every place
  historical data is read, classifying each as causal / leaking / justified.
- Add a **lookahead test harness**: a property-style test that feeds a detector/backtest a series
  truncated at time *t* and asserts the signal at *t* is identical to the same signal computed on the
  full series (no dependence on future bars). Lives in `tests/`.
- **Adjustment lookahead:** confirm/establish as-of adjustment (apply only splits/divs known by the
  bar date) rather than fully-adjusted series; document the chosen convention in
  `utils/yfinance_client.py`.

**DoD:** audit doc complete; lookahead test harness green across all 16 detectors + the backtest
entry path; adjustment convention documented and tested. Any genuine leak fixed (may move goldens —
flag per-fix).

### 4.3 Overfitting / multiple-testing controls

**Problem:** `tp_r` is chosen by a grid sweep with no penalty for the number of configurations
tried, scored on a metric (`avg_r × win_rate × √n`) that is not a Sharpe and has no sampling
distribution. The single contiguous IS/OOS split leaks across its boundary.

**Design (three ordered sub-deliverables):**

- **0.3a — Sharpe + Deflated Sharpe Ratio.** Compute the strategy Sharpe on the per-trade R-multiple
  series (the engine's natural unit: `SR = mean(r)/std(r)`, annualized by trade frequency). Add
  **Deflated Sharpe** (Bailey & López de Prado 2014) using the trial count `N` (= grid size from
  `_build_grid`) and the SR's sampling variance. New module `analytics/backtest/stats_overfit.py`;
  `BacktestResult` gains `sharpe`; `param_sweep` records `N` and emits DSR per config.
- **0.3b — Probability of Backtest Overfitting (PBO).** Combinatorially-Symmetric CV (Bailey et al.
  2017): partition the trade series into S submatrices, rank configs IS, measure the OOS rank of the
  IS-best, report PBO = P(logit < 0). Add to `stats_overfit.py`; surface in the sweep report.
- **0.3c — Purged + embargoed CV.** Replace/augment `_split_ohlcv` with a purged K-fold that drops
  trades straddling each fold boundary and applies an embargo of *k* bars after each test fold
  (López de Prado, *Advances in Financial ML* ch.7). Behind a `cv_mode` flag (default = legacy
  contiguous split → goldens unmoved).

**DoD:** every committed `tp_r` carries a Deflated-Sharpe and PBO figure in the sweep output; sweeps
log trial counts; purged-embargoed CV available and tested; `/wfo-sweep` + `/param-sweep-apply`
skills reference the new haircut. Default flags reproduce current goldens.

### 4.4 Realistic equity cost model

**Problem:** the flat `fee_pct` is a crypto taker-fee abstraction. Equities P&L is dominated by
**half-spread** (by liquidity bucket) + **square-root market impact** + (for shorts) **borrow cost**
over the holding period.

**Design:**

- New `analytics/backtest/cost_model.py`: a frozen `CostModel` dataclass + `cost_r(trade, ctx)`
  returning per-trade cost in R, decomposed as `spread_r + impact_r + borrow_r + commission_r`.
  - *Spread:* half-spread per liquidity bucket (bucket by ADV/price; free proxy from OHLCV).
  - *Impact:* square-root law `impact ∝ σ·√(order_size/ADV)`; order size from a notional assumption.
  - *Borrow:* shorts only, `rate × holding_days`; default rate a conservative constant (free borrow
    data is scarce — documented constraint, parameterized for later).
  - *Commission:* retail ≈ 0; keep configurable.
- Wire into `engine.py` where `fee_drag_r` is computed: `Trade` takes an optional `cost_model`;
  when `None`, falls back to the existing flat-fee path (**byte-identical default**).
- Config surface: `[backtest.cost_model]` TOML block; off by default.

**DoD:** cost model is a tested, documented module; turning it on produces equity-realistic net P&L.
**This deliverable, when switched on for the live configs, intentionally moves regression goldens →
requires `make db-update` + golden refresh + recalibrate, done as a deliberate, reviewed step** (not
a silent drift). Default-off path keeps goldens byte-identical.

### 4.5 Data-quality monitors

**Problem:** ingest has no integrity gate. Bad bars (gaps, NaNs, zero-volume, fat-finger prints,
unhandled splits) silently corrupt backtests and live signals.

**Design:**

- New `analytics/data_quality.py`: pure functions over an OHLCV DataFrame returning a typed
  `DataQualityReport` (gaps vs expected bar cadence, NaN/zero-volume rows, |return| outliers beyond
  a σ threshold, and a **split-sanity check** — an unflagged ~2×/3× overnight jump is a likely
  unadjusted split).
- Call from `data_sync.backfill/sync` after fetch: log the report; quarantine (drop + log) clearly
  bad bars; raise only on catastrophic emptiness (preserve current behaviour).
- Optional: persist a per-symbol data-quality summary for a future Stats UI card (out of Phase-0
  scope to render).

**DoD:** ingest emits a data-quality report; bad bars are quarantined with a log trail; unit tests
cover each check on synthetic dirty frames. Read-mostly/additive → goldens unmoved.

---

## 5. Suggested ordering & PR slicing

Each row is its own plan + PR. Recommended order maximizes trust-per-step:

1. **0.5 data-quality** — cheap, additive, protects everything downstream. (goldens unmoved)
2. **0.2 lookahead audit + harness** — establishes causality guarantees. (mostly unmoved)
3. **0.3a DSR + 0.3b PBO** — make the *measurement* trustworthy. (unmoved; new columns)
4. **0.1 universe policy** — bound the bias, self-describe runs. (unmoved)
5. **0.3c purged-embargoed CV** — behind a flag. (unmoved until enabled)
6. **0.4 cost model** — last, because it deliberately moves goldens and depends on a trustworthy
   measurement layer to interpret the new net numbers. (moves goldens → db-update)

## 6. Testing strategy

- Unit tests per module with `duckdb.connect(":memory:")` isolation; synthetic dirty/clean frames
  for 0.5; truncated-series property tests for 0.2; known-answer fixtures for the DSR/PBO math in
  0.3; a hand-computed cost example for 0.4.
- Regression goldens: every deliverable except 0.4-on must keep `make test-regression` byte-identical
  (proven before merge). 0.4-on does a deliberate golden refresh + `make db-update` + recalibrate.
- `make lint-py && make typecheck && make test` green per PR (repo gate).

## 7. Decisions & open questions

- **One spec, five PRs** (chosen) — Phase 0 is a phase, not a monolith; deliverables ship
  independently.
- **0.1 = bound-the-universe** (chosen default); free PIT-constituent scraping is an optional
  follow-on, not in DoD.
- **0.3 Sharpe basis = per-trade R series** (chosen) — matches the engine's native unit; revisit if
  Phase 4's portfolio backtester wants a daily-returns Sharpe instead.
- **Open:** borrow-rate default for 0.4 (no free borrow feed) — parameterized constant for now;
  revisit when/if the short book becomes real (Phase 3).
- **Open:** whether 0.4 turns on for *all* live configs at once or strategy-by-strategy after
  recalibration — decide at 0.4 plan time.

## 8. What stays untouched

Strategy detectors, the gate stack, live-parity replay, the signal daemon, the outcome ledger, and
the Stats UI structure. Phase 0 augments measurement; it does not touch signal generation.
