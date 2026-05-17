"""Signal event model and Telegram alert formatter."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from analytics.signal.types import ConfluenceData, SignalEvent, StatsContext

__all__ = [
    "ConfluenceData",
    "SignalEvent",
    "StatsContext",
    "format_confluence_alert",
    "format_signal_alert",
]

_MYT = timezone(timedelta(hours=8))
_ET = ZoneInfo("America/New_York")


_SESSION_EMOJI = {
    "Pre-Market": "🌅",
    "RTH": "🏛️",
    "Power Hour": "⚡",
    "After Hours": "🌙",
}

# US equity session windows in ET minutes-since-midnight (start inclusive, end exclusive).
# Using ET (America/New_York) handles EST/EDT automatically so the windows stay
# anchored to real-world session opens year-round.
#   Pre-Market:   04:00–09:30 ET — ECN open through RTH cash open
#   RTH:          09:30–15:00 ET — regular trading hours, ex-Power-Hour
#   Power Hour:   15:00–16:00 ET — last hour of cash, distinct volatility regime
#   After Hours:  16:00–20:00 ET — post-close ECN
# Outside 20:00–04:00 ET: overnight/closed, no session label emitted.
_EQUITY_SESSIONS: list[tuple[str, int, int]] = [
    ("Pre-Market", 4 * 60, 9 * 60 + 30),
    ("RTH", 9 * 60 + 30, 15 * 60),
    ("Power Hour", 15 * 60, 16 * 60),
    ("After Hours", 16 * 60, 20 * 60),
]


def _get_session_label(dt: datetime) -> str:
    """Return the US equity session label for a datetime, or empty string if outside."""
    et = dt.astimezone(_ET)
    minutes = et.hour * 60 + et.minute
    for label, start, end in _EQUITY_SESSIONS:
        if start <= minutes < end:
            return label
    return ""


def _fmt_time(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=_MYT).strftime("%d-%b %H:%M")


def _stars(n: int) -> str:
    """Return a 5-char star string, e.g. n=4 → '★★★★☆'."""
    return "★" * n + "☆" * (5 - n)


def _adr_bar(consumed_pct: float) -> str:
    """Return a 10-char ASCII progress bar for ADR consumption.

    0–100%: filled █ + empty ░, e.g. 88% → [████████░░]
    >100%:  full bar + overflow indicator ▓, e.g. 112% → [██████████▓]
    """
    BAR_LEN = 10
    filled = min(round(consumed_pct * BAR_LEN), BAR_LEN)
    bar = "█" * filled + "░" * (BAR_LEN - filled)
    suffix = "▓" if consumed_pct > 1.0 else ""
    return f"[{bar}{suffix}]"


def _weekly_timing_part(ctx: "StatsContext", is_long: bool) -> str | None:
    """Format the weekly-framing fragment for the stats line.

    Prefers the conditioned (by move bucket) value, then falls back to unconditional.
    Returns None when no weekly data is available.
    """
    if is_long:
        cond_pct = ctx.wk_low_still_ahead_conditioned_pct
        plain_pct = ctx.wk_low_still_ahead_pct
        label = "Weekly low"
    else:
        cond_pct = ctx.wk_high_still_ahead_conditioned_pct
        plain_pct = ctx.wk_high_still_ahead_pct
        label = "Weekly high"

    if cond_pct is not None and ctx.wk_move_bucket is not None:
        return f"{label}: {cond_pct:.0%} still ahead ({ctx.wk_move_bucket} move)"
    if plain_pct is not None:
        return f"{label}: {plain_pct:.0%} still ahead"
    return None


def _format_stats_line(ctx: "StatsContext", direction: str) -> str:
    """Format the stats context as two plain-English lines for Telegram.

    Line 1: bull%, P1 context (direction-aware), ADR bar
    Line 2: TP window (peak hour for target direction) + weekly timing
    """
    dow_short = ctx.today_dow[:3]
    dow_plural = ctx.today_dow + "s"
    avg_ret_str = f"{ctx.avg_return_today:+.1%}"
    bull_str = (
        f"{dow_short} closes bullish {ctx.bull_pct_today:.0%} ({avg_ret_str} avg)"
    )

    is_long = direction != "short"
    # "still ahead" = probability the directional extreme has NOT been set yet.
    # High = caution (likely more adverse move coming). Low = favourable.
    if is_long:
        p1_str = f"Low still ahead {1 - ctx.p1_low_pct_today:.0%} of {dow_plural}"
    else:
        p1_str = f"High still ahead {ctx.p1_low_pct_today:.0%} of {dow_plural}"

    if ctx.adr_consumed_pct is not None:
        adr_str = f"ADR {_adr_bar(ctx.adr_consumed_pct)} {ctx.adr_consumed_pct:.0%} · {ctx.adr_14:.1%}"
    else:
        adr_str = f"ADR {ctx.adr_14:.1%}"

    line1 = f"📐 {bull_str} · {p1_str} · {adr_str}"

    parts2: list[str] = []
    peak_hour = ctx.peak_high_hour_dow if is_long else ctx.peak_low_hour_dow
    if peak_hour is not None:
        extreme = "high" if is_long else "low"
        parts2.append(f"TP window: {extreme} ~{peak_hour:02d}:00 MYT on {dow_plural}")

    weekly_part = _weekly_timing_part(ctx, is_long)
    if weekly_part is not None:
        parts2.append(weekly_part)

    if parts2:
        return line1 + "\n🎯 " + " · ".join(parts2)
    return line1


# ---------------------------------------------------------------------------
# Candle-level warning helpers
# ---------------------------------------------------------------------------


def _is_marubozu(o: float, h: float, lo: float, c: float) -> bool:
    """Both wicks ≤ 10% of body → wickless candle, body tends to fill first."""
    body = abs(c - o)
    if body == 0:
        return False
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - lo
    return upper_wick / body <= 0.10 and lower_wick / body <= 0.10


def _is_doji(o: float, h: float, lo: float, c: float) -> bool:
    """Body < 10% of total range → pure indecision candle."""
    total_range = h - lo
    if total_range == 0:
        return False
    return abs(c - o) / total_range <= 0.10


def _is_inside_bar(h: float, lo: float, prev_h: float, prev_l: float) -> bool:
    """Signal candle range entirely within prior candle's range → breakout unconfirmed."""
    return h <= prev_h and lo >= prev_l


