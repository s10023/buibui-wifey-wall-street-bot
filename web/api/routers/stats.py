"""Stats router — GET /api/stats/{symbol}."""

from datetime import datetime, timedelta, timezone

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from analytics.data_store import DEFAULT_DB_PATH, get_stats_cache, upsert_stats_cache
from analytics.stats_lib import (
    StatsBundle,
    WeeklyCurrentState,
    WeeklyWickPercentile,
    compute_all,
    compute_today_path,
    compute_weekly_current_state,
    compute_weekly_wick_percentile,
)
from web.api.deps import get_db, require_token
from web.api.models.stats import (
    ADRResponse,
    ConeComboResponse,
    DOWPatternRow,
    FlipRiskConditionedRow,
    HourlyExtremeRow,
    P1P2DOWRow,
    P1P2Response,
    PathConeResponse,
    SessionRow,
    StatsResponse,
    TodayPathResponse,
    WeeklyCurrentStateResponse,
    WeeklyFlipRiskConditionedResponse,
    WeeklyP1P2Response,
    WeeklyP2TimingResponse,
    WeeklyWickPercentileResponse,
)

router = APIRouter(dependencies=[Depends(require_token)])


def _wcs_to_response(wcs: WeeklyCurrentState) -> WeeklyCurrentStateResponse:
    return WeeklyCurrentStateResponse(
        current_isodow=wcs.current_isodow,
        current_dow=wcs.current_dow,
        weekly_open=wcs.weekly_open,
        current_price=wcs.current_price,
        move_pct=wcs.move_pct,
        move_bucket=wcs.move_bucket,
        low_still_ahead_conditioned=wcs.low_still_ahead_conditioned,
        high_still_ahead_conditioned=wcs.high_still_ahead_conditioned,
    )


def _wwp_to_response(wwp: WeeklyWickPercentile) -> WeeklyWickPercentileResponse:
    return WeeklyWickPercentileResponse(
        current_wick_of_adr=wwp.current_wick_of_adr,
        exceedance_pct=wwp.exceedance_pct,
        p1_direction=wwp.p1_direction,
        sample_count=wwp.sample_count,
    )


_MYT = timezone(timedelta(hours=8))


def _bundle_to_response(bundle: StatsBundle) -> StatsResponse:
    """Convert a StatsBundle dataclass into a StatsResponse Pydantic model."""
    # P1/P2
    by_dow_list = [
        P1P2DOWRow(dow=dow, p1_low_pct=pct, sample_days=bundle.p1p2.sample_days)
        for dow, pct in bundle.p1p2.by_dow.items()
    ]
    p1p2_resp = P1P2Response(
        overall_p1_low_pct=bundle.p1p2.overall_p1_low_pct,
        by_dow=by_dow_list,
        sample_days=bundle.p1p2.sample_days,
        p1_strong_pct=bundle.p1p2.p1_strong_pct,
    )

    # Hourly
    hourly_list = [
        HourlyExtremeRow(
            hour_myt=row.hour_myt,
            high_pct=row.high_pct,
            low_pct=row.low_pct,
        )
        for row in bundle.hourly.rows
    ]

    # ADR
    adr_resp = ADRResponse(
        adr_14=bundle.adr.adr_14,
        adr_30=bundle.adr.adr_30,
        today_range_pct=bundle.adr.today_range_pct,
        today_consumed_pct=bundle.adr.today_consumed_pct,
    )

    # DOW
    dow_list = [
        DOWPatternRow(
            dow=row.dow,
            avg_range_pct=row.avg_range_pct,
            bull_pct=row.bull_pct,
            sample_days=row.sample_days,
            avg_return_pct=row.avg_return_pct,
            strong_high_pct=row.strong_high_pct,
            strong_low_pct=row.strong_low_pct,
        )
        for row in bundle.dow.rows
    ]

    # Sessions
    session_list = [
        SessionRow(session=row.session, high_pct=row.high_pct, low_pct=row.low_pct)
        for row in bundle.sessions.rows
    ]

    # Weekly P1/P2
    weekly_resp = WeeklyP1P2Response(
        overall_p1_low_pct=bundle.weekly_p1p2.overall_p1_low_pct,
        low_day=bundle.weekly_p1p2.low_day,
        high_day=bundle.weekly_p1p2.high_day,
        sample_weeks=bundle.weekly_p1p2.sample_weeks,
        low_by_dow=bundle.weekly_p1p2.low_by_dow,
        high_by_dow=bundle.weekly_p1p2.high_by_dow,
    )

    # Weekly P2 timing
    p2_timing_resp = WeeklyP2TimingResponse(
        low_still_ahead_by_dow=bundle.weekly_p2_timing.low_still_ahead_by_dow,
        high_still_ahead_by_dow=bundle.weekly_p2_timing.high_still_ahead_by_dow,
        low_flip_risk_by_dow=bundle.weekly_p2_timing.low_flip_risk_by_dow,
        high_flip_risk_by_dow=bundle.weekly_p2_timing.high_flip_risk_by_dow,
    )

    # Weekly flip risk conditioned
    flip_risk_resp = WeeklyFlipRiskConditionedResponse(
        rows=[
            FlipRiskConditionedRow(
                p1_direction=r.p1_direction,
                isodow=r.isodow,
                dow_label=r.dow_label,
                flip_pct=r.flip_pct,
                sample_count=r.sample_count,
            )
            for r in bundle.weekly_flip_risk_conditioned.rows
        ]
    )

    # Path cone — all 18 direction × weekday combos (cached with the bundle)
    path_cone_resp = PathConeResponse(
        combos={
            key: ConeComboResponse(
                direction=c.direction,
                weekday=c.weekday,
                n=c.n,
                bands=c.bands,
                low_in_by=c.low_in_by,
                high_in_by=c.high_in_by,
                mae_p=c.mae_p,
                mfe_p=c.mfe_p,
                high_piv=c.high_piv,
                low_piv=c.low_piv,
            )
            for key, c in bundle.path_cone.combos.items()
        },
        total_days=bundle.path_cone.total_days,
    )

    return StatsResponse(
        symbol=bundle.symbol,
        days=bundle.days,
        computed_at_ms=bundle.computed_at_ms,
        p1p2=p1p2_resp,
        hourly_extremes=hourly_list,
        adr=adr_resp,
        dow_patterns=dow_list,
        sessions=session_list,
        weekly_p1p2=weekly_resp,
        weekly_p2_timing=p2_timing_resp,
        weekly_flip_risk_conditioned=flip_risk_resp,
        path_cone=path_cone_resp,
    )


