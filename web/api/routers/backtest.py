"""Backtest router — POST /api/backtest."""

import datetime
from typing import Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from analytics.backtest_lib import BacktestResult, run_backtest
from analytics.backtest_runner import detect_signals_for_strategy
from analytics.data_store import (
    get_ohlcv,
    list_backtest_runs,
    upsert_backtest_run,
    upsert_backtest_trades,
)
from analytics.digest_lib import QUERY_NAMES, DigestScope, run_digest
from analytics.strategies import KNOWN_STRATEGIES
from utils.config_validation import load_universe_policy
from web.api.deps import get_db, require_token
from web.api.models.backtest import (
    BacktestRequest,
    BacktestResponse,
    BacktestRunSummary,
    TradeModel,
)

router = APIRouter(dependencies=[Depends(require_token)])

_MS_PER_DAY = 24 * 3_600 * 1_000


def _resolve_window_ms(body: BacktestRequest) -> tuple[int, int]:
    """Return (start_ms, end_ms) for the backtest window."""
    end_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    if body.since is not None:
        since_dt = datetime.datetime.strptime(body.since, "%Y-%m-%d").replace(
            tzinfo=datetime.UTC
        )
        return int(since_dt.timestamp() * 1000), end_ms
    return end_ms - body.days * _MS_PER_DAY, end_ms


def _result_to_response(result: BacktestResult) -> BacktestResponse:
    """BacktestResult uses cached_property, so build the response explicitly."""
    return BacktestResponse(
        symbol=result.symbol,
        timeframe=result.timeframe,
        strategy=result.strategy,
        total_trades=len(result.trades),
        closed_trades=len(result.closed_trades),
        win_count=result.win_count,
        loss_count=result.loss_count,
        win_rate=result.win_rate,
        avg_r=result.avg_r,
        total_r=result.total_r,
        max_drawdown_r=result.max_drawdown_r,
        recovery_factor=result.recovery_factor,
        long_closed_trades=len(result.long_closed_trades),
        long_win_count=result.long_win_count,
        long_win_rate=result.long_win_rate,
        long_avg_r=result.long_avg_r,
        long_total_r=result.long_total_r,
        short_closed_trades=len(result.short_closed_trades),
        short_win_count=result.short_win_count,
        short_win_rate=result.short_win_rate,
        short_avg_r=result.short_avg_r,
        short_total_r=result.short_total_r,
        trades=[
            TradeModel.model_validate(t, from_attributes=True) for t in result.trades
        ],
    )


@router.get("/backtest/runs", response_model=list[BacktestRunSummary])
def get_backtest_runs(
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> list[BacktestRunSummary]:
    """Return all saved backtest runs, newest first."""
    df = list_backtest_runs(db)
    return [BacktestRunSummary.model_validate(row) for row in df.to_dict("records")]


@router.get("/backtest/analysis")
def get_backtest_analysis(
    request: Request,
    query: str = Query(..., description=f"One of: {', '.join(QUERY_NAMES)}"),
    min_trades: int | None = Query(
        None, ge=1, description="Minimum closed trades (default: 5, or 3 for co_firing)"
    ),
    top_n: int = Query(20, ge=1, le=100, description="Max rows for combos query"),
    use_config: bool = Query(
        False,
        description="Scope results to the active server config (day_filter, fee_pct, symbols, per-TF min_trades)",
    ),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> dict[str, Any]:
    """Run a pre-canned aggregation query over backtest_runs and return {columns, rows}."""
    if query not in QUERY_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown query '{query}'. Valid: {', '.join(QUERY_NAMES)}",
        )
    scope: DigestScope | None = None
    if use_config:
        cfg = getattr(request.app.state, "active_config", None)
        if cfg is not None:
            scope = DigestScope(
                day_filter=cfg.day_filter if cfg.day_filter != "off" else None,
                fee_pct=cfg.fee_pct if cfg.fee_pct > 0 else None,
                symbols=cfg.symbols or [],
                min_trades=cfg.min_trades,
                min_trades_per_tf=cfg.min_trades_per_tf,
            )
    return run_digest(db, query, min_trades=min_trades, top_n=top_n, scope=scope)


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest_endpoint(
    body: BacktestRequest,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> BacktestResponse:
    """Run a backtest for a symbol/timeframe/strategy combination."""
    if body.strategy not in KNOWN_STRATEGIES or body.strategy == "seasonality":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown or unsupported strategy '{body.strategy}'.",
        )

    start_ms, end_ms = _resolve_window_ms(body)
    ohlcv = get_ohlcv(db, body.symbol, body.timeframe, start_ms, end_ms)
    if ohlcv.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No OHLCV data for {body.symbol} {body.timeframe}. Run 'analytics backfill' first.",
        )

    signals = detect_signals_for_strategy(
        db,
        ohlcv,
        body.symbol,
        body.timeframe,
        body.strategy,
        start_ms,
        end_ms,
    )
    if signals is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required data for strategy '{body.strategy}'.",
        )

    result = run_backtest(
        ohlcv,
        signals,
        body.symbol,
        body.timeframe,
        body.strategy,
        body.sl_pct,
        body.tp_r,
        body.fee_pct,
    )

    run_id = upsert_backtest_run(
        db,
        result,
        body.days,
        start_ms,
        end_ms,
        body.sl_pct,
        body.tp_r,
        body.fee_pct,
        "off",
        volume_suppress=None,
        universe_policy=load_universe_policy().to_json(),
        origin="web",
    )
    upsert_backtest_trades(db, result, run_id)
    return _result_to_response(result)