def _wick_rejection_against(
    o: float, h: float, lo: float, c: float, direction: str
) -> bool:
    """Wick pointing against signal direction > 40% of range → price resisted.

    LONG: long upper wick = price rejected higher levels on this candle.
    SHORT: long lower wick = price rejected lower levels on this candle.
    """
    total_range = h - lo
    if total_range == 0:
        return False
    wick_against = h - max(o, c) if direction == "long" else min(o, c) - lo
    return wick_against / total_range > 0.40


def _has_equal_levels(
    df: pd.DataFrame,
    price: float,
    direction: str,
    lookback: int = 10,
    tol_pct: float = 0.0015,
) -> bool:
    """Detect equal highs (above price, warns on SHORT) or equal lows (below, warns on LONG).

    Equal highs above entry = buy-side liquidity → sweep likely before SHORT continues.
    Equal lows below entry = sell-side liquidity → sweep likely before LONG continues.
    Excludes the signal candle itself; looks at the preceding `lookback` candles.
    """
    hist = df.iloc[:-1].tail(lookback)
    if len(hist) < 2:
        return False
    if direction == "long":
        candidates = [float(v) for v in hist["low"].values if float(v) < price]
    else:
        candidates = [float(v) for v in hist["high"].values if float(v) > price]
    if len(candidates) < 2:
        return False
    # Sorted adjacency scan: if any pair is within tolerance, the closest pair
    # must be adjacent after sorting — O(n log n) vs the prior O(n²) double loop.
    candidates.sort()
    tolerance = price * tol_pct
    return any(
        candidates[i + 1] - candidates[i] <= tolerance
        for i in range(len(candidates) - 1)
    )


