# Buibui Wifey Wall Street Bot — System Overview

**Purpose.** Provide a self-contained mental model of the live signal pipeline so an outside reviewer (ChatGPT, Gemini, a trading-savvy friend) can assess: *given the current architecture, what is missing for this system to be measurably profitable?*

**Audience.**

1. The maintainer — to keep a coherent mental model across long-running development.
2. External LLM reviewers — see [§9 External-AI briefing prompt](#9-external-ai-briefing-prompt) for the self-contained copy-paste.

**Last updated.** 2026-05-13 (post PR #364 — F9 trio closed, `liquidity_sweep 1h` re-enabled).

**Live state in one line.** 20 strategies registered; 16 enabled on `signal_watch.toml` (tue_thu); F8 HTF EMA gate hard; regime gate soft; **measured live edge: +0.089R on one cell (`liquidity_sweep 1h`)**; `bos` (87 % of live trade volume) remains net-negative across every F9 cell.

---

## Table of contents

1. [System architecture (data flow)](#1-system-architecture-data-flow)
2. [Active gate chain (ordered)](#2-active-gate-chain-ordered)
3. [Strategy inventory (live state)](#3-strategy-inventory-live-state)
4. [Current measured edge](#4-current-measured-edge)
5. [Known issues & open hypotheses](#5-known-issues--open-hypotheses)
6. [Profitability gaps](#6-profitability-gaps)
7. [Tooling & evidence trail](#7-tooling--evidence-trail)
8. [Open questions](#8-open-questions)
9. [External-AI briefing prompt](#9-external-ai-briefing-prompt)

---

## 1. System architecture (data flow)

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Binance Futures REST + WebSocket                                            │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │ klines (OHLCV), funding (unwired), OI (unwired)
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ analytics/data_sync.py  (incremental)  ·  data_fetcher.py  (backfill)       │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │ upsert
                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ DuckDB  (analytics.db)                                                      │
│  ohlcv · signals · backtest_runs · backtest_trades · backtest_combos        │
│  backtest_cross_tf_combos · backtest_cache · confidence_ratings · stats_*   │
└────────────────┬───────────────────────────────────────┬────────────────────┘
                 │ read                                  │ read
                 ▼                                       ▼
┌─────────────────────────┐                ┌──────────────────────────────────┐
│ Live signal daemon      │                │ FastAPI + Svelte web UI          │
│ (signal_runner.py)      │                │ (web/api/ + web/ui/)             │
│  ↓ run_scan_cycle       │                │ Chart · Backtest · SignalFeed    │
│  ↓ scan_symbol/tf       │                │ Positions · Prices · Stats       │
│  ↓ detect_<strategy>    │                └──────────────────────────────────┘
│  ↓ Phase 3 gate chain   │
│  ↓ Telegram + DB write  │
└─────────────────────────┘
```

**CLI surface** (single entry `wifey.py`):

- `buibui analytics backfill | sync` — ingestion
- `buibui signal watch` — live daemon
- `buibui signal test` — historical replay (no DB writes)
- `buibui backtest` — manual + sweep + combo + cross-TF modes
- `buibui param-audit | param-sweep` — WFO Phase 1 + Phase 2
- `buibui recalibrate` — refresh star ratings
- `buibui web` — start FastAPI

**Detection lives in `analytics/strategies/<name>.py`**. The `signals/` package only handles alert dispatch + dedup. The split is deliberate: detection is testable and reusable across live + replay + backtest; alerting is side-effectful and lives behind a thin facade.

---

## 2. Active gate chain (ordered)

This is the production ordering inside `run_scan_cycle` (`analytics/signal/scanner.py:467+`, Phase 3). Every signal that reaches Telegram has survived **all** gates below.

| Step | Gate | Mode | What it does | Where | Known issues |
| ------ | ------ | ------ | -------------- | ------- | -------------- |
| **Pre-fetch** | OHLCV + HTF EMA slope cache + regime cache | — | Phase 1: pre-computes per-cycle: 4h regime per symbol; HTF EMA slope per (symbol, tf, period). Cache miss falls open. | `scanner.py` Phase 1 (~L335) | — |
| **Detect** | `detect_<strategy>` | — | Phase 2: fan-out per (symbol, tf). 20 strategies registered, 16 enabled on `signal_watch.toml`. | `analytics/strategies/*.py` | — |
| **0** | ATR-as-min-SL floor (F9) | Per-cell opt-in | Phase 3 first. Widens `sl_price` to `max(structural_dist, atr_mult × ATR14)` when `atr_sl_floor=true`. TP recomputed to preserve R:R. | `analytics/signal/atr_floor.py` | Currently enabled only on `liquidity_sweep 1h` (PR #364). Default off. |
| **1** | Conflict resolution | Always on | Drops opposing same-(symbol, tf, candle) long+short. | `scanner.py` ~L490 | — |
| **2** | Cooldown dedup | Always on | Two-layer: candle watermark + per-(symbol, strategy, direction) cooldown timer (3600s). | `signals/cooldown_store.py` | — |
| **3** | Volume gate | Per-strategy opt-in | `volume_suppress`: drop low-vol signal. `volume_spike_boost`: tag spike for boost. Directional variants supported. | `scanner.py` ~L740 | — |
| **4** | **Bias: regime gate (Step −1)** | **SOFT** | Drops signals whose strategy type is not in `enabled_regimes[type]` for the current 4h regime. Per-strategy override: `bos→[high_vol, range]` (PR #366 routing-audit flip). | `analytics/signal/gates.py::_apply_regime_gate` | §6 mapping was empirically wrong for `bos` (PR #351 replay verdict "DO NOT FLIP"); PR #366 flip + replay now reads "FLIP justified". Soft mode pending ≥2 weeks live evidence. |
| **4.5** | **Bias: direction filter (Step −0.5)** | **SOFT** | Per-strategy directional suppress — drops signals matching `[strategy_params.<name>].suppress_long` / `.suppress_short`. Cheapest filter; no HTF / regime data. Production: `bos.suppress_long = true` (T2c). | `analytics/signal/gates.py::_apply_direction_filter_gate` | Replay (PR #367): bos longs −0.27R / n=34,767 suppressed; shorts +0.14R / n=37,876 kept. Soft mode pending ≥2 weeks live evidence. |
| **5** | **Bias: F8 HTF EMA (Step 0)** | **HARD** | Drops signals against HTF EMA-50 slope direction. Default anchor 4h; 1d override for `ema/smt_divergence/cvd_divergence/orb/eqh_eql/marubozu`. Deadband 0.3% lets HTF chop pass. | `analytics/signal/gates.py::_apply_htf_ema_gate` | Validated +0.074 lift on tue_thu in soft mode 2026-05-05 before flip. |
| **6** | Bias: ADR gate (Step 1) | Soft suppress | When 4h ADR consumed ≥ 0.80, suppress signals in the prevailing day-move direction (LONG if up-day). `adr_exempt=true` per-strategy bypasses (bos, eqh_eql). | `scanner.py` ~L850 | — |
| **7** | Bias: DOW soft suppress (Step 2) | Soft | Suppresses signals on days with strong empirical drift (`dow_soft_suppress`). | `scanner.py` ~L893 | — |
| **8** | EV gate (backtest min_avg_r) | HARD | Looks up the just-computed backtest run for this (symbol, strategy, tf, direction) and drops if `avg_r < min_avg_r` (=0.0 in prod). Floor-aware via `_backtest_run_id` cache key. | `scanner.py::_passes_ev_gate` ~L706 | This is the **runtime quality gate** that suppresses ETH 1h `liquidity_sweep` and BTC 4h `smt_divergence`. |
| **9** | Co-fire confluence (same-TF, D10 step 3) | Tag-only | If a known-good combo from `backtest_combos` fired within `window=5` candles + `min_avg_r=1.0` → append confluence blockquote. | `scanner.py` ~L1020, `analytics/signal/cofire.py` | Viable count dropped 28→13 (2026-04-22 → 2026-05-11). Market-driven sparsity, confirmed. |
| **10** | Co-fire confluence (cross-TF, D10 step 4) | Tag-only | Same as 9 but HTF→LTF, `cross_tf_window_hours=8.0`. | `scanner.py` ~L1040 | — |
| **11** | Telegram dispatch + DB persist | — | One alert per surviving signal. | `signals/alert_formatter.py`, `signal_runner.py` | No outcome write-back yet — see [§6](#6-profitability-gaps). |

**Notes on the chain.**

- Phase 1 caches are **per-cycle**, not per-symbol — so the regime gate is consistent within a cycle.
- F8 + regime are both **directional**: regime decides *whether the strategy type fires at all in this regime*; F8 decides *which direction*.
- The EV gate is the only gate that uses *backtest evidence*, not rule logic. Everything before it is rule-based.

---

## 3. Strategy inventory (live state)

Source: `config/signal_watch.toml` × `analytics/strategies/_registry.py`. Day filter: `tue_thu`. Day filter is enforced inside the backtest engine, not the live scanner — but the production config restricts alerts via `day_filter`.

Symbols watched (default): all from `config/coins.json`. Common: BTCUSDT, ETHUSDT, SOLUSDT.

| Strategy | Type | TFs enabled | adr_exempt | volume_suppress | Notes |
| ---------- | ------ | ------------- | ------------ | ----------------- | ------- |
| `bos` | structural | 15m, 1h, 4h | yes | yes (long-side) | 1d dropped (-0.785R). 87 % of live trade volume. **Net-negative across all F9 cells.** |
| `orb` | session | 1h, 4h, 1d | — | yes | 15m dropped (-0.163R). |
| `liquidity_sweep` | flow | **1h**, 1d | — | yes | **1h re-enabled 2026-05-13 with F9 floor** — BTC tp_r=1.5, SOL tp_r=1.0, ETH suppressed by EV gate. |
| `smt_divergence` | flow | 15m, 1h, 1d | yes | yes | 4h dropped (-0.307R). BTC 4h suppressed by EV gate. |
| `eqh_eql` | structural | 1h, 1d | yes | — | 4h dropped (-0.217R). |
| `order_block` | structural | 1d | — | — | 4h dropped (CONFLUENCE_ONLY). |
| `cvd_divergence` | flow | 1h | yes | no | Cleanest single cell: +1.38R @ tp_r=5.0, 41% WR, n=38. |
| `trend_day` | trend | 4h, 1d | — | — | 15m dropped. |
| `engulfing` | candlestick | 15m, 1h, 4h | — | long-side suppress | A15: spike boost on. |
| `pin_bar` | candlestick | 15m, 1h, 4h, 1d | — | no (low-vol edge) | tp_r_long=5.0, tp_r_short=3.0. |
| `inside_bar` | candlestick | (all) | — | — | tp_r_long=4.0, tp_r_short=2.0. |
| `hammer_hanging_man` | candlestick | (all) | — | no | — |
| `doji` | candlestick | 15m, 1h | — | yes | 4h/1d degraded. |
| `morning_evening_star` | candlestick | 15m, 1h, 4h | — | no | tp_r_long=4.0, tp_r_short=3.0. |
| `ema` | trend | (all) | — | — | New strategy (PR #342). Only ETH 1h/tue_thu @ tp_r=5.0 has WFO edge. |
| `wick_fill` | structural | — | — | — | **Excluded from current config** — all TFs negative pending BOS confluence fix. |
| `fvg` | structural | — | — | — | **Phase 1 cut 2026-05-07** — DROP_TF on every TF. |
| `marubozu` | candlestick | — | — | no | **Phase 1 cut 2026-05-07** — DROP_TF on every TF. |
| `ote_entry` | fib | — | — | — | Registered, never enabled. Audit 2026-05-11: no-edge across all cells. |
| `seasonality` | — | — | — | — | Not a detector — analytic only. |

There is one sister config:

- `signal_watch_weekdays.toml` — Mon–Fri
- Both inherit from `config/strategy_params.toml` (base — F8/regime/combo/backtest config).

---

## 4. Current measured edge

### 4a. Backtest aggregates (200d window, since=2025-09-12, tue_thu)

| Source | Result |
| -------- | -------- |
| Phase 0 edge audit (`tools/strategy_edge_audit.py`, 698,758 trades, 2026-05-07) | **0 KILL, 0 DEMOTE, 19 KEEP**. Broad-cut hypothesis from redesign §3 contradicted by data. |
| Phase 1 TOML cuts (marubozu + fvg removed, eqh/smt/liq/ob tightened) | **+0.0282R lift on `signal_watch_weekdays.toml`** (+67 % relative on −0.042R base). signal_watch.toml tue_thu +0.0006R (noise — already optimised). |
| F9 floor sweep (2026-05-11) | 13/18 cells move; 7 improve >+0.05R; 5 improve >+0.10R. **No cell turns net-positive** — floor reduces losses, doesn't rescue absent edge. |
| F9 joint sweep (2026-05-12) | Only `liquidity_sweep 1h @ 2.5×` net-positive (+0.107R agg n=152 @ tp_r=1.0). Re-audited 2026-05-13: aggregate decayed to +0.089R but still above +0.05R bar. |

### 4b. Live cell summary (post PR #364)

| Cell | OOS avg_r | n | Note |
| ------ | ----------- | --- | ------ |
| BTC `liquidity_sweep 1h` @ tp_r=1.5 | **+0.213R** | 51 | First production cell with F9 floor on. |
| SOL `liquidity_sweep 1h` @ tp_r=1.0 | **+0.133R** | 51 | Cross-symbol default. |
| ETH `liquidity_sweep 1h` | −0.068R | 53 | Suppressed by EV gate (`min_avg_r=0.0`). |
| BTC `smt_divergence 4h` | −0.58R, 15.1% WR | 53 | Suppressed by EV gate. |
| `bos` (all TFs) | **Net-negative across every F9 cell** | — | 87 % of live trade volume. Not fixable via SL sizing. |

### 4c. Combo viability (D10)

| Table | Total rows | Viable (live gate thresholds) | Trend |
| ------- | ----------- | ------------------------------- | ------- |
| `backtest_combos` (same-TF) | 4,433 | 13 (tue_thu + avg_r≥1.0 + n≥5) | Down from 28 (2026-04-22). Market sparsity confirmed. |
| `backtest_cross_tf_combos` | 31,563 | 3,068 (tue_thu + avg_r≥0.0 + n≥5) | Down from 3,644. |

Confluence blockquote sparsity in production is real, not stale-data. Spot-check via `tools/combo_health.py`.

---

## 5. Known issues & open hypotheses

1. **Regime gate is inverted in production for the live trade mix** (replay PR #351, 2026-05-10).
   - Suppressed n=40,606 avg_r **+0.0285** (= would-be winners we'd drop)
   - Kept n=43,416 avg_r **−0.1293** (= would-be losers we'd keep)
   - Three follow-ups evaluated: (a) per-strategy mapping flip for `fib_golden_zone` — shipped PR #354. (b) global slope-threshold sweep — partial validation for `bos` only, hurts `fib_golden_zone`, ruled out. (c) **unified `SignalCandidate` re-derivation** — recommended endgame (redesign §4); not started.
   - Soft mode stays indefinitely.
2. **`bos` is 87 % of trade volume AND net-negative.** Root cause is the v1 `setup_type → regime` taxonomy treating all `bos` fires as one cell. F9 sizing fix does not move it. Unified `SignalCandidate` is the path.
3. **F9 floor doesn't rescue absent edge.** Best multipliers cluster 2.0–2.5× — structural SLs are systematically too tight, but only one cell (`liquidity_sweep 1h`) crosses the +0.05R bar even with the floor on.
4. **`min_avg_r` filter ordering bug.** EV gate currently runs **before** combo confluence — a signal can fail the EV gate even though a confluence tag would have flipped it positive. 3 fix options drafted, deferred until directional distribution data is collected.
5. **`backtest_combos` market sparsity is real.** Combo refresh 2026-05-11 confirmed: same-TF viable dropped 28→13 due to market conditions + F8 trim, not stale data.
6. **No outcome write-back.** `signals` table records the alert but no automated job scans subsequent OHLCV to write `outcome` (TP-hit / SL-hit / open). **Live edge is currently only knowable through backtest replay, not actual taken trades.** This is the largest single profitability gap — see §6.
7. **Strategies registered but providing no edge:** `wick_fill` (excluded), `fvg` + `marubozu` (Phase 1 cut), `ote_entry` (no-edge audit closed).
8. **EMA detector v2 hypotheses falsified** (2026-05-06): neither ATR-normalised slope nor pullback-ATR depth separates winners from losers. Future EMA rework would have to be trigger-shape, not gate-shape. ETH 1h tue_thu tp_r=5.0 stays as the lone live cell.

---

## 6. Profitability gaps

These four are the actively unaddressed gaps between *the bot exists* and *the bot makes money*. The MEMORY citation lives at `project_profitability_priorities.md` (memory).

| Gap | Current state | What it blocks | Estimated lift |
| ----- | --------------- | ---------------- | ---------------- |
| **Outcome tracking** | `signals.outcome` column is NULL on all rows; no forward-scan job exists. | Cannot tell if any *live* change (regime flip, F9 cell, new strategy) actually improves PnL on taken trades. All evidence today is backtest-only. | Diagnostic — enables everything else. |
| **Position sizing** | Fixed % per trade (or manual). No Kelly/optimal-f. | Optimally sized losers can still kill the account; under-sized winners leave money on the table. | High — typical 10–50 % improvement on identical signal mix. |
| **Portfolio heat / concurrent risk cap** | No cap on # of open positions or correlated risk. | A correlated-asset crash with multiple positions open can blow up an otherwise-profitable signal mix. | Survival-level. |
| **Forward testing** | `buibui signal test` exists for historical replay, no paper-trade harness. | Can't validate a regime/strategy change in production without committing it. | Medium — accelerates iteration cycle. |

---

## 7. Tooling & evidence trail

| Tool | Purpose | Last result |
| ------ | --------- | ------------- |
| `make buibui-backtest CONFIG=… SAVE=1` | Per-strategy × TF backtest. Writes `backtest_runs` + `backtest_trades`. | 1,179 cells refreshed 2026-05-11. |
| `tools/strategy_edge_audit.py` | Phase 0 KILL/DEMOTE/KEEP rule across (strategy × tf × regime × session). | 0/0/19 on 698,758 trades. |
| `tools/regime_gate_replay.py` | Join `backtest_trades` × `classify_series` to label trades suppressed/kept. | DO NOT FLIP verdict, PR #351. |
| `tools/regime_threshold_sweep.py` | Re-run replay across `_SLOPE_TREND_THRESHOLD` grid. | Partial validation for `bos` only. |
| `tools/direction_filter_replay.py` | Join `backtest_trades` × per-strategy `suppress_long` / `suppress_short` flags to label trades suppressed/kept. | "FLIP justified" verdict for `bos.suppress_long`, PR #367 (n=72,643; suppressed −0.268R, kept +0.140R). |
| `tools/combo_health.py` | Spot-check `backtest_combos` + `backtest_cross_tf_combos` health. | 13 same-TF + 3,068 cross-TF viable rows post-refresh. |
| `buibui param-audit` (WFO Phase 1) | Strategy×TF×day_filter audit with OOS hold-out. | Per-strategy results in `project_*_findings.md`. |
| `buibui param-sweep` (WFO Phase 2) | Grid sweep across `tp_r` (now also `--atr-sl-floor` / `--atr-sl-multiplier`). | Same. |
| `buibui recalibrate` | Refresh `confidence_ratings` star ratings from accumulated backtest runs. | After every `SAVE=1` run. |
| `buibui signal test --at <ts> --lookback <h>` | Historical replay against detectors. | Ad-hoc per investigation. |

---

## 8. Open questions

These are the questions an external reviewer is best positioned to challenge:

1. **Is the v1 `setup_type` taxonomy salvageable, or is the unified `SignalCandidate` model (redesign §4) the only path?** The replay says the taxonomy is broken; the threshold sweep says no global knob fixes it; PR #354 shows per-strategy mapping can patch one strategy at a time. Is a full rewrite the right move, or is per-strategy mapping enough?
2. **Should we ship outcome tracking before any more strategy or gate changes?** Building more without measurement is the trap we keep falling into. But outcome tracking takes time and the regime fix is the biggest known lever.
3. **Is `bos` salvageable at all?** It's 87% of volume and net-negative everywhere. Is the right move to (a) deeply re-engineer it, (b) cut it entirely and lose 87% of trade volume (most of which is currently losing money anyway), or (c) leave it gated by regime until §4 lands?
4. **Have we under-explored confluence?** Same-TF viable count is 13 cells. Cross-TF is 3,068 cells. Are we filtering correctly, or is the `min_avg_r=1.0` threshold too strict?
5. **What's the right next strategy to add?** D18 `smt_sweep` has the cleanest scope. D10 confluence step 5 (orderflow signals) is the highest-impact but blocked on CoinGlass API. D14 (AMD cycle) is meta-context, not a detector.
6. **Position sizing — Kelly half? Fixed fractional? Risk parity?** No work has been done here yet. What's the right starting point for a 3-symbol crypto bot with the current signal mix?
7. **Is paper trading via Binance testnet enough, or do we need real forward-test on micro-size?** The latter has psychological + execution feedback the former doesn't. But it costs real money.

---

## 9. External-AI briefing prompt

> **Copy from here to the end and paste into ChatGPT / Gemini / Claude.ai when you want an outside read.**

```text
I'm building a crypto trading bot for Binance Futures (3 symbols: BTCUSDT,
ETHUSDT, SOLUSDT; 4 timeframes: 15m / 1h / 4h / 1d). It has:

- 20 strategies registered, 16 enabled on the production config.
- An 11-step live gate chain: detect → ATR-floor → conflict → dedup
  → volume → regime → HTF-EMA → ADR → DOW → backtest-EV gate → confluence-tag
  → Telegram.
- A full backtest engine, walk-forward optimisation tools, and a 700K-trade
  audit history.

After ~6 months of building, the measured live edge is:

  +0.089R per trade on ONE cell (`liquidity_sweep 1h`).
  `bos` (= 87% of live trade volume) is net-negative across every F9 cell
  even with ATR-as-min-SL floor on at 2.5×.
  The regime gate is inverted in production — a backtest replay shows
  suppressed trades avg +0.0285R (would-be winners) vs kept trades −0.1293R
  (would-be losers). Soft mode stays indefinitely while we figure it out.

Key findings to date:

1. Phase 0 audit on 698,758 trades: 0 KILL / 0 DEMOTE / 19 KEEP. The
   broad-cut hypothesis was contradicted by data.
2. Phase 1 TOML cuts (remove broadly-negative cells): +0.0282R on the
   weekdays config; noise on tue_thu.
3. ATR-as-min-SL floor (F9): reduces losses but does not create edge.
   Only `liquidity_sweep 1h` crosses the +0.05R bar.
4. EMA detector v2 hypotheses (ATR-normalised slope, pullback-ATR depth):
   both falsified by post-hoc analysis.
5. Combo confluence viable count crashed 28 → 13 same-TF over 3 weeks;
   market-driven sparsity, not stale data.

The big known gaps that block profitability:

- No outcome tracking on live signals (`signals.outcome` is NULL).
  All evidence is currently backtest-only.
- No position sizing logic (Kelly / fractional / risk parity — none).
- No portfolio heat or concurrent-risk cap.
- No forward-test harness (only historical replay).

The architectural question I'm wrestling with: the regime gate is built on
a v1 `setup_type → allowed_regimes` taxonomy. Backtest replay shows this
taxonomy is wrong for the live trade mix. Three options:

(a) Per-strategy mapping override (already shipped for fib_golden_zone).
(b) Global slope-threshold sweep — investigated, only partially valid.
(c) Unified `SignalCandidate` model: collapse the 4 setup_types, re-derive
    the regime matrix from per-strategy edge data, replace the v1 taxonomy.
    Multi-step branch.

My question to you: **given everything above, what's the single highest-impact
thing I should do next to make this system measurably profitable?**

Constraints: I have one developer (me), a few weeks of runway, and the bot
must run on Binance Futures with no exotic data feeds (CoinGlass would
require a paid subscription). Assume the codebase is well-tested and
modifiable.

Not looking for generic advice (don't tell me to "diversify" or
"backtest more"). I want a pointed assessment of which gap (regime
mapping, outcome tracking, position sizing, a specific new strategy, or
something I'm not seeing) is the highest-leverage move from here.
```

---

## Doc maintenance

- Update §2 when the gate chain changes (add/remove a gate or flip mode).
- Update §3 when a strategy is added/removed or its TFs change.
- Update §4a when a new `make db-update` lands.
- Update §4b when a new live cell turns net-positive or a cell flips negative.
- Update §5/§6 when an issue is closed or a gap is filled.
- Sync the line count of the live edge claim ("+0.089R on one cell") to §4b after each backtest refresh.

The file is intentionally self-contained — no internal `[[link]]`s. The audience includes external LLMs that won't have the repo loaded.
