"""Tests for analytics/recalibrate_lib.py."""

import textwrap
from pathlib import Path

import duckdb
import pandas as pd

from analytics.data_store import (
    get_confidence_ratings,
    get_directional_confidence_ratings,
    init_schema,
    prune_undeclared_confidence_ratings,
)
from analytics.recalibrate_lib import (
    compute_directional_ratings,
    compute_recalibrated_ratings,
    format_recalibration_report,
    get_backtest_win_rates,
    win_rate_to_stars,
    write_confidence_to_db,
    write_confidence_to_source,
)

# ---------------------------------------------------------------------------
# win_rate_to_stars — boundary values
# ---------------------------------------------------------------------------


class TestWinRateToStars:
    def test_negative_avg_r_returns_1_star(self) -> None:
        assert win_rate_to_stars(-0.5, 20) == 1

    def test_zero_avg_r_returns_2_stars(self) -> None:
        assert win_rate_to_stars(0.0, 20) == 2

    def test_boundary_0_2_exclusive_returns_2_stars(self) -> None:
        assert win_rate_to_stars(0.19, 20) == 2

    def test_boundary_0_2_inclusive_returns_3_stars(self) -> None:
        assert win_rate_to_stars(0.2, 20) == 3

    def test_mid_range_3_stars(self) -> None:
        assert win_rate_to_stars(0.35, 20) == 3

    def test_boundary_0_5_exclusive_returns_3_stars(self) -> None:
        assert win_rate_to_stars(0.499, 20) == 3

    def test_boundary_0_5_inclusive_returns_4_stars(self) -> None:
        assert win_rate_to_stars(0.5, 20) == 4

    def test_boundary_0_9_exclusive_returns_4_stars(self) -> None:
        assert win_rate_to_stars(0.89, 20) == 4

    def test_boundary_0_9_inclusive_returns_5_stars(self) -> None:
        assert win_rate_to_stars(0.9, 20) == 5

    def test_high_avg_r_returns_5_stars(self) -> None:
        assert win_rate_to_stars(1.5, 20) == 5

    def test_insufficient_trades_returns_none(self) -> None:
        assert win_rate_to_stars(0.8, 5, min_trades=10) is None

    def test_exactly_min_trades_returns_rating(self) -> None:
        assert win_rate_to_stars(0.8, 10, min_trades=10) == 4

    def test_custom_min_trades(self) -> None:
        assert win_rate_to_stars(0.5, 3, min_trades=3) == 4
        assert win_rate_to_stars(0.5, 2, min_trades=3) is None


# ---------------------------------------------------------------------------
# compute_recalibrated_ratings — in-memory DuckDB
# ---------------------------------------------------------------------------


