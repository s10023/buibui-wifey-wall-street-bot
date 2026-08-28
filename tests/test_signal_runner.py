"""Tests for signal_runner._update_ohlcv_cache and the daemon loop (--once flag)."""

import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from analytics import signal_runner
from analytics.data_store import init_schema, upsert_ohlcv
from analytics.signal_runner import _sync_watched_series, _update_ohlcv_cache

_MS = 15 * 60 * 1000  # 15 minutes in ms
_T0 = 1_700_000_000_000  # arbitrary base timestamp (ms)


def _make_row(open_time: int, close: float, volume: float) -> dict:
    return {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "open_time": open_time,
        "open": close,
        "high": close + 10,
        "low": close - 10,
        "close": close,
        "volume": volume,
    }


def _seed_db(conn: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    upsert_ohlcv(conn, df)


def test_warm_cache_replaces_stale_last_row() -> None:
    """The last cached row (a partial candle) must be replaced with its final values.

    Bug: the old code queried from cached_max_ts+1, so the partial last row was
    never updated in the cache even after sync() finalised it in the DB.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    # Seed DB with two closed candles and one partial (current open).
    rows = [
        _make_row(_T0, close=100.0, volume=50.0),  # closed
        _make_row(_T0 + _MS, close=200.0, volume=10.0),  # partial (low volume)
    ]
    _seed_db(conn, rows)

    cache: dict = {}
    now_ms = _T0 + 2 * _MS

    # Cold start — cache built from DB.
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, now_ms)
    assert len(cache[("BTCUSDT", "15m")]) == 2
    assert float(cache[("BTCUSDT", "15m")]["volume"].iloc[-1]) == 10.0  # partial

    # Simulate sync() finalising the partial candle with a large volume spike,
    # and adding the next partial candle.
    rows[1] = _make_row(_T0 + _MS, close=210.0, volume=500.0)  # now finalised
    rows.append(_make_row(_T0 + 2 * _MS, close=210.0, volume=5.0))  # new partial
    _seed_db(conn, rows)

    # Warm update — should replace the stale last row and append the new partial.
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 3 * _MS)

    df = cache[("BTCUSDT", "15m")]
    assert len(df) == 3  # T0, T0+15m (finalised), T0+30m (new partial)
    # The finalised candle must have the updated volume, not the stale partial.
    assert float(df["volume"].iloc[1]) == 500.0, "stale partial volume not replaced"
    assert float(df["close"].iloc[1]) == 210.0, "stale partial close not replaced"


def test_warm_cache_invalidates_on_gap() -> None:
    """If >2 rows arrive the cache is fully rebuilt (missed cycle / gap)."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    rows = [
        _make_row(_T0 + i * _MS, close=float(i * 100), volume=50.0) for i in range(3)
    ]
    _seed_db(conn, rows)

    cache: dict = {}
    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 3 * _MS)
    assert len(cache[("BTCUSDT", "15m")]) == 3

    # Add 3 new rows — simulates daemon being down for 2 cycles.
    extra = [
        _make_row(_T0 + i * _MS, close=float(i * 100), volume=50.0) for i in range(3, 6)
    ]
    _seed_db(conn, extra)

    _update_ohlcv_cache(conn, cache, "BTCUSDT", "15m", _T0, _T0 + 6 * _MS)
    assert len(cache[("BTCUSDT", "15m")]) == 6  # full rebuild


@pytest.fixture
def daemon_mocks() -> Any:
    """Patch every heavy dependency of run_signal_watch so the loop runs in-memory.

    All names below are module-level imports on ``analytics.signal_runner``, so
    patching them there exercises the loop without touching DuckDB or yfinance.
    """
    with ExitStack() as stack:
        mocks = {
            name: stack.enter_context(patch.object(signal_runner, name))
            for name in (
                "duckdb",
                "init_schema",
                "prune_backtest_cache",
                "get_combo_lookup",
                "get_cross_tf_combo_lookup",
                "get_confidence_ratings",
                "get_directional_confidence_ratings",
                "CooldownStore",
                "sync",
                "backfill",
                "_update_ohlcv_cache",
                "run_scan_cycle",
                "backfill_outcomes",
                "secs_until_next_boundary",
                "load_stocks_config",
            )
        }
        mocks["load_stocks_config"].return_value = {"AAPL": {}}
        mocks["get_combo_lookup"].return_value = {}
        mocks["get_cross_tf_combo_lookup"].return_value = {}
        mocks["get_confidence_ratings"].return_value = {}
        mocks["get_directional_confidence_ratings"].return_value = {}
        mocks["run_scan_cycle"].return_value = []
        mocks["secs_until_next_boundary"].return_value = (0.0, time.time() + 1)
        yield mocks


