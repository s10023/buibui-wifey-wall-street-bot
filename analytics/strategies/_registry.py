"""Strategy registry — owns the canonical STRATEGY_REGISTRY and DETECTOR_REGISTRY.

In strat-2 this module became the explicit-tuple-driven assembler that wires
together the per-detector modules into the public registries. Every spec is
byte-identical to the pre-split `analytics/indicators_lib.py` source.

Imports of `detect_*` functions land here (not in `analytics/strategies/__init__.py`)
so this is the single point of truth for the registry → callable mapping.
`indicators_lib.py` is now a thin shim that re-exports from here.
"""

from collections.abc import Callable

import pandas as pd

from analytics.strategies._base import ParamSpec, StrategySpec
from analytics.strategies.doji import detect_doji
from analytics.strategies.ema import detect_ema
from analytics.strategies.engulfing import detect_engulfing
from analytics.strategies.eqh_eql import detect_eqh_eql
from analytics.strategies.fib_golden_zone import detect_fib_golden_zone
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

STRATEGY_REGISTRY: dict[str, StrategySpec] = {
    "seasonality": StrategySpec(
        name="seasonality",
        description="Day-of-week, hour-of-day, and week-of-month return statistics.",
        strategy_type="session",
        confidence=2,
    ),
    "wick_fill": StrategySpec(
        name="wick_fill",
        description="Signals when price re-enters a prior candle's significant wick zone.",
        strategy_type="price_action",
        params=[
            ParamSpec(
                "min_wick_body_ratio",
                "float",
                1.5,
                0.1,
                2.0,
                "Minimum wick-to-body ratio to qualify as significant.",
            ),
            ParamSpec(
                "lookback",
                "int",
                20,
                1,
                200,
                "Candles to watch for wick zone fill after the signal candle.",
            ),
        ],
        confidence={"15m": 1, "1d": 2, "1h": 1, "4h": 1},
    ),
    "marubozu": StrategySpec(
        name="marubozu",
        description="Signals on retests of Marubozu (wickless) candle open prices.",
        strategy_type="price_action",
        params=[
            ParamSpec(
                "max_wick_ratio",
                "float",
                0.1,
                0.01,
                0.5,
                "Maximum wick-to-body ratio to qualify as wickless.",
            ),
            ParamSpec(
                "lookback",
                "int",
                30,
                1,
                200,
                "Candles to watch for marubozu open retest.",
            ),
            ParamSpec(
                "min_body_pct",
                "float",
                0.005,
                0.0001,
                0.05,
                "Minimum body size as fraction of open price to qualify as a Marubozu.",
            ),
        ],
        confidence={"15m": 1, "1h": 1, "4h": 1},
    ),
    "orb": StrategySpec(
        strategy_type="session",
        name="orb",
        description=(
            "Opening Range Breakout: high/low of the first 2 candles of each UTC day"
            " defines the range; a breakout signal fires on any later candle that closes"
            " outside the range (one signal per day per direction)."
        ),
        params=[
            ParamSpec(
                "range_candles",
                "int",
                2,
                1,
                4,
                "Number of candles from 00:00 UTC that form the opening range.",
            ),
        ],
        confidence={"15m": 1, "1h": 2, "4h": 2},
    ),
    "liquidity_sweep": StrategySpec(
        name="liquidity_sweep",
        description="Signals when a wick sweeps the rolling high/low but the candle closes back inside.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "lookback",
                "int",
                20,
                2,
                200,
                "Rolling window size for swing high/low detection.",
            ),
        ],
        confidence={"15m": 1, "1d": 4, "1h": 1, "4h": 1},
    ),
    "fvg": StrategySpec(
        name="fvg",
        description="Fair Value Gap: signals when price fills a 3-candle imbalance zone.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "lookback",
                "int",
                50,
                1,
                500,
                "Candles to watch for FVG fill after the gap forms.",
            ),
            ParamSpec(
                "min_gap_pct",
                "float",
                0.001,
                0.0001,
                0.05,
                "Minimum gap size as fraction of midpoint price (0.001 = 0.1%); filters noise.",
            ),
        ],
        confidence={"15m": 1, "1d": 1, "1h": 1, "4h": 1},
    ),
    "bos": StrategySpec(
        name="bos",
        description="Break of Structure / Change of Character: market structure shift signals.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "swing_lookback",
                "int",
                5,
                1,
                50,
                "Half-window size for swing high/low identification (window = 2×n+1).",
            ),
            ParamSpec(
                "min_swing_pct",
                "float",
                0.005,
                0.0,
                0.1,
                "Minimum price range (fraction) a swing level must span to qualify for BOS/CHoCH.",
            ),
        ],
        confidence={"15m": 1, "1d": 1, "1h": 1, "4h": 1},
    ),
    "smt_divergence": StrategySpec(
        name="smt_divergence",
        description="SMT divergence: primary makes new swing extreme but correlated asset does not.",
        strategy_type="flow",
        params=[
            ParamSpec(
                "lookback",
                "int",
                10,
                2,
                200,
                "Rolling window for swing high/low comparison between assets.",
            ),
            ParamSpec(
                "trend_filter",
                "int",
                1,
                0,
                1,
                "Require close > EMA(50) for LONG and close < EMA(50) for SHORT (1=on, 0=off).",
            ),
        ],
        requires_secondary=True,
        confidence={"15m": 4, "1h": 3, "4h": 1},
    ),
    "eqh_eql": StrategySpec(
        name="eqh_eql",
        description="Equal Highs/Lows: liquidity sweep of a double-top or double-bottom level.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "lookback",
                "int",
                50,
                5,
                500,
                "Candles to scan for equal high/low pairs.",
            ),
            ParamSpec(
                "tolerance_pct",
                "float",
                0.003,
                0.0001,
                0.05,
                "Max relative difference for two highs/lows to qualify as equal.",
            ),
        ],
        confidence={
            "15m": 1,
            "1h": 2,
            "4h": 3,
        },  # 0 trades in backtest — fires too rarely to have data
    ),
    "order_block": StrategySpec(
        name="order_block",
        description="ICT Order Block: last up/down-candle before displacement; entry on retest.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "lookback",
                "int",
                50,
                5,
                500,
                "Candles to scan back for order block formations.",
            ),
            ParamSpec(
                "displacement_pct",
                "float",
                0.005,
                0.001,
                0.05,
                "Minimum % move on displacement candle to qualify an order block.",
            ),
        ],
        confidence={"15m": 1, "1d": 3, "1h": 1, "4h": 1},
    ),
    "trend_day": StrategySpec(
        name="trend_day",
        description="Trend Day: candle opens near one extreme and closes near the other with a large body and tiny wicks.",
        strategy_type="price_action",
        params=[
            ParamSpec(
                "body_pct_min",
                "float",
                0.65,
                0.4,
                0.95,
                "Minimum body-to-range ratio (body / (high - low)) to qualify as a trend day.",
            ),
            ParamSpec(
                "wick_max",
                "float",
                0.15,
                0.0,
                0.4,
                "Maximum wick-to-range ratio for the wick in the trend direction (leading wick).",
            ),
        ],
        confidence={"15m": 1, "1d": 2, "1h": 1, "4h": 3},
    ),
    "engulfing": StrategySpec(
        name="engulfing",
        description="Bullish/Bearish Engulfing: current candle body fully engulfs the prior candle body.",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance (risk-reward ratio).",
            ),
        ],
        confidence={"15m": 2, "1d": 4, "1h": 3, "4h": 3},
    ),
    "pin_bar": StrategySpec(
        name="pin_bar",
        description="Pin Bar: small body with a long wick (≥2× body) indicating rejection.",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "wick_ratio",
                "float",
                2.0,
                1.0,
                10.0,
                "Minimum ratio of rejection wick to body to qualify as a pin bar.",
            ),
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 4, "1h": 2, "4h": 2},
    ),
    "inside_bar": StrategySpec(
        name="inside_bar",
        description="Inside Bar breakout: body of current candle is fully within prior candle body; signal fires on breakout close.",
        strategy_type="price_action",
        params=[
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 3, "1h": 2, "4h": 2},
    ),
    "hammer_hanging_man": StrategySpec(
        name="hammer_hanging_man",
        description="Hammer (bullish reversal at recent low) / Hanging Man (bearish at recent high): pin-bar shape with context.",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "wick_ratio",
                "float",
                2.0,
                1.0,
                10.0,
                "Minimum lower-wick-to-body ratio for hammer/hanging man shape.",
            ),
            ParamSpec(
                "context_lookback",
                "int",
                10,
                3,
                50,
                "Bars to check for prior trend context (down for hammer, up for hanging man).",
            ),
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 4, "1h": 2, "4h": 2},
    ),
    "doji": StrategySpec(
        name="doji",
        description="Doji (open ≈ close) followed by a strongly directional candle.",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "body_threshold",
                "float",
                0.1,
                0.01,
                0.3,
                "Maximum body-to-range ratio to qualify as a doji (open ≈ close).",
            ),
            ParamSpec(
                "confirm_body_pct",
                "float",
                0.6,
                0.3,
                0.95,
                "Minimum body-to-range ratio of the confirmation candle.",
            ),
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 5, "1h": 3, "4h": 2},
    ),
    "morning_evening_star": StrategySpec(
        name="morning_evening_star",
        description="Morning Star (3-candle bullish reversal) / Evening Star (3-candle bearish reversal).",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "star_body_max",
                "float",
                0.3,
                0.05,
                0.5,
                "Maximum body-to-range ratio for the star (middle) candle.",
            ),
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 4, "1h": 2, "4h": 3},
    ),
    # Legacy — superseded by fib_golden_zone (adds BOS confirmation, better SL/TP structure).
    # Uncomment to re-enable for backtest comparison.
    # "fibonacci_retracement": StrategySpec(
    #     name="fibonacci_retracement",
    #     description="Fibonacci golden zone (0.5–0.618) retracement entry after a swing high/low.",
    #     params=[
    #         ParamSpec("swing_lookback", "int", 20, 5, 100,
    #                   "Number of bars to scan for the most recent swing high and swing low."),
    #         ParamSpec("sl_pct", "float", 0.02, 0.001, 0.1,
    #                   "Stop-loss distance as a fraction of entry price (fallback; actual SL is fib_0.786)."),
    #         ParamSpec("tp_r", "float", 2.0, 0.5, 10.0, "Take-profit as a multiple of SL distance."),
    #     ],
    #     confidence=3,
    # ),
    "fib_golden_zone": StrategySpec(
        name="fib_golden_zone",
        description="Fibonacci golden zone (0.5–0.618) entry after a confirmed BOS; TP = 1.618 extension.",
        strategy_type="fib",
        params=[
            ParamSpec(
                "swing_lookback",
                "int",
                20,
                5,
                100,
                "Number of bars to scan for the BOS swing high/low.",
            ),
            ParamSpec(
                "bos_lookback",
                "int",
                5,
                2,
                30,
                "Rolling window half-size for BOS swing detection.",
            ),
        ],
        confidence={"15m": 1, "1h": 4, "4h": 4},
    ),
    # ote_entry: registered for code completeness, NEVER enabled in any signal_watch config.
    # WFO audit (2026-05-11, since 2025-09-12, BTC/ETH/SOL × 15m/1h/4h/1d, day_filter ∈ {tue_thu, off}):
    # every cell with adequate OOS n is OOS-negative — BTC 15m -0.71R (n=53), ETH 15m -0.36R (n=47),
    # SOL 15m -0.20R (n=50), BTC 15m off -0.55R (n=121); 4h/1d too thin to score (n≤7). Audit verdict
    # "no edge — fix detector logic before re-sweeping" across all cells. Geometry verified correct;
    # no-edge is regime-driven (deep-retracement shorts steamrolled by post-2025-09 bull trend) not buggy.
    # Re-audit when regime shifts; consider removing if a second pass confirms dead.
    "ote_entry": StrategySpec(
        name="ote_entry",
        description="Optimal Trade Entry (OTE): 0.618–0.786 retracement after a confirmed BOS; TP = 1.618 extension.",
        strategy_type="fib",
        params=[
            ParamSpec(
                "swing_lookback",
                "int",
                20,
                5,
                100,
                "Number of bars to scan for the BOS swing high/low.",
            ),
            ParamSpec(
                "bos_lookback",
                "int",
                5,
                2,
                30,
                "Rolling window half-size for BOS swing detection.",
            ),
        ],
        confidence=1,  # WFO no-edge verdict 2026-05-11; never enabled in signal_watch configs (see comment above).
    ),
    "ema": StrategySpec(
        name="ema",
        description="EMA pullback continuation: trend (slow EMA + slope) + regime gate, pullback wick into fast EMA, body trigger.",
        strategy_type="trend",
        params=[
            ParamSpec(
                "fast_period", "int", 20, 5, 100, "Fast EMA span (the pullback target)."
            ),
            ParamSpec(
                "slow_period",
                "int",
                50,
                20,
                400,
                "Slow EMA span (the trend filter); must be > fast_period.",
            ),
            ParamSpec(
                "slope_lookback",
                "int",
                10,
                3,
                50,
                "Bars to measure slow-EMA slope over.",
            ),
            ParamSpec(
                "regime_lookback",
                "int",
                20,
                5,
                100,
                "Bars to count price/fast-EMA crosses over (range = many crosses).",
            ),
            ParamSpec(
                "max_crosses",
                "int",
                2,
                0,
                10,
                "Max price/fast-EMA crosses inside regime_lookback to qualify as trending.",
            ),
            ParamSpec(
                "min_slope_pct",
                "float",
                0.003,
                0.0,
                0.05,
                "Minimum |slow-EMA slope| over slope_lookback (fraction).",
            ),
            ParamSpec(
                "pullback_lookback",
                "int",
                5,
                2,
                30,
                "Bars before the trigger to scan for an EMA touch + close-back.",
            ),
            ParamSpec(
                "min_body_pct",
                "float",
                0.5,
                0.1,
                0.95,
                "Minimum body-to-range ratio of the trigger candle.",
            ),
        ],
        confidence=3,  # default placeholder; recalibrate after first backtest pass
    ),
}


