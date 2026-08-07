"""Signal gates: ADR consumption filter, per-strategy ADR exemption, EV gate."""

import logging
from collections.abc import Callable, Mapping

import pandas as pd

from analytics.backtest.engine import BacktestResult
from analytics.regime import Regime
from analytics.signal.types import SignalEvent
from analytics.signal_config import BacktestFilterConfig, BiasConfig, StrategyOverride
from analytics.store.backtest_cache import BacktestSnapshot
from analytics.strategies import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)


def passes_ev_gate(
    result: BacktestResult | BacktestSnapshot | None,
    *,
    direction: str,
    timeframe: str,
    backtest_cfg: BacktestFilterConfig,
) -> bool:
    """Return True when a signal survives the hard EV gate (i.e. is dispatched).

    Extracted from a closure inside ``run_scan_cycle`` on 2026-08-07. It was
    unreachable from a test while it lived there, so every "EV gate" test in
    ``tests/test_backtest_filter.py`` re-implemented the comparison inline and
    asserted on its own copy — which is why the defect below survived: one of
    those tests encoded it verbatim.

    **The sample-size guard counts the population the statistic is computed
    from.** Until 2026-08-07 it counted ``len(result.closed_trades)`` — BOTH
    directions — and then tested a DIRECTIONAL ``avg_r``, so a long verdict
    could rest entirely on short trades. Measured on the live path over the
    declared 365d window: 53 of 260 blocked legs on ``signal_watch`` (20%) and
    45 of 429 on ``weekdays`` (10%) had fewer trades in the tested direction
    than ``min_trades`` nominally requires; 19 and 68 respectively rested on a
    SINGLE directional trade, where dispersion is undefined. No value of
    ``min_trades`` fixes that — raising it to 10 still admits an n_dir=1 block
    whenever the opposite direction carries the count.

    This matters beyond alert volume: a blocked leg is dropped from
    ``passing_events`` in ``run_scan_cycle`` BEFORE the outcome writer runs, so
    it never reaches ``signal_alert_outcomes``. A wrong block does not merely
    silence an alert, it destroys the observation.

    The gate FAILS OPEN in three places here (no result, too few trades, no
    directional data) plus a fourth in the caller, which skips it entirely
    unless ``backtest_cfg.mode == "hard"``. Audit:
    ``docs/audits/2026-08-07-ev-gate-directional-sample-guard.md``.
    """
    if result is None:
        return True  # no data — don't suppress
    if direction == "long":
        avg_r = result.long_avg_r
        n_closed = len(result.long_closed_trades)
        threshold = (
            backtest_cfg.min_avg_r_long
            if backtest_cfg.min_avg_r_long is not None
            else backtest_cfg.min_avg_r
        )
    elif direction == "short":
        avg_r = result.short_avg_r
        n_closed = len(result.short_closed_trades)
        threshold = (
            backtest_cfg.min_avg_r_short
            if backtest_cfg.min_avg_r_short is not None
            else backtest_cfg.min_avg_r
        )
    else:
        avg_r = result.avg_r
        n_closed = len(result.closed_trades)
        threshold = backtest_cfg.min_avg_r
    if n_closed < backtest_cfg.effective_min_trades(timeframe):
        return True  # not enough trades IN THIS DIRECTION — noise
    if avg_r is None:
        return True  # no directional data — don't suppress
    return avg_r >= threshold


