"""Pydantic model for the survival-core router."""

from datetime import date

from pydantic import BaseModel


class CoreStateResponse(BaseModel):
    """``analytics.overlay.live.CoreState`` plus its derived properties and staleness."""

    as_of: date
    close: float
    sma: float
    ma_in: bool
    sessions_in_state: int
    flip_level: float
    sigma_ann: float
    vm_weight: float
    exposure: float
    sma_distance: float
    flip_distance: float
    missing_sessions: int
