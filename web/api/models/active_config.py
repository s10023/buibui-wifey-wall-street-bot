"""Pydantic models for GET /api/active-config and GET /api/universe-policy."""

from pydantic import BaseModel


class UniversePolicyResponse(BaseModel):
    scope: str
    as_of: str
    survivorship_note: str
    n_symbols: int | None = None


class StrategyParamsModel(BaseModel):
    tp_r: float | None = None
    sl_pct: float | None = None
    tp_r_per_tf: dict[str, float] = {}


class ActiveConfigResponse(BaseModel):
    config_name: str | None
    symbols: list[str] | None
    timeframes: list[str]
    strategies: list[str] | None
    day_filter: str
    tp_r: float
    sl_pct: float
    fee_pct: float
    min_sl_pct: float
    adr_suppress_threshold: float | None
    strategy_params: dict[str, StrategyParamsModel]
    min_trades: int = 20
    min_trades_per_tf: dict[str, int] = {}
