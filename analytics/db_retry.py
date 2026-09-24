"""Retrying DuckDB connect for the jobs that take the write lock.

DuckDB allows one writer, and a second process is refused even with
`read_only=True` — only reader-vs-reader sharing works. So "open read-only to
dodge the writer" does not work, and every collision below is a hard failure
rather than a slow read.

Realistic collisions here are operator-paced rather than unattended-timer
collisions like the parent's: this fork runs no signal-watch daemon, timer or
cron, and the `buibui-*` systemd units belong to the crypto parent. The pairs
that do collide:

    make wifey-web   FastAPI takes a brief RW lock at startup to init schema,
                     and again whenever the stats router refreshes its cache.
                     The UI is a normal thing to leave running.
    make go-live     signal_runner opens and closes ~8 write connections in one
                     cycle. This is the ledger writer, and the ledger is the
                     one artifact this repo cannot rebuild.
    make db-update   backtest sweep + recalibrate, both writers.
    make backup      the snapshot job. It carries its own retry plus a
                     lock-free byte-copy fallback, so it does not use this
                     module — but if its opt-in timer is installed it becomes
                     a genuinely unattended writer, closer to the parent's case.

This is preventive rather than a repair for an observed failure: no wifey job
is on record dying of a lock conflict, since there are no daemons to collide.
The failure is reachable (holding a write connection makes a second process's
read-only ATTACH fail too), and `make go-live` is a documented cron candidate,
which is the point at which "operator-paced" stops being true.

The retry budget (~52s across 6 attempts) clears a brief RW open — web
startup, a stats cache refresh, one signal_runner write — which is the whole
realistic set. It deliberately does not cover a `make db-update` sweep, which
holds the write lock for minutes: a job that collides with a full sweep is
meant to fail loudly after ~52s rather than hang, since the operator started
the sweep and can retry after it, and silently blocking a `go-live` cycle for
several minutes would be worse than an error naming the cause.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import duckdb

DEFAULT_ATTEMPTS = 6
_BACKOFF_S = (2.0, 5.0, 10.0, 15.0, 20.0)


def is_lock_conflict(exc: BaseException) -> bool:
    """True when `exc` is DuckDB refusing a second writer.

    Matched on the message because DuckDB raises a bare `IOException` for every
    I/O failure -- a missing directory and a busy lock are the same class. Only
    the lock case is worth retrying; a genuinely bad path would otherwise retry
    six times before failing with the same error a minute later.

    This is also what makes an existing blanket `except duckdb.IOException`
    narrowable: three callers -- two in `web/`, plus the daemon's per-symbol
    `sync` in `signal_runner.py` -- swallowed *every* I/O error as "the
    database is busy", so a missing or corrupt file was reported as transient.
    """
    return isinstance(exc, duckdb.IOException) and "Conflicting lock" in str(exc)


def connect_with_retry(
    db_path: Path | str,
    *,
    read_only: bool = False,
    attempts: int = DEFAULT_ATTEMPTS,
    sleep: object = time.sleep,
) -> duckdb.DuckDBPyConnection:
    """Open `db_path`, waiting out a conflicting writer.

    Raises the underlying `duckdb.IOException` once the budget is spent, so a
    lock that never clears still fails loudly rather than hanging the job.
    `sleep` is injected so tests do not pay the backoff.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")

    sleep_fn = sleep if callable(sleep) else time.sleep
    last: duckdb.IOException | None = None

    for attempt in range(attempts):
        try:
            return duckdb.connect(str(db_path), read_only=read_only)
        except duckdb.IOException as e:
            if not is_lock_conflict(e):
                raise
            last = e
            if attempt == attempts - 1:
                break
            delay = _BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)]
            logging.warning(
                "analytics.db is locked by another job; retrying in %.0fs (%d/%d)",
                delay,
                attempt + 1,
                attempts - 1,
            )
            sleep_fn(delay)

    assert last is not None  # only reachable via the lock-conflict branch
    logging.error(
        "analytics.db still locked after %d attempts (~%.0fs) -- giving up",
        attempts,
        sum(_BACKOFF_S[: attempts - 1]),
    )
    raise last