def _apply_conflict_resolver(
    events: list[SignalEvent],
    symbol: str,
    tf: str,
    *,
    confidence_resolver: Callable[[SignalEvent], float] | None = None,
) -> list[SignalEvent]:
    """Resolve opposing long-vs-short events on the same (symbol, tf) cycle.

    Operates on a flat list of SignalEvents assumed to belong to one "moment"
    — a live scan cycle in scanner.run_scan_cycle, or one candle's worth of
    events when callers group by `open_time` for backtest replay.

    Resolution:
      - Both directions present → higher max-confidence side wins; loser
        dropped; winner gets ``conflict=True``.
      - Tie → both sides kept; all events marked ``conflict=True``.
      - Single direction → returned unchanged.

    ``confidence_resolver`` lets callers swap in an alternative score reader
    (e.g. the backtest path injects a confidence_ratings-derived avg_r since
    live editorial confidence is not set during replay). Defaults to the
    event's own ``confidence`` field (int). Return type widened to float so
    backtest-replay can use the continuous avg_r from the confidence_ratings
    table; int callers (the live path) remain compatible.
    """
    if not events:
        return events
    long_events = [e for e in events if e.direction == "long"]
    short_events = [e for e in events if e.direction == "short"]
    if not (long_events and short_events):
        return long_events or short_events

    resolver: Callable[[SignalEvent], float] = (
        confidence_resolver
        if confidence_resolver is not None
        else (lambda e: float(e.confidence))
    )
    long_conf = max(resolver(e) for e in long_events)
    short_conf = max(resolver(e) for e in short_events)
    if long_conf > short_conf:
        winners = long_events
        logger.info(
            "Conflict: %s %s — LONG wins (conf %g > %g), SHORT dropped (%s)",
            symbol,
            tf,
            long_conf,
            short_conf,
            [e.strategy for e in short_events],
        )
    elif short_conf > long_conf:
        winners = short_events
        logger.info(
            "Conflict: %s %s — SHORT wins (conf %g > %g), LONG dropped (%s)",
            symbol,
            tf,
            short_conf,
            long_conf,
            [e.strategy for e in long_events],
        )
    else:
        winners = long_events + short_events
        logger.info(
            "Conflict tie: %s %s conf %g — sending both LONG (%s) and SHORT (%s)",
            symbol,
            tf,
            long_conf,
            [e.strategy for e in long_events],
            [e.strategy for e in short_events],
        )
    for e in winners:
        e.conflict = True
    return winners


# Timeframes whose bars span a whole calendar day or more. The ADR gate's
# premise — "cumulative intraday range UP TO this candle" — requires at least two
# bars per calendar day for that to be a *partial* quantity. When a day holds
# exactly one bar the cumulative range IS the whole day's range for every row, so
# the ratio stops measuring exhaustion and silently becomes a wide-range-bar
# filter. Its direction guard degenerates in the same step: `move_up` reduces to
# "close in the upper half of its own bar", which is the same quantity
# close-derived detectors (doji / ema / trend_day) read their direction from, so
# `chasing` is true by construction and the guard spares nothing.
# Measured 2026-08-06 — see docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md.
#
# This set classifies timeframes by bars-per-calendar-day arithmetic, which is a
# property of the interval itself — it is NOT a claim about what this fork
# ingests (`YF_INTERVALS` is 1h/1d/1wk, with 4h derived). Listing an interval
# here means "the gate would be meaningful on it", not "we scan it".
_ADR_INTRADAY_TIMEFRAMES = frozenset({"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"})


def adr_gate_applies(timeframe: str) -> bool:
    """Whether the ADR consumed-ratio gate is meaningful on ``timeframe``.

    True only for intraday timeframes, where a calendar day holds more than one
    bar and "range consumed so far today" is a genuinely partial quantity.
    Unknown timeframes default to False — a gate that silently means something
    other than its documentation is worse than one that is off.
    """
    return timeframe in _ADR_INTRADAY_TIMEFRAMES


