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
  - "win"     — TP hit first. outcome_r = +rr_ratio
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


# Hold caps in BARS — never in calendar time. Each sits near the p85–p90 of the
# hold actually observed in `backtest_trades` (4,000-row sample, 2026-08-12:
# `4h` p50 3 / p90 27, `1d` p50 2 / p90 24.5), so the cap bites the tail rather
# than the body. The values are equity-measured and fine; it is the day-
# equivalents that were inherited from crypto and are wrong.
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
DEFAULT_MAX_HOLD_BARS: dict[str, int] = {
    "15m": 96,  # 96 bars — no live rows
    "1h": 48,  # 48 bars — no live rows
    "4h": 30,  # 30 bars = ~15 RTH trading days (~3 calendar weeks), NOT 5 days
    "1d": 14,  # 14 bars = 14 trading days (~20 calendar days), NOT 2 weeks
}


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
    """Decide outcome for one signal given pre-fetched OHLCV bars for its TF."""
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
        return "win", float(rr_ratio), int(t[tp_first])

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
                            "no_ohlcv": N}.  "open" means the row was inspected
    but the hold window hasn't elapsed yet — it stays NULL so the next cycle
    can retry. "no_ohlcv" means we found no candles after the signal bar.

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

    counts = {"win": 0, "loss": 0, "expired": 0, "open": 0, "no_ohlcv": 0}
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

        # NOTE: the fallback is the LARGEST entry, i.e. the most permissive
        # window, so an unlisted TF gets the loosest cap rather than the safest.
        # `1wk` is unlisted and would take the `15m` value — 96 bars = 96 WEEKS.
        # Latent only (no `1wk` row has ever reached this table and `1wk` is not
        # live), and picking a real value is calibration, so it is a user call
        # rather than a drive-by fix. Measured for whoever makes it: observed
        # `1wk` hold is p50 0 / p90 7 / p99 23 bars, max 30.
        max_hold = hold_map.get(tf, max(hold_map.values()))
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
