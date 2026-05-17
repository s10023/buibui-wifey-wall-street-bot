"""OHLCV router — GET /api/ohlcv."""

import duckdb
from fastapi import APIRouter, Depends

from analytics.data_store import get_ohlcv
from web.api.deps import get_db, require_token
from web.api.models.ohlcv import CandleRow, OhlcvResponse

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/ohlcv", response_model=OhlcvResponse)
def get_ohlcv_endpoint(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> OhlcvResponse:
    """Return OHLCV candles for a symbol/timeframe range."""
    df = get_ohlcv(db, symbol, timeframe, start_ms, end_ms)
    candles = [CandleRow.model_validate(row) for row in df.to_dict("records")]
    return OhlcvResponse(candles=candles)