def _filter_signals_by_adr(
    ohlcv_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    threshold: float,
    timeframe: str,
) -> pd.DataFrame:
    """Return signals where the ADR consumed at signal time is below threshold.

    For each signal candle, computes:
      consumed_ratio = (cumulative intraday range up to that candle) / (14-day ADR)

    Signals where consumed_ratio >= threshold are dropped — the daily move was
    already mostly done when the signal fired.  Signals whose candle is not found
    in ohlcv_df pass through untouched (safe-default: don't suppress unknown data).

    No-op on timeframes where a calendar day holds one bar (``1d`` / ``1wk``):
    there is no "up to that candle" to measure. ``timeframe`` is required rather
    than defaulted so mypy forces every call site to state it — a default would
    let a new caller silently re-acquire the degenerate behaviour.
    """
    if signals_df.empty or ohlcv_df.empty:
        return signals_df
    if not adr_gate_applies(timeframe):
        return signals_df

    df = ohlcv_df.copy()
    df["_date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.date
    df = df.sort_values("open_time")

    # Day open = first candle's open price within each calendar day
    day_opens: pd.Series = df.groupby("_date")["open"].first()

    # Cumulative intraday high/low up to each candle (inclusive)
    df["_cum_high"] = df.groupby("_date")["high"].cummax()
    df["_cum_low"] = df.groupby("_date")["low"].cummin()
    df["_day_open"] = df["_date"].map(day_opens)

    # Today's range as a fraction of day_open (avoid div/0)
    df["_today_range"] = (df["_cum_high"] - df["_cum_low"]) / df["_day_open"].where(
        df["_day_open"] > 0
    )

    # 14-day rolling ADR from daily extremes
    daily = (
        df.groupby("_date")
        .agg(_dh=("high", "max"), _dl=("low", "min"), _do=("open", "first"))
        .sort_index()
    )
    daily["_dr"] = (daily["_dh"] - daily["_dl"]) / daily["_do"].where(daily["_do"] > 0)
    daily["_adr14"] = daily["_dr"].rolling(14, min_periods=1).mean()

    df["_adr14"] = df["_date"].map(daily["_adr14"])

    # Consumed ratio at each candle; NaN when adr_14 is zero or unknown
    df["_consumed"] = df["_today_range"] / df["_adr14"].where(df["_adr14"] > 0)

    # Direction: close in upper half of today's range → move was upward
    df["_mid"] = (df["_cum_high"] + df["_cum_low"]) / 2
    df["_move_up"] = (df["close"] > df["_mid"]).astype(float)

    consumed_map: dict[int, float] = dict(
        zip(df["open_time"].astype(int), df["_consumed"].astype(float), strict=False)
    )
    move_up_map: dict[int, float] = dict(
        zip(df["open_time"].astype(int), df["_move_up"], strict=False)
    )

    signal_ratios = signals_df["open_time"].astype(int).map(consumed_map)
    signal_move_up = signals_df["open_time"].astype(int).map(move_up_map)

    # Suppress only the chasing direction: LONGs when move was up, SHORTs when down.
    # NaN move_up (candle not found) → neither condition fires → safe pass-through.
    chasing = ((signal_move_up == 1.0) & (signals_df["direction"] == "long")) | (
        (signal_move_up == 0.0) & (signals_df["direction"] == "short")
    )
    keep = signal_ratios.isna() | (signal_ratios < threshold) | ~chasing
    return signals_df[keep].reset_index(drop=True)


def _is_adr_exempt(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    direction: str | None = None,
) -> bool:
    """Resolve adr_exempt for ``(strategy, direction)``.

    Wifey has only a strategy-wide ``adr_exempt`` flag — per-direction
    overrides (parent's ``adr_exempt_long`` / ``adr_exempt_short``) were not
    ported. ``direction`` is accepted for parity with the parent-side caller
    pattern; both directions resolve to the strategy-wide value.
    """
    del direction  # both directions inherit the strategy-wide flag
    if not strategy_params:
        return False
    override = strategy_params.get(strategy)
    return override.adr_exempt if override is not None else False


def _apply_htf_ema_gate(
    events: list[SignalEvent],
    bias_cfg: BiasConfig,
    htf_slope_cache: Mapping[tuple[str, str, int, int], float | None],
    symbol: str,
    tf: str,
) -> list[SignalEvent]:
    """F8 directional gate — suppress signals opposing the HTF EMA slope.

    Per-strategy anchor resolved via bias_cfg.htf_ema_anchor(strategy).
    Slope must be looked up in htf_slope_cache (pre-computed once per scan cycle).

    Behaviour:
      - slope is None (warmup / missing data) → allow.
      - |slope| < deadband_pct → allow (HTF flat, no opinion).
      - opposing direction in hard mode → drop.
      - opposing direction in soft mode → log and keep.

    Returns the (possibly filtered) event list. Never raises.
    """
    if not bias_cfg.htf_ema_enabled or not events:
        return events

    hard = bias_cfg.htf_ema_mode == "hard"
    deadband = bias_cfg.htf_ema_deadband_pct
    kept: list[SignalEvent] = []
    suppressed = 0
    for event in events:
        anchor = bias_cfg.htf_ema_anchor(event.strategy)
        slope = htf_slope_cache.get(
            (symbol, anchor.tf, anchor.period, anchor.slope_lookback)
        )
        if slope is None or abs(slope) < deadband:
            kept.append(event)
            continue
        opposing = (slope > 0 and event.direction == "short") or (
            slope < 0 and event.direction == "long"
        )
        if not opposing:
            kept.append(event)
            continue
        logger.info(
            "F8 HTF EMA gate %s: %s %s %s %s — %s EMA-%d slope=%+.4f opposes",
            "dropped" if hard else "soft-flagged",
            symbol,
            tf,
            event.strategy,
            event.direction,
            anchor.tf,
            anchor.period,
            slope,
        )
        if hard:
            suppressed += 1
            continue
        kept.append(event)
    if suppressed and hard:
        logger.info(
            "F8 HTF EMA gate removed %d signal(s) for %s %s",
            suppressed,
            symbol,
            tf,
        )
    return kept


def _apply_direction_filter_gate(
    events: list[SignalEvent],
    bias_cfg: BiasConfig,
    strategy_params: dict[str, StrategyOverride] | None,
    symbol: str,
    tf: str,
) -> list[SignalEvent]:
    """T2c per-strategy directional suppress gate (Step −0.5 of bias chain).

    Drops signals whose direction is suppressed for that strategy via
    StrategyOverride.suppress_long / .suppress_short. Cheapest filter in the
    chain — no HTF data, no cache lookups, pure per-event flag check.

    Behaviour:
      - gate disabled → allow all.
      - no per-strategy override → allow (defensive: unknown strategy falls open).
      - direction not suppressed → allow.
      - direction suppressed + hard mode → drop and log.
      - direction suppressed + soft mode → log and keep.

    Returns the (possibly filtered) event list. Never raises.
    """
    if not bias_cfg.direction_filter_enabled or not events:
        return events
    if not strategy_params:
        return events

    hard = bias_cfg.direction_filter_mode == "hard"
    kept: list[SignalEvent] = []
    suppressed = 0
    for event in events:
        override = strategy_params.get(event.strategy)
        if override is None:
            kept.append(event)
            continue
        is_suppressed = (event.direction == "long" and override.suppress_long) or (
            event.direction == "short" and override.suppress_short
        )
        if not is_suppressed:
            kept.append(event)
            continue
        logger.info(
            "Direction filter %s: %s %s %s %s",
            "dropped" if hard else "soft-flagged",
            symbol,
            tf,
            event.strategy,
            event.direction,
        )
        if hard:
            suppressed += 1
            continue
        kept.append(event)
    if suppressed and hard:
        logger.info(
            "Direction filter removed %d signal(s) for %s %s",
            suppressed,
            symbol,
            tf,
        )
    return kept


def _apply_regime_gate(
    events: list[SignalEvent],
    bias_cfg: BiasConfig,
    regime_cache: Mapping[str, Regime],
    symbol: str,
    tf: str,
) -> list[SignalEvent]:
    """v2 Phase 2 regime gate — drop signals not enabled in the current regime.

    Per redesign §6 (docs/redesign/buibui-redesign.md). Resolution per event:
      1. Look up current regime in regime_cache[symbol].
      2. Cache miss or regime == "unknown" → allow.
      3. Resolve allowed-regime list via bias_cfg.regime_allowed(strategy, type, regime).
      4. Allowed → keep. Not allowed → drop in hard mode, log+keep in soft mode.

    Returns the (possibly filtered) event list. Never raises.
    """
    if not bias_cfg.regime_enabled or not events:
        return events

    regime = regime_cache.get(symbol)
    if regime is None or regime == "unknown":
        return events

    hard = bias_cfg.regime_mode == "hard"
    kept: list[SignalEvent] = []
    suppressed = 0
    for event in events:
        spec = STRATEGY_REGISTRY.get(event.strategy)
        # Unknown strategy (not in registry) → fall open. Defensive: a freshly
        # added detector should not be silently dropped before TOML is updated.
        strategy_type = spec.strategy_type if spec is not None else ""
        if bias_cfg.regime_allowed(event.strategy, strategy_type, regime):
            kept.append(event)
            continue
        logger.info(
            "Regime gate %s: %s %s %s %s — type=%s regime=%s",
            "dropped" if hard else "soft-flagged",
            symbol,
            tf,
            event.strategy,
            event.direction,
            strategy_type or "?",
            regime,
        )
        if hard:
            suppressed += 1
            continue
        kept.append(event)
    if suppressed and hard:
        logger.info(
            "Regime gate removed %d signal(s) for %s %s (regime=%s)",
            suppressed,
            symbol,
            tf,
            regime,
        )
    return kept
