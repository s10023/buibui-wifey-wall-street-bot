# Web Layer Reference

Detailed reference for `web/`. Load this when working on the FastAPI backend or Svelte frontend.

## Backend — `web/api/`

- `main.py` — app + StaticFiles mount; reads `WIFEY_CONFIG` env var (set by `buibui web --config <toml>`); stores `app.state.config_name` + `app.state.active_config`
- `deps.py` — `require_token`, `require_token_sse` (SSE query-param auth)
- `routers/` — config, ohlcv, fib, signals, backtest, positions, prices, stream, stats, zones
- `models/` — Pydantic models per router; `active_config.py` → `ActiveConfigResponse` + `StrategyParamsModel`; `zones.py` → `ZoneBox`, `ZoneLine`, `SwingPoint`, `ZonesResponse`

### Key endpoints

- `GET /api/zones?symbol&timeframe&start_ms&end_ms` → `ZonesResponse(boxes, lines, swings)`; `ZoneBox`/`ZoneLine` carry `close_ms: int | None`
- `GET /api/backtest/runs` / `POST /api/backtest` — `BacktestRunSummary` has `stars/long_stars/short_stars: int | None`, `long/short_total_r/recovery_factor: float | None`; validators coerce pandas NaN → None
- `GET /api/strategies?config=<name>` — confidence values with per-config DB ratings override
- `GET /api/active-config` — `config_name`, `symbols`, `timeframes`, `strategies`, `day_filter`, `tp_r`, `sl_pct`, `fee_pct`, `adr_suppress_threshold`, `strategy_params`, `min_trades`, `min_trades_per_tf`; empty defaults when no `--config`
- `GET /api/stats/{symbol}?days=180` — cached daily in `stats_cache` table; `weekly_current_state`, `daily_distance`, `weekly_wick_percentile` always live (never cached), injected via `_inject_live_fields()`
- `GET /api/backtest/analysis?use_config=true` — 12 digest query cards; `use_config=true` scopes via `DigestScope`

## Frontend — `web/ui/` (Svelte 5 + Vite)

Build: `make web-build` → `web/ui/dist/` served by FastAPI StaticFiles.

### Key files

- `src/api.ts` — typed client; `getStrategies(configName?)`, `getActiveConfig()`
- `src/stores/` — config, strategies, prices SSE, positions SSE, `activeConfig.ts` (exposes `activeConfigStore`, `configName`, `configDefaultSymbol`)
- `src/pages/` — Chart, Backtest, SignalFeed, Positions, Prices, Stats
- `src/components/` — Nav, CandleChart, BacktestResult, …

### Backtest page

- DB-backed sortable/filterable table; collapsible run form
- **"◈ \<config\>" button** — pre-fills all chips + fee_pct/tp_r/sl_pct from active TOML
- Stars per row: `stars` (combined), `long_stars` (↑★), `short_stars` (↓★) — JOINed by `(strategy, tf, day_filter, direction)`
- Columns: long/short win rate, avg R, total R (↑/↓), Max DD, RF (≥3 green / 2–3 yellow / <2 red) — all sortable
- ADR Gate column shows `adr_suppress_threshold` per row (2dp, `—` for NULL)
- Filter sections: CATEGORY (symbol/TF/strategy/day filter/ADR gate/stars), PERF (win%/trades/avg R/total R/max DD/RF), DIR (directional long+short)
- **Analysis sub-tab** — 12 lazy-loaded cards; `min_trades` input + "◈ Scope to config" toggle

### Chart page

- Watchlist sidebar; timeframe/days selectors
- **Strategies row** — 6 collapsible group toggles: Structure (bos/liquidity_sweep/eqh_eql/order_block/fvg), Fibonacci (fib_golden_zone/ote_entry), Price Action (wick_fill/marubozu/inside_bar/trend_day), Candlestick (engulfing/pin_bar/hammer_hanging_man/doji/morning_evening_star), Flow (smt_divergence/cvd_divergence/funding_reversion), Session (orb/seasonality); taxonomy in `STRATEGY_GROUPS` in `Chart.svelte`; groups absent from active TOML hidden
- **Indicators row** — EMA 20/50/200, RSI 14, **Zones** (7 toggles: FVG, OB, EQH·EQL, BOS, Fib Zone, OTE, Swings)
  - FVG/OB/Fib/OTE — HTML overlay divs; EQH/EQL/BOS — line series
  - Active zones extend to right edge; inactive end at `close_ms` (dimmed)
  - Colors: bull=`#56d364`, bear=`#f85149`, fib=`#e3b341`, ote=`#f0883e`
- **Range Levels** — MO, DO, PDH/PDL, WO, PWH/PWL, Mon H/L; solid lines from origin to right edge; HTML labels
- **CME Gap** — semi-transparent box for most recent Fri 21:00–Sun 22:00 UTC window; **15m and 1h only** (pill hidden on 4h/1d — `timeToCoordinate` returns null for inter-candle timestamps on coarser TFs)
- Time axis + crosshair: **MYT (UTC+8)** via `localization.timeFormatter`
- Signal markers; funding/OI sub-panels; Fib overlay; live candle via SSE + 30s seed refresh

### Stats page

- 10-card grid: P1/P2 (incl. P1 strong%), ADR, hourly distribution, DOW patterns (incl. Str H/Str L), session breakdown, weekly P1/P2, avg return by day, weekly P2 timing with flip risk, Daily Distance, P1 Wick Rank
- Default lookback: 365d
- "Daily Distance" + "P1 Wick Rank" — live, never cached
- Weekly P2 Timing: All/Bullish P1/Bearish P1 toggle; live "This week" banner with DOW, move%, distance bucket, conditioned probabilities

### Nav

- Shows active config name chip when server has a config loaded
- Chart + Stats default symbol: first config symbol → coins.json fallback
