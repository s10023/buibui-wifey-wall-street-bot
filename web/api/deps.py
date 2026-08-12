"""FastAPI dependency factories: get_db, require_token."""

import os
import secrets
from collections.abc import Generator

import duckdb
from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from analytics.db_retry import is_lock_conflict

_bearer = HTTPBearer()


def get_db(request: Request) -> Generator[duckdb.DuckDBPyConnection]:
    """Open a fresh read-only DuckDB connection per request (thread-safe)."""
    db_path: str = request.app.state.db_path
    # Deliberately NOT connect_with_retry: this runs per HTTP request, and the
    # retry budget is ~52s — long enough to hang the UI and exhaust the worker
    # pool. Failing fast with a 503 lets the browser retry, which is the right
    # shape for a request path. The retry belongs in the batch jobs.
    #
    # Narrowed from a bare `except duckdb.IOException`, which reported a missing
    # or corrupt database as "busy, try again in a few seconds" — advice that
    # can never come true. The old message also named "signal-watch", a daemon
    # this fork does not have; the real holders are `make go-live`,
    # `make db-update` and `make backup`.
    try:
        conn = duckdb.connect(db_path, read_only=True)
    except duckdb.IOException as e:
        if not is_lock_conflict(e):
            raise
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is busy (another job holds the write lock). "
            "Try again in a few seconds.",
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
