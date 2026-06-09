# Phase 0.2 — Lookahead / Leakage Audit

**Date:** 2026-06-09

**Deliverable:** Phase 0.2 of
`docs/superpowers/specs/2026-06-05-phase0-correctness-foundation-design.md` §4.2

**Guard:** `tests/test_lookahead.py` (truncated-series causality harness; all 16
detectors + the backtest entry path)

## Method

Every place the pipeline reads historical OHLCV is enumerated and classified:

- **causal** — the value at bar `t` depends only on bars with `open_time <= t`.
  Proven empirically by the harness where applicable.
- **justified** — reads "future" bars, but only to resolve the *realized*
  outcome of an already-committed decision (not to make the decision).
  Acceptable.
- **leaking** — depends on bars after the decision bar. Must be fixed (flag any
  golden move).

The harness re-runs each detector on the series truncated at every emitted
signal's `open_time` `t` (sampled up to 25 points per detector, always including
the first and last) and asserts the set of signals stamped at `t` is identical
to the full-series run. A meta-test (`test_harness_catches_injected_lookahead`)
proves the harness has teeth by flagging a deliberately peeking detector.

## Read-site classification

| # | Read site | File : function | Class | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Detector signal emission (all 16 detectors) | `analytics/strategies/*.py` via `DETECTOR_REGISTRY` | causal | Harness `test_detector_has_no_lookahead` green across 4h/1d/1wk for every detector. Forward-scan detectors (`wick_fill`, `fvg`, `eqh_eql`, `order_block`, `marubozu`) emit on the *fill/retest* bar `j` and read only bars `i..j` (all `<= j`). |
| 2 | `bos` (break-of-structure) signal emission | `analytics/strategies/market_structure.py : detect_market_structure` | causal (was leaking; **fixed** 2026-06-09) | Centered rolling window (`2*swing_lookback+1`, `center=True`) confirms a swing at bar `i` using `swing_lookback` bars *after* `i`. The signal `open_time` is now stamped at the confirmation bar `i + swing_lookback` (swing *price* still from bar `i`), so the emission decision is causal. Harness green on all 3 TFs. See Findings. |
| 3 | Volume confirmation rolling mean | `analytics/strategies/_shared.py : volume_confirm` | causal | Trailing window (`center=False`); the signal bar's volume vs the prior rolling mean. |
| 4 | EMA / slope | `analytics/strategies/_shared.py : compute_ema`, `ema_cross_count` | causal | `ewm` value at `i` is a weighted sum of bars `<= i`; truncating future bars cannot change it. |
| 5 | Backtest entry fill | `analytics/backtest/engine.py : run_backtest` (`entry_idx = sig_idx + 1; entry_price = opens_np[entry_idx]`) | causal | Harness `test_backtest_entry_is_strictly_next_bar_open` + `test_backtest_entry_independent_of_future_bars`. Next-bar-open. |
| 6 | Backtest exit resolution | `analytics/backtest/engine.py : run_backtest` (`highs_np[entry_idx:]`, `lows_np[entry_idx:]`) | justified | Walks forward to realize SL/TP of an already-placed trade — outcome, not decision. |
| 7 | ATR-based SL / floor | `analytics/backtest/engine.py : _compute_atr14(.., sig_idx)`; `analytics/signal/atr_floor.py : _apply_atr_floor` | causal | Trailing 14 bars ending at `sig_idx`. |
| 8 | Regime gate (live + replay) | `analytics/signal/gates.py : _apply_regime_gate`; `analytics/backtest/engine.py : _resolve_regime_at` | causal | Live `iloc[-2]` (last *closed* HTF bar); replay mirrors it via `searchsorted` for the largest HTF `open_time <= signal_time`. |
| 9 | HTF-EMA slope gate | `analytics/signal/gates.py : _apply_htf_ema_gate`; `analytics/backtest/engine.py : _resolve_series_at` | causal | Same `<= signal_time` searchsorted lookup as #8. |
| 10 | Live forming-bar exclusion | `analytics/signal/scanner.py : scan_symbol` | causal | Forming bar excluded by wall-clock `now < open_time + tf_ms` (go-live fix #67); catch-up path keeps own-candle entry. Covered by `tests/test_catch_up.py`. |
| 11 | Price-adjustment basis | `utils/yfinance_client.py : fetch_history` (`auto_adjust=False, actions=False`) | justified (bounded) | Raw close; dividends not back-adjusted; **splits back-applied** by yfinance = mild as-of violation, small on mega-caps, out of free-data scope to fix. Guarded by `tests/test_yfinance_client.py`. |
| 12 | Descriptive stats / seasonality | `analytics/stats/*`, `analytics/strategies/_seasonality.py` | context-only | Computed over the full series for alert *display* via `analytics/signal/stats_context.py`, never as a gate. Verified 2026-06-09: `analytics/signal/gates.py` imports only `regime`, `SignalEvent`, `BiasConfig`, `StrategyOverride`, `STRATEGY_REGISTRY` — no `analytics.stats` / `_seasonality` dependency. No stat feeds a signal-suppression decision. |

## Findings

One real leak was discovered by the Task 1 harness; it has since been fixed and
the rest of the pipeline is causal.

### `bos` (break-of-structure) — confirmed lookahead, now fixed (2026-06-09)

- **Mechanism.** `detect_market_structure` flags swing highs/lows with a
  *centered* rolling window: `window = 2 * swing_lookback + 1` and
  `center=True` (`market_structure.py:38-46`). A swing high at bar `i` is
  `high[i] == max(high[i-swing_lookback .. i+swing_lookback])`, so the
  confirmation reads `swing_lookback` (default 5) bars *after* `i`. The emitted
  BOS/CHoCH signal was originally stamped at the swing bar's own `open_time`,
  so the decision to emit at `open_time` `t` depended on bars after `t`.
- **Detection.** `tests/test_lookahead.py::test_detector_has_no_lookahead`
  flagged `bos` on all three timeframes (4h/1d/1wk): the signal at the
  truncation boundary vanished once the future confirmation bars were removed.
- **Live impact (pre-fix).** In live mode the last closed candle can never be a
  confirmed swing (it needs `swing_lookback` more bars), so live `bos` only
  fired once a swing was already `swing_lookback` candles in the past — while
  the backtest assumed entry at `swing_bar + 1`'s open. That backtest-vs-live
  timing divergence is exactly the class of bug Phase 0 exists to surface.
- **Fix (shipped).** The signal `open_time` is now stamped at the *confirmation*
  bar (`row_idx + swing_lookback`) — the bar at which the swing first becomes
  knowable — while the swing *price* level is still taken from bar `row_idx`
  (`market_structure.py`). The centered `min_periods=window` guarantees every
  swing bar satisfies `row_idx <= n-1-swing_lookback`, so the confirmation index
  is always in-bounds; an explicit `confirm_idx >= n` guard keeps the detector
  causal if that invariant ever changes. The harness now passes on all 3 TFs and
  the `_KNOWN_LOOKAHEAD_DETECTORS` set is empty. A unit guard
  (`tests/test_strategies.py::TestDetectMarketStructure::test_signal_stamped_at_confirmation_bar`)
  pins the open_time shift at the swing level.
- **Golden movement.** This is a behaviour change, so `make db-update` was run.
  The regression movement is isolated to exactly the three `bos` cells
  (`bos/4h`, `bos/1d`, `bos/1wk`) in both `golden_signal_watch.json` and
  `golden_weekdays.json`; no other strategy cell moved (the committed fixture
  parquets were held fixed so the goldens attribute purely to the fix). `bos`
  trade counts drop (e.g. `bos/4h` 18 → 2) because signals now land on
  confirmation bars — the correct causal population.

### Everything else — causal, no leak

- All 16 detectors, the backtest next-bar-open entry fill, the ATR SL, the
  regime gate, the HTF-EMA gate and the live forming-bar exclusion are proven
  causal (rows 1–10). The only as-of caveat is the bounded split
  back-adjustment (row 11), accepted and documented below.

## Adjustment convention (row 11, expanded)

The chosen convention is stated verbatim in the `utils/yfinance_client.py`
`fetch_history` docstring:

- `auto_adjust=False` preserves the raw print as `close` (absolute support /
  resistance levels need the un-rescaled price).
- `actions=False` strips the Dividends / Stock-Splits columns.
- Dividends are **not** back-adjusted — acceptable for signal geometry, which
  keys off price structure (wicks, swings, gaps) rather than total return.
- **Splits are still back-applied** by yfinance to historical OHLC, so a split
  effective after a given bar is embedded into that bar's price. This is a mild,
  bounded as-of violation.

**Why the bound is acceptable.** The Phase A universe is 13 liquid mega-caps
(AAPL/MSFT/GOOGL/AMZN/META/ORCL/ADBE/NVDA/AMD/TSLA/MSTR/SPY/QQQ). Splits on this
cohort are rare events, so the fraction of bars carrying an embedded future-split
factor over any backtest window is small, and the geometric distortion is a
single multiplicative rescale rather than a path-dependent leak.

**Upgrade path (deferred).** Eliminating the residual split bias requires an
unadjusted price feed plus manual as-of application of corporate actions
(splits and, if total-return signals are ever added, dividends). That is out of
the current free-data scope and is deferred to the universe / data-provider work
later in the roadmap. The guard test
`tests/test_yfinance_client.py::test_fetch_history_never_auto_adjusts_prices`
locks `auto_adjust=False` so the price basis cannot silently change.
