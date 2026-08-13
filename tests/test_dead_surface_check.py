"""Tests for tools/dead_surface_check.py — the data-driven half of silent-surface enforcement.

Full DB isolation: every test builds its own `:memory:` DuckDB and never touches
the real `analytics.db` (CLAUDE.md > Testing).
"""

import duckdb
import pytest

from analytics.signal_config import SignalWatchConfig
from tools import dead_surface_check
from tools.dead_surface_check import (
    DeadCell,
    OrphanRating,
    cells_declared_elsewhere,
    declared_cells,
    find_dead_cells,
    find_orphan_ratings,
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
    """Exercises the filtering mechanism, NOT whatever the allowlist happens to hold.

    These tests pinned the shipped contents until 2026-08-06 and broke the moment
    the list emptied — a test coupled to data that is designed to change. The
    mechanism is patched here; the shipped contents get their own assertion below.
    """

    @pytest.fixture
    def cell(self) -> DeadCell:
        return DeadCell("c.toml", "tue_thu", "doji", "1d", "detector never fired")

    @pytest.fixture
    def _allowlisted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            dead_surface_check,
            "_KNOWN_DEAD_CELLS",
            frozenset({("tue_thu", "doji", "1d")}),
        )

    def test_known_cell_is_not_unexpected(
        self, cell: DeadCell, _allowlisted: None
    ) -> None:
        assert unexpected([cell]) == []

    def test_same_cell_under_another_day_filter_is_unexpected(
        self, _allowlisted: None
    ) -> None:
        """The allowlist is keyed on day_filter — a cell dead elsewhere still fails."""
        other = DeadCell("c.toml", "off", "doji", "1d", "detector never fired")
        assert unexpected([other]) == [other]

    def test_new_dead_cell_is_reported(self, _allowlisted: None) -> None:
        fresh = DeadCell("c.toml", "tue_thu", "bos", "4h", "detector never fired")
        assert unexpected([fresh]) == [fresh]

    def test_key_is_the_allowlist_tuple(self, cell: DeadCell) -> None:
        assert cell.key == ("tue_thu", "doji", "1d")

    def test_empty_allowlist_reports_everything(self, cell: DeadCell) -> None:
        """With nothing allowlisted, every dead cell must surface."""
        assert unexpected([cell]) == [cell]

    def test_shipped_allowlist_is_empty(self) -> None:
        """The allowlist may only ever SHRINK — it reached empty on 2026-08-06.

        A new entry means a dead surface was accepted, which is the thing this
        module exists to prevent. Growing it should require deleting this test,
        which is the point: that is a deliberate act, not an oversight.
        """
        assert frozenset() == dead_surface_check._KNOWN_DEAD_CELLS


def _conn_with_ratings(
    rows: list[tuple[str, str, str, str, int, float | None]],
) -> duckdb.DuckDBPyConnection:
    """In-memory `confidence_ratings` holding (config, strategy, tf, direction, stars, avg_r)."""
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE confidence_ratings ("
        "  config_name TEXT, strategy TEXT, tf TEXT, direction TEXT,"
        "  stars INTEGER, avg_r DOUBLE"
        ")"
    )
    for row in rows:
        conn.execute(
            "INSERT INTO confidence_ratings VALUES (?, ?, ?, ?, ?, ?)", list(row)
        )
    return conn


class TestFindOrphanRatings:
    """The inverse of a dead cell: rated but undeclared.

    A dead cell surfaces as a zero and reads as absence; an orphan surfaces as
    a *number* and reads as evidence, which is why it survived 2.5 months
    unnoticed while `check-dead-surfaces` reported a clean run beside it.
    """

    def test_rating_for_an_undeclared_strategy_is_an_orphan(self) -> None:
        conn = _conn_with_ratings(
            [("signal_watch", "fib_golden_zone", "4h", "combined", 3, 0.4688)]
        )
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["4h"])
        orphans = find_orphan_ratings(conn, cfg, "signal_watch")
        assert [(o.strategy, o.timeframe) for o in orphans] == [
            ("fib_golden_zone", "4h")
        ]

    def test_declared_cell_is_not_an_orphan(self) -> None:
        conn = _conn_with_ratings([("signal_watch", "bos", "4h", "combined", 3, 0.1)])
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["4h"])
        assert find_orphan_ratings(conn, cfg, "signal_watch") == []

    def test_undeclared_timeframe_of_a_declared_strategy_is_an_orphan(self) -> None:
        """`strategy_timeframes` narrowing is the shape #143 created for `bos`."""
        conn = _conn_with_ratings(
            [
                ("signal_watch", "bos", "1d", "combined", 2, 0.05),
                ("signal_watch", "bos", "4h", "combined", 1, -0.3174),
            ]
        )
        cfg = SignalWatchConfig(
            strategies=["bos"],
            timeframes=["4h", "1d"],
            strategy_timeframes={"bos": ["1d"]},
        )
        orphans = find_orphan_ratings(conn, cfg, "signal_watch")
        assert [(o.strategy, o.timeframe) for o in orphans] == [("bos", "4h")]

    def test_every_direction_of_an_orphan_cell_is_reported(self) -> None:
        conn = _conn_with_ratings(
            [
                ("signal_watch", "gone", "4h", "combined", 3, 0.4),
                ("signal_watch", "gone", "4h", "long", 4, 0.8),
                ("signal_watch", "gone", "4h", "short", 1, -0.2),
            ]
        )
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["4h"])
        orphans = find_orphan_ratings(conn, cfg, "signal_watch")
        assert sorted(o.direction for o in orphans) == ["combined", "long", "short"]

    def test_scoped_to_the_named_config(self) -> None:
        """Called with the TOML *stem*, which is what the column stores."""
        conn = _conn_with_ratings(
            [("signal_watch_weekdays", "gone", "4h", "combined", 3, 0.4)]
        )
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["4h"])
        assert find_orphan_ratings(conn, cfg, "signal_watch") == []

    def test_null_avg_r_is_carried_not_crashed(self) -> None:
        conn = _conn_with_ratings([("signal_watch", "gone", "4h", "combined", 3, None)])
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["4h"])
        orphans = find_orphan_ratings(conn, cfg, "signal_watch")
        assert orphans[0].avg_r is None
        assert "—" in str(orphans[0])

    def test_worst_offender_sorts_first(self) -> None:
        """Reported stars-desc so the most prominently displayed orphan leads."""
        conn = _conn_with_ratings(
            [
                ("signal_watch", "quiet", "4h", "combined", 1, -0.5),
                ("signal_watch", "loud", "4h", "combined", 5, 1.2),
            ]
        )
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["4h"])
        orphans = find_orphan_ratings(conn, cfg, "signal_watch")
        assert [o.strategy for o in orphans] == ["loud", "quiet"]


