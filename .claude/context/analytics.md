# Analytics Module Reference

Detailed API reference for `analytics/`. Load this when working on any analytics module.

## data_store.py — DB schema + upsert/query helpers

- `upsert_signals(conn, df)` / `get_signals_history(conn, symbol, tf, start_ms, end_ms)`
- `list_backtest_runs(conn)` — newest-first; JOINs `stars`, `long_stars`, `short_stars` from `confidence_ratings` by `(strategy, tf, day_filter, direction)`; PARTITION BY includes `adr_suppress_threshold` so ADR-on/off runs appear as separate rows
- `upsert_backtest_run` / `upsert_backtest_trades` — `upsert_backtest_run` accepts `universe_policy: str | None = None` (Phase 0.1 policy JSON stamp; all four call sites — sweep runner, single-run CLI, scanner persistence, web `POST /api/backtest` — pass `load_universe_policy().to_json()`)
- `upsert_confidence_ratings(conn, config_name, ratings, win_rates, day_filter=None, direction="combined")` — PK `(config_name, strategy, tf, direction)`; direction = `'combined'` | `'long'` | `'short'`
- `get_confidence_ratings(conn, config_name, direction="combined")` / `get_directional_confidence_ratings(conn, config_name)` → `{strategy: {tf: {"long": stars, "short": stars}}}`
- `backtest_runs` columns: `adr_suppress_threshold REAL NULL`, `recovery_factor DOUBLE NULL`, `volume_suppress BOOLEAN NULL`, `universe_policy TEXT NULL` (Phase 0.1 — migration-list only, never in CREATE TABLE, so fresh + migrated DBs share one physical column order; `upsert_backtest_run`'s INSERT…SELECT is positional)
- `_backtest_run_id` appends `|adr:X` / `|vol_suppress` for unique run_id per param combo; `universe_policy` is deliberately excluded from the hash (metadata, doesn't change P&L)
- **D10 same-TF**: `backtest_combos` table; `upsert_combo_run` → stable `combo_id` (`symbol|tf|A+B|wN|day_filter`, no timestamp → `INSERT OR REPLACE`); `list_combo_runs(conn)`; `get_combo_lookup(conn)` → `dict[(symbol, tf, frozenset({a,b})), row_dict]` best avg_r per pair
- **D10 cross-TF**: `backtest_cross_tf_combos` keyed by `(symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf, window_hours, day_filter)`; `upsert_cross_tf_combo_run` / `list_cross_tf_combo_runs` / `get_cross_tf_combo_lookup` → ordered key (not frozenset — HTF/LTF roles are distinct)
- **CRITICAL**: `_upsert` uses explicit `conn.register`/`conn.unregister` in try/finally — do NOT switch to implicit replacement scan; it causes malloc heap corruption at `conn.close()`. Never drop the try/finally.
- `DEFAULT_DB_PATH` lives here — import from here, do not redefine in runners

## data_fetcher.py / data_sync.py / analytics_runner.py

- `data_fetcher.py` — pure fetch: yfinance → canonical OHLCV DataFrames (no DB). `fetch_bars(symbol, interval, start_ms, limit=BARS_MAX_LIMIT)` wraps `utils.yfinance_client.fetch_history`; supported intervals `1h | 4h | 1d | 1wk` (4h synthesised via 1h resample anchored to 13:30 UTC, US RTH open). `OHLCV_COLUMNS` excludes `vwap` and `taker_buy_volume` (yfinance OHLCV has neither). `BARS_MAX_LIMIT = 5000`. Tests patch `analytics.data_fetcher.fetch_history`.
- `data_sync.py` — yfinance-backed orchestration (T5, 2026-05-15). `backfill(conn, symbol, timeframe, start_ms)` is a single `fetch_bars` call (no client param, no pagination loop), gated on `data_quality.check_ohlcv` + `quarantine` between fetch and upsert (Phase 0.5) — corrupt rows are dropped before storage, soft anomalies logged; the return count reflects stored (clean) rows. `sync(conn, symbol, timeframe)` re-fetches from the latest stored `open_time` and delegates to `backfill`, so it inherits the gate. `backfill` also runs `trading_calendar.check_session_gaps` (N3 PR2) when ≥2 rows survive quarantine and logs missing NYSE sessions **warn-only** (never quarantined). `sync_funding_rates` / `sync_open_interest` removed in T5.
- `analytics_runner.py` — thin wrapper: opens DB, resolves symbols, delegates to `data_sync`. No client object (yfinance is module-level, no auth). `_resolve_symbols(symbols, *, use_universe=False)` defaults to the `utils.config_validation.load_stocks_config` watchlist and logs the active universe policy summary (`load_universe_policy().summary()`) whenever the implicit fallback is used (Phase 0.1 — no unstated symbol set); when `use_universe=True` (the `wifey analytics backfill/sync --universe` flag, threaded via `run_backfill`/`run_sync`) it resolves the **research breadth universe** instead (`load_research_universe().active_symbols()`, N3) and logs `ResearchUniverse.describe()`. Default (no flag) is byte-identical.

## data_quality.py — OHLCV ingest integrity monitor (Phase 0.5)

Pure detection + quarantine helper for OHLCV frames. No DB, no network, no side effects. Wired into `data_sync.backfill` between `fetch_bars` and `upsert_ohlcv`; additive — clean data is a pass-through, so behaviour (and regression goldens) are unchanged on good data.

- `check_ohlcv(df, *, return_outlier_pct=0.5) -> DataQualityReport` — never mutates the input, never logs, never raises on dirty data. Reports positional row indices (0..n-1 over an internally reset index).
- `DataQualityReport` (frozen dataclass) — index tuples per finding plus derived props:
  - **Quarantine sets** (dropped before storage, via `quarantine_idx`): `nan_idx` (NaN in any OHLCV field), `nonpositive_price_idx` (price ≤ 0), `bad_bar_idx` (broken geometry: `high < low/open/close`, `low > open/close`), `duplicate_time_idx` (later-duplicate `open_time`, `keep="first"`).
  - **Warn-only sets** (logged, kept — never silently delete a real 20% move or a halt bar): `zero_volume_idx`, `return_outlier_idx` (`|pct_change| > return_outlier_pct`), `suspected_split_idx` (`close` ratio within ±5% of a canonical split factor 0.5/⅓/0.25/0.2/2/3/4/5), `nonmonotonic_idx` (`open_time` goes backwards).
  - Props: `quarantine_idx`, `has_warnings`, `is_clean` (n_rows>0 ∧ no quarantine ∧ no warnings), `summary()` (human-readable count string).
- `quarantine(df, report) -> (clean, dropped)` — splits on `report.quarantine_idx`; positional, never mutates input; clean frame on a clean report returns the whole frame + an empty `dropped`.
- **Calendar-aware session-gap detection (N3 PR2):** the pure `detect_session_gaps(open_times, timeframe, sessions) -> SessionGapReport` maps each `open_time` → America/New_York session date and diffs it against an NYSE trading-date list (`1wk` → trading-week unit, else trading-day; expected sessions clamped to the observed `[min, max]` range). It takes a precomputed session list, so `data_quality.py` stays calendar-free; the NYSE list comes from `trading_calendar.nyse_sessions`. `SessionGapReport` carries `timeframe`, `unit`, `n_present`, `n_expected`, `missing`, with `n_missing` / `has_gaps` / `summary()`. `check_ohlcv` itself remains calendar-free.

## trading_calendar.py — NYSE trading-calendar wrapper (N3 PR2)

The **only** module importing `exchange_calendars`. No DB; one process-lifetime cached calendar handle.

- `nyse_sessions(start, end) -> list[date]` — NYSE (`XNYS`) session dates in `[start, end]`, weekends/holidays excluded, sorted. The calendar is built with an explicit `1990-01-01` start (not the library's rolling today-minus-20y default), and every query is clamped to its `[first_session, last_session]` window so out-of-range dates degrade to fewer/zero sessions rather than raising `DateOutOfBounds` (gap detection is warn-only and must never crash ingestion).
- `check_session_gaps(df, timeframe) -> SessionGapReport` — bridge: derives the observed ET session-date range from `df["open_time"]`, fetches the spanning NYSE sessions, and delegates to the pure `data_quality.detect_session_gaps`. Empty frame → empty (no-gap) report. Consumed by `data_sync.backfill`.

## strategies/ — strategy signal detection package

(After strat-3 the prior `indicators_lib.py` shim is removed; the 22 detect_* functions and the registries live in `analytics/strategies/`. Public entry: `from analytics.strategies import ...`.)

- **Per-detector modules**: `wick_fills.py`, `marubozu_retest.py`, `orb_breakout.py`, `fvg.py`, `market_structure.py` (= `bos`), `funding_extreme.py`, `eqh_eql.py`, `order_block.py`, `trend_day.py`, `engulfing.py`, `pin_bar.py`, `inside_bar.py`, `hammer_hanging_man.py`, `doji.py`, `morning_evening_star.py`, `fibonacci_retracement.py` (legacy), `ote_entry.py`, `ema.py` — one file per `detect_*` function (`smt_divergence` / `cvd_divergence` / `fib_golden_zone` / `liquidity_sweep` removed)
- **`_base.py`** — `ParamSpec`, `StrategySpec`, `SIGNAL_COLUMNS`
- **`_shared.py`** — `_find_bos_swing`, `volume_confirm`, `compute_ema`, `ema_cross_count`, `is_trending`, `_empty_signals`, `_signals_to_df`, `_fmt_time`
- **`_seasonality.py`** — `seasonality_stats`, `SEASONALITY_COLUMNS` (returns stats DataFrame, not signals)
- **`_registry.py`** — explicit-tuple-driven assembler holding `STRATEGY_REGISTRY` (17 entries), `DETECTOR_REGISTRY` (16 entries; `seasonality` and legacy `fibonacci_retracement` excluded), `KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS`, `INCOMPATIBLE_PAIRS`, `patch_confidence_scores`
- **`__init__.py`** — eager re-exports of every leaf + registry symbol; the public entry for callers
- 17 active strategies in `STRATEGY_REGISTRY`: `seasonality`, `wick_fill`, `marubozu`, `orb`, `fvg`, `bos`, `eqh_eql`, `order_block`, `trend_day`, `engulfing`, `pin_bar`, `inside_bar`, `hammer_hanging_man`, `doji`, `morning_evening_star`, `ote_entry`, `ema` (`fibonacci_retracement` / `fib_golden_zone` / `smt_divergence` / `cvd_divergence` / `liquidity_sweep` removed; `funding_extreme` exists as a function but isn't registered — needs a 2-arg signature, called directly)
- `StrategySpec.confidence: dict[str, int] | int` — use `get_confidence(tf)` (falls back to `"default"` key then `3`)
- `StrategySpec.tp_r_long/tp_r_short: float | None` — Gate 3 direction-split TP; use `get_tp_r(direction)` (falls back to 2.0)
- `StrategySpec.strategy_type` — one of `structural`, `fib`, `price_action`, `candlestick`, `flow`, `session`, `trend`
- `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS: dict[str, list[str]]` — type → strategy names
- `INCOMPATIBLE_PAIRS` — blocks bos+ote_entry (embeds BOS internally)
- `SIGNAL_COLUMNS` includes `tp_price` — `ote_entry` populates with 1.618 ext; others leave `0.0`
- `_candle_too_small(high, low, close, min_range_pct)` — filter for `(high-low)/close < min_range_pct`
- `min_range_pct: float = 0.0` added to all 6 candlestick detectors (default 0.0 = disabled); each has a `ParamSpec` in `STRATEGY_REGISTRY` for TOML tuning

## backtest_lib.py — pure backtest engine

- `Trade`, `BacktestResult`, `run_backtest`, format helpers
- Fee drag: `2 * fee_pct * entry / risk`; `min_sl_pct` widens SLs too close to entry. Phase 0.4: when `Trade.cost_model` is set, `pnl_r` replaces the flat fee with the decomposed equity cost (`cost_model.py` — spread/impact/borrow/commission in R) priced from a causal `Trade.cost_ctx`; `cost_model=None` (default) keeps the flat-fee path byte-identical
- Volume tiers: `_is_low_volume` (< 1.5× 20-candle mean) / `_is_volume_spike` (> 3× mean)
- `run_backtest(volume_suppress, volume_spike_boost, volume_suppress_long/short=None, volume_spike_boost_long/short=None, tp_r_long/short=None, atr_sl_floor=False, *, live_parity: LiveParityConfig | None = None, bias_cfg: BiasConfig | None = None, regime_series: pd.Series | None = None, strategy_params: dict[str, StrategyOverride] | None = None, htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None = None, cost_model: CostModel | None = None)` — directional params take precedence over symmetric; `atr_sl_floor=True` widens structural sl_price via `max(structural_dist, atr_sl_multiplier × ATR14)` (no-op otherwise — every active strategy emits structural sl_price, which short-circuits the bare ATR branch). All `live_parity` / `bias_cfg` / `regime_series` / `strategy_params` / `htf_slope_series_by_anchor` are keyword-only; defaults (all `None`) are a no-op. T6 PR-2: regime gate wired — per-signal `iloc[-2]` regime lookup at signal `open_time`, grouped + passed through live `_apply_regime_gate`. T6 PR-3: direction_filter + F8 HTF-EMA wired — direction_filter is a pure per-event check on `StrategyOverride.suppress_long`/`.suppress_short` (needs `strategy_params`); F8 HTF-EMA resolves slope at each signal's `open_time` via `_resolve_series_at` from `htf_slope_series_by_anchor` keyed by `(anchor_tf, period, slope_lookback)`, then groups events and passes them through live `_apply_htf_ema_gate`. T6 PR-4: ADR bias gate wired — `_apply_adr_bias_gate_to_signals` splits signals by per-direction `_is_adr_exempt(strategy, direction)` and reuses live `_filter_signals_by_adr` on the non-exempt slice (wifey has only strategy-wide `adr_exempt`; per-direction overrides not ported). The runner's legacy ADR pre-filter is skipped when `live_parity.is_on("adr_bias")` to avoid double-filtering. T6 PR-4b: cross-strategy conflict resolver applied at the runner level (engine path unchanged) — `_collect_sweep_results` splits into detect → resolve → backtest+save phases, pooling events per `(symbol, tf)` via the engine's `_df_to_events` adapter, calling `_apply_conflict_resolver` with a `confidence_ratings.avg_r` lookup keyed on `cfg.config_name`, and redistributing survivors via `_events_to_df`. T6 PR-5: cooldown gate wired — `_apply_cooldown_gate_to_signals` walks signals in `open_time` order against a per-call `_CooldownState` ledger keyed by `(symbol, tf, strategy, direction)`, dropping a row when `open_time < last_fire + cooldown_bars × tf_ms` (mirrors live `cooldown_store` candle-watermark semantics); `_resolve_cooldown_bars` reads `_DEFAULT_COOLDOWN_BARS_PER_TF` — wifey's **equity** map `{"4h": 2, "1d": 1, "1wk": 1}` (NOT parent's intraday map), unknown TF → 1, overridable via `live_parity.cooldown_bars_per_tf`. State is instantiated inside `run_backtest()` per call so two identical back-to-back calls are byte-equal; applied last in the gate chain (after ADR, before `BacktestResult`). **T6 live-parity series complete.** Phase 0.4: keyword-only `cost_model: CostModel | None = None` — when set, `run_backtest` builds a causal per-signal `CostContext` (trailing dollar ADV + daily sigma ending at the signal bar, from OHLCV) and stamps each `Trade`; default `None` keeps goldens byte-identical
- `_df_to_events(signals, symbol, timeframe, strategy) -> list[SignalEvent]` + `_events_to_df(events, original_df) -> pd.DataFrame` — adapters between backtest signals frames and the live `SignalEvent` shape. Lets PR-2+ feed backtest signals straight through `analytics/signal/gates.py`; gates only ever drop events, so `_events_to_df` filters `original_df` to the surviving `open_time` set and preserves dtypes.
- `Trade.low_volume` / `Trade.volume_spike` tag volume tier per trade
- `BacktestResult` exposes: `low/normal/spike_vol_closed_trades` + `*_avg_r`; 6 directional×volume cross-tabs (`long/short_low/normal/spike_vol_*`)
- `format_volume_split()` — 3-way table; `format_directional_volume_split()` — ↑/↓ × Low/Normal/Spike
- Rolling detectors (ote_entry, order_block, eqh_eql) fire at every historical candle; last-candle-only detectors fire at most once per run
- `BacktestResult` directional split: `long/short_closed_trades`, `long/short_win_count/rate/avg_r/total_r`
- `max_drawdown_r` (peak-to-trough) + `recovery_factor` (total_r / max_drawdown_r, 0.0 when no drawdown)
- **D10 same-TF**: `ComboBacktestResult`; `_find_cofire_signals` — greedy ±N-candle, same direction, each B once; `run_combo_backtest`; `format_combo_table`
- **D10 cross-TF**: `CrossTfComboBacktestResult(strategy_htf, strategy_ltf, tf_htf, tf_ltf, window_hours, result)`; `_find_cross_tf_signals` — LTF within `[ltf_time - window_hours, ltf_time]` of any same-direction HTF (no exclusivity); `run_cross_tf_combo_backtest`; `format_cross_tf_combo_table`

## backtest_runner.py — thin wrapper

- Opens DB, loads OHLCV/funding, calls indicator + backtest libs
- `run_digest_cmd(query, min_trades, top_n)` for CLI digest
- `run_combo_backtest_cmd(...)` — sweeps all symbol×TF×strategy-pair; skips `INCOMPATIBLE_PAIRS`; parallel via `ProcessPoolExecutor`; `_combo_worker` top-level (pickling); `workers=None` → `min(4, cpu_count-1)`; `workers=1` bypasses pool; symbols fall back to `coins.json`
- `_SWEEP_STRATEGIES` / `_non_seasonal` both exclude `("seasonality", "funding_reversion")`
- OHLCV cache in `_collect_sweep_results` — fetches once per `(symbol, timeframe)`
- T6 PR-4b: `_collect_sweep_results` is a three-phase pipeline (detect → resolve conflicts → backtest+save). `signals_map` is `dict[(sym, tf, strat), (ohlcv, signals, secondary)]` with `secondary` always `None` in wifey (no `smt_divergence`); insertion order matches `itertools.product` so default-off output is byte-identical to the legacy single loop. New helpers: `_build_confidence_ratings_map(conn, cfg)` returns `{(strategy, tf, direction): avg_r}` or `None` when the `conflict_resolver` gate is off; `_resolve_conflicts_for_signals_map(signals_map, ratings_map)` pools by `(symbol, tf)` and calls `_apply_conflict_resolver` per `open_time` moment with the avg_r resolver (directional → combined → 0.0). `BacktestSweepConfig.config_name` (new field, populated by `load_backtest_config` to the TOML stem) keys the ratings lookup. Caches (regime / HTF slope / ratings) are built once in `run_backtest_sweep` and threaded into `_collect_sweep_results` via kw-only params so all three sweep modes (single / tp_r / atr) share one build.
- `run_cross_tf_combo_backtest_cmd(...)` — sweeps symbol × (tf_htf, tf_ltf) × all strategy ordered pairs; `_cross_tf_combo_worker` chunked by `(symbol, tf_htf, tf_ltf)`; `_DEFAULT_HTF_LTF_PAIRS = [("4h","15m"),("4h","1h"),("1h","15m"),("1d","4h"),("1d","1h")]`

## perf_timer.py

- `timed(label)` context manager — prints `[perf] label: Xs`; import via `from analytics.perf_timer import timed`

## regime.py

- `classify_series(df, timeframe) → pd.Series[str]` — labels each row as `trend`/`range`/`high_vol`/`unknown` per §6 of `docs/redesign/buibui-redesign.md`
- `high_vol` if ATR-14% ≥ 90-day rolling 80th-percentile; else `trend` if `|EMA-50 slope|` ≥ 0.5% over 10 bars; else `range`; `unknown` for rows lacking enough history
- Used by `tools/strategy_edge_audit.py` (Phase 0). Live as soft-mode gate since 2026-05-10 — wired into `run_scan_cycle` as Step −1 of the bias chain via `analytics/signal/gates.py::_apply_regime_gate`.

## param_sweep.py — WFO sweep lib

- `run_param_sweep(conn, strategy, symbol, tf, days, param_ranges, wfo_split, min_trades, fee_pct, top_n, adr_suppress_threshold=None, day_filter="off", atr_sl_multiplier=None, atr_sl_floor=False, *, live_parity=None, bias_cfg=None, regime_series=None, strategy_params=None, htf_slope_series_by_anchor=None, cv=None)` → `ParamSweepReport{rows: list[SweepRow], gate: CommitGateVerdict, n_grid}` (N2 PR 2 — was `list[SweepRow]`; the `gate` is `sweep_guard.evaluate_commit_gate` deflated over the full grid before top-N truncation, callers read `.rows`) — `atr_sl_multiplier`/`atr_sl_floor` forwarded to every grid `run_backtest()` call (F9 joint sweeps); the kw-only live-parity inputs (`live_parity` + `bias_cfg` + pre-computed `regime_series`/`strategy_params`/`htf_slope_series_by_anchor`) are forwarded to both the IS+OOS `run_backtest()` calls so a WFO cell replays the live gate stack (regime/direction_filter/F8 HTF-EMA/ADR/cooldown). All default `None` → byte-identical raw-signal path. `tools/multi_symbol_wfo.py --live-parity` builds these inputs per config and threads them through (the filter-accurate fix for `wfo-filter-divergence`). `cv=CvConfig(mode="purged", n_folds, purge_bars, embargo_bars)` (Phase 0.3c) switches to purged+embargoed K-fold CV via `analytics/backtest/cv_splits.py` (`fold_bounds` partitions rows into K contiguous test folds; per fold the pre-test train segment loses `purge_bars` tail bars and the post-test segment loses `resolve_embargo_bars(n)` head bars — default ~1% of rows; per-segment truncation censors straddling trades as outcome="open"). IS = `_dedup_trades`-pooled train segments across folds, OOS = disjoint test folds pooled; `_sweep_grid_worker_cv` is the picklable CV worker; `_attach_overfit_stats(pooled_oos_only=True)` builds the PBO matrix from OOS only in CV mode. `cv=None`/contiguous = legacy byte-identical path
- `run_strategy_audit(...)` → `list[AuditRow]` — same `atr_sl_multiplier`/`atr_sl_floor` kwargs as `run_param_sweep`; forwarded to each worker's `run_backtest()`
- Applies `day_filter` before IS/OOS split — grades same population the live daemon sees
- `SweepRow` / `AuditRow` expose `long/short_oos_avg_r`, `long/short_oos_n` (Gate 3)
- `_directional_split_hint(row)` fires when |↑OOS − ↓OOS| ≥ 0.1R and n ≥ 3 each
- Parallelized: Phase 1 detects signals sequentially (needs DB conn), Phase 2 runs grid via `ProcessPoolExecutor` using `_sweep_grid_worker` / `_audit_strategy_worker` (picklable, take pre-computed DataFrames)
- **Overfitting controls (Phase 0.3a/b):** after the grid completes (before the `top_n` truncation), `_attach_overfit_stats(rows)` computes per-config IS/OOS Sharpe + a Deflated Sharpe and one sweep-level PBO over the full grid, attaching an `OverfitStats` to each `SweepRow.overfit_stats`. `format_sweep_results` prints `trials N=… PBO=…%` + the recommended config's per-trade Sharpe + Deflated Sharpe (a probability — ≥95% = robust). PBO matrix columns are sorted by params for run-to-run reproducibility. See `backtest/stats_overfit.py`

## stats_overfit.py — overfitting / multiple-testing controls (Phase 0.3a/b)

- Lives in `analytics/backtest/`. Pure math — numpy + stdlib `statistics.NormalDist` (Φ / Φ⁻¹; **no scipy**), no DB, no engine import (so `engine` → `stats_overfit` is one-directional, no cycle)
- `sharpe_ratio(returns)` — per-trade R Sharpe `mean/std` (sample std ddof=1, non-annualized — the engine's native unit). `0.0` on n<2 or zero-variance. `_moments(returns)` → `(mean, std, skew, kurt)` with non-excess kurtosis (normal == 3). `BacktestResult.sharpe` wraps this over closed-trade `pnl_r`
- `probabilistic_sharpe_ratio(sr, n, skew, kurt, sr_star=0.0)` — PSR = Φ((SR−SR\*)·√(n−1) / √(1 − skew·SR + (kurt−1)/4·SR²)). `expected_max_sharpe(sr_variance, n_trials)` — SR\*₀ = √V·[(1−γ)·Z⁻¹(1−1/N) + γ·Z⁻¹(1−1/(N·e))] (γ = Euler–Mascheroni). `deflated_sharpe_ratio(observed_sr, trial_sharpes, n_returns, skew, kurt)` = PSR evaluated against SR\*₀ estimated from the cross-trial Sharpe variance — Bailey & López de Prado 2014. DSR is a **probability** in [0,1]
- `probability_of_backtest_overfitting(perf_matrix, n_splits=16)` → `PBOResult(pbo, n_combinations, logits)` — CSCV (Bailey et al. 2017): partitions the T rows into S contiguous submatrices, and for every C(S, S/2) IS/OOS split ranks configs by IS mean R, takes the IS-best, records its OOS rank as a logit; PBO = P(logit < 0) (IS-best below OOS median). Performance metric = mean R per column (robust on sparse submatrices). `NaN` when < 2 configs or T < S
- `build_performance_matrix(trade_points, n_rows)` — time-buckets each config's `(entry_time, pnl_r)` points into a T×N matrix over the shared time span (the common axis CSCV needs; per-trade R is not row-alignable across configs with different `tp_r`)
- `OverfitStats(is_sharpe, oos_sharpe, deflated_sharpe, n_trials, pbo)` — carrier attached to `SweepRow.overfit_stats`. 0.3c shipped purged-embargoed CV (`cv_splits.py`; `_split_ohlcv` untouched, default-off)

## research_guards/ + sweep_guard.py + audit_guard.py — commit-gate subsystem (Phase N2)

- `analytics/research_guards/` — pure-math research-integrity package ported **verbatim** from the parent (numpy + stdlib `statistics.NormalDist`, no scipy, no DB/engine). Eager re-exports via `__init__.py`. Kept separate from `backtest/stats_overfit.py` on purpose: `stats_overfit` owns the Phase 0.3a/b **sweep-footer display** (mean-R PBO metric, `trial_sharpes`-list DSR), `research_guards` owns the **commit-gate** math at the parent's API — mirroring keeps `/sync-parent` ports 1:1. `sweep_guard` is **wired into `run_param_sweep`** + the sweep footer + the apply skills (N2 PR 2); `audit_guard` stays unwired (no wifey gate/ADR audit tool hosts it yet):
  - `probabilistic_sharpe_ratio(sr, n_obs, skew=0, kurtosis=3, sr_benchmark=0)` → P(true SR > `sr_benchmark`); **raises** `ValueError` on `n_obs < 2` or non-positive variance term (vs `stats_overfit`'s 0.0-return)
  - `expected_max_sharpe(n_trials, sr_variance)` (note arg order — `n_trials` first, opposite of `stats_overfit`'s) + `deflated_sharpe_ratio(sr, n_obs, *, trial_srs | (n_trials + sr_variance), skew=0, kurtosis=3)` — exactly one multiplicity source or `ValueError`; returns a probability
  - `cscv_pbo(perf_matrix, n_splits=14, metric=None)` → `PBOResult(pbo, logits, degradation_slope, n_combinations)` — default metric = per-trial **Sharpe**; **raises** on odd or `<4` `n_splits`, `<2` trials, or `<2` rows/block (vs `stats_overfit`'s auto-shrink + mean-R metric)
  - `min_track_record_length(sr, skew=0, kurtosis=3, target_sr=0, confidence=0.95)` → fractional obs needed for PSR to reach `confidence`; `inf` when `sr <= target_sr`
  - `block_bootstrap_ci(returns, stat_fn, n_boot=10000, block=None, alpha=0.05, method="stationary"|"circular", seed=None)` → `BootstrapCI(point, lo, hi, alpha, n_valid)`; serial-correlation-aware percentile CI (block default `round(n**(1/3))`)
  - `haircut_sharpe(sr, n_obs, n_tests, method="bonferroni"|"holm"|"bhy", pvalues_all=None)` → `HaircutResult(adjusted_pvalue, haircut_sharpe, haircut_pct, method, fell_back)`; Holm/BHY need `pvalues_all` else fall back to Bonferroni (`fell_back=True`)
- `analytics/sweep_guard.py` — `evaluate_commit_gate(chosen: TrialPerf, all_trials, *, n_grid, dsr_threshold=0.95, pbo_threshold=0.5, mintrl_confidence=0.95, n_splits=14)` → `CommitGateVerdict(decision, dsr, pbo, min_trl, n_obs, n_trials, reasons)` with `.committable`. Decision = `COMMIT` only when DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ n_obs ≥ MinTRL, else `DO_NOT_COMMIT`; `INSUFFICIENT` when `<2` trials or `n_obs < 2·n_splits`. `TrialPerf(label, returns, times)` is the per-trial carrier; `_build_perf_matrix` calendar-bins it for CSCV; `n_grid` (true pre-truncation trial count) floors the deflation N. Pure; consumes `research_guards`. **Consumed by** `param_sweep.run_param_sweep` via `_compute_sweep_gate(trial_rows, _recommended_row(top_rows), n_grid)` + the `_row_to_trialperf` adapter (pools each row's IS+OOS closed trades into a chronological `(entry_time, R)` series); the verdict rides in `ParamSweepReport.gate` and renders via `param_sweep._fmt_gate`
- `analytics/audit_guard.py` — `evaluate_audit_cells(cells: list[AuditCell], *, bar=0.05, alpha=0.05, min_n=30, haircut_method="holm", n_boot=2000, boot_method="circular", seed=12345, enable_concentrate=True)` → `list[CellVerdict]` aligned 1:1. A cell earns `ENABLE` (CI.hi ≤ −bar) / `DISABLE` (CI.lo ≥ +bar) only when the bootstrap CI clears ±`bar` **and** its Holm-adjusted p (shared family across tested cells) < `alpha`; `CONCENTRATE` refines `DISABLE` when the kept slice beats the positive suppressed slice by ≥ `bar`; `n_supp < min_n` → `INSUFFICIENT` (excluded from the haircut family). `AuditCell(label, supp_r, kept_r=[])`. Replaces the crude ±0.05R audit bar. Pure; consumes `research_guards`

## cost_model.py — realistic equity cost model (Phase 0.4)

- Lives in `analytics/backtest/`. Pure math — `json` + `math` + numpy, no DB, no engine import (`TradeLike` Protocol keeps it engine-free)
- `CostModel` (frozen dataclass): `adv_thresholds`/`half_spread_bps` (one spread per ADV bucket, 0 = least liquid → widest), `impact_coef`, `notional_usd`, `borrow_rate_annual` (0.01 default constant), `commission_bps`, `adv_window_days`. `__post_init__` validates ascending thresholds + bucket-count + non-negativity
- `cost_r(trade, ctx)` / `cost_breakdown(trade, ctx) -> CostBreakdown(spread_r, impact_r, borrow_r, commission_r)` — each component in R via `× entry/risk`. Spread = half-spread bps by `spread_bucket(adv)`; impact = `2 × impact_coef · σ · √(notional/ADV) × entry/risk` per leg (0 when ADV/σ unknown); borrow = shorts only, `rate × holding_days/365`, charged once; commission = `2 × bps`. Zero when risk is zero
- `build_cost_context(closes, volumes, idx, bars_per_day, window_bars) -> CostContext(adv_dollars, sigma_daily)` — **causal**: trailing window ending at `idx`; ADV = mean(close×vol)×bars_per_day, σ = population stdev of per-bar returns × √bars_per_day; None when not computable (conservative fallback). `bars_per_day_for_tf(tf)` from `BARS_PER_DAY` (`1h:7, 4h:2, 1d:1, 1wk:0.2`; unknown → 1.0)
- `cost_model_from_toml(raw)` — parses `[backtest.cost_model]`; absent/`enabled=false` → None (legacy flat-fee). `to_json()` is canonical (stamps `backtest_runs.cost_model` + the run_id hash). **Code default-off (`cost_model=None` byte-identical); ON for the live `signal_watch` configs since Phase 0.4 step 2 (#80, 2026-06-15) — that flip moved the regression goldens and is also threaded into `test_regression.py`**

## digest_lib.py — aggregation over backtest_runs

- 12 pre-canned queries: `symbol`, `strategy`, `tf`, `combos`, `adr_ab`, `volume_ab`, `day_filter_ab`, `direction_bias`, `consistency`, `recovery_factor`, `co_firing`, `cross_tf_combos`
- `run_digest(conn, query, min_trades=None, top_n, scope)` — `min_trades=None` resolves via `_QUERY_MIN_TRADES` (default 5; co_firing/cross_tf_combos default to 3)
- `DigestScope(day_filter, fee_pct, symbols, min_trades, min_trades_per_tf)` — `_scope_clauses()` + `_min_trades_expr()` (per-TF CASE)
- `query_co_firing` deduplicates via `QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol, timeframe, strategy_a, strategy_b, window_candles, day_filter ORDER BY run_at_ms DESC) = 1`
- `query_cross_tf_combos` deduplicates by `(symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf, window_hours, day_filter)`
- Powers `GET /api/backtest/analysis?use_config=true` and `buibui digest` CLI

## cme_gap_lib.py

- `CMEGap(gap_low, gap_high, gap_up, filled)`
- `get_recent_cme_gap(ohlcv_df, _now_sec=None)` — most recent Fri 21:00–Sun 22:00 UTC gap
- `cme_gap_alert_warning(gap, direction, entry, tp_price)` — LONG: unfilled gap below entry; SHORT: gap in TP path

## zones_lib.py — structural zone extraction (geometry only, no trade signals)

- `extract_fvg_zones(df)` — bull/bear FVG boxes; `active=False` + `close_ms` when CE midpoint crossed
- `extract_order_block_zones(df)` — OB boxes; `active=False` + `close_ms` when mitigated
- `extract_eqh_eql_zones(df)` — EQH/EQL lines from swing pivots; `active=False` + `close_ms` at first wick-touch
- `extract_bos_zones(df)` — active (unbroken) + last 5 broken levels with `close_ms`
- `extract_fib_golden_zones(df)` — current 0.5–0.618 box from most recent BOS swing
- `extract_ote_zones(df)` — current 0.618–0.786 OTE box
- `extract_swing_points(df)` — recent 3-bar pivot highs/lows as dot annotations
- All return `list[dict[str, Any]]` with `zone_low/high`, `price`, `start_ms`, `close_ms`, `active`, `direction`; active zones first + 3–5 recent inactive
- Imports `_find_bos_swing` from `analytics.strategies` for Fib/OTE

## signal_lib.py — pure scan lib

- `scan_symbol(..., catch_up=False)` — runs strategies on one symbol/tf; emits events for the latest closed candle only (default) or for every closed candle in the window when `catch_up=True` (each with its own candle close as entry price)
- `run_scan_cycle(..., catch_up=False)` — 3-phase: Phase 1 pre-fetches all DB data sequentially (`funding_map`/`ohlcv_map`), Phase 2 fans out via `ThreadPoolExecutor` (pure pandas, GIL released; workers = `min(cpu_count-1, n_pairs)`), Phase 3 fan-in: cooldown/backtest/upsert sequentially. With `catch_up=True`, each `(symbol, tf)` scan result is exploded into one per-candle group between Phase 2 and Phase 3 so conflict resolution + confluence stay per-candle correct; a cold-start guard (`CooldownStore.last_marked`) seeds only the latest candle for a fresh-watermark key. Default-off path byte-identical
- `ohlcv_cache: dict[(symbol, tf), DataFrame] | None` — daemon hot path skips DB reads in Phase 1
- `confidence_override` (combined) + `directional_confidence_override` ({strategy: {tf: {direction: stars}}}) — directional takes precedence
- `_compute_backtest()` respects `fee_pct`, `day_filter`, `min_sl_pct`, `atr_sl_multiplier`, `atr_sl_floor`, `since`; label shows `since YYYY-MM-DD` when set. `atr_sl_floor` flows through to `run_backtest()` so the alert's backtest gate evaluates trades with the same widened SLs the live path would apply
- `run_scan_cycle(atr_sl_multiplier=None, atr_sl_floor=False, ...)` — `atr_sl_floor` enables the F9 live widener; Phase 3 calls `analytics/signal/atr_floor.py::_apply_atr_floor` on returned events before conflict/dedup/bias/DB writes, so persisted `signals.sl_price` and Telegram alerts use the corrected SL/TP. Per-strategy / per-symbol+TF overrides via `strategy_params.atr_sl_floor` + `atr_sl_floor_per_tf`; resolver `_resolve_atr_sl_floor` mirrors the `atr_sl_multiplier` hierarchy. `_backtest_run_id` keyed on `atr_sl_floor` so cached/persisted runs don't bleed across on/off
- `_excluded_from_registry = {"seasonality", "funding_reversion"}` — skip silently (no WARNING)
- `_filter_signals_by_adr(ohlcv_df, signals_df, threshold)` — directional: suppresses chasing direction (LONGs when close > range midpoint, SHORTs when close < midpoint)
- `_is_adr_exempt(strategy_params, strategy)` — bypasses live gate + `_compute_backtest` filter; stores NULL `adr_suppress_threshold`
- Volume gate: `_resolve_volume_suppress/spike_boost_long/short` — directional overrides → symmetric fallback; `SignalEvent.volume_spike` tagged
- `_compute_stats_context()` computes `StatsContext` once per cycle
- CME gap: `get_recent_cme_gap(ohlcv_df)` per (symbol, tf); passes `cme_gap_warning` to formatter
- **Same-TF co-fire**: `combo_lookup`, `combo_window=5`, `combo_min_avg_r=1.0`; `_find_live_cofire` checks same-cycle pairs + cross-cycle DB signals; attaches `ConfluenceData`
- **Cross-TF co-fire**: `cross_tf_lookup`, `cross_tf_pairs`, `cross_tf_window_hours=4.0`, `cross_tf_min_avg_r=1.0`; `_find_cross_tf_cofire` queries DB signals history for HTF; same-TF and cross-TF both evaluated — higher avg_r wins
- `_parse_htf_ltf_pairs(list[str])` — parses `["4h:15m", ...]` TOML strings

## stats_lib.py — pure stats lib

- `compute_p1p2_daily` → `P1P2Result` (incl. `p1_strong_pct`)
- `compute_hourly_extremes` (incl. `peak_high/low_hour_by_dow` per-DOW MODE)
- `compute_adr` → `ADRResult(adr_14, adr_30, today_range_pct, today_consumed_pct, today_move_up: bool | None)`
- `compute_dow_patterns` (incl. `avg_return_pct`, `strong_high/low_pct`)
- `compute_session_breakdown`, `compute_weekly_p1p2`, `compute_weekly_p2_timing` → `WeeklyP2Timing`
- `compute_weekly_flip_risk_conditioned` → `WeeklyFlipRiskConditioned`; p1_direction="low"=bullish, "high"=bearish
- `compute_all` → `StatsBundle`
- Live (never-cached) functions via `_inject_live_fields()`: `compute_weekly_current_state`, `compute_daily_distance`, `compute_weekly_wick_percentile`
- All times MYT (UTC+8): `(epoch_ms + INTERVAL 8 HOUR)::TIMESTAMP`; raises `ValueError` on empty data

## signal_runner.py — thin daemon wrapper

- Creates client, opens DB, syncs candles, polls `run_scan_cycle` in a loop
- All TOML params wired through: `sl_pct`, `cooldown_seconds`, `fee_pct`, `day_filter`, `bias_cfg`
- Loads `confidence_override` + `directional_confidence_override` from DB at startup
- **OHLCV cache**: `_update_ohlcv_cache()` re-fetches from `cached_max_ts` inclusive; replaces cache[-1] + appends new rows; invalidates when `>2` rows arrive (`_CACHE_INVALIDATE_THRESHOLD = 2`)
- **Combo refresh**: `combo_lookup` + `cross_tf_lookup` reloaded every `_COMBO_REFRESH_CYCLES = 10` cycles
- **T2 outcome backfill**: after each `run_scan_cycle`, calls `analytics/signal/outcome_backfill.py::backfill_outcomes(conn, now_ms)` on the same write conn. Resolves `signal_alert_outcomes` rows where `outcome IS NULL` by walking OHLCV forward; mirrors the backtest engine's same-bar-tie-to-loss rule. Past `max_hold_bars` without TP/SL touch → `expired` with MTM `outcome_r`. Failure logs but never blocks the cycle. The writer now persists a non-NULL `sl_price`/`tp_price` for **every** fired event (`scanner._resolve_outcome_sl_tp`, ported from parent #410 — structural SL when valid, else the alert formatter's pct fallback), so all rows are scoreable. Legacy rows written before the fix (NULL `tp_price`) are recovered by the one-shot `tools/backfill_null_tp_outcomes.py` retro migration.

## signal_test_runner.py

- `run_signal_test(symbol, timeframe, strategy, at_ms, lookback, ...)` — read-only, no DB writes, no cooldown
- `--at` pins to historical candle (Unix ms or ISO datetime); `--lookback` default 200
- `secondary_map: dict[str, str] | None` — loads secondary OHLCV for `smt_divergence`
- `wifey.py` builds secondary map from `coins.json smt_secondary` automatically

## signal_config.py — signal_watch TOML config loader

- `BacktestFilterConfig`: `fee_pct`, `min_sl_pct`, `min_avg_r`, `min_avg_r_long/short: float | None`, `since: str | None`, `cost_model: CostModel | None` (Phase 0.4, from `[backtest.cost_model]`; None = flat-fee path). `BacktestSweepConfig` carries the same `cost_model` field
- Hard-mode gate uses `min_avg_r_long/short` when set, falls back to `min_avg_r`
- `SymbolOverride` — per-symbol tp_r/sl_pct/atr_sl overrides
- `StrategyOverride`: `tp_r_long/short` (per-strategy directional), `tp_r_long_per_tf` / `tp_r_short_per_tf` (per-TF directional, Task A 2026-05-18 — TOML keys `tp_r_long_4h`, `tp_r_short_1d`, …), `adr_exempt`, `volume_suppress/spike_boost` (symmetric + directional long/short)
- `SignalWatchConfig.effective_tp_r(strategy, symbol, tf, direction="")` — resolution (Task A): `symbol+TF+dir → symbol+TF → symbol → strategy+TF+dir → strategy+TF → strategy+dir → strategy → global`. The loader regex excludes `tp_r_long_*` / `tp_r_short_*` from `tp_r_per_tf` so the directional per-TF keys don't silently mis-parse.
- `effective_volume_suppress/spike_boost(strategy)` — per-strategy → global; directional variants return `bool | None`
- `BiasConfig` from `[bias]`: `adr_suppress_threshold`, `dow_soft_suppress`, `dow_suppress_min_abs_return`; F8 fields `htf_ema_enabled/mode/default_tf/default_period/default_slope_lookback/deadband_pct/per_strategy` (+ `htf_ema_anchor(strategy)` resolver); regime fields `regime_enabled/mode/htf_tf/enabled_regimes/per_strategy` (+ `regime_allowed(strategy, strategy_type, regime)` resolver — `unknown` regime + unmapped types fall open)
- `ComboConfig`: same-TF `window=5`, `min_avg_r=1.0`; cross-TF `cross_tf_pairs`, `cross_tf_window_hours=4.0`, `cross_tf_min_avg_r=1.0`
- `_deep_merge` + `_load_toml_with_extends` — config may declare `extends = "strategy_params.toml"`

## backtest_config.py — backtest sweep TOML loader

- `BacktestSweepConfig`: `min_sl_pct`, `liq_sweep_use_fib`, `volume_suppress/spike_boost`, `since: str | None`
- `is_adr_exempt(strategy)` — skips ADR filter in `backtest_runner.py`
- `effective_tp_r` / `effective_volume_suppress/spike_boost` / directional variants — mirror signal_config resolution
- Same `_deep_merge` + `_load_toml_with_extends` as signal_config.py

## recalibrate_lib.py / recalibrate_runner.py

- `get_backtest_win_rates(conn)` → DataFrame with combined + directional columns
- `compute_recalibrated_ratings(conn, min_trades)` → `dict[str, dict[str, int]]`
- `compute_directional_ratings(conn, min_trades=5)` → `{strategy: {tf: {"long": stars, "short": stars}}}`
- `write_confidence_to_db(conn, config_name, ratings, win_rates, day_filter=None, directional_ratings=None)`
- `write_confidence_to_source` — legacy: patches `analytics/strategies/_registry.py` directly
- Runner: `--config <toml>` derives `day_filter`, `config_name`, `adr_suppress_threshold`; `--apply` writes to DB (with config) or source (without config)
