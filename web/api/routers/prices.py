"""Prices router — GET /api/prices."""

from binance.client import Client
from fastapi import APIRouter, Depends, HTTPException, status
from utils.binance_client import load_coins_config

from monitor.price_lib import get_price_changes
from web.api.deps import get_client, require_token
from web.api.models.prices import PriceRow, PricesResponse

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/prices", response_model=PricesResponse)
def get_prices(client: Client = Depends(get_client)) -> PricesResponse:
    """Return latest price changes for all configured symbols."""
    try:
        coins = load_coins_config()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to load coins config: {exc}",
        ) from exc

    # telegram=True returns plain text (no ANSI color codes)
    # row layout: [symbol, last_price, 15m%, 1h%, 4h%, asia%, 24h%]
    table, _invalid = get_price_changes(client, list(coins.keys()), telegram=True)

    rows = [
        PriceRow(
            symbol=str(row[0]),
            last_price=str(row[1]),
            change_15m=str(row[2]),
            change_1h=str(row[3]),
            change_4h=str(row[4]),
            change_asia=str(row[5]),
            change_24h=str(row[6]),
        )
        for row in table
    ]
    return PricesResponse(prices=rows)