def _has_consecutive_candles(df: pd.DataFrame, direction: str, n: int = 3) -> bool:
    """Last n candles (including signal candle) all closed in signal direction → overextension."""
    if len(df) < n:
        return False
    recent = df.tail(n)
    if direction == "long":
        return bool((recent["close"] > recent["open"]).all())
    return bool((recent["close"] < recent["open"]).all())


def _build_candle_warnings(
    events: list["SignalEvent"],
    ohlcv_df: pd.DataFrame | None,
) -> list[str]:
    """Build ordered list of warning/note strings for the consolidated warnings block.

    Volume notes (moved from header), candle shape checks, structural, and momentum
    warnings. All are silent unless triggered. Rendered after SL/TP in the alert.
    """
    notes: list[str] = []

    if any(e.volume_spike for e in events):
        notes.append("⚡ Volume spike — high conviction")
    elif any(e.low_volume for e in events):
        notes.append("⚠️ Low volume — weaker conviction")

    if ohlcv_df is None or len(ohlcv_df) < 2:
        return notes

    last = ohlcv_df.iloc[-1]
    o, h, lo, c = (
        float(last["open"]),
        float(last["high"]),
        float(last["low"]),
        float(last["close"]),
    )
    prev = ohlcv_df.iloc[-2]
    prev_h = float(prev["high"])
    prev_l = float(prev["low"])
    direction = events[0].direction
    price = events[0].price
    is_doji = _is_doji(o, h, lo, c)

    # W7/W1: doji takes precedence over marubozu (doji has no dominant body).
    if is_doji:
        notes.append("⚠️ Doji signal candle — direction uncertain")
    elif _is_marubozu(o, h, lo, c):
        notes.append("⚠️ Wickless candle — body tends to fill first")

    # W8: Inside bar
    if _is_inside_bar(h, lo, prev_h, prev_l):
        notes.append("⚠️ Signal inside prior range — breakout unconfirmed")

    # W5: Wick rejection against direction (skip on doji — no dominant wick)
    if not is_doji and _wick_rejection_against(o, h, lo, c, direction):
        wick_label = "Upper" if direction == "long" else "Lower"
        notes.append(f"⚠️ {wick_label} wick rejection — price resisted signal direction")

    # W2: Equal highs / equal lows (liquidity pool warning)
    if _has_equal_levels(ohlcv_df, price, direction):
        if direction == "long":
            notes.append("⚠️ Equal lows below — sell-side liquidity, sweep likely first")
        else:
            notes.append("⚠️ Equal highs above — buy-side liquidity, sweep likely first")

    # W6: Consecutive candles in signal direction → possible overextension
    if _has_consecutive_candles(ohlcv_df, direction):
        bias = "bullish" if direction == "long" else "bearish"
        notes.append(f"⚠️ 3 {bias} candles in a row — possible overextension")

    return notes


# ---------------------------------------------------------------------------
# SL helper
# ---------------------------------------------------------------------------


def _widest_sl(
    events: list["SignalEvent"],
    direction: str,
    price: float,
    sl_pct: float,
) -> float:
    """Return the widest (most conservative) structural SL across a list of events.

    Widest for long = lowest sl_price below price (largest risk distance).
    Widest for short = highest sl_price above price.
    Falls back to pct-based SL if no valid structural level exists.

    Using the widest level for confluence means the trade has room to breathe —
    you need the furthest structural level to actually be invalidated.
    """
    if direction == "long":
        valid = [e.sl_price for e in events if 0 < e.sl_price < price]
        return min(valid) if valid else price * (1 - sl_pct)
    valid = [e.sl_price for e in events if e.sl_price > price]
    return max(valid) if valid else price * (1 + sl_pct)