@router.get("/stats/{symbol}", response_model=StatsResponse)
def get_stats(
    symbol: str,
    days: int = Query(default=180, ge=30, le=365),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> StatsResponse:
    """Return BrighterData-style statistical context for a symbol.

    Results are cached by (symbol, days, MYT date) — fresh once per day.
    """
    now_myt = datetime.now(tz=_MYT)
    date_str = now_myt.strftime("%Y-%m-%d")

    # Cache hit path
    cached = get_stats_cache(db, symbol, days, date_str)
    if cached is not None:
        try:
            response = StatsResponse.model_validate_json(cached)
            # Still inject live fields even on cache hit
            _inject_live_fields(db, symbol, days, response)
            return response
        except Exception:
            pass  # corrupted cache — fall through to recompute

    # Cache miss — compute
    try:
        bundle = compute_all(db, symbol, days)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    response = _bundle_to_response(bundle)

    # Write to cache via a brief RW connection (shared db param is read-only)
    try:
        with duckdb.connect(str(DEFAULT_DB_PATH)) as rw_conn:
            upsert_stats_cache(
                rw_conn, symbol, days, date_str, response.model_dump_json()
            )
    except Exception:
        pass  # never fail the response due to cache write failure

    _inject_live_fields(db, symbol, days, response)
    return response


def _inject_live_fields(
    db: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int,
    response: StatsResponse,
) -> None:
    """Inject live (never-cached) fields into a StatsResponse in-place."""
    adr_14 = response.adr.adr_14

    # Weekly current state
    try:
        wcs = compute_weekly_current_state(db, symbol, adr_14, days)
        if wcs is not None:
            response.weekly_current_state = _wcs_to_response(wcs)
    except Exception:
        pass

    # Today path overlay — today's normalized hourly closes (never cached)
    try:
        tp = compute_today_path(db, symbol)
        if tp is not None:
            response.today_path = TodayPathResponse(
                points=tp.points,
                elapsed_h=tp.elapsed_h,
                adr14_today=tp.adr14_today,
                today_open=tp.today_open,
            )
    except Exception:
        pass

    # Weekly P1 wick percentile — current week's wick rank vs history
    try:
        wwp = compute_weekly_wick_percentile(db, symbol, adr_14, days)
        response.weekly_wick_percentile = _wwp_to_response(wwp)
    except Exception:
        pass
