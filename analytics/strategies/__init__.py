"""Strategies package.

Eager re-exports of leaf modules (`_base`, `_shared`, `_seasonality`) and the
registry assembler (`_registry`) so callers can import everything from
`analytics.strategies` without having to reach into private submodules.

In strat-2 the registries (`STRATEGY_REGISTRY`, `DETECTOR_REGISTRY`, etc.) and
the per-detector `detect_*` functions moved here from `analytics.indicators_lib`.
`indicators_lib.py` is now a thin shim that re-exports from this package.
"""

from analytics.strategies._base import SIGNAL_COLUMNS, ParamSpec, StrategySpec
from analytics.strategies._registry import (
    DETECTOR_REGISTRY,
    INCOMPATIBLE_PAIRS,
    KNOWN_STRATEGIES,
    KNOWN_STRATEGY_TYPES,
    STRATEGY_REGISTRY,
    STRATEGY_TYPE_GROUPS,
    patch_confidence_scores,
)
from analytics.strategies._seasonality import SEASONALITY_COLUMNS, seasonality_stats
from analytics.strategies._shared import (
    _empty_signals,
    _find_bos_swing,
    _fmt_time,
    _signals_to_df,
    compute_ema,
    compute_htf_ema_slope,
    ema_cross_count,
    is_trending,
    volume_confirm,
)
from analytics.strategies.doji import detect_doji
from analytics.strategies.ema import detect_ema
from analytics.strategies.engulfing import detect_engulfing
from analytics.strategies.eqh_eql import detect_eqh_eql
from analytics.strategies.fibonacci_retracement import detect_fibonacci_retracement
from analytics.strategies.funding_extreme import detect_funding_extreme
from analytics.strategies.fvg import detect_fvg
from analytics.strategies.hammer_hanging_man import detect_hammer_hanging_man
from analytics.strategies.inside_bar import detect_inside_bar
from analytics.strategies.liquidity_sweep import detect_liquidity_sweep
from analytics.strategies.market_structure import detect_market_structure
from analytics.strategies.marubozu_retest import detect_marubozu_retest
from analytics.strategies.morning_evening_star import detect_morning_evening_star
from analytics.strategies.orb_breakout import detect_orb_breakout
from analytics.strategies.order_block import detect_order_block
from analytics.strategies.ote_entry import detect_ote_entry
from analytics.strategies.pin_bar import detect_pin_bar
from analytics.strategies.trend_day import detect_trend_day
from analytics.strategies.wick_fills import detect_wick_fills

__all__ = [
    "DETECTOR_REGISTRY",
    "INCOMPATIBLE_PAIRS",
    "KNOWN_STRATEGIES",
    "KNOWN_STRATEGY_TYPES",
    "ParamSpec",
    "SEASONALITY_COLUMNS",
    "SIGNAL_COLUMNS",
    "STRATEGY_REGISTRY",
    "STRATEGY_TYPE_GROUPS",
    "StrategySpec",
    "_empty_signals",
    "_find_bos_swing",
    "_fmt_time",
    "_signals_to_df",
    "compute_ema",
    "compute_htf_ema_slope",
    "detect_doji",
    "detect_ema",
    "detect_engulfing",
    "detect_eqh_eql",
    "detect_fibonacci_retracement",
    "detect_funding_extreme",
    "detect_fvg",
    "detect_hammer_hanging_man",
    "detect_inside_bar",
    "detect_liquidity_sweep",
    "detect_market_structure",
    "detect_marubozu_retest",
    "detect_morning_evening_star",
    "detect_orb_breakout",
    "detect_order_block",
    "detect_ote_entry",
    "detect_pin_bar",
    "detect_trend_day",
    "detect_wick_fills",
    "ema_cross_count",
    "is_trending",
    "patch_confidence_scores",
    "seasonality_stats",
    "volume_confirm",
]
