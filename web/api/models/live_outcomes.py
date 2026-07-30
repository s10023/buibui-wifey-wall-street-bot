"""Pydantic models for the live-outcomes router."""

from pydantic import BaseModel


class LiveOutcomesRollupModel(BaseModel):
    total_rows: int
    resolved: int
    open: int
    open_no_tp: int
    wins: int
    losses: int
    expired: int


class LiveOutcomeCellModel(BaseModel):
    strategy: str
    tf: str
    direction: str
    n: int
    wins: int
    losses: int
    expired: int
    win_rate: float | None
    avg_r: float | None


class LiveOutcomeStrategyModel(BaseModel):
    strategy: str
    n: int
    wins: int
    losses: int
    expired: int
    win_rate: float | None
    avg_r: float | None


class LiveOutcomeSymbolModel(BaseModel):
    symbol: str
    n: int


class LiveOpenPositionModel(BaseModel):
    signal_id: str
    symbol: str
    strategy: str
    tf: str
    direction: str
    fired_at_ms: int
    entry_price: float | None
    sl_price: float | None
    tp_price: float | None
    mark: float | None
    unrealized_r: float | None  # gross R at the mark — see the card help text
    dist_sl_pct: float | None
    dist_tp_pct: float | None


class LiveOpenPositionsResponse(BaseModel):
    symbol: str | None
    marks_ok: bool
    marked_at_ms: int
    positions: list[LiveOpenPositionModel]


class LiveOutcomesResponse(BaseModel):
    days: int
    min_n: int
    rollup: LiveOutcomesRollupModel
    cells: list[LiveOutcomeCellModel]
    by_strategy: list[LiveOutcomeStrategyModel]
    symbols: list[LiveOutcomeSymbolModel]
