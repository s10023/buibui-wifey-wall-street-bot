"""Scanner — fan-out over symbols/timeframes.

scan_symbol(): runs requested strategies against a pre-fetched OHLCV DataFrame,
               returning SignalEvents only for the latest candle.
run_scan_cycle(): fans out across all symbols/timeframes, pre-fetches OHLCV once
                  per (symbol, timeframe), deduplicates via CooldownStore,
                  formats alerts, and optionally sends Telegram.
No module-level side effects.
"""

import datetime
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import duckdb
import pandas as pd

from analytics.backtest_lib import (
    BacktestResult,
    _is_low_volume,
    _is_volume_spike,
)
from analytics.data_store import (
    BacktestSnapshot,
    _backtest_run_id,
    _make_bt_cache_key,
    get_backtest_cache,
    get_ohlcv,
    put_backtest_cache,
    upsert_backtest_run,
    upsert_signal_outcome,
    upsert_signals,
)
from analytics.overnight_gap_lib import gap_fill_warning, get_overnight_gap
from analytics.regime import Regime, classify_series
from analytics.signal._common import (
    _SCAN_WINDOW,
    _bt_mem_cache,
    parse_timeframe_secs,
)
from analytics.signal.atr_floor import _apply_atr_floor
from analytics.signal.bt_cache import _backtest_summary, _compute_backtest
from analytics.signal.cofire import (
    _find_cross_tf_cofire,
    _find_live_cofire,
)
from analytics.signal.gates import (
    _apply_conflict_resolver,
    _apply_direction_filter_gate,
    _apply_htf_ema_gate,
    _apply_regime_gate,
    _is_adr_exempt,
    effective_adr_threshold,
    passes_ev_gate,
)
from analytics.signal.outcome_backfill import implied_tp_r
from analytics.signal.resolvers import (
    _resolve_atr_sl_floor,
    _resolve_atr_sl_multiplier,
    _resolve_sl_pct,
    _resolve_tp_r,
    _resolve_volume_spike_boost,
    _resolve_volume_spike_boost_long,
    _resolve_volume_spike_boost_short,
    _resolve_volume_suppress,
    _resolve_volume_suppress_long,
    _resolve_volume_suppress_short,
)
from analytics.signal.stats_context import _compute_stats_context
from analytics.signal.types import ConfluenceData, SignalEvent, StatsContext
from analytics.signal_config import (
    BacktestFilterConfig,
    BiasConfig,
    StrategyOverride,
    _day_filter_to_weekdays,
)
from analytics.strategies import STRATEGY_REGISTRY, compute_htf_ema_slope
from signals.cooldown_store import CooldownStore
from signals.registry import SIGNAL_REGISTRY
from utils.config_validation import load_universe_policy

logger = logging.getLogger(__name__)


def _resolve_outcome_sl_tp(
    *,
    direction: str,
    entry: float,
    struct_sl: float,
    struct_tp: float,
    eff_sl_pct: float,
    min_sl_pct: float,
    tp_r: float,
) -> tuple[float, float]:
    """Return (sl_price, tp_price) for an outcome-ledger row.

    Mirrors the alert formatter's fallback (``signals/alert_formatter.py``):
    use the event's structural SL when it sits on the correct side of ``entry``,
    otherwise fall back to ``entry*(1∓eff_sl_pct)``. The SL distance is floored
    by ``entry*min_sl_pct``; the structural TP is preferred over
    ``entry ± sl_dist*tp_r`` when valid. Always returns concrete floats so every
    fired event is scoreable.
    """
    if direction == "long":
        structural = struct_sl if 0 < struct_sl < entry else entry * (1 - eff_sl_pct)
        sl_dist = max(entry - structural, entry * min_sl_pct)
        sl_price = entry - sl_dist
        tp_price = struct_tp if struct_tp > entry else entry + sl_dist * tp_r
    else:  # short
        structural = struct_sl if struct_sl > entry else entry * (1 + eff_sl_pct)
        sl_dist = max(structural - entry, entry * min_sl_pct)
        sl_price = entry + sl_dist
        tp_price = struct_tp if 0 < struct_tp < entry else entry - sl_dist * tp_r
    return sl_price, tp_price


