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

Outcomes (every resolved `outcome_r` below is NET of costs — see next block):
  - "win"     — TP hit first. gross = +the R implied by `tp_price`
                (`implied_tp_r`), NOT the declared `rr_ratio`.
  - "loss"    — SL hit first or same-bar tie. gross = -1.0
  - "expired" — exceeded `max_hold_bars` without hitting either.
                gross = mark-to-market at the last in-window bar.
  - (NULL)    — still within hold window; retry on the next cycle.

COSTS. `outcome_r` is `gross_r - cost_r` and `outcome_cost_r` stores the drag,
so the gross figure stays recoverable as `outcome_r + outcome_cost_r`. Charging
here is what makes the live ledger comparable to a backtest at all: `run_backtest`
has always charged its trades, so an uncharged ledger biased every
backtest-vs-live comparison IN FAVOUR OF LIVE — the opposite of the direction one
assumes when a live book underperforms its backtest.

`live_cost_r` mirrors `engine.Trade.pnl_r`'s two branches EXACTLY, and mirroring
rather than inventing is the point: a third cost basis would not have fixed the
comparability gap, it would have added one. So when a `CostModel` is set it
prices the decomposed equity stack (spread + impact + borrow + commission) and
`fee_pct` is ignored, precisely as `pnl_r` does; otherwise the flat per-leg
`fee_pct` path applies.

With NEITHER configured the row is left UNPRICED: `outcome_r` keeps its gross
value and `outcome_cost_r` stays NULL. NULL and 0.0 are kept distinct on purpose —
0.0 would claim "priced, and it cost nothing", and it would also hide the row from
migration 004, whose entire guard is `outcome_cost_r IS NULL`. That default is
also what keeps every pre-existing test honest.

⚠ **Cost was an ASYMMETRY between the books; it is not the largest error in
either.** A gap THROUGH the stop still books exactly -1.0R here, because the
resolver reads levels rather than fills — and `engine.py` does the same thing
(`trade.exit_price = sl_price`), so that absence is SHARED and does NOT bias the
comparison the way uncharged costs did. It does mean both books overstate: on the
2026-08-19 ledger 21.1% of losses (46 of 218) gapped through their stop, worth
about -0.10R per resolved row against this cost charge's -0.014R, i.e. ~7x
larger. Charging costs makes the two books comparable; it does not make either
of them right, and only the first of those is claimed here.

Pure function over conn + now_ms + config dict; no clock or network I/O.
"""

import logging
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from analytics.backtest.cost_model import (
    CostContext,
    CostModel,
    bars_per_day_for_tf,
    build_cost_context,
)
from analytics.backtest.fills import (
    gap_fill_price,
    level_is_on_the_expected_side,
)
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


def implied_tp_r(
    *,
    direction: str,
    entry: float,
    sl_price: float,
    rr_ratio: float,
    tp_price: float | None,
) -> float:
    """The R multiple the alert's TP was ACTUALLY at — not the declared one.

    NAMED `implied_tp_r`, NOT `effective_tp_r`, and the distinction is the whole
    subject of this function. `SignalWatchConfig.effective_tp_r` already exists
    and means the opposite thing: the *configured* `tp_r` after the
    symbol/TF/direction override chain resolves — i.e. the DECLARED side. Its
    output is what arrives here as `rr_ratio`. A shared name would put
    `effective_tp_r(rr_ratio=cfg.effective_tp_r(...))` in `scanner.py`, which
    reads as a tautology and hides exactly the gap this exists to close.

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
    if not level_is_on_the_expected_side(
        level=tp_price, entry=entry, direction=direction, side="target"
    ):
        return rr_ratio
    return (
        (tp_price - entry) / risk if direction == "long" else (entry - tp_price) / risk
    )


@dataclass
class _LiveTrade:
    """A resolved live alert viewed as `cost_model.TradeLike`.

    NOT frozen, and that is forced rather than chosen: `TradeLike` is a plain
    Protocol, so its members are settable variables and a frozen dataclass's
    read-only attributes do not satisfy it. Each instance is built, priced and
    discarded inside one loop iteration, so nothing shares it.

    The cost model is engine-free by design — it consumes a structural protocol
    rather than `engine.Trade` — so the live ledger can be priced by the SAME
    object the backtest uses instead of by a re-implementation. That is the
    whole reason this adapter is five fields and no logic.
    """

    direction: str
    entry_price: float
    sl_price: float
    entry_time: int
    exit_time: int | None


