"""Signal test runner — fires detectors against historical data for alert testing.

Unlike the live daemon, this:
  - Does NOT filter to the latest candle only.
  - Does NOT use the cooldown store.
  - Does NOT write to the DB.
  - Prints formatted alerts to stdout and optionally sends the most recent via Telegram.

Intended for testing alert formatting changes without waiting for a live signal.
"""

import datetime
import logging
import time
from pathlib import Path

import duckdb
import pandas as pd

from analytics.data_store import DEFAULT_DB_PATH, get_ohlcv
from analytics.signal.types import SignalEvent
from analytics.signal_config import BacktestFilterConfig, BiasConfig, StrategyOverride
from analytics.signal_lib import (
    _backtest_summary,
    _compute_backtest,
    _compute_stats_context,
    _is_adr_exempt,
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
    parse_timeframe_secs,
)
from analytics.strategies import STRATEGY_REGISTRY
from signals.alert_formatter import (
    format_confluence_alert,
    format_wife_confluence_alert,
)
from signals.registry import SIGNAL_REGISTRY
from utils.telegram import send_telegram_message
from utils.telegram_router import dispatch_to_channel

logger = logging.getLogger(__name__)

_MYT = datetime.timezone(datetime.timedelta(hours=8))


def _build_event(
    row: pd.Series,
    symbol: str,
    timeframe: str,
    strategy: str,
    closed_df: pd.DataFrame,
    fallback_close: float,
) -> SignalEvent:
    signal_ts = int(row["open_time"])
    matches = closed_df.loc[closed_df["open_time"] == signal_ts, "close"]
    price = float(matches.iloc[0]) if not matches.empty else fallback_close
    spec = STRATEGY_REGISTRY.get(strategy)
    confidence = spec.get_confidence(timeframe) if spec else 3
    tp_raw = row.get("tp_price")
    return SignalEvent(
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        direction=str(row["direction"]),
        reason=str(row.get("reason", "")),
        open_time=signal_ts,
        price=price,
        sl_price=float(row.get("sl_price") or 0.0),
        tp_price=float(tp_raw) if tp_raw is not None else 0.0,
        context=str(row.get("context", "")),
        confidence=confidence,
        conflict=False,
        low_volume=bool(row.get("low_volume", False)),
        volume_spike=bool(row.get("volume_spike", False)),
    )


