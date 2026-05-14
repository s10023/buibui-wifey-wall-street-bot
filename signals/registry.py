"""Signal plugin registry for the signal daemon.

Maps strategy name to plugin metadata. Excluded strategies:
- seasonality: produces stats, not actionable entry signals
- fibonacci_retracement: legacy, superseded by fib_golden_zone.

`requires_funding`, `requires_secondary`, and `confidence` flags live on
`analytics.strategies.STRATEGY_REGISTRY`; they are not duplicated here.
Confidence is resolved per-TF at dispatch time via STRATEGY_REGISTRY[name].get_confidence(tf).
"""

from collections.abc import Callable
from typing import TypedDict

import pandas as pd

from analytics.strategies import (
    detect_doji,
    detect_ema,
    detect_engulfing,
    detect_eqh_eql,
    detect_fib_golden_zone,
    detect_fvg,
    detect_hammer_hanging_man,
    detect_inside_bar,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_marubozu_retest,
    detect_morning_evening_star,
    detect_orb_breakout,
    detect_order_block,
    detect_ote_entry,
    detect_pin_bar,
    detect_smt_divergence,
    detect_trend_day,
    detect_wick_fills,
)

DetectorFn = Callable[..., pd.DataFrame]


class SignalPlugin(TypedDict):
    detector: DetectorFn


_DETECTORS: dict[str, DetectorFn] = {
    "wick_fill": detect_wick_fills,
    "marubozu": detect_marubozu_retest,
    "orb": detect_orb_breakout,
    "liquidity_sweep": detect_liquidity_sweep,
    "fvg": detect_fvg,
    "bos": detect_market_structure,
    "smt_divergence": detect_smt_divergence,
    "eqh_eql": detect_eqh_eql,
    "order_block": detect_order_block,
    "trend_day": detect_trend_day,
    "engulfing": detect_engulfing,
    "pin_bar": detect_pin_bar,
    "inside_bar": detect_inside_bar,
    "hammer_hanging_man": detect_hammer_hanging_man,
    "doji": detect_doji,
    "morning_evening_star": detect_morning_evening_star,
    "fib_golden_zone": detect_fib_golden_zone,
    "ote_entry": detect_ote_entry,
    "ema": detect_ema,
}


SIGNAL_REGISTRY: dict[str, SignalPlugin] = {
    name: SignalPlugin(detector=fn) for name, fn in _DETECTORS.items()
}
