"""Tests for tools/dead_surface_check.py — the data-driven half of silent-surface enforcement.

Full DB isolation: every test builds its own `:memory:` DuckDB and never touches
the real `analytics.db` (CLAUDE.md > Testing).
"""

import duckdb
import pytest

from analytics.signal_config import SignalWatchConfig
from tools.dead_surface_check import (
    DeadCell,
    declared_cells,
    find_dead_cells,
    unexpected,
)


def _conn_with_runs(
    rows: list[tuple[str, str, str, int]],
) -> duckdb.DuckDBPyConnection:
    """In-memory `backtest_runs` holding (strategy, timeframe, day_filter, total_signals)."""
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE backtest_runs ("
        "  strategy TEXT, timeframe TEXT, day_filter TEXT, total_signals INTEGER"
        ")"
    )
    for row in rows:
        conn.execute("INSERT INTO backtest_runs VALUES (?, ?, ?, ?)", list(row))
    return conn


class TestDeclaredCells:
    def test_expands_every_strategy_over_every_timeframe(self) -> None:
        cfg = SignalWatchConfig(strategies=["a", "b"], timeframes=["4h", "1d"])
        assert declared_cells(cfg) == [
            ("a", "4h"),
            ("a", "1d"),
            ("b", "4h"),
            ("b", "1d"),
        ]

    def test_strategy_timeframes_restricts_that_strategy_only(self) -> None:
        cfg = SignalWatchConfig(
            strategies=["orb", "bos"],
            timeframes=["4h", "1d"],
            strategy_timeframes={"orb": ["4h"]},
        )
        assert declared_cells(cfg) == [("orb", "4h"), ("bos", "4h"), ("bos", "1d")]

    def test_no_strategies_declares_nothing(self) -> None:
        assert declared_cells(SignalWatchConfig(timeframes=["4h"])) == []

    def test_duplicates_collapse(self) -> None:
        cfg = SignalWatchConfig(strategies=["a"], timeframes=["4h", "4h"])
        assert declared_cells(cfg) == [("a", "4h")]


class TestFindDeadCells:
    def test_cell_with_signals_is_alive(self) -> None:
        conn = _conn_with_runs([("bos", "4h", "tue_thu", 12)])
        cfg = SignalWatchConfig(
            strategies=["bos"], timeframes=["4h"], day_filter="tue_thu"
        )
        assert find_dead_cells(conn, cfg, "c.toml") == []

    def test_runs_exist_but_zero_signals_is_dead(self) -> None:
        """The #139 shape: rows exist, so the surface *looks* covered."""
        conn = _conn_with_runs(
            [("doji", "1d", "tue_thu", 0), ("doji", "1d", "tue_thu", 0)]
        )
        cfg = SignalWatchConfig(
            strategies=["doji"], timeframes=["1d"], day_filter="tue_thu"
        )
        dead = find_dead_cells(conn, cfg, "c.toml")
        assert len(dead) == 1
        assert dead[0].strategy == "doji"
        assert "never fired" in dead[0].reason
        assert "2 runs" in dead[0].reason

    def test_no_runs_at_all_is_dead(self) -> None:
        conn = _conn_with_runs([])
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["4h"], day_filter="off")
        dead = find_dead_cells(conn, cfg, "c.toml")
        assert len(dead) == 1
        assert "never backtested" in dead[0].reason

    def test_day_filter_scopes_the_lookup(self) -> None:
        """A cell alive under `weekdays` must not count as alive under `tue_thu`."""
        conn = _conn_with_runs([("ema", "1wk", "weekdays", 40)])
        alive = SignalWatchConfig(
            strategies=["ema"], timeframes=["1wk"], day_filter="weekdays"
        )
        dead_cfg = SignalWatchConfig(
            strategies=["ema"], timeframes=["1wk"], day_filter="tue_thu"
        )
        assert find_dead_cells(conn, alive, "c.toml") == []
        assert len(find_dead_cells(conn, dead_cfg, "c.toml")) == 1

    def test_reports_only_the_dead_cells_of_a_mixed_config(self) -> None:
        conn = _conn_with_runs(
            [
                ("bos", "4h", "off", 30),
                ("doji", "4h", "off", 0),
                ("ema", "4h", "off", 7),
            ]
        )
        cfg = SignalWatchConfig(
            strategies=["bos", "doji", "ema"], timeframes=["4h"], day_filter="off"
        )
        dead = find_dead_cells(conn, cfg, "c.toml")
        assert [c.strategy for c in dead] == ["doji"]


class TestAllowlist:
    @pytest.fixture
    def cell(self) -> DeadCell:
        return DeadCell("c.toml", "tue_thu", "doji", "1d", "detector never fired")

    def test_known_cell_is_not_unexpected(self, cell: DeadCell) -> None:
        # ("tue_thu", "doji", "1d") ships on the allowlist.
        assert unexpected([cell]) == []

    def test_same_cell_under_another_day_filter_is_unexpected(self) -> None:
        """The allowlist is keyed on day_filter — a cell dead elsewhere still fails."""
        other = DeadCell("c.toml", "off", "doji", "1d", "detector never fired")
        assert unexpected([other]) == [other]

    def test_new_dead_cell_is_reported(self) -> None:
        fresh = DeadCell("c.toml", "tue_thu", "bos", "4h", "detector never fired")
        assert unexpected([fresh]) == [fresh]

    def test_key_is_the_allowlist_tuple(self, cell: DeadCell) -> None:
        assert cell.key == ("tue_thu", "doji", "1d")