def run_signal_test(
    symbols: list[str],
    timeframes: list[str],
    strategies: list[str],
    at_ms: int | None = None,
    lookback: int = 200,
    since_ms: int | None = None,
    tp_r: float = 2.0,
    sl_pct: float = 0.02,
    min_sl_pct: float = 0.0,
    direction_filter: str | None = None,
    send_telegram: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    backtest_cfg: BacktestFilterConfig | None = None,
    day_filter: str = "off",
    strategy_params: dict[str, StrategyOverride] | None = None,
    bias_cfg: BiasConfig | None = None,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
) -> None:
    """Run detectors against historical candles and print formatted alerts.

    Iterates all symbol × timeframe × strategy combos. For each combo that
    produces a signal, prints a fully-formatted alert (with backtest summary
    and stats context where available). With ``--telegram``, sends the most
    recent signal found overall.

    Parameters
    ----------
    symbols:
        Symbols, e.g. ``["AAPL"]``.
    timeframes:
        Candle timeframes, e.g. ``["1h", "4h"]``.
    strategies:
        Strategy names from SIGNAL_REGISTRY.
    at_ms:
        Pin to a specific candle (Unix ms, UTC). The df is trimmed to candles
        with ``open_time <= at_ms`` before running the detector. Defaults to now.
    lookback:
        Number of candles to load ending at ``at_ms`` (or now). Ignored when
        ``since_ms`` is set. Default 200.
    since_ms:
        Load all candles from this Unix ms timestamp up to ``at_ms`` (or now).
        Overrides ``lookback`` when set. Use to match the live daemon's full
        history window so backtest counts, warnings, and stats context align.
    tp_r:
        TP risk:reward for alert formatting. Default 2.0.
    sl_pct:
        Fallback SL pct when no structural SL is available. Default 0.02.
    min_sl_pct:
        Minimum SL distance as fraction of price. Default 0.0 (disabled).
    direction_filter:
        When set to ``"long"`` or ``"short"``, only signals in that direction
        are considered. Default None (both directions).
    send_telegram:
        When True, sends the most recent signal found via Telegram. Default False.
    db_path:
        Path to the DuckDB analytics database.
    backtest_cfg:
        Backtest filter config — enables backtest summary in alerts when provided.
    day_filter:
        Day filter passed to backtest engine. Default ``"off"``.
    """
    # Validate strategies up front.
    unknown = [s for s in strategies if s not in SIGNAL_REGISTRY]
    if unknown:
        raise ValueError(
            f"Unknown strategy/strategies: {unknown}. "
            f"Available: {sorted(SIGNAL_REGISTRY.keys())}"
        )

    now_ms = int(time.time() * 1000)
    end_ms = at_ms if at_ms is not None else now_ms
    now_myt = datetime.datetime.now(tz=_MYT)

    n_combos = len(symbols) * len(timeframes) * len(strategies)
    print(
        f"Running {n_combos} combo(s): "
        f"{len(symbols)} symbol(s) × {len(timeframes)} TF(s) × {len(strategies)} strategy/strategies"
    )
    print(f"  symbols    : {symbols}")
    print(f"  timeframes : {timeframes}")
    print(f"  strategies : {strategies}")
    if at_ms:
        at_dt = datetime.datetime.fromtimestamp(at_ms / 1000, tz=_MYT)
        print(f"  pinned at  : {at_dt.strftime('%Y-%m-%d %H:%M MYT')}")
    if since_ms:
        since_dt = datetime.datetime.fromtimestamp(since_ms / 1000, tz=_MYT)
        print(f"  since      : {since_dt.strftime('%Y-%m-%d')} (full window)")
    else:
        print(f"  lookback   : {lookback} candles per TF")
    print()

    # Collect (open_time, alert_text) for all signals found.
    all_found: list[tuple[int, str, str]] = []
    found_combos = 0

    with duckdb.connect(str(db_path), read_only=True) as conn:
        # Stats context: computed once per symbol (expensive — 90d aggregate).
        stats_ctx_cache: dict[str, object] = {}

        for symbol in symbols:
            # Stats context — never raises, returns None on failure.
            if symbol not in stats_ctx_cache:
                stats_ctx_cache[symbol] = _compute_stats_context(conn, symbol, now_myt)

            for timeframe in timeframes:
                tf_secs = parse_timeframe_secs(timeframe)
                start_ms = (
                    since_ms
                    if since_ms is not None
                    else end_ms - lookback * tf_secs * 1000
                )

                ohlcv_df = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
                if ohlcv_df.empty:
                    print(
                        f"  [{symbol}/{timeframe}] No OHLCV data — "
                        f"run 'wifey analytics backfill --symbols {symbol}' first."
                    )
                    continue

                if at_ms is not None:
                    ohlcv_df = ohlcv_df[ohlcv_df["open_time"] <= at_ms].copy()
                    if ohlcv_df.empty:
                        print(
                            f"  [{symbol}/{timeframe}] No candles at or before the pinned timestamp."
                        )
                        continue

                closed_df = ohlcv_df.iloc[:-1].copy()
                if closed_df.empty:
                    print(f"  [{symbol}/{timeframe}] Not enough closed candles.")
                    continue

                fallback_close = float(closed_df["close"].iloc[-1])

                for strategy in strategies:
                    plugin = SIGNAL_REGISTRY[strategy]

                    try:
                        signals_df = plugin["detector"](closed_df)
                    except Exception:
                        logger.exception(
                            "Detector %s raised for %s/%s", strategy, symbol, timeframe
                        )
                        continue

                    if signals_df.empty:
                        continue

                    if direction_filter:
                        signals_df = signals_df[
                            signals_df["direction"] == direction_filter
                        ]
                        if signals_df.empty:
                            continue

                    row = signals_df.iloc[-1]
                    event = _build_event(
                        row, symbol, timeframe, strategy, closed_df, fallback_close
                    )

                    # F9 ATR-as-min-SL floor — apply so replay output matches
                    # what the live daemon would produce for this candle.
                    from analytics.signal.atr_floor import _apply_atr_floor

                    _apply_atr_floor(
                        [event],
                        ohlcv_df,
                        symbol,
                        timeframe,
                        strategy_params,
                        tp_r,
                        atr_sl_multiplier,
                        atr_sl_floor,
                    )

                    # Backtest summary — mirrors live daemon's per-strategy computation.
                    bt_summary: str | None = None
                    if backtest_cfg and backtest_cfg.mode != "off":
                        eff_tp_r = _resolve_tp_r(
                            strategy_params, strategy, symbol, timeframe, tp_r
                        )
                        eff_sl_pct = _resolve_sl_pct(
                            strategy_params, strategy, symbol, timeframe, sl_pct
                        )
                        eff_atr_sl = _resolve_atr_sl_multiplier(
                            strategy_params,
                            strategy,
                            symbol,
                            timeframe,
                            atr_sl_multiplier,
                        )
                        tp_r_long = _resolve_tp_r(
                            strategy_params, strategy, symbol, timeframe, tp_r, "long"
                        )
                        tp_r_short = _resolve_tp_r(
                            strategy_params, strategy, symbol, timeframe, tp_r, "short"
                        )
                        bt_result = _compute_backtest(
                            ohlcv_df=ohlcv_df,
                            strategy=strategy,
                            funding_df=None,
                            symbol=symbol,
                            timeframe=timeframe,
                            sl_pct=eff_sl_pct,
                            tp_r=eff_tp_r,
                            fee_pct=backtest_cfg.fee_pct,
                            day_filter=day_filter,
                            min_sl_pct=backtest_cfg.min_sl_pct,
                            atr_sl_multiplier=eff_atr_sl,
                            atr_sl_floor=_resolve_atr_sl_floor(
                                strategy_params,
                                strategy,
                                symbol,
                                timeframe,
                                atr_sl_floor,
                            ),
                            adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                            if bias_cfg
                            else None,
                            adr_exempt=_is_adr_exempt(strategy_params, strategy),
                            volume_suppress=_resolve_volume_suppress(
                                strategy_params, strategy, backtest_cfg.volume_suppress
                            ),
                            volume_spike_boost=_resolve_volume_spike_boost(
                                strategy_params,
                                strategy,
                                backtest_cfg.volume_spike_boost,
                            ),
                            volume_suppress_long=_resolve_volume_suppress_long(
                                strategy_params, strategy
                            ),
                            volume_suppress_short=_resolve_volume_suppress_short(
                                strategy_params, strategy
                            ),
                            volume_spike_boost_long=_resolve_volume_spike_boost_long(
                                strategy_params, strategy
                            ),
                            volume_spike_boost_short=_resolve_volume_spike_boost_short(
                                strategy_params, strategy
                            ),
                            tp_r_long=tp_r_long if tp_r_long != eff_tp_r else None,
                            tp_r_short=tp_r_short if tp_r_short != eff_tp_r else None,
                        )
                        if bt_result is not None:
                            bt_summary = _backtest_summary(
                                {strategy: bt_result},
                                [strategy],
                                backtest_cfg,
                                tf=timeframe,
                                direction=event.direction,
                            )

                    alert_text = format_confluence_alert(
                        [event],
                        tp_r=tp_r,
                        sl_pct=sl_pct,
                        min_sl_pct=min_sl_pct,
                        backtest_summary=bt_summary,
                        stats_context=stats_ctx_cache.get(symbol),  # type: ignore[arg-type]
                        ohlcv_df=ohlcv_df,
                    )

                    # Render the wife variant here, where `ohlcv_df` is in
                    # scope, and print it unconditionally. Building it only inside the
                    # `send_telegram` branch would leave the body unreadable
                    # without dispatching it to the real channel (the wife
                    # dry-run logs only its first line).
                    wife_text = format_wife_confluence_alert(
                        [event],
                        sl_pct=sl_pct,
                        tp_r=tp_r,
                        min_sl_pct=min_sl_pct,
                        ohlcv_df=ohlcv_df,
                    )

                    print(f"\n{'─' * 60}")
                    print(alert_text)
                    print("\n  ── wife channel ──")
                    print(wife_text)
                    all_found.append((event.open_time, alert_text, wife_text))
                    found_combos += 1

    print(f"\n{'─' * 60}")
    print(f"Found {found_combos} signal(s).")

    if not all_found:
        return

    if send_telegram:
        print(f"\nSending {len(all_found)} alert(s) to Telegram...")
        for _, alert_text, wife_text in sorted(all_found, key=lambda x: x[0]):
            send_telegram_message(alert_text)
            dispatch_to_channel(wife_text, "wife")
        print("[Telegram] Done.")