def scan_symbol(
    ohlcv_df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategies: list[str],
    funding_df: pd.DataFrame | None = None,
    day_filter: str = "off",
    strategy_timeframes: dict[str, list[str]] | None = None,
    confidence_override: dict[str, dict[str, int]] | None = None,
    directional_confidence_override: dict[str, dict[str, dict[str, int]]] | None = None,
    catch_up: bool = False,
) -> list[SignalEvent]:
    """Run requested strategies against a pre-fetched OHLCV DataFrame.

    Returns SignalEvents whose open_time matches the latest candle in the data.
    Only the latest candle is checked — signals on older candles are ignored
    to prevent re-alerting on historical data after a restart.

    When catch_up is True, emits an event for *every* closed candle in the
    window (each carrying its own candle close as the entry price), not just the
    latest. The live candle watermark in run_scan_cycle then drops the candles
    already alerted on a prior run, so a skipped run-day's signals are replayed
    instead of permanently lost. Still excludes the forming final bar.

    When day_filter is "tue_thu", signals whose open_time falls on Monday (weekday 0)
    or Friday (weekday 4) in UTC are suppressed (ICT weekly cycle — lower-quality
    manipulation/distribution days). "weekdays" suppresses weekends only. "off" disables.

    strategy_timeframes: optional per-strategy TF allow-list loaded from
    [strategy_timeframes] in signal_watch.toml.  If a strategy appears in this
    mapping, it is only run when the current timeframe is in its allowed list.
    Strategies not listed run on all timeframes (no restriction).
    """
    if ohlcv_df.empty or len(ohlcv_df) < 3:
        return []

    # Exclude the final bar ONLY when it is still forming, so detectors fire on
    # the latest *closed* candle. A forming bar has as little as a few seconds
    # of data and would produce spurious 100%-body readings (trend_day,
    # marubozu, engulfing, …). A bar is still forming until its period has fully
    # elapsed: now < open_time + timeframe.
    #
    # Binance streams a forming candle 24/7, so the old code dropped the last
    # row unconditionally. yfinance equities are different: scanned pre-market /
    # after-close (or after a NaN forming bar is dropped), the last row is
    # already a closed bar — dropping it unconditionally fires one candle stale.
    tf_ms = parse_timeframe_secs(timeframe) * 1000
    now_ms = int(time.time() * 1000)
    last_open_time = int(ohlcv_df["open_time"].iloc[-1])
    # Forming → drop the final bar; closed → keep it.
    is_forming = now_ms < last_open_time + tf_ms
    closed_df = ohlcv_df.iloc[:-1] if is_forming else ohlcv_df
    latest_open_time = int(closed_df["open_time"].iloc[-1])
    latest_close = float(closed_df["close"].iloc[-1])

    # Catch-up: map each closed candle's open_time → its own close so a
    # back-filled event carries that candle's entry price (not the latest).
    close_by_ot: dict[int, float] = {}
    if catch_up:
        close_by_ot = {
            int(t): float(c)
            for t, c in zip(closed_df["open_time"], closed_df["close"], strict=False)
        }

    events: list[SignalEvent] = []

    _excluded_from_registry = {"seasonality"}

    for strategy_name in strategies:
        plugin = SIGNAL_REGISTRY.get(strategy_name)
        if plugin is None:
            if strategy_name not in _excluded_from_registry:
                logger.warning("Unknown strategy %s — skipping", strategy_name)
            continue

        spec = STRATEGY_REGISTRY.get(strategy_name)
        requires_funding = spec.requires_funding if spec else False

        # Per-strategy timeframe allow-list from TOML [strategy_timeframes].
        # If the strategy is listed, skip it when the current TF is not allowed.
        if strategy_timeframes:
            allowed_tfs = strategy_timeframes.get(strategy_name)
            if allowed_tfs is not None and timeframe not in allowed_tfs:
                logger.debug(
                    "Skipping %s for %s %s — not in allowed TFs %s",
                    strategy_name,
                    symbol,
                    timeframe,
                    allowed_tfs,
                )
                continue

        try:
            if requires_funding:
                if funding_df is None or funding_df.empty:
                    logger.debug(
                        "Skipping %s for %s — no funding data", strategy_name, symbol
                    )
                    continue
                signals_df = plugin["detector"](closed_df, funding_df)
            else:
                signals_df = plugin["detector"](closed_df)
        except Exception:
            logger.exception(
                "Detector %s raised for %s %s", strategy_name, symbol, timeframe
            )
            continue

        if signals_df.empty:
            continue

        # Catch-up emits every closed candle (≤ latest_open_time excludes any
        # forming bar); the default path emits only the latest closed candle.
        if catch_up:
            emit_signals = signals_df[signals_df["open_time"] <= latest_open_time]
        else:
            emit_signals = signals_df[signals_df["open_time"] == latest_open_time]
        for _, row in emit_signals.iterrows():
            row_ot = int(row["open_time"])
            row_price = (
                latest_close if row_ot == latest_open_time else close_by_ot[row_ot]
            )
            events.append(
                SignalEvent(
                    symbol=symbol,
                    timeframe=timeframe,
                    strategy=strategy_name,
                    direction=str(row["direction"]),
                    reason=str(row["reason"]),
                    open_time=row_ot,
                    price=row_price,
                    sl_price=float(row["sl_price"]),
                    tp_price=float(row["tp_price"]) if row.get("tp_price") else 0.0,
                    context=str(row["context"]),
                    confidence=(
                        (directional_confidence_override or {})
                        .get(strategy_name, {})
                        .get(timeframe, {})
                        .get(str(row["direction"]))
                        or (confidence_override or {})
                        .get(strategy_name, {})
                        .get(timeframe)
                        or STRATEGY_REGISTRY[strategy_name].get_confidence(timeframe)
                    ),
                    low_volume=bool(row.get("low_volume", False)),
                )
            )

    allowed_weekdays = _day_filter_to_weekdays(day_filter)
    if allowed_weekdays is not None and events:
        filtered: list[SignalEvent] = []
        for event in events:
            weekday = datetime.datetime.fromtimestamp(
                event.open_time / 1000, tz=datetime.UTC
            ).weekday()
            if weekday not in allowed_weekdays:
                logger.debug(
                    "Day filter suppressed %s %s %s (weekday %d)",
                    event.symbol,
                    event.timeframe,
                    event.strategy,
                    weekday,
                )
            else:
                filtered.append(event)
        return filtered

    return events


def may_dispatch_candle(
    open_time: int,
    latest_closed: int,
    tf_ms: int,
    now_ms: int,
    max_alert_age_hours: float,
) -> bool:
    """Whether a closed candle may reach Telegram, or is ledger-only backfill.

    The newest closed candle always dispatches. An older one dispatches only
    while its CLOSE is within ``max_alert_age_hours`` of ``now_ms``.

    At ``0.0`` nothing extra is admitted, so the rule collapses to
    strictly-newest — the behaviour every caller had before this parameter
    existed, which is why that is the library default rather than the shipped
    one (``config/strategy_params.toml`` opts the live path in).

    Why a window rather than the strict rule: on an RTH tape with one pre-open
    run a day, the session's FIRST 4h bar can never be the newest closed candle,
    so it was structurally undeliverable — 120 of 351 ledger candles, 34%, all
    of them the 13:30 UTC bar. The cut was arbitrary rather than principled: at
    a Wed 12:50 run the dropped 13:30 bar is 19.3h stale against the 15.3h bar
    that dispatched happily. Measured 2026-08-25; see
    ``docs/audits/2026-08-25-dispatch-recency-window.md``.

    This widens what may dispatch, never what is replayed. A candle whose
    watermark a previous backfill already consumed stays consumed, so raising
    the window cannot re-send history — it only changes the verdict for candles
    this cycle is seeing for the first time.
    """
    if open_time == latest_closed:
        return True
    if max_alert_age_hours <= 0:
        return False
    age_ms = now_ms - (open_time + tf_ms)
    return 0 <= age_ms <= max_alert_age_hours * 3_600_000


