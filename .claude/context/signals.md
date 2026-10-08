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
- **Those counts are pinned by a test, so re-verify by running it rather than by hand.**
  `tests/test_signal_registry.py::test_detector_registry_is_wired_to_the_other_two` set-diffs all
  three registries in both directions, checks `SIGNAL_REGISTRY` and `DETECTOR_REGISTRY` bind the
  *same* function object, and checks `backtest_runner._SWEEP_STRATEGIES` is dispatchable. The two
  dicts are hand-maintained duplicates in different files — `signals/registry.py` builds
  `_DETECTORS` from direct `detect_*` imports and never reads `DETECTOR_REGISTRY` — so nothing but
  that test can see them diverge. `_REGISTRY_EXCLUDED` in the test is the deliberate opt-out.
- `confidence` field removed — resolved per-TF at dispatch via `STRATEGY_REGISTRY[name].get_confidence(tf)`
- **Detectors always run at their module defaults — both dispatch sites pass no params**, so a
  detector's own keyword arguments are dead on every live and backtest path.
  `analytics/signal/scanner.py:219`/`:221` call `plugin["detector"](closed_df[, funding_df])`, and
  `analytics/backtest_runner.py:325` calls `_SIMPLE_DETECTORS[strategy](ohlcv)`; `DETECTOR_REGISTRY`
  is typed `Callable[[pd.DataFrame], pd.DataFrame]`, which has no params slot at all. Config `tp_r`
  is real, but it lands **downstream** — the alert formatter and the backtest engine each derive the
  target from the config-resolved value, never from the detector.
- **`tp_r` is declared sweepable on exactly the six detectors where it controls nothing, and is
  undeclared on both where a target is actually produced — the intersection is empty.** `engulfing`,
  `pin_bar`, `inside_bar`, `hammer_hanging_man`, `doji` and `morning_evening_star` each carry a
  `tp_r: float = 2.0` signature argument that appears nowhere in the body, plus a matching
  `ParamSpec("tp_r", …)` in `_registry.py`; the two that emit a `tp_price` column, `ema` and
  `ote_entry`, declare neither. So **a `tp_r` sweep on those six returns a flat surface, which is
  indistinguishable from a knob already at its optimum** — read
  `tests/test_signal_registry.py::test_tp_r_sweep_surface_is_inverted` before interpreting one. The
  inversion is pinned rather than fixed, because both repairs are decisions: dropping the
  declarations turns the flat surface into a `KeyError` at `tools/multi_symbol_wfo.py`'s unguarded
  `row.params["tp_r"]`, and re-homing them changes the sweep surface while the TA book is frozen.
