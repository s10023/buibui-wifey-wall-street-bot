# CLAUDE.md

This file provides instructions for Claude Code when working in this repository.

## Fork lineage

This repo is a fork of `s10023/buibui-moon-trader-bot` (parent), forked 2026-05-14 and frozen at parent commit `635ed5a`. It is being repurposed from a Binance crypto bot into a yfinance-backed US-equities signal bot.

- **Sister memory** (parent's accumulated wisdom — strategy edges, regime classifier history, F8/F9/T2 work, sweep findings, gate architecture) lives at `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`. Read it when working on a feature that exists in both repos (back-port / forward-port). Skip parent's `Current State` and any crypto-specific findings (`smt_pairs`, `funding_reversion`, BTC/ETH/SOL cells, CME gap).
- **Active work**: Phase A (signals only, equities) — see `docs/superpowers/specs/2026-04-10-tradfi-equity-fork-design.md` and `docs/superpowers/plans/2026-04-10-tradfi-equity-fork.md`. Phase B (order layer + broker pick) is deferred.
- **In-flight task queue** (per plan §0.e): T6 → T7 → T9 → T10 → T2 → T3 → T4 → T5 → T11 → re-enable Actions → T8 → T13 → T15 → T16.

## Project Overview

Buibui Wifey Wall Street Bot — a yfinance-backed US-equities signal bot (Phase A: signals only). Analytics/backtest stack (DuckDB), a multi-strategy signal engine with Telegram alerts, and a FastAPI + Svelte web UI. Phase B = order layer (broker TBD), deferred. Python 3.11+, managed with Poetry.

## Key Commands

After making **any** Python code change:

```bash
make lint-py        # ruff format + lint
make typecheck      # mypy strict
make test           # full pytest suite
```

For Markdown changes: `make lint-md`.

For UI / API changes: `make web-build` (production bundle) or `make web-dev` (Vite dev server).

For routine DB refresh after backtest/strategy changes: `make db-update` (= `db-update-backtest` → `db-update-recalibrate` → `regression-update`).

## CLI

`wifey.py` is the single CLI entry point with subcommands:

- `wifey signal watch | test` — live signal daemon / historical replay (rewire pending T10 — currently imports deleted `utils/binance_client`)
- `wifey analytics backfill | sync` — OHLCV ingestion (will switch to yfinance in T2–T5)
- `wifey backtest` — run/save backtests (sweep, combo, cross-TF modes)
- `wifey digest` — pre-canned analytics queries
- `wifey param-audit | param-sweep` — WFO parameter tools
- `wifey recalibrate` — refresh star ratings
- `wifey web` — start FastAPI backend

Each Makefile `wifey-*` target wraps the equivalent CLI invocation.

> The legacy `wifey monitor price | position` subcommand was removed in T1 (live mode + `utils/binance_client` deleted); `monitor/price_monitor.py` and `monitor/position_monitor.py` remain on disk with broken imports and will be either rewired for equities or removed entirely in T16.

## Project Structure

- `wifey.py` — thin CLI entry shim (delegates to `cli.main:main`)
- `cli/` — argparse subcommand package: `main.py` builds the top-level parser and dispatches to per-subcommand modules (`monitor.py` legacy, `signal.py`, `analytics.py`, `backtest.py`, `digest.py`, `param.py`, `recalibrate.py`, `web.py`); `_common.py` shared helpers
- `monitor/` — legacy crypto price/position monitor; **non-functional after T1** (`utils/binance_client.py` + `monitor/live_price.py` + `monitor/live_position.py` deleted). Remaining `price_monitor.py` / `position_monitor.py` / `price_lib.py` / `position_lib.py` have broken imports; fate decided in T16 (rewire for equities or delete).
- `analytics/` — analytics data layer (DuckDB-backed). See `.claude/context/analytics.md` for full module API reference.
  - `store/` — DB layer split into 8 modules: `schema.py` (`init_schema`, `DEFAULT_DB_PATH`), `market_data.py` (OHLCV upsert + getters), `signals.py` (`upsert_signals`, `get_signals_history`, `upsert_signal_outcome`), `backtest_runs.py` (`upsert_backtest_run`, `upsert_backtest_trades`, `list_backtest_runs`, `get_win_rate_by_strategy`), `backtest_cache.py` (`BacktestSnapshot`, `get/put/prune_backtest_cache`), `confidence.py` (`upsert_confidence_ratings`, combined + directional getters), `combos.py` (combo + cross-TF combo upsert/list/lookup), `stats_cache.py`. `_common.py` holds the sealed `_upsert` register/unregister helper. `data_store.py` is a thin re-export shim for the 30+ external import sites. (T4 dropped `taker_buy_volume` column + `funding_rates` / `open_interest` tables — yfinance OHLCV has no taker data and equities have no funding/OI; legacy migration block removed.)
  - **CRITICAL**: `_upsert` (in `store/_common.py`) uses explicit `conn.register`/`conn.unregister` in try/finally — never switch to implicit replacement scan (causes malloc heap corruption). Never drop the try/finally.
  - `data_fetcher.py` — yfinance-backed OHLCV fetch (T3, since 2026-05-14). `fetch_bars(symbol, interval, start_ms, limit=BARS_MAX_LIMIT)` wraps `utils.yfinance_client.fetch_history`; supports `1h | 4h | 1d | 1wk` (4h synthesised by resampling 1h anchored to 13:30 UTC). `OHLCV_COLUMNS` no longer carries `taker_buy_volume`. T5 still owes `data_sync.py` rewire.
  - `data_sync.py` / `analytics_runner.py` — sync orchestration + thin runner (still Binance-shaped; T5 rewires)
  - `strategies/` — strategy signal detection package (one file per `detect_*` function): 21 detector modules (`wick_fills.py`, `marubozu_retest.py`, `orb_breakout.py`, `liquidity_sweep.py`, `fvg.py`, `market_structure.py` = `bos`, `funding_extreme.py`, `smt_divergence.py`, `eqh_eql.py`, `order_block.py`, `trend_day.py`, `engulfing.py`, `pin_bar.py`, `inside_bar.py`, `hammer_hanging_man.py`, `doji.py`, `morning_evening_star.py`, `fibonacci_retracement.py` legacy, `fib_golden_zone.py`, `ote_entry.py`, `ema.py`); `_base.py` (`ParamSpec`, `StrategySpec`, `SIGNAL_COLUMNS`); `_shared.py` (`_find_bos_swing`, `volume_confirm`, `compute_ema`, `ema_cross_count`, `is_trending`, `_empty_signals`, `_signals_to_df`, `_fmt_time`); `_seasonality.py` (`seasonality_stats`, `SEASONALITY_COLUMNS`); `_registry.py` (explicit-tuple-driven assembler holding `STRATEGY_REGISTRY` (20 entries), `DETECTOR_REGISTRY` (18; `seasonality` / `smt_divergence` / legacy `fibonacci_retracement` excluded), `KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES` (now includes `"trend"` for `ema`), `STRATEGY_TYPE_GROUPS`, `INCOMPATIBLE_PAIRS`, `patch_confidence_scores`); `__init__.py` eager re-exports. The package is the public entry — `from analytics.strategies import ...` (the prior `indicators_lib.py` shim was removed in strat-3). `cvd_divergence` was stripped in T7 — yfinance OHLCV has no taker_buy_volume so the detector can never fire on equities.
  - `backtest/` — backtest engine split into 6 modules: `engine.py` (`Trade`, `BacktestResult`, `run_backtest`, `_compute_atr14`), `gates.py` (`_is_low_volume`, `_is_volume_spike`, `filter_signals_by_day`), `combo.py` (`ComboBacktestResult`, `run_combo_backtest`), `cross_tf.py` (`CrossTfComboBacktestResult`, `run_cross_tf_combo_backtest`), `formatters.py` (10× `format_*` helpers + `_tf_sort_key`). `backtest_lib.py` is a thin re-export shim.
  - `backtest_runner.py` / `backtest_config.py` — thin runner + TOML config loader for sweep mode
  - `param_sweep.py` — WFO sweep lib; `run_param_sweep` / `run_strategy_audit`; parallelized via `ProcessPoolExecutor`
  - `digest_lib.py` — 12 pre-canned SQL queries; `run_digest`; `DigestScope`; powers `wifey digest` + analysis API
  - `zones_lib.py` — structural zone extraction (geometry only): FVG, OB, EQH/EQL, BOS, Fib, OTE, swing points
  - `overnight_gap_lib.py` — equity session-gap features (`OvernightGap` dataclass, `get_overnight_gap`, `gap_fill_warning`). Replaces parent's `cme_gap_lib`; consumed by T10 scanner rewire and T8 ORB anchor.
  - `signal/` — signal scanner split into 10 modules: `scanner.py` (`scan_symbol` + `run_scan_cycle` 3-phase fan-out), `types.py` (`SignalEvent`, `StatsContext`, `ConfluenceData`), `gates.py` (`_filter_signals_by_adr`, `_is_adr_exempt`, `_apply_direction_filter_gate`, `_apply_htf_ema_gate`, `_apply_regime_gate`), `resolvers.py` (10× `_resolve_*` helpers, incl. `_resolve_atr_sl_floor`), `bt_cache.py` (`_compute_backtest`, `_backtest_summary`), `atr_floor.py` (`_apply_atr_floor` — F9 ATR-as-min-SL widener for the live path; mirrors backtest engine), `outcome_backfill.py` (`backfill_outcomes` — forward-walks OHLCV to resolve outstanding `signal_alert_outcomes` rows; called once per cycle from `signal_runner`), `stats_context.py` (`_compute_stats_context`), `cofire.py` (live + cross-TF co-fire detection), `_common.py` (`_bt_mem_cache`, `_reset_bt_cache`, timeframe parsing). `signal_lib.py` is a 4-line re-export shim.
  - `signal_config.py` — `SignalWatchConfig`, `BacktestFilterConfig`, `BiasConfig`, `ComboConfig`; TOML `extends` support
  - `stats/` — stats package split per dimension: `bundle.py` (top-level `compute_all` orchestrator), `p1p2.py`, `adr.py`, `dow.py`, `hourly.py`, `session.py`, `daily_distance.py`, `weekly_state.py`, `weekly_p1p2.py`, `weekly_p2_timing.py`, `weekly_flip_risk.py`, `weekly_wick.py`. `_common.py` shared helpers; live fields injected by `bundle._inject_live_fields()`. `stats_lib.py` is a re-export shim.
  - `signal_runner.py` — daemon thin wrapper; OHLCV cache; combo lookup refresh every 10 cycles
  - `signal_test_runner.py` — historical replay: no DB writes, no cooldown; `--at` / `--lookback`
  - `recalibrate_lib.py` / `recalibrate_runner.py` — compute + write star ratings to DB or source
  - `perf_timer.py` — `timed(label)` context manager
  - `regime.py` — §6 regime classifier (`trend`/`range`/`high_vol`/`unknown`); pure function over OHLCV; wired as Phase 2 live gate (soft mode shipped 2026-05-10) per `docs/redesign/buibui-redesign.md`
- `signals/` — signal detection daemon package (alerting + dedup only — detection lives in `analytics/`). See `.claude/context/signals.md` for full reference.
  - `registry.py` — `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` (19 actionable strategies; `seasonality` / `fibonacci_retracement` excluded)
  - `cooldown_store.py` — two-layer dedup: candle watermark + cooldown timer; JSON-persisted to `signal_state.json`
  - `alert_formatter.py` — `SignalEvent`, `StatsContext`, `ConfluenceData`; 6-section alert layout; W1–W8 candle warnings
  - `DEFAULT_DB_PATH` lives in `analytics/store/schema.py` (re-exported via `analytics.data_store`) — import from either, do not redefine in runners
- `utils/` — shared utilities:
  - `config_validation.py` — config schema validation: `validate_coins_config` (legacy) + `validate_stocks_config` (Phase A equities, since T6)
  - `telegram.py` — Telegram message sending
  - `live_store.py` — shared in-memory store for live WebSocket data
  - `live_loop.py` — shared Rich live display loop logic
  - `yfinance_client.py` — yfinance helper (`fetch_history`, `YF_INTERVALS`); no auth, no module-level side effects; normalises Yahoo's tz-aware America/New_York DataFrame to canonical lowercase OHLCV + UTC-naive DatetimeIndex (T2, since 2026-05-14). 4h is not native — callers resample 1h→4h (T4).
- `web/` — web layer (Phase 4 + 5). See `.claude/context/web.md` for full API + UI reference.
  - `api/` — FastAPI: routers (config, ohlcv, fib, signals, backtest, positions, prices, stream, stats, zones); `GET /api/active-config`, `GET /api/zones`, `GET /api/backtest/analysis`; stats live fields via `_inject_live_fields()`
  - `ui/` — Svelte 5 + Vite; pages: Chart, Backtest, SignalFeed, Positions, Prices, Stats; build: `make web-build`
- `trade/open_trades.py` — legacy Binance Futures order opener (manual/CLI use; wired via `make wifey-open-trades`). **Phase A out of scope** — Phase B will replace with an equities broker adapter (broker TBD).
- `tools/` — one-shot analysis scripts (not part of the daemon/CLI surface):
  - `strategy_edge_audit.py` — Phase 0 strategy edge audit; aggregates `backtest_trades` by (strategy × tf × regime × session) + combo uplift; deterministic KILL/DEMOTE/KEEP rule. Run via `PYTHONPATH=. poetry run python tools/strategy_edge_audit.py`. See `docs/redesign/buibui-redesign-phase0.md`.
  - `live_outcomes_report.py` — read-only spot-check of `signal_alert_outcomes` after the T2 backfill worker runs; reports the resolved/open mix, per-(strategy, tf, direction) win rate + avg_r, and per-strategy aggregate. Stop-gap until a Stats UI card lands. Run via `PYTHONPATH=. poetry run python tools/live_outcomes_report.py [--days N] [--min-n N]`.
- `tests/` — pytest suite; tests import from lib modules and pass mock dependencies directly
- `.claude/context/` — long-form module references (`analytics.md`, `signals.md`, `web.md`) split out to keep this file lean
- `config/coins.json` — legacy per-symbol crypto config (gitignored; see `coins.json.example`).
- `config/stocks.json` — Phase A US-equities watchlist (gitignored; see `stocks.json.example`). 13 symbols: AAPL/MSFT/GOOGL/AMZN/META/ORCL/ADBE/NVDA/AMD/TSLA/MSTR/SPY/QQQ. Schema is `{ticker: {sl_pct: float in (0, 1.0)}}` validated by `utils.config_validation.validate_stocks_config`.
- `config/strategy_params.toml` — shared base config inherited via `extends = "strategy_params.toml"` by `signal_watch.toml`, `signal_watch_all.toml`, `signal_watch_weekdays.toml`. Contains `[smt_pairs]`, `[bias]`, `[backtest]` defaults, per-strategy `volume_suppress` / `volume_spike_boost` flags, and `tp_r_long` / `tp_r_short` directional overrides. `conservative.toml` / `scalping.toml` / `swing.toml` do **not** extend it — they carry their own `[bias]` / `[backtest]` values.

## Code Style

- **Linter + Formatter**: ruff (replaces black; handles linting, import sorting, and formatting)
- **Type checker**: mypy (strict — `disallow_untyped_defs = true`)
- **All functions must have type annotations** including return types (`-> None` for test methods)
- **Markdown linter**: markdownlint-cli2
- Use `from typing import Any` for mock parameters in tests

## Testing

- Framework: pytest + unittest.mock
- Tests must not make real network calls — lib functions accept a `client` parameter; tests pass a `MagicMock` directly
- Analytics tests use `duckdb.connect(":memory:")` for full DB isolation — never touch the real `analytics.db`
- Run: `make test` or `poetry run pytest tests/ -v`
- **Regression tests**: `make test-regression` — compares backtest pipeline output to golden JSON files in `tests/fixtures/`; skips if fixture parquets are absent; run `make regression-update` to regenerate golden files after intentional changes

## Dependencies

- Managed via Poetry: `poetry install --no-root`
- Runtime: `duckdb` (analytics DB), `pandas` (DataFrames), `pyarrow` (parquet fixture I/O)
- Dev deps: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs, pandas-stubs
- Never modify `poetry.lock` manually — use `poetry add` / `poetry remove`

## Documentation

When changes affect project structure, CLI commands, features, or behavior, update `README.md` to stay in sync.

## Session Memory Protocol

At the end of every session where anything changed (features, bug fixes, refactors, decisions), automatically update the **Current State** section in `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/MEMORY.md`. Do not wait to be asked.

Fields to keep current:

- Last session summary (one line: what changed)
- Open questions / pending decisions (or "none")

## Agent Skills

Skills live in `.claude/skills/<name>/SKILL.md` (project-specific, committed to repo) and are invoked with `/skill-name`. Each encapsulates a recurring workflow so you don't need to re-explain it. Use them proactively.

| Skill | Invoke | When to use | Cadence |
| ----- | ------ | ----------- | ------- |
| `sanity-check` | `/sanity-check` | Full project health check: git hygiene, docs sync, wiring audit, architecture review, skills freshness | Weekly or after any large refactor |
| `atr-sweep` | `/atr-sweep` | Find optimal ATR SL multiplier per strategy × TF; translates to `atr_sl_multiplier` TOML overrides | After any SL-related change or when backtests show high fee drag |
| `wfo-sweep` | `/wfo-sweep` | **Full automated WFO chain**: param-audit → param-sweep → apply → backtest → recalibrate → commit. One command to refresh all tp_r for a config. | When a config feels stale or after any major strategy/detector change |
| `config-refresh` | `/config-refresh` | Full TOML refresh: fix strategy_timeframes gaps, run TP sweep, update tp_r per strategy × TF, commit | When a signal_watch config feels stale, after detector rewrites, or when weekdays config drifts behind signal_watch.toml |
| `backtest-findings` | `/backtest-findings` | Interpret any sweep table (ATR/TP/volume/duration) and commit winners to TOML | After every sweep run |
| `param-sweep-apply` | `/param-sweep-apply` | Auto-apply WFO param-sweep/param-audit results: parse pasted tables, pick best tp_r per strategy × TF, edit TOML, run backtest + recalibrate | Paste results and invoke — use when running sweeps manually outside `/wfo-sweep` |
| `recalibrate` | `/recalibrate` | Update strategy star ratings in the `confidence_ratings` DB table from accumulated backtest runs (feeds Backtest UI stars, Telegram alerts, live signal-watch quality gate) | After any `make wifey-backtest SAVE=1` adds new runs |
| `volume-sweep` | `/volume-sweep` | Test `volume_suppress` per strategy; compare High Vol vs Low Vol avg R | When adding a new strategy; after entry logic changes that affect signal frequency |
| `new-strategy` | `/new-strategy` | Guided 4-file checklist for adding a new strategy (`analytics/strategies/<name>.py`, `_registry.py`, `signals/registry.py`, tests) | Every time a new strategy is added |
| `backtest-run` | `/backtest-run` | Quick reference for all `wifey backtest` invocations and flags | Any time you need a backtest command and can't remember the flags |
| `investigate-strategy` | `/investigate-strategy` | Debug why a strategy did/didn't fire on a specific candle using `wifey signal test` | When asked to investigate, diagnose, or replay a signal |
| `signal-watch` | `/signal-watch` | Signal daemon workflow, TOML config reference, signal flow diagram | When configuring or debugging the live signal scanner |
| `pr-summary` | `/pr-summary` | Write PR title + summary + test plan to `/tmp/pr-<branch>.md` | After finishing any feature branch |
| `post-branch` | `/post-branch` | Behaviour-gated docs sweep: diff branch changes against CLAUDE.md / README.md / MEMORY.md / Makefile / docker-compose.yml / `.claude/context/`, propose targeted edits, append "Documentation updates" to PR body. Skips for pure refactors. | Immediately after `gh pr create`, before reporting the PR URL |
| `stats-dashboard` | `/stats-dashboard` | Stats page architecture, card inventory, adding new cards, timezone constraints | When working on Stats page or `stats_lib.py` |
| `db-update` | `/db-update` | Routine `make db-update`: backtest (3 configs) → recalibrate → regression golden refresh | After any detector / strategy / config change that affects ratings or fixtures |
| `data-backfill` | `/data-backfill` | OHLCV ingestion via `wifey analytics backfill` / `sync` | First-time setup, wiped DB, new symbol or timeframe, filling a data gap |
| `confluence-backtest` | `/confluence-backtest` | Cross-TF (`--cross-tf`) and same-TF (`--combo`) co-firing backtests; HTF/LTF pair sweeps; post-run spot-check via `tools/combo_health.py` | After adding a strategy, changing entry logic, tuning the live `[combo]` gate, or to confirm combo tables are healthy after a refresh |
| `frontend-svelte` | `/frontend-svelte` | Svelte 5 + Vite UI workflow for `web/ui/` — pages, stores, lightweight-charts, dev/build commands | Any work under `web/ui/`; pair with `/frontend-design` for visual work |

**Always load `/frontend-design` before any Svelte/CSS/UI changes.**

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, `config/stocks.json`, or IDE-specific files
- **Per-repo git identity is mandatory** before any commit: this account uses `s10023 <ngkhaijian@gmail.com>` (global config inherits a work identity and will mis-attribute commits). Verify via `git config --local user.email` before committing. SSH alias `git@github.com-personal:...` is also required for s10023 remotes — see auto-memory `reference_ssh_host_aliases.md` for the full recipe.
- **`gh` commands in this repo must pass `--repo s10023/buibui-wifey-wall-street-bot` explicitly.** The user's `gh` default repo is intentionally set to the parent `s10023/buibui-moon-trader-bot` (primary project), so `gh pr view N` / `gh pr list` / `gh pr create` without `--repo` will resolve against the parent and either fail or target the wrong repo. This is a preference, not a fix-to-be-found — do not run `gh repo set-default` to "solve" it.