def run_scan_cycle(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    timeframes: list[str],
    strategies: list[str],
    store: CooldownStore,
    tp_r: float = 2.0,
    sl_pct: float = 0.02,
    min_sl_pct: float = 0.0,
    send_telegram: bool = False,
    days: int = 90,
    backtest_cfg: BacktestFilterConfig | None = None,
    day_filter: str = "off",
    strategy_timeframes: dict[str, list[str]] | None = None,
    strategy_params: dict[str, StrategyOverride] | None = None,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    confidence_override: dict[str, dict[str, int]] | None = None,
    directional_confidence_override: dict[str, dict[str, dict[str, int]]] | None = None,
    bias_cfg: BiasConfig | None = None,
    combo_lookup: "dict[tuple[str, str, frozenset[str]], Any] | None" = None,
    combo_window: int = 5,
    combo_min_avg_r: float = 1.0,
    cross_tf_lookup: "dict[tuple[str, str, str, str, str], Any] | None" = None,
    cross_tf_pairs: list[tuple[str, str]] | None = None,
    cross_tf_window_hours: float = 4.0,
    cross_tf_min_avg_r: float = 1.0,
    ohlcv_cache: "dict[tuple[str, str], pd.DataFrame] | None" = None,
    catch_up: bool = False,
    max_alert_age_hours: float = 0.0,
) -> list[str]:
    """Scan all symbol+timeframe combinations and return formatted alert strings.

    Pre-fetches OHLCV once per (symbol, timeframe) and passes the DataFrame into
    scan_symbol, avoiding redundant DB reads across strategies.
    Uses CooldownStore to suppress duplicate alerts. Optionally sends via Telegram.
    Returns list of formatted alert strings for logging/testing regardless of
    whether Telegram is enabled.

    day_filter: "off" | "weekdays" | "tue_thu" — suppress signals by weekday.
    strategy_timeframes: optional per-strategy TF allow-list from [strategy_timeframes] TOML.
    catch_up: when True, replay every un-alerted closed candle since the last run
    (not just the latest). scan_symbol emits multi-candle signals and each candle
    is processed as its own group so conflict resolution / confluence stacking stay
    per-candle correct. Backfilled candles are RECORDED — DB signals +
    outcome-ledger rows + watermark — but never dispatched to Telegram: a stale
    signal is untradeable noise in the chat, yet real ledger evidence.
    max_alert_age_hours: how far back of the newest closed candle (read from
    OHLCV) may still dispatch, measured from each candle's CLOSE. 0.0 = the
    newest closed candle alone, the strict latest-candle rule. See
    may_dispatch_candle for why a window rather than a point. A cold-start guard (no prior
    watermark for a key) restricts the first run to the latest candle so the
    window is not replayed as a burst. Recovery depth is bounded by the
    200-candle scan window (4h ~33 days, 1d ~200 days, 1wk ~4 years).
    Note: regime / HTF-EMA / ADR / DOW bias context is computed as-of-now and
    applied to historical candles too — a deliberate best-effort approximation for
    a few missed days, not a full as-of-candle replay (see the backtest live-parity
    path for that). A deep backfill is look-ahead in the *gating* and should be
    read as backtest output, not clean out-of-sample evidence.
    """
    from signals.alert_formatter import (
        format_confluence_alert,
        format_wife_confluence_alert,
    )
    from utils.telegram_router import dispatch_to_channel

    now_ms = int(time.time() * 1000)
    if backtest_cfg and backtest_cfg.since:
        import datetime as _dt

        start_ms = int(
            _dt.datetime.strptime(backtest_cfg.since, "%Y-%m-%d")
            .replace(tzinfo=_dt.UTC)
            .timestamp()
            * 1000
        )
    else:
        start_ms = now_ms - days * 24 * 3600 * 1000

    # Freshly computed BacktestResult objects this cycle, keyed by (symbol, tf, strategy).
    # Cache hits (BacktestSnapshot) are excluded — only full BacktestResult objects
    # can be persisted to backtest_runs at end of cycle.
    bt_to_save: dict[tuple[str, str, str], BacktestResult | None] = {}

    # Per-cycle stats context cache: symbol → StatsContext | None
    # Computed once per symbol (not per TF) to avoid redundant DB queries.
    stats_ctx_cache: dict[str, StatsContext | None] = {}
    now_myt = datetime.datetime.now(tz=datetime.timezone(datetime.timedelta(hours=8)))

    alerts: list[str] = []

    # --- Phase 1: Pre-fetch all DB data sequentially ---
    # Stats are per-symbol; OHLCV is per (symbol, tf).
    # Isolating all DB reads before the parallel scan phase ensures no DuckDB
    # connection is accessed from multiple threads simultaneously.
    # Phase A (equities, yfinance): no funding-rate strategies are registered,
    # so `funding_map` is always all-None. The `requires_funding` branch in
    # `scan_symbol` is dormant code retained for the upstream crypto path.
    funding_map: dict[str, pd.DataFrame | None] = dict.fromkeys(symbols)
    ohlcv_map: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol in symbols:
        if symbol not in stats_ctx_cache:
            stats_ctx_cache[symbol] = _compute_stats_context(conn, symbol, now_myt)
        for tf in timeframes:
            _key = (symbol, tf)
            ohlcv_map[_key] = (
                ohlcv_cache[_key]
                if ohlcv_cache and _key in ohlcv_cache
                else get_ohlcv(conn, symbol, tf, start_ms, now_ms)
            )

    # F8 HTF EMA slope cache — keyed by (symbol, htf_tf, period, slope_lookback).
    # Pre-computed once per cycle from the union of default + per-strategy anchors.
    # Reuses ohlcv_map / ohlcv_cache when the anchor TF is already loaded; otherwise
    # fetches HTF candles from DB. Slope is computed on closed candles only
    # (drops the in-progress bar) so a forming HTF candle does not skew direction.
    htf_slope_cache: dict[tuple[str, str, int, int], float | None] = {}
    if bias_cfg is not None and bias_cfg.htf_ema_enabled:
        needed_anchors: set[tuple[str, int, int]] = {
            (
                bias_cfg.htf_ema_default_tf,
                bias_cfg.htf_ema_default_period,
                bias_cfg.htf_ema_default_slope_lookback,
            )
        }
        for _ov in bias_cfg.htf_ema_per_strategy.values():
            needed_anchors.add((_ov.tf, _ov.period, _ov.slope_lookback))
        for _sym in symbols:
            for _atf, _period, _slb in needed_anchors:
                _ckey = (_sym, _atf, _period, _slb)
                _df = ohlcv_map.get((_sym, _atf))
                if _df is None and ohlcv_cache is not None:
                    _df = ohlcv_cache.get((_sym, _atf))
                if _df is None or _df.empty:
                    _df = get_ohlcv(conn, _sym, _atf, start_ms, now_ms)
                if _df is None or _df.empty or len(_df) < 3:
                    htf_slope_cache[_ckey] = None
                    continue
                _closed = _df["close"].iloc[:-1]
                htf_slope_cache[_ckey] = compute_htf_ema_slope(_closed, _period, _slb)

    # v2 Phase 2 regime cache — keyed by symbol. One classification per cycle
    # off the regime_htf_tf candles (default 4h per redesign §6). Mirrors the
    # F8 "drop in-progress bar" rule by reading iloc[-2] (last closed candle).
    # Cache miss / "unknown" → gate falls open.
    regime_cache: dict[str, Regime] = {}
    if bias_cfg is not None and bias_cfg.regime_enabled:
        _r_tf = bias_cfg.regime_htf_tf
        for _sym in symbols:
            _df = ohlcv_map.get((_sym, _r_tf))
            if _df is None and ohlcv_cache is not None:
                _df = ohlcv_cache.get((_sym, _r_tf))
            if _df is None or _df.empty:
                _df = get_ohlcv(conn, _sym, _r_tf, start_ms, now_ms)
            if _df is None or _df.empty or len(_df) < 2:
                continue
            try:
                _series = classify_series(_df, _r_tf)
            except ValueError:
                # Unsupported timeframe — fall open.
                continue
            if len(_series) >= 2:
                regime_cache[_sym] = _series.iloc[-2]  # last closed candle

    # --- Phase 2: Fan-out scan_symbol via ThreadPoolExecutor ---
    # scan_symbol is pure Python/pandas — no DB access, no shared mutable state.
    # pandas rolling/shift/boolean masks release the GIL → real concurrency.
    # Closures are safe here: ThreadPoolExecutor shares memory, no pickling needed.
    def _scan_task(_sym: str, _tf: str) -> "tuple[str, str, list[SignalEvent], Any]":
        _ohlcv = ohlcv_map[(_sym, _tf)]
        _funding = funding_map.get(_sym)
        _gap = get_overnight_gap(_ohlcv)
        # Slice to _SCAN_WINDOW for detectors — they only need recent candles
        # (max lookback = 100). Full window stays in ohlcv_map for Phase 3 backtest.
        _ohlcv_scan = (
            _ohlcv.iloc[-_SCAN_WINDOW:] if len(_ohlcv) > _SCAN_WINDOW else _ohlcv
        )
        _events = scan_symbol(
            ohlcv_df=_ohlcv_scan,
            symbol=_sym,
            timeframe=_tf,
            strategies=strategies,
            funding_df=_funding,
            day_filter=day_filter,
            strategy_timeframes=strategy_timeframes,
            confidence_override=confidence_override,
            directional_confidence_override=directional_confidence_override,
            catch_up=catch_up,
        )
        return _sym, _tf, _events, _gap

    _pairs = [(sym, tf) for sym in symbols for tf in timeframes]
    _n_workers = max(1, min((os.cpu_count() or 2) - 1, len(_pairs)))
    scan_results: list[Any] = []
    if _n_workers > 1 and len(_pairs) > 1:
        with ThreadPoolExecutor(max_workers=_n_workers) as _pool:
            _futs = {_pool.submit(_scan_task, sym, tf): (sym, tf) for sym, tf in _pairs}
            for _fut in as_completed(_futs):
                scan_results.append(_fut.result())
        # Sort HTF before LTF so Phase 3 writes HTF signals to DB first.
        # Cross-TF co-fire checks query the DB — if LTF is processed first,
        # the HTF signal from the same cycle isn't in DB yet and confluence
        # is silently missed.
        _sym_idx = {s: i for i, s in enumerate(symbols)}
        _tf_idx = {t: i for i, t in enumerate(timeframes)}
        scan_results.sort(key=lambda r: (_sym_idx[r[0]], -_tf_idx[r[1]]))
    else:
        # Single-worker: scan pairs in HTF-first order for the same reason.
        _tf_idx_single = {t: i for i, t in enumerate(timeframes)}
        _pairs_htf_first = sorted(
            _pairs, key=lambda p: (symbols.index(p[0]), -_tf_idx_single[p[1]])
        )
        for sym, tf in _pairs_htf_first:
            scan_results.append(_scan_task(sym, tf))

    # Catch-up: split each (symbol, tf) result into one pseudo-result per candle
    # open_time so Phase 3 processes every missed candle independently (conflict
    # resolution + confluence stacking stay per-candle correct). Each group
    # carries an is_backfill flag: the newest CLOSED candle may dispatch to
    # Telegram, plus any older one still inside max_alert_age_hours (see
    # may_dispatch_candle); everything else is recorded as ledger evidence only.
    # Default path = single latest candle = one group = byte-identical to the
    # pre-catch-up flow.
    _grouped: list[Any] = []
    for _s, _t, _evs, _g in scan_results:
        if not catch_up or not _evs:
            _grouped.append((_s, _t, _evs, _g, False))
            continue
        _full = ohlcv_map[(_s, _t)]
        if len(_full) < 2:
            _grouped.append((_s, _t, _evs, _g, False))
            continue
        # The newest CLOSED candle — taken from OHLCV, not from the events.
        # Using max(event.open_time) would promote an older candle to "latest"
        # whenever the newest bar produced no signal, and that candle would
        # then alert as if it were live. Mirrors scan_symbol's conditional
        # forming-bar rule: scanned pre-market / after-close the final yfinance
        # row is already closed, so iloc[-1] is the latest — not the parent's
        # unconditional iloc[-2].
        _last_ot = int(_full["open_time"].iloc[-1])
        _tf_ms = parse_timeframe_secs(_t) * 1000
        # One clock reading for both the forming-bar test and the age window:
        # two calls to time.time() can straddle a candle close and disagree.
        _now_ms = int(time.time() * 1000)
        _latest_closed = (
            int(_full["open_time"].iloc[-2])
            if _now_ms < _last_ot + _tf_ms
            else _last_ot
        )
        # Cold-start guard: a key with no watermark has never fired, so every
        # candle in the scan window would look "missed" and burst into the
        # ledger on first contact. Restrict such keys to the latest candle.
        _kept = [
            e
            for e in _evs
            if e.open_time == _latest_closed
            or store.last_marked(_s, _t, e.strategy) is not None
        ]
        _by_candle: dict[int, list[SignalEvent]] = {}
        for _e in _kept:
            _by_candle.setdefault(_e.open_time, []).append(_e)
        for _ot in sorted(_by_candle):
            _is_backfill = not may_dispatch_candle(
                _ot, _latest_closed, _tf_ms, _now_ms, max_alert_age_hours
            )
            _grouped.append((_s, _t, _by_candle[_ot], _g, _is_backfill))
    scan_results = _grouped

    # --- Phase 3: Fan-in — sequential processing of scan results ---
    # All shared-state operations happen here: CooldownStore reads/writes,
    # bt_cache updates, DB writes (upsert_signals, upsert_backtest_run).
    for symbol, tf, events, overnight_gap, is_backfill in scan_results:
        ohlcv_df = ohlcv_map[(symbol, tf)]
        funding_df = funding_map.get(symbol)

        # F9 ATR-as-min-SL floor — widen tight structural SLs and recompute
        # TP from tp_r. Runs before conflict/dedup/bias so every downstream
        # path (DB persistence, backtest filter, formatter) sees the
        # corrected sl_price/tp_price. Default off; opt-in per-strategy.
        events = _apply_atr_floor(
            events,
            ohlcv_df,
            symbol,
            tf,
            strategy_params,
            tp_r,
            atr_sl_multiplier,
            atr_sl_floor,
        )

        # Conflict resolution: opposite directions on same symbol/tf
        # Pick the side with higher max confidence; on a tie, send both sides
        # (each signal's reason will have "⚠️ conflict" appended).
        direction_events = _apply_conflict_resolver(events, symbol, tf)
        if not direction_events:
            continue

        # Filter each strategy independently by candle watermark. (The catch-up
        # cold-start guard lives in the Phase 2b expansion above, next to the
        # backfill split.)
        passing_events = [
            e
            for e in direction_events
            if store.is_new_candle(symbol, tf, e.strategy, e.open_time)
        ]
        if not passing_events:
            continue

        # Backtest filter — L1 (module dict) → L2 (DuckDB) → full compute.
        # Per-strategy tp_r/sl_pct overrides are applied here so the filter
        # uses the same parameters the strategy was calibrated against.
        bt_results: dict[str, BacktestResult | BacktestSnapshot | None] = {}
        if backtest_cfg and backtest_cfg.mode != "off":
            for event in passing_events:
                bt_key = (symbol, tf, event.strategy)
                eff_tp_r = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r
                )
                eff_sl_pct = _resolve_sl_pct(
                    strategy_params, event.strategy, symbol, tf, sl_pct
                )
                eff_atr_sl = _resolve_atr_sl_multiplier(
                    strategy_params,
                    event.strategy,
                    symbol,
                    tf,
                    atr_sl_multiplier,
                )
                eff_atr_floor = _resolve_atr_sl_floor(
                    strategy_params, event.strategy, symbol, tf, atr_sl_floor
                )
                _tp_r_long = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r, "long"
                )
                _tp_r_short = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r, "short"
                )
                tp_r_long_eff = _tp_r_long if _tp_r_long != eff_tp_r else None
                tp_r_short_eff = _tp_r_short if _tp_r_short != eff_tp_r else None
                eff_vs_long = _resolve_volume_suppress_long(
                    strategy_params, event.strategy
                )
                eff_vs_short = _resolve_volume_suppress_short(
                    strategy_params, event.strategy
                )
                eff_vsb_long = _resolve_volume_spike_boost_long(
                    strategy_params, event.strategy
                )
                eff_vsb_short = _resolve_volume_spike_boost_short(
                    strategy_params, event.strategy
                )
                eff_adr_exempt = _is_adr_exempt(strategy_params, event.strategy)
                eff_vs = _resolve_volume_suppress(
                    strategy_params, event.strategy, backtest_cfg.volume_suppress
                )
                eff_vsb = _resolve_volume_spike_boost(
                    strategy_params, event.strategy, backtest_cfg.volume_spike_boost
                )

                if backtest_cfg.cache_enabled:
                    # Deliberately the DEFAULT origin: this run_id is only a
                    # `backtest_cache` key, and that table has exactly one
                    # writer (this scanner), so it cannot collide with anyone.
                    # Do NOT "align" it with the origin="live_gate" used for
                    # the backtest_runs write below — that write needs a
                    # distinct identity because backtest_runs has four writers;
                    # this one would just cold-start the cache for no gain.
                    run_id = _backtest_run_id(
                        symbol,
                        tf,
                        event.strategy,
                        days,
                        eff_sl_pct,
                        eff_tp_r,
                        backtest_cfg.fee_pct,
                        day_filter,
                        bias_cfg.adr_suppress_threshold if bias_cfg else None,
                        eff_vs or None,
                        backtest_cfg.min_sl_pct,
                        eff_atr_sl,
                        tp_r_long_eff,
                        tp_r_short_eff,
                        eff_vs_long or None,
                        eff_vs_short or None,
                        eff_vsb_long or None,
                        eff_vsb_short or None,
                        eff_adr_exempt,
                        eff_atr_floor,
                        cost_model=backtest_cfg.cost_model.to_json()
                        if backtest_cfg.cost_model is not None
                        else None,
                    )
                    last_candle_ts = int(ohlcv_df["open_time"].iloc[-2])
                    cache_key = _make_bt_cache_key(run_id, last_candle_ts)

                    if cache_key in _bt_mem_cache:
                        bt_result: BacktestResult | BacktestSnapshot | None = (
                            _bt_mem_cache[cache_key]
                        )
                    else:
                        bt_result = get_backtest_cache(conn, cache_key)
                        if bt_result is None:
                            bt_result = _compute_backtest(
                                ohlcv_df=ohlcv_df,
                                strategy=event.strategy,
                                funding_df=funding_df,
                                symbol=symbol,
                                timeframe=tf,
                                sl_pct=eff_sl_pct,
                                tp_r=eff_tp_r,
                                fee_pct=backtest_cfg.fee_pct,
                                day_filter=day_filter,
                                min_sl_pct=backtest_cfg.min_sl_pct,
                                atr_sl_multiplier=eff_atr_sl,
                                atr_sl_floor=eff_atr_floor,
                                adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                                if bias_cfg
                                else None,
                                adr_exempt=eff_adr_exempt,
                                volume_suppress=eff_vs,
                                volume_spike_boost=eff_vsb,
                                volume_suppress_long=eff_vs_long,
                                volume_suppress_short=eff_vs_short,
                                volume_spike_boost_long=eff_vsb_long,
                                volume_spike_boost_short=eff_vsb_short,
                                tp_r_long=tp_r_long_eff,
                                tp_r_short=tp_r_short_eff,
                                cost_model=backtest_cfg.cost_model,
                            )
                            if bt_result is not None:
                                put_backtest_cache(
                                    conn,
                                    cache_key,
                                    run_id,
                                    last_candle_ts,
                                    bt_result,
                                )
                                bt_to_save[bt_key] = bt_result
                        _bt_mem_cache[cache_key] = bt_result
                else:
                    bt_result = _compute_backtest(
                        ohlcv_df=ohlcv_df,
                        strategy=event.strategy,
                        funding_df=funding_df,
                        symbol=symbol,
                        timeframe=tf,
                        sl_pct=eff_sl_pct,
                        tp_r=eff_tp_r,
                        fee_pct=backtest_cfg.fee_pct,
                        day_filter=day_filter,
                        min_sl_pct=backtest_cfg.min_sl_pct,
                        atr_sl_multiplier=eff_atr_sl,
                        atr_sl_floor=eff_atr_floor,
                        adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                        if bias_cfg
                        else None,
                        adr_exempt=eff_adr_exempt,
                        volume_suppress=eff_vs,
                        volume_spike_boost=eff_vsb,
                        volume_suppress_long=eff_vs_long,
                        volume_suppress_short=eff_vs_short,
                        volume_spike_boost_long=eff_vsb_long,
                        volume_spike_boost_short=eff_vsb_short,
                        tp_r_long=tp_r_long_eff,
                        tp_r_short=tp_r_short_eff,
                        cost_model=backtest_cfg.cost_model,
                    )
                    bt_to_save[bt_key] = bt_result
                bt_results[event.strategy] = bt_result

            if backtest_cfg.mode == "hard":
                passing_events = [
                    e
                    for e in passing_events
                    if passes_ev_gate(
                        bt_results.get(e.strategy),
                        direction=e.direction,
                        timeframe=tf,
                        backtest_cfg=backtest_cfg,
                    )
                ]
                if not passing_events:
                    logger.info("Backtest hard filter suppressed %s %s", symbol, tf)
                    continue

        # Volume gate — per-strategy, handles both suppression and spike tagging.
        # Suppression: drop low-volume signal candles when the strategy has
        #   volume_suppress enabled. Exception: spike candles bypass suppression
        #   when volume_spike_boost is on (spike > 3× rolling mean = conviction).
        # Spike tagging: always tag SignalEvent.volume_spike regardless of suppress
        #   so alert_formatter can show ⚡ even when suppression is off.
        if backtest_cfg:
            vol_time_to_idx: dict[int, int] | None = None
            vol_filtered: list[SignalEvent] = []
            for _e in passing_events:
                if vol_time_to_idx is None:
                    vol_time_to_idx = {
                        int(t): i
                        for i, t in enumerate(ohlcv_df["open_time"].astype("int64"))
                    }
                _idx = vol_time_to_idx.get(int(_e.open_time), 0)
                _is_spike = _is_volume_spike(ohlcv_df, _idx)
                if _is_spike:
                    _e.volume_spike = True
                # Direction-aware suppress: directional fields take precedence over symmetric.
                _dir = _e.direction
                _suppress_long = _resolve_volume_suppress_long(
                    strategy_params, _e.strategy
                )
                _suppress_short = _resolve_volume_suppress_short(
                    strategy_params, _e.strategy
                )
                _suppress = (
                    _suppress_long
                    if _dir == "long" and _suppress_long is not None
                    else _suppress_short
                    if _dir == "short" and _suppress_short is not None
                    else _resolve_volume_suppress(
                        strategy_params, _e.strategy, backtest_cfg.volume_suppress
                    )
                )
                _boost_long = _resolve_volume_spike_boost_long(
                    strategy_params, _e.strategy
                )
                _boost_short = _resolve_volume_spike_boost_short(
                    strategy_params, _e.strategy
                )
                _boost = (
                    _boost_long
                    if _dir == "long" and _boost_long is not None
                    else _boost_short
                    if _dir == "short" and _boost_short is not None
                    else _resolve_volume_spike_boost(
                        strategy_params,
                        _e.strategy,
                        backtest_cfg.volume_spike_boost,
                    )
                )
                if _suppress and _is_low_volume(ohlcv_df, _idx):
                    # Spike boost: exempt high-conviction candles from suppress.
                    if _is_spike and _boost:
                        logger.info(
                            "Volume spike exempted %s %s — %s %s",
                            symbol,
                            tf,
                            _e.direction.upper(),
                            _e.strategy,
                        )
                    else:
                        logger.info(
                            "Volume filter suppressed %s %s — %s %s",
                            symbol,
                            tf,
                            _e.direction.upper(),
                            _e.strategy,
                        )
                        continue
                vol_filtered.append(_e)
            passing_events = vol_filtered
            if not passing_events:
                continue

        # Bias gate — regime, F8 HTF EMA, ADR progress, and DOW context filters.
        # Never raises — stats failures must not block signal dispatch.
        if bias_cfg is not None:
            # Step −1: regime gate (v2 Phase 2 — coarsest, runs first).
            # Drops signals whose strategy type is not enabled in the current
            # 4h regime. F8 then refines direction within the allowed regime.
            if bias_cfg.regime_enabled and passing_events:
                passing_events = _apply_regime_gate(
                    passing_events,
                    bias_cfg,
                    regime_cache,
                    symbol,
                    tf,
                )
                if not passing_events:
                    continue

            # Step −0.5: T2c per-strategy directional suppress gate.
            # Cheapest filter — pure per-event flag check, no HTF/regime data.
            # Drops audited dead-direction cells (e.g. bos long −0.27R / n=34,767).
            if bias_cfg.direction_filter_enabled and passing_events:
                passing_events = _apply_direction_filter_gate(
                    passing_events,
                    bias_cfg,
                    strategy_params,
                    symbol,
                    tf,
                )
                if not passing_events:
                    continue

            # Step 0: F8 HTF EMA directional gate.
            # Suppresses signals that fight the HTF trend (per-strategy anchor).
            if bias_cfg.htf_ema_enabled and passing_events:
                passing_events = _apply_htf_ema_gate(
                    passing_events,
                    bias_cfg,
                    htf_slope_cache,
                    symbol,
                    tf,
                )
                if not passing_events:
                    continue

            bias_ctx = stats_ctx_cache.get(symbol)
            if bias_ctx is not None:
                # Step 1: ADR directional suppress — remove signals chasing the
                # already-consumed direction. LONGs suppressed when move was up
                # (price near day high); SHORTs when move was down. Reversal signals
                # in the opposing direction are kept — fading an extreme is valid
                # even when ADR is consumed. Falls back to blanket suppress when
                # move direction is unknown.
                if (
                    bias_cfg.adr_suppress_threshold is not None
                    and bias_ctx.adr_consumed_pct is not None
                    and bias_ctx.adr_consumed_pct >= bias_cfg.adr_suppress_threshold
                ):
                    if bias_ctx.adr_move_up is None:
                        logger.info(
                            "ADR bias gate suppressed %s %s — %.0f%% consumed "
                            "(direction unknown)",
                            symbol,
                            tf,
                            bias_ctx.adr_consumed_pct * 100,
                        )
                        continue
                    suppress_dir = "long" if bias_ctx.adr_move_up else "short"
                    n_before = len(passing_events)
                    passing_events = [
                        e
                        for e in passing_events
                        if e.direction != suppress_dir
                        or _is_adr_exempt(strategy_params, e.strategy)
                    ]
                    if len(passing_events) < n_before:
                        logger.info(
                            "ADR bias gate removed %d %s signal(s) for %s %s "
                            "— %.0f%% consumed, chasing %s",
                            n_before - len(passing_events),
                            suppress_dir,
                            symbol,
                            tf,
                            bias_ctx.adr_consumed_pct * 100,
                            suppress_dir,
                        )
                    if not passing_events:
                        continue

                # Step 2: DOW soft suppress — reduce confidence by 1 star
                # when signal direction opposes today's historical avg return.
                if bias_cfg.dow_soft_suppress:
                    avg_ret = bias_ctx.avg_return_today
                    if abs(avg_ret) >= bias_cfg.dow_suppress_min_abs_return:
                        for event in passing_events:
                            if (event.direction == "long" and avg_ret < 0) or (
                                event.direction == "short" and avg_ret > 0
                            ):
                                event.confidence = max(1, event.confidence - 1)
                                logger.debug(
                                    "DOW bias: %s %s %s confidence → %d (avg_ret %.3f)",
                                    symbol,
                                    tf,
                                    event.strategy,
                                    event.confidence,
                                    avg_ret,
                                )

        # Note: the primary candle watermark is NOT stamped here. It is marked
        # only after a successful live primary dispatch (see the send block
        # below), so a non-sending / dry run never "consumes" a candle and
        # dedups the real alert away. DB + outcome persistence stay
        # unconditional — they are idempotent upserts and re-run harmlessly.

        # Persist passing signals to DB so the Signal Feed can read from DB
        # instead of re-scanning on every page load.
        now_fired_ms = int(time.time() * 1000)
        signals_rows = [
            {
                "symbol": e.symbol,
                "timeframe": e.timeframe,
                "strategy": e.strategy,
                "open_time": e.open_time,
                "direction": e.direction,
                "entry_price": e.price,
                "sl_price": e.sl_price,
                "reason": e.reason,
                "confidence": e.confidence,
                "fired_at": now_fired_ms,
            }
            for e in passing_events
        ]
        signals_df = pd.DataFrame(signals_rows)
        try:
            upsert_signals(conn, signals_df)
        except Exception:
            logger.exception("Failed to persist signals to DB for %s %s", symbol, tf)

        # In a tied conflict, passing_events may contain both directions —
        # split by direction so each confluence alert is direction-homogeneous.
        directions_present = list(dict.fromkeys(e.direction for e in passing_events))
        for direction in directions_present:
            dir_events = [e for e in passing_events if e.direction == direction]
            # Scope backtest summary to this direction's strategies only.
            # Avoids showing SHORT strategy stats on a LONG alert (and vice versa)
            # in tied-conflict scenarios where both directions pass.
            dir_summary: str | None = None
            if backtest_cfg and backtest_cfg.mode != "off" and bt_results:
                dir_summary = _backtest_summary(
                    bt_results,
                    [e.strategy for e in dir_events],
                    backtest_cfg,
                    tf=tf,
                    direction=direction,
                )
            # Resolve effective tp_r for this alert — use the max across all
            # strategies in the confluence group (most optimistic target wins;
            # individual strategies already filtered to have edge at that level).
            # Direction-aware: long uses tp_r_long, short uses tp_r_short when set.
            eff_alert_tp_r = max(
                _resolve_tp_r(strategy_params, e.strategy, symbol, tf, tp_r, direction)
                for e in dir_events
            )

            # Persist per-event outcome rows so win/loss can be backfilled later.
            # tp_price/rr_ratio are filled here (after eff_alert_tp_r is known) so
            # the backfill worker can resolve win/loss against the same target the
            # alert showed — and `rr_ratio` records the R that target is ACTUALLY
            # at, so the two never describe different levels (see implied_tp_r).
            # Per-event SL/TP mirrors the formatter math: structural
            # SL when valid, else the same entry*(1±eff_sl_pct) pct fallback the
            # alert renders, floored by min_sl_pct. Every row is therefore
            # scoreable — no NULL sl_price/tp_price (closes the outcome-ledger
            # hole; see specs/2026-06-01-outcome-ledger-sl-tp-fallback-design.md).
            for e in dir_events:
                signal_id = (
                    f"{e.symbol}-{e.timeframe}-{e.strategy}-{e.open_time}-{e.direction}"
                )
                entry = e.price
                eff_sl_pct = _resolve_sl_pct(
                    strategy_params, e.strategy, symbol, tf, sl_pct
                )
                ev_sl, ev_tp = _resolve_outcome_sl_tp(
                    direction=direction,
                    entry=entry,
                    struct_sl=e.sl_price,
                    struct_tp=e.tp_price,
                    eff_sl_pct=eff_sl_pct,
                    min_sl_pct=min_sl_pct,
                    tp_r=eff_alert_tp_r,
                )
                # Record the EFFECTIVE target, not the configured one: when the
                # detector supplied a structural TP, `ev_tp` is that level and
                # `eff_alert_tp_r` describes a target this alert never had.
                ev_rr = implied_tp_r(
                    direction=direction,
                    entry=entry,
                    sl_price=ev_sl,
                    rr_ratio=eff_alert_tp_r,
                    tp_price=ev_tp,
                )
                try:
                    upsert_signal_outcome(
                        conn,
                        {
                            "signal_id": signal_id,
                            "symbol": e.symbol,
                            "tf": e.timeframe,
                            "strategy": e.strategy,
                            "direction": e.direction,
                            "fired_at_ms": now_fired_ms,
                            "candle_ts_ms": e.open_time,
                            "entry_price": entry,
                            "sl_price": ev_sl,
                            "tp_price": ev_tp,
                            "rr_ratio": ev_rr,
                            "confidence_at_fire": e.confidence,
                            "tags": e.reason,
                        },
                    )
                except Exception:
                    logger.exception(
                        "Failed to persist signal outcome for %s", signal_id
                    )

            # Compute the overnight gap-fill warning for this direction
            # (premise unaudited; see #400).
            # Rough TP mirrors the formatter's own SL/TP math so the gap
            # overlap check uses the same target price shown in the alert.
            _first = dir_events[0]
            _entry = _first.price
            if direction == "long":
                _valid_sls = [e.sl_price for e in dir_events if 0 < e.sl_price < _entry]
                _sl_dist = _entry - (
                    min(_valid_sls) if _valid_sls else _entry * (1 - sl_pct)
                )
                _sl_dist = max(_sl_dist, _entry * min_sl_pct)
                _rough_tp = (
                    _first.tp_price
                    if _first.tp_price > _entry
                    else _entry + _sl_dist * eff_alert_tp_r
                )
            else:
                _valid_sls = [e.sl_price for e in dir_events if e.sl_price > _entry]
                _sl_dist = (
                    max(_valid_sls) if _valid_sls else _entry * (1 + sl_pct)
                ) - _entry
                _sl_dist = max(_sl_dist, _entry * min_sl_pct)
                _rough_tp = (
                    _first.tp_price
                    if 0 < _first.tp_price < _entry
                    else _entry - _sl_dist * eff_alert_tp_r
                )
            _gap_warning = (
                gap_fill_warning(overnight_gap, direction, _entry)
                if overnight_gap is not None
                else None
            )

            # Co-fire confluence tagging (D10 step 3): check if a known-good
            # strategy pair from backtest_combos co-fired within combo_window
            # candles. Attaches ConfluenceData to each event so the formatter
            # can append the blockquote section.
            # Same-TF confluence (step 3): check known-good same-TF pairs.
            _best_cofire: ConfluenceData | None = None
            if combo_lookup:
                _same_tf = _find_live_cofire(
                    dir_events,
                    ohlcv_df,
                    conn,
                    combo_lookup,
                    symbol,
                    tf,
                    combo_window,
                    combo_min_avg_r,
                )
                if _same_tf is not None:
                    _best_cofire = _same_tf

            # Cross-TF confluence (step 4): HTF context + LTF entry.
            if cross_tf_lookup and cross_tf_pairs:
                _cross = _find_cross_tf_cofire(
                    dir_events,
                    conn,
                    symbol,
                    tf,
                    cross_tf_lookup,
                    cross_tf_pairs,
                    cross_tf_window_hours,
                    cross_tf_min_avg_r,
                )
                # Tag whichever has the higher avg_r.
                if _cross is not None and (
                    _best_cofire is None or _cross.avg_r > _best_cofire.avg_r
                ):
                    _best_cofire = _cross

            if _best_cofire is not None:
                for _e in dir_events:
                    _e.confluence_combo = _best_cofire
                _tf_label = (
                    f"{_best_cofire.htf_tf}→{_best_cofire.ltf_tf}"
                    if _best_cofire.htf_tf
                    else tf
                )
                logger.info(
                    "Co-fire: %s %s %s+%s [%s] (avg_r %.2f, %d candles ago)",
                    symbol,
                    tf,
                    dir_events[0].strategy,
                    _best_cofire.co_strategy,
                    _tf_label,
                    _best_cofire.avg_r,
                    _best_cofire.candles_ago,
                )

            if is_backfill:
                # Recovered candle: recorded above as ledger evidence (DB
                # signals + outcome rows), never alerted — it fell outside
                # max_alert_age_hours, so it is not tradeable and a burst of
                # stale alerts is noise. Consume
                # the primary watermark so the next run does not replay it;
                # this is the deliberate exception to the #68 "only mark on
                # dispatch" rule. The wife watermark stays untouched — nothing
                # was dispatched on that channel and nothing reads it for dedup.
                for e in dir_events:
                    store.mark_candle(symbol, tf, e.strategy, e.open_time)
                continue

            # Stack all passing strategies into one confluence alert
            msg = format_confluence_alert(
                dir_events,
                sl_pct=sl_pct,
                tp_r=eff_alert_tp_r,
                min_sl_pct=min_sl_pct,
                backtest_summary=dir_summary,
                stats_context=stats_ctx_cache.get(symbol),
                gap_warning=_gap_warning,
                ohlcv_df=ohlcv_df,
            )
            alerts.append(msg)
            logger.info(
                "Signal: %s %s %s %s (confluence: %d)",
                symbol,
                tf,
                direction,
                [e.strategy for e in dir_events],
                len(dir_events),
            )

            if send_telegram:
                # Primary channel: mark the candle watermark only after a
                # successful live dispatch (creds present, send attempted), so a
                # non-sending / dry run never consumes the candle. Mirrors the
                # wife channel below.
                try:
                    primary_sent = dispatch_to_channel(msg, "primary")
                    if primary_sent:
                        for e in dir_events:
                            store.mark_candle(symbol, tf, e.strategy, e.open_time)
                except Exception:
                    logger.exception("Telegram send failed for %s", symbol)

                # Wife channel: minimal BUY/HOLD relabel. Failures isolated so a
                # wife outage cannot block the primary trader-facing channel.
                try:
                    wife_msg = format_wife_confluence_alert(
                        dir_events,
                        sl_pct=sl_pct,
                        tp_r=eff_alert_tp_r,
                        min_sl_pct=min_sl_pct,
                        ohlcv_df=ohlcv_df,
                    )
                    sent = dispatch_to_channel(wife_msg, "wife")
                    if sent:
                        for e in dir_events:
                            store.mark_candle(
                                symbol, tf, e.strategy, e.open_time, channel="wife"
                            )
                except Exception:
                    logger.exception("Telegram wife send failed for %s", symbol)
    # Persist freshly computed backtest results to backtest_runs so win-rate data
    # accumulates passively. Cache hits (BacktestSnapshot) are excluded — only
    # full BacktestResult objects land here. Covers combos that fired this cycle.
    if backtest_cfg and backtest_cfg.save_results and bt_to_save:
        try:
            universe_policy_json: str | None = load_universe_policy().to_json()
        except ValueError:
            universe_policy_json = None
        for (sym, tf, strategy), bt_result in bt_to_save.items():
            if bt_result is None:
                continue
            try:
                upsert_backtest_run(
                    conn,
                    bt_result,
                    days=backtest_cfg.days,
                    data_start_ms=start_ms,
                    data_end_ms=now_ms,
                    sl_pct=_resolve_sl_pct(strategy_params, strategy, sym, tf, sl_pct),
                    tp_r=_resolve_tp_r(strategy_params, strategy, sym, tf, tp_r),
                    fee_pct=backtest_cfg.fee_pct,
                    day_filter=day_filter,
                    # Mirrors the conditions `bt_cache._compute_backtest`
                    # actually applied for this cell (its pre-filter no-ops on
                    # non-intraday timeframes and skips exempt strategies), not
                    # the config's declared value.
                    adr_suppress_threshold=effective_adr_threshold(
                        bias_cfg.adr_suppress_threshold if bias_cfg else None,
                        tf,
                        adr_exempt=_is_adr_exempt(strategy_params, strategy),
                    ),
                    # `bt_cache._compute_backtest` calls `run_backtest` with no
                    # LiveParityConfig at all, so no gate can have run here.
                    # None is the executed truth, not an omission.
                    live_parity=None,
                    volume_suppress=_resolve_volume_suppress(
                        strategy_params, strategy, backtest_cfg.volume_suppress
                    )
                    or None,
                    universe_policy=universe_policy_json,
                    cost_model=backtest_cfg.cost_model.to_json()
                    if backtest_cfg.cost_model is not None
                    else None,
                    origin="live_gate",
                )
            except Exception:
                logger.exception(
                    "Failed to persist backtest run for %s %s %s", sym, tf, strategy
                )

    return alerts
