# Signals & Shared Utilities Reference

Detailed reference for `signals/` (the alerting + dedup daemon — detection itself lives in
`analytics/`) and for `utils/` (shared clients and config loaders). Load this when working on
alert formatting, cooldown, the signal registry, Telegram dispatch, or config/universe loading.

## registry.py

- `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` — **16** actionable strategies, verified against the
  code 2026-08-05: `bos`, `doji`, `ema`, `engulfing`, `eqh_eql`, `fvg`, `hammer_hanging_man`,
  `inside_bar`, `marubozu`, `morning_evening_star`, `orb`, `order_block`, `ote_entry`, `pin_bar`,
  `trend_day`, `wick_fill`.
- The three registry counts differ and are easy to conflate: `SIGNAL_REGISTRY` = 16 =
  `DETECTOR_REGISTRY`; `STRATEGY_REGISTRY` = 17 (the extra entry is `seasonality`, which is a stats
  helper, not a dispatchable alert); `analytics/strategies/` holds 18 non-private detector modules
  (the two unregistered ones include the legacy `fibonacci_retracement`).
- Excluded from dispatch: `seasonality` (inactive by design), `fibonacci_retracement` (legacy),
  `fib_golden_zone` (removed — no_edge across 3 sweeps).
- `confidence` field removed — resolved per-TF at dispatch via `STRATEGY_REGISTRY[name].get_confidence(tf)`

`DEFAULT_DB_PATH` lives in `analytics/store/_common.py` (re-exported via `analytics.store` and
`analytics.data_store`) — import from either re-export, never redefine it in a runner.

## cooldown_store.py

- Two-layer dedup:
  1. Candle watermark per `(symbol, tf, strategy)` — prevents re-firing same candle
  2. Cooldown timer per `(symbol, strategy, direction)` — time-based suppression
- JSON-persisted to `signal_state.json`
- `is_new_candle` / `mark_candle` take `channel: str = "primary"` (Task D, 2026-05-20). Primary preserves the legacy `{sym}:{tf}:{strategy}` key shape so existing state files load without migration; wife uses `{sym}:{tf}:{strategy}:wife`. Scanner marks **each** channel's watermark only on a successful live `dispatch_to_channel` — primary on a successful primary send, wife on a successful wife send — so a non-sending / dry run never "consumes" a candle (which would dedup the real alert away) and the two watermarks move independently (fix `fix/watermark-on-send`, 2026-06-06; primary previously marked unconditionally).
- `last_marked(symbol, tf, strategy, channel="primary") -> int | None` (catch-up, 2026-06-06) — returns the stored watermark or `None` when never marked. Distinct from `is_new_candle`'s `-1` sentinel: the `--catch-up` cold-start guard needs to tell "no prior watermark" (→ seed latest candle only, no burst) apart from "marked at candle 0".

## Missed-day catch-up (`--catch-up`)

