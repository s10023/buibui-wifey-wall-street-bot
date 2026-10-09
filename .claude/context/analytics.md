# Analytics Module Reference

Detailed API reference for `analytics/`. Load this when working on any analytics module — this file is the target of CLAUDE.md's own pointer: "analytics/ — analytics data layer (DuckDB-backed). See `.claude/context/analytics.md` for full module API reference."

## data_store.py — DB schema + upsert/query helpers

The package physically backing these helpers is `store/`, split into 9 modules: `schema.py`
(`init_schema`), `market_data.py` (OHLCV upsert + getters), `signals.py` (`upsert_signals`,
`get_signals_history`, `upsert_signal_outcome`), `backtest_runs.py` (`upsert_backtest_run`,
`upsert_backtest_trades`, `list_backtest_runs`, `get_win_rate_by_strategy`), `backtest_cache.py`
(`BacktestSnapshot`, `get/put/prune_backtest_cache`), `confidence.py` (`upsert_confidence_ratings`,
combined + directional getters), `combos.py` (combo + cross-TF combo upsert/list/lookup),
`stats_cache.py`, `earnings.py` (`earnings_facts` upsert/get — the PEAD sleeve's EDGAR
diluted-EPS facts). `_common.py` holds the sealed `_upsert` register/unregister helper.
`data_store.py` is a thin re-export shim for the 30+ external import sites. `DEFAULT_DB_PATH`
lives here — import it from here rather than redefining it in a runner. yfinance OHLCV carries no
taker data and equities have no funding/open-interest, so the schema has no `taker_buy_volume`
column and no `funding_rates`/`open_interest` tables.

**`_upsert` (`store/_common.py`) must use explicit `conn.register`/`conn.unregister` in
try/finally, never an implicit replacement scan** — the implicit form causes malloc heap
corruption at `conn.close()`.

Core helpers:

- `upsert_signals(conn, df)` / `get_signals_history(conn, symbol, tf, start_ms, end_ms)`
- `list_backtest_runs(conn)` — newest-first; joins `stars`, `long_stars`, `short_stars` from
  `confidence_ratings` by `(strategy, tf, day_filter, direction)`; also partitions on
  `adr_suppress_threshold`, so ADR-on/off runs appear as separate rows.
- `upsert_backtest_run` / `upsert_backtest_trades` — `upsert_backtest_run` takes
  `universe_policy: str | None = None` (all four call sites — sweep runner, single-run CLI, scanner
  persistence, web `POST /api/backtest` — pass `load_universe_policy().to_json()`) plus two
  required keyword-only fields that identify the writer and what actually ran:
  - `origin: str` — `"sweep"` / `"single_run"` / `"live_gate"` / `"web"`, naming the writer.
    `_backtest_run_id` hashes only the parameter tuple, so a live-gate write using the sweep
    config's own `days`/`day_filter` produces an identical run_id, and `INSERT OR REPLACE` would
    silently overwrite the sweep row rather than accumulate beside it. `"sweep"` appends no
    suffix, so historical sweep run_ids are unchanged; `origin` is required rather than defaulted
    so mypy forces every new call site to declare its identity.
  - `live_parity: str | None` — pass `cfg.live_parity.identity()`, the gate set that **executed**,
    never a declared block. The shared base runs five live-parity gates and `cli/backtest.py` can
    override any of them per run, so a parameter tuple alone does not identify a measurement; the
    sweep and `single_run` writers pass the real identity, while `live_gate` and `web` pass `None`
    because `run_backtest` runs there with no `LiveParityConfig` at all. `identity()` returns
    `None` when no gate is on, so historical run_ids are unaffected.

  mypy cannot enforce a required kwarg through a `**dict` splat, and
  `tests/test_schema_insert_arity.py`'s `SCANNED_DIRS` excludes `tests/`, so six test-local
  positional INSERTs surfaced only under the full suite. Audit:
  `docs/audits/2026-08-26-run-id-live-parity-axis.md`.
- `upsert_confidence_ratings(conn, config_name, ratings, win_rates, day_filter=None, direction="combined")`
  — PK `(config_name, strategy, tf, direction)`; direction = `'combined'` | `'long'` | `'short'`.
- `get_confidence_ratings(conn, config_name, direction="combined")` /
  `get_directional_confidence_ratings(conn, config_name)` →
  `{strategy: {tf: {"long": stars, "short": stars}}}`.
- `prune_undeclared_confidence_ratings(conn, config_name, declared)` — deletes a config's rows (all
  directions) for cells it no longer declares, returning the pruned `(strategy, tf, direction)`
  triples. The upsert is insert-or-replace with no delete, so a dropped cell keeps its stars
  indefinitely without this — `fib_golden_zone × 4h` held 3★ +0.4688 for 2.5 months after removal. A
  declared cell that is merely unrated this run is not pruned.
