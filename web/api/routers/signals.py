"""Signals router — POST /api/signals and GET /api/signals/history."""

import math
from typing import Any

import duckdb
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status
from utils.binance_client import load_coins_config

from analytics.backtest_runner import detect_signals_for_strategy
from analytics.data_store import get_ohlcv, get_signals_history
from analytics.strategies import KNOWN_STRATEGIES, STRATEGY_REGISTRY
from web.api.deps import get_db, require_token
from web.api.models.signals import SignalRow, SignalsRequest, SignalsResponse

router = APIRouter(dependencies=[Depends(require_token)])


def _float_or_none(val: Any) -> float | None:
    """Coerce a value to float, returning None for None/NaN."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _confidence_or_default(val: Any) -> int:
    """Coerce confidence to int, defaulting to 3 for None/NaN/non-numeric."""
    f = _float_or_none(val)
    return 3 if f is None else int(f)


@router.post("/signals", response_model=SignalsResponse)
def run_signals(
    body: SignalsRequest,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> SignalsResponse:
    """Run one or more signal detectors on historical OHLCV data."""
    for strat in body.strategies:
        if strat not in KNOWN_STRATEGIES or strat == "seasonality":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown or unsupported strategy '{strat}'.",
            )

    ohlcv = get_ohlcv(db, body.symbol, body.timeframe, body.start_ms, body.end_ms)
    if ohlcv.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No OHLCV data for {body.symbol} {body.timeframe}.",
        )

    try:
        coins = load_coins_config()
    except Exception:
        coins = {}

    all_signals: list[pd.DataFrame] = []
    for strat in body.strategies:
        secondary_symbol: str | None = None
        if strat == "smt_divergence":
            secondary_symbol = coins.get(body.symbol, {}).get("smt_secondary")
            if secondary_symbol is None:
                continue  # skip silently — no secondary configured

        signals_df = detect_signals_for_strategy(
            db,
            ohlcv,
            body.symbol,
            body.timeframe,
            strat,
            body.start_ms,
            body.end_ms,
            secondary_symbol,
        )
        if signals_df is None or signals_df.empty:
            continue

        signals_df = signals_df.copy()
        signals_df["strategy"] = strat
        spec = STRATEGY_REGISTRY.get(strat)
        signals_df["confidence"] = spec.get_confidence(body.timeframe) if spec else 3
        for col, default in (
            ("reason", strat),
            ("context", ""),
            ("sl_price", 0.0),
            ("entry_price", None),
        ):
            if col not in signals_df.columns:
                signals_df[col] = default

        all_signals.append(signals_df)

    if not all_signals:
        return SignalsResponse(signals=[])

    merged = pd.concat(all_signals, ignore_index=True).sort_values("open_time")

    rows = [
        SignalRow(
            open_time=int(sig["open_time"]),
            direction=str(sig["direction"]),
            strategy=str(sig["strategy"]),
            reason=str(sig.get("reason", sig["strategy"])),
            sl_price=float(sig.get("sl_price", 0.0)),
            entry_price=_float_or_none(sig.get("entry_price")),
            confidence=_confidence_or_default(sig.get("confidence")),
            context=str(sig.get("context", "")),
        )
        for _, sig in merged.iterrows()
    ]
    return SignalsResponse(signals=rows)


@router.get("/signals/history", response_model=SignalsResponse)
def get_signals_history_endpoint(
    symbol: str = Query(...),
    timeframe: str = Query(...),
    start_ms: int = Query(...),
    end_ms: int = Query(...),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> SignalsResponse:
    """Return persisted signals from DB for a given symbol/timeframe window.

    Reads from the signals table populated by the signal-watch daemon.
    No live scan is performed — returns instantly from DB.
    """
    df = get_signals_history(db, symbol, timeframe, start_ms, end_ms)
    if df.empty:
        return SignalsResponse(signals=[])

    rows = [
        SignalRow(
            open_time=int(sig["open_time"]),
            direction=str(sig["direction"]),
            strategy=str(sig["strategy"]),
            reason=str(sig["reason"])
            if sig["reason"] is not None
            else str(sig["strategy"]),
            sl_price=float(sig["sl_price"]) if sig["sl_price"] is not None else 0.0,
            entry_price=_float_or_none(sig.get("entry_price")),
            confidence=int(sig["confidence"]) if sig["confidence"] is not None else 3,
            context="",
        )
        for _, sig in df.iterrows()
    ]
    return SignalsResponse(signals=rows)
