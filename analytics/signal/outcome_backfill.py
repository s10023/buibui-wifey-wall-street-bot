"""Forward-walk OHLCV to resolve outstanding signal_alert_outcomes rows.

P1 (commit a21681a) made every row carry `tp_price` + `rr_ratio` at fire time.
This module is the matching reader: scan rows where `outcome IS NULL` and
walk forward through OHLCV to record whether TP or SL was touched first,
mirroring the backtest engine semantics in `analytics/backtest/engine.py`:

  - long  win:  `high >= tp_price` before `low <= sl_price`
  - long  loss: `low  <= sl_price` before `high >= tp_price` (or same-bar tie)
  - short win:  `low  <= tp_price` before `high >= sl_price`
  - short loss: `high >= sl_price` before `low  <= tp_price` (or same-bar tie)

Same-bar TP+SL resolves to "loss" (conservative, matches the engine).

Outcomes:
  - "win"     — TP hit first. outcome_r = +the R implied by `tp_price`
                (`effective_tp_r`), NOT the declared `rr_ratio`.
  - "loss"    — SL hit first or same-bar tie. outcome_r = -1.0
  - "expired" — exceeded `max_hold_bars` without hitting either.
                outcome_r = mark-to-market at the last in-window bar.
  - (NULL)    — still within hold window; retry on the next cycle.

Pure function over conn + now_ms + config dict; no clock or network I/O.
"""

import logging

import duckdb
import numpy as np
import pandas as pd

from analytics.data_store import get_ohlcv
from analytics.signal._common import parse_timeframe_secs

logger = logging.getLogger(__name__)


# Hold caps in BARS — never in calendar time. Each is calibrated to the FRACTION
# OF TRADES THAT RESOLVE WITHIN IT, measured over the full closed-trade
# population in `backtest_trades` (2026-08-12): `4h` 30 bars covers **91.0%** of
# 15,799 trades, `1d` 14 bars covers **85.5%** of 7,168. So a cap bites the tail
# and leaves the body alone. That coverage figure — not a day count — is the
# thing to reproduce when adding or revisiting a timeframe; the script is
# `docs/plans/scripts/max_hold_coverage.py`.
#
# Coverage MUST be computed with the one-bar offset below (a live cap of N
# admits `bars_held <= N-1`). Comparing `bars_held <= N` instead inflates every
# figure by roughly a percentage point and is not comparable across timeframes,
# because the size of the bar-0 bucket differs sharply by TF (`4h` 20%, `1d`
# 28%, `1wk` 58%).
#
# READ THESE AS BARS. US-equity RTH does not have six `4h` bars in a day — it
# has TWO — so the old "5d" annotation understated the real window by 3×.
# Counting a row's age in calendar days against that annotation is how a
# correctly-open row looks overdue: on 2026-08-12 all 31 open ledger rows were
# simply starved of bars (`backfill_outcomes` returned `open: 31`, nothing
# resolvable), while the calendar reading manufactured a phantom "23 rows past
# their hold window".
#
# Override per-TF via `backfill_outcomes(..., max_hold_bars_by_tf=...)`. That is
# a FUNCTION parameter only — there is no `[outcome_backfill]` TOML block, and
# `signal_runner` passes no override, so on the production path these defaults
# are always the effective values.
#
# Every timeframe any config scans MUST have an entry here — `_resolve_max_hold`
# refuses an unlisted one rather than guessing, and `test_max_hold_covers_every
# _configured_timeframe` fails the build if a config adds a timeframe without one.
DEFAULT_MAX_HOLD_BARS: dict[str, int] = {
    "15m": 96,  # 96 bars — no live rows
    "1h": 48,  # 48 bars — no live rows
    "4h": 30,  # 30 bars = ~15 RTH trading days (~3 calendar weeks), NOT 5 days
    "1d": 14,  # 14 bars = 14 trading days (~20 calendar days), NOT 2 weeks
    # 7 bars ≈ 7 weeks. Covers 91.1% of 729 closed `1wk` trades, matched
    # deliberately to `4h`'s 91.0% rather than `1d`'s 85.5% (which would be 5):
    # the error is asymmetric. Too small force-expires a signal that would have
    # reached TP/SL and writes that wrong label PERMANENTLY, because this module
    # only ever revisits rows where `outcome IS NULL`; too large merely leaves a
    # row open one more cycle. Chosen by the user 2026-08-12. Note the live
    # window is offset by one from the backtest's: `_scan_forward` counts bars
    # strictly after the SIGNAL candle, while a backtest trade enters on the next
    # bar's open and can exit on it (58% of `1wk` trades do), so
    # live max_hold = backtest bars_held + 1.
    "1wk": 7,
}


def _resolve_max_hold(tf: str, hold_map: dict[str, int]) -> int | None:
    """Bars to scan for `tf`, or None if the timeframe has no calibrated cap.

    Returning None makes the caller leave those rows unresolved. The previous
    behaviour was `hold_map.get(tf, max(hold_map.values()))` — a fallback to the
    LOOSEST entry, so an unlisted timeframe silently received the most permissive
    window available: `1wk` would have taken the `15m` value of 96 bars, i.e. a
    **96-week** hold. Guessing wide is not the safe direction here, because the
    guess ends up written into `signal_alert_outcomes` as an `expired` label with
    a mark-to-market `outcome_r`, and nothing revisits it.
    """
    return hold_map.get(tf)


