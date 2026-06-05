# US-Equities Top-Tier System — Gap Map & Phased Roadmap

**Date:** 2026-06-05
**Status:** Discussion artifact / roadmap (no end-state committed — direction to be chosen later)
**Constraints chosen by user:** **Correctness-first** sequencing · **Free-data-only** budget
**Scope:** Gap map + phased roadmap. This is *not* a single-feature implementation spec; it is the
decomposition from which individual specs/plans will later be cut, one phase at a time.

---

## 1. Purpose & framing

This repo is a fork of a Binance crypto bot (`buibui-moon-trader-bot`) being repurposed into a
yfinance-backed US-equities signal bot. The engineering infrastructure is solid; the **quant
methodology is still crypto-single-name**. This document maps what a top-tier US-equities
systematic system actually requires, grades the current codebase against it, and lays out a
correctness-first roadmap achievable on free data.

### 1.1 The one reframe that subsumes most gaps

> **Current:** a single-name, signal-by-signal technical alert bot — "did a pin bar fire on AAPL's
> 4h chart?" Each strategy fires independently with a `tp_r`.
>
> **Top-tier:** a **cross-sectional, portfolio-level** system — rank the entire universe against
> itself, forecast expected returns, and construct a risk-controlled portfolio net of realistic
> costs. The unit of decision is "given my forecast and risk model, what do I hold and at what
> size?", not "did a pattern fire on one chart?"

Almost every specific gap below is a symptom of this single chasm.

### 1.2 The honest tension in "correctness-first + free-only"

These two choices collide at exactly one point: **survivorship bias.** Delisted-stock price history
is the paywalled part of the data world. You cannot fully eliminate survivorship bias for free.

The correctness-first response is itself free and is a *methodology* decision, not a purchase:

1. **Constrain the backtest universe to liquid large-caps**, where in-sample delisting is
   approximately zero (a mega-cap almost never delists mid-sample), so survivorship bias is
   negligible — and **state the bound explicitly**.
2. Optionally ingest **free point-in-time S&P constituent history** (community datasets on GitHub /
   Wikipedia revision history) to get *selection*-bias-free membership, accepting that delisted
   *price* series remain unavailable for free.
3. **Never make broad-universe claims** the free data cannot support.

This is the principled free answer: bound the claim to what the data can honestly support, rather
than silently backtesting today's winners and calling it edge.

---

## 2. Reference architecture — the exhaustive component map

What a top-tier US-equities systematic trading system needs, by pillar. Each is graded in §3.

### 2.1 Data & universe layer
- **Price data:** survivorship-free, point-in-time, split/dividend-adjusted *and* unadjusted
  (as-of adjustment — fully-adjusted series are themselves forward-looking).
- **Point-in-time universe / index membership:** which tickers were tradeable *on each date*.
- **Corporate actions:** splits, dividends, symbol changes, M&A, spin-offs.
- **Point-in-time fundamentals:** earnings, revenue, balance-sheet items, lagged to filing date to
  prevent lookahead.
- **Event calendar:** earnings dates, dividend ex-dates, index reconstitution.
- **Classification:** GICS sector/industry for neutralization.
- **Reference / alt data:** short interest & borrow availability, options-implied vol, analyst
  estimates, macro (rates, VIX).
- **Market-structure facts:** RTH 09:30–16:00 ET, pre/post market, LULD halts, open/close
  auctions, VWAP, **T+1 settlement**.

### 2.2 Universe & instrument modeling
- Tradeable-universe definition with liquidity filters (ADV, price floor, market-cap floor).
- Borrow availability + hard-to-borrow cost for the short book.
- Sector/industry/factor classification for neutralization and attribution.

### 2.3 Alpha / signal research
- **Cross-sectional factor alphas:** value, momentum (12-1), quality, low-vol, size, short-term
  reversal.
- **Event-driven:** post-earnings-announcement drift (PEAD), gap fades, index-add/drop.
- **Statistical arbitrage:** pairs / cointegration (note: `smt_divergence` was removed at fork).
- **Microstructure** (later / data-permitting).
- **Unified expected-return forecast:** a layer that blends many weak signals into one alpha
  forecast per name — currently absent (each strategy fires independently).
- Feature store, **alpha-decay monitoring**, orthogonalization against known factors.

