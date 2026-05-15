"""Positions router — GET /api/positions."""

import re
from typing import Any

from binance.client import Client
from fastapi import APIRouter, Depends, HTTPException, status
from utils.binance_client import load_coins_config

from monitor.position_lib import fetch_open_positions
from web.api.deps import get_client, require_token
from web.api.models.positions import PositionRow, PositionsResponse

router = APIRouter(dependencies=[Depends(require_token)])

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_NULL_TOKENS = ("-", "+", "", "None")


def _strip_ansi(s: Any) -> str:
    """Remove ANSI escape codes from a string."""
    if not isinstance(s, str):
        return str(s)
    return _ANSI_RE.sub("", s)


def _parse_float_or_none(val: Any) -> float | None:
    """Parse a value as float, returning None for '-' or unparseable.

    Strips ANSI codes and formatting characters that colorize() adds:
    thousand-separator commas, trailing '%', and '$' signs.
    """
    s = _strip_ansi(val).replace(",", "").replace("%", "").replace("$", "").strip()
    if s in _NULL_TOKENS:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_int_or_none(val: Any) -> int | None:
    """Parse a value as int, returning None for '-' or unparseable."""
    s = _strip_ansi(val)
    if s in _NULL_TOKENS:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _text_or_none(val: Any) -> str | None:
    """Strip ANSI; return None if the value is a dash placeholder."""
    return _strip_ansi(val) if val != "-" else None


def row_to_position(row: Any) -> PositionRow:
    """Build a PositionRow from one fetch_open_positions table row.

    Row layout — 0-12: display columns (ANSI-colored strings), 13: pnl_pct float
    (sort key), 14: sl_risk_usd float (sort key), 15: tp_price, 16: liq_price,
    17: position_side, 18: margin_type.
    """
    return PositionRow(
        symbol=str(row[0]),
        side=_strip_ansi(row[1]),
        leverage=_parse_int_or_none(row[2]),
        entry_price=_parse_float_or_none(row[3]),
        mark_price=_parse_float_or_none(row[4]),
        margin=_parse_float_or_none(row[5]),
        notional=_parse_float_or_none(row[6]),
        pnl=_parse_float_or_none(row[7]),
        pnl_pct=_parse_float_or_none(row[8]),
        risk_pct=_text_or_none(row[9]),
        sl_price=_parse_float_or_none(row[10]),
        sl_size=_text_or_none(row[11]),
        sl_usd=_text_or_none(row[12]),
        tp_price=row[15] if len(row) > 15 else None,
        liq_price=row[16] if len(row) > 16 else None,
        position_side=str(row[17]) if len(row) > 17 else "BOTH",
        margin_type=str(row[18]) if len(row) > 18 else None,
    )


@router.get("/positions", response_model=PositionsResponse)
def get_positions(client: Client = Depends(get_client)) -> PositionsResponse:
    """Fetch and return open futures positions."""
    try:
        coins = load_coins_config()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to load coins config: {exc}",
        ) from exc

    try:
        rows, total_risk_usd, wallet_balance, unrealized_pnl, available_balance = (
            fetch_open_positions(client, coins, list(coins.keys()), hide_empty=True)
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return PositionsResponse(
        positions=[row_to_position(row) for row in rows],
        wallet_balance=wallet_balance,
        unrealized_pnl=unrealized_pnl,
        available_balance=available_balance,
        total_risk_usd=total_risk_usd,
    )
