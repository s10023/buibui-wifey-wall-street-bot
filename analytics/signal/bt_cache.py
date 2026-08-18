"""Backtest compute + summary helpers used by the live signal scanner.

`_compute_backtest` runs the detector on history and feeds run_backtest.
`_backtest_summary` formats the one-line backtest header for alerts.
The L1 `_bt_mem_cache` itself lives in `analytics.signal._common` and is
mutated by run_scan_cycle directly.
"""

import logging
from collections.abc import Mapping

import pandas as pd

from analytics.backtest.cost_model import CostModel
from analytics.backtest_lib import (
    BacktestResult,
    filter_signals_by_day,
    run_backtest,
)
from analytics.data_store import BacktestSnapshot
from analytics.signal._common import _fmt_hold
from analytics.signal.gates import _filter_signals_by_adr
from analytics.signal_config import (
    BacktestFilterConfig,
    _day_filter_to_weekdays,
)
from analytics.strategies import STRATEGY_REGISTRY
from signals.registry import SIGNAL_REGISTRY

logger = logging.getLogger(__name__)


def _compute_backtest(
    ohlcv_df: pd.DataFrame,
    strategy: str,
    funding_df: pd.DataFrame | None,
    symbol: str,
    timeframe: str,
    sl_pct: float,
    tp_r: float,
    fee_pct: float = 0.0,
    day_filter: str = "off",
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    adr_suppress_threshold: float | None = None,
    adr_exempt: bool = False,
    volume_suppress: bool = False,
    volume_spike_boost: bool = False,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    volume_spike_boost_long: bool | None = None,
    volume_spike_boost_short: bool | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
    cost_model: CostModel | None = None,
) -> BacktestResult | None:
    """Run strategy detector on ohlcv[:-1] and backtest the resulting signals.

    Excludes the current (latest) candle to avoid lookahead bias.
    Returns None if there is insufficient data or the detector raises.

    adr_suppress_threshold: when set, filters historical signals where the ADR
    consumed at signal time exceeded the threshold — mirrors the live bias gate so
    backtested avg_r reflects only trades that would have passed the gate.
    adr_exempt: when True, skips the ADR filter regardless of threshold (for
    breakout/continuation strategies that should not be ADR-gated).
    volume_suppress: skip signal candles with volume < 1.5× rolling mean.
    volume_spike_boost: exempt spike candles (> 3× rolling mean) from suppression.
    volume_suppress_long/short: directional overrides — take precedence over volume_suppress.
    volume_spike_boost_long/short: directional overrides — take precedence over volume_spike_boost.
    cost_model: Phase 0.4 equity cost model — forwarded to run_backtest;
    None keeps the flat fee_pct path.
    """
    hist_df = ohlcv_df.iloc[:-1]
    if len(hist_df) < 3:
        return None

    plugin = SIGNAL_REGISTRY.get(strategy)
    if plugin is None:
        return None
    spec = STRATEGY_REGISTRY.get(strategy)

    try:
        if spec and spec.requires_funding:
            if funding_df is None or funding_df.empty:
                return None
            signals_df = plugin["detector"](hist_df, funding_df)
        else:
            signals_df = plugin["detector"](hist_df)
    except Exception:
        logger.exception(
            "Backtest detector %s raised for %s %s", strategy, symbol, timeframe
        )
        return None

    allowed_days = _day_filter_to_weekdays(day_filter)
    if allowed_days is not None:
        signals_df = filter_signals_by_day(signals_df, allowed_days)

    if adr_suppress_threshold is not None and not adr_exempt and not signals_df.empty:
        signals_df = _filter_signals_by_adr(
            hist_df, signals_df, adr_suppress_threshold, timeframe
        )

    return run_backtest(
        hist_df,
        signals_df,
        symbol,
        timeframe,
        strategy,
        sl_pct=sl_pct,
        tp_r=tp_r,
        fee_pct=fee_pct,
        min_sl_pct=min_sl_pct,
        atr_sl_multiplier=atr_sl_multiplier,
        atr_sl_floor=atr_sl_floor,
        volume_suppress=volume_suppress,
        volume_spike_boost=volume_spike_boost,
        volume_suppress_long=volume_suppress_long,
        volume_suppress_short=volume_suppress_short,
        volume_spike_boost_long=volume_spike_boost_long,
        volume_spike_boost_short=volume_spike_boost_short,
        tp_r_long=tp_r_long,
        tp_r_short=tp_r_short,
        cost_model=cost_model,
    )