- `backtest_runs` carries `adr_suppress_threshold REAL NULL`, `recovery_factor DOUBLE NULL`,
  `volume_suppress BOOLEAN NULL`, `universe_policy TEXT NULL`, `cost_model TEXT NULL` and
  `live_parity TEXT NULL` (NULL covers both "no gate ran" and a pre-2026-08-26 row that ran under
  the base's five gates) — all migration-list only, never in `CREATE TABLE`, so fresh and migrated
  DBs share one physical column order; `upsert_backtest_run`'s `INSERT … SELECT` is positional.
- `_backtest_run_id` appends `|adr:X` / `|vol_suppress` / `|lp:<gates>` for a unique run_id per
  param combo, the last being `LiveParityConfig.identity()` in `GATES` order and absent when no
  gate is on; `universe_policy` is excluded from the hash (metadata, does not change P&L).
- D10 same-TF: `backtest_combos`; `upsert_combo_run` builds a stable `combo_id`
  (`symbol|tf|A+B|wN|day_filter`, no timestamp, so `INSERT OR REPLACE` applies); `list_combo_runs`;
  `get_combo_lookup` → `dict[(symbol, tf, frozenset({a,b})), row_dict]` best avg_r per pair.
- D10 cross-TF: `backtest_cross_tf_combos` keyed by
  `(symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf, window_hours, day_filter)`;
  `upsert_cross_tf_combo_run` / `list_cross_tf_combo_runs` / `get_cross_tf_combo_lookup` use an
  ordered key, not a frozenset, because HTF/LTF roles are distinct.

`tests/test_schema_insert_arity.py` (in `make test`) ties every positional INSERT to its table's
real column list, checked against the real schema (`init_schema` against `:memory:`, read back
from `information_schema.columns`) rather than parsed DDL text — the four migration-only
`backtest_runs` columns above would otherwise read as a false failure on the table they guard.
`INSERT … SELECT` is checked by name order, not just count, so a transposition of two same-typed
columns fails. Falsified against three perturbations (dropped placeholder, transposed column,
added DDL column). An unparseable INSERT must be named in `EXEMPT_TABLES` /
`DYNAMIC_INSERT_FILES` with a reason, so coverage cannot silently shrink —
`store/_common.py`'s f-string `_upsert` is the one dynamic case.

## data_fetcher.py / data_sync.py / analytics_runner.py

- `data_fetcher.py` — pure fetch: yfinance → canonical OHLCV DataFrames, no DB.
  `fetch_bars(symbol, interval, start_ms, limit=BARS_MAX_LIMIT)` wraps
  `utils.yfinance_client.fetch_history`; supported intervals `1h | 4h | 1d | 1wk` (`4h` is
  synthesised via a `1h` resample anchored to 13:30 UTC, the US RTH open). `OHLCV_COLUMNS`
  excludes `vwap` and `taker_buy_volume` (yfinance OHLCV has neither). `BARS_MAX_LIMIT = 5000`.
  Tests patch `analytics.data_fetcher.fetch_history`.
- `data_sync.py` — yfinance-backed orchestration. `backfill(conn, symbol, timeframe, start_ms)`
  pages through `fetch_bars` (no client param) until a page returns fewer than `BARS_MAX_LIMIT`
  rows, gated on `data_quality.check_ohlcv` + `quarantine` between fetch and upsert — corrupt rows
  are dropped before storage, soft anomalies logged; the return count reflects stored (clean) rows.
  `fetch_bars` keeps only the first `limit` bars at or after its start, so an unpaginated call
  whose `--since` reaches more than 5,000 bars back would silently store the oldest 5,000 and drop
  everything after the first page — measured on `^GSPC 1d`, which sat at 4,896 rows from 2007
  against 24,774 available from 1927. Pinned by `tests/test_data_sync.py::TestBackfillPaging`
  (mutation-checked); narrative in `docs/audits/2026-08-19-h021-party-regime-annual-returns.md`.
  `sync_funding_rates` / `sync_open_interest` have no equity equivalent and are not ported.
- A fetch failure arrives as an empty frame, not an exception. yfinance 1.7 hides its own
  exceptions by default (`hide_exceptions`), so a DNS outage, a dropped connection and a
  delisted ticker each log an error and return nothing; a rate limit (`YFRateLimitError`) on
  the price request still raises. `fetch_bars(..., require_data=True)` turns "the provider returned nothing" into
  `NoProviderDataError`, distinct from history with no bars after the cursor; `backfill` and
  `sync` pass the flag through and default it off, so `signal_runner`'s own sync is unchanged.
  `analytics_runner.run_sync` opts in, logs `Sync summary: F of A fetches returned no data`,
  and exits 1 only when every attempted fetch failed, so a scheduled sync notifies on an
  outage. The bar is all-or-nothing because a delisted name empties one series on every run,
  which is `make freshness-check`'s to grade. `go-live` and `session-digest` call this path
  `-`-prefixed, so its exit code stops neither. Pinned by
  `tests/test_analytics_runner.py::TestRunSyncTotalFailure` (mutation-checked) (#444).
  `sync(conn, symbol, timeframe)`
  re-fetches from the latest stored `open_time` and delegates to `backfill`, so it inherits the
  gate and also guards the adjustment basis: the re-fetched overlap bar is the only point at which
  a provider split-restatement is observable, so `sync` compares its stored close against the
  refetched one and re-syncs the whole series from `get_earliest_open_time` when it moves by more
  than `ADJUSTMENT_BASIS_TOL` (1%). Without this a split leaves a permanent fake return, because
  the provider restates history retroactively while the stored bars keep the old basis — the seam
  is created by the refresh, not by a missing one. The tolerance is safe only because
  `utils/yfinance_client.py` fetches with `auto_adjust=False` (splits are retroactive, dividends
  are not; `auto_adjust=True` would trip the guard on every ex-dividend date). It prevents a new
  seam and cannot repair a stored one — that needs a full re-backfill. Audit:
  `docs/audits/2026-09-04-split-adjustment-seams.md`. `backfill` also runs
  `trading_calendar.check_session_gaps` (N3 PR2) when at least 2 rows survive quarantine and logs
  missing NYSE sessions warn-only, never quarantined.
- `analytics_runner.py` — thin wrapper: opens the DB, resolves symbols, delegates to `data_sync`. No
  client object (yfinance is module-level, no auth).
  `_resolve_symbols(symbols, *, use_universe=False)` defaults to the
  `utils.config_validation.load_stocks_config` watchlist and logs the active universe policy summary
  (`load_universe_policy().summary()`) whenever the implicit fallback is used; `use_universe=True`
  (the `wifey analytics backfill/sync --universe` flag, threaded via `run_backfill`/`run_sync`)
  resolves the research breadth universe instead (`load_research_universe().active_symbols()`, N3)
  and logs `ResearchUniverse.describe()`. Default (no flag) is byte-identical.

## db_retry.py — retrying DuckDB connect for the write jobs

Ported from the parent (#593).

- `connect_with_retry(db_path, *, read_only=False, attempts=6, sleep=time.sleep)` opens the DB,
  waiting out a conflicting writer with a `(2, 5, 10, 15, 20)`s backoff (~52s total), then
  re-raises the underlying `duckdb.IOException` so a lock that never clears still fails loudly.
  `sleep` is injected so tests do not pay the backoff.
- `is_lock_conflict(exc)` matches on the message, because DuckDB raises a bare `IOException` for
  every I/O failure — a missing path and a busy lock are the same class. Retrying a bad path would
  turn an instant accurate failure into the same failure a minute later.
- On duckdb 1.5.5 a second process is refused even with `read_only=True` — only reader-vs-reader
  opens share, and both flags return the identical `Conflicting lock` message. Opening read-only is
  not an escape from a writer conflict, so read-only batch opens go through this helper too.
- Wired at every write open in `analytics/` and `web/` except one: `signal_runner` (8 opens per
  cycle — it writes the irreplaceable ledger, and a `--once` cron run has no next cycle to recover
  an aborted one), `analytics_runner`, `recalibrate_runner`, `backtest_runner` (4 opens), and
  `web/api/main.py`'s startup schema init. `web/api/routers/stats.py`'s cache write is deliberately
  excluded — it sits on the request path, where a ~52s retry would block the response the cache
  exists to speed up, and a lost cache write only costs one recomputation.
- The collision this guards against is two scheduled writers firing close together:
  `wifey-signal-watch.timer` (08:30 UTC) sits 20 minutes behind `wifey-backup.timer`'s 08:10 fire,
  both writing `analytics.db` with `Persistent=true`, so a resume from suspend queues both missed
  fires together. Neither unit is a daemon (both are `Type=oneshot`); `backup-analytics.sh` has its
  own lock retry too. The other realistic collision — a `make wifey-web` session up while
  `make go-live` or `make db-update` runs — stays operator-paced.
- The budget covers a brief RW open and deliberately does not cover a `make db-update` sweep,
  which holds for minutes and is meant to fail loudly rather than hang a cycle.
  `TestBudgetIsSizedForThisRepo` pins both ends so the budget cannot be inflated into an effective
  hang.

## data_quality.py — OHLCV ingest integrity monitor

Pure detection + quarantine helper for OHLCV frames. No DB, no network, no side effects. Wired into
`data_sync.backfill` between `fetch_bars` and `upsert_ohlcv`; additive — clean data is a
pass-through, so behaviour (and regression goldens) are unchanged on good data.

- `check_ohlcv(df, *, return_outlier_pct=0.5, series_ends_here=False) -> DataQualityReport` — never
  mutates the input, never logs, never raises on dirty data. Reports positional row indices
  (0..n-1 over an internally reset index).
- `DataQualityReport` (frozen dataclass) — index tuples per finding plus derived props:
  - Quarantine sets (dropped before storage, via `quarantine_idx`): `nan_idx` (NaN in any OHLCV
    field), `nonpositive_price_idx` (price ≤ 0), `bad_bar_idx` (broken geometry:
    `high < low/open/close`, `low > open/close`), `duplicate_time_idx` (later-duplicate
    `open_time`, `keep="first"`), `frozen_tail_idx` (below).
  - Warn-only sets (logged, kept — never silently delete a real 20% move or a halt bar):
    `zero_volume_idx`, `return_outlier_idx` (`|pct_change| > return_outlier_pct`),
    `suspected_split_idx` (`close` ratio within ±5% of a canonical split factor
    0.5/⅓/0.25/0.2/2/3/4/5), `nonmonotonic_idx` (`open_time` goes backwards).
  - Props: `quarantine_idx`, `has_warnings`, `is_clean` (n_rows>0 ∧ no quarantine ∧ no warnings),
    `summary()` (human-readable count string).
- `frozen_tail_idx` is a property of the series, not of a row: the maximal run of zero-volume,
  close-unchanged bars that terminates the frame, i.e. a provider forward-filling a stopped tape's
  final print. It is empty unless the caller passes `series_ends_here=True`, which defaults to
  `False` because the only production caller pages — `data_sync.backfill` hands over up to
  `BARS_MAX_LIMIT` (5000) bars at a time, so an intermediate page's last row is a paging boundary
  rather than a stopped tape. `backfill` passes `series_ends_here=(page_rows < BARS_MAX_LIMIT)`,
  true only for the page that ends the loop.
  - Position is the discriminator; row shape alone is not. Measured DB-wide: "zero volume and
    unchanged close" matches 1,368 rows, of which only 9 are dead tails — the other 1,359 sit
    mid-history in live names (808 `SW`, 231 `AMCR`, 188 `^GSPC`). Requiring the run to reach the
    end leaves exactly those 9; the nearest surviving frozen row is 81 bars from its series end.
    Zero volume alone is not a hint either — `DX-Y.NYB`/`^TNX`/`^TYX` are permanently zero-volume
    and healthy, which is why `zero_volume_idx` stays warn-only.
  - Row 0 can never start a run (no previous close in-frame), so a wholly-frozen page keeps its
    first row rather than dropping every row (which would make `_store_page` store nothing). Rows
    already in the table are out of reach of a pre-storage drop; `migrations/006_purge_frozen_tail_bars.py`
    is the one-shot for those.
- `quarantine(df, report) -> (clean, dropped)` — splits on `report.quarantine_idx`; positional,
  never mutates input; a clean report returns the whole frame plus an empty `dropped`.
- Calendar-aware session-gap detection: the pure
  `detect_session_gaps(open_times, timeframe, sessions) -> SessionGapReport` maps each `open_time`
  to an America/New_York session date and diffs it against an NYSE trading-date list (`1wk` uses the
  trading-week unit, else trading-day; expected sessions clamp to the observed `[min, max]` range).
  It takes a precomputed session list, so `data_quality.py` stays calendar-free — the NYSE list
  comes from `trading_calendar.nyse_sessions`. `SessionGapReport` carries `timeframe`, `unit`,
  `n_present`, `n_expected`, `missing`, plus `n_missing` / `has_gaps` / `summary()`.
  `data_sync.backfill` logs this warn-only: a missing session is absent data, never quarantined.

## trading_calendar.py — NYSE trading-calendar wrapper

The **only** module importing `exchange_calendars`. No DB; one process-lifetime cached calendar handle.

- `nyse_sessions(start, end) -> list[date]` — NYSE (`XNYS`) session dates in `[start, end]`, weekends/holidays excluded, sorted. The calendar is built with an explicit `1990-01-01` start (not the library's rolling today-minus-20y default), and every query is clamped to its `[first_session, last_session]` window so out-of-range dates degrade to fewer/zero sessions rather than raising `DateOutOfBounds` (gap detection is warn-only and must never crash ingestion).
- `check_session_gaps(df, timeframe) -> SessionGapReport` — bridge: derives the observed ET session-date range from `df["open_time"]`, fetches the spanning NYSE sessions, and delegates to the pure `data_quality.detect_session_gaps`. Empty frame → empty (no-gap) report. Consumed by `data_sync.backfill`.

## pundit_direction.py / pundit_horizon.py / pundit_authors.py — ledger enum guards

Three pure, IO-free modules (ported from parent #560 / #561 / #555) holding the domain of
three `docs/plans/pundit-calls.jsonl` fields. The ledger is written by the **ingest skills**
as free JSON — there is no Python write path — so nothing ever asserted these domains while
`tools/pundit_score.py` read them through `.get(…, default)` tables. Applied at
`load_ledger`, so a violation becomes a per-line warning naming the line and the value,
instead of a silent wrong number. They live in `analytics/` rather than inside the scorer so
a second reader (a ported Brief board) imports the same definition rather than re-deriving it.

- `pundit_horizon.normalize_horizon(raw) -> str` — closes a live defect: a present-but-unrecognised
  horizon took two silent fallbacks, `SCORE_TIMEFRAME` (wrong bar series — a mistyped `intraday`
  walked on `1d` instead of `1h`) and `SESSION_WINDOWS` (wrong window — 10 sessions instead of 2 or
  21); the parent guards only the second. Absence is legitimate: `None`/`""` maps to `unspecified`,
  a real enum member, so only a present unknown raises. `VALID_HORIZONS` is bound to both tables'
  keys by `tests/test_pundit_horizon.py`; adding a member without an entry in each reintroduces the
  bug.
- `pundit_direction.normalize_direction(raw) -> str` — parity/prevention rather than a repair.
  Upstream's `score_call` special-cased only `"neutral"` and then ran
  `dirsign = 1.0 if direction == "long" else -1.0`, booking every unknown and every missing value
  as a short; this fork already gated `direction not in ("long", "short")` to `UNSCORED` with the
  value echoed in the note, so `dirsign` was never reachable here. The guard moves the rejection to
  the read boundary and binds `VALID_DIRECTIONS` to that inline tuple.
- `pundit_authors.normalize_author(raw) -> str` — strips whitespace and a leading `@`, so a ledger
  written with mixed conventions groups as one person. Idempotent, hence applicable at read time on
  both sides of the ledger/priors join without a migration. No key collides in this fork's ledger
  today (4 keys / 19 rows), but it already carries both conventions at once, so the split is latent
  rather than hypothetical. It does not handle case variants, aliases/transliterations, or
  collisions — those need a curated roster this fork does not have (the parent's
  `config/pundit_roster.toml` was not ported).

## strategies/ — strategy signal detection package

The 22 `detect_*` functions and the registries live in `analytics/strategies/`, with no
`indicators_lib.py` shim. Public entry: `from analytics.strategies import ...`.

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
- `KNOWN_STRATEGY_TYPES` includes `"trend"` for `ema`. Two strategies were retired for measured
  no-edge: `fib_golden_zone` (net-negative across three sweeps: 4-symbol, 13-symbol, and a 13-symbol
  ATR `1wk` sweep); `liquidity_sweep` (net-negative on `4h`/`1d` in both configs under the same ATR
  sweep; the one positive cell, `1wk` weekdays at +0.17R, was n=23, too thin to keep the strategy
  alive).

## backtest_lib.py — pure backtest engine

`backtest_lib.py` is a thin re-export shim; the engine lives in the `backtest/` package (11
modules, below). Core surface: `Trade`, `BacktestResult`, `run_backtest`, format helpers.

- Fee drag is `2 * fee_pct * entry / risk`; `min_sl_pct` widens SLs too close to entry. When
  `Trade.cost_model` is set, `pnl_r` replaces the flat fee with the decomposed equity cost
  (`cost_model.py` — spread/impact/borrow/commission in R) priced from a causal `Trade.cost_ctx`;
  `cost_model=None` (default) keeps the flat-fee path byte-identical.
- Volume tiers: `_is_low_volume` (< 1.5× 20-candle mean) / `_is_volume_spike` (> 3× mean).
- `run_backtest(volume_suppress, volume_spike_boost, volume_suppress_long/short=None, volume_spike_boost_long/short=None, tp_r_long/short=None, atr_sl_floor=False, *, live_parity: LiveParityConfig | None = None, bias_cfg: BiasConfig | None = None, regime_series: pd.Series | None = None, strategy_params: dict[str, StrategyOverride] | None = None, htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None = None, cost_model: CostModel | None = None)`
  — directional params take precedence over symmetric ones; `atr_sl_floor=True` widens a structural
  `sl_price` via `max(structural_dist, atr_sl_multiplier × ATR14)` (a no-op otherwise, since every
  active strategy emits a structural `sl_price`, which short-circuits the bare-ATR branch).
  `live_parity` / `bias_cfg` / `regime_series` / `strategy_params` / `htf_slope_series_by_anchor` /
  `cost_model` are all keyword-only; every default is `None`, and an all-`None` call is a no-op.
- **Live-parity gate chain** — `run_backtest` replays the live gate stack in this order, each gate a
  no-op unless its inputs are supplied: regime (per-signal `iloc[-2]` regime lookup at the signal's
  `open_time`, grouped and passed through live `_apply_regime_gate`); direction_filter (a pure
  per-event check on `StrategyOverride.suppress_long`/`.suppress_short`, needs `strategy_params`);
  HTF-EMA (resolves slope at each signal's `open_time` via `_resolve_series_at` from
  `htf_slope_series_by_anchor` keyed by `(anchor_tf, period, slope_lookback)`, then groups events
  through live `_apply_htf_ema_gate`); ADR bias (`_apply_adr_bias_gate_to_signals` splits signals by
  per-direction `_is_adr_exempt(strategy, direction)` and reuses live `_filter_signals_by_adr` on
  the non-exempt slice — wifey has only strategy-wide `adr_exempt`, not the parent's per-direction
  overrides; the runner's legacy ADR pre-filter is skipped when `live_parity.is_on("adr_bias")` to
  avoid double-filtering); cross-strategy conflict resolver (applied at the runner level, engine
  path unchanged — `_collect_sweep_results` splits into detect → resolve → backtest+save phases,
  pools events per `(symbol, tf)` via the engine's `_df_to_events` adapter, calls
  `_apply_conflict_resolver` with a `confidence_ratings.avg_r` lookup keyed on `cfg.config_name`,
  and redistributes survivors via `_events_to_df`); cooldown, applied last, after ADR
  (`_apply_cooldown_gate_to_signals` walks signals in `open_time` order against a per-call
  `_CooldownState` ledger keyed by `(symbol, tf, strategy, direction)`, dropping a row when
  `open_time < last_fire + cooldown_bars × tf_ms`, mirroring live `cooldown_store` candle-watermark
  semantics; `_resolve_cooldown_bars` reads `_DEFAULT_COOLDOWN_BARS_PER_TF`, wifey's equity map
  `{"4h": 2, "1d": 1, "1wk": 1}` rather than the parent's intraday
  `{"15m": 4, "1h": 3, "4h": 2, "1d": 1}`, unknown TF → 1, overridable via TOML
  `[backtest.live_parity.cooldown_bars]` (`live_parity.cooldown_bars_per_tf`); state is
  instantiated inside `run_backtest()` per call so two identical back-to-back calls are byte-equal).
- When `cost_model` is set, `run_backtest` builds a causal per-signal `CostContext` (trailing
  dollar ADV + daily sigma ending at the signal bar, from OHLCV) and stamps each `Trade`; the
  default `None` keeps goldens byte-identical.
- `_df_to_events(signals, symbol, timeframe, strategy) -> list[SignalEvent]` +
  `_events_to_df(events, original_df) -> pd.DataFrame` adapt between backtest signal frames and
  the live `SignalEvent` shape, letting backtest signals feed straight through
  `analytics/signal/gates.py`. Gates only ever drop events, so `_events_to_df` filters
  `original_df` to the surviving `open_time` set and preserves dtypes.
- `Trade.low_volume` / `Trade.volume_spike` tag volume tier per trade. `BacktestResult` exposes
  `low/normal/spike_vol_closed_trades` + `*_avg_r` and 6 directional×volume cross-tabs
  (`long/short_low/normal/spike_vol_*`). `format_volume_split()` prints the 3-way table,
  `format_directional_volume_split()` the ↑/↓ × Low/Normal/Spike cross.
- Rolling detectors (`ote_entry`, `order_block`, `eqh_eql`) fire at every historical candle;
  last-candle-only detectors fire at most once per run.
- `BacktestResult` directional split: `long/short_closed_trades`,
  `long/short_win_count/rate/avg_r/total_r`. `max_drawdown_r` is peak-to-trough;
  `recovery_factor` is `total_r / max_drawdown_r`, `0.0` when there is no drawdown.
- D10 same-TF: `ComboBacktestResult`; `_find_cofire_signals` (greedy ±N-candle, same direction,
  each B used once); `run_combo_backtest`; `format_combo_table`.
- D10 cross-TF:
  `CrossTfComboBacktestResult(strategy_htf, strategy_ltf, tf_htf, tf_ltf, window_hours, result)`;
  `_find_cross_tf_signals` matches an LTF signal to any same-direction HTF signal within
  `[ltf_time - window_hours, ltf_time]` (no exclusivity); `run_cross_tf_combo_backtest`;
  `format_cross_tf_combo_table`.

`backtest/` package modules:

- `fills.py` — the one gap-fill rule, shared with the live resolver. `gap_fill_price` returns the
  bar open when the bar opened through the level, else the level; `level_is_on_the_expected_side`
  gates it so a malformed row is never priced as a gap, and it is the single definition
  `implied_tp_r` routes through. Kept separate from `engine.py` so the live resolver can import it
  without pulling in the engine, which is what keeps the two books from drifting on the rule they
  share. It is symmetric by construction — pricing only the adverse tail installs a mirror bias
  and overstates the correction by about 65% (audit `docs/audits/2026-08-20-symmetric-gap-fill.md`).
- `engine.py` — `Trade`, `BacktestResult` (with a `sharpe` property, per-trade R Sharpe),
  `run_backtest` and its keyword-only live-parity params, `_compute_atr14`, the
  `_df_to_events`/`_events_to_df` adapters, and the gate-chain helpers described above:
  `_resolve_regime_at` (mirrors live `iloc[-2]` lookup via `np.searchsorted`) +
  `_apply_regime_gate_to_signals` (calls live `_apply_regime_gate` verbatim); `_resolve_series_at`
  (numeric sibling for HTF slope) + `_apply_direction_filter_gate_to_signals` +
  `_apply_htf_ema_gate_to_signals` (reuses live `_apply_htf_ema_gate` verbatim);
  `_apply_adr_bias_gate_to_signals`; `_apply_cooldown_gate_to_signals` + `_resolve_cooldown_bars`.
- `gates.py` — `_is_low_volume`, `_is_volume_spike`, `filter_signals_by_day`.
- `combo.py` — `ComboBacktestResult`, `run_combo_backtest`.
- `cross_tf.py` — `CrossTfComboBacktestResult`, `run_cross_tf_combo_backtest`.
- `formatters.py` — 10 `format_*` helpers + `_tf_sort_key`.
- `live_parity_config.py` — `LiveParityConfig`, the frozen dataclass of live-parity gate toggles
  (regime, direction_filter, HTF-EMA, ADR bias, conflict resolver, cooldown).
- `cv_splits.py` — `CvConfig`, `FoldSplit`, `fold_bounds`, `purged_kfold_split`: purged+embargoed
  K-fold CV splits (López de Prado, AFML ch. 7). Pure pandas, no DB/engine imports. Embargo
  defaults to ~1% of rows; purge defaults to 0 because per-segment truncation already censors
  boundary-straddling trades as `outcome="open"`.
- `cost_model.py` — the equity cost model: frozen `CostModel` + `cost_r(trade, ctx)` decomposed as
  spread (dollar-ADV liquidity buckets) + sqrt impact + short borrow + commission, all in R;
  `build_cost_context` derives causal trailing ADV/sigma from OHLCV; `cost_model_from_toml` parses
  the `[backtest.cost_model]` block. Wired into `Trade.pnl_r` / `run_backtest(cost_model=...)`,
  both config loaders, the sweep runner, the live bt_cache/scanner path, the regression-golden
  harness (`test_regression.py`), and stamped on `backtest_runs.cost_model` (nullable TEXT,
  migration-list only) plus the run_id hash. It is on for the live `signal_watch` configs, which
  moved the regression goldens by design; the code path stays byte-identical when `cost_model=None`
  (the default). The single-combo `run_backtest_cmd` CLI and web `POST /api/backtest` stay
  flat-fee.
- `stats_overfit.py` — overfitting / multiple-testing controls; see the dedicated section below.

## backtest_runner.py — sweep orchestrator

Not a thin wrapper: besides opening the DB and calling the libs, it owns the sweep pipeline
(detect → resolve conflicts → backtest+save), the OHLCV and ratings caches, and the combo process
pool. It is TA-sweep code, so it stays as is while the book is frozen.

- Opens the DB, loads OHLCV, calls the strategy + backtest libs.
- `run_digest_cmd(query, min_trades, top_n)` for the CLI digest.
- `run_combo_backtest_cmd(...)` sweeps all symbol × TF × strategy pairs, skipping
  `INCOMPATIBLE_PAIRS`; parallel via `ProcessPoolExecutor` with a top-level `_combo_worker` for
  pickling; `workers=None` resolves to `min(4, cpu_count-1)`, `workers=1` bypasses the pool;
  symbols fall back to `stocks.json`.
- `_SWEEP_STRATEGIES` / `_non_seasonal` both exclude `("seasonality", "funding_reversion")`.
- `_collect_sweep_results` caches OHLCV, fetching once per `(symbol, timeframe)`, and runs as a
  three-phase pipeline: detect → resolve cross-strategy conflicts → backtest+save. `signals_map` is
  `dict[(sym, tf, strat), (ohlcv, signals, secondary)]` with `secondary` always `None` in wifey (no
  `smt_divergence`); insertion order matches `itertools.product(symbols, timeframes, strategies)`,
  and the conflict-resolution phase is a no-op when `live_parity.conflict_resolver=False`, so that
  path is byte-identical to the legacy single loop.
  `_build_confidence_ratings_map(conn, cfg)` returns `{(strategy, tf, direction): avg_r}` or `None`
  when the `conflict_resolver` gate is off;
  `_resolve_conflicts_for_signals_map(signals_map, ratings_map)` pools by `(symbol, tf)`, pools
  events via the engine's `_df_to_events`, calls `_apply_conflict_resolver` per `open_time` moment
  with the avg_r resolver (directional → combined → 0.0), and redistributes survivors via
  `_events_to_df`, mutating the map in place. `BacktestSweepConfig.config_name` (populated by
  `load_backtest_config` to the TOML stem) keys the ratings lookup. Caches (regime / HTF slope /
  ratings) are built once in `run_backtest_sweep` and threaded into `_collect_sweep_results` via
  keyword-only params so all three sweep modes (single / tp_r / atr) share one build.
- `run_cross_tf_combo_backtest_cmd(...)` sweeps symbol × (tf_htf, tf_ltf) × all strategy ordered
  pairs; `_cross_tf_combo_worker` chunks by `(symbol, tf_htf, tf_ltf)`;
  `_DEFAULT_HTF_LTF_PAIRS = [("4h","1h"),("1d","4h"),("1d","1h"),("1wk","1d"),("1wk","4h")]` — every
  TF must be an `_INTERVAL_CONFIG` key, and an unsupported one yields "no data" skips rather than
  raising. Pinned by
  `tests/test_cross_tf_cofire.py::test_default_htf_ltf_pairs_use_supported_intervals`.

## perf_timer.py

- `timed(label)` context manager — prints `[perf] label: Xs`; import via `from analytics.perf_timer import timed`

## regime.py

- `classify_series(df, timeframe) → pd.Series[str]` labels each row `trend`/`range`/`high_vol`/
  `unknown` per §6 of `docs/redesign/buibui-redesign.md`: `high_vol` if ATR-14% ≥ 90-day rolling
  80th-percentile; else `trend` if `|EMA-50 slope|` ≥ 0.5% over 10 bars; else `range`; `unknown`
  for rows lacking enough history.
- Bar counts come from `cost_model.BARS_PER_DAY`, the one definition — do not maintain a private
  copy here. A crypto-era copy (`4h: 6`, `1h: 24`) made the "90-day" window read as 540 bars
  (~270 sessions); correcting it to the shared table moved 12.02% of `4h` labels, with zero
  dispatch/ratings blast radius (soft mode). Unknown timeframes fall closed (`ValueError`),
  unlike `bars_per_day_for_tf`, which falls open to `1.0`.
- `atr_window_bars(bars_per_day) → (history_window, min_history)` clamps `min_periods` to the
  window, because the 50-bar floor (calibrated on intraday counts) exceeds the 18-bar `1wk`
  window, which pandas rejects; `1h`/`4h`/`1d` keep 50.
- Used by `tools/strategy_edge_audit.py`; live as a soft-mode gate, wired into `run_scan_cycle` as
  step −1 of the bias chain via `analytics/signal/gates.py::_apply_regime_gate`.

## param_sweep.py — WFO sweep lib

- `run_param_sweep(conn, strategy, symbol, tf, days, param_ranges, wfo_split, min_trades, fee_pct, top_n, adr_suppress_threshold=None, day_filter="off", atr_sl_multiplier=None, atr_sl_floor=False, *, live_parity=None, bias_cfg=None, regime_series=None, strategy_params=None, htf_slope_series_by_anchor=None, cv=None)`
  → `ParamSweepReport{rows: list[SweepRow], gate: CommitGateVerdict, n_grid}`. `gate` is
  `sweep_guard.evaluate_commit_gate` deflated over the full grid before the top-N truncation;
  callers read `.rows`. `atr_sl_multiplier`/`atr_sl_floor` are forwarded to every grid
  `run_backtest()` call for joint sweeps. The keyword-only live-parity inputs (`live_parity` +
  `bias_cfg` + pre-computed `regime_series`/`strategy_params`/`htf_slope_series_by_anchor`) are
  forwarded to both the IS+OOS `run_backtest()` calls so a WFO cell replays the live gate stack
  (regime/direction_filter/HTF-EMA/ADR/cooldown); all default `None` for a byte-identical raw-signal
  path. `tools/multi_symbol_wfo.py --live-parity` builds these inputs per config and threads them
  through — the filter-accurate fix for `wfo-filter-divergence`.
  `cv=CvConfig(mode="purged", n_folds, purge_bars, embargo_bars)` switches to purged+embargoed
  K-fold CV via `analytics/backtest/cv_splits.py`: `fold_bounds` partitions rows into K contiguous
  test folds; per fold, the pre-test train segment loses `purge_bars` tail bars and the post-test
  segment loses `resolve_embargo_bars(n)` head bars (default ~1% of rows), and per-segment
  truncation censors straddling trades as `outcome="open"`. IS pools `_dedup_trades`-deduped train
  segments across folds (preferring resolved instances over open ones for the same
  `(signal_time, direction)`); OOS pools the disjoint test folds directly, and
  `_attach_overfit_stats(pooled_oos_only=True)` builds the PBO matrix from the OOS pool only in CV
  mode, since test folds tile history once while train overlaps would double-count.
  `_sweep_grid_worker_cv` is the picklable CV worker. `cv=None` (`mode="contiguous"`,
  the default) reproduces the legacy split byte-identically. CLI:
  `wifey param-sweep --cv-mode purged [--cv-folds 5] [--cv-purge-bars 0] [--cv-embargo-bars N]`;
  `run_strategy_audit` and `tools/multi_symbol_wfo.py` stay contiguous-only.
- `run_strategy_audit(...)` → `list[AuditRow]` — same `atr_sl_multiplier`/`atr_sl_floor` kwargs as
  `run_param_sweep`, forwarded to each worker's `run_backtest()`.
- Applies `day_filter` before the IS/OOS split, so a sweep grades the same population the live
  daemon sees.
- `SweepRow` / `AuditRow` expose `long/short_oos_avg_r`, `long/short_oos_n` (Gate 3).
  `_directional_split_hint(row)` fires when `|↑OOS − ↓OOS| ≥ 0.1R` and n ≥ 3 on each side.
- Parallelized: phase 1 detects signals sequentially (needs a DB conn); phase 2 runs the grid via
  `ProcessPoolExecutor` using `_sweep_grid_worker` / `_audit_strategy_worker` (picklable, taking
  pre-computed DataFrames).
- Overfitting controls: after the grid completes, before the `top_n` truncation,
  `_attach_overfit_stats(rows)` computes per-config IS/OOS Sharpe, a Deflated Sharpe, and one
  sweep-level PBO over the full grid, attaching an `OverfitStats` to each `SweepRow.overfit_stats`.
  `format_sweep_results` prints `trials N=… PBO=…%` plus the recommended config's per-trade Sharpe
  and Deflated Sharpe (a probability, ≥95% reads as robust); the PBO matrix columns are sorted by
  params for run-to-run reproducibility. See `backtest/stats_overfit.py`.
  `format_sweep_results(..., *, gate=)` appends a `PASS` / `DO-NOT-COMMIT` / `INSUFFICIENT`
  footer via `_fmt_gate`, additive to the overfit-controls block and printed only when there is a
  recommended config; the three early-exit paths return `_empty_report(reason)` (INSUFFICIENT)
  instead of raising. Callers unpack `.rows`: `cli/param.py` (passing `gate=`), the in-file
  `main()`, `tools/multi_symbol_wfo.py`. DSR/PBO/gate fields do not surface in the regression-golden
  pipeline (`test_regression._extract_metrics` enumerates fields explicitly), so goldens stay
  byte-identical.

## stats_overfit.py — overfitting / multiple-testing controls

Lives in `analytics/backtest/`. Pure math — numpy + stdlib `statistics.NormalDist` (no scipy), no
DB, no engine import, so `engine → stats_overfit` is one-directional with no cycle.

- `sharpe_ratio(returns)` — per-trade R Sharpe `mean/std` (sample std, ddof=1, non-annualized — the
  engine's native unit); `0.0` on n<2 or zero-variance.
  `_moments(returns) → (mean, std, skew, kurt)` with non-excess kurtosis (normal == 3).
  `BacktestResult.sharpe` wraps this over closed-trade `pnl_r`.
- `probabilistic_sharpe_ratio(sr, n, skew, kurt, sr_star=0.0)` — PSR = Φ((SR−SR\*)·√(n−1) / √(1 −
  skew·SR + (kurt−1)/4·SR²)). `expected_max_sharpe(sr_variance, n_trials)` — SR\*₀ =
  √V·[(1−γ)·Z⁻¹(1−1/N) + γ·Z⁻¹(1−1/(N·e))] (γ = Euler–Mascheroni).
  `deflated_sharpe_ratio(observed_sr, trial_sharpes, n_returns, skew, kurt)` is PSR evaluated
  against SR\*₀ estimated from the cross-trial Sharpe variance (Bailey & López de Prado 2014); DSR
  is a probability in [0,1].
- `probability_of_backtest_overfitting(perf_matrix, n_splits=16) → PBOResult(pbo, n_combinations, logits)`
  implements CSCV (Bailey et al. 2017): partitions the T rows into S contiguous submatrices, and for
  every C(S, S/2) IS/OOS split ranks configs by IS mean R, takes the IS-best, and records its OOS
  rank as a logit; PBO = P(logit < 0). The performance metric is mean R per column (robust on sparse
  submatrices); `NaN` when fewer than 2 configs or T < S.
- `build_performance_matrix(trade_points, n_rows)` time-buckets each config's `(entry_time, pnl_r)`
  points into a T×N matrix over the shared time span — the common axis CSCV needs, since per-trade R
  is not row-alignable across configs with different `tp_r`.
- `OverfitStats(is_sharpe, oos_sharpe, deflated_sharpe, n_trials, pbo)` is the carrier attached to
  `SweepRow.overfit_stats`. `cv_splits.py` carries the purged-embargoed CV split; `_split_ohlcv`
  is untouched and CV stays default-off.

## research_guards/ + sweep_guard.py + audit_guard.py — commit-gate subsystem

Three related pieces: `research_guards/` (pure math, ported near-verbatim from the parent),
`sweep_guard.py` (wraps it for the sweep commit gate), and `audit_guard.py` (wraps it for the
audit-cell gate hosted by `warning_audit.py`). `research_guards` is kept separate from
`backtest/stats_overfit.py` on purpose: `stats_overfit` owns the sweep-footer display figures
(mean-R PBO metric, `trial_sharpes`-list DSR), while `research_guards` owns the commit-gate math
at the parent's API, which keeps a `/sync-parent` port 1:1.

### research_guards/ package

Ported verbatim from the parent — numpy + stdlib `statistics.NormalDist`, no scipy, no DB/engine
import — except `correlation.py` (return shape diverges by decision) and `cluster.py`
(wifey-originated; the parent adopted it in #696, so a future sync hit on this path is wifey's own
work coming home, not an owed port). `dsr.py` and `psr.py` are byte-identical to the parent's;
`diff` them against `../buibui-moon-trader-bot` before assuming a numeric-core port still lines
up. Eager re-exports via `__init__.py`.

Modules: `psr.py` (`probabilistic_sharpe_ratio`, `sr_benchmark` arg, raises on degenerate input);
`dsr.py` (`deflated_sharpe_ratio` with either `trial_srs` or `n_trials`+`sr_variance`, plus
`expected_max_sharpe(n_trials, sr_variance)`); `pbo.py` (`cscv_pbo` →
`PBOResult{pbo, logits, degradation_slope, n_combinations}`, per-trial Sharpe metric, raises on
odd/`<4` `n_splits`); `mintrl.py` (`min_track_record_length`); `bootstrap.py`
(`block_bootstrap_ci` → `BootstrapCI`, stationary/circular); `cluster.py` (`cluster_stats` →
`ClusterStats` — one-way random-effects ICC, `DEFF = 1 + (m̄−1)·ICC`, `n_eff = n/DEFF` — and
`cluster_bootstrap_ci`, which resamples whole clusters; exists because a block bootstrap absorbs
only serial dependence, resampling runs adjacent in the array, and same-day cross-symbol trades
are scattered through it. The deflator can only shrink: `icc` clamps to `[0,1]` and `DEFF` to
`≥1`, so a negative sample ICC never invents more independence than there are trades; one cluster
gives `n_eff` 1, singleton clusters give `n_eff == n_obs`, and fewer than 2 clusters give an
infinite-width CI rather than a narrow one invented from a single day); `haircut.py`
(`haircut_sharpe` → `HaircutResult`; Bonferroni/Holm/BHY); `power.py` (`required_sharpe`, the
effect-size bar the gate demands, inverting DSR/PSR by bisection); `correlation.py`
(`effective_independent_series` → `SeriesDeflator`, the equicorrelation deflator
`n_eff = k / (1 + (k-1)·rho)` and its `sqrt(k/n_eff)` t-stat inflation, ported from the parent's
`analytics/forecast/attribution.py` — the one non-verbatim member: a dataclass return and a
`measured` flag replace the bare tuple, numeric path unchanged. Hosted by `tools/n_eff.py` /
`make wifey-n-eff`; measured 505-universe `n_eff` 2.96 at `1d`, inflation 13.05×); `sharpe.py`
(`per_period_sharpe` / `ann_sharpe`, the one mean/sd Sharpe both sleeve reports feed into DSR and
the `boot_lo` bootstrap statistic. Five sibling Sharpes are deliberately not folded into this one —
`audit_guard._slice_sharpe` (±inf on purpose), `sweep_guard._trial_sharpe`, `pbo._sharpe`,
`stats_overfit.sharpe_ratio`, `forecast.metrics.sharpe` — because they disagree on the
degenerate-input contract across three thresholds (exact `0.0`, `1e-12`, `1e-10`), so routing any
of them through here would be a behaviour change needing its own evidence. `ann_sharpe`'s factor is
already `sqrt(periods)`, where `xsmom.diagnostics._ann_sharpe` takes a raw `ann_days` — same name,
same shape, differing by ~15.9× at 252. Pinned by constructed inputs in
`tests/test_research_guards_sharpe.py`, each with a positive control); `survival.py`
(wifey-originated, OV-1 #418: `drawdown_series`, `ulcer_index` in percent per Martin & McCann,
`max_drawdown`, `time_under_water`, and `drawdown_breach_curve`, the P(drawdown ≥ D within a
horizon) curve from vectorised stationary-bootstrap paths. Wealth starts at 1.0 and that start
counts as a peak. The path generator does not reproduce `bootstrap._stationary_indices`' random
stream, so the two are not interchangeable under a pinned seed); `gate.py` (below).

Every function in this package treats `sr` and `sr_variance` as per-observation, never annualized:
`psr` forms `z = (sr − sr_benchmark)·√(n_obs−1)`, so a daily book is tested against a daily
`n_obs`. Passing an annualized `sr_variance = 0.25` (sd 0.5) asserts sd 0.5 daily, ≈7.9
annualized, and `required_sharpe` duly returns a 6.94 annualized bar — a units error rather than
a crash, and the only reason this case was caught is that the resulting bar was absurd (measured,
`docs/audits/2026-08-20-h1-ma-regime-filter.md`). The one units contract the docstrings do name is
`kurtosis` (non-excess, normal = 3.0).

Other package-level members raise rather than returning a degenerate value, unlike their
`stats_overfit` counterparts:

- `probabilistic_sharpe_ratio(sr, n_obs, skew=0, kurtosis=3, sr_benchmark=0)` → P(true SR >
  `sr_benchmark`); raises `ValueError` on `n_obs < 2` or a non-positive variance term.
- `expected_max_sharpe(n_trials, sr_variance)` (`n_trials` first, opposite of `stats_overfit`'s
  order) and
  `deflated_sharpe_ratio(sr, n_obs, *, trial_srs | (n_trials + sr_variance), skew=0, kurtosis=3)` —
  exactly one multiplicity source or `ValueError`; returns a probability.
- `cscv_pbo(perf_matrix, n_splits=14, metric=None)` →
  `PBOResult(pbo, logits, degradation_slope, n_combinations)` — default metric is per-trial Sharpe;
  raises on odd or `<4` `n_splits`, `<2` trials, or `<2` rows/block.
- `min_track_record_length(sr, skew=0, kurtosis=3, target_sr=0, confidence=0.95)` → fractional
  observations needed for PSR to reach `confidence`; `inf` when `sr <= target_sr`.
- `block_bootstrap_ci(returns, stat_fn, n_boot=10000, block=None, alpha=0.05, method="stationary"|"circular", seed=None)`
  → `BootstrapCI(point, lo, hi, alpha, n_valid)`, a serial-correlation-aware percentile CI (block
  default `round(n**(1/3))`).
- `haircut_sharpe(sr, n_obs, n_tests, method="bonferroni"|"holm"|"bhy", pvalues_all=None)` →
  `HaircutResult(adjusted_pvalue, haircut_sharpe, haircut_pct, method, fell_back)`; Holm/BHY need
  `pvalues_all` or fall back to Bonferroni (`fell_back=True`).
- `required_sharpe(n_obs, *, n_trials=None, sr_variance=None, benchmark_sr=None, skew=0.0, kurtosis=3.0, target=GATE_DSR)`
  — the smallest Sharpe whose deflated/probabilistic SR reaches `target`, found by bisection on the
  production `deflated_sharpe_ratio` / `probabilistic_sharpe_ratio` rather than a hand-derived
  closed form, which keeps the bar and the gate in agreement by construction. Supply exactly one
  benchmark source (`n_trials`+`sr_variance`, or `benchmark_sr`), mirroring
  `deflated_sharpe_ratio`'s contract. Returns `math.inf` when `target` is unreachable at any finite
  Sharpe — the PSR z-statistic is bounded above by roughly `sqrt(2*(n_obs-1))`, so a small sample
  against a wide trial family cannot clear the gate at any effect size; this is a finding, not an
  error. Consumed by `tools/distil_power.py`, the G3 gate of `/research-distil`.

### gate.py — the sleeve acceptance gate

One definition, two layers. `passes_gate(dsr, pbo, boot_lo)` is the parent's published three-leg
gate (`GATE_DSR = 0.95` ∧ `GATE_PBO = 0.5` ∧ `boot_lo > 0`), with an explicit `math.isnan(pbo)`
guard so a passing verdict never rests on IEEE comparison semantics.
`passes_sleeve_gate(*, dsr, pbo, boot_lo, sharpe_annual, gate_sharpe=GATE_SHARPE)` is wifey's
four-leg composition, adding `sharpe_annual >= GATE_SHARPE` (0.7); it also owns
`DEPLOY_SHARPE = 1.0`, a tier annotation rather than a pass line. Consumed by all four sleeve
reports (`xsmom`, `lowvol`, `xasset`, `pead`) and `tools/forecast_audit.py`, replacing four inline
restatements (plus a fifth inside a test that had re-derived the same expression to build its own
expectation).

`min_trl`/`n_obs` are deliberately not gate parameters, so re-adding a MinTRL leg would need to
touch every call site. At `target_sr` = Sharpe 1.0 a MinTRL leg returned `inf` for any sample at or
below the target, imposing an undeclared effective bar of ~1.585 at n=2000 against a declared 0.7
and making `DEPLOY_SHARPE` inert; at `target_sr` = 0 it is strictly implied by the DSR leg (MinTRL
round-trips with PSR, and DSR is PSR at the expected-max benchmark, never negative), so it can never
fire — measured, 0 of 124,882 DSR-passing draws blocked. Dropping the conjunct is verdict-neutral
by monotonicity (removing a conjunct only turns False into True), pinned by
`TestRecordedVerdictsAreUnchanged` and proven neutral over 2,985,984 exhaustive combinations plus
200k draws with zero mismatches.

### sweep_guard.py

A different gate from `research_guards/gate.py`, deliberately not merged with it: its thresholds are
caller-overridable parameters and its composition is DSR ∧ PBO ∧ MinTRL with no `boot_lo` leg.
`evaluate_commit_gate(chosen: TrialPerf, all_trials, *, n_grid, dsr_threshold=0.95, pbo_threshold=0.5, mintrl_confidence=0.95, n_splits=14)`
→ `CommitGateVerdict(decision, dsr, pbo, min_trl, n_obs, n_trials, reasons)` with `.committable`.
Decision is `COMMIT` only when DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ n_obs ≥ MinTRL, else `DO_NOT_COMMIT`;
`INSUFFICIENT` when fewer than 2 trials or `n_obs < 2·n_splits`. `TrialPerf(label, returns, times)`
is the per-trial carrier; `_build_perf_matrix` calendar-bins it for CSCV; `n_grid` (the true
pre-truncation trial count) floors the deflation N. Pure; consumes `research_guards`. Consumed by
`param_sweep.run_param_sweep` via
`_compute_sweep_gate(trial_rows, _recommended_row(top_rows), n_grid)` and the `_row_to_trialperf`
adapter (pools each row's IS+OOS closed trades into a chronological `(entry_time, R)` series); the
verdict rides in `ParamSweepReport.gate` and renders via `param_sweep._fmt_gate`. Wired into the
CLI sweep footer and the `/param-sweep-apply` and `/wfo-sweep` skills.

`DSR_THRESHOLD` is derived from `research_guards.gate.GATE_DSR` rather than restated (both are
0.95, so no verdict moves), pinned by `TestDsrThresholdIsDerivedNotRestated`, which asserts on the
source rather than on value equality — a value-equality test is vacuous while both constants agree.
`PBO_THRESHOLD` stays a local literal on purpose: `GATE_PBO` is the sleeve gate's bar, and this
gate's composition is deliberately different (no `boot_lo` leg), so sharing the constant would
assert an equivalence that does not hold.

### audit_guard.py

`evaluate_audit_cells(cells: list[AuditCell], *, bar=0.05, alpha=0.05, min_n=30, haircut_method="holm", n_boot=2000, boot_method="circular", seed=12345, enable_concentrate=True)`
→ `list[CellVerdict]` aligned 1:1. A cell earns `ENABLE` (CI.hi ≤ −bar) or `DISABLE` (CI.lo ≥ +bar)
only when the bootstrap CI clears ±`bar` and its Holm-adjusted p (shared family across tested cells)
is below `alpha`; `CONCENTRATE` refines `DISABLE` when the kept slice beats the positive suppressed
slice by ≥ `bar`; `n_supp < min_n` gives `INSUFFICIENT` and excludes the cell from the haircut
family.

`AuditCell(label, supp_r, cluster_key, kept_r=[])` — `cluster_key` is required and sits before the
defaulted `kept_r` so mypy refuses a call site that omits it. It takes one entry per `supp_r` row:
the session day for this repo's panels. Both legs consume it — the CI is a `cluster_bootstrap_ci`
over whole days, and the Holm leg forms `t = sr·√n_eff` with `n_eff = n / DEFF`. A length mismatch
fails closed to `INSUFFICIENT` rather than silently reverting to per-trade resampling, because an
unmeasurable panel and an uncorrelated one must not both read as a deflator of 1.0. `CellVerdict`
reports `n_clusters` and `design_effect` so a silently-applied deflator is distinguishable from a
forgotten one; `tools/regime_gate_replay.py` prints both beside `n`.

Pricing the CI per session day rather than per trade matters because a block bootstrap absorbs only
serial dependence — dependence between observations adjacent in the array it is handed — and
neither consumer hands it a meaningful adjacency on its own: `regime_gate_replay._load_trades` has
no `ORDER BY`, and `warning_audit.tag_trades` builds `keep_idx` symbol-by-symbol, so same-day
cross-symbol trades sit scattered through the array. Measured over 64 cells / 27,026 trades: median
ICC by day 0.598, design effect 3.349, day-clustered CIs 1.92× wider; the undeflated Holm leg
(`_two_sided_p` building `t = sr·√n_TRADES`) read 30 of 64 cells significant where only 12 survive
`n/DEFF`. The error size is a property of the cell cut, not of the tool — pooling across strategies
barely moved the result (1.208×) while single-strategy cells moved 1.6–3.2×, so a re-cut per
strategy reopens the exposure.
`powered_null` held on 0 of 64 cells before the day-level fix, so no prior "ruled out" verdict was
over-licensed; the live effect was on `ENABLE`/`DISABLE` verdicts, and `[bias.regime]`'s
`EXCLUDED` moved to UNBLOCKED (mapping untested; `mode` ruled to STAY). Measurement:
`docs/audits/2026-08-20-audit-guard-cross-sectional-clustering.md`; fix:
`docs/audits/2026-08-20-audit-guard-cluster-key-fix.md`.

`session_day_keys(ts_ms)` is the one definition of that unit: it floors to the UTC day, which is
the session day for US RTH (13:30–20:00 UTC on EDT, 14:30–21:00 on EST, both inside one date). Do
not port it to a 24-hour tape.

`powered_null(ci_lo, ci_hi, *, bar)` is the one definition of a powered null — call it rather than
restating `ci_lo > -bar and ci_hi < bar`. It returns `False` for a missing or non-finite bound,
because the failure callers care about is a null claimed too easily: an untested cell must fall to
`INSUFFICIENT`, never read as `powered`. `evaluate_audit_cells` only ever hands it two finite
floats, so the guard clause is reachable only from `tools/distil_power.py`, and is unit-tested
directly in `tests/test_audit_guard.py`.

`evaluate_audit_cells` replaces a crude ±0.05R audit bar. Pure; consumes `research_guards`. Hosted
by `analytics/warning_audit.py` (below), its first consumer; `tools/regime_gate_replay.py` is a
second, where it replaced a pooled n-weighted banner that could contradict its own per-cell table
(`docs/audits/2026-08-19-regime-gate-pooled-banner.md`) and separately runs a second Holm family
over kept cells — the flip question tests only the slice the gate would drop, so a regime the
mapping admits could bleed without ever earning a verdict
(`docs/audits/2026-08-19-regime-kept-cell-audit.md`). The two families answer different questions
(one global switch vs. independently editable mapping entries) and must not be merged: merging
would enlarge the flip's Holm denominator and silently restate its shipped verdict.

The parent's `tools/gate_audit.py` / `adr_threshold_audit.py` remain unported and blocked: they need
`low_volume`/`volume_spike` columns on `backtest_trades`, which wifey never had.

### warning_audit.py

Warning-value lib, pure (no DB/IO); ported verbatim from the parent (#492) and the host that wires
`audit_guard`. `WARNING_KEYS` is the six candle warnings: `w1_marubozu`, `w2_equal_levels`,
`w5_wick_rejection`, `w6_consecutive`, `w7_doji`, `w8_inside_bar`.
`compute_warning_flags(window, direction, price=None)` re-derives the live alert warnings from
OHLCV by importing `signals/alert_formatter.py`'s private helpers rather than reimplementing them,
so the audited quantity is the one the operator sees (doji-over-marubozu precedence, W5 skipped on
doji, fewer than 2 rows → `None`; `price` defaults to the signal-candle close, matching
`SignalEvent.price`). `tag_trades(entries, ohlcv_by_key, *, window_bars=12)` exact-matches the
signal candle and returns `(tagged, n_dropped)`. `two_sample_lift_ci` is a seeded warned-minus-clean
stamp, report-only and never gate-deciding. `evaluate_warning_cells(tagged, …)` → 12
`WarningVerdict`s through one `audit_guard` Holm family (`ENABLE` → `SUPPRESS-CANDIDATE`,
`DISABLE` → `REVERSE`, `CONCENTRATE` or `powered_null` → `COSMETIC`, else `INSUFFICIENT`).

The `COSMETIC` trigger requires `powered_null` (a CI strictly inside ±bar), not merely `n >= min_n`:
a sample-size floor is not a power test, and the point-estimate version published 11 under-powered
cells as "this warning carries no information" while 0 of 12 cells actually pass the honest test.
Volume notes are out of scope here for a stronger reason than upstream's: `backtest_trades` has no
`low_volume`/`volume_spike` columns, so they are not re-derivable at all. Consumed by
`tools/warning_value_audit.py` (`make wifey-warning-value-audit`); verdict
`docs/audits/2026-08-13-warning-value-audit.md`.

## forecast/ — EWMAC trend sleeve (P2, equity port; PR #91)

Additive, read-only package: a continuous, multi-speed, vol-normalised EWMAC trend sleeve ported
from the parent's `analytics/forecast/`, adapted crypto→equity. No DB writes, no schema, no
detector/backtest change, so regression goldens stay byte-identical. Pipeline: forecast primitives
→ causal per-instrument book → portfolio vol governor → research-guard G2 verdict → read-only
DB/universe front door.

- `vol.py` — causal EW vol estimators (`ew_return_vol` / `price_vol`, both `.shift(1)`-baked so the
  position held on day `d` is sized from data through `d-1`). `annualize(daily, days=252.0)`
  defaults to NYSE sessions.
- `ewmac.py` — pure forecast math: `raw_ewmac` (fast EMA − slow EMA), `scaled_forecast`
  (vol-normalised, scalar-adjusted, capped ±20), `combine_forecasts` (weighted FDM mean, NaN-leg
  re-normalised; equal-weight reduces to `.mean(axis=1)` byte-identical).
- `config.py` — `ForecastConfig` frozen dataclass. Equity defaults: `annualization_days=252.0`
  (not the parent's 365), `fee_pct=0.0001` (1 bp), `slippage_pct=0.0002` (2 bp); 4 Carver speeds
  `(8,32)…(64,256)`; `min_history` = longest slow + vol_span = 288; `from_toml` reads
  `[backtest].fee_pct`/`slippage_bps`. The `xs_dollar_neutral: bool = False` field is read only by
  the `xsmom/` sleeve; the trend book ignores it.
- `metrics.py` — the curve-metrics slice of the parent's `portfolio/metrics.py`
  (`sharpe`/`sortino`/`max_drawdown`/`annual_return`/`annual_vol`/`calmar`), `_PPY=252.0`;
  degenerate (flat/single-point) curves return `0.0`, not NaN. The parent's book-dependent
  attribution funcs (`avg_exposure`/`risk_turnover`/`attribution`) are not ported, since this
  package has no `portfolio.book` dependency.
- `weights.py` — `candidate_schemes(cfg)` → 6 labelled `WeightScheme(weights, a_priori)`
  (equal/inverse_cost a-priori; fast-tilt/drop/fast-only data-snooped). Verbatim; dimensionless.
- `book.py` — `instrument_returns(close, funding_daily, cfg)` → causal subsystem returns
  (leverage/gross/turnover_cost/funding_cost/net); equities pass an all-zero `funding_daily`.
  `run_forecast_backtest(closes, fundings, cfg)` → `ForecastBookResult` (portfolio_return
  post-governor, pre_governor, governor clamped to `[g_min,g_max]`, active_count,
  per_instrument_net) — an equal-risk-weight mean across active names plus a causal trailing-vol
  governor. `equity_curve(result)` compounds to a curve for the metrics. Both `instrument_returns`
  and `run_forecast_backtest` carry a default-off keyword-only `long_only: bool = False` that clips
  the combined forecast at 0 before sizing (the `xasset/` long-flat form); the default path is
  byte-identical.
- `report.py` — `evaluate(result, cfg, trial_returns, pbo_returns=None)` → `G2Report` (annualised
  Sharpe/Sortino/max-DD/Calmar plus DSR / PBO / block-bootstrap-CI / MinTRL via
  `analytics.research_guards`). Annualization threads from `cfg.annualization_days` into every
  `metrics.*` call. PBO needs `T≥28` per trial; the guards return degenerate stamps when `sr_d==0`.
- `replay.py` — the only DB-touching module, read-only. `load_daily_inputs(conn, symbols)` →
  `(closes, fundings)` day-indexed Series via `analytics.store.market_data.get_ohlcv(…, "1d", …)`;
  `fundings` is always zero. `replay_universe` / `replay_trials` (per-speed + combined
  multiple-testing family) / `replay_weight_schemes`; symbols default to
  `load_research_universe(min_history_days=…).stocks()` (ETFs excluded). Never writes.

**Verdict = FAIL** on the breadth universe (101 stocks, 2124 sessions): portfolio Sharpe −0.05
@2 bps, negative even @0 bps — a signal failure, not a cost failure. Fast legs bleed (s8_32 −0.27),
slow legs are mildly positive (s64_256 +0.29). Distinct from the parent's crypto +0.36. Audit:
`docs/audits/2026-06-17-p2-forecast-trend-g2-equity.md`; run via `tools/forecast_audit.py`
(`make wifey-forecast-audit`).

## xsmom/ — cross-sectional momentum sleeve (P3, equity port; PR #92)

Additive, read-only package: a dollar-neutral long-short book over cross-sectionally demeaned
EWMAC forecasts (relative strength), built on the `forecast/` primitives. Ported from the parent's
`analytics/xsmom/` (#444 + #445), adapted crypto→equity. No DB writes / schema / detector change,
so goldens stay byte-identical.

- `book.py` — `xs_forecasts` (per-instrument `combine_forecasts` aligned to the union daily index)
  → `xs_demeaned_forecasts` (subtract the active-set row mean, skipna, so each active row sums to
  ~0) → `xs_leverage` (demean → `.shift(1)` causal → vol-parity per leg with the `/10`
  trend-comparable divisor; when `cfg.xs_dollar_neutral`, re-centers so each day's active leverage
  nets to zero). `run_xs_backtest(closes, fundings, cfg)` → `XSBookResult` (portfolio_return
  post-governor, pre_governor, governor, active_count, per_instrument_net); legs are summed
  (long-short P&L) under the causal 20%-vol governor; funding is fed zeros. `equity_curve(result)`
  compounds to a curve.
- `diagnostics.py` — pure (no DB/forecast import): `equal_weight_market_return` (active-set mean
  daily return, the "alt market"), `beta_attribution` → `BetaAttribution` (full-sample OLS
  `r = α + β·mkt`, reporting beta-hedged Sharpe, alpha t-stat and R², degenerate-safe),
  `subperiod_sharpe` → `PersistenceReport` (per-calendar-year + trailing 1y/2y Sharpe). `ann_days`
  defaults to 252; callers thread `cfg.annualization_days`.
- `replay.py` — the only DB-touching module, read-only. `replay_xs` / `replay_xs_trials`
  (per-speed + combined multiple-testing family) reuse `forecast.load_daily_inputs`; symbols
  default to `load_research_universe(min_history_days=…).stocks()` (ETFs excluded). Never writes.
- `report.py` — `evaluate_xs(result, cfg, trial_returns, trend_returns)` → `XSReport` (annualised
  Sharpe/Sortino/max-DD/Calmar plus DSR / PBO / boot-CI / MinTRL via `analytics.research_guards`,
  plus `corr_to_trend` / `trend_sharpe` for the diversification read).

**Verdict = FAIL** on the breadth universe (101 stocks, 2124 sessions): combined Sharpe −0.156
@2 bps, negative even @0 bps — a signal failure. `corr_to_trend` +0.62 means it is not uncorrelated
to the trend sleeve, so it is not a diversification win either; no significant alpha (hedged Sharpe
≈0), no stable persistence (trailing-2y −0.04). Distinct from the parent's crypto G3 CLEAR. Audit:
`docs/audits/2026-06-18-p3-xsmom-g3-equity.md`; run via `tools/xsmom_audit.py`
(`make wifey-xsmom-audit`).

`residual.py` is experiment #1 (PR #98), an additive default-off "XS-momentum done right": `rolling_beta`
(causal trailing 252-session OLS β) → `residual_returns` (`r_i − β·r_mkt`) →
`residual_close`/`residual_closes` (synthetic beta-stripped price for EWMAC) →
`sector_neutral_demean` (within-GICS) → `xs_residual_leverage` (residual signal, actual-vol sizing,
mirrors `xs_leverage`) plus `long_only_residual_leverage` (top-quantile long-only leg).
`_SLOW_SPEEDS_DEFAULT` is the skip-month analog (drops the fast `(8,32)` leg). `book.run_xs_backtest`
takes a keyword-only `leverage=None` injection (default byte-identical); `replay.replay_residual_grid`
runs the pre-registered `{mega,broad}×{raw,residual+skip}` 2×2 (mega arm =
`config/universe_sp100_snapshot.json`); `report.evaluate_residual_grid` → `ResidualGridReport`
scores the committed `broad_residual_skip` cell against a fixed gate (DSR≥0.95 ∧ PBO≤0.5 ∧
boot_lo>0 ∧ n≥MinTRL ∧ Sharpe≥0.7). The universe backing this experiment was expanded 105→508
names (approximately the current S&P 500) via `tools/expand_universe_sp500.py`.

**Experiment #1 verdict = FAIL** (committed L/S Sharpe +0.15 @2bps, DSR 0.44, boot_lo<0 at
0/2/8 bps): construction (residual+skip) was the dominant lever, flipping the raw-G3 sign, while
breadth was roughly neutral. The long-only leg's +0.88 Sharpe is survivorship/beta-confounded and
is not the gated cell. Audit: `docs/audits/2026-06-21-experiment-1-residual-xsmom.md`; run via
`tools/xsmom_residual_audit.py` (`make wifey-xsmom-residual-audit`).

## exits/ — exit MFE/MAE diagnostic + policy replay A/B (exit spec §2–§5; PRs #96, #194)

Additive, read-only package porting the parent's `analytics/exits/` §2 MFE/MAE excursion study
(parent #433) and its §3–§5 policy / replay / A/B layer (parent #437). No DB writes / schema /
detector change, so goldens stay byte-identical (`make test-regression` reproduces the fixtures
unmoved).

- `mfe_mae.py` — `compute_excursions(conn)` reads every resolved `signal_alert_outcomes` row
  (`win`/`loss`/`expired` with non-NULL `candle_ts_ms`/`entry_price`/`sl_price`/`rr_ratio`/
  `outcome_filled_at_ms`), batches by `(symbol, tf)` (one `get_ohlcv` per group), walks the bars
  strictly after the signal candle up to and including the exit bar, and records `mfe_r` / `mae_r`
  in units of risk `|entry − sl|` (both floored at 0) plus `bars_held`. Conservative anti-bias
  intrabar conventions: a loss excludes the exit bar's favorable extreme (`mfe = prior_fav`), a win
  clamps post-TP overshoot (`mfe = max(prior_fav, implied_tp_r)`, exit-bar adverse still counts), an
  expired trade counts every in-window extreme.
  `aggregate_cohorts(excursions, *, by=("strategy","tf","direction"), min_n=30)` groups by
  `(outcome, *by)` and emits `reach_05`/ `reach_10` (share whose MFE hit ≥0.5R/≥1.0R) plus
  `tp_r_p50`, feeding the spec's 4-pattern verdict grid; `by=()` gives the overall roll-up.
  `EXCURSION_COLUMNS` is the per-alert frame schema; its target column is `implied_tp_r`, re-derived
  per row from `entry_price`/`sl_price`/`tp_price` rather than read from the stored `rr_ratio` (the
  declared target, which would inflate both the win clamp and `tp_r_p50`). Two source adaptations
  from the parent, which still reads the stored column raw: the `get_ohlcv` import path, and the
  `implied_tp_r` routing.
- `policies.py` / `replay.py` — the §3–§4 pluggable exit engine, copied verbatim from parent
  commit `fbf6607` (they import nothing from the parent's `portfolio/`, which is why only the
  verdict layer needed work). `ExitPolicyConfig` is a frozen parameter bundle interpreted by one
  evaluator,
  `replay_exits(highs, lows, closes, *, direction, entry, sl_price, policy) -> ExitOutcome`; named
  policies are configs, not subclasses. Anti-bias conventions: adverse-first on same-bar ambiguity,
  breakeven arms effective the next bar, partials accumulate position-weighted R, SL = −1R and
  BE = 0R by construction.
- `audit.py` — the §5 verdict layer, and the only part that is not a copy. Upstream judged on
  portfolio Sharpe from its `PaperBook`; wifey has no `portfolio/`, so the headline is per-trade R
  Sharpe (`stats_overfit.sharpe_ratio`, not annualized) and the decisive leg is a paired
  `research_guards.block_bootstrap_ci` on `d_i = r_policy_i − r_fixed_i`. DSR is a stamp;
  `passes_sleeve_gate` is deliberately not called, because its `sharpe_annual` leg means an
  annualized sleeve Sharpe and its `pbo` leg needs a CSCV trial matrix that a handful of named arms
  cannot form (NaN, which turns `passes_gate` unpassable). Four arms — `fixed` (baseline) /
  `time_only` / `be_partial` / `composite` — are a lever decomposition, not a swept grid.
  `max_hold_bars` reuses `DEFAULT_MAX_HOLD_BARS`; `TIME_STOP_FLOOR_BY_TF` is re-derived for
  equities (`4h` 4, `1d` 3, the winner bars-to-1R p90, against the parent's crypto `4h` of 7), and
  an undeclared timeframe gets no early time-stop rather than a borrowed number.

**§2 verdict = EXIT-FIXABLE at the cohort level** (n=264, 264/264 scored), superseding the earlier
n=22 INCONCLUSIVE call: 43.9% of the losses that could show excursion reached ≥1R before stopping
(CI 36.4–51.8%), against the 13.3% behind the earlier "entry-broken" read, which was an artifact of
`mfe_r` coming from `fav[:-1]` — a loss resolved on its first held bar reads 0.0 by construction.
Still blocked per-edge: 0 of 30 loss cells reach n=30 (max 29). Audit:
`docs/audits/2026-08-12-exit-mfe-mae-diagnostic-rerun.md`; run via `tools/exit_audit.py`
(`make wifey-exit-audit`).

**§3–§5 verdict = BOUNDED**: every arm beats the baseline on a paired CI clear of zero, the whole
effect is the time lever, and the ceiling it buys is +0.368R of uplift against no measurably
profitable book. `time_only` +0.317R [+0.173, +0.468] exceeds the full `composite` +0.297R, so
adding breakeven+partial to a time-stop makes it worse. The paired leg survives both multiplicity
(best paired t +4.91 vs a 20-trial Bonferroni bar of 3.02) and day-clustering ([+0.156, +0.470]) —
but it certifies "A beats B", never "A makes money": no arm's own mean R clears zero once the 31 ET
session days are the unit (`time_only` +0.141, t +1.80, CI [−0.027, +0.318]), and the swept maximum
is arm-level t +2.52 against a 10-trial bar of 2.81. Baseline avg_r −0.176 is itself only t −1.84,
so neither side of "an improvement on a losing book" is measurable, only the difference is.
Parameters are in-sample; the 31 days sit inside one +4.0% SPY stretch on 13 correlated names.
Costs cannot flip it (1R median 313bps, so a 10bps round trip is 0.032R).

The "mean R of an open position peaks at bar 3 (+0.192) and decays" table is a mislabel: it
re-resolves the `time_only` arm at `time_stop=k`, so it is the sensitivity sweep shifted by the
baseline (`+0.192 = −0.176 + 0.368`), double-counting one measurement as two findings. Positions
genuinely still unresolved at bar k improve monotonically (+0.283 at k=1 → +0.951 at k=20, share
open 84% → 7%) because a stop removes losers first. The defensible mechanism is the marginal bar
over the whole book (67.8% of alerts have resolved by bar 8, enough at −1R that the median trade
under an 8-bar stop is a full stop-out), which makes the actionable lever the holding period — a
3-bar time-stop and a 3-bar `max_hold_bars` are the same trade with different paperwork. Audits:
`docs/audits/2026-08-14-mfe-timing.md` (step 1), `docs/audits/2026-08-14-exit-policy-ab-v1.md`
(verdict); significance/multiplicity/clustering/costs script:
`docs/plans/scripts/exit_ab_arm_significance.py` (calls the production functions and asserts its
own loop reproduces production `fixed` on all 267 rows first). Run via `make wifey-exit-replay`.

## lowvol/ — low-beta / BAB sleeve (edge-hunt #2; PR #100)

Additive, read-only package: a beta-neutral betting-against-beta long-short book (1d) over the
505-name S&P 500 universe, chosen because a market-neutral construction should escape the
survivorship + bull-market confound that faked experiment #1's long-only leg. No DB writes /
schema / detector change, so goldens stay byte-identical. Reuses `xsmom.residual.rolling_beta`,
`forecast.vol.ew_return_vol`, `forecast.replay.load_daily_inputs`,
`xsmom.diagnostics.equal_weight_market_return`, and the `xsmom.book.run_xs_backtest(leverage=…)`
cost-aware injection.

- `signals.py` (pure, causal) — `causal_betas` (trailing 252-session OLS β vs the EW market,
  `.shift(1)`-ed) / `realized_vols` (trailing 252-session rolling std, `.shift(1)`-ed) →
  `cross_sectional_score` (z-scored negative demean: low metric → long) →
  `beta_neutral_leverage` (vol-parity sized, then per-day beta-neutralized via `_beta_neutralize`
  scaling the short leg by `k=−β_long/β_short` so `Σwβ=0`; degenerate `k≤0`/NaN days are left
  untouched) plus `long_only_leverage` (deployable bottom-quintile, vol-targeted unit longs;
  mirrors `long_only_residual_leverage`).
- `replay.py` (only DB-touching, read-only) —
  `replay_bab_grid(conn, cfg, symbols, *, beta_window, vol_window)` runs the 2×2
  `{beta,vol}×{beta-neutral L/S, long-only}` (keys `beta_neutral_ls` (gated) / `beta_long_only` /
  `vol_neutral_ls` / `vol_long_only`); `bab_market_return` returns the EW market series for the
  realized-β diagnostic.
- `report.py` — `BabGridReport` + `evaluate_bab_grid`: reuses `evaluate_xs` (DSR/PBO/boot-CI/MinTRL
  over the 4-book family) plus `beta_attribution` (realized-portfolio-β guardrail per cell). The
  gate reads on the committed `beta_neutral_ls` cell only (`_GATE_SHARPE=0.7`); `_DEPLOY_SHARPE=1.0`
  is a deploy-grade tier annotation (Sharpe≥1.0 ∧ long-only leg≥0.7), not the pass/fail line.

**Verdict = FAIL** (504 stocks, 2127 sessions): committed cell Sharpe −0.069 @2bps (negative even
@0bps, −0.047), DSR ~0.03, boot_lo<0 at 0/2/8 bps. The realized-β guardrail fired (β +3.9, not ≈0):
the ex-ante trailing-β neutralization did not deliver realized market-neutrality on the
vol-parity, governor-saturated 500-name book, so this is a clean fail of the construction, not a
clean BAB-premium test. The only positive cells are the survivorship/β-confounded long-only books
(realized β +17.5/+21.9, below 0.7). Audit: `docs/audits/2026-06-22-edge-hunt-2-lowvol-bab.md`;
run via `tools/lowvol_audit.py` (`make wifey-lowvol-audit`).

## xasset/ — cross-asset TSMOM sleeve (edge-hunt #3; PR #102)

Additive, read-only package: runs the `forecast/` EWMAC engine over a frozen 13-ETF cross-asset
basket (equity-index SPY/QQQ/EFA/EEM, rates/credit TLT/IEF/LQD, metals GLD/SLV,
energy/commodity DBC/USO/DBA, FX UUP), 1d from 2007-03-01 — the first edge family that structurally
leaves US-equity beta, the confounder behind the prior four sleeve fails. No DB writes / schema /
detector change, so goldens stay byte-identical. Reuses `forecast.replay.load_daily_inputs`,
`forecast.book.run_forecast_backtest`, `forecast.report.evaluate`, and
`xsmom.diagnostics.beta_attribution`.

- `universe.py` (pure, no I/O) — the pre-registration artifact: frozen `BROAD_BASKET` /
  `COMMODITY_BASKET` (`AssetMember{symbol, asset_class}` tuples) plus `broad_symbols()` /
  `commodity_symbols()` / `MARKET_PROXY="SPY"`. Deliberately not in `config/universe.json`, so the
  equity breadth universe and all four equity-sleeve audits stay untouched.
- `replay.py` (only DB-touching, read-only) — `replay_xasset_grid(conn, cfg)` runs the 2×2
  `{broad,commodity}×{long-short,long-flat}` (keys `broad_ls` (gated) / `broad_long` /
  `commodity_ls` / `commodity_long`) via `run_forecast_backtest(long_only=…)`;
  `xasset_market_return(conn)` returns SPY's own daily return as the equity-β benchmark, loaded
  separately so it does not double-count.
- `report.py` — `XAssetGridReport` + `evaluate_xasset_grid`: reuses `forecast.report.evaluate`
  (DSR/PBO/boot-CI/MinTRL over the 4-book family) plus `beta_attribution` (realized β to SPY, the
  diversification-thesis guardrail). The gate reads on the committed `broad_ls` cell only
  (`_GATE_SHARPE=0.7`); `_DEPLOY_SHARPE=1.0` is the deploy-grade tier (Sharpe≥1.0 ∧ long-flat
  leg≥0.7), not the pass/fail line. The lone existing-code touch elsewhere is a default-off
  `long_only` clip on `forecast.book` (see that section).

**Verdict = FAIL (clean)** (13 ETFs, 4858 sessions): committed `broad_ls` Sharpe +0.41 cost-free /
+0.36 @2bps (never ≥0.7), PBO ~0.79, boot_lo<0 by 2bps, MinTRL=∞. Unlike lowvol the equity-β
guardrail held (realized β −0.083 ≈ 0) — a clean test: the construction diversified as designed,
and the cross-asset TSMOM premium is simply too weak net of cost in the free-ETF proxy set
(ETF-vs-futures roll drag, thin 13-name breadth, 2007–2025 trend drought). Audit:
`docs/audits/2026-06-23-edge-hunt-3-cross-asset-tsmom.md`; backfill `make wifey-xasset-backfill`;
run `tools/xasset_audit.py` (`make wifey-xasset-audit`).

## pead/ — post-earnings-announcement-drift sleeve (edge-hunt #4; PR #104)

### insider/ — H-024 routine-vs-opportunistic insider sleeve

The first non-price sleeve, so the TA freeze does not bind it. Design and frozen pre-registration:
`docs/superpowers/specs/2026-08-29-h024-insider-routine-opportunistic-design.md`. `form4.py` is
pure Form 4 parsing (`parse_form4`, `iter_form4_filings`, `raw_document_name`), writing
`insider_transactions` via `analytics/store/insider.py`; `classify.py` is the pure CMP labeller
(`classify_owner_year`, `classify_insiders`, `label_transactions`, `cohort_shape`). Phase 3 adds
`book.py` (calendar-time monthly formation, `TRIALS` and their routine-arm `PLACEBOS`), `replay.py`
(read-only; raises on an absent or disabled `[backtest.cost_model]` rather than booking gross and
labelling it net), and `report.py` (`evaluate_insider_trials`, `PairedDifference`), audited by
`tools/insider_audit.py` (`make wifey-insider-audit`).

`CLASSIFY_LOOKBACK_YEARS` and `ROUTINE_MIN_STREAK_YEARS` are the pre-registration, not tuning
knobs — they are parameters only so tests can drive the general rule. A trade carries its trade
year's label, never its filing year's: keying on the filing year is self-referential, since a
December trade filed in January is part of the history that classifies that January's year.
`--max-filings-per-symbol` thins every insider's calendar and is unsafe for phase 2 — the capped
draw classifies 3.5% of rows and 0% routine against the uncapped draw's 46.0%, a directional bias
because a thinned calendar cannot show a same-month streak, so the full backfill must run uncapped
(spec Amendment 3). `ParseOutcome` returns failures rather than dropping them, because the
parse-coverage observable is the phase gate (measured 95.2%, clearing the 80% floor) and a parser
that silently skips what it cannot read would report 100% by construction. The pre-registered study
population is transaction codes P/S, filtered in `get_insider_transactions`
(default `codes=("P","S")`) rather than at ingestion, so what the study excludes stays auditable
from the table. Backfill: `make wifey-insider-backfill` (needs `EDGAR_CONTACT_EMAIL`; see
`context/tools.md`). `insider_backfill_progress` is the run's resume ledger, one row per symbol
that completed with zero fetch errors, keyed `(symbol, since)` so a widened window re-runs rather
than reading an old completion as coverage; `mark_symbol_complete` / `completed_symbols` are its
only accessors.

**Verdict = EXCLUDED as a deployable sleeve, and the premise itself is not refuted** — the two must
not be collapsed. Primary cell T1 (opportunistic L/S, 1mo, ADV-weighted) is Sharpe −0.468 net and
−0.402 gross, DSR 0.001, boot_lo −1.054; β −0.167 is near-neutral, so unlike `velocity/` this cell
is evidence about the premise rather than about the construction. But
`PairedDifference.indistinguishable` is `measurable AND CI-contains-zero`, not an `audit_guard`
powered null: the paired CIs run roughly ±150 bps/mo against the paper's 82 bps/mo, so the panel
separates neither the arms nor the effect from zero. `measurable` is a third state on purpose — two
never-funded books would give an all-zero difference and a `[0,0]` CI that satisfies
"indistinguishable" while establishing nothing. Audit:
`docs/audits/2026-09-20-h024-insider-phase3.md`.

### pead/ — post-earnings-announcement-drift sleeve

Additive: the only writes are the `earnings_facts` table and `utils/edgar_client.py`; book / replay
/ report / audit are read-only, so goldens stay byte-identical. The first event-driven /
fundamentals family, orthogonal to the price-factor sleeves, trading a free seasonal-random-walk
SUE (Foster-Olsen-Shevlin / Bernard-Thomas — EPS minus the same fiscal quarter a year prior,
divided by trailing UE std; no paid estimates) sourced from EDGAR `companyfacts`, entered the next
NYSE session strictly after the 8-K item-2.02 announcement (10-Q `filed` fallback), held over a
pre-registered 60-session drift window.

- `signals.py` — pure causal `seasonal_sue` + `sue_leverage` (vol-parity SUE cohorts,
  dollar-neutral demean for L/S or clip≥0 for long-only), the no-lookahead heart of the sleeve.
- `replay.py` (only DB-touching, read-only) — `replay_pead_grid` runs the 2×2
  `{broad,mega}×{long-short,long-only}` via the `xsmom.book.run_xs_backtest(leverage=…)` cost-aware
  injection plus `pead_market_return` (SPY benchmark); the mega arm is
  `config/universe_sp100_snapshot.json` intersected with active symbols.
- `report.py` — `PeadGridReport` + `evaluate_pead_grid`: reuses `forecast.report.evaluate` for
  DSR/PBO/boot-CI/MinTRL over the 4-book family plus `xsmom.diagnostics.beta_attribution` for the
  equity-β-to-SPY guardrail; gate on the committed `broad_ls` cell (`_GATE_SHARPE=0.7`,
  `_DEPLOY_SHARPE=1.0`).

**Verdict = FAIL**: committed `broad_ls` Sharpe +0.10 @2bps, well below 0.7, DSR 0.20, boot_lo<0,
MinTRL=∞ at 0/2/8 bps, and the equity-β guardrail fired (β ≈ +113) — like the lowvol sleeve, a
governor-saturation pathology on sparse daily earnings cohorts, so a fail of this construction's
neutrality rather than a clean premium test. The controlled mega arm (β −0.40) showed negative
drift (−0.53): the honest read is no PEAD in liquid large-caps net of cost on free data. Audit:
`docs/audits/2026-06-23-edge-hunt-4-pead-lite.md`; run via `tools/pead_audit.py`
(`make wifey-pead-audit`); backfill via `make wifey-pead-backfill` (EDGAR, 480/504 names, 24,747
quarters).

## overlay/ — risk overlays on the market premium (OV-1 #418, VM #421)

Not a sleeve: an overlay is judged against buy-and-hold on the overlay yardstick in
`docs/north-star.md` § Two yardsticks (ulcer index with Sharpe non-inferiority), never on
`GATE_SHARPE`. The frozen pre-registrations are the edge-pillars spec § Phase 2 (OV-1) and
§ Amendment 1 (VM). `__init__.py` re-exports the public names below.

- `rules.py` — `ma_signal` (H1's `ma200d` unchanged; NaN through the warm-up so it never reads
  as flat) and `position_on`, the one place the signal calendar meets the return calendar. With
  `lag >= 1` a session's position is the signal at the `lag`-th latest signal date strictly
  before it; `lag=0` admits the same session's close and exists only as the causality test's
  positive control. VM's `vol_weight` is `min(1, target / σ̂_20d)` (`realized_vol`, ddof 1,
  through `t`), shifted onto the next session because it is read from the return calendar itself;
  `target` may be a series (the real-time expanding median). `lag=0` is again the control.
- `frame.py` (only DB-touching, read-only) — `load_gspc_close` and `build_frame`. The return
  calendar is the market series' own, so the French file's 1,039 in-panel Saturdays survive;
  an inner join with `^GSPC` would drop them. Refuses a market series running past the last close
  (a stale signal nobody downstream could see) and any panel session missing `mkt` or `rf`.
  `vol_target` is VM's in-sample `σ_target` (median `σ̂` over the panel); `with_vol_weight` adds
  `w` and `w_ov = pos × w`, computing `w` on the full market series so the panel keeps its warm-up.
- `replay.py` — `arm_returns`: `bh` earns `mkt`; each arm in `positions` (default
  `OV1_POSITIONS = {"ov": "pos"}`) earns `mkt` on its position, fractional or not, and `rf` on the
  rest, less `bps × |Δpos|`. No `Trade` objects, so `cost_model.py` does not apply.
- `live.py` — the core's daily state (#423; the core is OV-1 × VM per #429). `core_state` reads
  both legs through `ma_signal` and `rules.weight_from_sigma`, VM's one formula, which
  `vol_weight` also calls. `CORE_SYMBOL = "^GSPC"`. `VM_SIGMA_TARGET = 0.00709` is the audit's
  pinned in-sample median, since the French file is not read here, and `σ̂` comes from `^GSPC`
  price returns because the French file lags by weeks. `flip_level` is the mean of the last 199
  closes: the next close is above the 200-session SMA that includes it exactly when it exceeds
  that mean. `completed_closes` drops a bar dated today before 21:00 UTC. `missing_sessions` lists
  closed NYSE sessions after `as_of` with no bar, the staleness rule both the digest's
  `core_findings` and `GET /api/core-state` (the Stats page card, #432) call.
  `tests/test_overlay_live.py` pins the read-out against `ma_signal` and `vol_weight`.
- `report.py` — `overlay_verdict` (EXCLUDED is checked first; a leg-1 CI straddling zero returns
  `INSUFFICIENT`, named by Amendment 1), `bootstrap_legs` and `evaluate_overlay` (both take a
  `base` and an `arm`, so VM's increment test benchmarks against `ov`; both legs
  on identical resamples: equal seeds, and the statistics draw nothing from the generator),
  `summarize_arm` (pass `pos=` for an arm's position column; read `turnover_per_year` for a
  fractional weight, where nearly every session is a switch), `beta_attribution`, `calendar_years`. Sharpe is in excess of `rf`. Annual
  return and switches per year use calendar years, because the pre-1952 Saturdays make a session
  count overstate the span by ~4%.

Causality: `tests/test_overlay_rules.py::TestCausality`, six truncation cuts with two positive
controls on the same channel (a rule reading tomorrow's close, flagged 6 of 6; the `lag=0`
mapping, flagged 3 of 6). VM's is `tests/test_overlay_vm.py::TestCausality`: returns after each of
six cuts get +20% a session, large enough that the capped weight cannot absorb it, and the `lag=0`
weight is flagged 6 of 6. Audit tools: `tools/overlay_audit.py` (`make wifey-overlay-audit`) and
`tools/vm_overlay_audit.py` (`make wifey-vm-audit`).

## gapfill/ — gap-fill "magnet" sleeve (edge-hunt #5; PR #198)

The first structural / market-microstructure family (the six prior sleeves were price-factor or
event-driven). Read-only: no DB writes, no schema, no detector, no dispatch, so goldens stay
byte-identical. Outside the TA freeze, since it is a measurement that changes no dispatch. It ran
only because the user explicitly reopened the concluded free-data arc for this candidate;
reopening is per-candidate.

- `signals.py` (pure, causal) — `nearest_unfilled_level` walks each symbol once, creating a gap
  when `|open/prev_close − 1| ≥ MIN_GAP_SIGMA × trailing vol`, expiring at `GAP_MAX_AGE`,
  fill-checking against the same session's high/low, then snapshotting the surviving edge nearest
  the close. `_raw_magnet` turns that into signed proximity decaying to 0 at `MAX_DIST_SIGMA`;
  `.shift(1)` is the causality guard. `reversal_score` is the confound control;
  `range_regime_mask` is a market-level causal sideways test; `cross_sectional_long_score` is a
  positive z, deliberately the opposite orientation to `lowvol.signals.cross_sectional_score`,
  whose inputs rank low-is-good.
- `replay.py` (only DB-touching, read-only) — `load_daily_ohlc` is a second daily loader, because
  gaps need OHLC while `load_daily_inputs` returns closes only; `TestLoaderMatchesForecastLoader`
  asserts its closes are byte-identical to that loader's so the two cannot describe different
  populations.
- `report.py` — `evaluate_gapfill_grid` reuses `evaluate_xs` + `beta_attribution` and adds
  `corr_to_reversal`. Sizing and booking are reused wholesale: `lowvol.signals.beta_neutral_leverage`
  / `causal_betas` / `long_only_leverage` and `xsmom.book.run_xs_backtest(leverage=…)`.

Pre-registered in `docs/superpowers/specs/2026-08-14-edge-hunt-5-gapfill-magnet-design.md`, written
before results: `MIN_GAP_SIGMA` 0.5, `MAX_DIST_SIGMA` 2.0, `GAP_MAX_AGE` 60, `RANGE_WINDOW` 20,
`RANGE_SIGMA` 1.0; four arms `broad_ls` (gated) / `broad_ls_range` / `reversal_control` /
`long_only`; gate `passes_sleeve_gate` on `broad_ls`.

**Verdict = EXCLUDED, and the direction is refuted rather than merely unsupported.** Cost-free the
magnet returns −0.460 — gaps continue, they do not revert — while the post-hoc inverse is +0.392,
below the 0.7 bar before a single bp. Gated `broad_ls` fails all four legs (Sharpe −1.333 @0bps /
−3.086 @2bps, DSR 0.000, boot_lo −1.984). Median daily gross turnover of ~211× means the 1bp
`fee_pct` alone costs ~0.9 Sharpe, so neither direction is tradeable; range conditioning changes
nothing (−1.317). 90.3% of 234,427 material gaps fill within 60 sessions, against an 88.9%
matched-placebo level — a +1.5pp gap-specific lift (+4.4pp at 1 session, +5.7pp at 5, +2.7pp at 20,
peaking short and decaying to nothing by 60). A fill rate needs a null: quoting the 90.3% alone is
misleading, since 98.3% of it is reproduced by an arbitrary level the same distance away
(`docs/plans/scripts/gapfill_fill_rate_null.py`, n=234,427 per arm). The descriptive claim is true,
almost entirely diffusion, and inert. Audit: `docs/audits/2026-08-14-edge-hunt-5-gapfill-magnet.md`;
run via `make wifey-gapfill-audit`; post-hoc script `docs/plans/scripts/gapfill_posthoc_diagnostics.py`.

Three findings here generalise beyond this sleeve. The runner's "0 bps" column is not cost-free:
`run_xs_backtest` charges `fee_pct + slippage_pct` and `fee_pct` defaults to 1bp, so on a
high-turnover book the zero-slippage column still carries ~2%/day and cannot answer "signal or
cost" — only a `fee_pct=0` row can, and every sibling sleeve's audit shares the same column. The
`long_only` arm is a construction artifact in every sleeve: `run_xs_backtest` sums legs, which
offsets in an L/S book and does not in a long-only one, so holding a median 181 names at 103×
gross produces β +44.6; it has never bound because no sleeve passed the main gate first, but it is
not a deployable read. `_beta_neutralize` leaves a day untouched when the short leg is degenerate —
measured 10.2% of active days here, the mechanism behind a fired β guardrail (−1.564) on a book
that is otherwise neutralized. That last mechanism does not generalise, though: `velocity/` fires
the same guardrail (−1.646) with the hatch accounting for only 9.2% of days, all of them
causal-beta warm-up, and an ex-ante post-neutralization net beta of exactly +0.000 — there the
cause is an ex-ante/ex-post estimation gap, since the causal betas do not describe the realized
period. Measure which mechanism fired before quoting either.

The reversal control falsified the reasoning that motivated it, which is why it stays: the spec
argued a priori that "trade toward the nearest unfilled edge" is mechanically a gap-fade and
predicted a high correlation to 1-session reversal. Measured −0.163 — the nearest unfilled edge is
often days old and unrelated to yesterday's return. Prediction wrong, control right.

## tom/ — turn-of-the-month exposure (#422)

An edge claim on the calendar, frozen in the edge-pillars spec § Amendment 2. Reads only the
French daily file; no `^GSPC`, no `analytics.db`. Verdict in CLAUDE.md's sleeve table.

- `rules.py` — `tom_position(dates)`: 1.0 on each month's last session and first three
  (`FIRST_SESSIONS`), from the calendar alone, so no return can leak in. The last date of any
  calendar reads as a month end because nothing after it is visible; `complete_months` cuts a
  trailing month the XNYS schedule says is unfinished. `tom_frame` labels the file's whole
  calendar before cutting the panel at `PRIMARY_START` (1988) or `SECONDARY_START` (2006), and
  `lag` shifts the position later.
- `report.py` — `hedged_returns` hedges with `overlay.report.beta_attribution`'s panel `β`;
  `evaluate_tom` gates the hedged series (annualized Sharpe ≥ `GATE_SHARPE`, DSR ≥ 0.95 at
  `N_TRIALS = 4` with `sr_variance = SR_VARIANCE_ANNUAL / 252` and the series' own moments, and a
  stationary-bootstrap lower bound > 0 on OV-1's block, resample count and seed). `tom_verdict`
  orders FOUND, EXCLUDED (CI upper bound below 0.7), BOUNDED, INSUFFICIENT; `premise_reading` is
  separate. `window_spread` bootstraps the inside-minus-outside excess return on paired sessions.
- Costs reuse `overlay.replay.arm_returns` (`positions={"tom": "pos"}`): 24 sides a year.

Causality: `tests/test_tom.py::TestCausality` cuts the calendar at six mid-month sessions; every
earlier label holds, and a positive control asserts the cut session itself is mislabelled.
Audit tool: `tools/tom_audit.py` (`make wifey-tom-audit`).

## velocity/ — velocity-alternation sleeve (edge-hunt #6; thesis H-007)

Tests whether the pace of a decline predicts the pace of the next move (slow grind → sharp rally).
Read-only: no DB writes, no schema, no detector, no dispatch. Outside the TA freeze on the same
precedent as `gapfill/`. It ran only because the user reopened the concluded arc per-candidate,
choosing the thesis-inbox candidates over paid data.

- `signals.py` (pure, causal) — `decline_metrics` computes `depth` (drawdown off the rolling
  `LOOKBACK` peak) and `duration` (sessions since that peak; ties resolve to the earliest bar, a
  convention rather than a derivation), then `velocity = depth/duration`. `_score_frame` owns the
  single `.shift(1)`; `velocity_score` is `−velocity`, and that sign is the entire hypothesis.
- `replay.py` (only DB-touching, read-only) — reuses `gapfill.replay.load_daily_ohlc` rather than
  adding a fifth loader, and returns the leverage matrices alongside the books, since turnover is
  not on `XSBookResult`.
- `report.py` — `evaluate_velocity_grid` adds `corr_to_controls` and `mean_gross_turnover`. Sizing
  and booking are reused wholesale from `lowvol.signals` and `xsmom.book`.

Pre-registered in `signals.py`'s docstring, fixed before the universe run: `LOOKBACK` 60,
`MIN_DEPTH` 0.10, `MIN_DURATION` 3; four arms `broad_ls` (gated) / `depth_control` /
`duration_control` / `long_only`. The two controls run on the ratio's own eligibility population,
enforced by `TestSharedPopulation`, because a control drawn from a wider population compares two
different experiments.

**Verdict = EXCLUDED, as a null rather than a refutation** (contrast `gapfill`, whose direction was
refuted). All four legs fail at every cost tier, but the β guardrail fired (−1.646), so the
informative column is the hedged one: beta-hedged −0.169, alpha t −0.49 — nothing in either
direction, and the post-hoc inverse is 4× below the 0.7 bar. The decomposition is the finding: corr
to `depth_control` is +0.499, to `duration_control` +0.546, with a Sharpe indistinguishable from
depth alone (−0.265 vs −0.279) — the ratio re-expresses a blend of two known effects. Cost is not
the constraint: 24.5× daily gross (8.6× lower than gapfill) and gross is already non-positive, so a
lower-turnover reformulation cannot rescue it. Audit:
`docs/audits/2026-08-14-edge-hunt-6-velocity-alternation.md`; run via `make wifey-velocity-audit`;
β diagnostic `docs/plans/scripts/velocity_beta_neutralize_failure.py`.

`long_only` is 100% market beta, which sharpens the construction-artifact point above: it prints
the best-looking cell in the table (+0.649 gross, DSR 0.713, boot_lo +0.046, the only arm to clear
a bootstrap lower bound) and hedges to +0.004 with alpha t +0.01 — not merely beta-contaminated,
beta and nothing else. `deploy_grade = passed ∧ Sharpe ≥ 1.0 ∧ long_only ≥ 0.7` is a gate shared by
five sleeve reports (`gapfill`, `lowvol`, `pead`, `xasset`, `velocity`; `xsmom` has no deploy tier),
so one leg of that deploy tier reads market exposure rather than edge. This is latent, since
nothing has cleared `passed`, and changing the gate is a user call.

The time-series form is untested and not queued: the pundit described one asset's own drawdown,
not a cross-section, and the cross-sectional null lowers the prior without closing it. Needs an
explicit user go.

## cost_model.py — realistic equity cost model

- Lives in `analytics/backtest/`. Pure math — `json` + `math` + numpy, no DB, no engine import
  (a `TradeLike` Protocol keeps it engine-free).
- `CostModel` (frozen dataclass): `adv_thresholds`/`half_spread_bps` (one spread per ADV bucket, 0
  is least liquid and widest), `impact_coef`, `notional_usd`, `borrow_rate_annual` (0.01 default
  constant), `commission_bps`, `adv_window_days`. `__post_init__` validates ascending thresholds,
  bucket count, and non-negativity.
- `cost_r(trade, ctx)` /
  `cost_breakdown(trade, ctx) -> CostBreakdown(spread_r, impact_r, borrow_r, commission_r)` — each
  component in R via `× entry/risk`. Spread is half-spread bps by `spread_bucket(adv)`; impact is
  `2 × impact_coef · σ · √(notional/ADV) × entry/risk` per leg (0 when ADV/σ is unknown); borrow
  applies to shorts only, `rate × holding_days/365`, charged once; commission is `2 × bps`. Zero
  when risk is zero.
- `build_cost_context(closes, volumes, idx, bars_per_day, window_bars) -> CostContext(adv_dollars, sigma_daily)`
  is causal: a trailing window ending at `idx`; ADV = mean(close×vol)×bars_per_day, σ = population
  stdev of per-bar returns × √bars_per_day; `None` when not computable (a conservative fallback).
  `bars_per_day_for_tf(tf)` reads `BARS_PER_DAY` (`1h:7, 4h:2, 1d:1, 1wk:0.2`; unknown → 1.0).
- `cost_model_from_toml(raw)` parses `[backtest.cost_model]`; absent or `enabled=false` gives
  `None` (the legacy flat-fee path). `to_json()` is canonical, stamping `backtest_runs.cost_model`
  and the run_id hash. The code path is default-off (`cost_model=None` is byte-identical); it is on
  for the live `signal_watch` configs, and that flip moved the regression goldens by design and is
  threaded into `test_regression.py`.

## digest_lib.py — aggregation over backtest_runs

- 12 pre-canned queries: `symbol`, `strategy`, `tf`, `combos`, `adr_ab`, `volume_ab`, `day_filter_ab`, `direction_bias`, `consistency`, `recovery_factor`, `co_firing`, `cross_tf_combos`
- `run_digest(conn, query, min_trades=None, top_n, scope)` — `min_trades=None` resolves via `_QUERY_MIN_TRADES` (default 5; co_firing/cross_tf_combos default to 3)
- `DigestScope(day_filter, fee_pct, symbols, min_trades, min_trades_per_tf)` — `_scope_clauses()` + `_min_trades_expr()` (per-TF CASE)
- `_pooled(avg_col, n_col="closed_trades")` → the SQL for a trade-weighted mean, `SUM(avg × n) / NULLIF(SUM(n) FILTER (WHERE avg IS NOT NULL), 0)`. **One definition, six call sites.** `query_strategy` / `query_tf` were always pooled; `query_symbol`, `query_direction_bias`, `query_consistency` and `query_recovery_factor` had drifted to `AVG(avg_r)` beside a `SUM(closed_trades)` — a mean and a count with different denominators. In `direction_bias` the unweighted value was also the `ORDER BY` key, so it ranked the report (3 pairs swap on real data). `AVG(recovery_factor)` / `AVG(max_drawdown_r)` stay unweighted on purpose — per-run extrema, not per-trade rates
- `query_co_firing` deduplicates via `QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol, timeframe, strategy_a, strategy_b, window_candles, day_filter ORDER BY run_at_ms DESC) = 1`
- `query_cross_tf_combos` deduplicates by `(symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf, window_hours, day_filter)`
- Powers `GET /api/backtest/analysis?use_config=true` and the `wifey digest` CLI

## overnight gap-fill warning — removed (#400)

`overnight_gap_lib.py` (the equity replacement for the parent's `cme_gap_lib`) fed one live alert
warning, "unfilled gap ⇒ magnet". It was audited and removed with its module: no warned cell earned a
keep, and warned shorts did better than clean ones. Audit: `docs/audits/2026-10-09-overnight-gap-fill-warning.md`.

Retired parent API this replaced (`cme_gap_lib.py`, kept here for historical reference only — no longer imported anywhere in this repo):

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
- `run_scan_cycle(..., catch_up=False)` — 3-phase: Phase 1 pre-fetches all DB data sequentially (`funding_map`/`ohlcv_map`), Phase 2 fans out via `ThreadPoolExecutor` (pure pandas, GIL released; workers = `min(cpu_count-1, n_pairs)`), Phase 3 fan-in: cooldown/backtest/upsert sequentially. With `catch_up=True`, each `(symbol, tf)` scan result is exploded into one per-candle group flagged `is_backfill` between Phase 2 and Phase 3 so conflict resolution + confluence stay per-candle correct; the newest closed candle is read from OHLCV rather than `max(event.open_time)`, so a signal-less newest bar can't promote an older candle to "live"; a cold-start guard (`CooldownStore.last_marked`) seeds only the latest candle for a fresh-watermark key. Default-off path byte-identical
- `ohlcv_cache: dict[(symbol, tf), DataFrame] | None` — daemon hot path skips DB reads in Phase 1
- `confidence_override` (combined) + `directional_confidence_override` ({strategy: {tf: {direction: stars}}}) — directional takes precedence
- `_compute_backtest()` respects `fee_pct`, `day_filter`, `min_sl_pct`, `atr_sl_multiplier`, `atr_sl_floor`, `since`; label shows `since YYYY-MM-DD` when set. `atr_sl_floor` flows through to `run_backtest()` so the alert's backtest gate evaluates trades with the same widened SLs the live path would apply
- `run_scan_cycle(atr_sl_multiplier=None, atr_sl_floor=False, ...)` — `atr_sl_floor` enables the F9 live widener; Phase 3 calls `analytics/signal/atr_floor.py::_apply_atr_floor` on returned events before conflict/dedup/bias/DB writes, so persisted `signals.sl_price` and Telegram alerts use the corrected SL/TP. Per-strategy / per-symbol+TF overrides via `strategy_params.atr_sl_floor` + `atr_sl_floor_per_tf`; resolver `_resolve_atr_sl_floor` mirrors the `atr_sl_multiplier` hierarchy. `_backtest_run_id` keyed on `atr_sl_floor` so cached/persisted runs don't bleed across on/off
- `_excluded_from_registry = {"seasonality", "funding_reversion"}` — skip silently (no warning).
- `_filter_signals_by_adr(ohlcv_df, signals_df, threshold, timeframe)` — directional: suppresses
  the chasing direction (LONGs when close > range midpoint, SHORTs when close < midpoint).
  `timeframe` is required, not defaulted, so mypy strict forces a new call site to declare it
  rather than silently re-acquiring the degenerate behaviour below. No-ops unless
  `adr_gate_applies(timeframe)`.
- `adr_gate_applies(timeframe)` — the gate needs more than one bar per calendar day for "cumulative
  intraday range up to this candle" to be a partial quantity. True for intraday TFs only
  (`1m`…`4h`); `1d`/`1wk`/`1mo` and any unknown TF fall closed. On a one-bar day the ratio silently
  becomes a high-range-day filter (median 0.925 on `1d` vs a 0.80 threshold, but dispersed p25
  0.71/p75 1.21, so it still reads as functional) and `move_up` collapses to "closed in the upper
  half of its own bar" — the same quantity `doji`/`ema`/`trend_day` derive direction from, making
  `chasing` true by construction and dropping 100% of their above-threshold signals (`bos` 48% /
  `eqh_eql` 76% are the controls). Measured cost was 28–82% of signals on every scanned TF for an
  effect surviving Benjamini–Hochberg in 1 of 17 cells. `4h` is unaffected (2 bars/day on RTH is a
  mis-scaled threshold, not an undefined one; re-picking it is frozen sweep work). Audit:
  `docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md`.
- Live and backtest ADR are not the same gate. Live dispatch reads `adr_consumed_pct` from
  `analytics/stats/adr.py`, which aggregates `1h` bars by UTC date (~7/day, genuinely
  non-degenerate) and is keyed per symbol, applied to every scanned TF. `_filter_signals_by_adr`
  uses the scan TF's own bars, per `(symbol, tf)`. Matching them is impossible at breadth: only 18
  of 522 symbols have `1h` data, so parity by shared function name is not parity.
- `_is_adr_exempt(strategy_params, strategy)` bypasses the live gate and the `_compute_backtest`
  filter. `backtest_runs.adr_suppress_threshold` records what the gate executed, via
  `effective_adr_threshold(declared, timeframe, adr_exempt=…)`, a required kwarg on
  `upsert_backtest_run` so mypy forces each of the four writers to state it — though mypy cannot
  see through a `**dict` splat, so twelve test call sites type-checked clean and failed only at
  runtime. Before this, writers stored the declared config value flat: 2,091 of 3,246 rows (64.4%)
  claimed a gate that never ran, 1,974 of them purely because the timeframe is not intraday and
  only 117 from `adr_exempt` (`bos`, `eqh_eql`) — the inverse of how the defect was first framed.
  `web` and `single_run` correctly wrote NULL, since neither wires `bias_cfg` into `run_backtest`.
  `migrations/002_adr_threshold_executed.py` rewrote 518 post-fix rows (column, run_id and
  `backtest_trades` cascade); earlier rows are untouched because the gate genuinely ran on
  `1d`/`1wk` then and the config state at write time is unrecorded. The migration is rating-neutral
  — `get_backtest_win_rates` already matched `= threshold OR IS NULL`, so all 6 rating surfaces are
  byte-identical across it.
- Volume gate: `_resolve_volume_suppress/spike_boost_long/short` — directional overrides → symmetric fallback; `SignalEvent.volume_spike` tagged
- `_compute_stats_context()` computes `StatsContext` once per cycle
- **Same-TF co-fire**: `combo_lookup`, `combo_window=5`, `combo_min_avg_r=1.0`; `_find_live_cofire` checks same-cycle pairs + cross-cycle DB signals; attaches `ConfluenceData`
- **Cross-TF co-fire**: `cross_tf_lookup`, `cross_tf_pairs`, `cross_tf_window_hours=4.0`, `cross_tf_min_avg_r=1.0`; `_find_cross_tf_cofire` queries DB signals history for HTF; same-TF and cross-TF both evaluated — higher avg_r wins
- `_parse_htf_ltf_pairs(list[str])` — parses `["1d:4h", ...]` TOML strings
- Backed by the `signal/` package (10 modules), with `signal_lib.py` as a 4-line re-export shim.
  `scan_symbol` and `run_scan_cycle` (`catch_up` mechanics above) live in `scanner.py`, which also
  holds `_resolve_outcome_sl_tp` (persists a non-NULL, scoreable SL/TP for every fired event —
  structural SL when valid, else the alert formatter's pct fallback; ported from the parent).
  `types.py` holds `SignalEvent`, `StatsContext`, `ConfluenceData`. `gates.py` holds
  `_filter_signals_by_adr`, `_is_adr_exempt` (accepts an optional `direction` kwarg for parity with
  the parent's per-direction API, though wifey resolves both directions to the strategy-wide flag),
  `_apply_direction_filter_gate`, `_apply_htf_ema_gate`, `_apply_regime_gate`,
  `_apply_conflict_resolver` — lifted out of `scanner.run_scan_cycle` so live and the backtest
  replay path share one implementation; the default `confidence_resolver` reads
  `event.confidence`. `resolvers.py` holds 10 `_resolve_*` helpers including
  `_resolve_atr_sl_floor`; `bt_cache.py` holds `_compute_backtest`/`_backtest_summary`;
  `atr_floor.py` holds `_apply_atr_floor` (the live SL widener, mirroring the backtest engine);
  `outcome_backfill.py` holds `backfill_outcomes` (see `signal_runner.py`); `stats_context.py`
  holds `_compute_stats_context`; `cofire.py` holds live + cross-TF co-fire detection; `_common.py`
  holds `_bt_mem_cache`, `_reset_bt_cache`, and timeframe parsing.

## stats_lib.py — pure stats lib

- `compute_p1p2_daily` → `P1P2Result` (incl. `p1_strong_pct`)
- `compute_hourly_extremes` (incl. `peak_high/low_hour_by_dow` per-DOW MODE)
- `compute_adr` → `ADRResult(adr_14, adr_30, today_range_pct, today_consumed_pct, today_move_up: bool | None)`
- `compute_dow_patterns` (incl. `avg_return_pct`, `median_range_pct`, `strong_high/low_pct`)
- `compute_session_breakdown`, `compute_weekly_p1p2`, `compute_weekly_p2_timing` → `WeeklyP2Timing`
- `compute_weekly_flip_risk_conditioned` → `WeeklyFlipRiskConditioned`; p1_direction="low"=bullish, "high"=bearish
- `compute_path_cone` → `PathConeBundle` (M5 daily cone: 18 direction × Mon–Fri combos over complete 7-bar RTH sessions, ADR14-normalized; all-history, ignores `days`)
- `compute_weekly_cone` → `WeeklyConeBundle` (M5 weekly cone: all/bull/bear over 35-bar Monday-anchored trading weeks, AWR14-normalized; `week_records` exposes the population)
- `compute_all` → `StatsBundle` (carries `path_cone` + `weekly_cone`)
- Live (never-cached) functions via `_inject_live_fields()`: `compute_weekly_current_state`, `compute_today_path`, `compute_current_week_path`, `compute_weekly_wick_percentile`
- All times MYT (UTC+8): `(epoch_ms + INTERVAL 8 HOUR)::TIMESTAMP`; raises `ValueError` on empty data (cone axes are ET display-side)
- Backed by the `stats/` package, split per dimension: `bundle.py` (the `compute_all`
  orchestrator), `p1p2.py`, `adr.py`, `dow.py`, `hourly.py`, `session.py`, `path_cone.py` (the
  daily cone above; superseded and replaced `daily_distance.py`), `weekly_cone.py` (the weekly cone
  above; holiday weeks drop via completeness), `weekly_state.py`, `weekly_p1p2.py`,
  `weekly_p2_timing.py`, `weekly_flip_risk.py`, `weekly_wick.py`, `live_outcomes.py` (cross-symbol
  live-alert outcome roll-up over `signal_alert_outcomes` — overall plus per-(strategy, tf,
  direction) plus per-strategy win-rate/avg-R/expired counts, optional `symbol=` scoping plus an
  always-global symbol chip list, and the `open_positions`/`mark_open_positions` open-panel layer;
  not part of the per-symbol `StatsBundle`/cache, served by its own router). `_common.py` holds
  shared helpers; live fields are injected by `bundle._inject_live_fields()`. `stats_lib.py` is a
  re-export shim.

## signal_runner.py — thin daemon wrapper

- Creates client, opens DB, syncs candles, polls `run_scan_cycle` in a loop
- All TOML params wired through: `sl_pct`, `cooldown_seconds`, `fee_pct`, `day_filter`, `bias_cfg`
- Loads `confidence_override` + `directional_confidence_override` from DB at startup
- Watched-series sync: `_sync_watched_series()` is extracted so the `ValueError` → first-backfill →
  cache-pop fallback is directly testable (`TestSyncWatchedSeriesDirect`); it swallows a
  `duckdb.IOException` only on `is_lock_conflict` — anything else kills the cycle on purpose (see
  `footguns.md`).
- OHLCV cache: `_update_ohlcv_cache()` re-fetches from `cached_max_ts` inclusive, replaces
  `cache[-1]`, and appends new rows; it invalidates when more than 2 rows arrive
  (`_CACHE_INVALIDATE_THRESHOLD = 2`).
- Live backtest window: one `bt_days = backtest_cfg.days` feeds both the cold cache read above and
  `run_scan_cycle(days=)`, and the two must move together — `run_scan_cycle` prefers a populated
  `ohlcv_cache` over its own `get_ohlcv(start_ms)`, so passing `days` alone widens the
  `_backtest_run_id` key and `backtest_runs.days` while `_compute_backtest` still sees the old
  window. Falls back to `_DEFAULT_BACKFILL_DAYS = 90` only when no `backtest_cfg` is supplied;
  `backtest_cfg.since`, when set, overrides the cache start.
- Combo refresh: `combo_lookup` + `cross_tf_lookup` reload every `_COMBO_REFRESH_CYCLES = 10`
  cycles.

**Outcome backfill.** After each `run_scan_cycle`, `signal_runner` calls
`analytics/signal/outcome_backfill.py::backfill_outcomes(conn, now_ms, cost_model=…, fee_pct=…)`
on the same write conn, resolving `signal_alert_outcomes` rows where `outcome IS NULL` by walking
OHLCV forward and mirroring the backtest engine's same-bar-tie-to-loss rule.

- The ledger is net of costs: `outcome_r = gross - cost_r`, with the drag recorded in
  `outcome_cost_r` so gross stays recoverable as the sum. The shared `live_cost_r` mirrors
  `engine.Trade.pnl_r`'s two branches exactly — a `CostModel` replaces `fee_pct` rather than adding
  to it — and the same function serves `migrations/004_live_ledger_net_of_cost.py`, so a migration
  cannot price rows on a different basis than the resolver. Do not port the parent's flat-fee
  `net_R` resolver: wifey's engine ignores `fee_pct` whenever a `CostModel` is set and the shared
  base sets one, so a verbatim port would price live on a basis the backtest does not use — a third
  basis does not fix a comparability gap, it adds one. `signal_runner` and
  `tools/backfill_null_tp_outcomes.py` are the two callers, and both must pass the cost args; a
  caller that omits them silently returns that ledger to gross.
  The OHLCV fetch widens backwards by the trailing ADV window when a cost model is set (ADV/sigma
  end at the signal bar), then slices back to the post-signal frame — without that slice a wider
  fetch would move rows from `no_ohlcv` into `open`, changing a count's meaning because an unrelated
  feature was switched on. `outcome_cost_r IS NULL` means unpriced, never "cost nothing"; open rows
  are charged when they resolve. Migration 004 restated all 292 pre-existing rows, moving pooled
  `avg_r` from −0.2050 to −0.2192. Costs are not the largest error in this ledger: a gap through the
  stop books a clean −1.0R (`engine.py:1116` does the same), and that absence is shared between live
  and backtest — worth roughly −0.10R/row against this charge's −0.014R. Audit:
  `docs/audits/2026-08-19-live-ledger-net-of-cost.md`.
- The OHLCV fetch is bounded at `now_ms - tf_secs*1000`, not `now_ms`, because `get_ohlcv` filters
  on `open_time` and an unbounded fetch would admit the still-forming candle, whose OHLC is
  provisional and which `upsert_ohlcv` replaces on the next sync — inflating the bar count by one
  and letting an `expired` mark-to-market read a `close` that was really "wherever price is right
  now". Because this module only revisits rows where `outcome IS NULL`, a label written off a
  provisional bar would be permanent. That guarantee depends on `upsert_signal_outcome`
  `COALESCE`-ing the three outcome columns (rather than a whole-row `INSERT OR REPLACE`), so a
  re-detection cannot reset a resolved row to NULL for this module to re-derive differently; joining
  `signals.fired_at` (`INSERT OR IGNORE`, so it records the first write) against
  `signal_alert_outcomes.fired_at` finds exactly one re-write in 295 events, 31 minutes after first
  detection and while still unresolved. `data_fetcher` does not independently prevent this: it drops
  forming bars only when yfinance returns them with NaN OHLCV, not a partial bar carrying real
  prices (the normal in-session shape, and every `_resample_to_4h` bucket). Measured on the wifey
  ledger: 0 of 264 resolved rows disagree with what the completed bars produce, so the bound is
  preventive rather than a repair — though 29 rows (11%) resolved on the last bar of their hold
  window, the shape it protects. What the bound buys is that the answer stops depending on when the
  resolver ran; `make go-live` is manual and documented for pre-market, before the US open (a
  same-evening-after-close run still sees the prior day's bar, because a `1d` bar stamped
  04:00/05:00 UTC does not close until the next day), and nothing enforces that scheduling.
- Past `max_hold_bars` without a TP/SL touch resolves as `expired` with a mark-to-market
  `outcome_r`. That window is counted in bars, never in calendar time —
  `if len(window) < max_hold_bars: return None`, so a row with too few bars available stays open no
  matter how old it is. US-equity RTH has 2 `4h` bars per day, so the `4h` cap of 30 bars is roughly
  15 trading days (~3 calendar weeks) and `1d`'s 14 bars is roughly 20 calendar days — a wall-clock
  reading of these caps produces a false positive ("open ledger rows past their hold window" when
  they are correctly open). The caps themselves are equity-measured and sit near p85–p90 of observed
  hold in `backtest_trades`. `max_hold_bars_by_tf` is a function parameter only — there is no
  `[outcome_backfill]` TOML block and `signal_runner` passes no override, so `DEFAULT_MAX_HOLD_BARS`
  is always effective in production.
- The caps are calibrated to coverage — the fraction of closed `backtest_trades` that resolve
  within them — not to a duration: `4h` 30 bars = 91.0% of 15,799, `1d` 14 bars = 85.5% of 7,168,
  `1wk` 7 bars = 91.1% of 729 (script: `docs/plans/scripts/max_hold_coverage.py`). Coverage must be
  computed with a one-bar offset, since a live cap of N admits `bars_held <= N-1` and the bar-0
  bucket differs sharply by TF (`4h` 20% / `1d` 28% / `1wk` 58%), so a naive `<= N` comparison is
  not comparable across timeframes. `1wk`'s cap is matched to `4h`'s coverage rather than `1d`'s
  because the error is asymmetric: too small a cap writes a wrong `expired`+MTM label permanently
  (only `outcome IS NULL` rows are revisited), while too large just leaves a row open another cycle.
  Reproduce the coverage figure when adding a timeframe. The live window is offset by one from the
  backtest's: `_scan_forward` counts bars strictly after the signal candle, while a backtest trade
  enters on the next bar's open and may exit on it (58% of `1wk` trades do), so live `max_hold` =
  backtest `bars_held` + 1.
- An unlisted timeframe is refused, not guessed: `_resolve_max_hold` returns `None`, and those rows
  stay NULL and land in `counts["no_hold_cap"]`, rather than falling back to `max(hold_map.values())`
  (the loosest entry, which handed `1wk` the `15m` value of 96 bars — 96 weeks).
  `tests/test_outcome_backfill.py::TestMaxHoldCalibrationCoverage` fails the build if any config
  scans a timeframe with no entry; failure logs but never blocks the cycle.
- The writer persists a non-NULL `sl_price`/`tp_price` for every fired event
  (`scanner._resolve_outcome_sl_tp`, ported from the parent — structural SL when valid, else the
  alert formatter's pct fallback), so all rows are scoreable. Rows written before this fix (NULL
  `tp_price`) are recovered by the one-shot `tools/backfill_null_tp_outcomes.py` retro migration.

## signal_test_runner.py

- `run_signal_test(symbol, timeframe, strategy, at_ms, lookback, ...)` — read-only, no DB writes, no cooldown
- `--at` pins to historical candle (Unix ms or ISO datetime); `--lookback` default 200

## signal_config.py — signal_watch TOML config loader

- `BacktestFilterConfig`: `fee_pct`, `min_sl_pct`, `min_avg_r`, `min_avg_r_long/short: float | None`,
  `min_avg_r_z: float = 1.64` (the significance requirement; `0.0` reproduces the legacy
  point-estimate rule), `since: str | None`, `cost_model: CostModel | None` (from
  `[backtest.cost_model]`; `None` is the flat-fee path). `BacktestSweepConfig` carries the same
  `cost_model` field.
- The hard-mode gate is `analytics/signal/gates.py::passes_ev_gate`, module-level rather than a
  closure inside `run_scan_cycle`, so tests can call it directly instead of re-implementing the
  comparison inline (a closure form let `TestEvGate` encode the defect below as its own
  expectation). It uses `min_avg_r_long/short` when set, falling back to `min_avg_r`. The
  `min_trades` guard counts the tested direction (`long_closed_trades` / `short_closed_trades`, the
  same population `long_avg_r` / `short_avg_r` average over) — counting both directions previously
  let a long verdict rest entirely on short trades (measured on 53 of 260 blocked `signal_watch`
  legs, 45 of 429 on weekdays; 19 and 68 resting on a single directional trade). It fails open in
  three places — no result, n below `min_trades`, `avg_r is None` — plus a fourth in the caller,
  which skips the gate entirely unless `mode == "hard"`.
- The significance step blocks a shortfall below `min_avg_r` only when
  `(threshold - avg_r) / (sd/sqrt(n_dir)) >= min_avg_r_z`; previously any negative `avg_r` blocked
  regardless of dispersion (measured 84 of 207 `signal_watch` blocks and 141 of 384 on weekdays at
  |t| < 1). It is deliberately not multiplicity-corrected: BH guards against selecting from many
  candidates (the sweep's job), and Bonferroni over ~300 cells gives z≈3.5, at which a fail-open
  gate blocks nothing. Zero-variance directional samples (every trade the same R) break the
  t-statistic, so they block only at `_ZERO_VARIANCE_MIN_TRADES = 5`+. Net impact when introduced:
  blocks 207→101 and 384→158, 0 newly blocked.
- The significance step needs `sd`, and the cached path is the normal one: `bt_results` holds
  `BacktestResult | BacktestSnapshot`, and since the snapshot stores aggregates only,
  `backtest_cache` carries nullable `r_long_sd` / `r_short_sd` (appended last in the DDL and
  migration, since `put_backtest_cache`'s INSERT is positional). `BacktestResult.long_pnl_sd` /
  `short_pnl_sd` and the snapshot's same-named properties give the gate one interface. A NULL sd
  (pre-migration row, or n_dir < 2) means abstain; such rows age out within a bar since the cache
  key includes `last_candle_ts`. Approximating sd from win rate was measured and rejected — median
  error 10.6%, p90 100%, 28.5% verdict flips (`docs/plans/scripts/sd_approx_check.py`).
- `SymbolOverride` — per-symbol tp_r/sl_pct/atr_sl overrides
- `StrategyOverride`: `tp_r_long/short` (per-strategy directional), `tp_r_long_per_tf` / `tp_r_short_per_tf` (per-TF directional, Task A 2026-05-18 — TOML keys `tp_r_long_4h`, `tp_r_short_1d`, …), `adr_exempt`, `volume_suppress/spike_boost` (symmetric + directional long/short)
- `SignalWatchConfig.effective_tp_r(strategy, symbol, tf, direction="")` — resolution (Task A): `symbol+TF+dir → symbol+TF → symbol → strategy+TF+dir → strategy+TF → strategy+dir → strategy → global`. The loader regex excludes `tp_r_long_*` / `tp_r_short_*` from `tp_r_per_tf` so the directional per-TF keys don't silently mis-parse.
- `effective_volume_suppress/spike_boost(strategy)` — per-strategy → global; directional variants return `bool | None`
- `BiasConfig` from `[bias]`: `adr_suppress_threshold`, `dow_soft_suppress`, `dow_suppress_min_abs_return`; F8 fields `htf_ema_enabled/mode/default_tf/default_period/default_slope_lookback/deadband_pct/per_strategy` (+ `htf_ema_anchor(strategy)` resolver); regime fields `regime_enabled/mode/htf_tf/enabled_regimes/per_strategy` (+ `regime_allowed(strategy, strategy_type, regime)` resolver — `unknown` regime + unmapped types fall open)
- `ComboConfig`: same-TF `window=5`, `min_avg_r=1.0`; cross-TF `cross_tf_pairs`, `cross_tf_window_hours=4.0`, `cross_tf_min_avg_r=1.0`
- `_deep_merge` + `_load_toml_with_extends` — config may declare `extends = "strategy_params.toml"`
- **Two load-time guards, both for the same defect class — a declared surface that silently produces
  nothing. Each raises `ValueError` in `load_signal_config`, before any consumer sees the config, and
  `load_backtest_config` inherits both because it calls `load_signal_config` internally.**
  - `dead_timeframes(day_filter, timeframes)` (#139) — a `day_filter` × fixed-open-weekday timeframe
    pairing is a blackout, not a filter. `_FIXED_OPEN_WEEKDAY = {"1wk": 0}`: weekly bars are stamped
    Monday, so `tue_thu` discarded 100% of them. Covers `strategy_timeframes` too, via `chain()`.
  - `voided_volume_gates(strategy_params, adr_suppress_threshold)` (2026-08-06) — `volume_suppress*`
    without `adr_exempt` while the ADR gate is live. The two gates select for **opposite** bars
    (range/volume correlate +0.61 on 1d, +0.67 on 4h): `P(pass both) = 0.0046` vs `0.036` under
    independence. Returns `[]` when the ADR gate is off (`adr_suppress_threshold is None`) or the
    strategy is `adr_exempt`. Only an explicit `True` counts — `None` means "inherit global".
    Falsified against the real pre-fix configs. See `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

## backtest_config.py — backtest sweep TOML loader

- `BacktestSweepConfig`: `min_sl_pct`, `liq_sweep_use_fib`, `volume_suppress/spike_boost`, `since: str | None`
- `is_adr_exempt(strategy)` — skips ADR filter in `backtest_runner.py`
- `effective_tp_r` / `effective_volume_suppress/spike_boost` / directional variants — mirror signal_config resolution
- Same `_deep_merge` + `_load_toml_with_extends` as signal_config.py

## recalibrate_lib.py / recalibrate_runner.py

- `get_backtest_win_rates(conn, day_filter=None, adr_suppress_threshold=None, declared=None)` →
  DataFrame with combined and directional columns. This is the single place the input population is
  filtered — the report, `compute_recalibrated_ratings` and `compute_directional_ratings` all go
  through it, so they cannot disagree about which cells are live. Four restrictions:
  - `closed_trades > 0`, and the latest run per `(strategy, timeframe, symbol)`.
  - `day_filter` / `adr_suppress_threshold`, keeping recalibration inside one execution context.
  - `declared` — only cells the config scans (`signal_config.declared_cells`, honouring
    `strategy_timeframes`). `backtest_runs` is permanent, so without this restriction a cell is
    re-rated forever after leaving the config.
  - `sweep_id IS NOT NULL` — sweep rows only. `signal.scanner`'s live EV gate also writes
    `backtest_runs` (one row per direction-leg: single strategy, no live-parity params, no
    conflict resolver, `sweep_id` NULL) on every scan cycle, and being almost always the newer row
    it would otherwise supersede the competed sweep row (measured 42 of 316 `signal_watch` inputs,
    15 of its 22 declared cells, median 4 closed trades vs the sweep's 8; `orb × 4h` read +0.0011
    against a sweep-only +0.2126, and five cells landed on the wrong side of zero).
    `signal_watch_weekdays` never runs live, so the two configs' stars were not comparable
    populations before this filter. The two writers produced the same `run_id`, so
    `INSERT OR REPLACE` let the live gate overwrite the sweep row outright, and this filter alone
    could not recover the competed measurement — it dropped the cell's symbols instead
    (`signal_watch` rated on 263 of 312 sweep rows, 17 of 24 cells short: `bos × 1d` on 6 of 13
    symbols, `trend_day × 4h` on 3). The `origin` discriminator on `upsert_backtest_run` (see
    `data_store.py`) fixes the root cause; after one `make db-update` the sweep is complete
    (312/468) and 11 star ratings moved, five direction-legs crossing from rated-positive to
    negative.
- Every average this returns is pooled over trades, not averaged over symbols: `avg_r` /
  `long_avg_r` / `short_avg_r` are `sum(avg_r_i × n_i) / sum(n_i)` via `_WEIGHTED_MEAN_COLS`,
  numerator and denominator masked together so a direction with no trades (NULL average) is
  excluded from both. Averaging trade counts while taking a plain `mean()` of `avg_r` let
  `min_trades` guard the pooled population while the statistic it guarded was a symbol-mean, which
  disagreed with `win_rate` in the same row (already pooled). Fixing this moved a star on 38 of 160
  rating rows, 21 crossing zero; coverage was unchanged.
- `compute_recalibrated_ratings(conn, min_trades, ..., declared=None)` → `dict[str, dict[str, int]]`
- `compute_directional_ratings(conn, min_trades=5, ..., declared=None)` → `{strategy: {tf: {"long": stars, "short": stars}}}`
- `write_confidence_to_db(conn, config_name, ratings, win_rates, day_filter=None, directional_ratings=None, declared=None)` → returns the pruned `(strategy, tf, direction)` triples; prunes first because the upsert cannot delete
- `write_confidence_to_source` — legacy: patches `analytics/strategies/_registry.py` directly
- Runner: `--config <toml>` derives `day_filter`, `config_name`, `adr_suppress_threshold`, `declared`; `--apply` writes to DB (with config) or source (without config). The prune runs **even when nothing is ratable** — an orphan is by definition a row no current run overwrites, so gating it on "something to write" would skip the worst case