def _seed_backtest_runs(conn: duckdb.DuckDBPyConnection) -> None:
    """Insert sample backtest_runs rows for testing."""
    rows = [
        # bos: avg_r=0.6 over 30 closed trades on 4h → 4★
        {
            "run_id": "aaa",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "strategy": "bos",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "total_signals": 30,
            "closed_trades": 30,
            "win_count": 18,
            "loss_count": 12,
            "win_rate": 0.6,
            "avg_r": 0.6,
            "total_r": 18.0,
            "max_drawdown_r": 3.0,
            "run_at_ms": 1000,
            "sweep_id": "sweep-1",
        },
        # fvg: avg_r=-0.1 over 20 closed trades on 1h → 1★
        {
            "run_id": "bbb",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "fvg",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "total_signals": 20,
            "closed_trades": 20,
            "win_count": 8,
            "loss_count": 12,
            "win_rate": 0.4,
            "avg_r": -0.1,
            "total_r": -2.0,
            "max_drawdown_r": 4.0,
            "run_at_ms": 1000,
            "sweep_id": "sweep-1",
        },
        # pin_bar: only 5 trades on 4h — below default min_trades=10 → excluded
        {
            "run_id": "ccc",
            "symbol": "ETHUSDT",
            "timeframe": "4h",
            "strategy": "pin_bar",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0,
            "day_filter": "off",
            "total_signals": 5,
            "closed_trades": 5,
            "win_count": 4,
            "loss_count": 1,
            "win_rate": 0.8,
            "avg_r": 1.2,
            "total_r": 6.0,
            "max_drawdown_r": 0.0,
            "run_at_ms": 1000,
            "sweep_id": "sweep-1",
        },
        # engulfing: 4h=0.7 (4★), 1h=-0.2 (1★) — different ratings per TF
        {
            "run_id": "ddd",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "strategy": "engulfing",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "total_signals": 15,
            "closed_trades": 15,
            "win_count": 10,
            "loss_count": 5,
            "win_rate": 0.667,
            "avg_r": 0.7,
            "total_r": 10.5,
            "max_drawdown_r": 2.0,
            "run_at_ms": 1000,
            "sweep_id": "sweep-1",
        },
        {
            "run_id": "eee",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "engulfing",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "total_signals": 12,
            "closed_trades": 12,
            "win_count": 4,
            "loss_count": 8,
            "win_rate": 0.333,
            "avg_r": -0.2,
            "total_r": -2.4,
            "max_drawdown_r": 3.0,
            "run_at_ms": 1000,
            "sweep_id": "sweep-1",
        },
    ]
    conn.executemany(
        "INSERT INTO backtest_runs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
        "NULL, NULL, NULL)",
        [
            [
                r["run_id"],
                r["symbol"],
                r["timeframe"],
                r["strategy"],
                r["data_start_ms"],
                r["data_end_ms"],
                r["days"],
                r["sl_pct"],
                r["tp_r"],
                r["fee_pct"],
                r["day_filter"],
                r["total_signals"],
                r["closed_trades"],
                r["win_count"],
                r["loss_count"],
                r["win_rate"],
                r["avg_r"],
                r["total_r"],
                r["max_drawdown_r"],
                r["run_at_ms"],
                r["sweep_id"],
            ]
            for r in rows
        ],
    )


