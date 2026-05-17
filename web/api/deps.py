"""FastAPI dependency factories: get_db, require_token."""

import os
import secrets
from collections.abc import Generator

import duckdb
from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer()


def get_db(request: Request) -> Generator[duckdb.DuckDBPyConnection]:
    """Open a fresh read-only DuckDB connection per request (thread-safe)."""
    db_path: str = request.app.state.db_path
    try:
        conn = duckdb.connect(db_path, read_only=True)
    except duckdb.IOException:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is busy (signal-watch is writing). Try again in a few seconds.",
        ) from None
    try:
        yield conn
    finally:
        conn.close()


def require_token(
    creds: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    """Validate Bearer token against API_TOKEN env var."""
    token = os.environ.get("API_TOKEN", "")
    if not token or not secrets.compare_digest(creds.credentials, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