def effective_tp_r(
    *,
    direction: str,
    entry: float,
    sl_price: float,
    rr_ratio: float,
    tp_price: float | None,
) -> float:
    """The R multiple the alert's TP was ACTUALLY at — not the declared one.

    `signal_alert_outcomes.rr_ratio` records the *configured* `tp_r`, but
    `alert_formatter` (mirrored by `_resolve_outcome_sl_tp`) prefers a detector's
    **structural** TP when it has one, and only then falls back to
    `entry ± sl_dist × tp_r`. So on a structural-TP alert the stored `rr_ratio`
    is the DECLARED target while `tp_price` is the EFFECTIVE one.

    This is the single definition of that conversion. It is deliberately shared
    by all three surfaces that need it — the resolver below (which credits the
    target it WALKED), `scanner.py` (which records it at fire time) and
    `analytics/exits/audit.py` (which re-derives it read-side for the replay) —
    because inlining it four times is what let the declared/effective split
    diverge unnoticed in the first place (#165).

    Falls back to `rr_ratio` when `tp_price` is absent, zero, or on the wrong
    side of entry — the same guards `alert_formatter` applies before trusting
    it. On a pct-fallback row `tp_price` is `entry ± sl_dist × tp_r` by
    construction, so the implied value reproduces `rr_ratio` exactly.
    """
    risk = abs(entry - sl_price)
    if risk <= 0.0 or tp_price is None or tp_price <= 0.0:
        return rr_ratio
    implied = (
        (tp_price - entry) / risk if direction == "long" else (entry - tp_price) / risk
    )
    return implied if implied > 0.0 else rr_ratio


def _scan_forward(
    bars: pd.DataFrame,
    candle_ts_ms: int,
    direction: str,
    entry: float,
    sl_price: float,
    tp_price: float,
    rr_ratio: float,
    max_hold_bars: int,
) -> tuple[str | None, float | None, int | None]:
    """Decide outcome for one signal given pre-fetched OHLCV bars for its TF.

    A win is credited the R implied by `tp_price` — the level this walk actually
    tests — and NOT the stored `rr_ratio`, which is only the declared target and
    is the larger of the two on a structural-TP alert. `rr_ratio` survives as the
    fallback inside `effective_tp_r` for rows whose `tp_price` is unusable.
    """
    post = bars[bars["open_time"] > candle_ts_ms].reset_index(drop=True)
    if post.empty:
        return None, None, None

    window = post.iloc[:max_hold_bars]
    h = window["high"].to_numpy()
    lo = window["low"].to_numpy()
    t = window["open_time"].to_numpy()

    if direction == "long":
        sl_idxs = np.nonzero(lo <= sl_price)[0]
        tp_idxs = np.nonzero(h >= tp_price)[0]
        sign = 1.0
    else:
        sl_idxs = np.nonzero(h >= sl_price)[0]
        tp_idxs = np.nonzero(lo <= tp_price)[0]
        sign = -1.0

    sl_first = int(sl_idxs[0]) if len(sl_idxs) else len(t)
    tp_first = int(tp_idxs[0]) if len(tp_idxs) else len(t)

    if sl_first <= tp_first and sl_first < len(t):
        return "loss", -1.0, int(t[sl_first])
    if tp_first < len(t):
        credited = effective_tp_r(
            direction=direction,
            entry=entry,
            sl_price=sl_price,
            rr_ratio=rr_ratio,
            tp_price=tp_price,
        )
        return "win", float(credited), int(t[tp_first])

    # Neither hit within the window so far.
    if len(window) < max_hold_bars:
        return None, None, None

    sl_dist = abs(entry - sl_price)
    last_close = float(window["close"].iloc[-1])
    mtm_r = (last_close - entry) / sl_dist * sign if sl_dist > 0 else 0.0
    return "expired", float(mtm_r), int(t[-1])