class TestComputeRecalibratedRatings:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_empty_db_returns_empty_dict(self) -> None:
        conn = self._make_conn()
        result = compute_recalibrated_ratings(conn)
        conn.close()
        assert result == {}

    def test_bos_gets_correct_stars_per_tf(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # bos avg_r=0.6 on 4h → 4★
        assert result["bos"] == {"4h": 4}

    def test_fvg_gets_correct_stars_per_tf(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # fvg avg_r=-0.1 on 1h → 1★
        assert result["fvg"] == {"1h": 1}

    def test_insufficient_trades_excluded(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # pin_bar has only 5 trades — should not appear
        assert "pin_bar" not in result

    def test_custom_min_trades_includes_pin_bar(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=5)
        conn.close()
        # pin_bar avg_r=1.2 on 4h → 5★ when min_trades=5
        assert result["pin_bar"] == {"4h": 5}

    def test_per_tf_divergence(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # engulfing: 4h=4★, 1h=1★ — different per TF
        assert result["engulfing"]["4h"] == 4
        assert result["engulfing"]["1h"] == 1

    def test_returns_only_strategies_with_data(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        assert set(result.keys()) == {"bos", "fvg", "engulfing"}


# ---------------------------------------------------------------------------
# format_recalibration_report
# ---------------------------------------------------------------------------


class TestFormatRecalibrationReport:
    def test_returns_non_empty_string(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}, "fvg": {"1h": 1}}
        win_rates = pd.DataFrame(
            [
                {
                    "strategy": "bos",
                    "timeframe": "4h",
                    "total_trades": 30,
                    "win_rate": 0.6,
                    "avg_r": 0.6,
                },
                {
                    "strategy": "fvg",
                    "timeframe": "1h",
                    "total_trades": 20,
                    "win_rate": 0.4,
                    "avg_r": -0.1,
                },
            ]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert len(report) > 0

    def test_contains_strategy_names(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}, "fvg": {"1h": 1}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "bos" in report
        assert "fvg" in report

    def test_shows_change_marker_for_changed_strategy(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "3→4" in report

    def test_shows_no_data_for_missing_new_rating(self) -> None:
        old: dict[str, dict[str, int] | int] = {"pin_bar": 2}
        new: dict[str, dict[str, int]] = {}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "no data" in report

    def test_unchanged_strategy_shows_equals_marker(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 3}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "=" in report

    def test_empty_new_ratings_all_no_data(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "No data" in report or "no data" in report

    def test_per_tf_dict_old_ratings_resolved_correctly(self) -> None:
        # old has per-TF dict; new has different rating for 4h
        old: dict[str, dict[str, int] | int] = {"engulfing": {"default": 1, "4h": 3}}
        new: dict[str, dict[str, int]] = {"engulfing": {"4h": 4}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "3→4" in report


# ---------------------------------------------------------------------------
# write_confidence_to_source — patches indicators_lib.py source in-place
# ---------------------------------------------------------------------------

_FAKE_SOURCE = textwrap.dedent("""\
    STRATEGY_REGISTRY: dict[str, StrategySpec] = {
        "fvg": StrategySpec(
            name="fvg",
            description="Fair Value Gap.",
            confidence=4,
        ),
        "bos": StrategySpec(
            name="bos",
            description="Break of Structure.",
            confidence=3,
        ),
        "pin_bar": StrategySpec(
            name="pin_bar",
            description="Pin Bar.",
            confidence=2,
        ),
    }
""")


class TestWriteConfidenceToSource:
    def test_patches_single_strategy_int(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": 5}, src)
        assert patched == ["fvg"]
        assert "confidence=5" in src.read_text()

    def test_patches_multiple_strategies(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": 5, "bos": 1}, src)
        assert set(patched) == {"fvg", "bos"}
        content = src.read_text()
        assert "confidence=5" in content
        assert "confidence=1" in content

    def test_patches_per_tf_dict(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": {"default": 1, "4h": 4}}, src)
        assert patched == ["fvg"]
        content = src.read_text()
        assert '"default": 1' in content
        assert '"4h": 4' in content

    def test_patches_dict_over_existing_dict(self, tmp_path: Path) -> None:
        # source already has a dict confidence; patch replaces it
        source = textwrap.dedent("""\
            "fvg": StrategySpec(
                name="fvg",
                confidence={"default": 1, "4h": 3},
            ),
        """)
        src = tmp_path / "indicators_lib.py"
        src.write_text(source)
        patched = write_confidence_to_source({"fvg": {"default": 2, "4h": 5}}, src)
        assert patched == ["fvg"]
        content = src.read_text()
        assert '"default": 2' in content
        assert '"4h": 5' in content

    def test_unknown_strategy_not_in_patched(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"nonexistent": 3}, src)
        assert patched == []
        assert src.read_text() == _FAKE_SOURCE  # file unchanged

    def test_same_value_still_patched(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"bos": 3}, src)
        assert "bos" in patched  # regex matched and wrote

    def test_does_not_corrupt_other_strategies(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        write_confidence_to_source({"fvg": 5}, src)
        content = src.read_text()
        # bos and pin_bar untouched
        assert '"bos": StrategySpec' in content
        assert '"pin_bar": StrategySpec' in content


# ---------------------------------------------------------------------------
# StrategySpec.get_confidence — TF resolution
# ---------------------------------------------------------------------------

from analytics.strategies import StrategySpec  # noqa: E402


class TestStrategySpecGetConfidence:
    def test_int_confidence_returns_same_for_any_tf(self) -> None:
        spec = StrategySpec(name="x", description="", confidence=3)
        assert spec.get_confidence("15m") == 3
        assert spec.get_confidence("1h") == 3
        assert spec.get_confidence("4h") == 3

    def test_dict_confidence_returns_tf_value(self) -> None:
        spec = StrategySpec(
            name="x", description="", confidence={"default": 1, "4h": 4}
        )
        assert spec.get_confidence("4h") == 4

    def test_dict_confidence_falls_back_to_default(self) -> None:
        spec = StrategySpec(
            name="x", description="", confidence={"default": 2, "4h": 4}
        )
        assert spec.get_confidence("1h") == 2
        assert spec.get_confidence("15m") == 2

    def test_dict_confidence_falls_back_to_3_when_no_default(self) -> None:
        spec = StrategySpec(name="x", description="", confidence={"4h": 4})
        assert spec.get_confidence("1h") == 3


# ---------------------------------------------------------------------------
# write_confidence_to_db
# ---------------------------------------------------------------------------


class TestWriteConfidenceToDb:
    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_writes_ratings_to_db(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}
        win_rates = pd.DataFrame(
            [
                {"strategy": "fvg", "timeframe": "1h", "avg_r": 0.35, "win_rate": 0.55},
                {"strategy": "fvg", "timeframe": "4h", "avg_r": 0.72, "win_rate": 0.60},
            ]
        )
        write_confidence_to_db(conn, "signal_watch", ratings, win_rates)
        from analytics.data_store import get_confidence_ratings

        result = get_confidence_ratings(conn, "signal_watch")
        assert result == {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}

    def test_different_configs_do_not_interfere(self) -> None:
        conn = self._conn()
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(conn, "signal_watch", {"fvg": {"1h": 2}}, empty_wr)
        write_confidence_to_db(
            conn, "signal_watch_weekdays", {"fvg": {"1h": 4}}, empty_wr
        )
        from analytics.data_store import get_confidence_ratings

        assert get_confidence_ratings(conn, "signal_watch")["fvg"]["1h"] == 2
        assert get_confidence_ratings(conn, "signal_watch_weekdays")["fvg"]["1h"] == 4

    def test_writes_directional_stars_to_db(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3}}
        dir_ratings = {"fvg": {"1h": {"long": 5, "short": 1}}}
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn, "signal_watch", ratings, empty_wr, directional_ratings=dir_ratings
        )
        result = get_directional_confidence_ratings(conn, "signal_watch")
        assert result["fvg"]["1h"]["long"] == 5
        assert result["fvg"]["1h"]["short"] == 1

    def test_combined_stars_unaffected_by_directional(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3}}
        dir_ratings = {"fvg": {"1h": {"long": 5, "short": 1}}}
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn, "signal_watch", ratings, empty_wr, directional_ratings=dir_ratings
        )
        from analytics.data_store import get_confidence_ratings

        combined = get_confidence_ratings(conn, "signal_watch")
        assert combined["fvg"]["1h"] == 3


# ---------------------------------------------------------------------------
# compute_directional_ratings
# ---------------------------------------------------------------------------


def _seed_directional_runs(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed backtest_runs with directional long/short split data."""
    conn.execute(
        "INSERT INTO backtest_runs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "dir_bos",
            "BTCUSDT",
            "4h",
            "bos",
            0,
            1,
            90,
            0.02,
            2.0,
            0.0,
            "off",
            20,
            20,
            12,
            8,
            0.6,
            0.4,
            8.0,
            4.0,
            1000,
            "sweep-1",  # sweep_id — a live-gate row would be NULL here
            # long: 10 trades, avg_r=0.9 → 5★ at min_trades=5
            10,
            8,
            0.8,
            0.9,
            # short: 10 trades, avg_r=-0.1 → 1★
            10,
            4,
            0.4,
            -0.1,
            9.0,  # long_total_r
            -1.0,  # short_total_r
            None,  # adr_suppress_threshold (added via ALTER TABLE)
            None,  # recovery_factor (added via ALTER TABLE)
            None,  # volume_suppress (added via ALTER TABLE)
            None,  # universe_policy (added via ALTER TABLE)
            None,  # cost_model (added via ALTER TABLE, last column)
        ],
    )


class TestComputeDirectionalRatings:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_empty_db_returns_empty(self) -> None:
        conn = self._make_conn()
        result = compute_directional_ratings(conn)
        conn.close()
        assert result == {}

    def test_directional_stars_computed_per_direction(self) -> None:
        conn = self._make_conn()
        _seed_directional_runs(conn)
        result = compute_directional_ratings(conn, min_trades=5)
        conn.close()
        assert "bos" in result
        assert "4h" in result["bos"]
        # long avg_r=0.9 → 5★; short avg_r=-0.1 → 1★
        assert result["bos"]["4h"]["long"] == 5
        assert result["bos"]["4h"]["short"] == 1

    def test_direction_excluded_when_below_min_trades(self) -> None:
        conn = self._make_conn()
        _seed_directional_runs(conn)
        result = compute_directional_ratings(conn, min_trades=15)
        conn.close()
        # 10 trades per direction < min_trades=15 → neither direction rated
        assert result == {}


# ---------------------------------------------------------------------------
# declared-cell filtering + orphan pruning
#
# `backtest_runs` is a permanent historical record and
# `upsert_confidence_ratings` only inserts-or-replaces, so without both a read
# filter and a delete a rating outlives the declaration that produced it.
# Found 2026-08-06: `fib_golden_zone × 4h` still showed 3★ +0.4688 — the
# second-highest-rated cell in the `signal_watch` table — 2.5 months after the
# strategy left the config, refreshed with a new timestamp on every recalibrate.
# ---------------------------------------------------------------------------


class TestDeclaredCellFiltering:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_undeclared_cell_is_not_rated(self) -> None:
        """engulfing/fvg have ratable runs but are absent from `declared`."""
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        rated = compute_recalibrated_ratings(conn, declared=[("bos", "4h")])
        conn.close()
        assert rated == {"bos": {"4h": 4}}

    def test_one_timeframe_of_a_declared_strategy_can_be_undeclared(self) -> None:
        """`strategy_timeframes` restricts a live strategy to a subset of TFs.

        This is the shape the sweep ignores: `bos` stayed in `signal_watch`
        while #143 retired it from 4h, so the sweep kept writing 4h rows and
        recalibrate kept rating a cell the daemon no longer scans.
        """
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        rated = compute_recalibrated_ratings(conn, declared=[("engulfing", "4h")])
        conn.close()
        assert rated == {"engulfing": {"4h": 4}}

    def test_declared_none_rates_everything(self) -> None:
        """Legacy source-patching mode has no config to be undeclared by."""
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        unfiltered = compute_recalibrated_ratings(conn, declared=None)
        conn.close()
        assert set(unfiltered) == {"bos", "engulfing", "fvg"}

    def test_declared_filter_applies_to_directional_ratings_too(self) -> None:
        """Combined and directional must not disagree about which cells are live."""
        conn = self._make_conn()
        _seed_directional_runs(conn)
        kept = compute_directional_ratings(conn, min_trades=5, declared=[("bos", "4h")])
        dropped = compute_directional_ratings(
            conn, min_trades=5, declared=[("bos", "1d")]
        )
        conn.close()
        assert kept["bos"]["4h"]["long"] == 5
        assert dropped == {}

    def test_win_rates_frame_keeps_columns_when_filter_empties_it(self) -> None:
        """Callers index into these columns unconditionally."""
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        df = get_backtest_win_rates(conn, declared=[("nonexistent", "4h")])
        conn.close()
        assert df.empty
        assert "long_avg_r" in df.columns
        assert "short_win_rate" in df.columns


class TestPruneUndeclaredConfidenceRatings:
    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def _empty_wr(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["strategy", "timeframe", "avg_r", "win_rate"])

    def test_orphan_row_is_deleted_and_declared_row_kept(self) -> None:
        conn = self._conn()
        write_confidence_to_db(
            conn,
            "signal_watch",
            {"bos": {"4h": 3}, "gone": {"4h": 5}},
            self._empty_wr(),
        )
        pruned = prune_undeclared_confidence_ratings(
            conn, "signal_watch", [("bos", "4h")]
        )
        result = get_confidence_ratings(conn, "signal_watch")
        conn.close()
        assert pruned == [("gone", "4h", "combined")]
        assert result == {"bos": {"4h": 3}}

    def test_prunes_every_direction_of_an_orphan_cell(self) -> None:
        """`declared` has no direction axis — a dead cell is dead in all three."""
        conn = self._conn()
        write_confidence_to_db(
            conn,
            "signal_watch",
            {"gone": {"4h": 5}},
            self._empty_wr(),
            directional_ratings={"gone": {"4h": {"long": 4, "short": 2}}},
        )
        pruned = prune_undeclared_confidence_ratings(conn, "signal_watch", [])
        conn.close()
        assert sorted(d for _, _, d in pruned) == ["combined", "long", "short"]

    def test_scoped_to_one_config(self) -> None:
        """The two live configs declare different cells and must not prune each other."""
        conn = self._conn()
        write_confidence_to_db(
            conn, "signal_watch", {"bos": {"4h": 3}}, self._empty_wr()
        )
        write_confidence_to_db(
            conn, "signal_watch_weekdays", {"bos": {"4h": 3}}, self._empty_wr()
        )
        prune_undeclared_confidence_ratings(conn, "signal_watch", [])
        weekdays = get_confidence_ratings(conn, "signal_watch_weekdays")
        conn.close()
        assert weekdays == {"bos": {"4h": 3}}

    def test_declared_but_unrated_cell_is_not_pruned(self) -> None:
        """Too-few-trades is not orphanhood — the next refresh may restore it."""
        conn = self._conn()
        write_confidence_to_db(
            conn, "signal_watch", {"bos": {"4h": 3}}, self._empty_wr()
        )
        pruned = prune_undeclared_confidence_ratings(
            conn, "signal_watch", [("bos", "4h"), ("thin", "1d")]
        )
        result = get_confidence_ratings(conn, "signal_watch")
        conn.close()
        assert pruned == []
        assert result == {"bos": {"4h": 3}}

    def test_write_confidence_to_db_prunes_when_declared_given(self) -> None:
        conn = self._conn()
        write_confidence_to_db(
            conn, "signal_watch", {"gone": {"4h": 5}}, self._empty_wr()
        )
        pruned = write_confidence_to_db(
            conn,
            "signal_watch",
            {"bos": {"4h": 3}},
            self._empty_wr(),
            declared=[("bos", "4h")],
        )
        result = get_confidence_ratings(conn, "signal_watch")
        conn.close()
        assert pruned == [("gone", "4h", "combined")]
        assert result == {"bos": {"4h": 3}}

    def test_write_confidence_to_db_without_declared_keeps_orphans(self) -> None:
        """Back-compat: the default must not silently delete rows."""
        conn = self._conn()
        write_confidence_to_db(
            conn, "signal_watch", {"gone": {"4h": 5}}, self._empty_wr()
        )
        pruned = write_confidence_to_db(
            conn, "signal_watch", {"bos": {"4h": 3}}, self._empty_wr()
        )
        result = get_confidence_ratings(conn, "signal_watch")
        conn.close()
        assert pruned == []
        assert result == {"bos": {"4h": 3}, "gone": {"4h": 5}}


# ---------------------------------------------------------------------------
# live-gate rows must not reach the ratings
#
# The live EV gate writes one `backtest_runs` row per direction-leg it
# evaluates, with no `sweep_id`. Deduplication is "latest per (strategy,
# timeframe, symbol)" with no notion of provenance, so before this filter a
# live row superseded the sweep row for that symbol — a different measurement
# (one strategy, no live-parity params, no conflict resolver, its own window)
# quietly replacing the competed one. Measured 2026-08-06: 42 of 316 rating
# inputs on `signal_watch`, 15 of 22 declared cells, 5 of them across zero.
# ---------------------------------------------------------------------------


def _insert_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    strategy: str,
    timeframe: str,
    symbol: str,
    closed_trades: int,
    avg_r: float,
    run_at_ms: int,
    sweep_id: str | None,
) -> None:
    conn.execute(
        "INSERT INTO backtest_runs VALUES "
        "(?, ?, ?, ?, 0, 1, 90, 0.02, 2.0, 0.0005, 'off', ?, ?, ?, 0, 0.5, ?, "
        "0.0, 0.0, ?, ?, "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
        "NULL, NULL, NULL)",
        [
            run_id,
            symbol,
            timeframe,
            strategy,
            closed_trades,
            closed_trades,
            closed_trades,
            avg_r,
            run_at_ms,
            sweep_id,
        ],
    )


class TestLiveGateRowsExcluded:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_newer_live_row_does_not_supersede_the_sweep_row(self) -> None:
        """The exact shape found in production: `orb × 4h` read +0.0011 vs +0.2126."""
        conn = self._make_conn()
        _insert_run(conn, "s", "orb", "4h", "SPY", 20, 0.6, 1000, "sweep-1")
        _insert_run(conn, "live", "orb", "4h", "SPY", 1, -1.0951, 2000, None)
        df = get_backtest_win_rates(conn)
        conn.close()
        assert len(df) == 1
        assert float(df.iloc[0]["avg_r"]) == 0.6
        assert int(df.iloc[0]["total_trades"]) == 20

    def test_a_cell_known_only_from_live_rows_is_not_rated(self) -> None:
        conn = self._make_conn()
        _insert_run(conn, "live", "orb", "4h", "SPY", 30, 1.4, 1000, None)
        rated = compute_recalibrated_ratings(conn)
        conn.close()
        assert rated == {}

    def test_live_row_for_one_symbol_leaves_other_symbols_intact(self) -> None:
        """Contamination was per-symbol — it thinned the sample as well as skewing it."""
        conn = self._make_conn()
        _insert_run(conn, "s1", "orb", "4h", "SPY", 20, 0.6, 1000, "sweep-1")
        _insert_run(conn, "s2", "orb", "4h", "QQQ", 20, 0.6, 1000, "sweep-1")
        _insert_run(conn, "live", "orb", "4h", "SPY", 1, -1.0, 2000, None)
        df = get_backtest_win_rates(conn)
        conn.close()
        assert int(df.iloc[0]["total_trades"]) == 40

    def test_older_sweep_runs_are_still_superseded_by_newer_sweep_runs(self) -> None:
        """The pre-existing param-sweep dedup must survive the provenance filter."""
        conn = self._make_conn()
        _insert_run(conn, "old", "orb", "4h", "SPY", 20, 0.6, 1000, "sweep-1")
        _insert_run(conn, "new", "orb", "4h", "SPY", 25, 0.1, 2000, "sweep-2")
        df = get_backtest_win_rates(conn)
        conn.close()
        assert len(df) == 1
        assert int(df.iloc[0]["total_trades"]) == 25


# ---------------------------------------------------------------------------
# cross-symbol aggregation must be trade-weighted
#
# `get_backtest_win_rates` pools counts across symbols (`closed_trades` sum,
# `win_count` sum) but took a plain `mean()` of `avg_r`, so a symbol with 1
# trade moved the star rating exactly as far as a symbol with 50 — while
# `min_trades` guarded the pooled count. The guard's population was not the
# statistic's population (the #150 rule), and `win_rate` in the same row was
# already pooled, so the two halves of one row disagreed about their
# denominator (the #169 rule).
#
# No fixture in this file put two symbols in one (strategy, timeframe) cell,
# so the whole cross-symbol path was untested. Measured on the real DB
# (docs/plans/scripts/recalibrate_avg_r_weighting.py): 22 of 22 rated
# `signal_watch` cells move a star, 16 of 32 on `signal_watch_weekdays`,
# several across zero.
# ---------------------------------------------------------------------------

_BT_COLS = (
    "run_id, symbol, timeframe, strategy, data_start_ms, data_end_ms, days, "
    "sl_pct, tp_r, fee_pct, day_filter, total_signals, closed_trades, "
    "win_count, loss_count, win_rate, avg_r, total_r, max_drawdown_r, "
    "run_at_ms, sweep_id, long_closed_trades, long_win_count, long_win_rate, "
    "long_avg_r, short_closed_trades, short_win_count, short_win_rate, "
    "short_avg_r"
)


def _insert_directional_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    symbol: str,
    closed_trades: int,
    avg_r: float,
    long_n: int | None = None,
    long_avg_r: float | None = None,
    short_n: int | None = None,
    short_avg_r: float | None = None,
    strategy: str = "orb",
    timeframe: str = "4h",
) -> None:
    """Insert one sweep row for (strategy, timeframe, symbol) by column name.

    Named columns, not positional — `tests/test_schema_insert_arity.py` ties
    positional INSERTs to the live column list, and this fixture only cares
    about the count/avg_r pairs.
    """
    conn.execute(
        f"INSERT INTO backtest_runs ({_BT_COLS}) VALUES "
        "(?, ?, ?, ?, 0, 1, 90, 0.02, 2.0, 0.0005, 'off', ?, ?, ?, 0, 0.5, ?, "
        "0.0, 0.0, 1000, 'sweep-1', ?, 0, NULL, ?, ?, 0, NULL, ?)",
        [
            run_id,
            symbol,
            timeframe,
            strategy,
            closed_trades,
            closed_trades,
            closed_trades,
            avg_r,
            long_n,
            long_avg_r,
            short_n,
            short_avg_r,
        ],
    )


class TestCrossSymbolAggregationIsTradeWeighted:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_combined_avg_r_is_trade_weighted_not_a_symbol_mean(self) -> None:
        """A 4-trade symbol must not outvote a 40-trade one.

        symbol mean  = (-0.10 + 1.50) / 2                = +0.700  -> 4 stars
        trade-weighted = (40*-0.10 + 4*1.50) / 44        = +0.045  -> 2 stars
        """
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 40, -0.10)
        _insert_directional_run(conn, "b", "AAPL", 4, 1.50)
        df = get_backtest_win_rates(conn)
        conn.close()
        assert len(df) == 1
        assert float(df.iloc[0]["avg_r"]) == 0.0455
        assert int(df.iloc[0]["total_trades"]) == 44

    def test_combined_star_rating_follows_the_weighted_value(self) -> None:
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 40, -0.10)
        _insert_directional_run(conn, "b", "AAPL", 4, 1.50)
        rated = compute_recalibrated_ratings(conn)
        conn.close()
        assert rated == {"orb": {"4h": 2}}

    def test_directional_avg_r_is_trade_weighted(self) -> None:
        """long: (30*-0.2 + 2*2.0) / 32 = -0.0625, not the symbol mean +0.900."""
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 40, -0.1, long_n=30, long_avg_r=-0.2)
        _insert_directional_run(conn, "b", "AAPL", 4, 1.5, long_n=2, long_avg_r=2.0)
        df = get_backtest_win_rates(conn)
        conn.close()
        assert float(df.iloc[0]["long_avg_r"]) == -0.0625
        assert int(df.iloc[0]["long_total_trades"]) == 32

    def test_directional_stars_follow_the_weighted_value(self) -> None:
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 40, -0.1, long_n=30, long_avg_r=-0.2)
        _insert_directional_run(conn, "b", "AAPL", 4, 1.5, long_n=2, long_avg_r=2.0)
        rated = compute_directional_ratings(conn)
        conn.close()
        assert rated == {"orb": {"4h": {"long": 1}}}

    def test_a_direction_absent_on_one_symbol_does_not_dilute_the_other(
        self,
    ) -> None:
        """`long_avg_r` is NULL exactly when `long_closed_trades` is 0.

        Numerator and denominator must skip the same rows, or the untraded
        symbol drags the mean toward zero.
        """
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 10, 0.5, long_n=10, long_avg_r=0.5)
        _insert_directional_run(conn, "b", "AAPL", 10, 0.5, long_n=0, long_avg_r=None)
        df = get_backtest_win_rates(conn)
        conn.close()
        assert float(df.iloc[0]["long_avg_r"]) == 0.5
        assert int(df.iloc[0]["long_total_trades"]) == 10

    def test_a_row_with_trades_but_no_average_is_excluded_from_both_parts(
        self,
    ) -> None:
        """Preventive — no such row exists in production (0 of 3,268, 2026-08-13).

        The engine returns a directional avg_r of None exactly when that
        direction has no closed trades, so `n > 0 AND avg_r IS NULL` is
        unreachable today. Pinned anyway because the alternative — counting n in
        the denominator with nothing in the numerator — silently invents n
        trades at R=0, which is the same bias-toward-a-fiction this whole fix
        removes. The previous test cannot catch it: there the absent direction's
        n is 0, so an unmasked denominator adds nothing.
        """
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 10, 0.5, long_n=10, long_avg_r=0.5)
        _insert_directional_run(conn, "b", "AAPL", 6, 0.5, long_n=6, long_avg_r=None)
        df = get_backtest_win_rates(conn)
        conn.close()
        assert float(df.iloc[0]["long_avg_r"]) == 0.5

    def test_weighted_mean_shares_its_denominator_with_the_reported_count(
        self,
    ) -> None:
        """total_r reconstructed from (avg_r x total_trades) must match the parts."""
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 40, -0.10)
        _insert_directional_run(conn, "b", "AAPL", 4, 1.50)
        _insert_directional_run(conn, "c", "QQQ", 6, 0.25)
        df = get_backtest_win_rates(conn)
        conn.close()
        row = df.iloc[0]
        expected_total_r = 40 * -0.10 + 4 * 1.50 + 6 * 0.25
        assert int(row["total_trades"]) == 50
        assert round(float(row["avg_r"]) * 50, 4) == round(expected_total_r, 4)

    def test_single_symbol_cell_is_unchanged_by_weighting(self) -> None:
        """Weighting is a no-op on one symbol — the existing fixtures must hold."""
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 20, 0.6, long_n=12, long_avg_r=0.8)
        df = get_backtest_win_rates(conn)
        conn.close()
        assert float(df.iloc[0]["avg_r"]) == 0.6
        assert float(df.iloc[0]["long_avg_r"]) == 0.8

    def test_equal_trade_counts_reduce_to_the_plain_mean(self) -> None:
        """The old and new estimators agree exactly when the counts are equal."""
        conn = self._make_conn()
        _insert_directional_run(conn, "a", "SPY", 10, -0.10)
        _insert_directional_run(conn, "b", "AAPL", 10, 1.50)
        df = get_backtest_win_rates(conn)
        conn.close()
        assert float(df.iloc[0]["avg_r"]) == 0.7
