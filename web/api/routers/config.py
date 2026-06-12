"""Config router — GET /api/config, GET /api/strategies, GET /api/active-config."""

from dataclasses import asdict
from typing import Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from analytics.data_store import get_confidence_ratings
from analytics.strategies import STRATEGY_REGISTRY
from utils.config_validation import load_stocks_config, load_universe_policy
from web.api.deps import get_db, require_token
from web.api.models.active_config import ActiveConfigResponse, UniversePolicyResponse

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/config")
def get_config() -> dict[str, Any]:
    """Return per-symbol configuration from stocks.json."""
    try:
        stocks = load_stocks_config()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to load stocks config: {exc}",
        ) from exc
    return {symbol: dict(cfg) for symbol, cfg in stocks.items()}


@router.get("/strategies")
def get_strategies(
    request: Request,
    config: str | None = Query(default=None),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> dict[str, Any]:
    """Return all strategy specs from the registry.

    If ?config=<name> is provided, confidence values are overridden with per-config
    DB ratings. Falls back to the server's active config (set via --config on startup)
    when the query param is absent.
    """
    effective_config = config or getattr(request.app.state, "config_name", None)
    specs = {name: asdict(spec) for name, spec in STRATEGY_REGISTRY.items()}
    if effective_config:
        overrides = get_confidence_ratings(db, effective_config)
        for strategy, tf_stars in overrides.items():
            if strategy in specs:
                specs[strategy]["confidence"] = tf_stars
    return specs


@router.get("/universe-policy")
def get_universe_policy() -> UniversePolicyResponse:
    """Return the active universe policy + survivorship caveat (Phase 0.1).

    n_symbols reflects the current watchlist size; None when stocks.json
    is unreadable (the caveat is still worth surfacing without a count).
    """
    policy = load_universe_policy()
    try:
        n_symbols: int | None = len(load_stocks_config())
    except Exception:
        n_symbols = None
    return UniversePolicyResponse(
        scope=policy.scope,
        as_of=policy.as_of,
        survivorship_note=policy.survivorship_note,
        n_symbols=n_symbols,
    )


@router.get("/active-config")
def get_active_config(request: Request) -> ActiveConfigResponse:
    """Return the active TOML config the server was started with.

    Returns a default/empty response when no --config was passed on startup.
    The UI uses this to auto-populate defaults across all tabs.
    """
    cfg: ActiveConfigResponse | None = getattr(request.app.state, "active_config", None)
    if cfg is None:
        return ActiveConfigResponse(
            config_name=None,
            symbols=None,
            timeframes=["4h"],
            strategies=None,
            day_filter="off",
            tp_r=2.0,
            sl_pct=0.02,
            fee_pct=0.0,
            min_sl_pct=0.0,
            adr_suppress_threshold=None,
            strategy_params={},
        )
    return cfg