def _apply_min_sl_floor(
    price: float, sl_price: float, direction: str, min_sl_pct: float
) -> float:
    """Widen SL so the distance from price is at least min_sl_pct of price."""
    if min_sl_pct <= 0:
        return sl_price
    min_dist = price * min_sl_pct
    if direction == "long" and (price - sl_price) < min_dist:
        return price - min_dist
    if direction == "short" and (sl_price - price) < min_dist:
        return price + min_dist
    return sl_price


def _candles_ago_str(n: int) -> str:
    if n == 0:
        return "this candle"
    if n == 1:
        return "1 candle ago"
    return f"{n} candles ago"


def _format_cofire_block(cofire: "ConfluenceData") -> str:
    """Render the co-firing confluence blockquote (with optional orderflow lines)."""
    ago_str = _candles_ago_str(cofire.candles_ago)
    if cofire.htf_tf:
        header = (
            f"\n> ⚡⚡ CONFLUENCE ({cofire.htf_tf} → {cofire.ltf_tf})"
            f"\n> {cofire.co_strategy} ({cofire.htf_tf}) {ago_str}"
        )
    else:
        header = f"\n> ⚡⚡ CONFLUENCE\n> {cofire.co_strategy} co-fired {ago_str}"
    block = (
        header + f"\n> Combo avg R: +{cofire.avg_r:.2f}R"
        f" · {cofire.trades} trades"
        f" · {cofire.win_rate:.1%} win"
        f"\n> Types: {cofire.type_a} + {cofire.type_b}"
    )
    for sig in cofire.orderflow_signals:
        block += f"\n> {sig}"
    return block


def _format_header(events: list["SignalEvent"], direction_label: str) -> str:
    """Section 1 header — single-strategy layout or stacked confluence layout."""
    first = events[0]
    if len(events) == 1:
        ev = first
        stars = f"  {_stars(ev.confidence)}" if ev.confidence else ""
        conflict_tag = " ⚠️ conflict" if ev.conflict else ""
        header = (
            f"<b>SIGNAL — ${ev.symbol} {ev.timeframe}  ·  {direction_label}</b>\n"
            f"<code>{ev.strategy}</code>{stars}{conflict_tag}\n"
            f"<code>{ev.reason}</code>\n"
        )
        if ev.context:
            header += f"{ev.context}\n"
        return header

    header = (
        f"<b>SIGNAL — ${first.symbol} {first.timeframe}  ·  {direction_label}</b>\n"
        f"Confluence: {len(events)} strategies\n"
    )
    for ev in events:
        stars = f" {_stars(ev.confidence)}" if ev.confidence else ""
        conflict_tag = " ⚠️ conflict" if ev.conflict else ""
        line = f"• <code>{ev.strategy}</code>{stars} — <code>{ev.reason}</code>{conflict_tag}"
        if ev.context:
            line += f"  ({ev.context})"
        header += line + "\n"
    return header


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------


def format_signal_alert(
    event: "SignalEvent",
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    min_sl_pct: float = 0.0,
    gap_warning: str | None = None,
    ohlcv_df: pd.DataFrame | None = None,
) -> str:
    """Format a single SignalEvent as a Markdown Telegram message.

    Uses structural sl_price when valid; falls back to sl_pct otherwise.
    min_sl_pct: if set, SL distance is floored at this fraction of price.
    ohlcv_df: recent OHLCV (signal candle = last row) for candle-level warnings.
    """
    return format_confluence_alert(
        [event],
        sl_pct=sl_pct,
        tp_r=tp_r,
        min_sl_pct=min_sl_pct,
        gap_warning=gap_warning,
        ohlcv_df=ohlcv_df,
    )


