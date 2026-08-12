"""Retrying DuckDB connect for the jobs that take the write lock.

DuckDB allows one writer, and on duckdb 1.5.5 a second PROCESS is refused even
with `read_only=True` — only reader-vs-reader shares. So "open read-only to dodge
the writer" does not work, and every collision below is a hard failure rather
than a slow read.

WHO COLLIDES HERE, AND WHY IT IS NOT THE PARENT'S LIST
-----------------------------------------------------
The upstream module (parent #593) is premised on **unattended systemd timers**
catching up in the same second after a laptop resumes from suspend — a 15-minute
signal-watch, a thrice-daily xsmom sync — and cites a real incident where the
xsmom job died on `Conflicting lock` nine seconds after signal-watch's catch-up
fired. **None of that exists in this fork.** There is no wifey signal-watch
daemon, timer, or cron; the `buibui-*` units in `systemctl --user` belong to the
crypto parent.

What does hold the write lock here is operator-paced, and the realistic pairs
are:

    make wifey-web   FastAPI takes a brief RW lock at startup to init schema,
                     and again whenever the stats router refreshes its cache.
                     The UI is a normal thing to leave running.
    make go-live     signal_runner opens and closes ~8 write connections in one
                     cycle. This is the ledger writer, and the ledger is the
                     one artifact this repo cannot rebuild.
    make db-update   backtest sweep + recalibrate, both writers.
    make backup      #162's snapshot. It carries its OWN retry plus a lock-free
                     byte-copy fallback, so it does not use this module — but if
                     its opt-in timer is installed it becomes a genuinely
                     unattended writer and the parent's premise starts to apply.

**This is preventive, not a repair.** No wifey job is on record dying of a lock
conflict — there are no daemons to collide. The failure is reachable (verified
2026-08-12: holding a write connection made a second process's read-only ATTACH
fail), and `make go-live` is documented in README as a cron candidate, which is
the point at which "operator-paced" stops being true.

WHAT THE BUDGET DOES AND DOES NOT COVER
---------------------------------------
~52s across 6 attempts. That clears a brief RW open — the web startup, a stats
cache refresh, one signal_runner write — which is the whole realistic set.

It deliberately does **not** cover a `make db-update` sweep, which holds the
write lock for minutes. A job that collides with a full sweep is meant to fail
loudly after ~52s rather than hang: the operator started the sweep and can
retry after it, and silently blocking a `go-live` cycle for several minutes
would be worse than an error naming the cause.
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
    narrowable: two callers in `web/` swallowed *every* I/O error as "the
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
