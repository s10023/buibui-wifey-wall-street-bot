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
    win_rate: float | None
    avg_r: float | None


class LiveOutcomesResponse(BaseModel):
    days: int
    min_n: int
    rollup: LiveOutcomesRollupModel
    cells: list[LiveOutcomeCellModel]
    by_strategy: list[LiveOutcomeStrategyModel]