### 2.4 Risk model (the largest structural hole)
- **Factor risk model:** Barra-style or **statistical PCA** decomposition into factor exposures +
  specific risk.
- **Covariance estimation:** shrinkage (Ledoit-Wolf) on the universe return matrix.
- **Risk budgeting:** portfolio VaR/vol target, factor-exposure limits, beta/sector neutrality,
  concentration limits, correlation-aware sizing.

### 2.5 Portfolio construction (the "alert bot → system" leap)
- Translate the alpha forecast into holdings: mean-variance (Markowitz), risk-parity, or
  rank-and-weight long/short.
- **Constraints:** gross/net exposure, sector/beta neutrality, position caps, turnover penalty,
  **transaction-cost-aware** objective.

### 2.6 Transaction-cost model & execution
- **Cost model:** commission, half-spread by liquidity bucket, **square-root market impact**, short
  **borrow cost**, slippage. (Crypto fee assumptions ≠ equities.)
- **Execution:** order types (limit, MOC/LOC for the close auction, VWAP/TWAP), smart routing,
  auction dynamics; broker integration (IBKR / Alpaca).
- **Fill modeling in backtest:** next-bar-open fills, no same-bar lookahead, partial fills,
  liquidity caps.

### 2.7 Backtesting & simulation rigor
- **Portfolio-level, point-in-time, cross-sectional** simulator (vs. today's single-instrument
  event replay).
- **Overfitting controls:** deflated Sharpe ratio, Probability of Backtest Overfitting (PBO),
  purged + embargoed cross-validation, trial-count tracking → multiple-testing haircut.
- Survivorship-free universe, correct corporate-action handling, T+1 settlement, realistic costs.

### 2.8 Performance & risk analytics
- **Factor-attributed performance:** how much return is just beta/momentum exposure vs. true alpha.
- Sharpe/Sortino/Calmar with confidence intervals, max-DD distribution, turnover, capacity, tail
  risk, hit-rate vs. payoff.

### 2.9 Live trading — OMS / EMS (Phase B)
- Order management, position reconciliation, fills, real-time P&L, state recovery, idempotency,
  kill-switches, pre-trade risk checks.

### 2.10 Capital & money management
- Position sizing: fractional Kelly, **vol-targeting** to a portfolio vol budget, drawdown control.
  (Currently fixed `sl_pct` per symbol — no portfolio vol budget.)

### 2.11 Regulatory / market-structure realities
- PDT rule (<$25k), Reg-T margin, short-sale uptick / SSR, locate requirements, wash-sale &
  tax-lot accounting, T+1 settlement.

### 2.12 Governance & ops
- Trial registry, walk-forward deployment log, alpha-decay alerts, data-quality monitors, model
  governance, audit trail. (Existing signal daemon + Telegram + Stats UI are good *alerting* bones,
  not trading governance.)

---

## 3. Gap analysis — have vs. ideal

Severity: **S0** = blocks trustworthy results · **S1** = major edge/realism gap · **S2** = needed for
"system" status · **S3** = Phase-B / nice-to-have.

| # | Component | What exists today | Severity | Free-fixable? |
|---|---|---|---|---|
| 1 | Survivorship-free PIT universe | yfinance = current listings only; 13 current mega-caps | **S0** | Partially — constrain universe + free PIT constituents; delisted prices not free |
| 2 | Lookahead/leakage hygiene | Live-parity gate replay exists; no formal audit; split-adjustment lookahead unexamined | **S0** | Yes (methodology) |
| 3 | Overfitting controls | WFO (`param_sweep`) exists; **no** deflated Sharpe / PBO / purged CV; tp_r sweeps untracked | **S0** | Yes (methodology) |
| 4 | Realistic cost model | Backtest applies fees; crypto-style; no spread/impact/borrow | **S0** | Yes (modeling) |
| 5 | Factor-attributed performance | Win-rate / avg-R only | **S1** | Yes (Ken French free) |
| 6 | Cross-sectional alpha | ~17 single-name candlestick/structure patterns | **S1** | Yes |
| 7 | Unified expected-return forecast | None (independent strategies) | **S1** | Yes |
| 8 | Factor risk model + covariance | None | **S2** | Yes (sklearn/PCA) |
| 9 | Portfolio construction / optimizer | None — alert-per-signal | **S2** | Yes (cvxpy) |
| 10 | Vol-targeting / Kelly sizing | Fixed `sl_pct` per symbol | **S2** | Yes |
| 11 | Portfolio-level backtester | Single-instrument event replay per (symbol, tf, strategy) | **S2** | Yes |
| 12 | PIT fundamentals / earnings | None | **S1** | Yes (SEC EDGAR free, true PIT) |
| 13 | Borrow / short-availability | None | **S2** | Limited free — constrains L/S path |
| 14 | OMS / EMS / execution | Phase B deferred (`trade/open_trades.py` = legacy Binance) | **S3** | Yes (Alpaca/IBKR free tier) |
| 15 | Regulatory realities (PDT/SSR/T+1) | None | **S3** | Yes (rules in code) |
| 16 | Governance (trial registry, decay) | Outcome ledger + Stats UI (alerting) | **S2** | Yes |

