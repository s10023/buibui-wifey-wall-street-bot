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
