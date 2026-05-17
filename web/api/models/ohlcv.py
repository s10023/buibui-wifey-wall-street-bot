"""Pydantic models for OHLCV endpoint."""

from pydantic import BaseModel, ConfigDict


class CandleRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class OhlcvResponse(BaseModel):
    candles: list[CandleRow]