def format_confluence_alert(
    events: list["SignalEvent"],
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    min_sl_pct: float = 0.0,
    backtest_summary: str | None = None,
    stats_context: "StatsContext | None" = None,
    gap_warning: str | None = None,
    ohlcv_df: pd.DataFrame | None = None,
) -> str:
    """Format one or more SignalEvents (same symbol/tf/direction) as a Telegram message.

    Single event: original single-strategy layout.
    Multiple events: stacked confluence layout showing all strategies.
    SL is the widest (most conservative) structural level across all events.
    min_sl_pct: if set, SL distance is floored at this fraction of price (e.g. 0.005
    ensures SL is at least 0.5% away from entry — useful to suppress noise signals).
    ohlcv_df: recent OHLCV (signal candle = last row) enables candle-level warnings.

    Layout (sections separated by blank lines):
      1. Header — symbol/tf/direction, strategy, stars, reason, context
      2. Entry — price, time, equity session label
      3. Levels — SL and TP
      4. Warnings — consolidated notes block (volume, candle shape, structure, momentum)
      5. Edge — backtest summary + co-firing confluence blockquote
      6. Context — stats lines (DOW, ADR, TP timing, weekly framing)
    """
    first = events[0]
    direction = first.direction
    price = first.price
    direction_label = "LONG 🟢" if direction == "long" else "SHORT 🔴"

    signal_dt = datetime.fromtimestamp(first.open_time / 1000, tz=_MYT)
    session = _get_session_label(signal_dt)
    session_line = f"{_SESSION_EMOJI[session]} {session}\n" if session else ""

    # Levels: widest structural SL, floored by min_sl_pct; TP prefers structural tp_price.
    sl_price = _apply_min_sl_floor(
        price, _widest_sl(events, direction, price, sl_pct), direction, min_sl_pct
    )
    if direction == "long":
        sl_dist = price - sl_price
        structural_tp = first.tp_price if first.tp_price > price else 0.0
        tp_price = structural_tp if structural_tp > 0 else price + sl_dist * tp_r
    else:
        sl_dist = sl_price - price
        structural_tp = first.tp_price if 0 < first.tp_price < price else 0.0
        tp_price = structural_tp if structural_tp > 0 else price - sl_dist * tp_r

    sl_pct_display = abs(sl_dist / price) * 100
    actual_r = abs(tp_price - price) / sl_dist if sl_dist > 0 else tp_r
    tp_pct_display = abs(tp_price - price) / price * 100

    # --- Section 1: Header ---
    header = _format_header(events, direction_label)

    # --- Section 2: Entry ---
    entry_line = f"{price:,.2f}  ·  {_fmt_time(first.open_time)} MYT\n"

    # --- Section 3: Levels ---
    sl_tp = (
        f"SL: {sl_price:,.2f}  ({sl_pct_display:.1f}%)\n"
        f"TP: {tp_price:,.2f}  ({tp_pct_display:.1f}%  ·  {actual_r:.1f}R)"
    )

    # --- Section 4: Warnings ---
    warnings = _build_candle_warnings(events, ohlcv_df)
    if gap_warning:
        warnings.append(gap_warning)
    warnings_block = ("\n\n" + "\n".join(warnings)) if warnings else ""

    # --- Section 5: Edge (backtest summary + co-firing confluence) ---
    edge_block = ""
    if backtest_summary:
        edge_block += f"\n\n{backtest_summary}"

    best_cofire: ConfluenceData | None = max(
        (e.confluence_combo for e in events if e.confluence_combo is not None),
        key=lambda c: c.avg_r,
        default=None,
    )
    if best_cofire is not None:
        edge_block += f"\n{_format_cofire_block(best_cofire)}"

    # --- Section 6: Context (stats) ---
    stats_block = (
        f"\n\n{_format_stats_line(stats_context, direction)}"
        if stats_context is not None
        else ""
    )

    return (
        header
        + f"\n{entry_line}"
        + session_line
        + f"\n{sl_tp}"
        + warnings_block
        + edge_block
        + stats_block
    )