### 3.1 What is genuinely strong — keep and lean on
- DuckDB analytics store (`analytics/store/`), backtest harness (`analytics/backtest/`).
- WFO infrastructure (`analytics/param_sweep.py`, `tools/multi_symbol_wfo.py`) — reuse for §0.3.
- **Regime classifier** (`analytics/regime.py`) — fold into a risk-on/off overlay.
- **Live-parity gate replay** (`LiveParityConfig`) — the discipline to make backtests match live.
- Signal daemon + Telegram alerting + `signal_alert_outcomes` ledger + `outcome_backfill.py`.
- Stats UI + live-outcomes card — the reporting surface for new analytics.

The gap is *methodology*, not *engineering*. The roadmap reuses this plumbing wherever possible.

---

## 4. Phased roadmap (ordered: correctness → edge → portfolio → execution)

Each phase is sequenced so that completing it makes the next phase's numbers trustworthy. Every item
is achievable on free data. Each phase will get its own spec + plan when started.

### Phase 0 — "Stop fooling myself" (correctness foundation)
*Until this is done, no backtest number is believable.*

- **0.1 Universe-as-of-date policy.** Introduce a tradeable-universe concept keyed by date. Default:
  liquid large-caps where in-sample delisting ≈ 0. Optionally ingest free PIT S&P-500 constituent
  history. Document the survivorship bound in the backtest output. *DoD:* backtests declare their
  universe-as-of policy; no silent use of "today's tickers".
- **0.2 Lookahead / leakage audit.** Systematic sweep of the pipeline for forward-looking data:
  detector bar indexing (`iloc[-1]` vs `[-2]`), regime-gate timing, any statistic computed over the
  full series, and **split/dividend-adjustment lookahead** (use as-of adjustment, not fully-adjusted
  series). *DoD:* documented audit; each finding fixed or explicitly justified.
- **0.3 Overfitting / multiple-testing controls.** Add **deflated Sharpe ratio** (Bailey & López de
  Prado 2014), **PBO** via combinatorially-symmetric CV, and **purged + embargoed K-fold CV**
  (López de Prado, *Advances in Financial ML*). Track trial counts from `param_sweep` so `tp_r`
  winners receive a multiple-testing haircut. *DoD:* every committed `tp_r` carries a deflated-Sharpe
  and PBO figure; sweeps log trial counts.
- **0.4 Realistic equity cost model.** Replace crypto fees with: half-spread by liquidity bucket +
  **square-root market impact** + (shorts) borrow cost. Switch backtest fills to **next-bar-open**,
  liquidity-capped, no same-bar lookahead. *DoD:* cost model is a documented, tested module; backtest
  P&L is net of it.
- **0.5 Data-quality monitors.** On ingest: gap / NaN / zero-volume / extreme-return sentinels;
  corporate-action sanity (split detection). *DoD:* ingest emits a data-quality report; bad bars
  quarantined.

### Phase 1 — Honest measurement & attribution
*Is there real edge beyond exposure?*

- **1.1 Factor attribution.** Regress strategy/portfolio returns on the **Ken French Data Library**
  (free, canonical: Mkt-RF, SMB, HML, RMW, CMA, Mom) to separate true alpha from factor exposure.
- **1.2 Proper risk metrics.** Sharpe/Sortino/Calmar **with confidence intervals**, max-DD
  distribution, turnover, capacity estimate, tail metrics — surfaced in the Stats UI.
- **1.3 Null benchmarks.** Compare vs. SPY buy-and-hold and vs. a random-entry null with matched
  exposure. *DoD:* the Stats UI answers "is there alpha after stripping beta/momentum?"

