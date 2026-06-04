"""Live-outcomes router — GET /api/live-outcomes.

Cross-symbol roll-up of the live ``signal_alert_outcomes`` ledger. Unlike the
per-symbol /api/stats/{symbol} bundle this aggregates across all symbols and is
never cached (the ledger changes as the daemon resolves trades).
"""

import duckdb
from fastapi import APIRouter, Depends, Query

from analytics.stats import compute_live_outcomes
from web.api.deps import get_db, require_token
from web.api.models.live_outcomes import (
    LiveOutcomeCellModel,
    LiveOutcomesResponse,
    LiveOutcomesRollupModel,
    LiveOutcomeStrategyModel,
)

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/live-outcomes", response_model=LiveOutcomesResponse)
def get_live_outcomes(
    days: int = Query(default=30, ge=0, le=365),
    min_n: int = Query(default=1, ge=1, le=100),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> LiveOutcomesResponse:
    """Return the live signal-alert outcome roll-up + per-cell breakdowns.

    ``days`` windows the per-cell / per-strategy tables (0 = all time); the
    roll-up is always all-time. Empty ledger returns a zero roll-up, not 404.
    """
    result = compute_live_outcomes(db, days=days, min_n=min_n)
    return LiveOutcomesResponse(
        days=result.days,
        min_n=result.min_n,
        rollup=LiveOutcomesRollupModel(
            total_rows=result.rollup.total_rows,
            resolved=result.rollup.resolved,
            open=result.rollup.open,
            open_no_tp=result.rollup.open_no_tp,
            wins=result.rollup.wins,
            losses=result.rollup.losses,
            expired=result.rollup.expired,
        ),
        cells=[
            LiveOutcomeCellModel(
                strategy=c.strategy,
                tf=c.tf,
                direction=c.direction,
                n=c.n,
                wins=c.wins,
                losses=c.losses,
                expired=c.expired,
                win_rate=c.win_rate,
                avg_r=c.avg_r,
            )
            for c in result.cells
        ],
        by_strategy=[
            LiveOutcomeStrategyModel(
                strategy=s.strategy,
                n=s.n,
                win_rate=s.win_rate,
                avg_r=s.avg_r,
            )
            for s in result.by_strategy
        ],
    )