def cost_window_bars(cost_model: CostModel, timeframe: str) -> int:
    """Trailing bars needed to estimate ADV/sigma for `timeframe`.

    Mirrors the engine's own window derivation so the live ledger and the
    backtest measure liquidity over the same span. Read the count as BARS: on
    RTH `4h` a trading day holds TWO bars, not six, so a 20-day ADV window is
    40 bars here and 20 on `1d`.
    """
    return max(2, round(cost_model.adv_window_days * bars_per_day_for_tf(timeframe)))


def cost_context_at(
    bars: pd.DataFrame, candle_ts_ms: int, timeframe: str, cost_model: CostModel
) -> CostContext | None:
    """Causal ADV/sigma context ending at the signal bar, or None if unreachable.

    `bars` must extend BACK from the signal bar by at least `cost_window_bars`;
    the caller widens its fetch for exactly this reason. Returning None when the
    signal bar is not in `bars` is deliberate and conservative — `cost_breakdown`
    then falls back to the widest spread bucket and zero impact, i.e. it
    OVERCHARGES rather than quietly charging nothing. A missing context must
    never be able to look like a free trade.
    """
    open_times = bars["open_time"].to_numpy()
    idxs = np.nonzero(open_times == candle_ts_ms)[0]
    if not len(idxs):
        return None
    return build_cost_context(
        bars["close"].to_numpy(dtype=float),
        bars["volume"].to_numpy(dtype=float),
        int(idxs[0]),
        bars_per_day_for_tf(timeframe),
        cost_window_bars(cost_model, timeframe),
    )