def test_once_runs_single_cycle_then_exits(daemon_mocks: Any) -> None:
    signal_runner.run_signal_watch(
        symbols=["AAPL"], timeframes=["4h"], strategies=["bos"], once=True
    )
    assert daemon_mocks["run_scan_cycle"].call_count == 1
    # --once breaks before scheduling the next-candle sleep.
    daemon_mocks["secs_until_next_boundary"].assert_not_called()


class TestLiveBacktestWindowIsExecuted:
    """`[backtest] days` must reach the live EV gate, not just parse.

    Until 2026-08-06 `run_signal_watch` called `run_scan_cycle` without `days`, so
    the gate silently used the 90-day signature default while both live configs
    declared 365. The gate abstains below `min_trades`, so the narrow window did not
    fail loudly — it made the hard gate a no-op on 71% of all direction-legs.

    These assert the EXECUTED window on both surfaces. Asserting that the config
    parsed to 365 cannot detect this defect: it parsed correctly the whole time.
    """

    @staticmethod
    def _run(days: int | None, mocks: Any) -> None:
        cfg = (
            None
            if days is None
            else signal_runner.BacktestFilterConfig(mode="hard", days=days)
        )
        signal_runner.run_signal_watch(
            symbols=["AAPL"],
            timeframes=["1d"],
            strategies=["bos"],
            once=True,
            backtest_cfg=cfg,
        )

    def test_configured_days_reaches_run_scan_cycle(self, daemon_mocks: Any) -> None:
        self._run(365, daemon_mocks)
        assert daemon_mocks["run_scan_cycle"].call_args.kwargs["days"] == 365

    def test_configured_days_also_widens_the_ohlcv_cache(
        self, daemon_mocks: Any
    ) -> None:
        """The cache read must move with `days`.

        `run_scan_cycle` prefers a populated `ohlcv_cache` over its own `start_ms`
        read, so a `days` argument alone would widen the declared window while the
        DataFrame handed to `_compute_backtest` stayed 90 days.
        """
        now_ms = int(time.time() * 1000)
        self._run(365, daemon_mocks)
        start_ms = daemon_mocks["_update_ohlcv_cache"].call_args.args[4]
        span_days = (now_ms - start_ms) / 86_400_000
        assert 364 <= span_days <= 366

    def test_falls_back_to_default_without_a_backtest_config(
        self, daemon_mocks: Any
    ) -> None:
        self._run(None, daemon_mocks)
        assert (
            daemon_mocks["run_scan_cycle"].call_args.kwargs["days"]
            == signal_runner._DEFAULT_BACKFILL_DAYS
        )


def test_default_loops_and_sleeps_between_cycles(daemon_mocks: Any) -> None:
    # Break the otherwise-infinite loop by raising on the 2nd scan cycle.
    daemon_mocks["run_scan_cycle"].side_effect = [[], KeyboardInterrupt]
    with patch.object(signal_runner.time, "sleep"), pytest.raises(KeyboardInterrupt):
        signal_runner.run_signal_watch(
            symbols=["AAPL"], timeframes=["4h"], strategies=["bos"], once=False
        )
    assert daemon_mocks["run_scan_cycle"].call_count == 2
    # Default (daemon) path reaches the boundary-sleep scheduler after cycle 1.
    assert daemon_mocks["secs_until_next_boundary"].call_count == 1


