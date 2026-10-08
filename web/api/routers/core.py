"""Survival-core router — GET /api/core-state.

Read-only view of OV-1 × VM (``docs/north-star.md`` § Frozen allows this panel,
#430). It returns what the digest prints: the same ``completed_closes`` →
``core_state`` call and the same ``missing_sessions`` staleness rule, with no
rule of its own.
"""

from datetime import UTC, datetime

import duckdb
from fastapi import APIRouter, Depends, HTTPException, status

from analytics.overlay.frame import load_gspc_close
from analytics.overlay.live import completed_closes, core_state, missing_sessions
from analytics.trading_calendar import nyse_sessions
from web.api.deps import get_db, require_token
from web.api.models.core import CoreStateResponse

router = APIRouter(dependencies=[Depends(require_token)])


def _now() -> datetime:
    """Wall clock in UTC; a seam so tests can pin the session boundary."""
    return datetime.now(UTC)


@router.get("/core-state", response_model=CoreStateResponse)
def get_core_state(
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> CoreStateResponse:
    """OV-1 × VM at the latest completed ``^GSPC`` close.

    404 when the DB holds too few ``^GSPC`` closes for either leg, since no
    watchlist carries the index and only ``make core-sync`` fetches it.
    """
    now = _now()
    try:
        state = core_state(completed_closes(load_gspc_close(db), now))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"^GSPC history too short ({e}); run make core-sync",
        ) from None
    return CoreStateResponse(
        as_of=state.as_of,
        close=state.close,
        sma=state.sma,
        ma_in=state.ma_in,
        sessions_in_state=state.sessions_in_state,
        flip_level=state.flip_level,
        sigma_ann=state.sigma_ann,
        vm_weight=state.vm_weight,
        exposure=state.exposure,
        sma_distance=state.sma_distance,
        flip_distance=state.flip_distance,
        missing_sessions=len(missing_sessions(state.as_of, now, nyse_sessions)),
    )
