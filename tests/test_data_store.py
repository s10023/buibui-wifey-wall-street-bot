"""Tests for analytics/data_store.py."""

import time
from typing import Any

import duckdb
import pandas as pd
import pytest

from analytics.backtest_lib import BacktestResult, Trade
from analytics.data_store import (
    BacktestSnapshot,
    _backtest_run_id,
    _make_bt_cache_key,
    get_backtest_cache,
    get_confidence_ratings,
    get_latest_open_time,
    get_ohlcv,
    get_signals_history,
    get_win_rate_by_strategy,
    init_schema,
    list_backtest_runs,
    prune_backtest_cache,
    put_backtest_cache,
    upsert_backtest_run,
    upsert_backtest_trades,
    upsert_confidence_ratings,
    upsert_ohlcv,
    upsert_signal_outcome,
    upsert_signals,
)

_OHLCV_ROW: dict[str, object] = {
    "symbol": "BTCUSDT",
    "timeframe": "1h",
    "open_time": 1_700_000_000_000,
    "open": 30000.0,
    "high": 31000.0,
    "low": 29500.0,
    "close": 30500.0,
    "volume": 100.0,
}


def _one(conn: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    """Execute a query and return the single result row, asserting it exists."""
    row = conn.execute(sql).fetchone()
    assert row is not None
    return row


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


_SIGNAL_ROW: dict[str, object] = {
    "symbol": "BTCUSDT",
    "timeframe": "1h",
    "strategy": "fvg",
    "open_time": 1_700_000_000_000,
    "direction": "long",
    "entry_price": 30500.0,
    "sl_price": 29000.0,
    "reason": "FVG filled",
    "confidence": 4,
    "fired_at": 1_700_000_001_000,
}


class TestInitSchema:
    def test_creates_tables(self, conn: duckdb.DuckDBPyConnection) -> None:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert {
            "ohlcv",
            "signals",
            "signal_alert_outcomes",
            "backtest_runs",
            "backtest_trades",
            "backtest_combos",
            "backtest_cross_tf_combos",
            "backtest_cache",
            "stats_cache",
            "confidence_ratings",
            "earnings_facts",
            "insider_transactions",
        } == tables

    def test_idempotent(self, conn: duckdb.DuckDBPyConnection) -> None:
        init_schema(conn)
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "ohlcv" in tables

    def test_ohlcv_schema_has_no_taker_buy_volume_or_vwap(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ohlcv'"
            ).fetchall()
        }
        assert "taker_buy_volume" not in cols
        assert "vwap" not in cols

    def test_no_funding_rates_table(self, conn: duckdb.DuckDBPyConnection) -> None:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "funding_rates" not in tables

    def test_no_open_interest_table(self, conn: duckdb.DuckDBPyConnection) -> None:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "open_interest" not in tables


class TestUpsertOhlcv:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        assert _one(conn, "SELECT COUNT(*) FROM ohlcv")[0] == 1

    def test_replaces_on_conflict(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        upsert_ohlcv(conn, pd.DataFrame([{**_OHLCV_ROW, "close": 99999.0}]))
        assert _one(conn, "SELECT close FROM ohlcv")[0] == 99999.0

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame(columns=list(_OHLCV_ROW.keys())))
        assert _one(conn, "SELECT COUNT(*) FROM ohlcv")[0] == 0


class TestGetOhlcv:
    def test_returns_rows_in_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["close"] == 30500.0

    def test_excludes_rows_outside_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 1_000_000_000)
        assert result.empty

    def test_returns_empty_dataframe_when_no_data(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.empty


class TestUpsertSignals:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 1

    def test_ignores_on_conflict(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        # Same PK — second insert should be ignored, not raise or update.
        upsert_signals(
            conn, pd.DataFrame([{**_SIGNAL_ROW, "reason": "updated reason"}])
        )
        row = _one(conn, "SELECT reason FROM signals")
        assert row[0] == "FVG filled"  # original preserved

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame(columns=list(_SIGNAL_ROW.keys())))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 0

    def test_multiple_strategies_same_candle(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row2 = {**_SIGNAL_ROW, "strategy": "bos"}
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW, row2]))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 2