- **A third shape spends a target on prose.** `orb_breakout` computes a TP and interpolates it into
  its `context` **string** only, never the `tp_price` column, so the rendered header could contradict
  the levels block beneath it (fixed #212). When auditing a detector's target, grep for the column
  write, not for the concept — `grep -c tp_price` counts docstrings and local variables too.

`DEFAULT_DB_PATH` lives in `analytics/store/_common.py` (re-exported via `analytics.store` and
`analytics.data_store`) — import from either re-export, never redefine it in a runner.

## cooldown_store.py

- Two-layer dedup:
  1. Candle watermark per `(symbol, tf, strategy)` — prevents re-firing same candle
  2. Cooldown timer per `(symbol, strategy, direction)` — time-based suppression
- JSON-persisted to `signal_state.json`
- **Both live configs share ONE state file, deliberately** (`state_file` is a config field
  defaulting to `signal_state.json`; neither declares an override). The watermark key carries no
  config identity, so the sharing IS the cross-config dedup: `signal_watch_weekdays`' 32 declared
  cells are a strict **superset** of `signal_watch`'s 22 (overlap 22 of 22, verified 2026-08-14)
  and both dispatch to the same Telegram channels, so splitting the file would let one Tue–Thu
  candle alert twice. **Do not give weekdays its own `state_file`.** The cost of sharing is
  narrower than it looks: `day_filter` is applied inside `scan_symbol` *before* events are
  returned, so a suppressed candle never fires and never marks — after a `tue_thu` run Mon/Fri are
  still unmarked and a later weekdays run alerts them normally. Whichever config runs first alerts
  the shared candles with its own `tp_r`; the other then stays silent. No alert is lost, so this is
  operator discipline (do not run both live on the same day), not a defect.
- `is_new_candle` / `mark_candle` take `channel: str = "primary"` (Task D, 2026-05-20). Primary preserves the legacy `{sym}:{tf}:{strategy}` key shape so existing state files load without migration; wife uses `{sym}:{tf}:{strategy}:wife`. Scanner marks **each** channel's watermark only on a successful live `dispatch_to_channel` — primary on a successful primary send, wife on a successful wife send — so a non-sending / dry run never "consumes" a candle (which would dedup the real alert away) and the two watermarks move independently (fix `fix/watermark-on-send`, 2026-06-06; primary previously marked unconditionally).
- `last_marked(symbol, tf, strategy, channel="primary") -> int | None` (catch-up, 2026-06-06) — returns the stored watermark or `None` when never marked. Distinct from `is_new_candle`'s `-1` sentinel: the `--catch-up` cold-start guard needs to tell "no prior watermark" (→ seed latest candle only, no burst) apart from "marked at candle 0".

## Missed-day catch-up (`--catch-up`)

The scanner normally fires only on the single latest closed candle, so a skipped run-day
permanently loses that day's signals — there is no backlog replay by default.
`wifey signal watch --catch-up` (off by default) replays every un-alerted closed candle since the
last run:

- `scan_symbol(..., catch_up=True)` emits an event for every closed candle in the scan window
  (each carrying its own candle close as the entry price), not just the latest; the forming bar is
  still excluded (`open_time <= latest_open_time`).
- `run_scan_cycle(..., catch_up=True)` splits each `(symbol, tf)` scan result into one
  pseudo-result per candle `open_time`, so conflict resolution and confluence stacking stay
  per-candle correct; each group carries an `is_backfill` flag. The newest closed candle is read
  from OHLCV, not from the events (mirroring `scan_symbol`'s conditional forming-bar rule) — using
  `max(event.open_time)` would promote an older candle to "live" whenever the newest bar produced
  no signal. The existing candle watermark then drops the candles already alerted on a prior run.
  With `catch_up=False` there is a single latest candle, one group, and output byte-identical to
  the pre-catch-up flow.
- Backfilled candles are recorded, never dispatched (record-not-dispatch, ported from the parent):
  the newest closed candle may reach Telegram, plus any older one still inside
  `max_alert_age_hours` (`may_dispatch_candle`; shared base ships 24.0, library default `0.0` =
  newest-only). Older candles still get DB signals and outcome-ledger rows and consume the primary
  watermark — the deliberate exception to the mark-on-dispatch rule; the wife watermark stays
  untouched, since nothing was dispatched on it and nothing reads it for dedup. A signal surfaced
  days late is untradeable noise in the chat but real ledger evidence: replay grows
  `signal_alert_outcomes` without flooding the channels with stale setups.
- Cold-start guard: a key with no prior watermark would treat every window candle as "new" and
  burst the whole ledger on first contact. The guard restricts such keys to the latest candle only
  (`open_time == latest_closed or last_marked(...) is not None`); later runs then catch up
  genuinely-missed candles. Recovery depth is bounded by the 200-candle `_SCAN_WINDOW` (4h ~33
  days, 1d ~200 days, 1wk ~4 years).
- The dispatch window, `max_alert_age_hours`: the newest closed candle always dispatches; an older
  one dispatches while its close is within the window. It is inert without `catch_up=True` — the
  non-catch-up branch never produces a non-latest candle group, so `is_backfill` is unconditionally
  `False` there and the window is never consulted. It exists because "strictly newest" was
  arbitrary under a fixed cadence: with one pre-open run a day, the session's first `4h` bar can
  never be the newest closed candle, so it was structurally undeliverable — 120 of 351 ledger
  candles (34%, every one the 13:30 UTC bar) were dropped for being 19.3h stale while the 15.3h bar
  shipped. `0.0` restores the old rule and is the documented escape hatch; a negative value is
  refused at load, since it would read as stricter while behaving exactly like `0.0`. Widening it
  cannot replay history, because an already-consumed watermark is dropped upstream at the
  `is_new_candle` filter before the window is consulted, so no window can revive it (constructed in
  `tests/test_catch_up.py::TestRunScanCycleRecencyWindow`). It also does not recover a skipped run
  day (~87h stale at a Mon run for Thu bars) — that stays a cadence habit, so run Wed/Thu/Fri.
  `fired_at_ms` is not a dispatch record, since it is overwritten on every re-detection; the
  `:wife` watermark is the only dispatch oracle, since backfill marks primary alone and a send
  marks both. Audit: `docs/audits/2026-08-25-dispatch-recency-window.md`.
- Fidelity caveat: regime / HTF-EMA / ADR / DOW bias context is computed as of the run time and
  applied to historical candles too — a deliberate best-effort approximation for a few missed
  days, not a full as-of-candle replay (the backtest live-parity path does that). A deep backfill
  is look-ahead in the gating and should be read as backtest output, not clean out-of-sample
  evidence.

## alert_formatter.py

### Dataclasses

- `SignalEvent`: `tp_price: float` (structural TP from detector; `0.0` = use `tp_r` fallback), `volume_spike: bool` (> 3× rolling mean), `confluence_combo: ConfluenceData | None`
- `StatsContext`: `adr_move_up: bool | None`, `wk_low/high_still_ahead_conditioned_pct: float | None`, `wk_move_bucket: str | None`
- `ConfluenceData`: `co_strategy`, `candles_ago`, `avg_r`, `trades`, `win_rate`, `type_a`, `type_b`, `orderflow_signals: list[str]`, `htf_tf: str = ""`, `ltf_tf: str = ""`

### Alert layout (6 sections)

1. Header — `strategy · variant` + stars, one line. `_reason_detail` strips from the detector reason whatever the alert already states elsewhere: the `@<price>` token **only when that price is the entry**, then the strategy name and the direction word on an underscore boundary. So `ema_pullback_long@333.85` renders `ema · pullback`, `doji_bull@326.99` collapses to bare `doji`, and `ob_long@303.27-307.23` keeps its zone — a blunt `@`-strip would delete the upper bound and leave `-307.23` dangling.
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
- An empty sample says which emptiness it is: `no closed longs yet` (n=0) vs `3 longs — too few to judge` (n below `min_trades`). `n/a` read as "broken" rather than "checked, nothing there".

### Other

- `format_signal_alert()` / `format_confluence_alert()` — both accept `ohlcv_df: pd.DataFrame | None` (signal candle = last row) + `gap_warning: str | None` (overnight equity gap notice; replaces parent's `cme_gap_warning`)
- `_adr_bar(consumed_pct)` — 10-char ASCII bar with `▓` overflow
- `_format_stats_line(ctx, direction)` — direction-aware; line 1: `📐` bull%/P1/ADR; line 2: `🎯` TP window/weekly timing
- Same-TF confluence renders `> ⚡⚡ CONFLUENCE`; cross-TF renders `> ⚡⚡ CONFLUENCE (1d → 4h)`
- `orderflow_signals` is a step-5 extension point for CoinGlass/NPOC lines

### Wife-channel formatter (Task D, 2026-05-20)

- `format_wife_alert()` / `format_wife_confluence_alert()` — BUY/WAIT wife-channel variant: the primary layout condensed, not a different one. Strips strategy name, reason, edge backtest summary and stats line; keeps stars and **one** warning. Design: `docs/superpowers/specs/2026-08-18-wife-alert-layout-design.md`.
- LONG → header `BUY — $SYM TF` + stars, `Entry <price> · <time> MYT`, one `Stop … · Target …` line with signed percentages and no R multiple (same widest-structural-SL / floor / structural-TP-or-tp_r logic as the primary formatter), then at most one warning.
- SHORT → header `WAIT — $SYM TF` + price + time + `Sit tight — conditions look weak`. No levels — wife is not expected to action shorts. The header says `WAIT`, not `HOLD`: "hold" is a position instruction presuming she is already in, when the intent is "take no action".
- The single warning is ranked by `_WIFE_WARNING_RANK`, because `_build_candle_warnings` appends in source order and never sorts. `⚡ Volume spike` is excluded outright — it is an encouragement *and* the builder's first entry, so taking the head of the list would render it under a warning heading.
- Both renders print on every `wifey signal test`, so the wife body is reviewable without a send. Do not reintroduce building it only inside the `send_telegram` branch: that limited the wife dry-run log to the first line.
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
  `load_research_universe(path=Path("config/universe.json"), *, min_history_days=None)`. Distinct
  from the live-alert watchlist. `delisted` is a lifecycle seam and it is used: 3 of 505 are
  flagged (`EA`, `EQR`, `SATS`, 2026-09-02) and a flagged member is retained rather than deleted,
  so `symbols()` stays 505 while the active accessors return 502 (point-in-time membership
  deliberately not scraped per gap-map decision #6).
- The optional `listed` per-member date (first available 1d bar; `None` ⇒ full-history survivor
  listed on/before the backfill start) is the history seam: `with_min_history(days)` — also
  surfaced as the `load_research_universe(min_history_days=…)` kwarg — drops members with fewer than
  `days` of history as of the fixed `membership_as_of` snapshot. Pure (no DB), reproducible,
  identity for `days ≤ 0`, untagged members always kept; default-off (`None`) keeps every member
  byte-identically. Forward-prep for the G1-gated XS-momentum sleeve's uniform lookback. Untagged
  members are always kept, which is why this seam fails open for anything never stamped: hand-
  stamped on only 3 of 505 until 2026-08-13, so a 1-year floor dropped 0 members at that point. 26
  members are stamped today from the DB by `tools/stamp_universe_listed.py`
  (`make universe-stamp-listed`) — re-run it after any membership or backfill change, since a new
  constituent arrives untagged. → `context/tools.md`

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
- `french_client.py` — Ken French daily 3-factor file (OV-1, #418; stdlib `urllib` + `zipfile`, no
  key). `fetch_zip` / `fetch_daily_factors` take the byte fetcher as a parameter; `csv_from_zip` and
  `parse_daily_factors` are pure and return decimals, not the file's percent. The parser raises
  `FrenchFormatError` on any malformed row rather than skipping it, since a skipped row shortens
  the panel silently. The file carries NYSE Saturdays to 1952, which `^GSPC` lacks, so map onto
  its calendar rather than inner-joining. `RF` is quantised to 1 bp a day.
- `yfinance_client.fetch_total_return_close` — the one `auto_adjust=True` fetch (dividend- and
  split-adjusted close), for research frames that compare holding with cash. Never write it to
  `ohlcv`, whose levels stay raw.

### Live display

- `live_store.py` — shared in-memory store for live WebSocket data.
- `live_loop.py` — shared Rich live display loop logic.
