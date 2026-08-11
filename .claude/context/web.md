# Web Layer Reference

Detailed reference for `web/`. Load this when working on the FastAPI backend or Svelte frontend.

## Backend — `web/api/`

- `main.py` — app + StaticFiles mount; reads `WIFEY_CONFIG` env var (set by `wifey web --config <toml>`); stores `app.state.config_name` + `app.state.active_config`
- `deps.py` — `get_db` (per-request read-only DuckDB conn) + `require_token` (Bearer auth)
- `routers/` — config, ohlcv, fib, signals, backtest, stats, zones (T16-full removed the Binance-Futures-only `positions` / `prices` / `stream` routers; Phase B will re-introduce per the equities broker)
- `models/` — Pydantic models per router; `active_config.py` → `ActiveConfigResponse` + `StrategyParamsModel` + `UniversePolicyResponse`; `zones.py` → `ZoneBox`, `ZoneLine`, `SwingPoint`, `ZonesResponse`

### Key endpoints

- `GET /api/zones?symbol&timeframe&start_ms&end_ms` → `ZonesResponse(boxes, lines, swings)`; `ZoneBox`/`ZoneLine` carry `close_ms: int | None`
- `GET /api/backtest/runs` / `POST /api/backtest` — `BacktestRunSummary` has `stars/long_stars/short_stars: int | None`, `long/short_total_r/recovery_factor: float | None`; validators coerce pandas NaN → None
- `GET /api/strategies?config=<name>` — confidence values with per-config DB ratings override
- `GET /api/active-config` — `config_name`, `symbols`, `timeframes`, `strategies`, `day_filter`, `tp_r`, `sl_pct`, `fee_pct`, `adr_suppress_threshold`, `strategy_params`, `min_trades`, `min_trades_per_tf`; empty defaults when no `--config`
- `GET /api/universe-policy` — active `universe_policy` from stocks.json (`scope`, `as_of`, `survivorship_note`) + best-effort `n_symbols` (None when stocks.json unreadable); Phase 0.1 honesty surface, feeds the Backtest page caveat banner. `POST /api/backtest` stamps the policy JSON onto each saved run
- `GET /api/stats/{symbol}?days=180` — cached daily in `stats_cache` table; `path_cone` (required — pre-M5 cache rows fail validation and self-heal) + `weekly_cone` (Optional with an explicit None→recompute cache branch) cached with the bundle; `weekly_current_state`, `today_path`, `current_week_path`, `weekly_wick_percentile` always live (never cached), injected via `_inject_live_fields()`
- `GET /api/live-outcomes?days&min_n[&symbol]` — cross-symbol roll-up of the live `signal_alert_outcomes` ledger (roll-up + per-(strategy, tf, direction) + per-strategy win-rate/avg-R/expired; `symbol=` scopes roll-up + tables, chip list always global); never cached, own router (not the per-symbol StatsBundle)
- `GET /api/live-outcomes/open[?symbol]` — unresolved alerts marked to the newest stored OHLCV close per symbol (gross unrealized R + SL/TP distances; best-effort marks, never 5xx)
- `GET /api/backtest/analysis?use_config=true` — 12 digest query cards; `use_config=true` scopes via `DigestScope`

## Frontend — `web/ui/` (Svelte 5 + Vite)

Build: `make web-build` → `web/ui/dist/` served by FastAPI StaticFiles.

### Key files

- `src/api.ts` — typed client; `getStrategies(configName?)`, `getActiveConfig()`, `getUniversePolicy()`
- `src/stores/` — config, strategies, watchlist, `activeConfig.ts` (exposes `activeConfigStore`, `configName`, `configDefaultSymbol`)
- `src/pages/` — Chart, Backtest, SignalFeed, Stats
- `src/components/` — Nav, CandleChart, BacktestResult, …

### Backtest page

