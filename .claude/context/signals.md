# Signals Package Reference

Detailed reference for `signals/`. Load this when working on alert formatting, cooldown, or the signal registry.

## registry.py

- `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` — 20 actionable strategies
- Excluded: `seasonality` (inactive by design), `funding_reversion` (no live feed, partial DB), `fibonacci_retracement` (legacy)
- `confidence` field removed — resolved per-TF at dispatch via `STRATEGY_REGISTRY[name].get_confidence(tf)`

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
- Same-TF confluence renders `> ⚡⚡ CONFLUENCE`; cross-TF renders `> ⚡⚡ CONFLUENCE (4h → 15m)`
- `orderflow_signals` is a step-5 extension point for CoinGlass/NPOC lines

### Wife-channel formatter (Task D, 2026-05-20)

- `format_wife_alert()` / `format_wife_confluence_alert()` — minimal BUY/HOLD wife-channel variant. Strips strategy name, reason, stars, candle warnings, edge backtest summary, and stats line.
- LONG → header `BUY — $SYM TF` + entry price + time + SL/TP block (same widest-structural-SL / floor / structural-TP-or-tp_r logic as the primary formatter).
- SHORT → header `HOLD — $SYM TF` + price + time + `(regime caution — sit tight)`. No SL/TP — wife is not expected to action shorts; HOLD is regime context only.
- Dispatched via `utils.telegram_router.dispatch_to_channel(msg, "wife")`; `TELEGRAM_WIFE_DRY_RUN=1` logs the first line at INFO instead of sending.