class TestSyncIoErrorsAreNarrowed:
    """DuckDB raises one class for every I/O failure, so the per-symbol handler
    around ``sync`` must let a missing, full or corrupt database through and
    swallow only a lock conflict.

    A bare handler logged "will retry" and the cycle went on to scan, alert and
    backfill outcomes against stale data — and under ``--once`` (how
    ``make go-live`` runs) there is no next cycle for that retry to happen in.
    The two sibling sites, ``web/api/deps.py`` and ``web/api/main.py``, were
    narrowed for this reason; this one was missed.

    Both directions are asserted on purpose: without the swallow control,
    "it propagated" would pass just as well if the loop never reached ``sync``.
    """

    def test_a_lock_conflict_is_swallowed_and_the_cycle_continues(
        self, daemon_mocks: Any, tmp_path: Path
    ) -> None:
        # The fixture patches the whole `duckdb` module on the runner, which
        # would make `except duckdb.IOException` uncatchable. Restore the real
        # class so the handler under test is the one that runs.
        daemon_mocks["duckdb"].IOException = duckdb.IOException
        daemon_mocks["sync"].side_effect = duckdb.IOException(
            'Conflicting lock is held in "analytics.db"'
        )

        signal_runner.run_signal_watch(
            symbols=["AAPL"],
            timeframes=["4h"],
            strategies=["bos"],
            once=True,
            db_path=tmp_path / "analytics.db",
        )

        # Swallowed, so the cycle ran to completion.
        assert daemon_mocks["run_scan_cycle"].call_count == 1
        # An IOException is not the ValueError that triggers a first backfill.
        daemon_mocks["backfill"].assert_not_called()

    def test_a_non_lock_io_error_propagates(
        self, daemon_mocks: Any, tmp_path: Path
    ) -> None:
        daemon_mocks["duckdb"].IOException = duckdb.IOException
        daemon_mocks["sync"].side_effect = duckdb.IOException(
            "IO Error: Could not read from file: database is corrupt"
        )

        with pytest.raises(duckdb.IOException, match="corrupt"):
            signal_runner.run_signal_watch(
                symbols=["AAPL"],
                timeframes=["4h"],
                strategies=["bos"],
                once=True,
                db_path=tmp_path / "analytics.db",
            )

        # The whole point: no scan, no alert, no outcome backfill on stale data.
        daemon_mocks["run_scan_cycle"].assert_not_called()
        daemon_mocks["backfill_outcomes"].assert_not_called()


class TestSyncWatchedSeriesDirect:
    """Unit tests on the extracted helper (parent #688's other half).

    ``TestSyncIoErrorsAreNarrowed`` above proves the same narrowing END-TO-END
    through ``run_signal_watch`` and stays; what only this class covers is the
    ``ValueError`` fallback — first backfill plus the cache pop that forces the
    next read cold. The end-to-end tests could only assert that path's absence.
    """

    @staticmethod
    def _call(
        sync_effect: object,
        cache: dict[tuple[str, str], pd.DataFrame] | None = None,
    ) -> list[tuple[str, str, int]]:
        backfilled: list[tuple[str, str, int]] = []
        with (
            patch("analytics.signal_runner.sync", side_effect=sync_effect),
            patch(
                "analytics.signal_runner.backfill",
                side_effect=lambda c, s, t, ms: backfilled.append((s, t, ms)),
            ),
        ):
            _sync_watched_series(
                conn=object(),  # type: ignore[arg-type]  # never touched: sync is patched
                symbols=["AAPL"],
                timeframes=["4h"],
                backfill_start_ms=123,
                ohlcv_cache={} if cache is None else cache,
            )
        return backfilled

    def test_a_lock_conflict_is_swallowed(self) -> None:
        self._call(duckdb.IOException('Conflicting lock is held in "analytics.db"'))

    def test_a_non_lock_io_error_propagates(self) -> None:
        with pytest.raises(duckdb.IOException, match="corrupt"):
            self._call(duckdb.IOException("IO Error: database is corrupt"))

    def test_a_missing_series_backfills_and_forces_a_cold_read(self) -> None:
        """The path the end-to-end tests never drive: a new series must get its
        first backfill (with the caller's window) AND lose its cache entry, or
        the cycle scans a warm cache that predates the bars just written."""
        cache = {("AAPL", "4h"): pd.DataFrame({"open_time": [1]})}
        assert self._call(ValueError("no data"), cache) == [("AAPL", "4h", 123)]
        assert ("AAPL", "4h") not in cache