def _backtest_summary(
    results: Mapping[str, BacktestResult | BacktestSnapshot | None],
    strategies: list[str],
    cfg: BacktestFilterConfig,
    tf: str = "",
    direction: str = "",
) -> str:
    """Format a one-line backtest summary for appending to an alert message.

    Shows direction-specific win rate and avg R when direction is provided.
    Falls back to overall stats when directional bucket has fewer than min_trades.

    Single strategy (long): '📊 Backtest 90d [↑]: 62% win · avg +1.4R (18 longs) · hold ~16h'
    Multiple (short):       '📊 Backtest 90d [↓]: fvg 55%·+1.1R (12) · bos n/a (3)'
    No direction:           '📊 Backtest 90d: 62% win (28 trades) · hold ~4h'
    """
    arrow = " [↑]" if direction == "long" else " [↓]" if direction == "short" else ""
    single = len(strategies) == 1
    min_trades = cfg.effective_min_trades(tf)
    parts: list[str] = []

    for s in strategies:
        result = results.get(s)
        if result is None:
            parts.append("n/a" if single else f"{s}: n/a")
            continue

        if direction == "long":
            dir_trades = result.long_closed_trades
            dir_win_rate = result.long_win_rate
            dir_avg_r = result.long_avg_r
            dir_median_h = result.long_median_duration_h
            trade_noun = "long"
        elif direction == "short":
            dir_trades = result.short_closed_trades
            dir_win_rate = result.short_win_rate
            dir_avg_r = result.short_avg_r
            dir_median_h = result.short_median_duration_h
            trade_noun = "short"
        else:
            dir_trades = result.closed_trades
            dir_win_rate = result.win_rate if result.closed_trades else None
            dir_avg_r = result.avg_r if result.closed_trades else None
            dir_median_h = result.median_duration_h
            trade_noun = "trade"

        n = len(dir_trades)
        hold_str = f" {_fmt_hold(dir_median_h)}" if dir_median_h is not None else ""
        hold_suffix = f" · hold{hold_str}" if single and hold_str else ""
        if n < min_trades or dir_win_rate is None:
            # Say which of the two it is. `n/a` reads as "broken"; these read as
            # "checked, and there is nothing there yet" — a different claim.
            noun = trade_noun if n == 1 else f"{trade_noun}s"
            if n == 0:
                label = f"no closed {noun} yet" if single else f"{s}: none yet"
            elif n < min_trades:
                label = (
                    f"{n} {noun} — too few to judge"
                    if single
                    else f"{s}: too few ({n})"
                )
            else:
                label = (
                    f"no win rate yet ({n} {noun})" if single else f"{s}: no rate ({n})"
                )
        else:
            pct = f"{dir_win_rate:.0%}"
            if dir_avg_r is not None:
                avg_r_str = f"{dir_avg_r:+.1f}R"
                label = (
                    f"{pct} win · avg {avg_r_str} ({n} {trade_noun}s){hold_suffix}"
                    if single
                    else f"{s}: {pct}·{avg_r_str} ({n}){hold_str}"
                )
            else:
                label = (
                    f"{pct} win ({n} {trade_noun}s){hold_suffix}"
                    if single
                    else f"{s}: {pct} ({n}){hold_str}"
                )
        parts.append(label)

    body = " · ".join(parts)
    window_label = f"since {cfg.since}" if cfg.since else f"{cfg.days}d"
    return f"📊 Backtest {window_label}{arrow}: {body}"
