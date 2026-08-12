"""FastAPI application — lifespan, CORS, health endpoint, router mounts."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.db_retry import connect_with_retry, is_lock_conflict
from web.api.routers import (
    backtest,
    config,
    fib,
    live_outcomes,
    ohlcv,
    signals,
    stats,
    zones,
)

_UI_DIST = (Path(__file__).resolve().parent.parent / "ui" / "dist").resolve()


def _load_active_config(config_path: str) -> None:
    """Parse TOML config and populate app.state with the config name and structured data.

    Called once at startup. Logs a warning and leaves state empty on any failure.
    """
    from analytics.signal_config import load_signal_config
    from web.api.models.active_config import ActiveConfigResponse, StrategyParamsModel

    try:
        sw_cfg = load_signal_config(config_path)
        config_name = Path(config_path).stem
        app.state.active_config = ActiveConfigResponse(
            config_name=config_name,
            symbols=sw_cfg.symbols,
            timeframes=sw_cfg.timeframes,
            strategies=sw_cfg.strategies,
            day_filter=sw_cfg.day_filter,
            tp_r=sw_cfg.tp_r,
            sl_pct=sw_cfg.sl_pct,
            fee_pct=sw_cfg.backtest.fee_pct,
            min_sl_pct=sw_cfg.backtest.min_sl_pct,
            adr_suppress_threshold=sw_cfg.bias.adr_suppress_threshold,
            min_trades=sw_cfg.backtest.min_trades,
            min_trades_per_tf=sw_cfg.backtest.min_trades_per_tf,
            strategy_params={
                name: StrategyParamsModel(
                    tp_r=override.tp_r,
                    sl_pct=override.sl_pct,
                    tp_r_per_tf=override.tp_r_per_tf,
                )
                for name, override in sw_cfg.strategy_params.items()
            },
        )
        app.state.config_name = config_name
        logging.info("Active config loaded: %s", config_name)
    except Exception:
        logging.warning(
            "Failed to load active config from %s", config_path, exc_info=True
        )
        app.state.config_name = None
        app.state.active_config = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open DB (brief RW for schema, then read-only) on startup."""
    # Brief RW open to ensure schema is initialised, waiting out whatever else
    # holds the lock (`make go-live`, `make db-update`) rather than skipping.
    #
    # The old comment blamed "the signal-watch daemon" — there is no such daemon
    # in this fork; those units belong to the crypto parent. Worse, the bare
    # `except duckdb.IOException: pass` swallowed EVERY I/O error, so a missing
    # or unreadable database started the API with no schema and no complaint.
    # Only a lock conflict is survivable here; anything else must surface.
    try:
        with connect_with_retry(DEFAULT_DB_PATH) as rw_conn:
            init_schema(rw_conn)
    except duckdb.IOException as e:
        if not is_lock_conflict(e):
            raise
        logging.warning(
            "analytics.db is locked; starting read-only without a schema check"
        )

    app.state.db_path = str(DEFAULT_DB_PATH)
    app.state.config_name = None
    app.state.active_config = None

    config_path = os.environ.get("WIFEY_CONFIG")
    if config_path:
        _load_active_config(config_path)
    yield


app = FastAPI(title="Wifey Web API", version="1.0.0", lifespan=lifespan)

_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (
    config,
    ohlcv,
    fib,
    signals,
    backtest,
    stats,
    zones,
    live_outcomes,
):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Health check — no auth required."""
    return {"status": "ok"}


if _UI_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_UI_DIST / "assets")),
        name="ui-assets",
    )

    @app.get("/", include_in_schema=False)
    def serve_ui() -> FileResponse:
        """Serve the Svelte SPA entry point."""
        return FileResponse(str(_UI_DIST / "index.html"))

    @app.get("/buibui-logo.svg", include_in_schema=False)
    def serve_logo() -> FileResponse:
        """Serve the buibui logo from the dist root."""
        return FileResponse(str(_UI_DIST / "buibui-logo.svg"))


def run(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Start the uvicorn server."""
    uvicorn.run("web.api.main:app", host=host, port=port, reload=reload)
