"""Tests for analytics.db_retry.connect_with_retry."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from analytics.db_retry import DEFAULT_ATTEMPTS, connect_with_retry, is_lock_conflict

# The real message DuckDB 1.5.5 raises in THIS repo when a second process
# arrives, captured 2026-08-12 by holding a write connection from a child
# process. Matching is done on substring, so a test that invented its own
# wording would pass while the guard never fired -- which is why this is copied
# from an observed failure rather than written from memory, and why it is the
# wifey path and not the parent's.
#
# The same message appears for `read_only=True`. That is the finding that makes
# this module necessary at all: on 1.5.5 a second PROCESS is refused even
# read-only, so "open read-only to dodge the writer" is not an alternative.
LOCK_MSG = (
    'IO Error: Could not set lock on file "/home/kng/repo/'
    'buibui-wifey-wall-street-bot/analytics.db": Conflicting lock is held in '
    "/home/linuxbrew/.linuxbrew/Cellar/python@3.13/3.13.15/bin/python3.13 "
    "(PID 3364232) by user kng."
)


class TestIsLockConflict:
    def test_recognises_a_real_conflicting_lock(self) -> None:
        assert is_lock_conflict(duckdb.IOException(LOCK_MSG))

    def test_rejects_other_io_errors(self) -> None:
        # A bad path is the same exception class. Retrying it would turn an
        # instant, accurate failure into the same failure a minute later -- and
        # in `web/`, reporting it to the user as "the database is busy".
        assert not is_lock_conflict(
            duckdb.IOException("IO Error: No such file or directory")
        )

    def test_rejects_unrelated_exception_types(self) -> None:
        assert not is_lock_conflict(RuntimeError(LOCK_MSG))


class TestConnectWithRetry:
    def test_returns_connection_without_sleeping_when_free(self) -> None:
        conn = MagicMock()
        slept: list[float] = []
        with patch("analytics.db_retry.duckdb.connect", return_value=conn) as connect:
            got = connect_with_retry("/tmp/x.db", sleep=slept.append)
        assert got is conn
        assert slept == []
        connect.assert_called_once_with("/tmp/x.db", read_only=False)

    def test_passes_read_only_through(self) -> None:
        with patch("analytics.db_retry.duckdb.connect") as connect:
            connect_with_retry("/tmp/x.db", read_only=True, sleep=lambda _: None)
        connect.assert_called_once_with("/tmp/x.db", read_only=True)

    def test_retries_past_a_conflicting_lock_and_succeeds(self) -> None:
        conn = MagicMock()
        slept: list[float] = []
        attempts = [duckdb.IOException(LOCK_MSG), duckdb.IOException(LOCK_MSG), conn]

        def fake_connect(*_a: Any, **_k: Any) -> Any:
            got = attempts.pop(0)
            if isinstance(got, Exception):
                raise got
            return got

        with patch("analytics.db_retry.duckdb.connect", side_effect=fake_connect):
            assert connect_with_retry("/tmp/x.db", sleep=slept.append) is conn
        # Two failures => two waits, and the backoff must grow rather than spin.
        assert len(slept) == 2
        assert slept[1] > slept[0]

    def test_does_not_retry_a_non_lock_io_error(self) -> None:
        slept: list[float] = []
        boom = duckdb.IOException("IO Error: No such file or directory")
        with (
            patch("analytics.db_retry.duckdb.connect", side_effect=boom) as connect,
            pytest.raises(duckdb.IOException, match="No such file"),
        ):
            connect_with_retry("/tmp/x.db", sleep=slept.append)
        assert connect.call_count == 1
        assert slept == []

    def test_raises_the_lock_error_once_the_budget_is_spent(self) -> None:
        slept: list[float] = []
        with (
            patch(
                "analytics.db_retry.duckdb.connect",
                side_effect=duckdb.IOException(LOCK_MSG),
            ) as connect,
            pytest.raises(duckdb.IOException, match="Conflicting lock"),
        ):
            connect_with_retry("/tmp/x.db", attempts=4, sleep=slept.append)
        assert connect.call_count == 4
        # Waits happen *between* attempts, never after the last one.
        assert len(slept) == 3

    def test_single_attempt_never_sleeps(self) -> None:
        slept: list[float] = []
        with (
            patch(
                "analytics.db_retry.duckdb.connect",
                side_effect=duckdb.IOException(LOCK_MSG),
            ) as connect,
            pytest.raises(duckdb.IOException),
        ):
            connect_with_retry("/tmp/x.db", attempts=1, sleep=slept.append)
        assert connect.call_count == 1
        assert slept == []

    def test_rejects_a_nonsense_attempt_budget(self) -> None:
        with pytest.raises(ValueError, match="attempts must be >= 1"):
            connect_with_retry("/tmp/x.db", attempts=0)


class TestBudgetIsSizedForThisRepo:
    """The budget's job here is NOT the parent's job.

    Upstream sizes it against a 15-minute signal-watch timer that runs 24-34s.
    This fork has no daemon at all; the writers are `make wifey-web` (a brief RW
    open at startup and on each stats-cache refresh), `make go-live`, and
    `make db-update`. These two tests pin both ends of what that means, because
    a budget with no stated ceiling invites someone to "fix" a db-update
    collision by inflating it until the call effectively hangs.
    """

    @staticmethod
    def _total_wait() -> float:
        slept: list[float] = []
        with (
            patch(
                "analytics.db_retry.duckdb.connect",
                side_effect=duckdb.IOException(LOCK_MSG),
            ),
            pytest.raises(duckdb.IOException),
        ):
            connect_with_retry("/tmp/x.db", sleep=slept.append)
        return sum(slept)

    def test_outlasts_a_brief_rw_open(self) -> None:
        """A web startup / stats-cache refresh is seconds. Clearing it is the point."""
        assert self._total_wait() >= 30

    def test_does_not_silently_block_for_minutes(self) -> None:
        """A `make db-update` sweep holds for minutes and is meant to FAIL LOUDLY.

        Blocking a go-live cycle for several minutes would be worse than an
        error naming the cause, so the ceiling is deliberate, not an oversight.
        """
        assert self._total_wait() <= 120
        assert DEFAULT_ATTEMPTS == 6