KNOWN_STRATEGIES: list[str] = list(STRATEGY_REGISTRY.keys())

# All valid strategy type labels — used for D10 confluence filtering and UI grouping.
KNOWN_STRATEGY_TYPES: list[str] = [
    "structural",
    "fib",
    "price_action",
    "candlestick",
    "flow",
    "session",
    "trend",
]

# Inverse index: type → list of strategy names (derived from STRATEGY_REGISTRY).
STRATEGY_TYPE_GROUPS: dict[str, list[str]] = {t: [] for t in KNOWN_STRATEGY_TYPES}
for _name, _spec in STRATEGY_REGISTRY.items():
    if _spec.strategy_type in STRATEGY_TYPE_GROUPS:
        STRATEGY_TYPE_GROUPS[_spec.strategy_type].append(_name)

# Strategy pairs that must not be combined in co-firing backtests because one
# embeds the other's detection logic — pairing them would double-count the same edge.
# fib_golden_zone and ote_entry both call _find_bos_swing() internally.
INCOMPATIBLE_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"fib_golden_zone", "bos"}),
        frozenset({"ote_entry", "bos"}),
    }
)


def patch_confidence_scores(updates: dict[str, dict[str, int] | int]) -> None:
    """Mutate STRATEGY_REGISTRY confidence values in-place.

    Only updates strategies that exist in the registry; unknown keys are silently skipped.
    Intended for use by the recalibration runner after computing new star ratings.
    """
    for name, stars in updates.items():
        if name in STRATEGY_REGISTRY:
            STRATEGY_REGISTRY[name].confidence = stars


# DETECTOR_REGISTRY — single source of truth for simple (OHLCV-only) detectors.
# Strategies that require extra data (smt_divergence → secondary OHLCV) are NOT
# listed here; callers handle those explicitly.  seasonality is also excluded
# (returns stats, not signals).  fibonacci_retracement is legacy (see comment
# above the spec block) — its detector still ships in
# `analytics/strategies/fibonacci_retracement.py` for tests and A/B comparison.
DETECTOR_REGISTRY: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "wick_fill": detect_wick_fills,
    "marubozu": detect_marubozu_retest,
    "orb": detect_orb_breakout,
    "liquidity_sweep": detect_liquidity_sweep,
    "fvg": detect_fvg,
    "bos": detect_market_structure,
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


__all__ = [
    "DETECTOR_REGISTRY",
    "INCOMPATIBLE_PAIRS",
    "KNOWN_STRATEGIES",
    "KNOWN_STRATEGY_TYPES",
    "STRATEGY_REGISTRY",
    "STRATEGY_TYPE_GROUPS",
    "patch_confidence_scores",
]