- The scanner normally fires only on the single latest **closed** candle, so a skipped run-day permanently loses that day's signals (no backlog replay). `wifey signal watch --catch-up` (off by default) replays every un-alerted closed candle since the last run:
  - `scan_symbol(..., catch_up=True)` emits an event for **every** closed candle in the scan window (each carrying its own candle close as the entry price), not just the latest; the forming bar is still excluded (`open_time <= latest_open_time`).
  - `run_scan_cycle(..., catch_up=True)` splits each `(symbol, tf)` scan result into one pseudo-result **per candle `open_time`** so conflict resolution + confluence stacking stay per-candle correct; each group carries an `is_backfill` flag. The **newest closed candle is read from OHLCV, not from the events** (mirroring `scan_symbol`'s conditional forming-bar rule) — using `max(event.open_time)` would promote an older candle to "live" whenever the newest bar produced no signal. The existing candle watermark then drops the candles already alerted on a prior run. Default (`catch_up=False`) → single latest candle → one group → byte-identical to the pre-catch-up flow.
  - **Backfilled candles are recorded, never dispatched** (record-not-dispatch, back-ported from parent #504): only the newest closed candle may reach Telegram. Older candles still get DB signals + outcome-ledger rows and consume the primary watermark (the deliberate exception to the #68 mark-on-dispatch rule; the wife watermark stays untouched — nothing was dispatched on it and nothing reads it for dedup). A signal surfaced days late is untradeable noise in the chat but real ledger evidence — replay grows `signal_alert_outcomes` without flooding the channels with stale setups.
  - **Cold-start guard**: a key with no prior watermark would treat every window candle as "new" and burst the whole ledger on first contact. The guard (in the Phase 2b expansion, next to the backfill split) restricts such keys to the latest candle only (`open_time == latest_closed or last_marked(...) is not None`); later runs then catch up genuinely-missed candles. Recovery depth is bounded by the 200-candle `_SCAN_WINDOW` (4h ~33 days, 1d ~200 days, 1wk ~4 years).
  - **Fidelity caveat**: regime / HTF-EMA / ADR / DOW bias context is computed as-of-now and applied to historical candles too — a deliberate best-effort approximation for a few missed days, not a full as-of-candle replay (the backtest live-parity path does that). A deep backfill is look-ahead in the *gating* and should be read as backtest output, not clean out-of-sample evidence.

## alert_formatter.py

### Dataclasses

- `SignalEvent`: `tp_price: float` (structural TP from detector; `0.0` = use `tp_r` fallback), `volume_spike: bool` (> 3× rolling mean), `confluence_combo: ConfluenceData | None`
- `StatsContext`: `adr_move_up: bool | None`, `wk_low/high_still_ahead_conditioned_pct: float | None`, `wk_move_bucket: str | None`
- `ConfluenceData`: `co_strategy`, `candles_ago`, `avg_r`, `trades`, `win_rate`, `type_a`, `type_b`, `orderflow_signals: list[str]`, `htf_tf: str = ""`, `ltf_tf: str = ""`

### Alert layout (6 sections)

1. Header — strategy/stars/reason
2. Entry — price/time/session
3. Levels — SL/TP
4. Warnings — all notes consolidated (silent unless triggered)
5. Edge — backtest summary + confluence blockquote
6. Context — stats lines

### Warning helpers (`_build_candle_warnings`)

- W1 `_is_marubozu` — both wicks ≤ 10% of body
- W2 `_has_equal_levels` — equal lows below → LONG warn; equal highs above → SHORT warn (liquidity sweep likely)
- W5 `_wick_rejection_against` — wick > 40% of range against signal direction
- W6 `_has_consecutive_candles` — 3 candles same direction (overextension)
- W7 `_is_doji` — body < 10% of range (takes priority over W1)
- W8 `_is_inside_bar` — signal inside prior candle range
- Volume spike/low-volume moved from header into warnings block

### Other

- `format_signal_alert()` / `format_confluence_alert()` — both accept `ohlcv_df: pd.DataFrame | None` (signal candle = last row) + `gap_warning: str | None` (overnight equity gap notice; replaces parent's `cme_gap_warning`)
- `_adr_bar(consumed_pct)` — 10-char ASCII bar with `▓` overflow
- `_format_stats_line(ctx, direction)` — direction-aware; line 1: `📐` bull%/P1/ADR; line 2: `🎯` TP window/weekly timing
- Same-TF confluence renders `> ⚡⚡ CONFLUENCE`; cross-TF renders `> ⚡⚡ CONFLUENCE (1d → 4h)`
- `orderflow_signals` is a step-5 extension point for CoinGlass/NPOC lines

### Wife-channel formatter (Task D, 2026-05-20)

- `format_wife_alert()` / `format_wife_confluence_alert()` — minimal BUY/HOLD wife-channel variant. Strips strategy name, reason, stars, candle warnings, edge backtest summary, and stats line.
- LONG → header `BUY — $SYM TF` + entry price + time + SL/TP block (same widest-structural-SL / floor / structural-TP-or-tp_r logic as the primary formatter).
- SHORT → header `HOLD — $SYM TF` + price + time + `(regime caution — sit tight)`. No SL/TP — wife is not expected to action shorts; HOLD is regime context only.
- Dispatched via `utils.telegram_router.dispatch_to_channel(msg, "wife")`; `TELEGRAM_WIFE_DRY_RUN=1` logs the first line at INFO instead of sending.

## utils/ — shared utilities

### config_validation.py

Config schema validation and the two universe loaders.

- `validate_coins_config` (legacy) + `validate_stocks_config` (Phase A equities, since T6) +
  `load_stocks_config(path=Path("config/stocks.json"))` (T5; loads + validates, replaces the deleted
  `load_coins_config` from `utils/binance_client`).
- **Universe policy (Phase 0.1)** — the `UniversePolicy` frozen dataclass
  (`scope` / `as_of` / `survivorship_note`; `summary()` / `to_json()` / `describe()`),
  `validate_universe_policy`, `DEFAULT_UNIVERSE_POLICY`, and `load_universe_policy()`. Reads the
  reserved top-level `universe_policy` key in stocks.json (stripped by `load_stocks_config` so
  symbol-iterating call sites are untouched); missing file/block → default policy, invalid block →
  `ValueError`. Every saved backtest run is stamped with the policy JSON (nullable `universe_policy`
  column on `backtest_runs` — **migration-list-only, never in CREATE TABLE**, because
  `upsert_backtest_run`'s INSERT…SELECT is positional; deliberately excluded from the
  `_backtest_run_id` hash). The CLI runners print `describe()`, and the web UI surfaces it via
  `GET /api/universe-policy` + a caveat banner on the Backtest page.
- **Research breadth universe (N3)** — `UniverseMember`
  (`symbol` / `sector` / `kind` (`stock` | `etf`) / `delisted` / `listed`) + `ResearchUniverse`
  (`policy` / `membership_as_of` / `members`; helpers `symbols()` / `active_symbols()` /
  `stocks()` (active single-names only) / `n_active` / `describe()` / `with_min_history(days)`)
  frozen dataclasses, plus `validate_research_universe` and
  `load_research_universe(path=Path("config/universe.json"), *, min_history_days=None)`.
  **Distinct from the live-alert watchlist.** `delisted` is a lifecycle seam — all current members
  are survivors (PIT membership deliberately not scraped per gap-map decision #6).
- The optional `listed` per-member date (first available 1d bar; `None` ⇒ full-history survivor
  listed on/before the backfill start) is the **history seam**: `with_min_history(days)` — also
  surfaced as the `load_research_universe(min_history_days=…)` kwarg — drops members with fewer than
  `days` of history as of the fixed `membership_as_of` snapshot. Pure (no DB), reproducible,
  identity for `days ≤ 0`, untagged members always kept; default-off (`None`) keeps every member
  byte-identically. Forward-prep for the G1-gated XS-momentum sleeve's uniform lookback.
  **"Untagged members always kept" is why the seam fails open**: it was tagged on 3 of 505 by hand
  until 2026-08-13, so a 1-year floor dropped **0** members. Now **26**, stamped from the DB by
  `tools/stamp_universe_listed.py` (`make universe-stamp-listed`) — re-run it after any membership
  or backfill change, since a new constituent arrives untagged. → `context/tools.md`

### telegram.py / telegram_router.py

- `telegram.py` — low-level Telegram message sending (single channel, with retry); takes explicit
  `bot_token` / `chat_id` or falls back to `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` env.
  Two things it does that are easy to undo by accident (both ported from parent #579, and the
  first extends it):
  - **Every exception is logged through `_redact(exc, bot_token)`, at all THREE sites.**
    `requests` embeds the request URL in the exception string and that URL is
    `/bot<TOKEN>/sendMessage`, so logging a bare exception writes a live credential to the
    terminal or journal. Upstream redacts only the two `HTTPError` sites; the generic
    `except Exception` branch leaks identically (a `ConnectionError` renders the same URL) and
    is redacted here too. Never log a raw exception from this module.
  - **A 400 drops `parse_mode` and retries as plain text**, because a 400 is a *rejected
    payload* — resending the identical body can never succeed. The usual trigger is HTML: a
    traceback's `line 33, in <module>` reads as an unclosed tag, which made failure alerts fail
    on precisely the crashes they exist to report. Scoped to 400 deliberately — a 500 IS
    transient and its retry stays faithful to the original payload. The payload is rebuilt per
    attempt rather than mutated, so dropping it cannot retroactively rewrite what earlier
    attempts sent.
- `telegram_router.py` — dual-channel dispatcher (Task D, 2026-05-20).
  `Channel = Literal["primary","wife"]`; `dispatch_to_channel(text, channel)` resolves env creds
  (`TELEGRAM_BOT_TOKEN_2` / `TELEGRAM_CHAT_ID_2` for wife), honours `TELEGRAM_WIFE_DRY_RUN=1`
  (log-instead-of-send rollout safety), and isolates send failures so a wife outage can't block
  primary.

### Data clients

- `yfinance_client.py` — yfinance helper (`fetch_history`, `YF_INTERVALS`); no auth, no
  module-level side effects; normalises Yahoo's tz-aware America/New_York DataFrame to canonical
  lowercase OHLCV + UTC-naive DatetimeIndex (T2, since 2026-05-14). **4h is not native** — callers
  resample 1h→4h (T4).
- `edgar_client.py` — free EDGAR earnings client (edge-hunt #4, PR #104; stdlib `urllib`, no key,
  ≤10 req/s throttle). Network shims `fetch_company_tickers` / `fetch_company_facts` /
  `fetch_submissions` (integration-only) + pure fixture-tested parsers `ticker_to_cik`,
  `parse_eps_facts` (quarterly diluted EPS, originally-filed-only dedup, Q4 = FY−ΣQ1..Q3, YTD spans
  excluded), `parse_announce_dates` (8-K item-2.02 dates). No new poetry dep. Mirrors
  `yfinance_client.py` (no module-level side effects).

### Live display

- `live_store.py` — shared in-memory store for live WebSocket data.
- `live_loop.py` — shared Rich live display loop logic.