def backfill_outcomes(
    conn: duckdb.DuckDBPyConnection,
    now_ms: int,
    max_hold_bars_by_tf: dict[str, int] | None = None,
) -> dict[str, int]:
    """Resolve unresolved signal_alert_outcomes rows by walking OHLCV forward.

    Returns a counts dict: {"win": N, "loss": N, "expired": N, "open": N,
                            "no_ohlcv": N, "no_hold_cap": N}.  "open" means the
    row was inspected but the hold window hasn't elapsed yet — it stays NULL so
    the next cycle can retry. "no_ohlcv" means we found no candles after the
    signal bar. "no_hold_cap" means the timeframe has no entry in
    `DEFAULT_MAX_HOLD_BARS`, so the rows were left unresolved rather than scored
    against a guessed window; it should always be 0 in production, and a
    non-zero value means a config gained a timeframe the calibration missed.

    Only rows with both `tp_price` and `sl_price` set are eligible — that
    matches the P1 fire-time persistence rule.
    """
    hold_map = {**DEFAULT_MAX_HOLD_BARS, **(max_hold_bars_by_tf or {})}

    rows = conn.execute(
        "SELECT signal_id, symbol, tf, direction, candle_ts_ms, "
        "entry_price, sl_price, tp_price, rr_ratio "
        "FROM signal_alert_outcomes "
        "WHERE outcome IS NULL "
        "AND tp_price IS NOT NULL "
        "AND sl_price IS NOT NULL "
        "AND entry_price IS NOT NULL "
        "AND rr_ratio IS NOT NULL"
    ).fetchall()

    counts = {
        "win": 0,
        "loss": 0,
        "expired": 0,
        "open": 0,
        "no_ohlcv": 0,
        "no_hold_cap": 0,
    }
    if not rows:
        return counts

    # Group by (symbol, tf) so each TF's OHLCV is fetched once.
    by_tf: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        by_tf.setdefault((r[1], r[2]), []).append(r)

    for (symbol, tf), tf_rows in by_tf.items():
        earliest_candle = min(r[4] for r in tf_rows)
        tf_secs = parse_timeframe_secs(tf)
        # Pull bars from one TF-bar after the earliest signal up to the last
        # CLOSED bar.
        #
        # Admitting the still-forming bar is permanent damage. `get_ohlcv`
        # filters on `open_time`, so passing `now_ms` lets the current candle
        # through: its open_time has passed, but its OHLC is provisional and
        # `upsert_ohlcv` replaces it on the next sync. Two things then go wrong
        # at once — the bar count is inflated by one, so a hold window can be
        # declared complete a bar early, and the "expired" mark-to-market reads
        # a `close` that is really "wherever price is right now". Because this
        # module only revisits rows where `outcome IS NULL`, a label written off
        # a provisional bar is never corrected.
        #
        # `data_fetcher` does NOT already prevent this: it drops forming bars
        # only when yfinance hands them back with NaN OHLCV (the intermittent
        # case). A partial bar carrying real prices — the normal in-session
        # shape, and every `_resample_to_4h` bucket built from the 1h bars so
        # far — is upserted like any other.
        #
        # Measured on the wifey ledger 2026-08-11: 0 of 264 resolved rows
        # currently disagree with what the completed bars produce, so this is
        # preventive rather than a repair — but 29 of them (11%) resolved ON the
        # last bar of their hold window, which is the exposed shape. What the
        # bound really buys is that the answer stops depending on WHEN the
        # resolver was run; `make go-live` is a manual command documented for
        # after the US close, and nothing enforces that.
        #
        # A bar opening at T closes at T + tf_secs*1000, so this bound admits
        # exactly the bars whose close is already in the past. Rows that would
        # have resolved off the forming bar simply stay NULL and resolve on the
        # next cycle — which is what "open" already means here.
        last_closed_open_ms = now_ms - tf_secs * 1000
        bars = get_ohlcv(
            conn, symbol, tf, earliest_candle + tf_secs * 1000, last_closed_open_ms
        )
        if bars.empty:
            counts["no_ohlcv"] += len(tf_rows)
            continue

        max_hold = _resolve_max_hold(tf, hold_map)
        if max_hold is None:
            # No calibrated cap for this timeframe. Leave the rows NULL rather
            # than scoring them against a guessed window — see _resolve_max_hold.
            logger.warning(
                "No max_hold_bars for timeframe %r; leaving %d row(s) unresolved. "
                "Add an entry to DEFAULT_MAX_HOLD_BARS.",
                tf,
                len(tf_rows),
            )
            counts["no_hold_cap"] += len(tf_rows)
            continue

        updates: list[tuple[str, float, int, str]] = []

        for (
            signal_id,
            _sym,
            _tf,
            direction,
            candle_ts_ms,
            entry_price,
            sl_price,
            tp_price,
            rr_ratio,
        ) in tf_rows:
            outcome, outcome_r, filled_at = _scan_forward(
                bars,
                int(candle_ts_ms),
                str(direction),
                float(entry_price),
                float(sl_price),
                float(tp_price),
                float(rr_ratio),
                max_hold,
            )
            if outcome is None:
                counts["open"] += 1
                continue
            counts[outcome] += 1
            assert outcome_r is not None and filled_at is not None
            updates.append((outcome, outcome_r, filled_at, str(signal_id)))

        if updates:
            conn.executemany(
                "UPDATE signal_alert_outcomes "
                "SET outcome = ?, outcome_r = ?, outcome_filled_at_ms = ? "
                "WHERE signal_id = ?",
                updates,
            )

    total_resolved = counts["win"] + counts["loss"] + counts["expired"]
    if total_resolved:
        logger.info(
            "Outcome backfill: %d resolved (W%d L%d E%d), %d still open, %d no-ohlcv",
            total_resolved,
            counts["win"],
            counts["loss"],
            counts["expired"],
            counts["open"],
            counts["no_ohlcv"],
        )
    return counts
