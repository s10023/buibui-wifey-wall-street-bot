"""Phase 0.4 realistic equity cost model.

Replaces the flat crypto taker-fee abstraction with a decomposed equity cost
stack: half-spread by liquidity bucket + square-root market impact + short
borrow over the holding period + commission. Pure math over trade/context
primitives — no DB, no engine import, no module-level side effects.

Cost is expressed in R (1R = amount risked at the stop). Position is sized to
risk 1R over ``risk = |entry - sl|``, so a cost ``f`` (fraction of notional)
converts to R as ``f * entry_price / risk`` — the same conversion the
engine's flat-fee path uses.

Defaults are deliberately conservative (overestimate cost):

- Spread buckets by trailing dollar ADV: <$5M -> 20 bps half-spread,
  $5M-$50M -> 8, $50M-$500M -> 3, >$500M -> 1. Unknown ADV -> widest bucket.
- Impact: sqrt law ``impact_coef * sigma_daily * sqrt(notional / ADV)``,
  charged per leg. Unknown ADV/sigma -> 0 (the spread bucket already
  defaulted wide).
- Borrow (shorts only): flat 1%/yr on notional over the holding period in
  calendar days, charged once. Free per-symbol borrow data is scarce —
  documented constraint, parameterized for a later feed.
- Commission: 0 bps (US retail).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

#: Closed US-equity bars per trading day for the supported timeframes.
#: 1h = 7 yfinance session bars; 4h = 2 resampled bars; 1wk = 1/5 day.
#: Used to scale per-bar dollar volume to daily ADV and per-bar return
#: stdev to daily sigma. Unknown TFs fall back to 1.0 (treat bars as days).
BARS_PER_DAY: dict[str, float] = {"1h": 7.0, "4h": 2.0, "1d": 1.0, "1wk": 0.2}

_MS_PER_DAY = 86_400_000.0
_DAYS_PER_YEAR = 365.0


def bars_per_day_for_tf(timeframe: str) -> float:
    """Closed bars per trading day for a timeframe; unknown TFs -> 1.0."""
    return BARS_PER_DAY.get(timeframe, 1.0)


class TradeLike(Protocol):
    """Structural view of engine.Trade — keeps this module engine-free."""

    direction: str
    entry_price: float
    sl_price: float
    entry_time: int
    exit_time: int | None


@dataclass(frozen=True)
class CostContext:
    """Per-trade market context, built causally from bars up to the signal bar.

    None fields mean "not computable from available history" — cost_r then
    falls back conservatively (widest spread bucket) or to zero (impact).
    """

    adv_dollars: float | None = None
    sigma_daily: float | None = None


@dataclass(frozen=True)
class CostBreakdown:
    """Per-trade cost decomposition, each component in R."""

    spread_r: float = 0.0
    impact_r: float = 0.0
    borrow_r: float = 0.0
    commission_r: float = 0.0

    @property
    def total_r(self) -> float:
        return self.spread_r + self.impact_r + self.borrow_r + self.commission_r


@dataclass(frozen=True)
class CostModel:
    """Equity cost-model parameters. Frozen so a single instance can be
    shared across every Trade of a backtest run.

    adv_thresholds: ascending dollar-ADV bucket edges. An ADV exactly at a
    threshold falls in the more liquid bucket.
    half_spread_bps: one entry per bucket, index 0 = least liquid; must have
    exactly ``len(adv_thresholds) + 1`` entries.
    """

    adv_thresholds: tuple[float, ...] = (5e6, 5e7, 5e8)
    half_spread_bps: tuple[float, ...] = (20.0, 8.0, 3.0, 1.0)
    impact_coef: float = 1.0
    notional_usd: float = 10_000.0
    borrow_rate_annual: float = 0.01
    commission_bps: float = 0.0
    adv_window_days: float = 20.0

    def __post_init__(self) -> None:
        if len(self.half_spread_bps) != len(self.adv_thresholds) + 1:
            raise ValueError(
                "half_spread_bps must have exactly one more entry than "
                "adv_thresholds (one spread per bucket)"
            )
        if any(t <= 0.0 for t in self.adv_thresholds):
            raise ValueError("adv_thresholds must be positive")
        if any(
            b <= a
            for a, b in zip(self.adv_thresholds, self.adv_thresholds[1:], strict=False)
        ):
            raise ValueError("adv_thresholds must be strictly ascending")
        if any(s < 0.0 for s in self.half_spread_bps):
            raise ValueError("half_spread_bps must be non-negative")
        if self.impact_coef < 0.0:
            raise ValueError("impact_coef must be non-negative")
        if self.notional_usd <= 0.0:
            raise ValueError("notional_usd must be positive")
        if self.borrow_rate_annual < 0.0:
            raise ValueError("borrow_rate_annual must be non-negative")
        if self.commission_bps < 0.0:
            raise ValueError("commission_bps must be non-negative")
        if self.adv_window_days <= 0.0:
            raise ValueError("adv_window_days must be positive")

    def spread_bucket(self, adv_dollars: float | None) -> int:
        """Liquidity bucket index; 0 = least liquid (widest spread).

        Unknown or non-positive ADV falls back to bucket 0 — conservative.
        """
        if adv_dollars is None or adv_dollars <= 0.0:
            return 0
        return int(np.searchsorted(self.adv_thresholds, adv_dollars, side="right"))

    def cost_breakdown(
        self, trade: TradeLike, ctx: CostContext | None
    ) -> CostBreakdown:
        """Decomposed per-trade cost in R. Zero when risk is zero (the
        engine's pnl_r already returns None for those trades)."""
        risk = abs(trade.entry_price - trade.sl_price)
        if risk == 0.0:
            return CostBreakdown()
        notional_to_r = trade.entry_price / risk
        adv = ctx.adv_dollars if ctx is not None else None
        sigma = ctx.sigma_daily if ctx is not None else None

        half_spread_frac = self.half_spread_bps[self.spread_bucket(adv)] / 10_000.0
        spread_r = 2.0 * half_spread_frac * notional_to_r

        impact_r = 0.0
        if adv is not None and adv > 0.0 and sigma is not None and sigma > 0.0:
            impact_frac = self.impact_coef * sigma * math.sqrt(self.notional_usd / adv)
            impact_r = 2.0 * impact_frac * notional_to_r

        borrow_r = 0.0
        if trade.direction == "short" and trade.exit_time is not None:
            holding_days = max(0.0, (trade.exit_time - trade.entry_time) / _MS_PER_DAY)
            borrow_r = (
                self.borrow_rate_annual
                * (holding_days / _DAYS_PER_YEAR)
                * notional_to_r
            )

        commission_r = 2.0 * (self.commission_bps / 10_000.0) * notional_to_r
        return CostBreakdown(
            spread_r=spread_r,
            impact_r=impact_r,
            borrow_r=borrow_r,
            commission_r=commission_r,
        )

    def cost_r(self, trade: TradeLike, ctx: CostContext | None) -> float:
        """Total per-trade cost in R (spread + impact + borrow + commission)."""
        return self.cost_breakdown(trade, ctx).total_r

    def to_json(self) -> str:
        """Canonical JSON of the parameters. Stamps backtest_runs rows and
        feeds the run_id hash, so it must be deterministic."""
        return json.dumps(
            {
                "adv_thresholds": list(self.adv_thresholds),
                "half_spread_bps": list(self.half_spread_bps),
                "impact_coef": self.impact_coef,
                "notional_usd": self.notional_usd,
                "borrow_rate_annual": self.borrow_rate_annual,
                "commission_bps": self.commission_bps,
                "adv_window_days": self.adv_window_days,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def build_cost_context(
    closes: np.ndarray[Any, np.dtype[np.float64]],
    volumes: np.ndarray[Any, np.dtype[np.float64]],
    idx: int,
    bars_per_day: float,
    window_bars: int,
) -> CostContext:
    """Trailing-window dollar ADV + daily sigma ending at bar ``idx`` (inclusive).

    Causal by construction: uses only bars [idx-window_bars+1, idx]. The
    signal bar's close/volume are known once the bar closes; the engine
    enters at the NEXT bar's open.

    ADV = mean(close x volume) x bars_per_day (scales per-bar dollar volume
    to a daily figure). sigma_daily = population stdev of per-bar simple
    returns x sqrt(bars_per_day). Fields are None when not computable
    (too little history, zero volume, non-finite values) — cost_breakdown
    then falls back conservatively.
    """
    lo = max(0, idx - window_bars + 1)
    c = closes[lo : idx + 1]
    v = volumes[lo : idx + 1]

    adv: float | None = None
    if len(c) >= 1:
        adv = float(np.mean(c * v)) * bars_per_day
        if not math.isfinite(adv) or adv <= 0.0:
            adv = None

    sigma: float | None = None
    if len(c) >= 3:
        with np.errstate(divide="ignore", invalid="ignore"):
            rets = np.diff(c) / c[:-1]
        sigma = float(np.std(rets)) * math.sqrt(bars_per_day)
        if not math.isfinite(sigma):
            sigma = None

    return CostContext(adv_dollars=adv, sigma_daily=sigma)


def cost_model_from_toml(raw: object) -> CostModel | None:
    """Parse a ``[backtest.cost_model]`` TOML table into a CostModel.

    Returns None when the block is absent or ``enabled`` is falsy — the
    engine then keeps the legacy flat-fee path (byte-identical default).
    Raises ValueError on a malformed block.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            "backtest.cost_model must be a TOML table (e.g. [backtest.cost_model])"
        )
    if not raw.get("enabled", False):
        return None
    defaults = CostModel()
    try:
        return CostModel(
            adv_thresholds=tuple(
                float(x) for x in raw.get("adv_thresholds", defaults.adv_thresholds)
            ),
            half_spread_bps=tuple(
                float(x) for x in raw.get("half_spread_bps", defaults.half_spread_bps)
            ),
            impact_coef=float(raw.get("impact_coef", defaults.impact_coef)),
            notional_usd=float(raw.get("notional_usd", defaults.notional_usd)),
            borrow_rate_annual=float(
                raw.get("borrow_rate_annual", defaults.borrow_rate_annual)
            ),
            commission_bps=float(raw.get("commission_bps", defaults.commission_bps)),
            adv_window_days=float(raw.get("adv_window_days", defaults.adv_window_days)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid [backtest.cost_model] block: {exc}") from exc
