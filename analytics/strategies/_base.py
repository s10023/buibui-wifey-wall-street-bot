"""Strategy infra dataclasses + signal output schema.

Extracted from `analytics/indicators_lib.py` in strat-1. No behaviour change.
"""

from dataclasses import dataclass, field


@dataclass
class ParamSpec:
    name: str
    param_type: str  # "int" or "float"
    default: int | float
    min_val: int | float
    max_val: int | float
    description: str


@dataclass
class StrategySpec:
    name: str
    description: str
    params: list[ParamSpec] = field(default_factory=list)
    requires_funding: bool = False
    # Taxonomy group for confluence logic. One of: structural, fib, price_action,
    # candlestick, flow, session. Empty string means unclassified.
    strategy_type: str = ""
    # 1–5 quality score per TF or a single value for all TFs.
    # Use a dict with a "default" key for TF-specific ratings:
    #   {"default": 2, "4h": 4}  → 4★ on 4h, 2★ on all other TFs
    # A plain int applies to all TFs.
    confidence: dict[str, int] | int = 3
    # Optional direction-split TP multiples. When set, the directional value is used
    # instead of tp_r for that direction. Falls back to tp_r when None.
    tp_r_long: float | None = None
    tp_r_short: float | None = None

    def get_tp_r(self, direction: str) -> float:
        """Resolve effective tp_r for a given direction.

        Falls back to the combined tp_r (2.0) when no directional value is set.
        """
        if direction == "long" and self.tp_r_long is not None:
            return self.tp_r_long
        if direction == "short" and self.tp_r_short is not None:
            return self.tp_r_short
        return 2.0

    def get_confidence(self, tf: str) -> int:
        """Resolve confidence for a given timeframe.

        If confidence is a plain int, returns it directly.
        If confidence is a dict, looks up tf, then "default", then falls back to 3.
        """
        if isinstance(self.confidence, int):
            return self.confidence
        return self.confidence.get(tf, self.confidence.get("default", 3))


SIGNAL_COLUMNS: list[str] = [
    "open_time",
    "direction",
    "reason",
    "sl_price",
    "context",
    "low_volume",
    "tp_price",
]


__all__ = [
    "SIGNAL_COLUMNS",
    "ParamSpec",
    "StrategySpec",
]