def live_cost_r(
    *,
    direction: str,
    entry_price: float,
    sl_price: float,
    entry_time_ms: int,
    exit_time_ms: int | None,
    cost_model: CostModel | None = None,
    fee_pct: float = 0.0,
    ctx: CostContext | None = None,
) -> float:
    """Cost of one resolved live alert, in R. Non-negative; 0.0 when unpriced.

    THE single definition of live cost, shared by `backfill_outcomes` (which
    charges it at resolution time) and `migrations/004_live_ledger_net_of_cost.py`
    (which charges it retroactively). Sharing it is not tidiness — the two
    surfaces must agree exactly or the restatement creates the very basis
    boundary it exists to remove, which is how the parent's own port ended up
    with two permanent bases.

    Branches mirror `engine.Trade.pnl_r`: a `CostModel` prices the decomposed
    stack and IGNORES `fee_pct`; otherwise the flat per-leg `fee_pct` applies.
    Zero risk returns 0.0, matching the model's own guard — cost in R is
    undefined when nothing is risked.
    """
    risk = abs(entry_price - sl_price)
    if risk <= 0.0:
        return 0.0
    if cost_model is not None:
        return cost_model.cost_r(
            _LiveTrade(
                direction=direction,
                entry_price=entry_price,
                sl_price=sl_price,
                entry_time=entry_time_ms,
                exit_time=exit_time_ms,
            ),
            ctx,
        )
    return 2.0 * fee_pct * entry_price / risk


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
    fallback inside `implied_tp_r` for rows whose `tp_price` is unusable.
    """
    post = bars[bars["open_time"] > candle_ts_ms].reset_index(drop=True)
    if post.empty:
        return None, None, None

    window = post.iloc[:max_hold_bars]
    h = window["high"].to_numpy()
    lo = window["low"].to_numpy()
    o = window["open"].to_numpy()
    t = window["open_time"].to_numpy()
    sl_dist = abs(entry - sl_price)

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

    # A bar that OPENS beyond the level fills there. Both branches stay
    # byte-identical to the pre-2026-08-20 behaviour when the bar did NOT gap,
    # so the rr_ratio fallback inside `implied_tp_r` is preserved untouched for
    # every non-gapped win. Mirrors engine.py exactly — see fills.py.
    if sl_first <= tp_first and sl_first < len(t):
        realized = -1.0
        fill = gap_fill_price(
            level=sl_price,
            bar_open=float(o[sl_first]),
            direction=direction,
            side="stop",
        )
        if (
            fill != sl_price
            and sl_dist > 0
            and level_is_on_the_expected_side(
                level=sl_price, entry=entry, direction=direction, side="stop"
            )
        ):
            realized = (fill - entry) / sl_dist * sign
        return "loss", float(realized), int(t[sl_first])
    if tp_first < len(t):
        credited = implied_tp_r(
            direction=direction,
            entry=entry,
            sl_price=sl_price,
            rr_ratio=rr_ratio,
            tp_price=tp_price,
        )
        fill = gap_fill_price(
            level=tp_price,
            bar_open=float(o[tp_first]),
            direction=direction,
            side="target",
        )
        if (
            fill != tp_price
            and sl_dist > 0
            and level_is_on_the_expected_side(
                level=tp_price, entry=entry, direction=direction, side="target"
            )
        ):
            credited = (fill - entry) / sl_dist * sign
        return "win", float(credited), int(t[tp_first])

    # Neither hit within the window so far.
    if len(window) < max_hold_bars:
        return None, None, None

    last_close = float(window["close"].iloc[-1])
    mtm_r = (last_close - entry) / sl_dist * sign if sl_dist > 0 else 0.0
    return "expired", float(mtm_r), int(t[-1])


def backfill_outcomes(
    conn: duckdb.DuckDBPyConnection,
    now_ms: int,
    max_hold_bars_by_tf: dict[str, int] | None = None,
    cost_model: CostModel | None = None,
    fee_pct: float = 0.0,
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

    `cost_model` / `fee_pct` come from the live `[backtest]` config so the ledger
    is charged exactly what a backtest of the same signal would be (see
    `live_cost_r`). BOTH DEFAULT TO NO COST, which reproduces the pre-2026-08-19
    gross behaviour — the default is deliberate, because a resolver that invents
    a cost basis when its caller supplies none would put a second basis in the
    same column. `signal_runner` passes both from `BacktestFilterConfig`.
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
        walk_start_ms = earliest_candle + tf_secs * 1000
        # A cost model needs history the forward walk never looks at: ADV and
        # sigma are TRAILING statistics ending at the signal bar. So widen the
        # fetch backwards, then slice `bars` back to exactly the old frame.
        # The slice is the load-bearing half — without it a wider fetch would
        # make `bars.empty` false for a signal with no bars after it, silently
        # moving those rows out of `no_ohlcv` and into `open`. Widening a fetch
        # must not be able to change what a count means.
        fetch_start_ms = walk_start_ms
        if cost_model is not None:
            fetch_start_ms = (
                earliest_candle - cost_window_bars(cost_model, tf) * tf_secs * 1000
            )
        bars_with_history = get_ohlcv(
            conn, symbol, tf, fetch_start_ms, last_closed_open_ms
        )
        bars = bars_with_history[
            bars_with_history["open_time"] >= walk_start_ms
        ].reset_index(drop=True)
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

        updates: list[tuple[str, float, float | None, int, str]] = []
        # NULL vs 0.0 in `outcome_cost_r` is a real distinction, not a nicety:
        # NULL means UNPRICED (the caller configured no cost basis) while 0.0
        # would mean "priced, and it cost nothing". Collapsing them would let a
        # row nobody gave a basis for read as a measured zero, and would hide it
        # from migration 004, whose entire guard is `outcome_cost_r IS NULL`.
        priced = cost_model is not None or fee_pct > 0.0

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
            # Entry is taken when the signal bar CLOSES, which is the next bar's
            # open — the same instant the engine enters. Passing candle_ts_ms
            # itself would date the entry one bar early and understate borrow.
            cost_r = (
                live_cost_r(
                    direction=str(direction),
                    entry_price=float(entry_price),
                    sl_price=float(sl_price),
                    entry_time_ms=int(candle_ts_ms) + tf_secs * 1000,
                    exit_time_ms=filled_at,
                    cost_model=cost_model,
                    fee_pct=fee_pct,
                    ctx=(
                        cost_context_at(
                            bars_with_history, int(candle_ts_ms), tf, cost_model
                        )
                        if cost_model is not None
                        else None
                    ),
                )
                if priced
                else None
            )
            updates.append(
                (
                    outcome,
                    outcome_r - cost_r if cost_r is not None else outcome_r,
                    cost_r,
                    filled_at,
                    str(signal_id),
                )
            )

        if updates:
            conn.executemany(
                "UPDATE signal_alert_outcomes "
                "SET outcome = ?, outcome_r = ?, outcome_cost_r = ?, "
                "outcome_filled_at_ms = ? "
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