class TestOrphanTiers:
    """`undeclared anywhere` vs `declared by another config` (sister PR #608).

    The two live configs partition the calendar by `day_filter`, so a cell can be
    undeclared by the config that rates it while the other still declares it.
    Both are orphans for that config; only the first means nothing scans the cell.
    """

    def _orphan(self, elsewhere: set[tuple[str, str]] | None) -> OrphanRating:
        conn = _conn_with_ratings(
            [("signal_watch", "doji", "1wk", "combined", 4, 0.76)]
        )
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["1wk"])
        orphans = find_orphan_ratings(conn, cfg, "signal_watch", elsewhere)
        assert len(orphans) == 1
        return orphans[0]

    def test_cell_no_other_config_declares_is_undeclared_anywhere(self) -> None:
        orphan = self._orphan({("ema", "1d")})
        assert orphan.declared_elsewhere is False
        assert orphan.tier == "undeclared anywhere"
        assert "undeclared anywhere" in str(orphan)

    def test_cell_another_config_declares_is_the_milder_tier(self) -> None:
        orphan = self._orphan({("doji", "1wk")})
        assert orphan.declared_elsewhere is True
        assert orphan.tier == "declared by another config"
        assert "declared by another config" in str(orphan)

    def test_omitting_elsewhere_reports_undeclared_anywhere(self) -> None:
        """A single-config run has no other config to have declared the cell."""
        assert self._orphan(None).tier == "undeclared anywhere"

    def test_tier_labels_but_never_suppresses(self) -> None:
        """The milder tier is still a FAIL — it is a diagnosis, not an excuse."""
        conn = _conn_with_ratings(
            [
                ("signal_watch", "doji", "1wk", "combined", 4, 0.76),
                ("signal_watch", "ema", "1wk", "combined", 2, 0.10),
            ]
        )
        cfg = SignalWatchConfig(strategies=["bos"], timeframes=["1wk"])
        orphans = find_orphan_ratings(conn, cfg, "signal_watch", {("doji", "1wk")})
        assert len(orphans) == 2
        assert {o.strategy for o in orphans} == {"doji", "ema"}

    def test_matching_needs_both_strategy_and_timeframe(self) -> None:
        """A same-named strategy on a different timeframe is not the same cell."""
        assert self._orphan({("doji", "1d")}).tier == "undeclared anywhere"


class TestCellsDeclaredElsewhere:
    """The union feeding the tier decision, extracted from `main` so it is reachable.

    `main` has no test of its own, so an inlined union would have been the one
    piece of tier logic nothing could falsify — and a defect in it downgrades
    every orphan's tier without moving the pass/fail result an end-to-end run
    checks.
    """

    def test_excludes_the_config_itself(self) -> None:
        by_config = {"a.toml": {("bos", "4h")}, "b.toml": {("doji", "1d")}}
        assert cells_declared_elsewhere(by_config, "a.toml") == {("doji", "1d")}

    def test_unions_every_other_config(self) -> None:
        by_config = {
            "a.toml": {("bos", "4h")},
            "b.toml": {("doji", "1d")},
            "c.toml": {("ema", "1wk")},
        }
        assert cells_declared_elsewhere(by_config, "a.toml") == {
            ("doji", "1d"),
            ("ema", "1wk"),
        }

    def test_single_config_has_no_elsewhere(self) -> None:
        """The empty union — a lone config cannot have been declared by another."""
        assert cells_declared_elsewhere({"a.toml": {("bos", "4h")}}, "a.toml") == set()

    def test_a_cell_both_configs_declare_is_still_elsewhere(self) -> None:
        """Overlap is the common case: signal_watch's 22 cells are a subset of weekdays'."""
        by_config = {"a.toml": {("bos", "4h")}, "b.toml": {("bos", "4h")}}
        assert cells_declared_elsewhere(by_config, "a.toml") == {("bos", "4h")}

    def test_unknown_config_name_unions_everything(self) -> None:
        by_config = {"a.toml": {("bos", "4h")}, "b.toml": {("doji", "1d")}}
        assert cells_declared_elsewhere(by_config, "nope.toml") == {
            ("bos", "4h"),
            ("doji", "1d"),
        }