class TestGetSignalsHistory:
    def test_returns_signals_in_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["strategy"] == "fvg"
        assert result.iloc[0]["direction"] == "long"

    def test_excludes_outside_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 1_000_000_000)
        assert result.empty

    def test_filters_by_symbol_and_timeframe(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        other = {**_SIGNAL_ROW, "symbol": "ETHUSDT"}
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW, other]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "BTCUSDT"

    def test_returns_empty_when_no_data(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.empty

    def test_ordered_descending_by_open_time(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row1 = {**_SIGNAL_ROW, "open_time": 1_700_000_000_000}
        row2 = {**_SIGNAL_ROW, "open_time": 1_700_003_600_000, "strategy": "bos"}
        upsert_signals(conn, pd.DataFrame([row1, row2]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.iloc[0]["open_time"] > result.iloc[1]["open_time"]


_OUTCOME_ROW: dict[str, object] = {
    "signal_id": "btcusdt-1h-fvg-1700000000000-long",
    "symbol": "BTCUSDT",
    "tf": "1h",
    "strategy": "fvg",
    "direction": "long",
    "fired_at_ms": 1_700_000_001_000,
    "candle_ts_ms": 1_700_000_000_000,
    "entry_price": 30500.0,
    "sl_price": 29000.0,
    "tp_price": 33500.0,
    "rr_ratio": 2.0,
    "confidence_at_fire": 4,
    "tags": '["vol_high"]',
    "outcome": None,
    "outcome_r": None,
    "outcome_filled_at_ms": None,
}


class TestSignalAlertOutcomesSchema:
    def test_init_creates_signal_alert_outcomes_table(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "signal_alert_outcomes" in tables

    def test_signal_alert_outcomes_table_idempotent(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        init_schema(conn)
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "signal_alert_outcomes" in tables

    def test_migration_renames_signal_outcomes(self) -> None:
        c = duckdb.connect(":memory:")
        # Simulate a legacy DB with the old table name.
        c.execute("""
            CREATE TABLE signal_outcomes (
                signal_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL, tf TEXT NOT NULL,
                strategy TEXT NOT NULL, direction TEXT NOT NULL,
                fired_at_ms BIGINT NOT NULL,
                candle_ts_ms BIGINT, entry_price DOUBLE,
                sl_price DOUBLE, tp_price DOUBLE,
                rr_ratio DOUBLE, confidence_at_fire INTEGER,
                tags TEXT, outcome TEXT,
                outcome_r DOUBLE, outcome_filled_at_ms BIGINT
            )
        """)
        c.execute(
            "INSERT INTO signal_outcomes(signal_id, symbol, tf, strategy, direction, fired_at_ms) "
            "VALUES ('old-id', 'BTCUSDT', '1h', 'fvg', 'long', 1700000001000)"
        )
        init_schema(c)
        tables = {r[0] for r in c.execute("SHOW TABLES").fetchall()}
        assert "signal_alert_outcomes" in tables
        assert "signal_outcomes" not in tables
        # Data preserved after rename.
        count = c.execute("SELECT COUNT(*) FROM signal_alert_outcomes").fetchone()
        assert count is not None
        assert count[0] == 1


class TestUpsertSignalOutcome:
    def test_inserts_row(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signal_outcome(conn, dict(_OUTCOME_ROW))
        assert _one(conn, "SELECT COUNT(*) FROM signal_alert_outcomes")[0] == 1

    def test_upsert_replaces_on_conflict(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signal_outcome(conn, dict(_OUTCOME_ROW))
        updated = {**_OUTCOME_ROW, "outcome": "win", "outcome_r": 1.8}
        upsert_signal_outcome(conn, updated)
        # Still only one row (no duplicate inserted).
        assert _one(conn, "SELECT COUNT(*) FROM signal_alert_outcomes")[0] == 1
        row = _one(conn, "SELECT outcome, outcome_r FROM signal_alert_outcomes")
        assert row[0] == "win"
        assert abs(row[1] - 1.8) < 1e-9

    def test_redetection_preserves_a_resolved_outcome(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """A re-fire must not erase a label the backfill already resolved.

        This is the live shape, not a hypothetical: `scanner.py` writes the
        alert row on EVERY scan that still detects the signal (the write is
        unconditional — only dispatch is watermarked), and it passes no
        outcome / outcome_r / outcome_filled_at_ms key at all. Measured
        2026-08-11: 13 rows re-stamped in one cycle, the oldest a signal from
        7 weeks earlier that was already booked as a loss.
        """
        upsert_signal_outcome(conn, dict(_OUTCOME_ROW))
        # Backfill resolves it — a direct UPDATE, which is what
        # `outcome_backfill.py` actually issues.
        conn.execute(
            "UPDATE signal_alert_outcomes SET outcome = 'loss', outcome_r = -1.0, "
            "outcome_filled_at_ms = 1700000900000 WHERE signal_id = ?",
            [_OUTCOME_ROW["signal_id"]],
        )
        # The scanner re-detects the same signal: same identity, no outcome keys.
        redetect = {
            k: v
            for k, v in _OUTCOME_ROW.items()
            if k not in {"outcome", "outcome_r", "outcome_filled_at_ms"}
        }
        redetect["fired_at_ms"] = 1_700_009_999_000
        upsert_signal_outcome(conn, redetect)

        row = _one(
            conn,
            "SELECT outcome, outcome_r, outcome_filled_at_ms, fired_at_ms "
            "FROM signal_alert_outcomes",
        )
        assert row[0] == "loss", "re-detection erased the resolved outcome"
        assert abs(row[1] - (-1.0)) < 1e-9
        assert row[2] == 1_700_000_900_000
        # The re-fire still refreshes the non-outcome columns.
        assert row[3] == 1_700_009_999_000

    def test_explicit_outcome_still_overwrites_on_conflict(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """Preserving must not become ignoring — a supplied value still wins.

        Guards the other direction of the same change: if the preserve rule
        were written as "never update outcome", the backfill's own re-resolve
        path and every fixture that seeds an outcome would silently stop
        working.
        """
        upsert_signal_outcome(conn, dict(_OUTCOME_ROW))
        conn.execute(
            "UPDATE signal_alert_outcomes SET outcome = 'loss', outcome_r = -1.0 "
            "WHERE signal_id = ?",
            [_OUTCOME_ROW["signal_id"]],
        )
        upsert_signal_outcome(
            conn, {**_OUTCOME_ROW, "outcome": "win", "outcome_r": 2.5}
        )
        row = _one(conn, "SELECT outcome, outcome_r FROM signal_alert_outcomes")
        assert row[0] == "win"
        assert abs(row[1] - 2.5) < 1e-9

    def test_missing_optional_fields_default_to_null(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        minimal: dict[str, object] = {
            "signal_id": "minimal-signal",
            "symbol": "ETHUSDT",
            "tf": "4h",
            "strategy": "bos",
            "direction": "short",
            "fired_at_ms": 1_700_000_002_000,
        }
        upsert_signal_outcome(conn, minimal)
        row = _one(conn, "SELECT outcome, outcome_r FROM signal_alert_outcomes")
        assert row[0] is None
        assert row[1] is None

    def test_stores_all_fields_correctly(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signal_outcome(conn, dict(_OUTCOME_ROW))
        row = _one(
            conn,
            "SELECT symbol, tf, strategy, direction, entry_price, sl_price, "
            "tp_price, rr_ratio, confidence_at_fire, tags "
            "FROM signal_alert_outcomes",
        )
        assert row[0] == "BTCUSDT"
        assert row[1] == "1h"
        assert row[2] == "fvg"
        assert row[3] == "long"
        assert row[4] == 30500.0
        assert row[5] == 29000.0
        assert row[6] == 33500.0
        assert abs(row[7] - 2.0) < 1e-9
        assert row[8] == 4
        assert row[9] == '["vol_high"]'


# ---------------------------------------------------------------------------
# Helpers for backtest store tests
# ---------------------------------------------------------------------------


class _FakeTrade:
    def __init__(
        self,
        signal_time: int,
        entry_time: int,
        entry_price: float,
        direction: str,
        sl_price: float,
        tp_price: float,
        outcome: str,
        pnl_r: float | None,
    ) -> None:
        self.signal_time = signal_time
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.direction = direction
        self.sl_price = sl_price
        self.tp_price = tp_price
        self.exit_time: int | None = entry_time + 3_600_000
        self.exit_price: float | None = tp_price if outcome == "win" else sl_price
        self.outcome = outcome
        self.pnl_r = pnl_r


class _FakeResult:
    def __init__(self, symbol: str, timeframe: str, strategy: str) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy = strategy
        self.fee_pct = 0.0
        self.trades: list[Any] = [
            _FakeTrade(
                1_700_000_000_000,
                1_700_003_600_000,
                30000.0,
                "long",
                29400.0,
                31200.0,
                "win",
                2.0,
            ),
            _FakeTrade(
                1_700_007_200_000,
                1_700_010_800_000,
                30100.0,
                "long",
                29498.0,
                31304.0,
                "loss",
                -1.0,
            ),
        ]

    @property
    def closed_trades(self) -> list[Any]:
        return [t for t in self.trades if t.outcome != "open"]

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.closed_trades if t.outcome == "win")

    @property
    def loss_count(self) -> int:
        return sum(1 for t in self.closed_trades if t.outcome == "loss")

    @property
    def win_rate(self) -> float:
        closed = len(self.closed_trades)
        return self.win_count / closed if closed else 0.0

    @property
    def avg_r(self) -> float:
        vals = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def total_r(self) -> float:
        vals: list[float] = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(vals)

    @property
    def max_drawdown_r(self) -> float:
        return 1.0

    @property
    def long_closed_trades(self) -> list[Any]:
        return [t for t in self.closed_trades if t.direction == "long"]

    @property
    def short_closed_trades(self) -> list[Any]:
        return [t for t in self.closed_trades if t.direction == "short"]

    @property
    def long_win_count(self) -> int:
        return sum(1 for t in self.long_closed_trades if t.outcome == "win")

    @property
    def long_win_rate(self) -> float | None:
        n = len(self.long_closed_trades)
        return self.long_win_count / n if n > 0 else None

    @property
    def long_avg_r(self) -> float | None:
        vals = [t.pnl_r for t in self.long_closed_trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def short_win_count(self) -> int:
        return sum(1 for t in self.short_closed_trades if t.outcome == "win")

    @property
    def short_win_rate(self) -> float | None:
        n = len(self.short_closed_trades)
        return self.short_win_count / n if n > 0 else None

    @property
    def short_avg_r(self) -> float | None:
        vals = [t.pnl_r for t in self.short_closed_trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def long_total_r(self) -> float:
        vals: list[float] = [
            t.pnl_r for t in self.long_closed_trades if t.pnl_r is not None
        ]
        return sum(vals)

    @property
    def short_total_r(self) -> float:
        vals: list[float] = [
            t.pnl_r for t in self.short_closed_trades if t.pnl_r is not None
        ]
        return sum(vals)

    @property
    def recovery_factor(self) -> float:
        dd = self.max_drawdown_r
        return self.total_r / dd if dd > 0 else 0.0


_BT_PARAMS: dict[str, Any] = {
    "days": 90,
    "data_start_ms": 1_690_000_000_000,
    "data_end_ms": 1_700_000_000_000,
    "sl_pct": 0.02,
    "tp_r": 2.0,
    "fee_pct": 0.0,
    "day_filter": "off",
    "sweep_id": None,
    "origin": "sweep",
    # Required since 2026-08-11: the EXECUTED threshold, not the declared one.
    # Note mypy cannot enforce this through a `**dict` splat — the 12 call
    # sites below type-checked clean and failed at runtime.
    "adr_suppress_threshold": None,
    # Required since 2026-08-26, and unenforceable here for the same reason —
    # the EXECUTED live-parity gate set, not a config's declared block.
    "live_parity": None,
}


class TestUpsertBacktestRun:
    def test_inserts_row(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 1

    def test_stores_aggregate_fields(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        row = _one(
            conn,
            "SELECT symbol, timeframe, strategy, closed_trades, win_count, "
            "loss_count, win_rate, avg_r FROM backtest_runs",
        )
        assert row[0] == "BTCUSDT"
        assert row[1] == "4h"
        assert row[2] == "bos"
        assert row[3] == 2
        assert row[4] == 1
        assert row[5] == 1
        assert abs(row[6] - 0.5) < 1e-9
        assert abs(row[7] - 0.5) < 1e-9

    def test_replaces_on_same_params(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        id1 = upsert_backtest_run(conn, result, **_BT_PARAMS)
        id2 = upsert_backtest_run(conn, result, **_BT_PARAMS)
        assert id1 == id2
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 1

    def test_different_params_produce_different_rows(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        params_b = {**_BT_PARAMS, "sl_pct": 0.03}
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_run(conn, result, **params_b)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 2

    def test_tail_columns_land_in_correct_slots(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        # Regression: the SELECT column order in upsert_backtest_run used to
        # be misaligned with the table layout starting at long_total_r, so
        # adr_suppress_threshold ended up holding recovery_factor values and
        # vice versa. Assert each named column reads back what we passed.
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(
            conn,
            result,
            **{**_BT_PARAMS, "adr_suppress_threshold": 0.8, "volume_suppress": True},
        )
        row = _one(
            conn,
            "SELECT adr_suppress_threshold, volume_suppress, long_total_r, "
            "short_total_r, recovery_factor FROM backtest_runs",
        )
        assert abs(row[0] - 0.8) < 1e-6
        assert row[1] is True
        assert abs(row[2] - result.long_total_r) < 1e-6
        assert abs(row[3] - result.short_total_r) < 1e-6
        assert abs(row[4] - result.recovery_factor) < 1e-6

    def test_universe_policy_persisted(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(
            conn,
            result,
            **_BT_PARAMS,
            universe_policy='{"scope": "liquid_large_cap"}',
        )
        row = _one(conn, "SELECT universe_policy FROM backtest_runs")
        assert row[0] == '{"scope": "liquid_large_cap"}'

    def test_universe_policy_defaults_null(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        row = _one(conn, "SELECT universe_policy FROM backtest_runs")
        assert row[0] is None


class TestUpsertBacktestTrades:
    def test_inserts_trade_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_trades")[0] == 2

    def test_trade_fields_correct(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        row = _one(
            conn,
            "SELECT outcome, pnl_r FROM backtest_trades ORDER BY signal_time LIMIT 1",
        )
        assert row[0] == "win"
        assert abs(row[1] - 2.0) < 1e-9

    def test_empty_trades_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        result.trades = []
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_trades")[0] == 0


class TestGetWinRateByStrategy:
    def test_returns_aggregated_win_rate(self, conn: duckdb.DuckDBPyConnection) -> None:
        # Insert enough closed trades to pass the min_trades=20 gate.
        # We fake it by inserting a row directly with closed_trades=25.
        from analytics.data_store import _backtest_run_id

        run_id = _backtest_run_id("BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off")
        conn.execute(
            "INSERT INTO backtest_runs VALUES (?, 'BTCUSDT', '4h', 'bos', "
            "1690000000000, 1700000000000, 90, 0.02, 2.0, 0.0, 'off', "
            "25, 25, 15, 10, 0.6, 0.5, 12.5, 3.0, 1700000001000, NULL, "
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            [run_id],
        )
        df = get_win_rate_by_strategy(conn)
        assert len(df) == 1
        assert df.iloc[0]["strategy"] == "bos"
        assert abs(df.iloc[0]["win_rate_pct"] - 60.0) < 0.01

    def test_excludes_low_trade_count(self, conn: duckdb.DuckDBPyConnection) -> None:
        from analytics.data_store import _backtest_run_id

        run_id = _backtest_run_id("BTCUSDT", "4h", "fvg", 90, 0.02, 2.0, 0.0, "off")
        conn.execute(
            "INSERT INTO backtest_runs VALUES (?, 'BTCUSDT', '4h', 'fvg', "
            "1690000000000, 1700000000000, 90, 0.02, 2.0, 0.0, 'off', "
            "5, 5, 3, 2, 0.6, 0.4, 2.0, 1.0, 1700000001000, NULL, "
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            [run_id],
        )
        df = get_win_rate_by_strategy(conn)
        assert df.empty

    def test_mean_avg_r_is_pooled_over_trades_not_averaged_over_runs(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """`mean_avg_r` sits beside a pooled count and a pooled win_rate_pct.

        40 trades @ -0.10R and 25 trades @ +1.50R:
            average over runs  = (-0.10 + 1.50) / 2       = +0.700
            pooled over trades = (40*-0.1 + 25*1.5) / 65  = +0.515
        Both runs clear the query's own closed_trades >= 20 gate.
        """
        for symbol, closed, wins, avg_r in [
            ("SPY", 40, 10, -0.10),
            ("QQQ", 25, 20, 1.50),
        ]:
            conn.execute(
                f"INSERT INTO backtest_runs VALUES ('{symbol}-bos', '{symbol}', "
                "'4h', 'bos', 1690000000000, 1700000000000, 90, 0.02, 2.0, 0.0, "
                f"'off', {closed}, {closed}, {wins}, {closed - wins}, "
                f"{wins / closed}, {avg_r}, 0.0, 3.0, 1700000001000, NULL, "
                "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                "NULL, NULL, NULL, NULL, NULL, NULL)"
            )
        df = get_win_rate_by_strategy(conn)
        assert len(df) == 1
        assert abs(float(df.iloc[0]["mean_avg_r"]) - 0.515) < 0.001
        assert int(df.iloc[0]["total_closed"]) == 65


class TestConfidenceRatings:
    def test_upsert_and_get_roundtrip(self, conn: duckdb.DuckDBPyConnection) -> None:
        ratings = {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}
        win_rates = pd.DataFrame(
            [
                {"strategy": "fvg", "timeframe": "1h", "avg_r": 0.35, "win_rate": 0.55},
                {"strategy": "fvg", "timeframe": "4h", "avg_r": 0.72, "win_rate": 0.60},
                {
                    "strategy": "bos",
                    "timeframe": "15m",
                    "avg_r": -0.28,
                    "win_rate": 0.13,
                },
            ]
        )
        upsert_confidence_ratings(conn, "signal_watch", ratings, win_rates)
        result = get_confidence_ratings(conn, "signal_watch")
        assert result == {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}

    def test_returns_empty_dict_for_unknown_config(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = get_confidence_ratings(conn, "nonexistent_config")
        assert result == {}

    def test_configs_are_isolated(self, conn: duckdb.DuckDBPyConnection) -> None:
        ratings_a = {"fvg": {"1h": 3}}
        ratings_b = {"fvg": {"1h": 5}}
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        upsert_confidence_ratings(conn, "config_a", ratings_a, empty_wr)
        upsert_confidence_ratings(conn, "config_b", ratings_b, empty_wr)
        assert get_confidence_ratings(conn, "config_a") == {"fvg": {"1h": 3}}
        assert get_confidence_ratings(conn, "config_b") == {"fvg": {"1h": 5}}

    def test_upsert_replaces_existing(self, conn: duckdb.DuckDBPyConnection) -> None:
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        upsert_confidence_ratings(conn, "signal_watch", {"fvg": {"1h": 2}}, empty_wr)
        upsert_confidence_ratings(conn, "signal_watch", {"fvg": {"1h": 4}}, empty_wr)
        result = get_confidence_ratings(conn, "signal_watch")
        assert result["fvg"]["1h"] == 4

    def test_empty_ratings_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        upsert_confidence_ratings(conn, "signal_watch", {}, empty_wr)
        assert get_confidence_ratings(conn, "signal_watch") == {}

    def test_day_filter_stored_and_joined_in_backtest_runs(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """stars should be resolved per backtest row via day_filter JOIN."""
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        upsert_confidence_ratings(
            conn,
            "signal_watch_weekdays",
            {"bos": {"4h": 4}},
            empty_wr,
            day_filter="tue_thu",
        )
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **{**_BT_PARAMS, "day_filter": "tue_thu"})
        df = list_backtest_runs(conn)
        assert df.iloc[0]["stars"] == 4

    def test_stars_null_when_no_matching_confidence(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        df = list_backtest_runs(conn)
        assert pd.isna(df.iloc[0]["stars"])


class TestGetLatestOpenTime:
    def test_returns_none_when_no_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        assert get_latest_open_time(conn, "BTCUSDT", "1h") is None

    def test_returns_max_open_time(self, conn: duckdb.DuckDBPyConnection) -> None:
        rows = [
            {**_OHLCV_ROW, "open_time": 1_700_000_000_000},
            {**_OHLCV_ROW, "open_time": 1_700_003_600_000},
        ]
        upsert_ohlcv(conn, pd.DataFrame(rows))
        assert get_latest_open_time(conn, "BTCUSDT", "1h") == 1_700_003_600_000


def _make_result(
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    strategy: str = "engulfing",
) -> BacktestResult:
    """Minimal BacktestResult with 2 long wins and 1 long loss."""
    entry, sl, tp = 50000.0, 49000.0, 52000.0

    def _trade(outcome: str) -> Trade:
        exit_price = tp if outcome == "win" else sl
        return Trade(
            signal_time=1_000_000,
            entry_time=1_100_000,
            entry_price=entry,
            direction="long",
            sl_price=sl,
            tp_price=tp,
            exit_time=2_000_000,
            exit_price=exit_price,
            outcome=outcome,
        )

    return BacktestResult(
        symbol=symbol,
        timeframe=tf,
        strategy=strategy,
        trades=[_trade("win"), _trade("win"), _trade("loss")],
    )


class TestBacktestCache:
    def test_get_miss(self, conn: duckdb.DuckDBPyConnection) -> None:
        assert get_backtest_cache(conn, "nonexistent") is None

    def test_put_and_get_round_trip(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        snap = get_backtest_cache(conn, "key1")
        assert snap is not None
        assert isinstance(snap, BacktestSnapshot)
        assert len(snap.closed_trades) == len(result.closed_trades)
        assert len(snap.long_closed_trades) == len(result.long_closed_trades)
        assert len(snap.short_closed_trades) == len(result.short_closed_trades)
        assert snap.win_count == result.win_count
        assert snap.win_rate == pytest.approx(result.win_rate)
        assert snap.avg_r == pytest.approx(result.avg_r)
        assert snap.long_win_rate == pytest.approx(result.long_win_rate)
        assert snap.long_avg_r == pytest.approx(result.long_avg_r)

    def test_get_miss_on_different_key(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        assert get_backtest_cache(conn, "key2") is None

    def test_directional_sd_round_trips(self, conn: duckdb.DuckDBPyConnection) -> None:
        """The EV gate's significance test reads sd off the cached snapshot.

        Without this the gate silently degrades to abstain on every cache hit —
        which is the normal steady-state path, so the gate would stop biting
        without any error. Added with the sd columns, 2026-08-07.
        """
        result = _make_result()
        put_backtest_cache(conn, "sd_key", "run_sd", 100_000, result)
        snap = get_backtest_cache(conn, "sd_key")
        assert snap is not None
        assert result.long_pnl_sd is not None
        assert snap.r_long_sd == pytest.approx(result.long_pnl_sd)
        # `long_pnl_sd` is the interface the gate reads — it must agree on both
        # types, not merely be present on each.
        assert snap.long_pnl_sd == pytest.approx(result.long_pnl_sd)

    def test_migration_adds_sd_columns_to_a_preexisting_cache(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """A DB created before the sd columns must gain them, and read back NULL.

        NULL is the abstain signal, so an un-refreshed row must not block. The
        cache key includes last_candle_ts, so real rows age out within a bar.
        """

        def cache_cols() -> set[str]:
            return {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'backtest_cache'"
                ).fetchall()
            }

        conn.execute("ALTER TABLE backtest_cache DROP COLUMN r_long_sd")
        conn.execute("ALTER TABLE backtest_cache DROP COLUMN r_short_sd")
        assert "r_long_sd" not in cache_cols()

        init_schema(conn)  # re-run migrations, as a real process does at startup
        assert {"r_long_sd", "r_short_sd"} <= cache_cols()

    def test_prune_removes_old_entries(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "new_key", "run_new", 100_000, result)
        # Clone the row as "old_key" with cached_at_ms backdated 40 days.
        old_ms = int(time.time() * 1000) - 40 * 24 * 3600 * 1000
        conn.execute(
            "INSERT INTO backtest_cache "
            "SELECT 'old_key', run_id, last_candle_ts, symbol, timeframe, strategy, fee_pct, "
            "n_closed, n_long, n_short, n_win, n_loss, r_win_rate, r_avg, r_total, "
            "n_long_win, r_long_win_rate, r_long_avg, r_long_total, "
            "n_short_win, r_short_win_rate, r_short_avg, r_short_total, "
            "h_median, h_long_median, h_short_median, ?, "
            # Positional INSERT — must track the DDL's trailing columns.
            "r_long_sd, r_short_sd "
            "FROM backtest_cache WHERE cache_key = 'new_key'",
            [old_ms],
        )
        prune_backtest_cache(conn, keep_days=30)
        assert get_backtest_cache(conn, "old_key") is None
        assert get_backtest_cache(conn, "new_key") is not None

    def test_prune_keeps_recent_entries(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        prune_backtest_cache(conn, keep_days=30)
        assert get_backtest_cache(conn, "key1") is not None

    def test_backtest_run_id_unchanged_for_defaults(self) -> None:
        old_id = _backtest_run_id(
            "BTCUSDT", "1h", "engulfing", 90, 0.02, 3.0, 0.0, "off", 1, None
        )
        new_id = _backtest_run_id(
            "BTCUSDT",
            "1h",
            "engulfing",
            90,
            0.02,
            3.0,
            0.0,
            "off",
            1,
            None,
            min_sl_pct=0.0,
            atr_sl_multiplier=None,
            tp_r_long=None,
            tp_r_short=None,
            volume_suppress_long=None,
            volume_suppress_short=None,
            volume_spike_boost_long=None,
            volume_spike_boost_short=None,
            adr_exempt=False,
        )
        assert old_id == new_id

    def test_backtest_run_id_changes_for_nondefault_min_sl(self) -> None:
        base = _backtest_run_id(
            "BTCUSDT", "1h", "engulfing", 90, 0.02, 3.0, 0.0, "off", 1, None
        )
        with_min_sl = _backtest_run_id(
            "BTCUSDT",
            "1h",
            "engulfing",
            90,
            0.02,
            3.0,
            0.0,
            "off",
            1,
            None,
            min_sl_pct=0.005,
        )
        assert base != with_min_sl

    def test_make_bt_cache_key_changes_with_ts(self) -> None:
        k1 = _make_bt_cache_key("run1", 100)
        k2 = _make_bt_cache_key("run1", 200)
        assert k1 != k2

    def test_make_bt_cache_key_changes_with_run_id(self) -> None:
        k1 = _make_bt_cache_key("run1", 100)
        k2 = _make_bt_cache_key("run2", 100)
        assert k1 != k2

    def test_snapshot_truthiness(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        snap = get_backtest_cache(conn, "key1")
        assert snap is not None
        assert bool(snap.closed_trades)
        assert not bool(snap.short_closed_trades)


class TestBacktestRunIdOrigin:
    """A param combination does not uniquely identify a MEASUREMENT.

    The sweep and the live EV gate can produce identical
    `symbol|timeframe|strategy|days|sl_pct|tp_r|fee_pct|day_filter` tuples
    while measuring different things over different windows. They shared a
    run_id, so `INSERT OR REPLACE` made the live gate overwrite the sweep row
    in place (flipping `sweep_id` to NULL) instead of accumulating alongside
    it — deleting the very rows `confidence_ratings` is built from.
    """

    def test_sweep_origin_keeps_legacy_hash(self) -> None:
        """Every historical sweep run_id must survive this change untouched."""
        from analytics.data_store import _backtest_run_id

        legacy = _backtest_run_id("SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off")
        explicit = _backtest_run_id(
            "SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", origin="sweep"
        )
        assert legacy == explicit

    def test_live_gate_origin_differs_from_sweep(self) -> None:
        from analytics.data_store import _backtest_run_id

        sweep = _backtest_run_id("SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "tue_thu")
        live = _backtest_run_id(
            "SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "tue_thu", origin="live_gate"
        )
        assert sweep != live

    def test_distinct_origins_do_not_overwrite_each_other(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """The regression: a live-gate write must not destroy the sweep row.

        Identical params, identical `day_filter` — exactly what the live
        scanner passes. Before the `origin` discriminator this left ONE row,
        with `sweep_id` NULL and the live gate's numbers.
        """
        from analytics.data_store import upsert_backtest_run

        params: dict[str, Any] = {
            "days": 365,
            "data_start_ms": 0,
            "data_end_ms": 1,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0,
            "day_filter": "tue_thu",
            "adr_suppress_threshold": None,
            "live_parity": None,
        }
        swept = BacktestResult(symbol="SPY", timeframe="4h", strategy="bos")
        upsert_backtest_run(conn, swept, sweep_id="sweep-1", origin="sweep", **params)
        live = BacktestResult(symbol="SPY", timeframe="4h", strategy="bos")
        upsert_backtest_run(conn, live, sweep_id=None, origin="live_gate", **params)

        rows = conn.execute(
            "SELECT sweep_id FROM backtest_runs WHERE symbol = 'SPY' "
            "AND timeframe = '4h' AND strategy = 'bos'"
        ).fetchall()
        assert len(rows) == 2, "live-gate write overwrote the sweep row"
        assert {r[0] for r in rows} == {None, "sweep-1"}

    def test_sweep_row_survives_repeated_live_writes(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """The live gate writes every scan cycle; the sweep row must persist."""
        from analytics.data_store import upsert_backtest_run

        params: dict[str, Any] = {
            "days": 365,
            "data_start_ms": 0,
            "data_end_ms": 1,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0,
            "day_filter": "weekdays",
            "adr_suppress_threshold": None,
            "live_parity": None,
        }
        upsert_backtest_run(
            conn,
            BacktestResult(symbol="AAPL", timeframe="1d", strategy="doji"),
            sweep_id="sweep-9",
            origin="sweep",
            **params,
        )
        for _ in range(5):
            upsert_backtest_run(
                conn,
                BacktestResult(symbol="AAPL", timeframe="1d", strategy="doji"),
                sweep_id=None,
                origin="live_gate",
                **params,
            )

        surviving = conn.execute(
            "SELECT count(*) FROM backtest_runs WHERE sweep_id = 'sweep-9'"
        ).fetchone()
        assert surviving is not None and surviving[0] == 1


class TestBacktestRunIdCostModel:
    def test_none_cost_model_keeps_legacy_hash(self) -> None:
        from analytics.data_store import _backtest_run_id

        legacy = _backtest_run_id("SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off")
        explicit = _backtest_run_id(
            "SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", cost_model=None
        )
        assert legacy == explicit

    def test_cost_model_changes_hash(self) -> None:
        from analytics.data_store import _backtest_run_id

        legacy = _backtest_run_id("SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off")
        costed = _backtest_run_id(
            "SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", cost_model='{"x":1}'
        )
        assert legacy != costed
        # Deterministic for the same stamp.
        assert costed == _backtest_run_id(
            "SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", cost_model='{"x":1}'
        )

    def test_upsert_persists_cost_model_column(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        from analytics.data_store import upsert_backtest_run

        result = BacktestResult(symbol="SPY", timeframe="4h", strategy="bos")
        run_id = upsert_backtest_run(
            conn,
            result,
            days=90,
            data_start_ms=0,
            data_end_ms=1,
            sl_pct=0.02,
            tp_r=2.0,
            fee_pct=0.0,
            day_filter="off",
            cost_model='{"impact_coef":1.0}',
            origin="sweep",
            adr_suppress_threshold=None,
            live_parity=None,
        )
        row = conn.execute(
            "SELECT cost_model FROM backtest_runs WHERE run_id = ?", [run_id]
        ).fetchone()
        assert row is not None and row[0] == '{"impact_coef":1.0}'