### Phase 2 — Cross-sectional alpha layer (the methodology upgrade)
- **2.1 Daily cross-section.** Build a per-date cross-section of the universe; z-score / winsorize
  signal strengths into ranks.
- **2.2 Free factor + event alphas.** 12-1 momentum, short-term reversal, low-vol; **PEAD** via free
  earnings dates; gap fades; value/quality via **SEC EDGAR** PIT fundamentals.
- **2.3 Alpha combination.** Blend signals into one expected-return forecast per name (IC-weighted or
  ridge), with alpha-decay monitoring. *DoD:* a daily ranked long/short candidate list with a
  scalar expected-return forecast per name.

### Phase 3 — Risk model & portfolio construction
- **3.1 Covariance.** Ledoit-Wolf shrinkage (`sklearn.covariance.LedoitWolf`) on universe returns.
- **3.2 Statistical risk model.** PCA factor decomposition (Barra is paid) → factor exposures +
  specific risk.
- **3.3 Portfolio construction.** Start simple: dollar-neutral L/S, equal- or vol-weighted, sector
  caps. Then optional **cvxpy** mean-variance optimizer with gross/net/turnover/position constraints
  + transaction-cost term.
- **3.4 Sizing.** Vol-targeting to a portfolio vol budget; fractional-Kelly overlay; drawdown
  control. *DoD:* signals → a sized, risk-controlled book.

### Phase 4 — Portfolio-level backtester
- **4.1 Cross-sectional PIT simulator.** Daily-rebalance over the universe using the Phase-0 cost
  model + T+1 settlement; replaces single-name event replay for *portfolio* claims. *DoD:*
  end-to-end backtest of the constructed book with attribution + overfitting controls applied.

### Phase 5 — Live execution / OMS (Phase B, deferred)
- Alpaca/IBKR paper → live; MOC/LOC auction orders; OMS + reconciliation + kill-switch + pre-trade
  risk checks; PDT / Reg-T / SSR / T+1 handling. *In scope as a roadmap item; not started until
  Phases 0–4 give a book worth executing.*

### Cross-cutting (runs alongside all phases)
- **Regime overlay:** fold `analytics/regime.py` into a risk-on/off exposure scaler.
- **Governance:** trial registry feeding the §0.3 haircut; walk-forward deployment log; alpha-decay
  alerts on top of the existing outcome ledger.

---

## 5. Free-data sourcing appendix

| Need | Free source | Notes |
|---|---|---|
| Adjusted + unadjusted prices | yfinance (primary), **Stooq** / **Alpaca IEX free** (backup/cross-check) | Cross-check guards against silent yfinance adjustment errors |
| **PIT fundamentals** | **SEC EDGAR** (filing date = true point-in-time) | The free PIT win; lag to filing date |
| **Canonical factors** | **Ken French Data Library** | Gold standard for §1.1 attribution; free |
| Corporate actions / splits / divs | yfinance, Nasdaq | Validate via §0.5 split detection |
| Earnings dates | yfinance / Nasdaq / Finnhub free tier | For PEAD (§2.2) |
| PIT index membership | Community S&P-500 constituent datasets (GitHub / Wikipedia history) | Selection-bias fix; delisted *prices* still not free |
| Macro / volatility | FRED, CBOE (VIX) | Regime overlay, risk-on/off |
| Borrow / short availability | Very limited free | **Real constraint** on the L/S path — note honestly |
| Paper/live execution | Alpaca (free, IEX + paper), IBKR | Phase 5 |

---

## 6. Decisions deferred (revisit before cutting Phase specs)
- **End-state ambition** (full cross-sectional L/S vs. upgraded discretionary assistant vs. hybrid
  ranked watchlist) — intentionally deferred; this roadmap is direction-agnostic through Phase 2 and
  only forks at Phase 3 (how aggressive the portfolio engine is).
- **Phase 5 inclusion** — parked until a book exists.
- **Whether to invest effort scraping free PIT constituents** (0.1 option) vs. simply bounding the
  universe to large-caps. Default = bound the universe; revisit if broad-universe claims become
  necessary.

---

## 7. What stays untouched
DuckDB store, WFO infra, signal daemon + alerting, `signal_alert_outcomes` ledger, Stats UI,
live-parity gate replay, regime classifier. These are reused, not rebuilt.