- DB-backed sortable/filterable table; collapsible run form
- **"◈ \<config\>" button** — pre-fills all chips + fee_pct/tp_r/sl_pct from active TOML
- Stars per row: `stars` (combined), `long_stars` (↑★), `short_stars` (↓★) — JOINed by `(strategy, tf, day_filter, direction)`
- Columns: long/short win rate, avg R, total R (↑/↓), Max DD, RF (≥3 green / 2–3 yellow / <2 red) — all sortable
- ADR Gate column shows `adr_suppress_threshold` per row (2dp, `—` for NULL). Since 2026-08-11 it reports **what the gate executed**, so `adr_exempt` strategies and every `1d` / `1wk` row correctly display `—`; only `4h` non-exempt rows show `0.80`. Rows written before 2026-08-06 still display `0.80` on `1d` / `1wk` — the gate really did run there back then (degenerately), so the migration left them alone
- Filter sections: CATEGORY (symbol/TF/strategy/day filter/ADR gate/stars), PERF (win%/trades/avg R/total R/max DD/RF), DIR (directional long+short)
- **Analysis sub-tab** — 12 lazy-loaded cards; `min_trades` input + "◈ Scope to config" toggle

### Chart page

- Watchlist sidebar; timeframe/days selectors
- **Strategies row** — collapsible group toggles: Structure (bos/eqh_eql/order_block/fvg), Fibonacci (ote_entry), Price Action (wick_fill/marubozu/inside_bar/trend_day), Candlestick (engulfing/pin_bar/hammer_hanging_man/doji/morning_evening_star), Session (orb/seasonality), Trend (ema); taxonomy in `STRATEGY_GROUPS` in `Chart.svelte`; groups absent from active TOML hidden. (T5b/T7 stripped smt_divergence + cvd_divergence; T16-full dropped funding_reversion + the Flow group; `fib_golden_zone` removed — only `ote_entry` survives in the Fibonacci group; `liquidity_sweep` removed.)
- **Indicators row** — EMA 20/50/200, RSI 14, **Zones** (7 toggles: FVG, OB, EQH·EQL, BOS, Fib Zone, OTE, Swings)
  - FVG/OB/Fib/OTE — HTML overlay divs; EQH/EQL/BOS — line series
  - Active zones extend to right edge; inactive end at `close_ms` (dimmed)
  - Colors: bull=`#56d364`, bear=`#f85149`, fib=`#e3b341`, ote=`#f0883e`
- **Range Levels** — MO, DO, PDH/PDL, WO, PWH/PWL, Mon H/L; solid lines from origin to right edge; HTML labels
- **CME Gap** — semi-transparent box for most recent Fri 21:00–Sun 22:00 UTC window; **1h only** (pill hidden on 4h/1d/1wk — `timeToCoordinate` returns null for inter-candle timestamps on coarser TFs). Was `15m and 1h` until 2026-08-05; `15m` is not a fetchable interval
- Time axis + crosshair: **MYT (UTC+8)** via `localization.timeFormatter`
- Signal markers + Fib overlay. (T16-full removed funding/OI sub-panels + the SSE / `/api/ohlcv/live` live-candle seed — yfinance has no realtime equivalent; the UI now seeds the current candle from the last DB row.)

### Stats page

- 10-card grid: P1/P2 (incl. P1 strong%), ADR, hourly distribution, DOW patterns (incl. Str H/Str L), session breakdown, weekly P1/P2, avg return by day, weekly P2 timing with flip risk, Daily Distance, P1 Wick Rank
- **Live Alert Outcomes** card (full-width, below the grid) — cross-symbol, fetched independently of the symbol picker via `getLiveOutcomes(days, minN)`; all-time roll-up chips (incl. a no-TP integrity badge) + period/min-n pill toggles + by-strategy and by-cell tables with diverging avg-R bars
- Default lookback: 365d
- "Daily Distance" + "P1 Wick Rank" — live, never cached; "Live Alert Outcomes" — cross-symbol, never cached
- Weekly P2 Timing: All/Bullish P1/Bearish P1 toggle; live "This week" banner with DOW, move%, distance bucket, conditioned probabilities

### Nav

- Shows active config name chip when server has a config loaded
- Chart + Stats default symbol: first config symbol → `stocks.json` fallback
